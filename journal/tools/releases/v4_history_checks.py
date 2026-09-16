"""Independent, read-only V2 checks for the combined V2/V4 candidate audit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swingset.fetch.archive import Archive
from swingset.history.catalog import Target
from swingset.sources import get_page_kind

if TYPE_CHECKING:
    import duckdb

PHASE1_SHA256 = "7b5954a5cca0bf46f29bc1176c3030787cf0b6fbc36a4849112a51e4822f5720"


def audit_history(
    conn: duckdb.DuckDBPyConnection,
    state: sqlite3.Connection,
    *,
    phase1: Path,
    expected_years: tuple[int, ...],
) -> dict[str, Any]:
    """Read new_TABLE views, retained SQLite state, and the pinned phase1 package."""
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    def rows(query: str) -> list[dict[str, Any]]:
        result = conn.execute(query)
        names = [column[0] for column in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]

    def count(query: str) -> int:
        row = conn.execute(query).fetchone()
        assert row is not None
        return int(row[0])

    occurrences = """SELECT DISTINCT series_id,substr(CAST(event_month AS VARCHAR),1,7) AS event_month
        FROM new_registry_placements WHERE CAST(event_month AS VARCHAR)>='2010-01'"""
    retained_occurrences = state.execute("""SELECT DISTINCT series_id,substr(event_month,1,7)
        FROM registry_placements WHERE event_month>='2010-01'""").fetchall()
    conn.execute(
        """CREATE OR REPLACE TEMP TABLE v2_retained_occurrences AS
        SELECT unnest(?::VARCHAR[]) AS series_id,unnest(?::VARCHAR[]) AS event_month""",
        (
            [str(row[0]) for row in retained_occurrences],
            [str(row[1]) for row in retained_occurrences],
        ),
    )
    details["retained_registry_occurrences"] = len(retained_occurrences)
    details["retained_occurrence_inventory_errors"] = rows("""
        SELECT r.series_id,r.event_month,count(e.event_id) AS event_count
        FROM v2_retained_occurrences r LEFT JOIN new_events e
        ON e.series_id=r.series_id AND e.event_month=r.event_month
        GROUP BY 1,2 HAVING count(e.event_id)<>1
    """)
    checks["history_retained_occurrences_are_published_once"] = not details[
        "retained_occurrence_inventory_errors"
    ]
    details["registry_occurrences"] = count(f"SELECT count(*) FROM ({occurrences})")
    details["occurrence_mapping_errors"] = rows(f"""
        SELECT r.series_id,r.event_month,count(e.event_id) AS event_count
        FROM ({occurrences}) r LEFT JOIN new_events e
        ON e.series_id=r.series_id AND e.event_month=r.event_month
        GROUP BY 1,2 HAVING count(e.event_id)<>1
    """)
    checks["history_each_registry_occurrence_has_one_event"] = not details[
        "occurrence_mapping_errors"
    ]
    details["registry_event_reference_errors"] = count("""
        SELECT count(*) FROM new_registry_placements r LEFT JOIN new_events e USING(event_id)
        WHERE CAST(r.event_month AS VARCHAR)>='2010-01' AND
        (e.event_id IS NULL OR e.series_id IS DISTINCT FROM r.series_id
         OR e.event_month IS DISTINCT FROM substr(CAST(r.event_month AS VARCHAR),1,7))
    """)
    checks["history_registry_references_match_occurrences"] = (
        details["registry_event_reference_errors"] == 0
    )
    details["date_precision_errors"] = rows("""
        SELECT event_id,date_precision,start_date,end_date,event_month FROM new_events
        WHERE date_precision IS NULL OR date_precision NOT IN ('day','month')
        OR NOT regexp_full_match(coalesce(event_month,''),'[0-9]{4}-(0[1-9]|1[0-2])')
        OR (date_precision='month' AND (start_date IS NOT NULL OR end_date IS NOT NULL))
        OR (date_precision='day' AND
            (try_cast(start_date AS DATE) IS NULL OR try_cast(end_date AS DATE) IS NULL
             OR try_cast(start_date AS DATE)>try_cast(end_date AS DATE)))
    """)
    checks["history_dates_respect_printed_precision"] = not details["date_precision_errors"]
    coverage = rows("""SELECT year,count(*) AS groups,bool_and(events_accepted) AS accepted,
        min(unresolved_findings) AS unresolved FROM new_coverage GROUP BY year ORDER BY year""")
    by_year = {int(row["year"]): row for row in coverage}
    public_years = {
        str(row[0])
        for row in conn.execute("""SELECT subject_id FROM new_review_queue
            WHERE kind='phase1_incomplete'""").fetchall()
    }
    details["unsupported_public_year_findings"] = [
        str(item)
        for item, year in conn.execute("""SELECT item_id,subject_id FROM new_review_queue
            WHERE kind='phase1_incomplete'""").fetchall()
        if not state.execute(
            """SELECT 1 FROM findings WHERE finding_id=?
            AND kind='phase1_incomplete' AND subject_kind='history_year' AND subject_id=?
            AND closed_at IS NULL""",
            (item, year),
        ).fetchone()
    ]
    checks["history_public_year_findings_have_open_support"] = not details[
        "unsupported_public_year_findings"
    ]
    details["year_coverage"] = coverage
    details["years_without_coverage"] = [year for year in expected_years if year not in by_year]
    details["years_without_acceptance_or_named_findings"] = [
        year
        for year in expected_years
        if year not in by_year or (not by_year[year]["accepted"] and str(year) not in public_years)
    ]
    checks["history_every_year_has_coverage"] = not details["years_without_coverage"]
    checks["history_every_year_accepted_or_explained"] = not details[
        "years_without_acceptance_or_named_findings"
    ]
    checks["history_no_unreviewed_year_acceptance"] = (
        state.execute("SELECT count(*) FROM history_acceptance").fetchone()[0] == 0
        and count("SELECT count(*) FROM new_coverage WHERE events_accepted") == 0
    )
    details["coverage_accounting_errors"] = rows(_COVERAGE_SQL)
    checks["history_coverage_matches_actual_source_transport"] = not details[
        "coverage_accounting_errors"
    ]
    checks["history_coverage_tiers_partition_events"] = (
        count("""
        SELECT count(*) FROM new_coverage WHERE events IS DISTINCT FROM
        (events_registry_only+events_index_only+events_sheets_partial+events_sheets_complete)
        OR rounds IS DISTINCT FROM parsed_rounds
    """)
        == 0
    )
    package = _package_checks(state, phase1)
    checks.update(package["checks"])
    details.update(package["details"])
    return {"checks": checks, "details": details}


def _package_checks(state: sqlite3.Connection, phase1: Path) -> dict[str, Any]:
    raw = (phase1 / "phase1-export.json").read_bytes()
    package = json.loads(raw)
    checks = {
        "phase1_package_matches_reviewed_digest": hashlib.sha256(raw).hexdigest() == PHASE1_SHA256
    }
    details: dict[str, Any] = {"phase1_manifest_sha256": hashlib.sha256(raw).hexdigest()}
    bodies = {row["body_sha256"] for row in package["snapshots"] if row["body_sha256"]}
    bodies.update(row["body_sha256"] for row in package["cdx_receipts"])
    bodies.update(row["body_sha256"] for row in package["catalog_inputs"])
    bodies.update(row["robots_sha256"] for row in package["hosts"] if row["robots_sha256"])
    extracts = {row["extract_sha256"] for row in package["snapshots"] if row["extract_sha256"]}
    archive = Archive(phase1)
    errors = []
    for kind, digests, read in (
        ("body", bodies, archive.read_body),
        ("extract", extracts, archive.read_extract),
    ):
        for digest in sorted(digests):
            try:
                read(digest)
            except (OSError, ValueError, EOFError) as exc:
                errors.append({"kind": kind, "digest": digest, "error": type(exc).__name__})
    details["phase1_artifact_errors"] = errors
    details["phase1_artifacts"] = {"bodies": len(bodies), "extracts": len(extracts)}
    checks["phase1_artifacts_are_closed"] = not errors
    interpretation_errors = []
    for snapshot in package["snapshots"]:
        identifier = snapshot["snapshot_id"]
        actual = state.execute(
            """SELECT s.body_sha256,s.extract_version,s.parser_version,s.parse_status,w.parser
            FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?""",
            (identifier,),
        ).fetchone()
        if actual is None:
            interpretation_errors.append({"snapshot_id": identifier, "reason": "missing snapshot"})
            continue
        page = get_page_kind(actual[4])
        if (
            actual[0] != snapshot["body_sha256"]
            or str(actual[1]) != str(page.EXTRACT_VERSION)
            or str(actual[2]) != str(page.PARSER_VERSION)
        ):
            interpretation_errors.append(
                {"snapshot_id": identifier, "reason": "changed body or stale interpretation"}
            )
        if (
            actual[3] != "ok"
            and not state.execute(
                """SELECT 1 FROM findings
                WHERE snapshot_id=? AND closed_at IS NULL AND kind IN ('parse_failure','admission_blocked') LIMIT 1""",
                (identifier,),
            ).fetchone()
        ):
            interpretation_errors.append(
                {
                    "snapshot_id": identifier,
                    "reason": "unsuccessful interpretation has no open finding",
                }
            )
    details["phase1_interpretation_errors"] = interpretation_errors
    checks["phase1_retained_inputs_current_or_explained"] = not interpretation_errors
    statuses: dict[str, int] = {}
    for target in package["catalog"]["targets"]:
        identifier = Target(**target).target_id
        status = package["ledger"]["targets"].get(identifier, {}).get("status", "pending")
        statuses[status] = statuses.get(status, 0) + 1
    details["phase1_catalog_statuses"] = statuses
    return {"checks": checks, "details": details}


_COVERAGE_SQL = """
WITH support AS (
 SELECT e.year,e.event_id,e.source,e.snapshot_id,'events' AS kind,e.event_id AS row_id FROM new_events e
 UNION ALL SELECT e.year,e.event_id,r.source,r.snapshot_id,'events',e.event_id FROM new_registry_placements r JOIN new_events e USING(event_id)
 UNION ALL SELECT e.year,c.event_id,c.source,c.snapshot_id,'contests',c.contest_id FROM new_contests c JOIN new_events e USING(event_id)
 UNION ALL SELECT e.year,n.event_id,n.source,n.snapshot_id,'entries',n.entry_id FROM new_entries n JOIN new_events e USING(event_id)
 UNION ALL SELECT e.year,c.event_id,r.source,r.snapshot_id,'rounds',r.round_id FROM new_rounds r JOIN new_contests c USING(contest_id) JOIN new_events e ON e.event_id=c.event_id
), actual AS (
 SELECT p.year,p.source,coalesce(s.via,CASE WHEN p.snapshot_id='override' THEN 'manual' ELSE 'origin' END) AS via,
 count(DISTINCT p.event_id) AS events,
 count(DISTINCT CASE WHEN p.kind='contests' THEN p.row_id END) AS contests,
 count(DISTINCT CASE WHEN p.kind='rounds' THEN p.row_id END) AS rounds,
 count(DISTINCT CASE WHEN p.kind='entries' THEN p.row_id END) AS entries
 FROM support p LEFT JOIN new_snapshots s USING(snapshot_id) GROUP BY 1,2,3
)
SELECT coalesce(a.year,c.year) AS year,coalesce(a.source,c.source) AS source,coalesce(a.via,c.via) AS via,
 a.events AS actual_events,c.events AS coverage_events,a.contests AS actual_contests,c.contests AS coverage_contests,
 a.rounds AS actual_rounds,c.rounds AS coverage_rounds,a.entries AS actual_entries,c.entries AS coverage_entries
FROM actual a FULL OUTER JOIN new_coverage c USING(year,source,via)
WHERE a.events IS DISTINCT FROM c.events OR a.contests IS DISTINCT FROM c.contests
 OR a.rounds IS DISTINCT FROM c.rounds OR a.entries IS DISTINCT FROM c.entries
"""
