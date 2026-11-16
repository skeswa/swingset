"""Schema parity, canonical trigger bytes and real capability roles."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter

import psycopg
import pytest
from psycopg import sql

from swingset.dev.services import ensure_service
from swingset.state import postgres_schema as schema


@pytest.fixture
def fresh_database():
    service = ensure_service()
    name = "sst_schema_" + uuid.uuid4().hex[:20]
    with service.connect() as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'").format(
                sql.Identifier(name)
            )
        )
    try:
        with service.connect(name) as conn:
            yield service, name, conn
    finally:
        with service.connect() as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(name)))


def test_every_schema37_column_key_index_foreign_key_and_trigger_has_parity(fresh_database):
    _, _, conn = fresh_database
    schema.migrate(conn)
    result = schema.validate_schema(conn, full=True)
    assert result["equal"]
    assert (result["source_tables"], result["tables"], result["triggers"], result["views"]) == (
        141,
        142,
        809,
        1,
    )
    assert schema.semantic_columns("snapshots")[-1] == "extract_recipe_sha256"
    assert "rowid" not in schema.semantic_columns("snapshots")


def test_schema_parity_rejects_structural_drift_beyond_counts(fresh_database):
    _, _, conn = fresh_database
    schema.migrate(conn)
    conn.execute("ALTER TABLE ops.runs ALTER COLUMN dry_run DROP NOT NULL")
    with pytest.raises(RuntimeError, match="nullability"):
        schema.verify(conn)


def test_schema_parity_rejects_partial_index_predicate_drift(fresh_database):
    _, _, conn = fresh_database
    schema.migrate(conn)
    conn.execute("DROP INDEX ops.execution_one_publication")
    conn.execute(
        "CREATE UNIQUE INDEX execution_one_publication ON ops.execution_admissions((1)) WHERE false"
    )
    with pytest.raises(RuntimeError, match="predicate"):
        schema.verify(conn)


def test_all_guard_messages_preserve_the_original_literal_bytes():
    reference = "\n".join(
        item["sql"] for item in schema.reference_manifest()["objects"] if item["type"] == "trigger"
    )
    source = Counter(re.findall(r"RAISE\(ABORT,('(?:[^']|'')*')\)", reference))
    target = Counter(re.findall(r"MESSAGE=('(?:[^']|'')*')", schema.migration_sql()))
    assert source == target
    assert sum(source.values()) == 519


def test_compact_json_preserves_unicode_escape_and_numeric_types(fresh_database):
    _, _, conn = fresh_database
    schema.migrate(conn)
    expected = json.dumps(
        ["雪", 'quote"slash\\ /', "\n\t\x01", "", None, 7, 2.5],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    found = conn.execute(
        "SELECT ops.compact_json(json_build_array(%s::text,%s::text,%s::text,%s::text,NULL,7,2.5))",
        ("雪", 'quote"slash\\ /', "\n\t\x01", ""),
    ).fetchone()[0]
    assert found == expected
    assert conn.execute(
        "SELECT ops.try_timestamp('not-a-time'),ops.try_timestamp('2026-02-30'),ops.try_timestamp('2026-11-04T10:30:00Z')::text"
    ).fetchone() == (None, None, "2026-11-04 10:30:00+00")


def test_worker_and_control_grants_protect_schema_identity_and_evidence(fresh_database):
    service, name, conn = fresh_database
    schema.migrate(conn)
    for role in ("worker", "control"):
        with psycopg.connect(service.dsn(name, role=role), autocommit=True) as limited:
            assert schema.validate_schema(limited)["schema_version"] == schema.SCHEMA_VERSION
            assert limited.execute(
                "SELECT environment FROM ops.environment_identity"
            ).fetchone() == ("dev",)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                limited.execute("UPDATE ops.environment_identity SET environment='production'")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                limited.execute("ALTER TABLE ops.runs ADD COLUMN forbidden text")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                limited.execute("TRUNCATE ops.runs")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                limited.execute("ALTER TABLE ops.runs DISABLE TRIGGER ALL")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                limited.execute("SET ROLE swingset_owner")
            if role == "worker":
                limited.execute("VACUUM (FULL,ANALYZE) ops.runs")
                assert limited.execute("SELECT ops.wal_size()").fetchone()[0] > 0
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    limited.execute("SELECT * FROM pg_catalog.pg_ls_waldir()")
            if role == "control":
                assert (
                    limited.execute("SELECT count(*) FROM evidence.observations").fetchone()[0] == 0
                )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    limited.execute("DELETE FROM evidence.observations")


def test_trigger_families_enqueue_bump_reject_and_roll_back(fresh_database):
    _, _, conn = fresh_database
    schema.migrate(conn)
    conn.execute("SELECT set_config('search_path',%s,false)", (schema.SEARCH_PATH,))
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES('fixture-watch','eepro','round','GET','https://example.test/','eepro.round','live')"
    )
    version = conn.execute(
        "SELECT version FROM derivation_input_versions WHERE namespace='watch_sources' AND input_key='all'"
    ).fetchone()[0]
    conn.execute("UPDATE watches SET source='scoringdance' WHERE watch_id='fixture-watch'")
    assert (
        conn.execute(
            "SELECT version FROM derivation_input_versions WHERE namespace='watch_sources' AND input_key='all'"
        ).fetchone()[0]
        > version
    )
    conn.execute(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES('project','event','fixture-event','2026-11-04T00:00:00Z')"
    )
    before = conn.execute(
        "SELECT generation FROM work_generations WHERE unit_id='fixture-event'"
    ).fetchone()[0]
    with pytest.raises(RuntimeError):
        with conn.transaction():
            conn.execute(
                "UPDATE pending_work SET enqueued_at='2026-11-04T00:00:01Z' WHERE unit_id='fixture-event'"
            )
            assert (
                conn.execute(
                    "SELECT generation FROM work_generations WHERE unit_id='fixture-event'"
                ).fetchone()[0]
                > before
            )
            raise RuntimeError("rollback")
    assert (
        conn.execute(
            "SELECT generation FROM work_generations WHERE unit_id='fixture-event'"
        ).fetchone()[0]
        == before
    )
    conn.execute(
        "INSERT INTO identity_journal_acceptances(digest,bundle_digest,accepted_at,decision_count,generation) VALUES('fixture-digest','fixture-bundle','2026-11-04T00:00:00Z',0,1)"
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="append-only"):
        conn.execute("DELETE FROM identity_journal_acceptances WHERE digest='fixture-digest'")


@pytest.mark.parametrize("kind", ["publication", "publication_narrow"])
def test_publication_fence_actions_keep_controls_and_noop_narrow_upserts(fresh_database, kind):
    _, _, conn = fresh_database
    schema.migrate(conn)
    conn.execute("SELECT set_config('search_path',%s,false)", (schema.SEARCH_PATH,))
    conn.execute("INSERT INTO meta(key,value) VALUES('input_bundle_hash','before')")
    conn.execute(
        "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state) VALUES('fixture-admission',%s,'2026-11-04T00:00:00Z',0,0,0,'active')",
        (kind,),
    )
    conn.execute("UPDATE control_state SET revision=revision+1 WHERE singleton=1")
    if kind == "publication_narrow":
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('input_bundle_hash','ignored') ON CONFLICT DO NOTHING"
        )
        conn.execute("UPDATE meta SET value=value WHERE key='input_bundle_hash'")
        conn.execute("INSERT INTO meta(key,value) VALUES('unrelated-counter','allowed')")
    for statement in (
        "INSERT INTO admission_policies(page_kind,contract_version,mode,policy_revision) VALUES('fixture.kind','1','shadow','1')",
        "UPDATE meta SET value='changed' WHERE key='input_bundle_hash'",
        "DELETE FROM meta WHERE key='input_bundle_hash'",
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match="active publication fences"):
            conn.execute(statement)
    assert conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone() == (
        "before",
    )


def test_deferred_delete_authority_must_not_outlive_transaction(fresh_database):
    _, _, conn = fresh_database
    schema.migrate(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO ops.storage_eviction_authority(singleton,reason,granted_at) VALUES(1,'fixture','2026-11-04T00:00:00Z')"
            )
    assert conn.execute("SELECT count(*) FROM ops.storage_eviction_authority").fetchone()[0] == 0


def test_explicit_forward_migration_preserves_environment_and_fences_new_tables(
    fresh_database, tmp_path, monkeypatch
):
    import shutil

    _, _, conn = fresh_database
    schema.migrate(conn)
    original_identity = conn.execute("SELECT database_id FROM ops.environment_identity").fetchone()[
        0
    ]
    original_version = schema.SCHEMA_VERSION
    next_version = original_version + 1
    copied = tmp_path / "migrations"
    shutil.copytree(schema.ROOT, copied)
    (copied / f"{next_version:04d}_fixture_probe.sql").write_text(
        "CREATE TABLE ops.fixture_probe(value TEXT PRIMARY KEY);\n"
    )
    monkeypatch.setattr(schema, "ROOT", copied)
    monkeypatch.setattr(schema, "SCHEMA_VERSION", next_version)
    with pytest.raises(RuntimeError, match="version or migration checksum"):
        schema.validate_schema(conn)
    assert schema.migrate(conn)["schema_version"] == next_version
    assert schema.migrate(conn)["schema_version"] == next_version
    assert (
        conn.execute("SELECT database_id FROM ops.environment_identity").fetchone()[0]
        == original_identity
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid='ops.fixture_probe'::regclass AND NOT tgisinternal"
        ).fetchone()[0]
        == 3
    )
    conn.execute("INSERT INTO ops.fixture_probe VALUES('before')")
    conn.execute(
        "INSERT INTO ops.execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state) VALUES('fixture-forward','publication','2026-11-04T00:00:00Z',0,0,0,'active')"
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="active publication fences"):
        conn.execute("INSERT INTO ops.fixture_probe VALUES('blocked')")
    monkeypatch.setattr(schema, "SCHEMA_VERSION", original_version)
    with pytest.raises(RuntimeError, match="downgrade"):
        schema.migrate(conn)


def test_an_applied_migration_cannot_be_silently_edited(fresh_database, tmp_path, monkeypatch):
    import shutil

    _, _, conn = fresh_database
    schema.migrate(conn)
    copied = tmp_path / "migrations"
    shutil.copytree(schema.ROOT, copied)
    with (copied / "0037_schema.sql").open("a") as stream:
        stream.write("\n-- a changed applied migration is not accepted\n")
    monkeypatch.setattr(schema, "ROOT", copied)
    with pytest.raises(RuntimeError, match="applied Postgres migration checksum"):
        schema.migrate(conn)
    assert conn.execute("SELECT value FROM ops.meta WHERE key='schema_version'").fetchone() == (
        "37",
    )


def test_full_parity_uses_unique_row_labels_on_the_worker_engine(fresh_database):
    from swingset.state import engine

    service, name, conn = fresh_database
    schema.migrate(conn)
    with engine.connect(service.dsn(name, role="worker")) as worker:
        assert schema.validate_schema(worker, full=True)["equal"]


def test_native38_forward_upgrade_keeps_sqlite37_rows_and_existing_identity(
    fresh_database, monkeypatch
):
    _, _, conn = fresh_database
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 38)
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, "SCHEMA_VERSION", 37)
        schema.migrate(conn, environment="dev")
    original_identity = conn.execute("SELECT * FROM ops.environment_identity").fetchall()
    original_meta = conn.execute("SELECT * FROM ops.meta ORDER BY key").fetchall()
    with pytest.raises(RuntimeError, match="version or migration checksum"):
        schema.validate_schema(conn)
    assert schema.migrate(conn)["schema_version"] == 38
    assert conn.execute("SELECT * FROM ops.environment_identity").fetchall() == original_identity
    assert conn.execute("SELECT * FROM ops.meta ORDER BY key").fetchall() == original_meta
    assert (
        dict(conn.execute("SELECT key,value FROM ops.schema_identity()"))["source_schema_version"]
        == "37"
    )
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, "SCHEMA_VERSION", 37)
        with pytest.raises(RuntimeError, match="version or migration checksum"):
            schema.validate_schema(conn)
        with pytest.raises(RuntimeError, match="downgrade"):
            schema.migrate(conn)


def test_subject_delete_indexes_preserve_rows_history_and_cross_kind_deletes(
    fresh_database, monkeypatch
):
    _, _, conn = fresh_database
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, "SCHEMA_VERSION", 40)
        schema.migrate(conn)
    conn.execute("SELECT set_config('search_path',%s,false)", (schema.SEARCH_PATH,))
    conn.execute(
        "INSERT INTO identity_links(link_id,subject_kind,subject_id,wsdc_id,method,status,"
        "confidence,constraints_applied,asserted_at,run_id,linker_version) "
        "SELECT kind||n,kind,'subject-'||n,NULL,'fixture','review',0.5,'{}',"
        "'2026-11-17T00:00:00Z','fixture','9' "
        "FROM generate_series(1,300) AS n CROSS JOIN (VALUES('entry'),('judge')) AS kinds(kind)"
    )
    conn.execute(
        "INSERT INTO link_candidates(subject_kind,subject_id,wsdc_id,score,name_similarity,"
        "name_rarity,division_ok,role_ok,recency_ok,geography,bib_reuse,registry_confirms,"
        "source_id_confirms,rank,chosen,run_id) "
        "SELECT kind,'subject-'||n,1,0.5,0.5,0.5,NULL,NULL,0,NULL,0,0,0,1,0,'fixture' "
        "FROM generate_series(1,300) AS n CROSS JOIN (VALUES('entry'),('judge')) AS kinds(kind)"
    )

    def snapshot():
        return {
            table: conn.execute(
                sql.SQL("SELECT row_to_json(t)::text FROM {} AS t ORDER BY 1").format(
                    sql.SQL(schema.qualified(table))
                )
            ).fetchall()
            for table in (
                "identity_links",
                "link_candidates",
                "identity_link_history",
                "history_dispatch_fence",
            )
        }

    baseline = snapshot()
    assert schema.migrate(conn)["schema_version"] == schema.SCHEMA_VERSION
    assert schema.migrate(conn)["schema_version"] == schema.SCHEMA_VERSION
    assert snapshot() == baseline
    for table in ("identity_links", "link_candidates"):
        conn.execute(sql.SQL("ANALYZE {}").format(sql.SQL(schema.qualified(table))))
        plan = conn.execute(
            sql.SQL("EXPLAIN (FORMAT JSON) DELETE FROM {} WHERE subject_id=%s").format(
                sql.SQL(schema.qualified(table))
            ),
            ("subject-1",),
        ).fetchone()[0][0]["Plan"]
        nodes = [plan]
        for node in nodes:
            nodes.extend(node.get("Plans", []))
        assert any(node.get("Index Name") == table + "_subject_idx" for node in nodes)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        with conn.transaction():
            for table in ("link_candidates", "identity_links"):
                deleted = conn.execute(
                    sql.SQL("DELETE FROM {} WHERE subject_id=%s").format(
                        sql.SQL(schema.qualified(table))
                    ),
                    ("subject-1",),
                )
                assert deleted.rowcount == 2
            assert (
                conn.execute(
                    "SELECT count(*) FROM identity_link_history WHERE subject_id='subject-1'"
                ).fetchone()[0]
                == 2
            )
            raise Rollback
    assert snapshot() == baseline


@pytest.mark.parametrize(
    "base_version,target_version", [(38, 39), (39, 40), (38, 40), (40, 41), (38, 41)]
)
def test_statement_upgrades_preserve_rows_and_worker_fence_semantics(
    fresh_database, monkeypatch, base_version, target_version
):
    from swingset.state import engine

    service, name, conn = fresh_database
    monkeypatch.setattr(schema, "SCHEMA_VERSION", target_version)
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, "SCHEMA_VERSION", base_version)
        schema.migrate(conn, environment="dev")
    conn.execute("SELECT set_config('search_path',%s,false)", (schema.SEARCH_PATH,))
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) "
        "SELECT 'watch-'||n,'eepro','round','GET','https://example.test/'||n,'eepro.round','live' "
        "FROM generate_series(1,8) AS n"
    )
    conn.execute(
        "INSERT INTO work_generations(stage,unit_kind,unit_id,generation,retry_generation,"
        "retry_reason,retry_requested_at) VALUES('parse','snapshot','one',7,3,'rehearsal',"
        "'2026-11-10T00:00:00Z')"
    )
    queries = [
        sql.SQL(
            "SELECT {} AS source_table,coalesce(array_agg(row_to_json(t)::text "
            "ORDER BY row_to_json(t)::text),ARRAY[]::text[]) AS rows FROM {} AS t"
        ).format(sql.Literal(table), sql.SQL(schema.qualified(table)))
        for table in schema.reference_manifest()["tables"]
    ]

    def rows(connection):
        return dict(connection.execute(sql.SQL(" UNION ALL ").join(queries)))

    def epoch(connection):
        return connection.execute('SELECT revision FROM "fetch".history_dispatch_fence').fetchone()[
            0
        ]

    baseline = rows(conn)
    identity = conn.execute("SELECT * FROM ops.environment_identity").fetchall()
    grants_sql = (
        "SELECT n.nspname,p.proname,p.proowner,p.proacl,p.prosecdef,p.proconfig FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE p.proname=ANY(%s) ORDER BY n.nspname,p.proname"
    )
    names = ["tr_" + name for name in sorted(schema.STATEMENT_HISTORY_TRIGGERS)]
    if target_version >= 40:
        names += ["tr_" + name for name in sorted(schema.STATEMENT_WORK_TRIGGERS)]
    grants = conn.execute(grants_sql, (names,)).fetchall()

    class Rollback(Exception):
        pass

    def exercise(connection):
        deltas = []
        with service.connect(name) as observer:
            committed = epoch(observer)
            with pytest.raises(Rollback):
                with connection.transaction():
                    for statement in (
                        "UPDATE watches SET parser=parser",
                        "UPDATE watches SET parser=parser WHERE false",
                        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
                        "VALUES('parse','snapshot','one','2026-11-10T00:00:00Z'),"
                        "('parse','snapshot','two','2026-11-10T00:00:00Z'),"
                        "('parse','snapshot','three','2026-11-10T00:00:00Z')",
                        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
                        "SELECT 'parse','snapshot','absent','2026-11-10T00:00:00Z' WHERE false",
                        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
                        "VALUES('parse','snapshot','one','2026-11-10T00:00:01Z'),"
                        "('parse','snapshot','four','2026-11-10T00:00:01Z') "
                        "ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET enqueued_at=excluded.enqueued_at",
                        "UPDATE work_generations SET generation=generation+1",
                        "UPDATE pending_work SET enqueued_at=enqueued_at",
                        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
                        "VALUES('parse','snapshot','one','2026-11-10T00:00:02Z') ON CONFLICT DO NOTHING",
                        "UPDATE work_generations SET generation=generation+1 WHERE false",
                        "DELETE FROM work_generations WHERE unit_id='three'",
                        "UPDATE pending_work SET enqueued_at=enqueued_at WHERE unit_id='three'",
                    ):
                        before = epoch(connection)
                        physical = connection.execute(
                            "SELECT ctid::text FROM history_dispatch_fence"
                        ).fetchone()[0]
                        connection.execute(statement)
                        delta = epoch(connection) - before
                        deltas.append(delta)
                        if delta == 0:
                            assert (
                                connection.execute(
                                    "SELECT ctid::text FROM history_dispatch_fence"
                                ).fetchone()[0]
                                == physical
                            )
                        assert epoch(observer) == committed
                    changed = rows(connection)
                    raise Rollback
            assert epoch(observer) == committed
        assert rows(connection) == baseline
        return deltas, changed

    legacy_result = exercise(conn)
    assert legacy_result[0] == [8, 0, 6, 0, 4, 4, 8, 0, 0, 1, 1]
    assert schema.migrate(conn)["schema_version"] == target_version
    assert schema.migrate(conn)["schema_version"] == target_version
    assert rows(conn) == baseline
    assert conn.execute("SELECT * FROM ops.environment_identity").fetchall() == identity
    assert conn.execute(grants_sql, (names,)).fetchall() == grants
    with engine.connect(service.dsn(name, role="worker")) as worker:
        assert schema.validate_schema(worker, full=True)["equal"]
        assert exercise(worker) == legacy_result
        with service.connect(name) as observer:
            before = epoch(observer)
            with worker.transaction():
                worker.execute(
                    "UPDATE watches SET parser=parser WHERE watch_id IN ('watch-1','watch-2')"
                )
                assert epoch(worker) == before + 2
                assert epoch(observer) == before
            assert epoch(observer) == before + 2
            with worker.transaction():
                worker.execute(
                    "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
                    "VALUES('parse','snapshot','one','2026-11-10T00:00:00Z'),"
                    "('parse','snapshot','committed','2026-11-10T00:00:00Z')"
                )
                assert epoch(worker) == before + 6
                assert epoch(observer) == before + 2
                assert (
                    observer.execute(
                        "SELECT generation FROM derive.work_generations WHERE unit_id='one'"
                    ).fetchone()[0]
                    == 7
                )
            assert epoch(observer) == before + 6
            assert tuple(
                observer.execute(
                    "SELECT generation,retry_generation,retry_reason FROM derive.work_generations "
                    "WHERE unit_id='one'"
                ).fetchone()
            ) == (8, 3, "rehearsal")
            assert tuple(
                observer.execute(
                    "SELECT generation,retry_generation,retry_reason FROM derive.work_generations "
                    "WHERE unit_id='committed'"
                ).fetchone()
            ) == (1, 0, None)
        conn.execute(
            "INSERT INTO ops.execution_admissions(action_id,action_kind,admitted_at,control_revision,"
            "all_sources,all_kinds,state) VALUES('statement-fence','publication',"
            "'2026-11-10T00:00:00Z',0,0,0,'active')"
        )
        before = epoch(worker)
        for statement in (
            "UPDATE watches SET parser=parser",
            "UPDATE pending_work SET enqueued_at=enqueued_at",
            "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
            "VALUES('parse','snapshot','blocked','2026-11-10T00:00:00Z')",
        ):
            generations = worker.execute(
                "SELECT * FROM work_generations ORDER BY unit_id"
            ).fetchall()
            with pytest.raises(psycopg.errors.CheckViolation, match="active publication fences"):
                worker.execute(statement)
            assert epoch(worker) == before
            assert (
                worker.execute("SELECT * FROM work_generations ORDER BY unit_id").fetchall()
                == generations
            )


@pytest.mark.parametrize("version", [37, 38, 39])
def test_checkpoint_reader_admits_only_verified_readonly_native38(
    fresh_database, tmp_path, monkeypatch, version
):
    import shutil

    from swingset.state import engine
    from swingset.state.db import open_database

    service, name, conn = fresh_database
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, "SCHEMA_VERSION", version)
        schema.migrate(conn, environment="dev")
    original = conn.execute(
        "SELECT obj_description('ops'::regnamespace,'pg_namespace')"
    ).fetchone()[0]
    state = tmp_path / "checkpoint"
    engine.write_settings(state, service.dsn(name, role="worker"), environment="dev")
    with pytest.raises(RuntimeError, match="schema version or migration checksum"):
        open_database(state, read_only=True, lock=False)
    if version != 38:
        with pytest.raises(RuntimeError, match="checkpoint schema"):
            open_database(state, read_only=True, lock=False, allow_checkpoint_schema=True)
    else:
        with open_database(
            state, read_only=True, lock=False, allow_checkpoint_schema=True
        ) as checkpoint:
            assert schema.validate_checkpoint_schema(checkpoint.connection)["schema_version"] == 38
            with pytest.raises(psycopg.OperationalError, match="readonly"):
                checkpoint.connection.execute("INSERT INTO ops.meta VALUES('forbidden','write')")
        with monkeypatch.context() as future:
            future.setattr(schema, "SCHEMA_VERSION", schema.SCHEMA_VERSION + 1)
            with pytest.raises(RuntimeError, match="checkpoint schema"):
                open_database(state, read_only=True, lock=False, allow_checkpoint_schema=True)
        copied = tmp_path / "changed-migrations"
        shutil.copytree(schema.ROOT, copied)
        with (copied / "0038_environment_dryrun.sql").open("a") as stream:
            stream.write("\n-- checksum mismatch must be rejected\n")
        with monkeypatch.context() as changed:
            changed.setattr(schema, "ROOT", copied)
            with pytest.raises(RuntimeError, match="checkpoint schema"):
                open_database(state, read_only=True, lock=False, allow_checkpoint_schema=True)
    for read_only, lock in [(False, False), (True, True)]:
        with pytest.raises(ValueError, match="read-only access"):
            open_database(state, read_only=read_only, lock=lock, allow_checkpoint_schema=True)
    assert (
        conn.execute("SELECT obj_description('ops'::regnamespace,'pg_namespace')").fetchone()[0]
        == original
    )
    assert conn.execute("SELECT count(*) FROM ops.meta WHERE key='forbidden'").fetchone()[0] == 0


@pytest.mark.parametrize("fault", ["body", "level", "transition", "event"])
@pytest.mark.parametrize(
    "namespace,table,trigger,event,transition",
    [
        ("fetch", "watches", "history_timing_watches_update", "UPDATE", "swingset_changed_rows"),
        ("derive", "pending_work", "work_enqueued", "INSERT", "swingset_pending_rows"),
        ("derive", "pending_work", "work_invalidated", "UPDATE", "swingset_pending_rows"),
    ],
)
def test_catalog_audit_rejects_changed_statement_trigger(
    fresh_database, fault, namespace, table, trigger, event, transition
):
    _, _, conn = fresh_database
    schema.migrate(conn)
    function = sql.Identifier(namespace, "tr_" + trigger)
    if fault == "body":
        conn.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION {}() RETURNS trigger "
                "LANGUAGE plpgsql AS $$BEGIN RETURN NULL; END$$"
            ).format(function)
        )
    else:
        conn.execute(
            sql.SQL("DROP TRIGGER {} ON {}").format(
                sql.Identifier(trigger), sql.Identifier(namespace, table)
            )
        )
        alias = "wrong_rows" if fault == "transition" else transition
        binding = (
            "FOR EACH ROW"
            if fault == "level"
            else (f"REFERENCING NEW TABLE AS {alias} FOR EACH STATEMENT")
        )
        if fault == "event":
            event = "UPDATE" if event == "INSERT" else "INSERT"
        conn.execute(
            sql.SQL("CREATE TRIGGER {} AFTER {} ON {} {} EXECUTE FUNCTION {}()").format(
                sql.Identifier(trigger),
                sql.SQL(event),
                sql.Identifier(namespace, table),
                sql.SQL(binding),
                function,
            )
        )
    with pytest.raises(
        RuntimeError,
        match="function body differs|level or transition tables differ|event or timing differs",
    ):
        schema.validate_schema(conn, full=True)


def test_fresh_dryrun_is_seeded_once_after_forward_ddl_and_worker_cannot_change_it(
    fresh_database, monkeypatch
):
    service, name, conn = fresh_database
    seed = schema.seed_environment
    calls = []

    def observed_seed(connection, environment="dev", database_id=None):
        assert (
            connection.execute("SELECT count(*) FROM ops.environment_identity").fetchone()[0] == 0
        )
        assert dict(connection.execute("SELECT key,value FROM ops.schema_identity()"))[
            "schema_version"
        ] == str(schema.SCHEMA_VERSION)
        calls.append(environment)
        return seed(connection, environment, database_id)

    monkeypatch.setattr(schema, "seed_environment", observed_seed)
    schema.migrate(conn, environment="dryrun")
    assert calls == ["dryrun"]
    original = conn.execute(
        "SELECT environment,database_id FROM ops.environment_identity"
    ).fetchone()
    assert original[0] == "dryrun"
    schema.migrate(conn, environment="dryrun")
    assert calls == ["dryrun"]
    with psycopg.connect(service.dsn(name, role="worker"), autocommit=True) as worker:
        assert (
            worker.execute(
                "SELECT environment,database_id FROM ops.environment_identity"
            ).fetchone()
            == original
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("UPDATE ops.environment_identity SET environment='production'")
    with pytest.raises(RuntimeError, match="cannot change"):
        schema.migrate(conn, environment="production")


def test_failed_fresh_forward_ddl_leaves_no_temporary_identity(
    fresh_database, tmp_path, monkeypatch
):
    import shutil

    _, _, conn = fresh_database
    copied = tmp_path / "migration-failure"
    shutil.copytree(schema.ROOT, copied)
    next_version = schema.SCHEMA_VERSION + 1
    (copied / f"{next_version:04d}_fixture_failure.sql").write_text("SELECT 1/0;\n")
    monkeypatch.setattr(schema, "ROOT", copied)
    monkeypatch.setattr(schema, "SCHEMA_VERSION", next_version)
    with pytest.raises(psycopg.errors.DivisionByZero):
        schema.migrate(conn, environment="dryrun")
    assert conn.execute("SELECT to_regclass('ops.environment_identity')").fetchone()[0] is None


def test_trusted_empty_table_bootstrap_is_adopted_by_owner_without_cluster_admin(fresh_database):
    from swingset.state import postgres_bootstrap as bootstrap

    _, _, conn = fresh_database
    marker = bootstrap.prepare(conn)
    assert bootstrap.prepare(conn) == marker
    assert conn.execute("SELECT count(*) FROM ops.environment_identity").fetchone()[0] == 0
    with pytest.raises(RuntimeError, match="version or migration checksum"):
        schema.validate_schema(conn)
    conn.execute("RESET search_path")
    conn.execute("SET ROLE swingset_owner")
    try:
        assert bootstrap.adopt(conn)
        assert schema.migrate(conn, environment="dryrun")["schema_version"] == schema.SCHEMA_VERSION
        assert (
            conn.execute("SELECT environment FROM ops.environment_identity").fetchone()[0]
            == "dryrun"
        )
        assert (
            conn.execute("SELECT value FROM ops.meta WHERE key='schema_version'").fetchone()[0]
            == "37"
        )
    finally:
        conn.execute("RESET ROLE")
    assert not bootstrap.prepared(conn)
    assert schema.validate_schema(conn, full=True)["equal"]


@pytest.mark.parametrize("change", ["row", "column", "binding", "acl"])
def test_prepared_table_adoption_refuses_data_catalog_or_foreign_binding_drift(
    fresh_database, change
):
    from swingset.state import postgres_bootstrap as bootstrap

    _, _, conn = fresh_database
    bootstrap.prepare(conn)
    if change == "row":
        conn.execute("INSERT INTO ops.meta VALUES('unexpected','data')")
    elif change == "column":
        conn.execute("ALTER TABLE ops.runs ALTER COLUMN dry_run DROP NOT NULL")
    elif change == "acl":
        conn.execute("GRANT SELECT ON ops.runs TO swingset_worker")
    else:
        metadata = bootstrap._comment(conn)
        metadata[bootstrap.KEY]["database"]["name"] = "a_foreign_database"
        conn.execute(
            sql.SQL("COMMENT ON SCHEMA ops IS {}").format(sql.Literal(json.dumps(metadata)))
        )
    with pytest.raises(RuntimeError, match="prepared database"):
        bootstrap.adopt(conn)


def test_worker_and_control_cannot_prepare_or_adopt_tables(fresh_database):
    from swingset.state import postgres_bootstrap as bootstrap

    _, _, conn = fresh_database
    bootstrap.prepare(conn)
    for role in ("swingset_worker", "swingset_control"):
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        try:
            with pytest.raises(RuntimeError, match="administrator"):
                bootstrap.prepare(conn)
            with pytest.raises(RuntimeError, match="only the application owner"):
                bootstrap.adopt(conn)
        finally:
            conn.execute("RESET ROLE")


def test_prepared_catalog_hash_matches_native_engine_reader_and_real_owner_open(fresh_database):
    from swingset.state import engine
    from swingset.state import postgres_bootstrap as bootstrap

    service, name, admin = fresh_database
    marker = bootstrap.prepare(admin)
    with engine.connect(service.dsn(name)) as wrapped:
        assert bootstrap._catalog(wrapped) == marker["catalog_sha256"]
        wrapped.execute("RESET search_path")
        wrapped.execute("SET ROLE swingset_owner")
        assert bootstrap.adopt(wrapped)
        assert (
            schema.migrate(wrapped, environment="dryrun")["schema_version"] == schema.SCHEMA_VERSION
        )
        assert schema.validate_schema(wrapped, full=True)["equal"]


def test_real_owner_login_finishes_bootstrap_without_wal_grant_authority(fresh_database):
    from psycopg.conninfo import make_conninfo

    from swingset.state import postgres_bootstrap as bootstrap

    service, name, admin = fresh_database
    login = "sst_owner_" + uuid.uuid4().hex[:20]
    password = uuid.uuid4().hex
    marker = bootstrap.prepare(admin)
    assert marker["owner_wal_execute"] is True
    assert marker["owner_wal_grant_option"] is False
    admin.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(login), sql.Literal(password)
        )
    )
    try:
        admin.execute(
            sql.SQL("GRANT swingset_owner TO {} WITH INHERIT TRUE, SET TRUE, ADMIN FALSE").format(
                sql.Identifier(login)
            )
        )
        with psycopg.connect(
            make_conninfo(service.dsn(name), user=login, password=password), autocommit=True
        ) as owner:
            assert owner.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
            ).fetchone() == (False, False, False)
            assert bootstrap.adopt(owner)
            assert (
                schema.migrate(owner, environment="dryrun")["schema_version"]
                == schema.SCHEMA_VERSION
            )
            assert owner.execute(
                "SELECT has_function_privilege(current_user,'pg_catalog.pg_ls_waldir()','EXECUTE'),has_function_privilege(current_user,'pg_catalog.pg_ls_waldir()','EXECUTE WITH GRANT OPTION')"
            ).fetchone() == (True, False)
            assert not owner.execute(
                "SELECT has_function_privilege('swingset_reader','pg_catalog.pg_ls_waldir()','EXECUTE')"
            ).fetchone()[0]
            try:
                owner.execute(
                    "GRANT EXECUTE ON FUNCTION pg_catalog.pg_ls_waldir() TO swingset_reader"
                )
            except psycopg.errors.InsufficientPrivilege:
                pass
            assert not owner.execute(
                "SELECT has_function_privilege('swingset_reader','pg_catalog.pg_ls_waldir()','EXECUTE')"
            ).fetchone()[0]
        with psycopg.connect(service.dsn(name, role="worker"), autocommit=True) as worker:
            assert worker.execute("SELECT ops.wal_size()").fetchone()[0] > 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute("SELECT * FROM pg_catalog.pg_ls_waldir()")
    finally:
        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))


@pytest.mark.parametrize("phase", ["prepare", "adopt"])
def test_bootstrap_refuses_unexpected_wal_grant_authority_without_revoking_it(
    fresh_database, phase
):
    from swingset.state import postgres_bootstrap as bootstrap

    _, _, admin = fresh_database
    schema.install_roles(admin)
    with admin.transaction(force_rollback=True):
        if phase == "adopt":
            bootstrap.prepare(admin)
        admin.execute(
            "GRANT EXECUTE ON FUNCTION pg_catalog.pg_ls_waldir() TO swingset_owner WITH GRANT OPTION"
        )
        with pytest.raises(RuntimeError, match="without grant authority"):
            getattr(bootstrap, phase)(admin)
        assert admin.execute(
            "SELECT has_function_privilege('swingset_owner','pg_catalog.pg_ls_waldir()','EXECUTE WITH GRANT OPTION')"
        ).fetchone()[0]
        if phase == "prepare":
            assert admin.execute("SELECT to_regnamespace('ops')").fetchone()[0] is None
