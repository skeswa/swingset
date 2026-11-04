"""The weekly restore drill (architecture phase 4, WP7; D-0232): it installs
the newest backup set into scratch, checks it and hydrates one release, and
says so in `backup_sets`, the maintain summary and ALERTS.log. Fakes only."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from swingset.backup import drill
from swingset.backup.segments import LOCAL_TRANSPORT, create_set, local_sets
from swingset.backup.transport import LocalDirectoryTransport
from swingset.build import manifest
from swingset.build.files import canonical_json
from swingset.config import StorageConfig, parse_storage
from swingset.state import guards
from swingset.state.db import open_database
from swingset.state.retention import artifact_directory

NOW = datetime(2026, 10, 2, 3, 30, tzinfo=UTC)


def _artifact(database, *, unit=None) -> str:
    """A real retained generation and its reproducible coverage Parquet artifact."""
    import pyarrow as pa

    from swingset.build import artifact_store, partitions
    from swingset.build.schema import SCHEMAS
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    payload = {
        "year": 2025,
        "source": "wsdc_registry",
        "via": "origin",
        "events": 1,
        "contests": 0,
        "rounds": 0,
        "entries": 0,
        "events_registry_only": 1,
        "events_index_only": 0,
        "events_sheets_partial": 0,
        "events_sheets_complete": 0,
        "events_day_precision": 0,
        "events_listed_only": 0,
        "events_accepted": 0,
        "expected_rounds": None,
        "parsed_rounds": 0,
        "unresolved_findings": 0,
        "last_changed_at": NOW.isoformat(),
    }
    key = '[2025,"wsdc_registry","origin"]'
    unit = unit or WorkUnit("project", "year", "2025")
    run = database.start_run(NOW)
    with database.transaction() as conn:
        selection = derivations.capture(conn, unit, now=NOW, recipe={"recipe/test": "drill"})
        generation = derivations.complete(
            conn,
            selection,
            rows=lambda: iter([derivations.OutputRow("coverage", key, payload)]),
            now=NOW,
            run_id=run,
        )
    tables = partitions.public_tables(
        partitions.Partition("year/2025", "year"), {("coverage", key): payload}
    )
    arrow = {
        name: pa.Table.from_pylist(rows, schema=SCHEMAS[name]) for name, rows in tables.items()
    }
    digest, _size = artifact_store.write(
        database.state_dir,
        arrow,
        {
            "format": partitions.FORMAT,
            "partition": "year/2025",
            "inputs_digest": "fixture",
            "inputs": {},
            "support": [],
            "generations": [generation],
            "withheld_subjects": [],
            "counts": {},
        },
    )
    return digest


def _candidate(state: Path, name: str, policy: dict[str, Any]) -> None:
    candidate = state / "candidates" / name
    (candidate / "_meta").mkdir(parents=True)
    body = canonical_json({"candidate_id": name, "files": {}, "release_policy": policy})
    (candidate / "BUILT").write_bytes(
        canonical_json({"manifest_hash": hashlib.sha256(body).hexdigest()})
    )
    (candidate / "PUBLISHED").write_text(json.dumps({"commit": "abc", "verified_at": "x"}))
    (candidate / "_meta" / "manifest.json").write_bytes(body)
    (state / "baseline").symlink_to(Path("candidates") / name)


def build_state(state: Path, policy: dict[str, Any] | None = None) -> str:
    """A native schema37 state with one retained, reproducible partition."""
    with open_database(state) as database:
        artifact = _artifact(database)
        body = canonical_json({"format": manifest.FORMAT, "built": [["year/2025", artifact]]})
        digest = hashlib.sha256(body).hexdigest()
        manifest.store(database, body, digest, now=NOW)
    _candidate(state, "cand_base", policy or {"mode": "partitioned", "manifest_digest": digest})
    return digest


def backup(state: Path, *, now: datetime = NOW) -> dict[str, Any]:
    transport = LocalDirectoryTransport(state / "checkpoints", name=LOCAL_TRANSPORT)
    with open_database(state) as database:
        return create_set(database, transport, now=now, versions={"swingset": "test"}, headroom=0)


#: Room enough for any test drill: the real check reads this machine's disk.
ROOMY = 1 << 50


def _drill(state: Path, scratch: Path, *, now: datetime = NOW, **options: Any) -> dict[str, Any]:
    options.setdefault("free", lambda path: ROOMY)
    with open_database(state) as database:
        return drill.run(database, now=now, drill_dir=scratch, **options)


def _row(state: Path, set_id: str) -> tuple[Any, ...]:
    with open_database(state, lock=False) as database:
        return tuple(
            database.connection.execute(
                "SELECT drilled_at, drill_json FROM backup_sets WHERE set_id = %s", (set_id,)
            ).fetchone()
        )


def test_the_drill_passes_on_a_set_and_hydrates_its_partitioned_release(tmp_path: Path) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    digest = build_state(state)
    backup(state)
    later = backup(state, now=NOW + timedelta(hours=1))
    record = _drill(state, scratch, now=NOW + timedelta(hours=2))
    assert record["passed"] is True, record
    assert record["set_id"] == later["set_id"]
    assert record["install"]["integrity"] == "full"
    assert record["install"]["baseline_candidate"] == "cand_base"
    assert record["release"] == {
        "candidate": "cand_base",
        "mode": "partitioned",
        "manifest_digest": digest,
        "artifacts": 1,
    }
    assert record["need_bytes"] > drill.HEADROOM_BYTES
    # The scratch is gone; the drilled set and the newest stay.
    assert list(scratch.iterdir()) == []
    assert local_sets(state / "checkpoints") == [later["set_id"]]
    assert record["pruned"]["kept"] == [later["set_id"]]
    drilled, body = _row(state, later["set_id"])
    assert drilled == (NOW + timedelta(hours=2)).isoformat()
    assert json.loads(body)["passed"] is True
    with open_database(state, lock=False) as database:
        conn = database.connection
        assert not drill.due(conn, now=NOW + timedelta(days=6), every_days=7)
        assert drill.due(conn, now=NOW + timedelta(days=7), every_days=7)
    assert not (state / "ALERTS.log").exists()


def test_historical_parquet_recovery_preserves_labels_and_ignores_current_gates(
    tmp_path, monkeypatch
):
    from postgres_statements import contents

    from swingset.backup.artifacts import hydrate
    from swingset.build import partitions
    from swingset.state import derivations

    state = tmp_path / "state"
    digest = build_state(state)
    artifact = manifest.read(state, digest)["built"][0][1]
    directory = artifact_directory(state, artifact)
    original = {path.name: path.read_bytes() for path in directory.glob("*.parquet")}
    for name in original:
        (directory / name).unlink()
    with open_database(state) as database:
        before = contents(database.connection)

    def current_gate(*args, **kwargs):
        raise AssertionError(
            "historical artifacts must not run current admission or currency checks"
        )

    monkeypatch.setattr(partitions, "extract", current_gate)
    monkeypatch.setattr(derivations, "current", current_gate)
    assert hydrate(state, digest) == {"recovered": 1, "artifacts": 1}
    assert {path.name: path.read_bytes() for path in directory.glob("*.parquet")} == original
    assert manifest.verify(state, digest)["built"] == [["year/2025", artifact]]
    with open_database(state) as database:
        assert contents(database.connection) == before


@pytest.mark.parametrize("defect", ["missing_recipe", "different_file_digest"])
def test_historical_parquet_recovery_refuses_insufficient_recipes_without_installing(
    tmp_path, defect
):
    from postgres_statements import contents

    from swingset.backup.artifacts import ArtifactUnavailable, hydrate

    state = tmp_path / "state"
    digest = build_state(state)
    artifact = manifest.read(state, digest)["built"][0][1]
    sidecar = json.loads((artifact_directory(state, artifact) / "sidecar.json").read_bytes())
    if defect == "missing_recipe":
        sidecar["generations"] = []
    else:
        sidecar["tables"]["coverage"]["sha256"] = "0" * 64
    body = canonical_json(sidecar)
    changed = hashlib.sha256(body).hexdigest()
    directory = artifact_directory(state, changed)
    directory.mkdir(parents=True)
    (directory / "sidecar.json").write_bytes(body)
    release = canonical_json({"format": manifest.FORMAT, "built": [["year/2025", changed]]})
    release_digest = hashlib.sha256(release).hexdigest()
    with open_database(state) as database:
        manifest.store(database, release, release_digest, now=NOW)
        before = contents(database.connection)
    with pytest.raises(
        ArtifactUnavailable, match="retained generation recipe|cannot be reconstructed"
    ):
        hydrate(state, release_digest)
    assert sorted(path.name for path in directory.iterdir()) == ["sidecar.json"]
    assert (directory / "sidecar.json").read_bytes() == body
    with open_database(state) as database:
        assert contents(database.connection) == before


def test_a_corrupt_part_fails_the_drill_with_the_alert_line(tmp_path: Path) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    build_state(state)
    result = backup(state)
    part = state / "checkpoints" / "sets" / result["set_id"] / "db" / "part-0000.zst"
    body = bytearray(part.read_bytes())
    body[len(body) // 2] ^= 0xFF
    part.write_bytes(bytes(body))
    record = _drill(state, scratch)
    assert record["passed"] is False
    assert "does not match its digest" in record["error"]
    drilled, stored = _row(state, result["set_id"])
    assert drilled is None and json.loads(stored)["passed"] is False
    lines = (state / "ALERTS.log").read_text().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(
        f"2026-10-02T03:30:00Z [restore-drill-failed] set {result['set_id']} (local) failed"
        " its restore drill: SetError: db/part-0000.zst does not match its digest;"
    )
    # The scratch stays for inspection; a pass is still owed.
    assert (scratch / f"swingset-drill-{result['set_id']}").is_dir()
    with open_database(state, lock=False) as database:
        assert drill.due(database.connection, now=NOW, every_days=7)
    # The next drill removes this set's old scratch before it starts, unless
    # an operator kept it.
    kept = scratch / f"swingset-drill-{result['set_id']}"
    (kept / "KEEP").touch()
    again = _drill(state, scratch, now=NOW + timedelta(days=1))
    assert again["passed"] is False
    assert len((state / "ALERTS.log").read_text().splitlines()) == 2
    assert (kept / "KEEP").exists()
    assert sorted(path.name for path in scratch.iterdir()) == [
        kept.name,
        f"{kept.name}-{int((NOW + timedelta(days=1)).timestamp())}",
    ]
    (kept / "KEEP").unlink()
    # The second drill's scratch dates from its drill, not from the wall clock.
    stamp = (NOW + timedelta(days=1)).timestamp()
    os.utime(scratch / f"{kept.name}-{int(stamp)}", (stamp, stamp))
    _drill(state, scratch, now=NOW + timedelta(days=3))
    assert [path.name for path in scratch.iterdir()] == [kept.name]


def test_a_drill_without_room_keeps_a_guard_and_does_not_start(tmp_path: Path) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    build_state(state)
    result = backup(state)
    record = _drill(state, scratch, free=lambda path: 0)
    assert record["skipped"] == "no_room" and record["free_bytes"] == 0
    assert record["need_bytes"] > drill.HEADROOM_BYTES
    assert list(scratch.iterdir()) == []
    later = NOW + timedelta(days=1)
    assert _drill(state, scratch, now=later, free=lambda path: 0)["skipped"] == "no_room"
    with open_database(state, lock=False) as database:
        row = guards.get(database.connection, "loop:maintain", drill.NO_ROOM)
    assert row is not None
    # Since the first refusal, with a 24 h clock the watchdog's
    # [guard-expired] reads once a refusal lasts longer.
    assert row.since == "2026-10-02T03:30:00.000000Z"
    assert row.expires_at == "2026-10-03T03:30:00.000000Z"
    assert row.last_seen_at == "2026-10-03T03:30:00.000000Z" and row.seen == 2
    assert _row(state, result["set_id"]) == (None, None)
    assert not (state / "ALERTS.log").exists()
    assert _drill(state, scratch, now=later)["passed"] is True
    with open_database(state, lock=False) as database:
        assert guards.get(database.connection, "loop:maintain", drill.NO_ROOM) is None


def _proof(state: Path, *, corrupt: bool = False) -> dict[str, Any]:
    """A closure proof row and its public commitment, as `closure.retain` leaves them."""
    from swingset.build.closure_manifest import canonical, digest

    private = {"cutoff": "2026-10-01", "selected": [], "source_support": []}
    body = canonical(private)
    identifier = digest(private)
    with open_database(state) as database:
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO derivation_dependency_sets (dependency_set_id, manifest_json) VALUES (%s, %s)",
                (identifier, body.replace("2026", "2025") if corrupt else body),
            )
    public = {
        "format": "release-closure-public-v1",
        "private_digest": identifier,
        "selected_generations": [],
    }
    return {**public, "digest": digest(public)}


def test_a_closure_baseline_streams_its_proof_against_the_commitment(tmp_path: Path) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    public = _proof(state)
    public["digest"] = "0" * 64
    build_state(state, {"mode": "closure", "closure": public})
    made = backup(state)
    # A public commitment that does not hash to its digest fails the drill.
    record = _drill(state, scratch)
    assert record["passed"] is False and "public_closure_digest_mismatch" in record["error"]
    # The owner job removes both the restored database and expanded files.
    # Only the compact fetched outer manifest remains for inspection.
    failed = scratch / f"swingset-drill-{made['set_id']}"
    assert not (failed / "state").exists()
    assert [path.name for path in failed.iterdir()] == ["failure.json"]
    # Another drill's failed scratch goes once it is 20 hours old, so the
    # next daily tick never meets it.
    other = scratch / "swingset-drill-set_20261001T040000Z"
    failed.rename(other)
    stamp = (NOW - timedelta(hours=21)).timestamp()
    os.utime(other, (stamp, stamp))
    _drill(state, scratch)
    assert not other.exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_a_closure_proof_is_checked_by_its_bytes_not_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    from swingset.build import closure

    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    build_state(state, {"mode": "partitioned", "manifest_digest": "unused"})
    public = _proof(state, corrupt=corrupt)
    meta = state / "candidates" / "cand_base" / "_meta" / "manifest.json"
    body = canonical_json(
        {
            "candidate_id": "cand_base",
            "files": {},
            "release_policy": {"mode": "closure", "closure": public},
        }
    )
    meta.write_bytes(body)
    (meta.parents[1] / "BUILT").write_bytes(
        canonical_json({"manifest_hash": hashlib.sha256(body).hexdigest()})
    )
    backup(state)

    def decoded(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the drill must not decode a closure proof")

    monkeypatch.setattr(closure, "hydrate", decoded)
    record = _drill(state, scratch)
    if corrupt:
        assert record["passed"] is False and "private_closure_proof_corrupt" in record["error"]
        return
    assert record["passed"] is True, record
    assert record["release"] == {
        "candidate": "cand_base",
        "mode": "closure",
        "private_digest": public["private_digest"],
        "proof_bytes": len(
            json.dumps(
                {"cutoff": "2026-10-01", "selected": [], "source_support": []},
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    }


def test_a_drill_while_the_backup_runs_skips_with_a_guard(tmp_path: Path) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    build_state(state)
    result = backup(state)
    from swingset.backup.snapshot import BackupBusy, backup_lock

    with open_database(state, lock=False) as guard:
        with backup_lock(guard.connection):
            record = _drill(state, scratch)
            with open_database(state, lock=False) as other:
                with pytest.raises(BackupBusy):
                    with backup_lock(other.connection):
                        pass
    assert record == {"skipped": "backup_running", "set_id": result["set_id"]}
    assert not scratch.exists() or list(scratch.iterdir()) == []
    with open_database(state, lock=False) as database:
        row = guards.get(database.connection, "loop:maintain", drill.BACKUP_RUNNING)
    assert row is not None and row.expires_at == "2026-10-03T03:30:00.000000Z"
    assert _row(state, result["set_id"]) == (None, None)
    # The lock is free again: the drill runs, clears the guard, and lets go.
    assert _drill(state, scratch, now=NOW + timedelta(hours=1))["passed"] is True
    with open_database(state, lock=False) as database:
        assert guards.get(database.connection, "loop:maintain", drill.BACKUP_RUNNING) is None
    with open_database(state, lock=False) as guard:
        with backup_lock(guard.connection):
            pass


def test_a_killed_drill_leaves_a_started_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    build_state(state)
    result = backup(state)

    def killed(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt  # stands in for SIGKILL: nothing after it runs

    monkeypatch.setattr(drill, "drill_set", killed)
    with pytest.raises(KeyboardInterrupt):
        _drill(state, scratch)
    drilled, body = _row(state, result["set_id"])
    started = json.loads(body)
    assert drilled is None
    assert started["passed"] is None and started["at"] == NOW.isoformat()
    assert started["set_id"] == result["set_id"]
    # The backup lock went with the drill.
    from swingset.backup.snapshot import backup_lock

    with open_database(state, lock=False) as guard:
        with backup_lock(guard.connection):
            pass


def test_no_set_is_not_due_and_the_storage_table_parses(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        assert not drill.due(database.connection, now=NOW, every_days=7)
        assert drill.run(database, now=NOW, drill_dir=tmp_path / "scratch") == {"skipped": "no_set"}
    shipped = parse_storage(Path("config/sources.toml").read_bytes())
    assert shipped == StorageConfig()
    assert parse_storage(b"") == StorageConfig()
    for body in (
        b"[storage]\nevict_refs_per_tick = -1\n",
        b"[storage]\ncollect_every_days = 0\n",
        b"[storage]\ndrill_dir = 'tmp'\n",
        b"[storage]\nunknown = 1\n",
    ):
        with pytest.raises(ValueError):
            parse_storage(body)


def test_the_drill_reads_the_quota_cache(tmp_path: Path) -> None:
    quota = tmp_path / "swingset-disk"
    quota.write_text("900 1000 2026-10-02T03:00:00Z\n")
    assert drill.free_bytes(tmp_path, quota=quota) == 100
    assert drill.free_bytes(tmp_path, quota=tmp_path / "absent") > 100


def test_named_r2_drill_uses_its_actual_database_and_rejects_a_different_bucket(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from swingset.backup import r2

    calls = []

    def configured(connection, *, read_only):
        calls.append((connection, read_only))
        return SimpleNamespace(name="r2:retained-backups")

    monkeypatch.setattr(r2, "configured", configured)
    with open_database(tmp_path / "state") as database:
        source = drill.transport_for(
            database.state_dir, "r2:retained-backups", connection=database.connection
        )
        assert source.name == "r2:retained-backups"
        assert calls == [(database.connection, True)]
        with pytest.raises(drill.DrillError, match="differs from the recorded"):
            drill.transport_for(
                database.state_dir, "r2:another-bucket", connection=database.connection
            )


def test_development_drill_cannot_use_the_unbound_operator_hf_read_path(tmp_path):
    from types import SimpleNamespace

    from swingset.backup.transport import HuggingFaceSegments
    from swingset.state.environment import EnvironmentRefused

    state = tmp_path / "state"
    build_state(state)
    backup(state)
    calls = []

    def outside(*args, **kwargs):
        calls.append(args)
        raise AssertionError("a development drill must refuse before outside I/O")

    source = HuggingFaceSegments(
        "owner/archive", token="fixture", api=SimpleNamespace(list_repo_files=outside)
    )
    with pytest.raises(EnvironmentRefused, match="development database"):
        _drill(state, tmp_path / "scratch", transport=source)
    assert calls == []
    assert source.connection is None


def test_a_set_a_newer_backup_pruned_mid_drill_is_skipped_not_alerted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    build_state(state)
    first = backup(state)
    sets = state / "checkpoints" / "sets"

    def pruned(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Someone removes the set by hand while the drill installs it.
        (sets / "set_20261002T040000Z").mkdir()
        (sets / "set_20261002T040000Z" / "backup.json").write_text("{}")
        shutil.rmtree(sets / first["set_id"])
        raise drill.SetError(f"{first['set_id']} is missing")

    monkeypatch.setattr(drill, "drill_set", pruned)
    record = _drill(state, scratch)
    assert record == {"skipped": "set_pruned", "set_id": first["set_id"]}
    assert not (state / "ALERTS.log").exists()
    assert _row(state, first["set_id"]) == (None, None)
    assert list(scratch.iterdir()) == []
