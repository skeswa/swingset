"""Migration 35: link read sets, mapping proposals and release lifecycle rows.

The migration adds `derivation_reads` (the reverse index), `source_event_proposals`,
`release_partitions`, `release_artifacts` and `release_manifests`, and deletes
the pending and queue rows of the retired `project/history/all` and
`project/map/all` (docs/plans/architecture.md, section 9, phase 3). The tests
check the CHECK constraints, that an active publication blocks writes to each
new table, that a checkpoint at 35 round-trips, that code at 34 refuses a 35
file, and the three table modules.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from swingset.state import db as db_module
from swingset.state import proposals, read_index, release_store
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

AT = "2026-09-29T00:00:00Z"
TABLES = (
    "derivation_reads",
    "source_event_proposals",
    "release_partitions",
    "release_artifacts",
    "release_manifests",
)
RETIRED = (WorkUnit("project", "history", "all"), WorkUnit("project", "map", "all"))
KEPT = (
    WorkUnit("project", "event", "e1"),
    WorkUnit("project", "history", "e1"),
    WorkUnit("project", "inventory", "all"),
    WorkUnit("link", "event", "all"),
)


def insert(conn: sqlite3.Connection, table: str) -> None:
    """One valid row in `table`."""
    values = {
        "derivation_reads": "INSERT INTO derivation_reads(stage,unit_kind,unit_id,read_key) "
        "VALUES ('link','event','e1','project/event/e1')",
        "source_event_proposals": "INSERT INTO source_event_proposals(source,source_ref,"
        "event_id,match_method,match_confidence) VALUES ('eepro','eepro:a','e1','name_date',1.0)",
        "release_partitions": "INSERT INTO release_partitions(partition,inputs_digest,"
        "artifact_digest,state,built_at,run_id) VALUES ('event/e1','in','art','built',?,'r1')",
        "release_artifacts": "INSERT INTO release_artifacts(artifact_digest,partition,bytes,"
        "created_at,last_named_at) VALUES ('art','event/e1',10,?,?)",
        "release_manifests": "INSERT INTO release_manifests(manifest_digest,bytes,created_at) "
        "VALUES ('man',10,?)",
    }
    sql = values[table]
    conn.execute(sql, (AT,) * sql.count("?"))


def work_rows(conn: sqlite3.Connection, table: str) -> set[WorkUnit]:
    return {WorkUnit(*row) for row in conn.execute(f"SELECT stage,unit_kind,unit_id FROM {table}")}


def test_migration_from_34_adds_tables_and_drops_retired_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 34)
        with open_database(state, lock=False) as database:
            assert database.schema_version == 34
            conn = database.connection
            with database.transaction():
                for unit in RETIRED + KEPT:
                    conn.execute(
                        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
                        "VALUES (?,?,?,?)",
                        (unit.stage, unit.unit_kind, unit.unit_id, AT),
                    )
                    conn.execute(
                        "INSERT INTO work_queue(stage,unit_kind,unit_id,partition,state,"
                        "enqueued_at,updated_at) VALUES (?,?,?,?,'ready',?,?)",
                        (unit.stage, unit.unit_kind, unit.unit_id, unit.unit_kind, AT, AT),
                    )
                    # A generation staged at the switch (history/all stages).
                    conn.execute(
                        "INSERT INTO derivation_building(generation_id,stage,unit_kind,unit_id,"
                        "input_fingerprint,run_id,started_at,updated_at) "
                        "VALUES (?,?,?,?,'f','r',?,?)",
                        (
                            f"dg-{unit.unit_kind}-{unit.unit_id}",
                            unit.stage,
                            unit.unit_kind,
                            unit.unit_id,
                            AT,
                            AT,
                        ),
                    )
            for table in TABLES:
                assert (
                    conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
                    is None
                )

    with open_database(state, lock=False) as database:
        conn = database.connection
        assert database.schema_version == 35
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 35
        assert (
            conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "35"
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert work_rows(conn, "pending_work") == set(KEPT)
        assert work_rows(conn, "work_queue") == set(KEPT)
        assert work_rows(conn, "derivation_building") == set(KEPT)
        for table in TABLES:
            for action in ("insert", "update", "delete"):
                name = f"publication_fence_{table}_{action}"
                assert conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
                ).fetchone()
            assert not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name LIKE ?",
                (f"history_timing_{table}_%",),
            ).fetchone()
        reads_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='derivation_reads'"
        ).fetchone()[0]
        assert "WITHOUT ROWID" in reads_sql
        indexes = {
            str(row[0]): str(row[1])
            for row in conn.execute(
                "SELECT name,tbl_name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            )
        }
        assert indexes["derivation_reads_key"] == "derivation_reads"
        assert indexes["source_event_proposals_event"] == "source_event_proposals"
        plan = " ".join(
            str(row[3])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM derivation_reads WHERE read_key='pool:e1'"
            )
        )
        assert "derivation_reads_key" in plan


def test_check_constraints_refuse_bad_partitions(tmp_path: Path) -> None:
    base = (
        "INSERT INTO release_partitions(partition,inputs_digest,artifact_digest,state,reason,"
        "built_at,run_id) VALUES (?,?,?,?,?,?,'r1')"
    )
    refused = [
        ("built without a digest", None, "built", None),
        ("withheld without a reason", None, "withheld", None),
        ("withheld with a digest", "art", "withheld", "support"),
        ("unknown state", "art", "published", None),
    ]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        for name, digest, state, reason in refused:
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                with database.transaction():
                    conn.execute(base, (name, "in", digest, state, reason, AT))
        with database.transaction():
            conn.execute(base, ("event/e1", "in", "art", "built", None, AT))
            conn.execute(base, ("event/e2", "in", None, "withheld", "support", AT))
        assert conn.execute("SELECT count(*) FROM release_partitions").fetchone()[0] == 2


@pytest.mark.parametrize("table", TABLES)
def test_an_active_publication_blocks_writes(tmp_path: Path, table: str) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            conn.execute(
                "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,"
                "control_revision,all_sources,all_kinds,state) "
                "VALUES ('publishing','publication',?,0,1,1,'active')",
                (AT,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="active publication fences"):
            with database.transaction():
                insert(conn, table)
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_a_checkpoint_at_schema_35_verifies_and_restores(tmp_path: Path) -> None:
    from swingset.backup.checkpoint import (
        create_checkpoint,
        restore_checkpoint,
        verify_checkpoint,
    )

    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        assert database.schema_version == 35
        conn = database.connection
        with database.transaction():
            for table in TABLES:
                insert(conn, table)
        expected = {
            table: sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))
            for table in TABLES
        }
        checkpoint = create_checkpoint(
            state,
            conn,
            tmp_path / "checkpoint",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
    manifest = verify_checkpoint(checkpoint.path, maximum_schema_version=35)
    assert int(manifest["schema_version"]) == 35

    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=35)
    with open_database(restored, lock=False, allow_restore_pending=True) as database:
        assert database.schema_version == 35
        assert {
            table: sorted(
                tuple(row) for row in database.connection.execute(f"SELECT * FROM {table}")
            )
            for table in TABLES
        } == expected


def test_schema_34_code_refuses_a_schema_35_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        assert database.schema_version == 35
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 34)
    with pytest.raises(RuntimeError, match="schema 35 is newer than supported 34"):
        open_database(state, lock=False)


def test_read_index_replaces_a_unit_set_and_answers_dependents(tmp_path: Path) -> None:
    first = WorkUnit("link", "event", "e1")
    second = WorkUnit("link", "event", "e2")
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            read_index.replace(conn, first, ["project/event/e1", "project/dancer/7", "pool:e1"])
            read_index.replace(conn, second, ["project/event/e2", "project/dancer/7"])
            assert read_index.keys(conn, first) == {
                "project/event/e1",
                "project/dancer/7",
                "pool:e1",
            }
            assert read_index.dependents(conn, "project/dancer/7") == [first, second]
            read_index.replace(conn, first, ["project/event/e1", "project/event/e1"])
            assert read_index.keys(conn, first) == {"project/event/e1"}
            assert read_index.dependents(conn, "project/dancer/7") == [second]
            assert read_index.dependents(conn, "pool:e1") == []
            read_index.replace(conn, second, [])
            assert read_index.keys(conn, second) == frozenset()
            assert read_index.count(conn) == 1


def test_proposals_replace_one_source_and_list_by_event(tmp_path: Path) -> None:
    a = proposals.Proposal("eepro", "eepro:a", "e1", "name_date", 1.0)
    b = proposals.Proposal("eepro", "eepro:b", "gen-1", "generated", 0.0, '{"name": "B"}')
    c = proposals.Proposal("scoringdance", "scoringdance:c", "e1", "override", 1.0)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            proposals.replace_source(conn, "eepro", [a, b])
            proposals.replace_source(conn, "scoringdance", [c])
            assert proposals.for_event(conn, "e1") == [a, c]
            assert proposals.for_source(conn, "eepro") == [a, b]
            assert proposals.event_ids(conn) == ["e1", "gen-1"]
            proposals.replace_source(conn, "eepro", [b])
            assert proposals.for_event(conn, "e1") == [c]
            with pytest.raises(ValueError, match="other sources"):
                proposals.replace_source(conn, "eepro", [c])
            assert proposals.for_source(conn, "eepro") == [b]


def test_release_store_rows_round_trip_and_count(tmp_path: Path) -> None:
    built = release_store.PartitionRow("event/e1", "in1", "built", AT, "r1", artifact_digest="art1")
    withheld = release_store.PartitionRow(
        "event/e2", "in2", "withheld", AT, "r1", reason="support", detail_json="{}"
    )
    artifact = release_store.ArtifactRow("art1", "event/e1", 100, AT, AT)
    manifest = release_store.ManifestRow("man1", 50, AT)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            for row in (built, withheld, artifact, manifest):
                release_store.upsert(conn, row)
            assert release_store.get(conn, release_store.PartitionRow, "event/e1") == built
            assert release_store.get(conn, release_store.ArtifactRow, "art1") == artifact
            assert release_store.get(conn, release_store.ManifestRow, "man1") == manifest
            assert release_store.get(conn, release_store.ManifestRow, "nope") is None
            assert release_store.withheld(conn) == [withheld]
            assert release_store.counts(conn) == {
                "built": 1,
                "withheld": 1,
                "withheld:support": 1,
                "artifacts": 1,
                "artifacts_bytes": 100,
                "manifests": 1,
                "manifests_bytes": 50,
            }
            judged = release_store.ManifestRow("man1", 50, AT, "cand_1", '{"pass": true}')
            release_store.upsert(conn, judged)
            assert release_store.all_rows(conn, release_store.ManifestRow) == [judged]
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                release_store.upsert(
                    conn, release_store.PartitionRow("event/e3", "in3", "withheld", AT, "r1")
                )
            assert release_store.delete(conn, release_store.PartitionRow, "event/e2")
            assert not release_store.delete(conn, release_store.PartitionRow, "event/e2")
            assert release_store.withheld(conn) == []
