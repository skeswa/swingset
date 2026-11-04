"""Historical SQLite upgrades plus current Postgres partition guarantees."""

from pathlib import Path

import pytest

from swingset.state import engine as postgres
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


def insert(conn: postgres.Connection, table: str) -> None:
    """One valid row in `table`."""
    values = {
        "derivation_reads": (
            "INSERT INTO derivation_reads (stage, unit_kind, unit_id, read_key) VALUES ('link', 'event', "
            "'e1', 'project/event/e1')"
        ),
        "source_event_proposals": (
            "INSERT INTO source_event_proposals (source, source_ref, event_id, match_method, "
            "match_confidence) VALUES ('eepro', 'eepro:a', 'e1', 'name_date', 1.0)"
        ),
        "release_partitions": (
            "INSERT INTO release_partitions (partition, inputs_digest, artifact_digest, state, built_at, "
            "run_id) VALUES ('event/e1', 'in', 'art', 'built', %s, 'r1')"
        ),
        "release_artifacts": (
            "INSERT INTO release_artifacts (artifact_digest, partition, bytes, created_at, "
            "last_named_at) VALUES ('art', 'event/e1', 10, %s, %s)"
        ),
        "release_manifests": "INSERT INTO release_manifests (manifest_digest, bytes, created_at) VALUES ('man', 10, %s)",
    }
    sql = values[table]
    conn.execute(sql, (AT,) * sql.count("%s"))


def test_check_constraints_refuse_bad_partitions(tmp_path: Path) -> None:
    base = (
        "INSERT INTO release_partitions (partition, inputs_digest, artifact_digest, state, reason, "
        "built_at, run_id) VALUES (%s, %s, %s, %s, %s, %s, 'r1')"
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
            with pytest.raises(postgres.IntegrityError, match="23514"):
                with database.transaction():
                    conn.execute(base, (name, "in", digest, state, reason, AT))
        with database.transaction():
            conn.execute(base, ("event/e1", "in", "art", "built", None, AT))
            conn.execute(base, ("event/e2", "in", None, "withheld", "support", AT))
        assert conn.execute("SELECT COUNT(*) FROM release_partitions").fetchone()[0] == 2


@pytest.mark.parametrize("table", TABLES)
def test_an_active_publication_blocks_writes(tmp_path: Path, table: str) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            conn.execute(
                (
                    "INSERT INTO execution_admissions (action_id, action_kind, admitted_at, control_revision, "
                    "all_sources, all_kinds, state) VALUES ('publishing', 'publication', %s, 0, 1, 1, 'active')"
                ),
                (AT,),
            )
        with pytest.raises(postgres.IntegrityError, match="active publication fences"):
            with database.transaction():
                insert(conn, table)
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


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
            with pytest.raises(postgres.IntegrityError, match="23514"):
                with database.transaction():
                    release_store.upsert(
                        conn, release_store.PartitionRow("event/e3", "in3", "withheld", AT, "r1")
                    )
            assert release_store.delete(conn, release_store.PartitionRow, "event/e2")
            assert not release_store.delete(conn, release_store.PartitionRow, "event/e2")
            assert release_store.withheld(conn) == []


def test_migration_from_34_adds_tables_and_drops_retired_work(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_partitions_migration.py", "test_migration_from_34_adds_tables_and_drops_retired_work"
    )


def test_a_checkpoint_at_schema_35_verifies_and_restores(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_partitions_migration.py", "test_a_checkpoint_at_schema_35_verifies_and_restores"
    )


def test_schema_34_code_refuses_a_schema_35_file(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_partitions_migration.py", "test_schema_34_code_refuses_a_schema_35_file"
    )
