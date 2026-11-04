"""Postgres preserves schema 37 storage lifecycle tables and eviction guards.

The migration adds `blob_classes` (seeded), `objects`, `derivation_evicted`,
the two body pack tables, the backup tables and `storage_applies`; indexes
`derivation_generations(output_digest)`; adds `derivation_dependency_sets.created_at`;
and recreates the immutability triggers so that row references, dependency
sets, payloads and eviction markers may be deleted, and parse bodies and build
recipes stubbed and restored, only under `storage_eviction_authority`
(docs/plans/architecture.md, section 9, phase 4). The authority row cannot
commit (the D-0136 pattern).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swingset.state import engine
from swingset.state.db import Database, open_database
from swingset.state.objects import CLASSES, STUB
from swingset.state.publication_fence import (
    NARROW_KIND,
    OPERATIONAL_TABLES,
    expected_narrow,
    narrow_triggers,
)

AT = "2026-10-01T00:00:00Z"
NEW_TABLES = (
    "blob_classes",
    "objects",
    "derivation_evicted",
    "source_generation_packs",
    "label_body_packs",
    "storage_eviction_grant",
    "storage_eviction_authority",
    "storage_applies",
    "backup_sets",
    "backup_objects",
)
BODY = {"manifest_json": '{"m":1}', "result_json": '{"r":1}', "report_json": '{"p":1}'}
RECIPE = '{"context":{"release_closure":"x"}}'


def seed(conn: engine.Connection) -> None:
    """A run, one source generation, a build and a project label with rows."""
    conn.execute("INSERT INTO runs (run_id, started_at, dry_run) VALUES ('r1', %s, 0)", (AT,))
    conn.execute(
        "INSERT INTO watches (watch_id, source, kind, method, url, parser, state) VALUES ('w1', "
        "'eepro', 'round', 'GET', 'https://example.test/r', 'round', 'live')"
    )
    conn.execute(
        "INSERT INTO source_units (unit_key, watch_id, page_kind) VALUES ('u1', 'w1', 'round')"
    )
    conn.execute(
        (
            "INSERT INTO source_generations (generation_id, unit_key, page_kind, contract_version, "
            "input_fingerprint, manifest_json, recipe_json, result_json, report_json, created_at, "
            "run_id, state, removal_authority) VALUES ('sg1', 'u1', 'round', '1', 'f', %s, '{}', %s, %s, "
            "%s, 'r1', 'superseded', 'none')"
        ),
        (BODY["manifest_json"], BODY["result_json"], BODY["report_json"], AT),
    )
    for set_id in ("ds1", "ds_unnamed"):
        conn.execute(
            "INSERT INTO derivation_dependency_sets (dependency_set_id, manifest_json) VALUES (%s, '[]')",
            (set_id,),
        )
    for stage, kind, gid, recipe in (
        ("build", "release", "dg_build", RECIPE),
        ("project", "event", "dg_proj", RECIPE),
    ):
        conn.execute(
            "INSERT INTO derivation_scopes (stage, unit_kind, unit_id, registered_at) VALUES (%s, %s, 'all', %s)",
            (stage, kind, AT),
        )
        conn.execute(
            (
                "INSERT INTO derivation_generations (generation_id, stage, unit_kind, unit_id, "
                "input_fingerprint, recipe_json, dependency_set_id, output_digest, row_count, created_at, "
                "run_id) VALUES (%s, %s, %s, 'all', 'f', %s, 'ds1', %s, 1, %s, 'r1')"
            ),
            (gid, stage, kind, recipe, "d" * 64, AT),
        )
        conn.execute(
            (
                "INSERT INTO derivation_row_refs (generation_id, ordinal, table_name, record_key, "
                "payload_sha256) VALUES (%s, 0, 'event', 'e1', %s)"
            ),
            (gid, "p" * 64),
        )
    conn.execute(
        "INSERT INTO derivation_payloads (payload_sha256, payload_json) VALUES (%s, '{}')",
        ("p" * 64,),
    )
    conn.execute(
        "INSERT INTO derivation_payloads (payload_sha256, payload_json) VALUES (%s, '{\"x\":1}')",
        ("q" * 64,),
    )


@pytest.fixture
def migrated(tmp_path: Path):
    """Explicitly provisioned schema 37 retains all historical storage guards."""
    with open_database(tmp_path / "state", lock=False) as database:
        with database.transaction() as conn:
            seed(conn)
        yield database


@pytest.fixture
def authority():
    """Run a transaction under the storage authority, closing it before commit."""

    def run(database: Database, statements: list[tuple[str, tuple]]) -> None:
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO storage_eviction_authority (singleton, reason, granted_at) VALUES (1, 'test', %s)",
                (AT,),
            )
            for sql, args in statements:
                conn.execute(sql, args)
            conn.execute("DELETE FROM storage_eviction_authority")

    return run


def test_postgres_schema_preserves_the_lifecycle_tables(migrated: Database) -> None:
    from swingset.state.postgres_schema import validate_schema

    conn = migrated.connection
    assert migrated.schema_version == 37
    validate_schema(conn, full=True)
    rows = conn.execute(
        "SELECT class,location,roots,collector,min_age_seconds,backed_up FROM blob_classes"
    ).fetchall()
    assert {row[0]: tuple(row) for row in rows} == {
        c.name: (c.name, c.location, c.roots, c.collector, c.min_age_seconds, int(c.backed_up))
        for c in CLASSES.values()
    }
    assert conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0] == 2
    assert (
        conn.execute("SELECT result_json FROM source_generations").fetchone()[0]
        == BODY["result_json"]
    )
    assert conn.execute(
        "SELECT 1 FROM pg_indexes WHERE indexname='derivation_generations_output'"
    ).fetchone()
    for table in NEW_TABLES:
        fenced = conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid=to_regclass(%s) AND tgname LIKE %s",
            (table, f"a_publication_fence_{table}_%"),
        ).fetchone()[0]
        assert fenced == (0 if table in OPERATIONAL_TABLES else 3), table
    assert narrow_triggers(conn) == expected_narrow(conn)


@pytest.mark.parametrize(
    ("setup", "delete", "message"),
    [
        (None, "DELETE FROM derivation_row_refs WHERE generation_id = 'dg_proj'", "immutable"),
        (
            None,
            "DELETE FROM derivation_dependency_sets WHERE dependency_set_id = 'ds_unnamed'",
            "immutable",
        ),
        (None, "DELETE FROM derivation_payloads WHERE payload_sha256='" + "q" * 64 + "'", "auth"),
        (
            "INSERT INTO derivation_evicted VALUES ('dg_proj','" + AT + "')",
            "DELETE FROM derivation_evicted WHERE generation_id = 'dg_proj'",
            "authority",
        ),
    ],
)
def test_deletes_need_the_storage_authority(
    migrated: Database, authority, setup: str | None, delete: str, message: str
) -> None:
    conn = migrated.connection
    if setup:
        with migrated.transaction():
            conn.execute(setup)
    with pytest.raises(engine.IntegrityError, match=message):
        with migrated.transaction():
            conn.execute(delete)
    authority(migrated, [(delete, ())])
    assert conn.execute(delete.replace("DELETE", "SELECT 1", 1)).fetchone() is None
    assert (
        conn.execute(
            "SELECT conname FROM pg_constraint WHERE contype='f' AND NOT convalidated"
        ).fetchall()
        == []
    )


def test_the_old_payload_gate_still_opens_payload_deletes(migrated: Database) -> None:
    conn = migrated.connection
    with migrated.transaction():
        conn.execute(
            (
                "INSERT INTO derivation_payload_removal_authority (singleton, reason, granted_at) VALUES (1, "
                "'retention', %s)"
            ),
            (AT,),
        )
        conn.execute("DELETE FROM derivation_payloads WHERE payload_sha256 = %s", ("q" * 64,))
        with pytest.raises(engine.IntegrityError, match="immutable"), migrated.transaction():
            conn.execute("DELETE FROM derivation_row_refs WHERE generation_id = 'dg_proj'")
        conn.execute("DELETE FROM derivation_payload_removal_authority")


def test_other_updates_and_deletes_stay_refused_under_the_authority(
    migrated: Database, authority
) -> None:
    refused = [
        "UPDATE derivation_row_refs SET record_key = 'e2'",
        "UPDATE derivation_payloads SET payload_json = '[]'",
        "UPDATE derivation_dependency_sets SET manifest_json = '[1]'",
        "DELETE FROM derivation_generations WHERE generation_id = 'dg_proj'",
        "UPDATE derivation_generations SET row_count = 2 WHERE generation_id = 'dg_build'",
        "UPDATE source_generations SET recipe_json = '{\"x\":1}'",
        "UPDATE source_generations SET contract_version = '2'",
    ]
    for sql in refused:
        with pytest.raises(engine.IntegrityError, match="immutable"):
            authority(migrated, [(sql, ())])
    # State stays mutable without any authority.
    with migrated.transaction() as conn:
        conn.execute("UPDATE source_generations SET state = 'accepted'")


def _body(conn: engine.Connection) -> tuple[str, str, str]:
    return tuple(
        conn.execute(
            "SELECT manifest_json, result_json, report_json FROM source_generations"
        ).fetchone()
    )  # type: ignore[return-value]


STUB_BODY = (
    "UPDATE source_generations SET manifest_json = %s, result_json = %s, report_json = %s WHERE "
    "generation_id = 'sg1'"
)
PACK_ROW = "INSERT INTO source_generation_packs (generation_id, pack_digest, packed_at) VALUES ('sg1', %s, %s)"


def test_a_parse_body_is_stubbed_and_restored_only_under_the_authority(
    migrated: Database, authority
) -> None:
    conn = migrated.connection
    stub = (STUB, STUB, STUB)
    with pytest.raises(engine.IntegrityError, match="immutable"):
        with migrated.transaction():
            conn.execute(PACK_ROW, ("b" * 64, AT))
            conn.execute(STUB_BODY, stub)
    # No pack row: refused even under the authority.
    with pytest.raises(engine.IntegrityError, match="immutable"):
        authority(migrated, [(STUB_BODY, stub)])
    # Not all three together: refused.
    with pytest.raises(engine.IntegrityError, match="immutable"):
        authority(
            migrated,
            [(PACK_ROW, ("b" * 64, AT)), (STUB_BODY, (BODY["manifest_json"], STUB, STUB))],
        )
    authority(migrated, [(PACK_ROW, ("b" * 64, AT)), (STUB_BODY, stub)])
    assert _body(conn) == stub
    # A pack row stays while its row is stubbed, authority or not.
    with pytest.raises(engine.IntegrityError, match="restored row"):
        with migrated.transaction():
            conn.execute("DELETE FROM source_generation_packs")
    with pytest.raises(engine.IntegrityError, match="restored row"):
        authority(migrated, [("DELETE FROM source_generation_packs", ())])
    with pytest.raises(engine.IntegrityError, match="immutable"):
        with migrated.transaction():
            conn.execute(STUB_BODY, tuple(BODY.values()))
    with pytest.raises(engine.IntegrityError, match="immutable"):
        with migrated.transaction():
            conn.execute("UPDATE source_generation_packs SET pack_digest = 'c'")
    authority(migrated, [(STUB_BODY, tuple(BODY.values()))])
    assert _body(conn) == tuple(BODY.values())
    with pytest.raises(engine.IntegrityError, match="restored row"):
        with migrated.transaction():
            conn.execute("DELETE FROM source_generation_packs")
    authority(migrated, [("DELETE FROM source_generation_packs", ())])
    assert (
        conn.execute(
            "SELECT conname FROM pg_constraint WHERE contype='f' AND NOT convalidated"
        ).fetchall()
        == []
    )


RECIPE_SET = "UPDATE derivation_generations SET recipe_json = %s WHERE generation_id = %s"
LABEL_PACK = (
    "INSERT INTO label_body_packs (generation_id, pack_digest, packed_at) VALUES (%s, %s, %s)"
)


def test_a_build_recipe_is_stubbed_and_restored_only_under_the_authority(
    migrated: Database, authority
) -> None:
    conn = migrated.connection
    with pytest.raises(engine.IntegrityError, match="immutable"):
        with migrated.transaction():
            conn.execute(LABEL_PACK, ("dg_build", "b" * 64, AT))
            conn.execute(RECIPE_SET, (STUB, "dg_build"))
    with pytest.raises(engine.IntegrityError, match="immutable"):
        authority(migrated, [(RECIPE_SET, (STUB, "dg_build"))])
    # Only build labels' recipes are packed.
    with pytest.raises(engine.IntegrityError, match="immutable"):
        authority(
            migrated,
            [(LABEL_PACK, ("dg_proj", "b" * 64, AT)), (RECIPE_SET, (STUB, "dg_proj"))],
        )
    # Another column changing with the recipe is refused.
    with pytest.raises(engine.IntegrityError, match="immutable"):
        authority(
            migrated,
            [
                (LABEL_PACK, ("dg_build", "b" * 64, AT)),
                (
                    "UPDATE derivation_generations SET recipe_json = %s, output_digest = 'e' WHERE generation_id = 'dg_build'",
                    (STUB,),
                ),
            ],
        )
    authority(
        migrated, [(LABEL_PACK, ("dg_build", "b" * 64, AT)), (RECIPE_SET, (STUB, "dg_build"))]
    )
    stored = "SELECT recipe_json FROM derivation_generations WHERE generation_id = 'dg_build'"
    assert conn.execute(stored).fetchone()[0] == STUB
    with pytest.raises(engine.IntegrityError, match="restored row"):
        authority(migrated, [("DELETE FROM label_body_packs", ())])
    authority(migrated, [(RECIPE_SET, (RECIPE, "dg_build"))])
    assert conn.execute(stored).fetchone()[0] == RECIPE
    authority(migrated, [("DELETE FROM label_body_packs", ())])


def test_a_grant_cannot_commit(migrated: Database) -> None:
    conn = migrated.connection
    with pytest.raises(engine.IntegrityError, match="23503"):
        with migrated.transaction():
            conn.execute(
                "INSERT INTO storage_eviction_authority (singleton, reason, granted_at) VALUES (1, 'left open', %s)",
                (AT,),
            )
    assert conn.execute("SELECT COUNT(*) FROM storage_eviction_authority").fetchone()[0] == 0
    with pytest.raises(engine.IntegrityError, match="immutable"):
        with migrated.transaction():
            conn.execute("DELETE FROM derivation_row_refs")


def test_workers_cannot_disable_the_authority_constraint(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        with pytest.raises(engine.Error) as error:
            database.connection.execute("SET session_replication_role='replica'")
        assert error.value.sqlstate == "42501"
        assert (
            database.connection.execute("SHOW session_replication_role").fetchone()[0] == "origin"
        )


def test_a_storage_apply_note_is_written_once_and_finished_once(migrated: Database) -> None:
    conn = migrated.connection
    insert = "INSERT INTO storage_applies (plan_digest, kind, started_at) VALUES (%s, %s, %s)"
    with pytest.raises(engine.IntegrityError, match="23514"):
        with migrated.transaction():
            conn.execute(insert, ("p0", "other", AT))
    with pytest.raises(engine.IntegrityError, match="23514"):
        with migrated.transaction():
            conn.execute(
                (
                    "INSERT INTO storage_applies (plan_digest, kind, started_at, finished_at, receipt_json) "
                    "VALUES ('p0', 'storage', %s, %s, NULL)"
                ),
                (AT, AT),
            )
    with migrated.transaction():
        conn.execute(insert, ("p1", "storage", AT))
    with pytest.raises(engine.IntegrityError, match="written once"):
        with migrated.transaction():
            conn.execute("UPDATE storage_applies SET kind = 'collect'")
    with migrated.transaction():
        conn.execute(
            "UPDATE storage_applies SET finished_at = %s, receipt_json = %s",
            (AT, json.dumps({"ok": True})),
        )
    with pytest.raises(engine.IntegrityError, match="written once"):
        with migrated.transaction():
            conn.execute("UPDATE storage_applies SET receipt_json = '{}'")
    with pytest.raises(engine.IntegrityError, match="permanent"):
        with migrated.transaction():
            conn.execute("DELETE FROM storage_applies")


def _admit(conn: engine.Connection, kind: str) -> None:
    conn.execute(
        (
            "INSERT INTO execution_admissions (action_id, action_kind, admitted_at, control_revision, "
            "all_sources, all_kinds, state) VALUES ('publishing', %s, %s, 0, 1, 1, 'active')"
        ),
        (kind, AT),
    )


def test_a_full_publication_fences_storage_but_not_backups(migrated: Database) -> None:
    conn = migrated.connection
    with migrated.transaction():
        _admit(conn, "publication")
    for sql in (
        "INSERT INTO objects VALUES ('parse_body','" + "a" * 64 + "',1,1,'" + AT + "')",
        "INSERT INTO derivation_evicted VALUES ('dg_proj','" + AT + "')",
        "INSERT INTO storage_eviction_authority VALUES (1,'x','" + AT + "')",
        # The fence still speaks before the recreated guards.
        "DELETE FROM derivation_row_refs",
        "UPDATE derivation_generations SET row_count = 2",
        "UPDATE source_generations SET result_json = '{}'",
    ):
        with pytest.raises(engine.IntegrityError, match="active publication fences"):
            with migrated.transaction():
                conn.execute(sql)
    with migrated.transaction():
        conn.execute(
            (
                "INSERT INTO backup_sets (set_id, created_at, manifest_sha256, db_raw_bytes, db_raw_sha256, "
                "parts, objects, new_objects, transport) VALUES ('s1', %s, 'm', 1, 'd', 1, 0, 0, 'local')"
            ),
            (AT,),
        )
        conn.execute(
            "INSERT INTO backup_objects (transport, key, segment, staged_at) VALUES ('local', 'k', 'seg', %s)",
            (AT,),
        )


def test_a_narrow_publication_fences_only_build_storage(migrated: Database) -> None:
    conn = migrated.connection
    with migrated.transaction():
        _admit(conn, NARROW_KIND)
    for sql in (
        "INSERT INTO objects VALUES ('release_manifest','" + "a" * 64 + "',1,1,'" + AT + "')",
        "INSERT INTO objects VALUES ('derivation_output','" + "d" * 64 + "',1,1,'" + AT + "')",
        "INSERT INTO derivation_evicted VALUES ('dg_build','" + AT + "')",
        "INSERT INTO label_body_packs VALUES ('dg_build','" + "b" * 64 + "','" + AT + "')",
    ):
        with pytest.raises(engine.IntegrityError, match="active publication fences"):
            with migrated.transaction():
                conn.execute(sql)
    with migrated.transaction():
        conn.execute(
            (
                "INSERT INTO objects (class, digest, raw_bytes, stored_bytes, stored_at) VALUES "
                "('parse_body', %s, 1, 1, %s)"
            ),
            ("a" * 64, AT),
        )
        conn.execute(
            (
                "INSERT INTO objects (class, digest, raw_bytes, stored_bytes, stored_at) VALUES "
                "('derivation_output', %s, 1, 1, %s)"
            ),
            ("e" * 64, AT),
        )
        conn.execute(
            "INSERT INTO derivation_evicted (generation_id, evicted_at) VALUES ('dg_proj', %s)",
            (AT,),
        )
        conn.execute(
            "INSERT INTO source_generation_packs (generation_id, pack_digest, packed_at) VALUES ('sg1', %s, %s)",
            ("b" * 64, AT),
        )


def test_older_code_refuses_a_newer_postgres_schema(migrated: Database, monkeypatch) -> None:
    from swingset.state import postgres_schema

    state = migrated.state_dir
    migrated.close()
    monkeypatch.setattr(postgres_schema, "SCHEMA_VERSION", 36)
    with pytest.raises(RuntimeError, match="schema version"):
        open_database(state, lock=False)
