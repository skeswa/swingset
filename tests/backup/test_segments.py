"""Segmented backup sets (architecture phase 4, WP6): the writer, parts,
segments, the index, pruning and segment recovery. Fakes only, no network."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import tarfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from swingset.backup.recovery import SegmentRecovery, local_recovery
from swingset.backup.restore_set import install
from swingset.backup.segments import (
    CLASSES,
    LOCAL_TRANSPORT,
    BackupError,
    create_set,
    local_sets,
    prune_sets,
    read_index,
    read_manifest,
)
from swingset.backup.transport import LocalDirectoryTransport
from swingset.fetch.archive import Archive
from swingset.state.db import SCHEMA_VERSION, open_database
from swingset.state.objects import Store, record
from swingset.state.retention import add_hold

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
VERSIONS = {"swingset": "test"}


def _blob(state: Path, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    path = state / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, mtime=0))
    return digest


def _extract(state: Path, value: object) -> str:
    body = json.dumps(value, sort_keys=True).encode()
    digest = hashlib.sha256(body).hexdigest()
    path = state / "extracts" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return digest


def build_state(state: Path) -> dict[str, Any]:
    """A schema-36 state with a file of every backed-up class, plus files a
    set must leave out."""
    found: dict[str, Any] = {}
    with open_database(state) as database:
        conn = database.connection
        run = database.start_run(NOW)
        body = _blob(state, b"User-agent: *\n")
        extract = _extract(state, {"held": True})
        store = Store(state)
        with database.transaction():
            conn.execute(
                "INSERT INTO hosts(host,pause_streak,robots_sha256) VALUES ('h.example',0,?)",
                (body,),
            )
            conn.execute(
                "INSERT INTO meta(key,value) VALUES ('input_bundle_hash','bundle-a') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            for cls in ("derivation_output", "parse_body", "label_body"):
                digest, stored = store.put_bytes(cls, f"{cls} rows\n".encode() * 50)
                record(conn, cls, digest, 50, stored, NOW)
                found[cls] = digest
            manifest = json.dumps({"built": [["year/2025", "a" * 64]]}).encode()
            digest, stored = store.put_bytes("release_manifest", manifest)
            record(conn, "release_manifest", digest, len(manifest), stored, NOW)
            found["release_manifest"] = digest
            conn.execute(
                "INSERT INTO release_partitions(partition,inputs_digest,artifact_digest,state,"
                "built_at,run_id) VALUES ('year/2025','i',?,'built',?,?)",
                ("b" * 64, NOW.isoformat(), run),
            )
        add_hold(state, conn, who="test", why="keep the extract", now=NOW, artifacts=[extract])
    bundle = state / "inputs" / "bundle-a"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text('{"bundle":"a"}')
    artifact = state / "partitions" / "bb" / ("b" * 64)
    artifact.mkdir(parents=True)
    (artifact / "sidecar.json").write_text('{"generations":[]}')
    (artifact / "rows.parquet").write_bytes(os.urandom(2048))
    # A phase 3 manifest that the published candidate names.
    legacy = json.dumps({"built": [["year/2024", "b" * 64]]}).encode()
    legacy_digest = hashlib.sha256(legacy).hexdigest()
    (state / "partitions" / "manifests").mkdir(parents=True)
    (state / "partitions" / "manifests" / f"{legacy_digest}.json").write_bytes(legacy)
    candidate = state / "candidates" / "cand_base"
    (candidate / "_meta").mkdir(parents=True)
    (candidate / "BUILT").write_text('{"manifest_hash":"m"}')
    (candidate / "PUBLISHED").write_text(
        json.dumps({"commit": "abc", "manifest_digest": legacy_digest, "verified_at": "x"})
    )
    (candidate / "_meta" / "manifest.json").write_text(
        json.dumps(
            {
                "release_policy": {"mode": "partitioned", "manifest_digest": legacy_digest},
                "files": {},
            }
        )
    )
    (candidate / "data.parquet").write_bytes(b"published rows")
    (state / "baseline").symlink_to(Path("candidates/cand_base"))
    (state / "archive-cdx" / "q1").mkdir(parents=True)
    (state / "archive-cdx" / "q1" / "page-0.json").write_text('{"rows":[]}')
    (state / "ALERTS.log").write_text("[old] alert\n")
    # Never backed up.
    (state / "venv").mkdir()
    (state / "venv" / "secret").write_text("excluded")
    (state / "tmp").mkdir(exist_ok=True)
    (state / "tmp" / "scratch").write_text("excluded")
    (state / "gc").mkdir(exist_ok=True)
    (state / "gc" / "plan.json").write_text("{}")
    found.update(body=body, extract=extract, legacy=legacy_digest)
    return found


def local(state: Path) -> LocalDirectoryTransport:
    return LocalDirectoryTransport(state / "checkpoints", name=LOCAL_TRANSPORT)


def backup(state: Path, *, now: datetime = NOW, **options: Any) -> dict[str, Any]:
    transport = options.pop("transport", None) or local(state)
    with open_database(state) as database:
        return create_set(database, transport, now=now, versions=VERSIONS, **options)


def index_of(state: Path, set_id: str) -> dict[str, Any]:
    return {
        entry.path: entry
        for entry in read_index(state / "checkpoints" / "sets" / set_id / "index.jsonl.zst")
    }


def test_a_set_round_trips_every_class(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    result = backup(state)
    assert set(result["classes"]) == set(CLASSES)
    entries = index_of(state, result["set_id"])
    assert not [path for path in entries if path.split("/")[0] in {"venv", "tmp", "gc"}]
    assert "partitions/manifests" in "\n".join(entries)
    manifest = read_manifest(state / "checkpoints" / "sets" / result["set_id"])
    assert manifest["baseline_candidate"] == "cand_base"
    assert manifest["schema_version"] == SCHEMA_VERSION
    restored = tmp_path / "restored"
    summary = install(local(state), restored)
    assert summary["items"] == len(entries)
    for path in entries:
        assert (restored / path).read_bytes() == (state / path).read_bytes(), path
    assert (restored / "baseline").resolve().name == "cand_base"
    assert not (restored / "venv").exists() and not (restored / "tmp").exists()
    copy = sqlite3.connect(restored / "state.sqlite")
    assert copy.execute("SELECT host FROM hosts").fetchall() == [("h.example",)]
    assert copy.execute("SELECT count(*) FROM backup_sets").fetchone() == (0,)
    copy.close()
    with open_database(state) as database:
        row = database.connection.execute(
            "SELECT objects,new_objects,transport,parts FROM backup_sets"
        ).fetchone()
        rows = database.connection.execute("SELECT count(*) FROM backup_objects").fetchone()[0]
    assert tuple(row) == (len(entries), len(entries), LOCAL_TRANSPORT, 1)
    assert rows == len(entries)


def test_a_second_set_sends_only_new_files(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    first = backup(state)
    body = _blob(state, b"a new page")
    with open_database(state) as database, database.transaction() as conn:
        conn.execute(
            "INSERT INTO hosts(host,pause_streak,robots_sha256) VALUES ('n.example',0,?)", (body,)
        )
    with (state / "ALERTS.log").open("a") as alerts:
        alerts.write("[new] alert\n")
    second = backup(state, now=NOW + timedelta(days=1))
    assert second["new_items"] == 2
    assert len(second["new_segments"]) == 1
    entries = index_of(state, second["set_id"])
    assert (
        entries[f"blobs/sha256/{body[:2]}/{body[2:4]}/{body}"].segment
        == (second["new_segments"][0])
    )
    assert entries["ALERTS.log"].segment == second["new_segments"][0]
    assert set(first["new_segments"]) <= {entry.segment for entry in entries.values()}
    # The first set is gone (neither newest nor drilled); its segment stays,
    # since the second set names it.
    assert local_sets(state / "checkpoints") == [second["set_id"]]
    restored = tmp_path / "restored"
    install(local(state), restored)
    assert (restored / "ALERTS.log").read_text() == "[old] alert\n[new] alert\n"


def test_parts_split_and_rejoin(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    with open_database(state) as database, database.transaction() as conn:
        conn.execute("CREATE TABLE filler(x BLOB)")
        conn.executemany("INSERT INTO filler VALUES (?)", ((os.urandom(65536),) for _ in range(60)))
    result = backup(state, part_bytes=1 << 20)
    manifest = read_manifest(state / "checkpoints" / "sets" / result["set_id"])
    parts = manifest["database"]["parts"]
    assert len(parts) >= 3
    assert all(part["bytes"] <= 1 << 20 for part in parts)
    assert all(part["bytes"] == 1 << 20 for part in parts[:-1])
    restored = tmp_path / "restored"
    install(local(state), restored)
    copy = sqlite3.connect(restored / "state.sqlite")
    assert copy.execute("SELECT count(*) FROM filler").fetchone() == (60,)
    copy.close()


def test_segments_stay_under_their_size(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    for index in range(12):
        (state / "archive-cdx" / "q1" / f"big-{index}.json").write_bytes(os.urandom(20000))
    result = backup(state, segment_bytes=64 * 1024)
    assert len(result["new_segments"]) >= 4
    for segment in result["new_segments"]:
        path = state / "checkpoints" / "segments" / f"{segment}.tar"
        assert path.stat().st_size <= 64 * 1024
        assert hashlib.sha256(path.read_bytes()).hexdigest() == segment
        with tarfile.open(path) as archive:
            assert all(member.mtime == 0 and member.uid == 0 for member in archive)
    install(local(state), tmp_path / "restored")
    with pytest.raises(BackupError, match="does not fit"):
        (state / "archive-cdx" / "huge.json").write_bytes(os.urandom(80000))
        backup(state, now=NOW + timedelta(hours=1), segment_bytes=64 * 1024)


def test_a_corrupt_or_truncated_file_is_left_out_and_the_set_still_written(
    tmp_path: Path,
) -> None:
    """A file that does not match its digest, or a pack cut short, no longer
    stops every set: the set holds everything else, names the two under
    `missing`, alerts, refuses to install, and its segment stays a whole tar."""
    from swingset.backup.restore_set import SetError

    state = tmp_path / "state"
    found = build_state(state)
    body, pack = found["body"], found["derivation_output"]
    blob = state / "blobs" / "sha256" / body[:2] / body[2:4] / body
    original = blob.read_bytes()
    blob.write_bytes(gzip.compress(b"something else", mtime=0))
    packed = Store(state).path("derivation_output", pack)
    whole = packed.read_bytes()
    packed.write_bytes(whole[: len(whole) // 2])
    result = backup(state)
    assert result["missing"] == 2 and result["alerted"] is True
    manifest = read_manifest(state / "checkpoints" / "sets" / result["set_id"])
    reasons = {(cls, key): reason for cls, key, reason in manifest["missing"]}
    assert reasons[("body", body)] == "does not match its digest"
    assert reasons[("derivation_output", pack)].startswith("does not decompress")
    entries = index_of(state, result["set_id"])
    assert f"blobs/sha256/{body[:2]}/{body[2:4]}/{body}" not in entries
    assert f"objects/derivation_output/{pack[:2]}/{pack}" not in entries
    assert "ALERTS.log" in entries and result["items"] == len(entries)
    for segment in result["new_segments"]:
        with tarfile.open(state / "checkpoints" / "segments" / f"{segment}.tar") as archive:
            assert len(archive.getmembers()) == len(entries) + 2
    for integrity in ("quick", "full"):
        with pytest.raises(SetError):
            install(local(state), tmp_path / f"restored-{integrity}", integrity=integrity)
    # Repaired by hand, the next set holds both and installs.
    blob.write_bytes(original)
    packed.write_bytes(whole)
    second = backup(state, now=NOW + timedelta(days=1))
    # The two files and ALERTS.log, which gained the alert.
    assert second["missing"] == 0 and second["new_items"] == 3
    install(local(state), tmp_path / "restored")


def test_a_lost_pack_comes_back_from_the_last_set(tmp_path: Path) -> None:
    """The pack of an evicted generation is its only copy outside the sets
    (F40). Lost after set 1, it is put back from set 1's segment, set 2 is
    complete and installs, and pruning keeps the segment set 2 names."""
    state = tmp_path / "state"
    found = build_state(state)
    first = backup(state)
    lost = found["derivation_output"]
    relative = f"objects/derivation_output/{lost[:2]}/{lost}"
    segment = index_of(state, first["set_id"])[relative].segment
    path = Store(state).path("derivation_output", lost)
    whole = path.read_bytes()
    path.unlink()
    second = backup(state, now=NOW + timedelta(days=1))
    assert second["missing"] == 0 and second["restored"] == 1 and second["absent"] == 0
    assert path.read_bytes() == whole
    assert index_of(state, second["set_id"])[relative].segment == segment
    assert second["pruned"]["removed_sets"] == [first["set_id"]]
    assert (state / "checkpoints" / "segments" / f"{segment}.tar").is_file()
    restored = tmp_path / "restored"
    install(local(state), restored, set_id=second["set_id"], integrity="quick")
    assert (restored / relative).read_bytes() == whole


def test_a_lost_pack_with_no_good_local_copy_is_named_by_its_earlier_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no local copy can be put back, the segment that holds the pack
    (`backup_objects`) still stands for it, so set 2 is complete; with no
    such segment the pack is `missing`."""
    import swingset.backup.recovery as recovery_module
    from swingset.state.objects import ObjectUnavailable

    def refuse(self: Any, store: Any, cls: str, sha256: str) -> dict[str, str]:
        raise ObjectUnavailable(f"{cls}/{sha256} is in no local backup set")

    state = tmp_path / "state"
    found = build_state(state)
    first = backup(state)
    lost = found["derivation_output"]
    relative = f"objects/derivation_output/{lost[:2]}/{lost}"
    segment = index_of(state, first["set_id"])[relative].segment
    path = Store(state).path("derivation_output", lost)
    whole = path.read_bytes()
    path.unlink()
    monkeypatch.setattr(recovery_module.SegmentRecovery, "recover_object", refuse)
    second = backup(state, now=NOW + timedelta(days=1))
    assert second["missing"] == 0 and second["absent"] == 1 and not path.exists()
    assert index_of(state, second["set_id"])[relative].segment == segment
    restored = tmp_path / "restored"
    install(local(state), restored, set_id=second["set_id"], integrity="quick")
    assert (restored / relative).read_bytes() == whole
    # Once no held segment has it, the pack is missing again.
    for name in (state / "checkpoints" / "segments").iterdir():
        name.unlink()
    third = backup(state, now=NOW + timedelta(days=2))
    manifest = read_manifest(state / "checkpoints" / "sets" / third["set_id"])
    assert third["missing"] == 1
    assert manifest["missing"][0][:2] == ["derivation_output", lost]


def test_a_lost_segment_is_written_again(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    first = backup(state)
    for segment in first["new_segments"]:
        (state / "checkpoints" / "segments" / f"{segment}.tar").unlink()
    second = backup(state, now=NOW + timedelta(days=1))
    assert second["new_items"] == second["items"]
    install(local(state), tmp_path / "restored")


def test_pruning_keeps_the_newest_and_the_newest_drilled_set(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    first = backup(state, prune=False)
    with open_database(state) as database, database.transaction() as conn:
        conn.execute(
            "UPDATE backup_sets SET drilled_at=?,drill_json='{}' WHERE set_id=?",
            (NOW.isoformat(), first["set_id"]),
        )
    (state / "archive-cdx" / "q1" / "page-0.json").write_text('{"rows":[1]}')
    second = backup(state, now=NOW + timedelta(days=1), prune=False)
    (state / "archive-cdx" / "q1" / "page-0.json").write_text('{"rows":[2]}')
    third = backup(state, now=NOW + timedelta(days=2), prune=False)
    # A scratch set and a half-written segment from a crashed backup.
    (state / "checkpoints" / "sets" / ".tmp-set_20261001T000000Z").mkdir()
    (state / "checkpoints" / "segments" / ".writing-dead.tar").write_bytes(b"x")
    old = (NOW - timedelta(days=3)).timestamp()
    for path in (state / "checkpoints").rglob("*"):
        os.utime(path, (old, old), follow_symlinks=False)
    with open_database(state) as database:
        pruned = prune_sets(database, now=NOW + timedelta(days=2))
        kept_rows = database.connection.execute(
            "SELECT DISTINCT segment FROM backup_objects"
        ).fetchall()
    assert pruned["kept"] == sorted([first["set_id"], third["set_id"]])
    assert second["set_id"] in pruned["removed_sets"]
    assert pruned["removed_segments"] == 1  # only the second set's own segment
    assert not (state / "checkpoints" / "segments" / ".writing-dead.tar").exists()
    assert not (state / "checkpoints" / "sets" / ".tmp-set_20261001T000000Z").exists()
    assert second["new_segments"][0] not in {row[0] for row in kept_rows}
    for set_id in pruned["kept"]:
        install(local(state), tmp_path / set_id, set_id=set_id)


# 15 s: a writer thread commits throughout one set; extended (core time).
@pytest.mark.extended
def test_a_set_taken_while_a_writer_commits_is_consistent(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    batch = 25
    with open_database(state) as database, database.transaction() as conn:
        conn.execute("CREATE TABLE writes(batch INTEGER, n INTEGER)")
        conn.execute("CREATE TABLE filler(x BLOB)")
        conn.executemany("INSERT INTO filler VALUES (?)", ((os.urandom(65536),) for _ in range(80)))
    stop = threading.Event()
    committed: list[int] = []

    def writer() -> None:
        conn = sqlite3.connect(state / "state.sqlite", timeout=30, isolation_level=None)
        number = 0
        while not stop.is_set():
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "INSERT INTO writes VALUES (?,?)", ((number, item) for item in range(batch))
            )
            conn.execute("COMMIT")
            committed.append(number)
            number += 1
        conn.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        while len(committed) < 3:
            threading.Event().wait(0.01)
        result = backup(state)
    finally:
        stop.set()
        thread.join()
    restored = tmp_path / "restored"
    install(local(state), restored, set_id=result["set_id"])
    copy = sqlite3.connect(restored / "state.sqlite")
    counts = copy.execute("SELECT batch,count(*) FROM writes GROUP BY batch").fetchall()
    copy.close()
    assert counts and all(count == batch for _, count in counts)
    assert len(counts) <= len(committed)


def test_segment_recovery_restores_a_deleted_blob(tmp_path: Path) -> None:
    state = tmp_path / "state"
    found = build_state(state)
    backup(state)
    body, extract = found["body"], found["extract"]
    blob = state / "blobs" / "sha256" / body[:2] / body[2:4] / body
    blob.unlink()
    (state / "extracts" / extract).write_bytes(b"damaged")
    archive = Archive(state, recovery=local_recovery(state, maximum_schema_version=SCHEMA_VERSION))
    import swingset.backup.recovery as recovery_module

    reads: list[Path] = []
    scans: list[object] = []
    real = recovery_module.read_index
    real_open = tarfile.open

    def counted(path: Path) -> Any:
        reads.append(path)
        return real(path)

    def scanned(*args: Any, **kwargs: Any) -> Any:
        scans.append(args[0] if args else kwargs.get("name"))
        return real_open(*args, **kwargs)

    recovery_module.read_index = counted  # type: ignore[assignment]
    recovery_module.tarfile.open = scanned  # type: ignore[assignment]
    try:
        assert archive.read_body(body) == b"User-agent: *\n"
        assert archive.read_extract(extract) == {"held": True}
    finally:
        recovery_module.read_index = real  # type: ignore[assignment]
        recovery_module.tarfile.open = real_open  # type: ignore[assignment]
    # Two files from one set and one segment: the index is read once, and the
    # segment's headers are scanned once; each file is then one seek.
    assert len(reads) == 1 and len(scans) == 1
    assert archive.recovery is not None
    restored = archive.recovery.restored  # type: ignore[attr-defined]
    assert [item["kind"] for item in restored] == ["body", "extract"]
    # A target inside checkpoints/ is refused.
    recovery = SegmentRecovery(state / "checkpoints")
    with pytest.raises(OSError, match="immutable_checkpoint"):
        recovery.recover("body", body, state / "checkpoints" / "x")


def test_a_set_refuses_an_open_transaction(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    with open_database(state) as database:
        database.connection.execute("BEGIN")
        with pytest.raises(BackupError, match="inside a transaction"):
            create_set(database, local(state), now=NOW, versions=VERSIONS)
        database.connection.execute("ROLLBACK")


def test_a_missing_named_file_is_recorded_and_the_set_refuses_to_restore(tmp_path: Path) -> None:
    """One objects row without its file no longer fails every set: the set
    is written, names it under `missing`, alerts, and refuses to install."""
    from swingset.backup.restore_set import SetError

    state = tmp_path / "state"
    found = build_state(state)
    lost = found["derivation_output"]
    Store(state).path("derivation_output", lost).unlink()
    result = backup(state)
    assert result["missing"] == 1 and result["alerted"] is True
    manifest = read_manifest(state / "checkpoints" / "sets" / result["set_id"])
    assert manifest["missing"] == [["derivation_output", lost, "objects row without a file"]]
    assert f"objects/derivation_output/{lost[:2]}/{lost}" not in index_of(state, result["set_id"])
    line = (state / "ALERTS.log").read_text().splitlines()[-1]
    assert line.startswith("2026-10-02T12:00:00Z [backup-incomplete] set ")
    assert "(lost files)" in line and "`gc --collect` drops only rows nothing names" in line
    for integrity in ("quick", "full"):
        with pytest.raises(SetError, match="restored objects are incomplete: 1 missing"):
            install(local(state), tmp_path / f"restored-{integrity}", integrity=integrity)


def test_a_pack_row_without_an_objects_row_is_missing(tmp_path: Path) -> None:
    from swingset.backup.segments import unindexed_packs

    state = tmp_path / "state"
    build_state(state)
    with open_database(state) as database:
        conn = database.connection
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master")}
        assert unindexed_packs(conn, tables) == []
        # A pack row naming an object nobody indexed (foreign keys aside).
        conn.execute("PRAGMA foreign_keys=OFF")
        with database.transaction():
            conn.execute(
                "INSERT INTO label_body_packs(generation_id,pack_digest,packed_at) "
                "VALUES ('dg_x',?,?)",
                ("c" * 64, NOW.isoformat()),
            )
        conn.execute("PRAGMA foreign_keys=ON")
        assert unindexed_packs(conn, tables) == [
            ["label_body", "c" * 64, "label_body_packs names no objects row"]
        ]
    # The set names it under `missing`, so it never restores as whole.
    result = backup(state)
    manifest = read_manifest(state / "checkpoints" / "sets" / result["set_id"])
    assert manifest["missing"] == [
        ["label_body", "c" * 64, "label_body_packs names no objects row"]
    ]


def test_a_set_refuses_before_its_copy_without_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swingset.state import disk

    state = tmp_path / "state"
    build_state(state)
    with open_database(state) as database:
        conn = database.connection
        in_use = int(conn.execute("PRAGMA page_size").fetchone()[0]) * (
            int(conn.execute("PRAGMA page_count").fetchone()[0])
            - int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        )
    monkeypatch.setattr(
        disk, "room", lambda directory, **options: disk.Room(in_use + 99, None, None, False)
    )
    with pytest.raises(BackupError, match="a backup set needs about"):
        backup(state, headroom=100)
    assert [path.name for path in (state / "checkpoints" / "sets").iterdir()] == []
    result = backup(state, headroom=99)
    assert result["room"]["needed_bytes"] == in_use + 99


def test_a_missing_input_bundle_refuses_before_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import swingset.backup.segments as segments_module

    state = tmp_path / "state"
    build_state(state)
    shutil.rmtree(state / "inputs" / "bundle-a")
    copied: list[object] = []
    monkeypatch.setattr(segments_module, "_vacuum_into", lambda *args: copied.append(args))
    with pytest.raises(BackupError, match="referenced input bundle is missing: bundle-a"):
        backup(state)
    assert copied == []
    assert [path.name for path in (state / "checkpoints" / "sets").iterdir()] == []


def test_a_killed_backup_scratch_goes_before_the_next_set(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    sets = state / "checkpoints" / "sets"
    segments = state / "checkpoints" / "segments"
    sets.mkdir(parents=True)
    segments.mkdir(parents=True)
    old, young = sets / ".tmp-set_20261001T040000Z", sets / ".tmp-set_20261002T113000Z"
    for directory in (old, young):
        directory.mkdir()
        (directory / "db.sqlite").write_bytes(b"x" * 1000)
    (segments / ".writing-dead.tar").write_bytes(b"y" * 10)
    stamp = (NOW - timedelta(days=2)).timestamp()
    for path in (old, old / "db.sqlite", segments / ".writing-dead.tar"):
        os.utime(path, (stamp, stamp))
    result = backup(state, prune=False)
    assert result["cleaned"] == {
        "removed": ["sets/.tmp-set_20261001T040000Z", "segments/.writing-dead.tar"],
        "removed_bytes": 1010,
    }
    # A younger scratch may belong to a backup still running elsewhere.
    assert young.is_dir() and not old.exists()


def test_a_failed_publish_removes_the_unrecorded_set(tmp_path: Path) -> None:
    from swingset.backup.transport import TransportError

    state = tmp_path / "state"
    build_state(state)
    first = backup(state)

    class Failing(LocalDirectoryTransport):
        def publish(self, local: Path, set_id: str, segments: Any) -> str | None:
            raise TransportError("archive commit failed")

    with pytest.raises(TransportError):
        backup(
            state,
            now=NOW + timedelta(days=1),
            transport=Failing(state / "checkpoints", name=LOCAL_TRANSPORT),
        )
    assert local_sets(state / "checkpoints") == [first["set_id"]]
    with open_database(state) as database:
        assert database.connection.execute("SELECT count(*) FROM backup_sets").fetchone()[0] == 1
