"""An exact source-kind admission gate, separate from human year acceptance."""

from __future__ import annotations

import sqlite3

from swingset.admission.contracts import contract_version
from swingset.project.history import phase_two_allowed
from swingset.sources import get_page_kind

PHASE1_KINDS = frozenset(
    {
        "swingdancecouncil.events",
        "wsdc_calendar.events",
        "wsdc_newsletter.events",
        "wsdc_newsletter.index",
    }
)


def is_phase_one_index(source: str, page_kind: str, watch_kind: str) -> bool:
    """An index label alone never grants the event-list acquisition exception."""
    return watch_kind == "index" and page_kind in PHASE1_KINDS and page_kind.split(".")[0] == source


def archive_watch_gate(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_ref: str | None,
    page_kind: str,
    watch_kind: str,
    archive_url: str | None,
) -> str | None:
    """Only the four catalog event-list kinds are exempt from phase-two acceptance."""
    if not archive_url:
        return None
    if is_phase_one_index(source, page_kind, watch_kind):
        return None
    return phase_two_gate(conn, source=source, source_ref=source_ref, page_kind=page_kind)


def phase_two_gate(
    conn: sqlite3.Connection, *, source: str, source_ref: str | None, page_kind: str
) -> str | None:
    """Return a stable stop reason. Call within the same transaction as watch insertion."""
    if page_kind.split(".")[0] != source:
        return "history_page_kind_source_mismatch"
    event = conn.execute(
        "SELECT e.year FROM source_event_map m JOIN events e USING(event_id) WHERE m.source=? AND m.source_ref=?",
        (source, source_ref),
    ).fetchone()
    if event is None:
        return "history_event_unmapped"
    if not phase_two_allowed(conn, int(event[0])):
        return "history_year_unaccepted"
    try:
        get_page_kind(page_kind)
    except KeyError:
        return "history_page_kind_unavailable"
    version = contract_version(page_kind)
    if version == "unassessed":
        return "history_contract_unassessed"
    policy = conn.execute(
        "SELECT p.mode,p.contract_version,p.reviewed_report_digest,r.contract_version,r.page_kind FROM admission_policies p LEFT JOIN admission_reviews r ON r.report_digest=p.reviewed_report_digest WHERE p.page_kind=?",
        (page_kind,),
    ).fetchone()
    if policy is None or policy[0] != "enforce":
        return "history_contract_not_enforced"
    if policy[1] != version or policy[3] != version or policy[4] != page_kind or not policy[2]:
        return "history_contract_review_stale"
    return None
