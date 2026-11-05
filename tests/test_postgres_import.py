"""Read-only checkpoint rejection, exact COPY and resumable table boundaries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_postgres_schema import fresh_database  # noqa: F401

from swingset.state import postgres_import as importer
from swingset.state import postgres_schema as schema


@pytest.fixture
def source_database(tmp_path):
    path = tmp_path / "source" / "state.sqlite"
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.executescript(Path("tests/fixtures/postgres/schema37.sql").read_text())
        conn.execute("PRAGMA user_version=37")
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inspection_cli_reads_finalized_partial_import_and_refuses_changed_counts(
    source_database,
    fresh_database,  # noqa: F811
    tmp_path,
    monkeypatch,
    capsys,
):
    from swingset.cli import main
    from swingset.state import engine

    with sqlite3.connect(source_database) as source:
        source.execute("INSERT INTO meta(key,value) VALUES('inspection-proof','original')")
    service, name, owner = fresh_database
    result = importer.load(source_database, owner, expected_sha256=sha(source_database))
    result.update(complete=False, audit=None)
    receipt = tmp_path / "stopped-import.json"
    receipt.write_text(json.dumps(result))
    original = receipt.read_bytes()
    oid = owner.execute("SELECT oid FROM pg_database WHERE datname=current_database()").fetchone()[
        0
    ]
    stored_id = owner.execute("SELECT database_id FROM ops.environment_identity").fetchone()[0]
    owner.close()
    monkeypatch.setenv("SWINGSET_OWNER_DATABASE_URL", service.dsn(name))
    arguments = [
        "inspect-import",
        str(receipt),
        "--receipt-sha256",
        sha(receipt),
        "--source-sha256",
        sha(source_database),
        "--database",
        name,
        "--database-oid",
        str(oid),
        "--environment",
        "dev",
        "--database-id",
        stored_id,
    ]
    connections = []
    original_connect = engine.connect

    def observe_connection(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        assert kwargs["read_only"] is True
        with pytest.raises(engine.Error):
            connection.execute("CREATE TABLE public.inspection_must_not_write(value int)")
        connections.append(connection)
        return connection

    monkeypatch.setattr(engine, "connect", observe_connection)
    assert main(arguments) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["complete"] and not inspected["native_complete"]
    assert not inspected["typed_values_checked"]
    assert inspected["database_id"] == stored_id and inspected["schema"]["equal"]
    assert inspected["counts"] == {key: value["rows"] for key, value in result["tables"].items()}
    assert inspected["persistent_database_writes"] == inspected["other_client_sessions"] == 0
    assert len(connections) == 1 and connections[0].closed
    assert receipt.read_bytes() == original
    with service.connect(name) as writer:
        writer.execute("DELETE FROM ops.meta WHERE key='inspection-proof'")
    assert main(arguments) == 1
    assert "ImportRejected" in capsys.readouterr().err
    assert receipt.read_bytes() == original


@pytest.mark.parametrize("fault", ["receipt-digest", "source", "migration", "missing", "row-count"])
def test_inspection_rejects_unbound_or_partial_receipt_before_target_queries(
    source_database, tmp_path, fault
):
    value = importer.preflight(source_database, expected_sha256=sha(source_database))
    value["loaded"] = json.loads(json.dumps(value["tables"]))
    value["migration_checksum"] = schema.migration_checksum()
    if fault == "source":
        value["source_sha256"] = "0" * 64
    elif fault == "migration":
        value["migration_checksum"] = "0" * 64
    elif fault == "missing":
        value["loaded"].pop("meta")
    elif fault == "row-count":
        value["loaded"]["meta"]["rows"] = value["tables"]["meta"]["rows"] = -1
    receipt = tmp_path / "import.json"
    receipt.write_text(json.dumps(value))

    class NoTargetQueries:
        def execute(self, *args, **kwargs):
            pytest.fail("a rejected receipt must not query the destination")

    with pytest.raises(importer.ImportRejected):
        importer.inspect_loaded_target(
            receipt,
            NoTargetQueries(),
            receipt_sha256="0" * 64 if fault == "receipt-digest" else sha(receipt),
            source_sha256=sha(source_database),
            database="unused",
            database_oid=1,
            environment="dev",
        )


@pytest.mark.parametrize(
    "fault", ["database-oid", "database-id", "active-client", "hidden-client", "writer-transaction"]
)
def test_inspection_refuses_wrong_target_or_another_client(
    source_database,
    fresh_database,  # noqa: F811
    tmp_path,
    fault,
):
    from swingset.state import engine

    service, name, owner = fresh_database
    result = importer.load(source_database, owner, expected_sha256=sha(source_database))
    receipt = tmp_path / "import.json"
    receipt.write_text(json.dumps(result))
    oid = owner.execute("SELECT oid FROM pg_database WHERE datname=current_database()").fetchone()[
        0
    ]
    if fault not in {"active-client", "hidden-client"}:
        owner.close()
    with engine.connect(service.dsn(name), read_only=True) as reader:
        if fault == "hidden-client":
            reader.execute("SET ROLE swingset_owner")
            assert (
                reader.execute(
                    "SELECT backend_type FROM pg_stat_activity WHERE pid=%s",
                    (owner.info.backend_pid,),
                ).fetchone()[0]
                is None
            )
        if fault == "writer-transaction":
            reader.execute("BEGIN READ WRITE")
            assert reader.execute("SHOW transaction_read_only").fetchone()[0] == "off"
        with pytest.raises(importer.ImportRejected):
            importer.inspect_loaded_target(
                receipt,
                reader,
                receipt_sha256=sha(receipt),
                source_sha256=sha(source_database),
                database=name,
                database_oid=oid + 1 if fault == "database-oid" else oid,
                environment="dev",
                database_id="00000000-0000-0000-0000-000000000000"
                if fault == "database-id"
                else None,
            )
    assert receipt.read_text() == json.dumps(result)


def test_cli_opens_destination_after_preflight_and_closes_it_after_audit(
    source_database,
    fresh_database,  # noqa: F811
    tmp_path,
    monkeypatch,
    capsys,
):
    from swingset.backup import destination
    from swingset.cli import main

    service, name, observer = fresh_database
    monkeypatch.setenv("SWINGSET_OWNER_DATABASE_URL", service.dsn(name))
    opened = []
    validations = 0
    original_preflight, original_owner = importer.preflight, destination.owner

    def expire_idle_sessions(*args, **kwargs):
        nonlocal validations
        validations += 1
        # A full-size source can outlive the relay's idle timeout. Any owner
        # session opened before validation finishes is unusable afterwards.
        for connection in opened:
            connection.close()
        return original_preflight(*args, **kwargs)

    @contextmanager
    def observe_owner(state):
        with original_owner(state) as connection:
            opened.append(connection)
            yield connection

    monkeypatch.setattr(importer, "preflight", expire_idle_sessions)
    monkeypatch.setattr(destination, "owner", observe_owner)
    receipt = tmp_path / "import.json"
    assert (
        main(
            [
                "import-sqlite37",
                str(source_database),
                "--state",
                str(tmp_path / "state"),
                "--sha256",
                sha(source_database),
                "--receipt",
                str(receipt),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] and result["audit"]["equal"]
    assert len(result["audit"]["tables"]) == 141
    assert validations == 1 and len(opened) == 1 and opened[0].closed
    assert schema.validate_schema(observer, full=True)["equal"]
    progress = json.loads(receipt.with_name("import-preflight.json").read_text())
    assert progress["phase"] == "validated" and progress["checked_tables"] == 141


def test_preflight_progress_distinguishes_source_validation_from_loading(source_database):
    observed = []
    result = importer.preflight(
        source_database, expected_sha256=sha(source_database), progress=observed.append
    )
    assert result["complete"]
    assert [row["phase"] for row in observed[:4]] == [
        "source_digest",
        "integrity",
        "foreign_keys",
        "files",
    ]
    checks = [row for row in observed if row["phase"] == "values"]
    assert {row["table"] for row in checks} == set(result["tables"])
    assert len(checks) == 141 and observed[-1]["phase"] == "validated"
    assert all("loaded" not in row and "complete" not in row for row in observed)


def test_managed_import_rejects_source_before_opening_destination(source_database):
    @contextmanager
    def forbidden():
        pytest.fail("invalid source must not open a destination")
        yield  # pragma: no cover

    with pytest.raises(importer.ImportRejected, match="digest differs"):
        importer.load_into(source_database, forbidden, expected_sha256="0" * 64)


@pytest.mark.parametrize(
    "statement,values,reason",
    [
        ("INSERT INTO meta(key,value) VALUES('nul',?)", ("bad\0text",), "NUL"),
        ("INSERT INTO meta(key,value) VALUES(NULL,'value')", (), "NULL key"),
        (
            "INSERT INTO host_budget(host,day,requests,bytes) VALUES('fixture.test','2026-11-04',?,0)",
            (1.5,),
            "storage class",
        ),
        (
            "INSERT INTO runs(run_id,started_at,dry_run,summary_json) VALUES('fixture-run','2026-11-04T00:00:00Z',0,?)",
            ("{",),
            "JSON",
        ),
        ("INSERT INTO meta(key,value) VALUES('bad-utf8',CAST(x'80' AS TEXT))", (), "decoded"),
    ],
)
def test_preflight_rejects_incompatible_values_before_destination_load(
    source_database, statement, values, reason
):
    with sqlite3.connect(source_database) as conn:
        conn.execute(statement, values)
    with pytest.raises(importer.ImportRejected, match=reason):
        importer.preflight(source_database, expected_sha256=sha(source_database))


def test_preflight_requires_source_digest_and_exact_schema_fingerprint(source_database):
    with pytest.raises(importer.ImportRejected, match="manifest or explicit"):
        importer.preflight(source_database)
    with sqlite3.connect(source_database) as conn:
        conn.execute("DROP TRIGGER admission_review_immutable")
    with pytest.raises(importer.ImportRejected, match="fingerprint"):
        importer.preflight(source_database, expected_sha256=sha(source_database))


def test_preflight_checks_referenced_raw_files_but_ignores_unused_object_index_rows(
    source_database,
):
    with sqlite3.connect(source_database) as conn:
        conn.execute("INSERT INTO hosts(host,robots_sha256) VALUES('fixture.test',?)", ("a" * 64,))
    with pytest.raises(ValueError, match="referenced"):
        importer.preflight(source_database, expected_sha256=sha(source_database))
    from swingset.fetch.archive import Archive

    archive = Archive(source_database.parent)
    body = archive.store_body(b"User-agent: *\nDisallow: /private\n")
    with sqlite3.connect(source_database) as conn:
        conn.execute("UPDATE hosts SET robots_sha256=? WHERE host='fixture.test'", (body,))
        conn.execute(
            "INSERT INTO objects(class,digest,raw_bytes,stored_bytes,stored_at) VALUES('derivation_output',?,1,1,'2026-11-04T00:00:00Z')",
            ("b" * 64,),
        )
    checked = importer.preflight(source_database, expected_sha256=sha(source_database))
    assert checked["files"]["files"] == 1
    assert checked["complete"]


def test_preflight_requires_evicted_predecessor_file_of_selected_generation(source_database):
    from swingset.state.legacy_checkpoint import verify_sqlite_files
    from swingset.state.objects import Store

    root = source_database.parent
    store = Store(root)
    digest, stored = store.put_bytes("derivation_output", b"")
    with sqlite3.connect(source_database) as conn:
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES('chain-run','2026-11-04T00:00:00Z',0)"
        )
        conn.execute(
            "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES('chain-deps','[]')"
        )
        conn.execute(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES('project','dancer','1','2026-11-04T00:00:00Z')"
        )
        for generation, previous in [("chain-old", None), ("chain-selected", "chain-old")]:
            conn.execute(
                "INSERT INTO derivation_generations(generation_id,stage,unit_kind,unit_id,input_fingerprint,recipe_json,dependency_set_id,previous_generation_id,output_digest,row_count,created_at,run_id) VALUES(?,'project','dancer','1',?,'{}','chain-deps',?,?,0,'2026-11-04T00:00:00Z','chain-run')",
                (generation, generation, previous, digest),
            )
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id='chain-selected' WHERE stage='project' AND unit_kind='dancer' AND unit_id='1'"
        )
        conn.execute(
            "INSERT INTO derivation_evicted(generation_id,evicted_at) VALUES('chain-old','2026-11-04T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO objects(class,digest,raw_bytes,stored_bytes,stored_at) VALUES('derivation_output',?,0,?,'2026-11-04T00:00:00Z')",
            (digest, stored),
        )
        result = verify_sqlite_files(root, conn, include_paths=True)
    expected = store.path("derivation_output", digest)
    assert expected.relative_to(root).as_posix() in result["paths"]
    assert importer.preflight(source_database, expected_sha256=sha(source_database))["complete"]
    expected.unlink()
    with pytest.raises(ValueError, match="packed object is absent or corrupt"):
        importer.preflight(source_database, expected_sha256=sha(source_database))


def test_exact_large_unicode_copy_independent_audit_and_idempotent_resume(
    source_database,
    fresh_database,  # noqa: F811
    tmp_path,  # noqa: F811
    monkeypatch,
):  # noqa: F811
    _, _, destination = fresh_database
    # Snow crosses both the old 64 KiB and new 1 MiB byte boundaries.
    body = "x" * (1024**2 - 1) + "雪\t\\N\n" * 100_000
    monkeypatch.setattr(importer, "CHUNK", 64 * 1024)
    with sqlite3.connect(source_database) as conn:
        conn.execute("INSERT INTO meta(key,value) VALUES('large-fixture',?)", (body,))
    expected = sha(source_database)
    receipt = tmp_path / "import.json"
    calls = 0
    loaded_before_stop = sorted(schema.reference_manifest()["tables"]).index("meta") + 1

    def stop():
        nonlocal calls
        calls += 1
        return calls > loaded_before_stop

    partial = importer.load(
        source_database, destination, expected_sha256=expected, receipt_path=receipt, stop=stop
    )
    assert not partial["complete"] and len(partial["loaded"]) == loaded_before_stop
    assert "meta" in partial["loaded"]
    monkeypatch.setattr(importer, "CHUNK", 1024**2)
    resumed = importer.load(
        source_database, destination, expected_sha256=expected, receipt_path=receipt
    )
    assert resumed["complete"] and len(resumed["loaded"]) == 141
    assert resumed["audit"]["equal"]
    assert resumed["tables"] == partial["tables"]
    assert (
        destination.execute("SELECT value FROM ops.meta WHERE key='large-fixture'").fetchone()[0]
        == body
    )
    assert (
        destination.execute(
            "SELECT count(*) FROM ops.meta WHERE key='postgres_migration_checksum'"
        ).fetchone()[0]
        == 0
    )
    monkeypatch.setattr(importer, "CHUNK", 4 * 1024**2)
    repeated = importer.load(
        source_database, destination, expected_sha256=expected, receipt_path=receipt
    )
    assert repeated["complete"] and repeated["tables"] == partial["tables"]
    assert schema.validate_schema(destination, full=True)["equal"]


def test_audit_detects_value_drift_even_when_row_counts_match(
    source_database,
    fresh_database,  # noqa: F811
    tmp_path,  # noqa: F811
):  # noqa: F811
    _, _, destination = fresh_database
    expected = sha(source_database)
    importer.load(
        source_database,
        destination,
        expected_sha256=expected,
        receipt_path=tmp_path / "import.json",
    )
    destination.execute("UPDATE ops.meta SET value='changed' WHERE key='installed_at'")
    with pytest.raises(importer.ImportRejected, match="typed value digest"):
        importer.audit(source_database, destination, expected_sha256=expected)


def test_old_sqlite_schema_is_rejected_before_any_postgres_mutation(
    source_database,
    fresh_database,  # noqa: F811
):  # noqa: F811
    _, _, destination = fresh_database
    with sqlite3.connect(source_database) as source:
        source.execute("PRAGMA user_version=11")
        source.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
    with pytest.raises(importer.ImportRejected, match="user_version"):
        importer.load(source_database, destination, expected_sha256=sha(source_database))
    assert destination.execute("SELECT to_regclass('ops.meta')").fetchone() == (None,)
