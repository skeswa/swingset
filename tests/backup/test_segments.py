"""Segmented backup sets (architecture phase 4, WP6): the writer, parts,
segments, the index, pruning and segment recovery. Fakes only, no network."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
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
from swingset.build.files import canonical_json
from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.state import engine
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
    """Native schema37 history with rooted objects in every backup class."""
    from test_drill import _artifact
    from test_output_packs import evict, write

    from swingset.build import manifest as release_manifest
    from swingset.schedule.derive import derive_one
    from swingset.state.inputs import accept, capture
    from swingset.state.work import WorkUnit, enqueue

    found: dict[str, Any] = {}
    config, overrides = (
        state.parent / (state.name + "-config"),
        state.parent / (state.name + "-overrides"),
    )
    config.mkdir(exist_ok=True)
    overrides.mkdir(exist_ok=True)
    (config / "hosts.toml").write_text("[hosts]\n")
    (config / "sources.toml").write_text("[sources]\n")
    bundle = capture(config, overrides, state, VERSIONS)
    found["bundle"] = bundle.digest
    with open_database(state) as database:
        conn = database.connection
        clock = FakeClock(NOW)
        accept(database, bundle, clock)
        run = database.start_run(NOW)
        body = _blob(state, b"User-agent: *\n")
        extract = _extract(state, {"held": True})
        # Historical nonempty public bytes remain immutable; a separate real
        # empty-year projector is the ordinary cache anchor activation rebuilds.
        artifact = _artifact(database, unit=WorkUnit("project", "history", "partition-fixture"))
        for ordinary in (
            WorkUnit("project", "inventory", "all"),
            WorkUnit("project", "year", "2024"),
        ):
            enqueue(conn, (ordinary,), enqueued_at=NOW.isoformat())
            outcome = derive_one(database, Archive(state), ordinary, bundle, clock, run)
            assert not outcome.failed and outcome.reason is None
        # A detached historical generation whose only output copy is its pack.
        write(
            database,
            "dg_pack",
            ("project", "history", "pack"),
            [
                (
                    "coverage",
                    '[2025,"test","origin"]',
                    {"year": 2025, "source": "test", "via": "origin"},
                )
            ],
            point=False,
        )
        evict(database, "dg_pack")
        found["derivation_output"] = conn.execute(
            "SELECT output_digest FROM derivation_generations WHERE generation_id='dg_pack'"
        ).fetchone()[0]
        store = Store(state)
        with database.transaction():
            conn.execute(
                "INSERT INTO hosts(host,pause_streak,robots_sha256) VALUES('h.example',0,%s)",
                (body,),
            )
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('input_bundle_hash',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (bundle.digest,),
            )
            for cls in ("parse_body", "label_body"):
                value = f"{cls} rows\n".encode() * 50
                digest, stored = store.put_bytes(cls, value)
                record(conn, cls, digest, len(value), stored, NOW)
                found[cls] = digest
            release = canonical_json(
                {"format": release_manifest.FORMAT, "built": [["year/2025", artifact]]}
            )
            digest, stored = store.put_bytes("release_manifest", release)
            record(conn, "release_manifest", digest, len(release), stored, NOW)
            found["release_manifest"] = digest
            conn.execute(
                "INSERT INTO release_partitions(partition,inputs_digest,artifact_digest,state,built_at,run_id) VALUES('year/2025','i',%s,'built',%s,%s)",
                (artifact, NOW.isoformat(), run),
            )
        add_hold(state, conn, who="test", why="keep the extract", now=NOW, artifacts=[extract])
    legacy_digest = hashlib.sha256(release).hexdigest()
    (state / "partitions" / "manifests").mkdir(parents=True)
    (state / "partitions" / "manifests" / f"{legacy_digest}.json").write_bytes(release)
    candidate = state / "candidates" / "cand_base"
    (candidate / "_meta").mkdir(parents=True)
    candidate_manifest = canonical_json(
        {
            "candidate_id": "cand_base",
            "release_policy": {"mode": "partitioned", "manifest_digest": legacy_digest},
            "files": {},
        }
    )
    (candidate / "BUILT").write_bytes(
        canonical_json({"manifest_hash": hashlib.sha256(candidate_manifest).hexdigest()})
    )
    (candidate / "PUBLISHED").write_text(
        json.dumps({"commit": "abc", "manifest_digest": legacy_digest, "verified_at": "x"})
    )
    (candidate / "_meta" / "manifest.json").write_bytes(candidate_manifest)
    (candidate / "data.parquet").write_bytes(
        b"published rows are rebuilt or fetched, never history backup bytes"
    )
    (state / "baseline").symlink_to(Path("candidates/cand_base"))
    (state / "archive-cdx" / "q1").mkdir(parents=True)
    (state / "archive-cdx" / "q1" / "page-0.json").write_text('{"rows":[]}')
    (state / "ALERTS.log").write_text("[old] alert\n")
    (state / "venv").mkdir()
    (state / "venv" / "secret").write_text("excluded")
    (state / "tmp").mkdir(exist_ok=True)
    (state / "tmp" / "scratch").write_text("excluded")
    (state / "gc").mkdir(exist_ok=True)
    (state / "gc" / "plan.json").write_text("{}")
    found.update(body=body, extract=extract, legacy=legacy_digest, artifact=artifact)
    return found


def local(state: Path) -> LocalDirectoryTransport:
    return LocalDirectoryTransport(state / "checkpoints", name=LOCAL_TRANSPORT)


def backup(state: Path, *, now: datetime = NOW, **options: Any) -> dict[str, Any]:
    transport = options.pop("transport", None) or local(state)
    options.setdefault("headroom", 0)
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
    assert not (restored / "venv").exists()
    assert sorted(path.name for path in (restored / "tmp").iterdir()) == ["restore-history"]
    assert not (restored / "tmp/scratch").exists()
    copy = engine.connect(engine.resolve_dsn(restored))
    assert [tuple(row) for row in copy.execute("SELECT host FROM hosts")] == [("h.example",)]
    assert tuple(copy.execute("SELECT COUNT(*) FROM backup_sets").fetchone()) == (0,)
    copy.close()
    with open_database(state) as database:
        row = database.connection.execute(
            "SELECT objects, new_objects, transport, parts FROM backup_sets"
        ).fetchone()
        rows = database.connection.execute("SELECT COUNT(*) FROM backup_objects").fetchone()[0]
    assert tuple(row) == (len(entries), len(entries), LOCAL_TRANSPORT, 1)
    assert rows == len(entries)


def test_a_second_set_sends_only_new_files(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    first = backup(state)
    body = _blob(state, b"a new page")
    with open_database(state) as database, database.transaction() as conn:
        conn.execute(
            "INSERT INTO hosts (host, pause_streak, robots_sha256) VALUES ('n.example', 0, %s)",
            (body,),
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
        conn.executemany(
            "INSERT INTO meta(key,value) VALUES(%s,%s)",
            ((f"filler/{index}", os.urandom(65536).hex()) for index in range(60)),
        )
    result = backup(state, part_bytes=1 << 20)
    manifest = read_manifest(state / "checkpoints" / "sets" / result["set_id"])
    parts = manifest["database"]["parts"]
    assert len(parts) >= 3
    assert all(part["bytes"] <= 1 << 20 for part in parts)
    assert all(part["bytes"] == 1 << 20 for part in parts[:-1])
    restored = tmp_path / "restored"
    install(local(state), restored)
    copy = engine.connect(engine.resolve_dsn(restored))
    assert tuple(
        copy.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'filler/%%'").fetchone()
    ) == (60,)
    copy.close()


def test_segments_stay_under_their_size(tmp_path: Path) -> None:
    state = tmp_path / "state"
    build_state(state)
    # A real captured runtime includes individual fixture files near 1 MiB.
    limit = 2 * 1024 * 1024
    for index in range(12):
        (state / "archive-cdx" / "q1" / f"big-{index}.json").write_bytes(os.urandom(700000))
    result = backup(state, segment_bytes=limit)
    assert len(result["new_segments"]) >= 4
    for segment in result["new_segments"]:
        path = state / "checkpoints" / "segments" / f"{segment}.tar"
        assert path.stat().st_size <= limit
        assert hashlib.sha256(path.read_bytes()).hexdigest() == segment
        with tarfile.open(path) as archive:
            assert all(member.mtime == 0 and member.uid == 0 for member in archive)
    install(local(state), tmp_path / "restored")
    with pytest.raises(BackupError, match="does not fit"):
        (state / "archive-cdx" / "huge.json").write_bytes(os.urandom(limit + 1))
        backup(state, now=NOW + timedelta(hours=1), segment_bytes=limit)


def test_corrupt_or_truncated_files_refuse_success_until_both_are_repaired(tmp_path: Path) -> None:
    """A success receipt now requires an actual restore; bad bytes keep the prior set."""
    state = tmp_path / "state"
    found = build_state(state)
    body, pack = found["body"], found["derivation_output"]
    blob = state / "blobs" / "sha256" / body[:2] / body[2:4] / body
    original = blob.read_bytes()
    packed = Store(state).path("derivation_output", pack)
    whole = packed.read_bytes()
    blob.write_bytes(gzip.compress(b"something else", mtime=0))
    packed.write_bytes(whole[: len(whole) // 2])
    with pytest.raises(ValueError, match="corrupt blob"):
        backup(state, now=NOW + timedelta(hours=1))
    assert local_sets(state / "checkpoints") == []
    # Repairing the body alone must still reject the independently bad pack.
    blob.write_bytes(original)
    with pytest.raises((ValueError, EOFError, gzip.BadGzipFile)):
        backup(state, now=NOW + timedelta(hours=2))
    assert local_sets(state / "checkpoints") == []
    packed.write_bytes(whole)
    second = backup(state, now=NOW + timedelta(days=1))
    assert second["missing"] == 0
    install(local(state), tmp_path / "restored", set_id=second["set_id"])


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


def test_a_lost_pack_with_no_local_segment_recovers_from_the_earlier_remote_segment(
    tmp_path: Path,
) -> None:
    """The stored segment identity repairs a genuinely missing local pack and tar."""
    state = tmp_path / "state"
    found = build_state(state)
    remote = LocalDirectoryTransport(tmp_path / "archive")
    first = backup(state, transport=remote)
    lost = found["derivation_output"]
    relative = f"objects/derivation_output/{lost[:2]}/{lost}"
    segment = index_of(state, first["set_id"])[relative].segment
    path = Store(state).path("derivation_output", lost)
    whole = path.read_bytes()
    path.unlink()
    (state / "checkpoints" / "segments" / f"{segment}.tar").unlink()
    second = backup(state, now=NOW + timedelta(days=1), transport=remote)
    assert second["missing"] == 0 and second["restored"] == 1
    assert path.read_bytes() == whole
    assert index_of(state, second["set_id"])[relative].segment == segment
    restored = tmp_path / "restored"
    install(remote, restored, set_id=second["set_id"], integrity="quick")
    assert (restored / relative).read_bytes() == whole
    # Once neither verified local nor remote copy exists, refuse a success.
    for directory in (state / "checkpoints" / "segments", tmp_path / "archive" / "segments"):
        for file in directory.iterdir():
            file.unlink()
    path.unlink()
    with pytest.raises(ValueError, match="missing captured file"):
        backup(state, now=NOW + timedelta(days=2), transport=remote)


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
            "UPDATE backup_sets SET drilled_at = %s, drill_json = '{}' WHERE set_id = %s",
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
        conn.executemany(
            "INSERT INTO meta(key,value) VALUES(%s,%s)",
            ((f"filler/{index}", os.urandom(65536).hex()) for index in range(80)),
        )
    stop = threading.Event()
    committed: list[int] = []

    def writer() -> None:
        conn = engine.connect(engine.resolve_dsn(state), timeout=30)
        number = 0
        while not stop.is_set():
            conn.execute("BEGIN")
            conn.executemany(
                "INSERT INTO meta(key,value) VALUES (%s,%s)",
                ((f"writes/{number}/{item}", str(item)) for item in range(batch)),
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
    copy = engine.connect(engine.resolve_dsn(restored))
    counts = copy.execute(
        "SELECT split_part(key,'/',2),COUNT(*) FROM meta WHERE key LIKE 'writes/%%' GROUP BY split_part(key,'/',2)"
    ).fetchall()
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


def test_a_missing_named_file_refuses_a_successful_set(tmp_path: Path) -> None:
    state = tmp_path / "state"
    found = build_state(state)
    lost = found["derivation_output"]
    Store(state).path("derivation_output", lost).unlink()
    with pytest.raises(ValueError, match="missing captured file"):
        backup(state)
    assert local_sets(state / "checkpoints") == []
    with open_database(state) as database:
        assert database.connection.execute("SELECT count(*) FROM backup_sets").fetchone()[0] == 0


def test_a_pack_row_without_an_objects_row_is_missing(tmp_path: Path) -> None:
    from swingset.backup.segments import unindexed_packs

    state = tmp_path / "state"
    build_state(state)
    with open_database(state) as database:
        conn = database.connection
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT relname AS name FROM pg_catalog.pg_class WHERE (PG_TABLE_IS_VISIBLE(oid) AND relnamespace NOT IN ('pg_catalog'::regnamespace,'information_schema'::regnamespace))"
            )
        }
        assert unindexed_packs(conn, tables) == []
        # A pack row naming an object nobody indexed (foreign keys aside).
        from psycopg.conninfo import conninfo_to_dict

        from swingset.dev.services import ensure_service

        name = conninfo_to_dict(engine.resolve_dsn(state))["dbname"]
        with ensure_service().connect(name) as owner:
            owner.execute("SET session_replication_role=replica")
            owner.execute(
                "INSERT INTO derive.label_body_packs(generation_id,pack_digest,packed_at) VALUES('dg_pack',%s,%s)",
                ("c" * 64, NOW.isoformat()),
            )
        assert unindexed_packs(conn, tables) == [
            ["label_body", "c" * 64, "label_body_packs names no objects row"]
        ]
    # The malformed reference cannot earn a successful history backup.
    with pytest.raises(
        (ValueError, RuntimeError), match="reference|missing|closure|no retained storage ledger row"
    ):
        backup(state)
    assert local_sets(state / "checkpoints") == []


def test_a_set_refuses_before_its_copy_without_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swingset.state import disk

    state = tmp_path / "state"
    build_state(state)
    with open_database(state) as database:
        conn = database.connection
        in_use = int(conn.execute("SELECT pg_database_size(current_database())").fetchone()[0])
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
    found = build_state(state)
    shutil.rmtree(state / "inputs" / found["bundle"])
    copied: list[object] = []
    monkeypatch.setattr(segments_module, "_snapshot", lambda *args: copied.append(args))
    with pytest.raises(BackupError, match="referenced input bundle is missing: " + found["bundle"]):
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
        "removed": [
            "sets/.tmp-set_20261001T040000Z",
            "sets/.tmp-set_20261002T113000Z",
            "segments/.writing-dead.tar",
        ],
        "removed_bytes": 2010,
        "removed_unrecorded_sets": [],
        "confirmed_sets": [],
    }
    # The cluster-wide backup job lock proves neither scratch has a live owner.
    assert not young.exists() and not old.exists()


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
        assert database.connection.execute("SELECT COUNT(*) FROM backup_sets").fetchone()[0] == 1
