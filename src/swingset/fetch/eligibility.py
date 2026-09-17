"""Read-only ordinary acquisition proofs; selection rank is not an admission gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit

from swingset.fetch.politeness import Gate, Grant, Paused
from swingset.fetch.robots import Robots
from swingset.history.archive_timing import ArchiveOfferProof
from swingset.history.origin_dispatch import managed
from swingset.schedule.fairness import request_denial
from swingset.state.control_scopes import for_watch
from swingset.state.controls import matching_pauses

State = Literal["eligible", "blocked", "unknown", "inactive"]


@dataclass(frozen=True)
class Assessment:
    state: State
    reason: str
    until: datetime


def ordinary(
    gate: Gate,
    robots: Robots,
    watch_id: str,
    *,
    deadline: datetime,
    archive_proof: ArchiveOfferProof | None = None,
    run_id: str | None = None,
) -> Assessment:
    """Assess first-request eligibility, never debit or dispatch historical work.

    The caller must close observations before mutations and recheck control
    revision at both endpoints. Cached robots without verifiable bytes is unknown.
    """
    try:
        return _ordinary(
            gate, robots, watch_id, deadline=deadline, archive_proof=archive_proof, run_id=run_id
        )
    except (ValueError, TypeError, OverflowError):
        return Assessment("unknown", "request_gate_metadata_invalid", deadline)


def _ordinary(
    gate: Gate,
    robots: Robots,
    watch_id: str,
    *,
    deadline: datetime,
    archive_proof: ArchiveOfferProof | None,
    run_id: str | None,
) -> Assessment:
    conn, now, config = gate.connection, gate.clock.now(), gate.config
    row = conn.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
    if row is None:
        return Assessment("unknown", "watch_missing", deadline)
    watch = SimpleNamespace(**dict(row))
    from swingset.sources import get_page_kind

    try:
        get_page_kind(watch.parser)
    except KeyError:
        return Assessment("unknown", "source_page_kind_unavailable", deadline)
    if not config.enabled(watch.source):
        return Assessment("blocked", "source_disabled", deadline)
    if archive_proof is not None:
        if not archive_proof.current(conn, config, now, run_id) or not archive_proof.binds(watch):
            return Assessment("unknown", "historical_dispatch_proof_stale", deadline)
        deadline = min(deadline, archive_proof.until)
        # The dispatcher may advance this exact existing watch to the offered
        # capture. This diagnostic never performs that mutation itself.
        watch.archive_url = archive_proof.spec.archive_url
    url = watch.archive_url or watch.url
    host = urlsplit(url).hostname
    if not host or urlsplit(url).scheme not in {"http", "https"}:
        return Assessment("blocked", "invalid_url", deadline)
    pauses = matching_pauses(
        conn, for_watch(conn, source=watch.source, watch_id=watch_id, host=host), now=now
    )
    if pauses:
        until = min(
            (datetime.fromisoformat(p["until_at"]) for p in pauses if p["until_at"]),
            default=deadline,
        )
        return Assessment("blocked", "operator_pause", min(deadline, until))
    if watch.state in {"sealed", "retired"}:
        return Assessment("blocked", "watch_not_scheduled", deadline)
    if watch.next_check_at is None:
        return Assessment("unknown", "watch_due_unknown", deadline)
    for value, reason in ((watch.paused_until, "watch_retry"), (watch.next_check_at, "watch_due")):
        if value and datetime.fromisoformat(value) > now:
            return Assessment("blocked", reason, min(deadline, datetime.fromisoformat(value)))
    if watch.archive_url:
        if archive_proof is None:
            return Assessment("unknown", "historical_dispatch_proof_required", deadline)
        if host != "web.archive.org" or urlsplit(url).scheme != "https":
            return Assessment("blocked", "invalid_archive_url", deadline)
    elif managed(conn, watch_id):
        return Assessment("unknown", "historical_dispatch_proof_required", deadline)
    elif watch.source == "steprightsolutions":
        return Assessment("blocked", "archive_required", deadline)
    if denied := request_denial(
        conn, config, watch_id=watch_id, host=host, now=now, independent=True
    ):
        return Assessment("blocked", denied, deadline)
    grant, until = gate.assess(host, source=watch.source)
    until = min(until, deadline)
    if not isinstance(grant, Grant):
        reason = (
            grant.reason
            if isinstance(grant, Paused)
            else ("host_inflight" if host in gate.inflight else "host_cooldown")
        )
        return Assessment("blocked", reason, until)
    policy, expires = robots.cached(url)
    if policy is None or expires is None:
        return Assessment("unknown", "robots_proof_unavailable", until)
    until = min(until, expires)
    if not policy.allowed:
        return Assessment("blocked", "robots_disallow", until)
    # policy() may extend this host's next_allowed_at when it learns a delay.
    if policy.crawl_delay:
        fetched = conn.execute(
            "SELECT robots_fetched_at FROM hosts WHERE host=?", (host,)
        ).fetchone()[0]
        from datetime import timedelta

        due = datetime.fromisoformat(fetched) + timedelta(seconds=policy.crawl_delay)
        if due > now:
            return Assessment("blocked", "robots_crawl_delay", min(until, due))
    return Assessment("eligible", "ordinary_request_gates_open", until)


def union(assessments: list[Assessment], *, deadline: datetime) -> Assessment:
    """Union event waiting time; two eligible pages do not earn double age."""
    until = min((value.until for value in assessments), default=deadline)
    if any(value.state == "eligible" for value in assessments):
        return Assessment("eligible", "at_least_one_request_eligible", until)
    if not assessments or any(value.state == "unknown" for value in assessments):
        return Assessment("unknown", "incomplete_request_gate_proof", until)
    return Assessment("blocked", ",".join(sorted({value.reason for value in assessments})), until)
