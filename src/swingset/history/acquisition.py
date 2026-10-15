"""An exact source-kind admission gate, separate from human year acceptance."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from swingset.admission.kinds import contract_version
from swingset.project.history import phase_two_allowed
from swingset.sources import get_page_kind
from swingset.sources.phase_one import PHASE1_KINDS

_REFUSALS: ContextVar[Counter[str] | None] = ContextVar("phase_two_refusals", default=None)


@contextmanager
def phase_two_refusals() -> Iterator[Counter[str]]:
    """Count the phase-two gate's refusals by reason, for the block.

    The cycle reports the count, so a gate that stays closed for hours shows
    as a number while other fetches keep `[stale-fetch]` quiet (I4).
    """
    counted: Counter[str] = Counter()
    token = _REFUSALS.set(counted)
    try:
        yield counted
    finally:
        _REFUSALS.reset(token)


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
    conn: sqlite3.Connection,
    *,
    source: str,
    source_ref: str | None,
    page_kind: str,
    year_allowed: Callable[[int], bool] | None = None,
) -> str | None:
    """Return a stable stop reason. Call within the same transaction as watch insertion.

    ``year_allowed`` lets one pass over many pages answer the year gate once
    per year instead of once per page.
    """
    reason = _phase_two_gate(
        conn, source=source, source_ref=source_ref, page_kind=page_kind, year_allowed=year_allowed
    )
    counted = _REFUSALS.get()
    if reason is not None and counted is not None:
        counted[reason] += 1
    return reason


def _phase_two_gate(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_ref: str | None,
    page_kind: str,
    year_allowed: Callable[[int], bool] | None,
) -> str | None:
    if page_kind.split(".")[0] != source:
        return "history_page_kind_source_mismatch"
    event = conn.execute(
        "SELECT e.year FROM source_event_map m JOIN events e USING(event_id) WHERE m.source=? AND m.source_ref=?",
        (source, source_ref),
    ).fetchone()
    if event is None:
        return "history_event_unmapped"
    allowed = (
        year_allowed(int(event[0]))
        if year_allowed is not None
        else phase_two_allowed(conn, int(event[0]))
    )
    if not allowed:
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
