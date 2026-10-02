"""Backup set transports (architecture phase 4, WP6): the archive transport
against a recording fake `HfApi`, and a directory transport. No network."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_segments import NOW, backup, build_state

import swingset.backup.transport as transport_module
from swingset.backup.restore_set import install
from swingset.backup.transport import (
    MAX_FILE_BYTES,
    HuggingFaceSegments,
    LocalDirectoryTransport,
    TransportError,
)
from swingset.state.db import open_database


class RecordingApi:
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
            "SELECT count(*) FROM backup_sets WHERE uploaded_at IS NOT NULL"
        ).fetchone()[0]
    assert [row[0] for row in rows] == ["hf:owner/archive"]
    assert uploaded == 4
    # The newest set installs from the archive alone.
    summary = install(remote, tmp_path / "restored")
    assert summary["set_id"] == results[-1]["set_id"]
    assert (tmp_path / "restored" / "ALERTS.log").read_text().endswith("day 3\n")


def _named(api: RecordingApi, set_id: str) -> list[str]:
    return list(json.loads(api.files[f"sets/{set_id}/backup.json"])["segments"])


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
            "SELECT count(*) FROM backup_objects WHERE segment=?", (gone[0],)
        ).fetchone()[0]
    assert stale == 0
    # Every kept set still installs from the archive alone.
    for result in results[2:]:
        install(remote, tmp_path / result["set_id"], set_id=result["set_id"])


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
        assert database.connection.execute("SELECT count(*) FROM backup_sets").fetchone()[0] == 1


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
        assert database.connection.execute("SELECT count(*) FROM backup_sets").fetchone()[0] == 0
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
