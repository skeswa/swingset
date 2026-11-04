"""Read-only checkpoint rejection, exact COPY and resumable table boundaries."""

from __future__ import annotations

import hashlib
import sqlite3
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
):  # noqa: F811
    _, _, destination = fresh_database
    body = "雪\t\\N\n" * 100_000
    with sqlite3.connect(source_database) as conn:
        conn.execute("INSERT INTO meta(key,value) VALUES('large-fixture',?)", (body,))
    expected = sha(source_database)
    receipt = tmp_path / "import.json"
    calls = 0

    def stop():
        nonlocal calls
        calls += 1
        return calls > 2

    partial = importer.load(
        source_database, destination, expected_sha256=expected, receipt_path=receipt, stop=stop
    )
    assert not partial["complete"] and len(partial["loaded"]) == 2
    resumed = importer.load(
        source_database, destination, expected_sha256=expected, receipt_path=receipt
    )
    assert resumed["complete"] and len(resumed["loaded"]) == 141
    assert resumed["audit"]["equal"]
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
    assert importer.load(
        source_database, destination, expected_sha256=expected, receipt_path=receipt
    )["complete"]
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
