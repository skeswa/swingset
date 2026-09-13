"""Read retained capture outcomes without confusing a green parse with complete evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from swingset.admission.generations import deserialize_result
from swingset.admission.support import interpretation_support
from swingset.fetch.wayback import Capture
from swingset.history.platform import PlannedPage
from swingset.sources.records import RoundSheet
from swingset.state.attempts import eligible, latest_attempt
from swingset.state.work import WorkUnit
from swingset.state.work_fingerprints import input_fingerprint


@dataclass(frozen=True)
class CaptureState:
    state: Literal["unfetched", "waiting", "incomplete", "complete", "retryable"]
    reason: str
    snapshot_id: str | None = None


def capture_state(
    conn: sqlite3.Connection, page: PlannedPage, capture: Capture, *, now: datetime | None = None
) -> CaptureState:
    snapshots = conn.execute(
        "SELECT s.* FROM snapshots s JOIN watches w USING(watch_id) WHERE w.source=? AND w.parser=? AND s.url=? AND (s.archive_url=? OR s.requested_archive_url=?) ORDER BY s.fetched_at DESC,s.snapshot_id DESC",
        (
            page.page.source,
            page.page.page_kind,
            page.page.url,
            capture.archive_url,
            capture.archive_url,
        ),
    ).fetchall()
    accepted_http = next(
        (s for s in snapshots if s["http_status"] == 200 and s["classification"] == "Ok"), None
    )
    if accepted_http is None:
        return CaptureState("retryable" if snapshots else "unfetched", "capture_not_usable")
    return snapshot_state(conn, page, accepted_http, now=now)


def snapshot_state(
    conn: sqlite3.Connection,
    page: PlannedPage,
    snapshot: sqlite3.Row,
    *,
    now: datetime | None = None,
) -> CaptureState:
    """Apply the same complete-interpretation rules to archived and origin evidence."""
    identifier = str(snapshot["snapshot_id"])
    unit = WorkUnit("parse", "snapshot", identifier)
    if conn.execute(
        "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_kind='snapshot' AND unit_id=?",
        (identifier,),
    ).fetchone():
        attempt = latest_attempt(conn, unit)
        if (attempt is not None and attempt["outcome"] == "running") or eligible(
            conn, unit, input_fingerprint(conn, unit), now or datetime.now(UTC)
        ):
            return CaptureState("waiting", "interpretation_pending", identifier)
    if snapshot["extract_status"] == "failed" or snapshot["parse_status"] == "failed":
        return CaptureState("incomplete", "extraction_or_parse_failed", identifier)
    if snapshot["parse_status"] not in {"ok", "empty"}:
        generation = conn.execute(
            "SELECT g.state,g.report_json FROM source_units u JOIN source_generations g USING(unit_key) JOIN admission_policies p ON p.page_kind=g.page_kind AND p.contract_version=g.contract_version WHERE u.watch_id=? AND json_extract(g.recipe_json,'$.context.snapshot_id')=? ORDER BY g.created_at DESC,g.generation_id DESC LIMIT 1",
            (snapshot["watch_id"], identifier),
        ).fetchone()
        # H7 preserves the transport's pending status when it declines promotion.
        # A retained source guard failure is complete diagnostic evidence, not an
        # unfinished parse. Local missing-artifact recovery still remains pending.
        if generation and generation[0] in {"needs_review", "waiting_for_inputs"}:
            failures = set(json.loads(generation[1]).get("failures", ()))
            if failures - {"manifest_artifact_missing"}:
                return CaptureState("incomplete", "admission_guard_incomplete", identifier)
        return CaptureState("waiting", "interpretation_pending", identifier)
    support = interpretation_support(
        conn,
        identifier,
        parser_version=str(snapshot["parser_version"]),
        extract_version=str(snapshot["extract_version"]),
    )
    if not support["usable"]:
        return CaptureState("incomplete", "interpretation_not_admitted", identifier)
    generation = conn.execute(
        "SELECT g.result_json,g.report_json FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id WHERE u.watch_id=? AND json_extract(g.recipe_json,'$.context.snapshot_id')=?",
        (snapshot["watch_id"], identifier),
    ).fetchone()
    if generation is None:
        return CaptureState("incomplete", "selected_generation_missing", identifier)
    result = deserialize_result(str(generation[0]))
    report = json.loads(generation[1])
    if report.get("failures"):
        return CaptureState("incomplete", "admission_guard_incomplete", identifier)
    if not result.observations or result.legitimate_empty:
        return CaptureState("incomplete", "historical_results_empty", identifier)
    for observation in result.observations:
        payload = observation.payload
        if isinstance(payload, RoundSheet) and (
            not payload.tables or any(not t.rows for t in payload.tables)
        ):
            return CaptureState("incomplete", "round_table_empty", identifier)
    if conn.execute(
        "SELECT 1 FROM contests WHERE source=? AND snapshot_id=? AND parse_status='unsupported' LIMIT 1",
        (page.page.source, identifier),
    ).fetchone():
        return CaptureState("incomplete", "scoring_layout_unsupported", identifier)
    for child in result.watches:
        if (
            child.kind == "round"
            and not conn.execute(
                "SELECT 1 FROM archive_captures WHERE source=? AND url=? AND status=200 LIMIT 1",
                (child.source, child.url),
            ).fetchone()
        ):
            return CaptureState("incomplete", "advertised_child_missing", identifier)
    return CaptureState("complete", "admitted_complete_capture", identifier)


def next_capture(
    conn: sqlite3.Connection, page: PlannedPage, *, now: datetime | None = None
) -> tuple[Capture | None, str]:
    """At most the preferred three distinct bodies; retained success is never fetched again."""
    outcomes = [(capture, capture_state(conn, page, capture, now=now)) for capture in page.captures]
    if any(result.state == "complete" for _, result in outcomes):
        return None, "complete"
    for capture, result in outcomes:
        if result.state == "waiting":
            return None, "waiting"
        if result.state in {"unfetched", "retryable"}:
            return capture, result.reason
    return None, "capture_gap_exhausted"
