import importlib.util
import json
import sqlite3
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from swingset.build.schema import SCHEMAS
from swingset.fetch.archive import Archive, canonical
from swingset.sources import get_page_kind

MODULE = importlib.util.spec_from_file_location(
    "v4_history_checks", Path(__file__).parents[1] / "journal/tools/releases/v4_history_checks.py"
)
assert MODULE is not None and MODULE.loader is not None
history = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(history)


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    conn = duckdb.connect()
    public_rows = {
        "events": [
            {
                "event_id": "e",
                "series_id": "series",
                "event_month": "2020-02",
                "year": 2020,
                "date_precision": "month",
                "source": "wsdc_registry",
                "snapshot_id": "registry",
            }
        ],
        "registry_placements": [
            {
                "series_id": "series",
                "event_month": date(2020, 2, 1),
                "event_id": "e",
                "source": "wsdc_registry",
                "snapshot_id": "registry",
            }
        ],
        "contests": [],
        "entries": [],
        "rounds": [],
        "snapshots": [
            {"snapshot_id": "registry", "via": "origin"},
            {"snapshot_id": "sheet", "via": "wayback"},
        ],
        "coverage": [
            {
                "year": 2020,
                "source": "wsdc_registry",
                "via": "origin",
                "events": 1,
                "contests": 0,
                "rounds": 0,
                "entries": 0,
                "events_registry_only": 1,
                "events_index_only": 0,
                "events_sheets_partial": 0,
                "events_sheets_complete": 0,
                "parsed_rounds": 0,
                "events_accepted": False,
                "unresolved_findings": 1,
            }
        ],
        "review_queue": [{"item_id": "finding", "kind": "phase1_incomplete", "subject_id": "2020"}],
    }
    for name, records in public_rows.items():
        conn.register("fixture_arrow", pa.Table.from_pylist(records, schema=SCHEMAS[name]))
        conn.execute(f"CREATE TABLE new_{name} AS SELECT * FROM fixture_arrow")
        conn.unregister("fixture_arrow")
    state = sqlite3.connect(":memory:")
    state.executescript("""
      CREATE TABLE history_acceptance(year);
      CREATE TABLE registry_placements(series_id,event_month);
      INSERT INTO registry_placements VALUES ('series','2020-02');
      CREATE TABLE snapshots(snapshot_id,watch_id,body_sha256,extract_version,parser_version,parse_status);
      CREATE TABLE watches(watch_id,parser);
      CREATE TABLE findings(finding_id,kind,subject_kind,subject_id,snapshot_id,closed_at);
      INSERT INTO findings VALUES ('finding','phase1_incomplete','history_year','2020',NULL,NULL);
    """)
    page = get_page_kind("wsdc_calendar.events")
    archive = Archive(tmp_path)
    body = archive.store_body(b"retained test body")
    extract = archive.store_extract([])
    state.execute("INSERT INTO watches VALUES ('watch',?)", (page.kind,))
    state.execute(
        "INSERT INTO snapshots VALUES ('snapshot','watch',?,?,?,'ok')",
        (body, str(page.EXTRACT_VERSION), str(page.PARSER_VERSION)),
    )
    package = {
        "snapshots": [{"snapshot_id": "snapshot", "body_sha256": body, "extract_sha256": extract}],
        "cdx_receipts": [],
        "catalog_inputs": [],
        "hosts": [],
        "catalog": {"targets": []},
        "ledger": {"targets": {}},
    }
    raw = canonical(package)
    (tmp_path / "phase1-export.json").write_bytes(raw)
    # Trust only this synthetic test package; production pins the reviewed receipt.
    monkeypatch.setattr(history, "PHASE1_SHA256", history.hashlib.sha256(raw).hexdigest())
    yield conn, state, tmp_path
    conn.close()
    state.close()


def test_partial_year_with_named_gap_passes_without_acceptance(corpus):
    conn, state, package = corpus
    report = history.audit_history(conn, state, phase1=package, expected_years=(2020,))
    assert all(report["checks"].values()), report
    assert report["details"]["registry_occurrences"] == 1
    assert state.execute("SELECT count(*) FROM history_acceptance").fetchone()[0] == 0


def test_public_date_month_is_normalized_but_wrong_month_still_fails(corpus):
    conn, state, package = corpus
    assert (
        conn.execute("SELECT typeof(event_month) FROM new_registry_placements").fetchone()[0]
        == "DATE"
    )
    conn.execute("UPDATE new_registry_placements SET event_month=DATE '2020-03-01'")
    report = history.audit_history(conn, state, phase1=package, expected_years=(2020,))
    assert not report["checks"]["history_registry_references_match_occurrences"]
    assert report["details"]["registry_event_reference_errors"] == 1


@pytest.mark.parametrize(
    ("mutation", "check"),
    [
        (
            "INSERT INTO new_events(event_id,series_id,event_month,year,date_precision,start_date,end_date,source,snapshot_id) SELECT 'duplicate',series_id,event_month,year,date_precision,start_date,end_date,source,snapshot_id FROM new_events",
            "history_each_registry_occurrence_has_one_event",
        ),
        (
            "UPDATE new_registry_placements SET event_id=NULL",
            "history_registry_references_match_occurrences",
        ),
        (
            "UPDATE new_events SET start_date='2020-02-01'",
            "history_dates_respect_printed_precision",
        ),
        ("DELETE FROM new_review_queue", "history_every_year_accepted_or_explained"),
        ("UPDATE new_coverage SET events_accepted=true", "history_no_unreviewed_year_acceptance"),
    ],
)
def test_candidate_errors_cannot_be_hidden_by_private_state(corpus, mutation, check):
    conn, state, package = corpus
    conn.execute(mutation)
    assert not history.audit_history(conn, state, phase1=package, expected_years=(2020,))["checks"][
        check
    ]


def test_archive_sheet_counts_cannot_be_attributed_to_registry_origin(corpus):
    conn, state, package = corpus
    conn.execute(
        "INSERT INTO new_contests(event_id,contest_id,source,snapshot_id) VALUES ('e','c','eepro','sheet')"
    )
    conn.execute(
        "INSERT INTO new_rounds(contest_id,round_id,source,snapshot_id) VALUES ('c','r','eepro','sheet')"
    )
    conn.execute("UPDATE new_coverage SET contests=1,rounds=1,parsed_rounds=1")
    report = history.audit_history(conn, state, phase1=package, expected_years=(2020,))
    assert not report["checks"]["history_coverage_matches_actual_source_transport"]
    assert any(
        row["source"] == "eepro" and row["via"] == "wayback"
        for row in report["details"]["coverage_accounting_errors"]
    )


def test_omitting_registry_rows_cannot_hide_missing_retained_occurrences(corpus):
    conn, state, package = corpus
    conn.execute("DELETE FROM new_registry_placements")
    conn.execute("DELETE FROM new_events")
    report = history.audit_history(conn, state, phase1=package, expected_years=(2020,))
    assert not report["checks"]["history_retained_occurrences_are_published_once"]
    assert report["details"]["retained_registry_occurrences"] == 1


def test_stale_interpretation_or_unexplained_failure_cannot_pass(corpus):
    conn, state, package = corpus
    state.execute("UPDATE snapshots SET parser_version='stale',parse_status='failed'")
    report = history.audit_history(conn, state, phase1=package, expected_years=(2020,))
    assert not report["checks"]["phase1_retained_inputs_current_or_explained"]
    assert len(report["details"]["phase1_interpretation_errors"]) == 2
    snapshot = json.loads((package / "phase1-export.json").read_bytes())["snapshots"][0]
    Archive(package).blob_path(snapshot["body_sha256"]).unlink()
    report = history.audit_history(conn, state, phase1=package, expected_years=(2020,))
    assert not report["checks"]["phase1_artifacts_are_closed"]
