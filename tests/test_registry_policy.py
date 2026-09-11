from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint
from swingset.build.input import read_build_input
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.model.ids import observation_id, snapshot_id
from swingset.model.observations import encode_payload
from swingset.schedule.registry import (
    advance_sweep,
    archive_crosscheck_dump,
    crosscheck,
    discover_registry,
    replay_crosscheck,
    run_saved_crosscheck_if_due,
    seed_sweep,
)
from swingset.schedule.watches import due_watches, upsert_watch
from swingset.sources.base import WatchSpec
from swingset.sources.records import DancerLookup
from swingset.sources.wsdc_registry.adapter import SOURCE
from swingset.state.db import Database, open_database

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def comparison_dump(dancers: list[dict[str, object]]) -> dict[str, object]:
    return {
        "dancers": dancers,
        "divisions": [],
        "event_occurrences": [],
        "events": [],
        "placements": [],
        "roles": [],
        "upcoming_events": [],
    }


def archive_dump(
    database: Database, tmp_path: Path, dancers: list[dict[str, object]] | None = None
) -> str:
    dump = tmp_path / "comparison.json"
    dump.write_text(json.dumps(comparison_dump(dancers or [])))
    run = database.start_run(NOW, dry_run=True)
    return archive_crosscheck_dump(database, dump, Archive(database.state_dir), NOW, run)


def record(database: Database, wsdc_id: int, outcome: str) -> None:
    connection = database.connection
    spec = SOURCE.watch(wsdc_id)
    upsert_watch(connection, spec, NOW)
    body_hash = f"{wsdc_id:064x}"
    snap = snapshot_id(NOW, body_hash)
    run = "run_registry"
    connection.execute(
        "INSERT OR IGNORE INTO runs(run_id,started_at,dry_run) VALUES (?,?,1)",
        (run, NOW.isoformat()),
    )
    connection.execute(
        "INSERT OR IGNORE INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) "
        "VALUES (?,?,?,?,?,200,?,1,1,?,'OK')",
        (snap, spec.watch_id, "POST", spec.url, NOW.isoformat(), body_hash, run),
    )
    payload = DancerLookup(
        "dancer_lookup", outcome, wsdc_id, wsdc_id if outcome == "found" else None
    )
    connection.execute(
        "DELETE FROM observations WHERE scope_kind='dancer' AND scope_id=?", (str(wsdc_id),)
    )
    connection.execute(
        "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            observation_id(spec.watch_id, snap, payload.kind, 0),
            spec.watch_id,
            snap,
            payload.kind,
            "dancer",
            str(wsdc_id),
            0,
            "1",
            "1",
            encode_payload(payload),
        ),
    )


def cursor(database: Database, name: str) -> str | None:
    row = database.connection.execute("SELECT value FROM cursors WHERE name=?", (name,)).fetchone()
    return str(row[0]) if row else None


def registry_config() -> Config:
    return Config(
        {"points.worldsdc.com": HostConfig(), "example.test": HostConfig()},
        {"wsdc_registry": SourceConfig(True), "wdr": SourceConfig(True)},
    )


def due_registry_ids(database: Database) -> list[int]:
    watch_ids = due_watches(database.connection, registry_config(), NOW)
    refs = {
        str(row["watch_id"]): str(row["source_ref"])
        for row in database.connection.execute(
            "SELECT watch_id,source_ref FROM watches WHERE source='wsdc_registry'"
        )
    }
    return [int(refs[watch_id].removeprefix("wsdc:")) for watch_id in watch_ids]


def test_due_registry_sweep_and_probe_follow_numeric_source_ids(tmp_path: Path) -> None:
    with open_database(tmp_path / "sweep") as database:
        seed_sweep(database, 1)
        assert discover_registry(database, NOW, batch_size=350) == 350
        assert due_registry_ids(database) == list(range(1, 351))

    with open_database(tmp_path / "probe") as database:
        assert discover_registry(database, NOW, batch_size=350) == 20
        assert due_registry_ids(database) == list(range(1, 21))


def test_registry_sequence_keeps_other_host_in_its_existing_fairness_slot(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        seed_sweep(database, 1)
        discover_registry(database, NOW, batch_size=20)
        other = WatchSpec(
            "",
            "wdr",
            "event",
            "GET",
            "https://example.test/results",
            "wdr.rounds",
        )
        upsert_watch(database.connection, other, NOW)
        database.connection.execute(
            "UPDATE watches SET priority=5,next_check_at=? WHERE watch_id=?",
            (NOW.isoformat(), other.watch_id),
        )
        hash_order = [
            str(row[0])
            for row in database.connection.execute(
                "SELECT watch_id FROM watches ORDER BY priority,next_check_at,"
                "CASE kind WHEN 'round' THEN 1 ELSE 0 END,watch_id"
            )
        ]
        fair_slot = hash_order.index(other.watch_id)
        ordered = due_watches(database.connection, registry_config(), NOW)
        assert ordered.index(other.watch_id) == fair_slot
        registry_ids = [
            int(
                database.connection.execute(
                    "SELECT source_ref FROM watches WHERE watch_id=?", (watch_id,)
                ).fetchone()[0][5:]
            )
            for watch_id in ordered
            if watch_id != other.watch_id
        ]
        assert registry_ids == list(range(1, 21))


def test_sweep_advances_only_contiguous_verified_outcomes_across_restart(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        seed_sweep(database, 1)
        record(database, 1, "found")
        record(database, 2, "not_found")
        record(database, 3, "invalid")
        record(database, 4, "found")
        with database.transaction():
            advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "3"
        assert cursor(database, "registry_sweep_misses") == "0"
    with open_database(tmp_path) as database:
        assert cursor(database, "registry_sweep_next") == "3"
        record(database, 3, "found")
        with database.transaction():
            advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "5"
        assert cursor(database, "registry_sweep_misses") == "0"


def test_sweep_finishes_only_after_twenty_verified_trailing_misses(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        sha = archive_dump(database, tmp_path)
        seed_sweep(database, 1)
        record(database, 1, "found")
        for wsdc_id in range(2, 22):
            record(database, wsdc_id, "not_found")
        with database.transaction():
            assert advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") is None
        assert cursor(database, "registry_sweep_misses") is None
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_crosscheck_due'"
            ).fetchone()[0]
            == sha
        )


def test_sweep_ignores_interior_misses_until_above_comparison_bound(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        archive_dump(database, tmp_path, [{"id": 25}])
        seed_sweep(database, 1)
        for wsdc_id in range(1, 26):
            record(database, wsdc_id, "not_found")
        with database.transaction():
            assert not advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "26"
        for wsdc_id in range(26, 45):
            record(database, wsdc_id, "not_found")
        with database.transaction():
            assert not advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "45"
        record(database, 45, "not_found")
        with database.transaction():
            assert advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") is None


def test_sweep_bound_includes_unprojected_found_observations(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        archive_dump(database, tmp_path, [{"id": 10}])
        seed_sweep(database, 20)
        for wsdc_id in range(20, 40):
            record(database, wsdc_id, "not_found")
        record(database, 40, "found")
        with database.transaction():
            assert not advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "41"
        for wsdc_id in range(41, 60):
            record(database, wsdc_id, "not_found")
        with database.transaction():
            assert not advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "60"
        record(database, 60, "not_found")
        with database.transaction():
            assert advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") is None


def test_existing_dump_bound_is_lazily_cached_and_missing_blob_fails_closed(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path) as database:
        sha = archive_dump(database, tmp_path, [{"id": 25}])
        database.connection.execute(
            "DELETE FROM meta WHERE key IN ('registry_sweep_bound_blob','registry_sweep_dump_bound')"
        )
        seed_sweep(database, 1)
        record(database, 1, "not_found")
        with database.transaction():
            assert not advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "2"
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_sweep_bound_blob'"
            ).fetchone()[0]
            == sha
        )
        Archive(database.state_dir).blob_path(sha).unlink()
        record(database, 2, "not_found")
        with pytest.raises(FileNotFoundError, match="comparison dump is missing"):
            with database.transaction():
                advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "2"


def test_replaced_high_id_miss_can_raise_local_found_bound(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        archive_dump(database, tmp_path, [{"id": 10}])
        seed_sweep(database, 1)
        record(database, 100, "not_found")
        with database.transaction():
            assert not advance_sweep(database, NOW)
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_sweep_local_bound'"
            ).fetchone()[0]
            == "0"
        )
        record(database, 100, "found")
        with database.transaction():
            assert not advance_sweep(database, NOW)
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_sweep_local_bound'"
            ).fetchone()[0]
            == "100"
        )


def test_reseed_clears_stale_completion_and_probe_bookkeeping(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        database.connection.executemany(
            "INSERT INTO cursors(name,value) VALUES (?,?)",
            (
                ("registry_probe_cursor", "50"),
                ("registry_probe_misses", "4"),
                ("registry_probe_next_at", NOW.isoformat()),
            ),
        )
        database.connection.executemany(
            "INSERT INTO meta(key,value) VALUES (?,?)",
            (
                ("registry_crosscheck_due", "old"),
                ("registry_crosscheck_completed", "old"),
            ),
        )
        seed_sweep(database, 5910)
        assert cursor(database, "registry_sweep_next") == "5910"
        assert cursor(database, "registry_sweep_misses") == "0"
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM cursors WHERE name LIKE 'registry_probe_%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM meta WHERE key IN "
                "('registry_crosscheck_due','registry_crosscheck_completed')"
            ).fetchone()[0]
            == 0
        )


def test_new_dump_bound_clamps_restored_tail_misses_across_restart(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with open_database(state) as database:
        archive_dump(database, tmp_path, [{"id": 100}])
        seed_sweep(database, 120)
        database.connection.execute(
            "UPDATE cursors SET value='19' WHERE name='registry_sweep_misses'"
        )
        archive_dump(database, tmp_path, [{"id": 115}])
    with open_database(state) as database:
        record(database, 120, "not_found")
        with database.transaction():
            assert not advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "121"
        assert cursor(database, "registry_sweep_misses") == "5"


def test_weekly_probe_resets_misses_on_found_and_waits_after_twenty(tmp_path: Path) -> None:
    clock = FakeClock(NOW)
    with open_database(tmp_path) as database:
        discover_registry(database, clock.now())
        for wsdc_id in range(1, 11):
            record(database, wsdc_id, "not_found")
        record(database, 11, "found")
        for wsdc_id in range(12, 32):
            record(database, wsdc_id, "not_found")
        with database.transaction():
            advance_sweep(database, clock.now())
        assert cursor(database, "registry_probe_cursor") is None
        assert datetime.fromisoformat(
            cursor(database, "registry_probe_next_at") or ""
        ) == clock.now() + timedelta(days=7)


def test_invalid_probe_result_never_counts_as_a_miss(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        discover_registry(database, NOW)
        record(database, 1, "invalid")
        with database.transaction():
            advance_sweep(database, NOW)
        assert cursor(database, "registry_probe_cursor") == "1"
        assert cursor(database, "registry_probe_misses") == "0"


def test_trickle_schedules_at_most_one_hundred_stale_dancers_per_day(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        columns = database.connection.execute("PRAGMA table_info(dancers)").fetchall()
        placeholders = ",".join("?" for _ in columns)
        for wsdc_id in range(1, 102):
            values: list[object] = []
            for column in columns:
                name, kind = str(column[1]), str(column[2])
                if name == "wsdc_id":
                    values.append(wsdc_id)
                elif name == "registry_fetched_at":
                    values.append("2020-01-01T00:00:00+00:00")
                elif int(column[3]) == 0:
                    values.append(None)
                elif "INT" in kind:
                    values.append(0)
                else:
                    values.append("x")
            database.connection.execute(f"INSERT INTO dancers VALUES ({placeholders})", values)
        discover_registry(database, NOW)
        scheduled = database.connection.execute(
            "SELECT COUNT(*) FROM watches WHERE notes='trickle'"
        ).fetchone()[0]
        assert scheduled == 100
        discover_registry(database, NOW + timedelta(hours=1))
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM watches WHERE notes='trickle'"
            ).fetchone()[0]
            == 100
        )


def test_crosscheck_is_manual_snapshot_and_replayable_from_blob(tmp_path: Path) -> None:
    dump = tmp_path / "dump.json"
    dump.write_text("[]")
    with open_database(tmp_path / "state") as database:
        run = database.start_run(NOW, dry_run=True)
        archive = Archive(database.state_dir)
        assert crosscheck(database, dump, archive, NOW, run) == 0
        row = database.connection.execute(
            "SELECT w.source,s.via,s.body_sha256 FROM snapshots s JOIN watches w USING(watch_id)"
        ).fetchone()
        assert tuple(row)[:2] == ("crosscheck", "manual")
        assert (
            database.connection.execute(
                "SELECT value FROM revisions WHERE name='snapshots'"
            ).fetchone()[0]
            == 1
        )
        assert (
            replay_crosscheck(database, str(row[2]), archive, NOW + timedelta(seconds=1), run) == 0
        )


def test_crosscheck_finding_is_a_build_input_and_survives_restore(tmp_path: Path) -> None:
    dump = tmp_path / "dump.json"
    dump.write_text('[{"wscid":7,"first_name":"Ada"}]')
    state_dir = tmp_path / "state"
    checkpoint_dir = tmp_path / "checkpoint"
    with open_database(state_dir) as database:
        run = database.start_run(NOW, dry_run=True)
        assert crosscheck(database, dump, Archive(state_dir), NOW, run) == 1
        data = read_build_input(
            database.connection, SimpleNamespace(file_hashes={}, digest="bundle")
        )
        assert data.tables["review_queue"][0]["kind"] == "registry_diff"
        body_hash = str(
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_crosscheck_blob'"
            ).fetchone()[0]
        )
        create_checkpoint(
            state_dir,
            database.connection,
            checkpoint_dir,
            schema_version=1,
            versions={},
            input_bundle_hash=None,
        )
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint_dir, restored, maximum_schema_version=1)
    with open_database(restored, allow_restore_pending=True) as database:
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM findings WHERE closed_at IS NULL"
            ).fetchone()[0]
            == 1
        )
        assert Archive(restored).read_body(body_hash) == dump.read_bytes()


def test_crosscheck_accepts_mechstack_dancer_ids_only_in_full_dump_shape(tmp_path: Path) -> None:
    full_dump = comparison_dump([{"id": 7, "first_name": "Ada", "last_name": "Lovelace"}])
    dump = tmp_path / "data.json"
    dump.write_text(json.dumps(full_dump))
    with open_database(tmp_path / "state") as database:
        run = database.start_run(NOW, dry_run=True)
        assert crosscheck(database, dump, Archive(database.state_dir), NOW, run) == 1
        finding = database.connection.execute(
            "SELECT subject_id,evidence_json FROM findings WHERE kind='registry_diff'"
        ).fetchone()
        assert finding[0] == "7"
        assert '"first_name":"Ada"' in finding[1]

    dump.write_text('[{"id":7,"first_name":"Ada"}]')
    with open_database(tmp_path / "bare-state") as database:
        run = database.start_run(NOW, dry_run=True)
        with pytest.raises(ValueError, match="lacks wscid/wsdc_id"):
            crosscheck(database, dump, Archive(database.state_dir), NOW, run)


def test_archive_only_records_dump_without_findings_then_sweep_runs_it_once(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "data.json"
    dump.write_text(json.dumps(comparison_dump([{"id": 1, "first_name": "Ada"}])))
    with open_database(tmp_path / "state") as database:
        run = database.start_run(NOW, dry_run=True)
        archive = Archive(database.state_dir)
        sha = archive_crosscheck_dump(database, dump, archive, NOW, run)
        assert database.connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_crosscheck_due'"
            ).fetchone()
            is None
        )
        assert archive.read_body(sha) == dump.read_bytes()
        seed_sweep(database, 1)
        record(database, 1, "found")
        for wsdc_id in range(2, 22):
            record(database, wsdc_id, "not_found")
        with database.transaction():
            assert advance_sweep(database, NOW)
        assert run_saved_crosscheck_if_due(database, archive, NOW, run) == 1
        assert run_saved_crosscheck_if_due(database, archive, NOW, run) is None
        assert database.connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_crosscheck_completed'"
            ).fetchone()[0]
            == sha
        )


def test_seeded_sweep_completion_without_archived_dump_fails_closed(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        seed_sweep(database, 1)
        for wsdc_id in range(1, 21):
            record(database, wsdc_id, "not_found")
        with pytest.raises(RuntimeError, match="without an archived comparison dump"):
            with database.transaction():
                advance_sweep(database, NOW)
        assert cursor(database, "registry_sweep_next") == "1"


def test_archive_only_rejects_generic_or_duplicate_id_dumps(tmp_path: Path) -> None:
    dump = tmp_path / "data.json"
    with open_database(tmp_path / "state") as database:
        run = database.start_run(NOW, dry_run=True)
        archive = Archive(database.state_dir)
        dump.write_text('[{"wscid":1}]')
        with pytest.raises(ValueError, match="known registry comparison dump shape"):
            archive_crosscheck_dump(database, dump, archive, NOW, run)
        dump.write_text(json.dumps(comparison_dump([{"id": 1}, {"id": 1}])))
        with pytest.raises(ValueError, match="duplicate dancer ids"):
            archive_crosscheck_dump(database, dump, archive, NOW, run)


def test_due_crosscheck_with_missing_blob_remains_due(tmp_path: Path) -> None:
    digest = "a" * 64
    with open_database(tmp_path) as database:
        run = database.start_run(NOW, dry_run=True)
        database.connection.execute(
            "INSERT INTO meta(key,value) VALUES ('registry_crosscheck_due',?)", (digest,)
        )
        with pytest.raises(FileNotFoundError):
            run_saved_crosscheck_if_due(database, Archive(tmp_path), NOW, run)
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_crosscheck_due'"
            ).fetchone()[0]
            == digest
        )
