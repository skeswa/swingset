"""History COPY restore and immutable-field audits use real PostgreSQL."""

from __future__ import annotations

import os
import stat
import threading
import uuid

import pytest
from psycopg import sql
from psycopg.pq import TransactionStatus
from test_history_catalog import seed_history
from test_postgres_schema import fresh_database  # noqa: F401

from swingset.state import postgres_backup as backup
from swingset.state import postgres_schema as schema


def test_object_gc_clock_refresh_keeps_first_audit_exact_and_rejects_backward_clock_or_sizes(
    tmp_path,
):
    from datetime import UTC, datetime, timedelta

    from swingset.state import engine, objects
    from swingset.state.db import open_database

    now = datetime(2026, 11, 5, tzinfo=UTC)
    target = tmp_path / "history"
    with open_database(tmp_path / "state") as database:
        conn = database.connection
        store = objects.store_for(conn)
        digest, raw_bytes, stored_bytes = store.put_stream("parse_body", (b"known",))
        objects.record(conn, "parse_body", digest, raw_bytes, stored_bytes, now)
        path = store.path("parse_body", digest)
        backup.prepare(conn)
        with conn.read_snapshot():
            backup.export(
                conn,
                target,
                files={path.relative_to(database.state_dir).as_posix(): path},
                runtime_identity="fixture",
            )
        assert backup.audit(conn, target)["equal"]
        later = now + timedelta(minutes=10)
        objects.record(conn, "parse_body", digest, raw_bytes, stored_bytes, later)
        objects.record(conn, "parse_body", digest, raw_bytes, stored_bytes, now)
        assert conn.execute(
            "SELECT stored_at FROM ops.objects WHERE digest=%s", (digest,)
        ).fetchone()[0] == objects.stamp(later)
        with pytest.raises(RuntimeError, match="history differs: objects"):
            backup.audit(conn, target)
        assert backup.audit(conn, target, after_rebuild=True)["object_clocks"] == {
            "checked": 1,
            "advanced": 1,
            "unknown": 0,
        }
        conn.execute(
            "UPDATE ops.objects SET stored_at=%s WHERE digest=%s",
            (objects.stamp(now - timedelta(seconds=1)), digest),
        )
        with pytest.raises(RuntimeError, match="object GC clock"):
            backup.audit(conn, target, after_rebuild=True)
        conn.execute(
            "UPDATE ops.objects SET stored_at=%s WHERE digest=%s", (objects.stamp(later), digest)
        )
        with pytest.raises(engine.IntegrityError):
            conn.execute("UPDATE ops.objects SET stored_at=NULL WHERE digest=%s", (digest,))
        conn.execute("UPDATE ops.objects SET raw_bytes=raw_bytes+1 WHERE digest=%s", (digest,))
        with pytest.raises(RuntimeError, match="history differs: objects"):
            backup.audit(conn, target, after_rebuild=True)


def test_defensive_object_clock_join_preserves_unknowns(fresh_database):  # noqa: F811
    _, _, conn = fresh_database
    schema.migrate(conn)
    # Schema 37 forbids NULL storage clocks. Relax only this disposable fixture
    # to exercise the defensive legacy-unknown rules without exporting a
    # checkpoint that violates the real retained schema.
    conn.execute("ALTER TABLE ops.objects ALTER COLUMN stored_at DROP NOT NULL")
    conn.execute("CREATE TEMP TABLE _swingset_original_objects (LIKE ops.objects)")
    conn.execute(
        "INSERT INTO ops.objects(class,digest,raw_bytes,stored_bytes,stored_at) VALUES('parse_body','unknown',1,1,NULL),('parse_body','known',1,1,'2026-11-05T00:00:00Z')"
    )
    conn.execute("INSERT INTO pg_temp._swingset_original_objects SELECT * FROM ops.objects")
    assert backup._check_object_clocks(conn) == {"checked": 2, "advanced": 0, "unknown": 1}
    conn.execute("UPDATE ops.objects SET stored_at='2026-11-06T00:00:00Z' WHERE digest='unknown'")
    with pytest.raises(RuntimeError, match="unknown state"):
        backup._check_object_clocks(conn)
    conn.execute("UPDATE ops.objects SET stored_at=NULL")
    with pytest.raises(RuntimeError, match="object GC clock"):
        backup._check_object_clocks(conn)


def test_manifest_candidate_pointer_advances_without_changing_manifest_history(
    fresh_database,  # noqa: F811
    tmp_path,
):
    from swingset.build.manifest import name_candidate

    _, _, conn = fresh_database
    schema.migrate(conn)
    conn.execute("SELECT set_config('search_path',%s,false)", (schema.SEARCH_PATH,))
    conn.execute(
        "INSERT INTO release_manifests(manifest_digest,bytes,created_at,candidate_id,verdict_json) VALUES('manifest-fixture',5,'2026-11-05T00:00:00Z','candidate-old','{}')"
    )
    target = tmp_path / "history"
    backup.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        backup.export(conn, target, files={}, runtime_identity="fixture")
    assert backup.audit(conn, target)["equal"]
    name_candidate(conn, "manifest-fixture", "candidate-new")
    assert (
        conn.execute("SELECT candidate_id FROM release_manifests").fetchone()[0] == "candidate-new"
    )
    with pytest.raises(RuntimeError, match="history differs: release_manifests"):
        backup.audit(conn, target)
    assert backup.audit(conn, target, after_rebuild=True)["equal"]
    for field, old, corrupt in (
        ("bytes", 5, 6),
        ("created_at", "2026-11-05T00:00:00Z", "2026-11-06T00:00:00Z"),
        ("verdict_json", "{}", '{"corrupt":true}'),
    ):
        conn.execute(f"UPDATE release_manifests SET {field}=%s", (corrupt,))
        with pytest.raises(RuntimeError, match="history differs: release_manifests"):
            backup.audit(conn, target, after_rebuild=True)
        conn.execute(f"UPDATE release_manifests SET {field}=%s", (old,))


def test_export_flushes_nested_directory_entries_before_manifest_and_parent_after_rename(
    fresh_database,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    _, _, conn = fresh_database
    schema.migrate(conn)
    backup.prepare(conn)
    source = tmp_path / "retained"
    source.write_bytes(b"retained history")
    target = tmp_path / "backup"
    synced = []
    manifest_checked = []
    fsync, durable_write = backup.os.fsync, backup.durable_write

    def sync(descriptor):
        value = os.fstat(descriptor)
        if stat.S_ISDIR(value.st_mode):
            synced.append((value.st_dev, value.st_ino))
        fsync(descriptor)

    def manifest(path, body):
        if path.name == backup.MANIFEST:
            directories = [path.parent, *(p for p in path.parent.rglob("*") if p.is_dir())]
            required = {(p.stat().st_dev, p.stat().st_ino) for p in directories}
            assert required <= set(synced)
            assert (path.parent / "files/history/nested/member").read_bytes() == source.read_bytes()
            manifest_checked.append(True)
        durable_write(path, body)

    monkeypatch.setattr(backup.os, "fsync", sync)
    monkeypatch.setattr(backup, "durable_write", manifest)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        receipt = backup.export(
            conn,
            target,
            files={"history/nested/member": source},
            runtime_identity="fixture",
        )
    parent = tmp_path.stat()
    assert manifest_checked == [True]
    assert synced[-1] == (parent.st_dev, parent.st_ino)
    assert backup.verify(target, expected_manifest_sha256=receipt["manifest_sha256"])["complete"]


def test_export_waits_for_every_data_flush_before_publishing_manifest(
    fresh_database,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    _, _, conn = fresh_database
    schema.migrate(conn)
    backup.prepare(conn)
    target = tmp_path / "blocked-flush"
    entered, release = threading.Event(), threading.Event()
    original = backup._FileFlushes._flush
    results, errors = [], []

    def slow_flush(descriptor):
        if not entered.is_set():
            entered.set()
            assert release.wait(10), "test did not release the blocked file flush"
        original(descriptor)

    def export():
        try:
            results.append(backup.export(conn, target, files={}, runtime_identity="fixture"))
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(backup._FileFlushes, "_flush", staticmethod(slow_flush))
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        thread = threading.Thread(target=export)
        thread.start()
        try:
            assert entered.wait(10)
            assert not target.exists()
            assert not list(tmp_path.glob(".blocked-flush.building-*/history.json"))
        finally:
            release.set()
            thread.join(10)
        assert not thread.is_alive()
    assert errors == []
    assert results[0]["complete"]
    assert backup.verify(target)["complete"]


def test_failed_data_flush_leaves_no_completed_export_and_can_retry(
    fresh_database,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    _, _, conn = fresh_database
    schema.migrate(conn)
    backup.prepare(conn)
    target = tmp_path / "failed-flush"
    original = backup.os.fsync

    def failed_flush(descriptor):
        if threading.current_thread().name.startswith("swingset-history-flush"):
            raise OSError("injected file flush failure")
        original(descriptor)

    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with monkeypatch.context() as patch:
            patch.setattr(backup.os, "fsync", failed_flush)
            with pytest.raises(OSError, match="injected file flush failure"):
                backup.export(conn, target, files={}, runtime_identity="fixture")
        assert not target.exists()
        assert not list(tmp_path.glob(".failed-flush.building-*"))
        assert backup.export(conn, target, files={}, runtime_identity="fixture")["complete"]
    assert backup.verify(target)["complete"]


def test_filtered_history_round_trip_and_rebuild_audit(fresh_database, tmp_path, monkeypatch):  # noqa: F811
    service, _, conn = fresh_database
    seed_history(conn)
    hold = tmp_path / "operator-hold"
    hold.write_text("fixture hold\n")
    seed = tmp_path / "seed.json"
    seed.write_text("{}\n")
    baseline = tmp_path / "phase1-baseline.json"
    baseline.write_text("{}\n")
    target = tmp_path / "backup"
    backup.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SELECT count(*) FROM ops.runs")
        receipt = backup.export(
            conn,
            target,
            files={"operator-hold": hold, "seed.json": seed, "phase1-baseline.json": baseline},
            runtime_identity="retained-fixture-image",
        )
    assert receipt["tables"]["derivation_generations"]["rows"] == 3
    assert receipt["tables"]["pending_work"]["rows"] == 0
    assert (target / "files/operator-hold").read_bytes() == hold.read_bytes()
    assert "seed.json" not in receipt["files"]
    assert not (target / "files/seed.json").exists()
    assert (target / "files/phase1-baseline.json").read_bytes() == baseline.read_bytes()
    assert backup.verify(target, expected_manifest_sha256=receipt["manifest_sha256"])["complete"]
    name = "sst_restore_" + uuid.uuid4().hex[:20]
    with service.connect() as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'").format(
                sql.Identifier(name)
            )
        )
    try:
        with service.connect(name) as restored:
            execute = type(restored).execute
            analyzed = []

            def native_analyze(connection, query, *args, **kwargs):
                if query == "ANALYZE":
                    analyzed.append(connection.info.transaction_status)
                    assert analyzed[-1] == TransactionStatus.IDLE
                return execute(connection, query, *args, **kwargs)

            monkeypatch.setattr(type(restored), "execute", native_analyze)
            result = backup.restore(
                target,
                restored,
                runtime_identity="retained-fixture-image",
                files_target=tmp_path / "restored",
            )
            assert analyzed == [TransactionStatus.IDLE]
            assert result["audit"]["equal"]
            assert result["database_id"] != receipt["source_environment"][1]
            assert (
                restored.execute("SELECT count(*) FROM derive.derivation_payloads").fetchone()[0]
                == 1
            )
            assert (
                restored.execute(
                    f"SELECT count(*) FROM {schema.qualified('pending_work')}"
                ).fetchone()[0]
                == 0
            )
            assert (tmp_path / "restored/operator-hold").read_bytes() == hold.read_bytes()
            restored.execute(
                "UPDATE derive.derivation_scopes SET materialized_signature='after-rebuild' WHERE unit_kind='event'"
            )
            restored.execute(
                "INSERT INTO derive.derivation_generations(generation_id,stage,unit_kind,unit_id,input_fingerprint,recipe_json,dependency_set_id,previous_generation_id,output_digest,row_count,created_at,run_id) VALUES('project-rebuilt','project','event','cache-event','rebuilt-input','{}','cache-deps','project-selected','fixture-output',1,'2026-11-04T00:00:01Z','history-run')"
            )
            restored.execute(
                "UPDATE derive.derivation_scopes SET materialized_generation_id='project-rebuilt' WHERE unit_kind='event'"
            )
            assert backup.audit(restored, target, after_rebuild=True)["equal"]
            with pytest.raises(RuntimeError, match="derivation_scopes"):
                backup.audit(restored, target)
            restored.execute(
                "UPDATE ops.runs SET started_at='changed-immutable-time' WHERE run_id='history-run'"
            )
            with pytest.raises(RuntimeError, match="runs"):
                backup.audit(restored, target, after_rebuild=True)
    finally:
        with service.connect() as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(name)))


def test_backup_rejects_unpinned_snapshot_and_corruption_before_restore(fresh_database, tmp_path):  # noqa: F811
    _, _, conn = fresh_database
    schema.migrate(conn)
    backup.prepare(conn)
    with pytest.raises(ValueError, match="pinned"):
        backup.export(conn, tmp_path / "not-pinned", files={}, runtime_identity="fixture")
    target = tmp_path / "backup"
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        backup.export(conn, target, files={}, runtime_identity="fixture")
    with pytest.raises(ValueError, match="runtime identity"):
        backup.verify(target, runtime_identity="another-image")
    table_stream = next((target / "tables").iterdir())
    table_stream.write_bytes(table_stream.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="digest"):
        backup.verify(target)


def test_offline_verify_rejects_omitted_generation_only_extract_even_with_rewritten_file_map(
    fresh_database,  # noqa: F811
    tmp_path,  # noqa: F811
):  # noqa: F811
    import hashlib
    import json

    from swingset.fetch.archive import canonical

    _, _, conn = fresh_database
    seed_history(conn)
    extract = tmp_path / "extract"
    extract.write_bytes(b'{"fixture":"generation-only"}')
    digest = hashlib.sha256(extract.read_bytes()).hexdigest()
    manifest = json.dumps([{"extract_sha256": digest, "body_sha256": None}], separators=(",", ":"))
    conn.execute(
        "INSERT INTO evidence.source_generations(generation_id,unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,run_id,state,removal_authority) VALUES('generation-file-owner','history-unit','round','1','input',%s,'{}','{}','{}','2026-11-04T00:00:00Z','history-run','accepted','none')",
        (manifest,),
    )
    backup.prepare(conn)
    target = tmp_path / "backup"
    name = "extracts/" + digest
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        backup.export(conn, target, files={name: extract}, runtime_identity="fixture")
    value = json.loads((target / backup.MANIFEST).read_text())
    del value["files"][name]
    (target / "files" / name).unlink()
    (target / backup.MANIFEST).write_bytes(canonical(value))
    with pytest.raises(ValueError, match="decoded COPY history requires"):
        backup.verify(target)


def test_restore_preserves_revoked_alternative_fingerprint_mapping(fresh_database, tmp_path):  # noqa: F811
    from swingset.admission.generations import revoked_fingerprint
    from swingset.state.db import open_database
    from swingset.state.engine import write_settings

    service, _, conn = fresh_database
    seed_history(conn)
    conn.execute(
        "UPDATE evidence.source_generations SET state='revoked' WHERE generation_id='source-previous'"
    )
    conn.execute(
        "INSERT INTO evidence.source_equivalences(unit_key,input_fingerprint,generation_id,recipe_json,work_token,created_at,run_id) VALUES('history-unit','revoked-alternative','source-previous','{}','original-pending-token','2026-11-04T00:00:00Z','history-run')"
    )
    conn.execute(
        "INSERT INTO evidence.source_generations(generation_id,unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,run_id,state,removal_authority) VALUES('unretained-source','history-unit','round','1','unused','[]','{}','{}','{}','2026-11-04T00:00:00Z','history-run','superseded','none')"
    )
    conn.execute(
        "INSERT INTO evidence.source_equivalences(unit_key,input_fingerprint,generation_id,recipe_json,created_at,run_id) VALUES('history-unit','unused-alternative','unretained-source','{}','2026-11-04T00:00:00Z','history-run')"
    )
    assert revoked_fingerprint(conn, "history-unit", "revoked-alternative")
    target = tmp_path / "backup"
    backup.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        receipt = backup.export(conn, target, files={}, runtime_identity="revocation-fixture")
    assert receipt["tables"]["source_equivalences"]["rows"] == 1
    name = "sst_revoked_restore_" + uuid.uuid4().hex[:12]
    with service.connect() as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'").format(
                sql.Identifier(name)
            )
        )
    try:
        with service.connect(name) as restored:
            assert backup.restore(target, restored, runtime_identity="revocation-fixture")["audit"][
                "equal"
            ]
        state = tmp_path / "restored"
        write_settings(state, service.dsn(name, role="worker"))
        with open_database(state) as database:
            assert revoked_fingerprint(database.connection, "history-unit", "revoked-alternative")
            assert not revoked_fingerprint(
                database.connection, "history-unit", "unknown-alternative"
            )
            assert tuple(
                database.connection.execute(
                    "SELECT input_fingerprint,work_token FROM source_equivalences"
                ).fetchone()
            ) == ("revoked-alternative", "original-pending-token")
    finally:
        with service.connect() as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(name)))
