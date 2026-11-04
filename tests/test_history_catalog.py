"""Real history closure across superseded labels, anchors and JSON references."""

from __future__ import annotations

import pytest
from test_postgres_schema import fresh_database  # noqa: F401

from swingset.state import history_catalog as history
from swingset.state import postgres_schema as schema


def seed_history(conn):
    schema.migrate(conn)
    conn.execute("SELECT set_config('search_path',%s,false)", (schema.SEARCH_PATH,))
    conn.execute(
        "INSERT INTO runs(run_id,started_at,dry_run) VALUES('history-run','2026-11-04T00:00:00Z',0)"
    )
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES('history-watch','eepro','round','GET','https://example.test/','eepro.round','live')"
    )
    conn.execute(
        "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES('history-unit','history-watch','round')"
    )
    conn.execute(
        "INSERT INTO source_generations(generation_id,unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,run_id,state,removal_authority) VALUES('source-previous','history-unit','round','1','input','[]','{}','{}','{}','2026-11-04T00:00:00Z','history-run','superseded','none')"
    )
    conn.execute(
        "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES('history-deps','{\"nested\":[{\"generation_id\":\"source-previous\"}]}'),('cache-deps','{}')"
    )
    for kind, unit in (("event", "cache-event"), ("dataset", "weekly")):
        stage = "build" if kind == "dataset" else "project"
        conn.execute(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES(%s,%s,%s,'2026-11-04T00:00:00Z')",
            (stage, kind, unit),
        )
    for generation, stage, kind, unit, deps, previous in (
        ("project-previous", "project", "event", "cache-event", "cache-deps", None),
        ("project-selected", "project", "event", "cache-event", "cache-deps", "project-previous"),
        ("build-history", "build", "dataset", "weekly", "history-deps", "project-previous"),
    ):
        conn.execute(
            "INSERT INTO derivation_generations(generation_id,stage,unit_kind,unit_id,input_fingerprint,recipe_json,dependency_set_id,previous_generation_id,output_digest,row_count,created_at,run_id) VALUES(%s,%s,%s,%s,'input','{}',%s,%s,'fixture-output',1,'2026-11-04T00:00:00Z','history-run')",
            (generation, stage, kind, unit, deps, previous),
        )
    conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id='project-selected',materialized_signature='before' WHERE unit_kind='event'"
    )
    conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id='build-history' WHERE stage='build'"
    )
    conn.execute(
        "INSERT INTO derivation_payloads(payload_sha256,payload_json) VALUES('history-payload','{\"value\":\"immutable\"}')"
    )
    conn.execute(
        "INSERT INTO derivation_row_refs(generation_id,ordinal,table_name,record_key,payload_sha256) VALUES('build-history',0,'events','[\"fixture\"]','history-payload')"
    )
    conn.execute(
        "INSERT INTO derivation_payloads(payload_sha256,payload_json) VALUES('unused-payload','{}')"
    )
    conn.execute(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES('project','event','cache-only','2026-11-04T00:00:00Z')"
    )


def test_prepare_changes_only_session_temporary_tables(fresh_database):  # noqa: F811
    _, _, conn = fresh_database
    schema.migrate(conn)
    before = {
        table: conn.execute(f"SELECT count(*) FROM {schema.qualified(table)}").fetchone()[0]
        for table in schema.reference_manifest()["tables"]
    }
    history.prepare(conn)
    after = {
        table: conn.execute(f"SELECT count(*) FROM {schema.qualified(table)}").fetchone()[0]
        for table in before
    }
    assert after == before
    assert len([history.classification(table) for table in before]) == 141
    conn.execute("CREATE TABLE ops.unclassified_fixture(id bigint PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="no backup class"):
        history.prepare(conn)


def test_pipeline_input_tombstones_are_history_while_audit_and_environment_reset(tmp_path):
    import hashlib

    from swingset.state.db import open_database
    from swingset.state.work import accept_inputs

    absent = hashlib.sha256(b"").hexdigest()
    pipeline = {
        "recipe/runtime": absent,
        "overrides/removed.csv": absent,
        "recipe/env": "excluded environment",
    }
    changes = []

    def affected(name, old, new, conn):
        changes.append((name, old, new))
        return ()

    with open_database(tmp_path) as database:
        conn = database.connection
        accept_inputs(database, "pipeline", pipeline, affected)
        accept_inputs(database, "audit", {"recipe/code/build": "recomputed audit"}, affected)
        history.prepare(conn)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            assert history.select_history(conn)["accepted_inputs"] == 2
            assert [
                tuple(row)
                for row in conn.execute(
                    f"SELECT r.consumer,r.input_name,r.digest FROM derive.accepted_inputs r WHERE {history.where('accepted_inputs', 'r')} ORDER BY r.input_name"
                )
            ] == [
                ("pipeline", "overrides/removed.csv", absent),
                ("pipeline", "recipe/runtime", absent),
            ]
        changes.clear()
        assert accept_inputs(database, "pipeline", pipeline, affected) == set()
        assert changes == []
        updated = hashlib.sha256(b"new ordinary input bytes").hexdigest()
        assert accept_inputs(
            database, "pipeline", {"overrides/removed.csv": updated}, affected
        ) == {"overrides/removed.csv"}
        assert changes == [("overrides/removed.csv", absent, updated)]
        assert (
            conn.execute(
                "SELECT digest FROM derive.accepted_inputs WHERE consumer='pipeline' AND input_name='overrides/removed.csv'"
            ).fetchone()[0]
            == updated
        )
    assert history.classification("accepted_inputs").kind == "history"
    assert history.MUTABLE_FIELDS["accepted_inputs"] == ("digest",)


def test_closure_keeps_json_previous_and_current_anchor_targets(fresh_database):  # noqa: F811
    _, _, conn = fresh_database
    seed_history(conn)
    history.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        counts = history.select_history(conn)
        assert counts["derivation_generations"] == 3
        assert counts["source_generations"] == 1
        assert counts["derivation_payloads"] == 1
        assert counts["pending_work"] == 0
        assert counts["derivation_scopes"] == 2
        assert any(edge["kind"] == "soft_reference" for edge in history.reference_list(conn))
        conn.execute(f"DELETE FROM {history.marker('source_generations')}")
        with pytest.raises(RuntimeError, match="JSON identity"):
            history.assert_closed(conn)


def test_pressure_catalog_cursor_resets_without_losing_acquisition_cursor(fresh_database):  # noqa: F811
    _, _, conn = fresh_database
    schema.migrate(conn)
    conn.execute(
        "INSERT INTO \"fetch\".cursors(name,value) VALUES('event_pressure_inventory_after','123'),('accepted-origin-cursor','unchanged')"
    )
    history.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert history.select_history(conn)["cursors"] == 1
        assert conn.execute(
            f'SELECT c.name,c.value FROM "fetch".cursors c WHERE {history.where("cursors", "c")}'
        ).fetchall() == [("accepted-origin-cursor", "unchanged")]


def test_pending_parse_identity_token_is_history_but_other_hints_reset(fresh_database):  # noqa: F811
    _, _, conn = fresh_database
    seed_history(conn)
    conn.execute(
        "INSERT INTO \"fetch\".snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES('pending-source-body','history-watch','GET','https://example.test/','2026-11-04T00:00:00Z',200,%s,1,1,'history-run','Ok')",
        ("a" * 64,),
    )
    conn.execute(
        "INSERT INTO derive.pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES('parse','snapshot','pending-source-body','2026-11-03T00:00:00Z')"
    )
    history.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert history.select_history(conn)["pending_work"] == 1
        assert conn.execute(
            f"SELECT p.unit_id,p.enqueued_at FROM derive.pending_work p WHERE {history.where('pending_work', 'p')}"
        ).fetchall() == [("pending-source-body", "2026-11-03T00:00:00Z")]


def test_rebuild_can_append_source_alias_but_cannot_lose_retained_alias(fresh_database, tmp_path):  # noqa: F811
    from swingset.state import postgres_backup as backup

    _, _, conn = fresh_database
    seed_history(conn)
    conn.execute(
        "INSERT INTO evidence.source_equivalences(unit_key,input_fingerprint,generation_id,recipe_json,created_at,run_id) VALUES('history-unit','original-proof','source-previous','{}','2026-11-04T00:00:00Z','history-run')"
    )
    target = tmp_path / "history"
    backup.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        backup.export(conn, target, files={}, runtime_identity="fixture")
    conn.execute(
        "INSERT INTO evidence.source_equivalences(unit_key,input_fingerprint,generation_id,recipe_json,created_at,run_id) VALUES('history-unit','rebuilt-proof','source-previous','{}','2026-11-04T00:00:01Z','history-run')"
    )
    assert backup.audit(conn, target, after_rebuild=True)["equal"]
    # Simulate owner-level corruption after proving ordinary writers cannot delete it.
    from psycopg.errors import CheckViolation

    with pytest.raises(CheckViolation, match="immutable"):
        conn.execute(
            "DELETE FROM evidence.source_equivalences WHERE input_fingerprint='original-proof'"
        )
    with conn.transaction():
        conn.execute(
            "ALTER TABLE evidence.source_equivalences DISABLE TRIGGER source_equivalence_no_delete"
        )
        conn.execute(
            "DELETE FROM evidence.source_equivalences WHERE input_fingerprint='original-proof'"
        )
        conn.execute(
            "ALTER TABLE evidence.source_equivalences ENABLE TRIGGER source_equivalence_no_delete"
        )
    with pytest.raises(RuntimeError, match="source_equivalences"):
        backup.audit(conn, target, after_rebuild=True)


def test_unpublished_selected_scope_keeps_its_receipt_predecessor_and_output(fresh_database):  # noqa: F811
    _, _, conn = fresh_database
    seed_history(conn)
    conn.execute(
        "INSERT INTO derive.derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES('project','dancer','unpublished-unit','2026-11-04T00:00:00Z')"
    )
    for generation, previous, count in (
        ("unpublished-previous", None, 0),
        ("unpublished-selected", "unpublished-previous", 1),
    ):
        conn.execute(
            "INSERT INTO derive.derivation_generations(generation_id,stage,unit_kind,unit_id,input_fingerprint,recipe_json,dependency_set_id,previous_generation_id,output_digest,row_count,created_at,run_id) VALUES(%s,'project','dancer','unpublished-unit','input','{}','cache-deps',%s,'unpublished-output',%s,'2026-11-04T00:00:01Z','history-run')",
            (generation, previous, count),
        )
    conn.execute(
        "INSERT INTO derive.derivation_payloads(payload_sha256,payload_json) VALUES('unpublished-payload','{\"wsdc_id\":42}')"
    )
    conn.execute(
        "INSERT INTO derive.derivation_row_refs(generation_id,ordinal,table_name,record_key,payload_sha256) VALUES('unpublished-selected',0,'dancers','[42]','unpublished-payload')"
    )
    conn.execute(
        "UPDATE derive.derivation_scopes SET materialized_generation_id='unpublished-selected' WHERE unit_id='unpublished-unit'"
    )
    history.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        counts = history.select_history(conn)
        assert counts["derivation_generations"] == 5
        assert counts["derivation_payloads"] == 2
        assert conn.execute(
            f"SELECT g.generation_id,g.created_at,g.run_id FROM derive.derivation_generations g WHERE {history.where('derivation_generations', 'g')} AND g.unit_id='unpublished-unit' ORDER BY g.generation_id"
        ).fetchall() == [
            ("unpublished-previous", "2026-11-04T00:00:01Z", "history-run"),
            ("unpublished-selected", "2026-11-04T00:00:01Z", "history-run"),
        ]
