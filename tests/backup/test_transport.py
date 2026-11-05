"""Backup set transports (architecture phase 4, WP6): the archive transport
against a recording fake `HfApi`, and a directory transport. No network."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_segments import NOW, backup, build_state, index_of

import swingset.backup.transport as transport_module
from swingset.backup.restore_set import install
from swingset.backup.segments import create_set
from swingset.backup.transport import (
    MAX_FILE_BYTES,
    HuggingFaceSegments,
    LocalDirectoryTransport,
    TransportError,
)
from swingset.state.db import open_database
from swingset.state.environment import EnvironmentRefused, FixtureIO, identity


class RecordingApi(FixtureIO):
    """A repository as a dict of path to bytes; every commit is recorded."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commits: list[dict[str, Any]] = []
        self.listings = 0
        self.squashes = 0

    def list_repo_files(self, repo_id: str, *, repo_type: str) -> list[str]:
        assert repo_type == "dataset"
        self.listings += 1
        return sorted(self.files)

    def create_commit(self, **kwargs: Any) -> SimpleNamespace:
        added: list[tuple[str, int]] = []
        deleted: list[str] = []
        for operation in kwargs["operations"]:
            if type(operation).__name__ == "CommitOperationDelete":
                deleted.append(operation.path_in_repo)
                if not operation.is_folder:
                    del self.files[operation.path_in_repo]
                    continue
                for name in [
                    name for name in self.files if name.startswith(operation.path_in_repo)
                ]:
                    del self.files[name]
            else:
                body = Path(operation.path_or_fileobj).read_bytes()
                self.files[operation.path_in_repo] = body
                added.append((operation.path_in_repo, len(body)))
        self.commits.append({"added": added, "deleted": deleted, **kwargs})
        return SimpleNamespace(oid=f"commit-{len(self.commits)}")

    def super_squash_history(self, repo_id: str, **kwargs: Any) -> None:
        assert kwargs["repo_type"] == "dataset"
        self.squashes += 1

    def list_repo_tree(self, repo_id: str, **kwargs: Any) -> list[SimpleNamespace]:
        assert kwargs == {"recursive": True, "repo_type": "dataset"}
        return [SimpleNamespace(path=name, size=len(body)) for name, body in self.files.items()]

    def hf_hub_download(self, repo_id: str, filename: str, **kwargs: Any) -> str:
        target = Path(kwargs["local_dir"]) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if filename not in self.files:
            raise FileNotFoundError(filename)
        target.write_bytes(self.files[filename])
        return str(target)


class UntrustedApi:
    """A non-fixture API identity, with a recorder instead of any outside calls."""

    def __init__(self):
        self.calls = []
        self.recorder = RecordingApi()

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            return getattr(self.recorder, name)(*args, **kwargs)

        return call


@pytest.mark.parametrize("flag", [None, "dev", "production"])
def test_stored_dev_identity_blocks_every_real_archive_io_before_the_api(
    tmp_path, monkeypatch, flag
):
    state = tmp_path / "state"
    api = UntrustedApi()
    with open_database(state) as database:
        if flag is not None:
            monkeypatch.setenv("SWINGSET_ENV", flag)
        remote = HuggingFaceSegments(
            "owner/archive", token="fixture", api=api, connection=database.connection
        )
        for operation in (
            remote.segments,
            remote.sets,
            remote.collect,
            remote._sizes,
            lambda: remote.publish(state, "unused", []),
            lambda: remote.fetch("README.md", tmp_path),
        ):
            with pytest.raises(EnvironmentRefused):
                operation()
        with pytest.raises(EnvironmentRefused):
            create_set(database, remote, now=NOW, versions={})
        assert api.calls == []
        assert not (state / "checkpoints").exists()


def test_unbound_operator_reads_are_explicit_but_real_archive_writes_need_identity(tmp_path):
    api = UntrustedApi()
    remote = HuggingFaceSegments("owner/archive", token="fixture", api=api)
    assert remote.sets() == []
    assert api.calls == ["list_repo_files"]
    for operation in (remote.collect, lambda: remote.publish(tmp_path, "unused", [])):
        with pytest.raises(EnvironmentRefused, match="attached database identity"):
            operation()
    assert api.calls == ["list_repo_files"]


def test_archive_binding_restores_previous_connection_and_rejects_another_uuid(tmp_path):
    with open_database(tmp_path / "one") as first, open_database(tmp_path / "two") as second:
        remote = HuggingFaceSegments(
            "owner/archive", token="fixture", api=RecordingApi(), connection=first.connection
        )
        with pytest.raises(EnvironmentRefused, match="another database"):
            with remote.bound(second.connection):
                pytest.fail("binding must compare actual UUIDs first")
        assert remote.connection is first.connection
        detached = HuggingFaceSegments("owner/archive", token="fixture", api=RecordingApi())
        with pytest.raises(RuntimeError, match="cancelled"):
            with detached.bound(first.connection):
                assert detached.sets() == []
                raise RuntimeError("cancelled")
        assert detached.connection is None


def test_archive_rechecks_stored_identity_between_listing_and_commit(tmp_path):
    from swingset.dev.services import ensure_service

    with (
        open_database(tmp_path / "state") as database,
        ensure_service().connect(database.connection.info.dbname) as owner,
    ):
        owner.execute("UPDATE ops.environment_identity SET environment='production'")
        api = UntrustedApi()

        def listed(*args, **kwargs):
            api.calls.append("list_repo_files")
            owner.execute("UPDATE ops.environment_identity SET environment='dev'")
            return []

        api.list_repo_files = listed
        remote = HuggingFaceSegments(
            "owner/archive", token="fixture", api=api, connection=database.connection
        )
        local = tmp_path / "backup"
        directory = local / "sets" / "set_20261002T120000Z"
        directory.mkdir(parents=True)
        (directory / "backup.json").write_text("{}")
        with pytest.raises(EnvironmentRefused, match="development database"):
            remote.publish(local, directory.name, [])
        assert api.calls == ["list_repo_files"]


@pytest.mark.extended
def test_the_archive_gets_one_commit_per_set_and_keeps_three(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    api = RecordingApi()
    remote = HuggingFaceSegments("owner/archive", token="t", api=api)
    results = []
    for day in range(4):
        with (state / "ALERTS.log").open("a") as alerts:
            alerts.write(f"day {day}\n")
        results.append(backup(state, now=NOW + timedelta(days=day), transport=remote))
    # Four sets; every segment is still named by a kept set, so the
    # collector commits nothing.
    assert len(api.commits) == 4 and api.squashes == 0
    first = api.commits[0]
    names = [name for name, _ in first["added"]]
    set_id = results[0]["set_id"]
    assert names[-1] == f"sets/{set_id}/backup.json"
    assert f"sets/{set_id}/index.jsonl.zst" in names
    assert f"sets/{set_id}/db/part-0000.zst" in names
    assert [name for name in names if name.startswith("segments/")] == [
        f"segments/{segment}.tar" for segment in results[0]["new_segments"]
    ]
    # A later set sends only its new segment.
    second = [name for name, _ in api.commits[1]["added"] if name.startswith("segments/")]
    assert second == [f"segments/{results[1]['new_segments'][0]}.tar"]
    assert results[1]["new_items"] == 1
    # The fourth commit removes the oldest set; a publish never removes a
    # segment, and every segment the first set wrote is still named.
    assert api.commits[3]["deleted"] == [f"sets/{set_id}/"]
    assert remote.sets() == [result["set_id"] for result in results[1:]]
    assert all(not name.startswith("segments/") for name in api.commits[3]["deleted"])
    assert [result["remote"]["removed_segments"] for result in results] == [0, 0, 0, 0]
    assert results[3]["remote"]["bytes"] == sum(len(body) for body in api.files.values())
    record = json.loads((state / "checkpoints" / "remote.json").read_text())
    assert record["set_id"] == results[3]["set_id"] and record["transport"] == "hf:owner/archive"
    assert "collect_error" not in record
    assert all(size <= MAX_FILE_BYTES for commit in api.commits for _, size in commit["added"])
    with open_database(state) as database:
        rows = database.connection.execute(
            "SELECT DISTINCT transport FROM backup_objects"
        ).fetchall()
        uploaded = database.connection.execute(
            "SELECT COUNT(*) FROM backup_sets WHERE NOT uploaded_at IS NULL"
        ).fetchone()[0]
    assert [row[0] for row in rows] == ["hf:owner/archive"]
    assert uploaded == 4
    # The newest set installs from the archive alone.
    summary = install(remote, tmp_path / "restored")
    assert summary["set_id"] == results[-1]["set_id"]
    assert (tmp_path / "restored" / "ALERTS.log").read_text().endswith("day 3\n")


def _named(api: RecordingApi, set_id: str) -> list[str]:
    return list(json.loads(api.files[f"sets/{set_id}/backup.json"])["segments"])


@pytest.mark.extended
def test_the_collector_removes_a_segment_no_kept_set_names_and_squashes(tmp_path: Path) -> None:
    """Day 1's ALERTS.log lives in its own segment; once day 1's set is no
    longer kept, nothing names it: it goes, and the history is squashed so
    its bytes leave the archive (D-0234)."""
    state = tmp_path / "state"
    build_state(state)
    api = RecordingApi()
    remote = HuggingFaceSegments("owner/archive", token="t", api=api)
    results = []
    for day in range(5):
        with (state / "ALERTS.log").open("a") as alerts:
            alerts.write(f"day {day}\n")
        results.append(backup(state, now=NOW + timedelta(days=day), transport=remote))
    named = {segment for result in results[2:] for segment in _named(api, result["set_id"])}
    gone = sorted(set(results[1]["new_segments"]) - named)
    assert len(gone) == 1
    assert api.commits[-1]["deleted"] == [f"segments/{gone[0]}.tar"]
    assert api.squashes == 1
    assert [result["remote"]["removed_segments"] for result in results] == [0, 0, 0, 0, 1]
    assert results[4]["remote"]["squashed"] is True
    assert remote.segments() == named
    # No row names the removed segment, so a file in it would be sent again.
    with open_database(state) as database:
        stale = database.connection.execute(
            "SELECT COUNT(*) FROM backup_objects WHERE segment = %s", (gone[0],)
        ).fetchone()[0]
    assert stale == 0
    # Every kept set still installs from the archive alone.
    for result in results[2:]:
        install(remote, tmp_path / result["set_id"], set_id=result["set_id"])


@pytest.mark.extended
def test_the_collector_never_squashes_a_repository_that_holds_checkpoints(tmp_path: Path) -> None:
    """Squashing drops every old commit. A repository that also holds the
    full-checkpoint archive (`checkpoint.json`, `_transport/`) is refused:
    nothing is removed or squashed, and `[archive-size]` reads the error.
    Sets default to their own repository, not the checkpoint archive."""
    assert transport_module.SETS_REPO != "skeswa/swingset-archive"
    assert HuggingFaceSegments(token="t", api=RecordingApi()).repo_id == (
        transport_module.SETS_REPO
    )
    state = tmp_path / "state"
    build_state(state)
    api = RecordingApi()
    api.files["checkpoint.json"] = b"{}"
    api.files["_transport/checkpoint.tar"] = b"tar"
    remote = HuggingFaceSegments("owner/archive", token="t", api=api)
    results = []
    for day in range(5):
        with (state / "ALERTS.log").open("a") as alerts:
            alerts.write(f"day {day}\n")
        results.append(backup(state, now=NOW + timedelta(days=day), transport=remote))
    assert api.squashes == 0
    assert all(
        not commit["deleted"] or commit["deleted"][0].startswith("sets/") for commit in api.commits
    )
    assert "checkpoint.json" in api.files and "_transport/checkpoint.tar" in api.files
    error = results[-1]["remote"]["collect_error"]
    assert error.startswith("TransportError: owner/archive holds _transport, checkpoint.json")
    # Without the foreign paths, the same repository is collected and squashed.
    del api.files["checkpoint.json"], api.files["_transport/checkpoint.tar"]
    later = backup(state, now=NOW + timedelta(days=5), transport=remote)
    assert "collect_error" not in later["remote"] and api.squashes == 1


def test_a_failed_collection_keeps_the_set_and_says_so(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    api = RecordingApi()
    remote = HuggingFaceSegments("owner/archive", token="t", api=api)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("tree listing down")

    api.list_repo_tree = refuse  # type: ignore[method-assign,assignment]
    result = backup(state, transport=remote)
    assert result["remote"]["collect_error"].startswith("TransportError: cannot size archive")
    with open_database(state) as database:
        assert database.connection.execute("SELECT COUNT(*) FROM backup_sets").fetchone()[0] == 1


def test_a_failed_commit_records_nothing_and_resends_next_time(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    api = RecordingApi()
    remote = HuggingFaceSegments("owner/archive", token="t", api=api)

    def refuse(**_kwargs: Any) -> None:
        raise ConnectionError("network down")

    api.create_commit = refuse  # type: ignore[method-assign,assignment]
    with pytest.raises(TransportError, match="network down"):
        backup(state, transport=remote)
    with open_database(state) as database:
        assert database.connection.execute("SELECT COUNT(*) FROM backup_sets").fetchone()[0] == 0
    del api.create_commit
    retry = backup(state, now=NOW + timedelta(hours=1), transport=remote)
    assert retry["new_items"] == retry["items"]
    assert len(api.commits) == 1


def test_no_file_over_the_limit_is_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    build_state(state)
    api = RecordingApi()
    remote = HuggingFaceSegments("owner/archive", token="t", api=api)
    monkeypatch.setattr(transport_module, "MAX_FILE_BYTES", 1024)
    with pytest.raises(TransportError, match="over the 1024 limit"):
        backup(state, transport=remote)
    assert api.commits == []


@pytest.mark.extended
def test_a_directory_transport_copies_a_set_and_keeps_three(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    destination = tmp_path / "offsite"
    remote = LocalDirectoryTransport(destination, keep=3)
    results = [backup(state, now=NOW + timedelta(days=day), transport=remote) for day in range(4)]
    assert remote.sets() == [result["set_id"] for result in results[1:]]
    assert remote.segments() >= set(results[0]["new_segments"])
    # The copy stands alone: the state's own checkpoints are not needed.
    shutil.rmtree(state / "checkpoints")
    install(remote, tmp_path / "restored")
    with pytest.raises(TransportError, match="unsafe"):
        remote.fetch("../escape", tmp_path)


@pytest.mark.extended
def test_a_directory_transport_collects_a_segment_no_kept_set_names(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    remote = LocalDirectoryTransport(tmp_path / "offsite", keep=3)
    results = []
    for day in range(5):
        with (state / "ALERTS.log").open("a") as alerts:
            alerts.write(f"day {day}\n")
        results.append(backup(state, now=NOW + timedelta(days=day), transport=remote))
    named = {
        segment
        for set_id in remote.sets()
        for segment in json.loads(
            (tmp_path / "offsite" / "sets" / set_id / "backup.json").read_text()
        )["segments"]
    }
    gone = set(results[1]["new_segments"]) - named
    assert len(gone) == 1 and results[4]["remote"]["removed_segments"] == 1
    assert remote.segments() == named and not gone & remote.segments()


def _transport_set(root: Path, day: int) -> tuple[str, list[str]]:
    """Opaque publication files and the segment references collection reads."""
    set_id = f"set_2026100{day + 1}T120000Z"
    directory = root / "sets" / set_id
    (directory / "db").mkdir(parents=True)
    segments = []
    for body in (b"shared segment", f"day {day}".encode()):
        digest = hashlib.sha256(body).hexdigest()
        path = root / "segments" / f"{digest}.tar"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(body)
        segments.append(digest)
    (directory / "db" / "part-0000.zst").write_bytes(b"database part")
    (directory / "index.jsonl.zst").write_bytes(b"index")
    (directory / "backup.json").write_text(json.dumps({"segments": segments}))
    return set_id, segments


def _transport(tmp_path: Path, backend: str):
    api = RecordingApi()
    remote = (
        HuggingFaceSegments("owner/archive", token="t", api=api)
        if backend == "hf"
        else LocalDirectoryTransport(tmp_path / "offsite", keep=3)
    )
    return remote, api


@pytest.mark.parametrize("backend", ["hf", "directory"])
def test_transport_publication_keeps_three_sets_without_deleting_shared_segments(tmp_path, backend):
    remote, api = _transport(tmp_path, backend)
    local = tmp_path / "local"
    sets = []
    for day in range(4):
        set_id, segments = _transport_set(local, day)
        remote.publish(local, set_id, segments)
        sets.append((set_id, segments))
    assert remote.sets() == [set_id for set_id, _ in sets[1:]]
    assert remote.segments() == {segment for _, segments in sets for segment in segments}
    for set_id, _ in sets[1:]:
        for relative in ("backup.json", "index.jsonl.zst", "db/part-0000.zst"):
            name = f"sets/{set_id}/{relative}"
            assert (
                remote.fetch(name, tmp_path / "fetch").read_bytes() == (local / name).read_bytes()
            )
    if backend == "hf":
        assert len(api.commits) == 4 and api.squashes == 0
        first = api.commits[0]["added"]
        assert first[-1][0] == f"sets/{sets[0][0]}/backup.json"
        assert {name for name, _ in first} == {
            *(f"segments/{segment}.tar" for segment in sets[0][1]),
            f"sets/{sets[0][0]}/backup.json",
            f"sets/{sets[0][0]}/index.jsonl.zst",
            f"sets/{sets[0][0]}/db/part-0000.zst",
        }
        assert [name for name, _ in api.commits[1]["added"] if name.startswith("segments/")] == [
            f"segments/{sets[1][1][1]}.tar"
        ]
        assert api.commits[3]["deleted"] == [f"sets/{sets[0][0]}/"]
        assert all(size <= MAX_FILE_BYTES for commit in api.commits for _, size in commit["added"])


@pytest.mark.parametrize("backend", ["hf", "directory"])
def test_transport_collection_removes_only_unnamed_segments_and_is_idempotent(tmp_path, backend):
    remote, api = _transport(tmp_path, backend)
    local = tmp_path / "local"
    sets = []
    for day in range(4):
        set_id, segments = _transport_set(local, day)
        remote.publish(local, set_id, segments)
        sets.append((set_id, segments))
    needed = {segment for _, segments in sets[1:] for segment in segments}
    removed = sets[0][1][1]
    result = remote.collect()
    assert result["removed_segments"] == 1
    assert result["sets"] == 3 and result["segments"] == len(needed)
    assert remote.segments() == needed and removed not in remote.segments()
    assert remote.sets() == [set_id for set_id, _ in sets[1:]]
    if backend == "hf":
        assert len(api.commits) == 5 and api.squashes == 1
        assert api.commits[-1]["deleted"] == [f"segments/{removed}.tar"]
        assert api.commits[-1]["added"] == []
        assert result["squashed"] is True
        assert result["bytes"] == sum(map(len, api.files.values()))
    else:
        assert result["bytes"] == sum(
            path.stat().st_size for path in remote.root.rglob("*") if path.is_file()
        )
    assert remote.collect()["removed_segments"] == 0
    if backend == "hf":
        assert len(api.commits) == 5 and api.squashes == 1


def test_foreign_checkpoint_paths_prevent_every_collection_mutation(tmp_path):
    assert transport_module.SETS_REPO != "skeswa/swingset-archive"
    assert HuggingFaceSegments(token="t", api=RecordingApi()).repo_id == transport_module.SETS_REPO
    remote, api = _transport(tmp_path, "hf")
    local = tmp_path / "local"
    for day in range(4):
        set_id, segments = _transport_set(local, day)
        remote.publish(local, set_id, segments)
    api.files.update({"checkpoint.json": b"{}", "_transport/checkpoint.tar": b"checkpoint"})
    before = dict(api.files)
    with pytest.raises(TransportError, match="holds _transport, checkpoint.json"):
        remote.collect()
    assert api.files == before and len(api.commits) == 4 and api.squashes == 0
    del api.files["checkpoint.json"], api.files["_transport/checkpoint.tar"]
    assert remote.collect()["removed_segments"] == 1
    assert api.squashes == 1


@pytest.mark.parametrize("backend", ["hf", "directory"])
def test_one_native_set_restores_from_the_transport_alone(tmp_path, backend):
    state = tmp_path / "state"
    build_state(state)
    remote, _ = _transport(tmp_path, backend)
    result = backup(state, transport=remote)
    entries = index_of(state, result["set_id"])
    record = json.loads((state / "checkpoints" / "remote.json").read_text())
    assert record["set_id"] == result["set_id"] and record["transport"] == remote.name
    assert "collect_error" not in record
    with open_database(state) as database:
        source_id = identity(database.connection).database_id
        assert database.connection.execute("SELECT COUNT(*) FROM backup_sets").fetchone()[0] == 1
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM backup_sets WHERE NOT uploaded_at IS NULL"
            ).fetchone()[0]
            == 1
        )
        transports = database.connection.execute(
            "SELECT DISTINCT transport FROM backup_objects"
        ).fetchall()
        assert [row[0] for row in transports] == [remote.name]
    shutil.rmtree(state / "checkpoints")
    restored = tmp_path / "restored"
    summary = install(remote, restored)
    assert summary["set_id"] == result["set_id"] and summary["items"] == len(entries)
    for name in entries:
        assert (restored / name).read_bytes() == (state / name).read_bytes(), name
    with open_database(restored, allow_restore_pending=True) as database:
        assert identity(database.connection).database_id != source_id
        assert database.connection.execute("SELECT host FROM hosts").fetchone()[0] == "h.example"
    with pytest.raises(TransportError, match="unsafe"):
        remote.fetch("../escape", tmp_path)
