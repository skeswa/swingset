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


def _artifact(state: Path) -> str:
    rows = b"partition rows"
    sidecar = canonical_json(
        {
            "generations": [],
            "tables": {
                "rows": {"file": "rows.parquet", "sha256": hashlib.sha256(rows).hexdigest()}
            },
        }
    )
    digest = hashlib.sha256(sidecar).hexdigest()
    directory = artifact_directory(state, digest)
    directory.mkdir(parents=True)
    (directory / "sidecar.json").write_bytes(sidecar)
    (directory / "rows.parquet").write_bytes(rows)
    return digest


def _candidate(state: Path, name: str, policy: dict[str, Any]) -> None:
    candidate = state / "candidates" / name
    (candidate / "_meta").mkdir(parents=True)
    (candidate / "BUILT").write_text('{"manifest_hash":"m"}')
    (candidate / "PUBLISHED").write_text(json.dumps({"commit": "abc", "verified_at": "x"}))
    (candidate / "_meta" / "manifest.json").write_text(json.dumps({"release_policy": policy}))
    (state / "baseline").symlink_to(Path("candidates") / name)


def build_state(state: Path, policy: dict[str, Any] | None = None) -> str:
    """A schema-36 state whose baseline is a partitioned release with one artifact."""
    with open_database(state) as database:
        artifact = _artifact(state)
        body = canonical_json({"format": manifest.FORMAT, "built": [["year/2025", artifact]]})
        digest = hashlib.sha256(body).hexdigest()
        manifest.store(database, body, digest, now=NOW)
    _candidate(state, "cand_base", policy or {"mode": "partitioned", "manifest_digest": digest})
    return digest


def backup(state: Path, *, now: datetime = NOW) -> dict[str, Any]:
    transport = LocalDirectoryTransport(state / "checkpoints", name=LOCAL_TRANSPORT)
    with open_database(state) as database:
        return create_set(database, transport, now=now, versions={"swingset": "test"})


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
                "SELECT drilled_at,drill_json FROM backup_sets WHERE set_id=?", (set_id,)
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
    assert record["install"]["integrity"] == "quick"
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
                "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
                "VALUES (?,?)",
                (identifier, body.replace("2026", "2025") if corrupt else body),
            )
    public = {"format": "release-closure-public-v1", "private_digest": identifier}
    return {**public, "digest": digest(public)}


def test_a_closure_baseline_streams_its_proof_against_the_commitment(tmp_path: Path) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    public = {"format": "release-closure-public-v1", "private_digest": "p" * 64}
    build_state(state, {"mode": "closure", "closure": public})
    made = backup(state)
    # A public commitment that does not hash to its digest fails the drill.
    record = _drill(state, scratch)
    assert record["passed"] is False and "public_closure_digest_mismatch" in record["error"]
    # The failure came after the install: the scratch keeps the restored
    # files, not the restored database, which would refuse the next set's room.
    failed = scratch / f"swingset-drill-{made['set_id']}"
    assert (failed / "state" / "candidates" / "cand_base" / "BUILT").is_file()
    assert not (failed / "state" / "state.sqlite").exists()
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
    meta.write_text(json.dumps({"release_policy": {"mode": "closure", "closure": public}}))
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
    from swingset.state import rwlock

    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    build_state(state)
    result = backup(state)
    held = rwlock.try_loop(state, "backup")
    assert held is not None
    try:
        record = _drill(state, scratch)
        # A backup cannot start while the drill holds the lock either.
        assert rwlock.try_loop(state, "backup") is None
    finally:
        held.release()
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
    again = rwlock.try_loop(state, "backup")
    assert again is not None
    again.release()


def test_a_killed_drill_leaves_a_started_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, scratch = tmp_path / "state", tmp_path / "var-tmp"
    build_state(state)
    result = backup(state)

    def killed(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt  # stands in for SIGKILL: nothing after it runs

    monkeypatch.setattr(drill, "install", killed)
    with pytest.raises(KeyboardInterrupt):
        _drill(state, scratch)
    drilled, body = _row(state, result["set_id"])
    started = json.loads(body)
    assert drilled is None
    assert started["passed"] is None and started["at"] == NOW.isoformat()
    assert started["set_id"] == result["set_id"]
    # The backup lock went with the drill.
    held = drill.rwlock.try_loop(state, "backup")
    assert held is not None
    held.release()


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

    monkeypatch.setattr(drill, "install", pruned)
    record = _drill(state, scratch)
    assert record == {"skipped": "set_pruned", "set_id": first["set_id"]}
    assert not (state / "ALERTS.log").exists()
    assert _row(state, first["set_id"]) == (None, None)
    assert list(scratch.iterdir()) == []
