"""One bounded, resumable archive dispatch through the ordinary fetch and parse pipeline."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from swingset.admission.generations import deserialize_result
from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.client import FetchResult
from swingset.fetch.wayback import Capture, schedule_capture
from swingset.history.acquisition import phase_two_gate
from swingset.history.captures import next_capture
from swingset.history.origin import OriginProposal
from swingset.history.platform import PlannedPage, PlatformPlan, retained_plan
from swingset.schedule.watches import due_watches, refresh_policy
from swingset.sources import get_page_kind
from swingset.sources.base import PageKind, WatchSpec
from swingset.state.control_scopes import for_watch
from swingset.state.controls import matching_pauses
from swingset.state.db import Database
from swingset.state.findings import Finding, replace_findings
from swingset.state.work import runnable_exists

if TYPE_CHECKING:
    from swingset.history.archive_timing import OfferTiming


class Fetcher(Protocol):
    def fetch(
        self,
        watch_id: str,
        page_kind: PageKind,
        run_id: str,
        *,
        deadline: datetime | None = None,
    ) -> FetchResult: ...


@dataclass(frozen=True)
class Dispatch:
    reason: str
    watch_id: str | None = None
    capture: Capture | None = None
    result: FetchResult | None = None


def _busy(database: Database, config: Config, clock: Clock, deadline: datetime) -> str | None:
    conn = database.connection
    if (deadline - clock.now()).total_seconds() <= 120:
        return "wall_clock_reserve"
    from urllib.parse import urlsplit

    from swingset.state.control_scopes import unit_allowed

    if runnable_exists(
        conn, now=clock.now(), allowed=lambda unit: unit_allowed(conn, unit, now=clock.now())
    ):
        return "pipeline_work_pending"
    for watch_id in due_watches(conn, config, clock.now()):
        watch = conn.execute(
            "SELECT state,priority,source,url,archive_url FROM watches WHERE watch_id=?",
            (watch_id,),
        ).fetchone()
        if matching_pauses(
            conn,
            for_watch(
                conn,
                source=watch["source"],
                watch_id=watch_id,
                host=urlsplit(watch["archive_url"] or watch["url"]).hostname,
            ),
            now=clock.now(),
        ):
            continue
        if watch[0] != "backfill" or watch[1] != 6:
            return "other_watch_due"
    return None


def _parent_ready(
    database: Database, item: PlannedPage, plan: PlatformPlan, *, now: datetime
) -> bool:
    if item.page.kind != "round":
        return True
    parents = [
        p
        for p in plan.pages
        if p.page.source == item.page.source
        and p.event.event_id == item.event.event_id
        and p.page.kind != "round"
        and (p.page.url == item.page.parent_url or item.page.parent_url is None)
    ]
    for parent in parents:
        candidate, state = next_capture(database.connection, parent, now=now)
        if candidate or state == "waiting":
            return False
        # A partial parent may still supply independently admitted round links after
        # its three capture alternatives are exhausted. Do not invent those links.
        retained = database.connection.execute(
            "SELECT g.result_json FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id JOIN watches w ON w.watch_id=u.watch_id WHERE w.source=? AND w.url=? AND g.state='accepted'",
            (parent.page.source, parent.page.url),
        ).fetchone()
        if retained:
            declarations = deserialize_result(str(retained[0])).watches
            if any(
                child.url == item.page.url
                and child.source_ref == item.page.source_ref
                and child.source == item.page.source
                for child in declarations
            ):
                return True
    return False


def _candidate(
    database: Database, config: Config, clock: Clock, item: PlannedPage, plan: PlatformPlan
) -> tuple[WatchSpec | None, Capture | None, str]:
    conn, page = database.connection, item.page
    if not config.enabled(page.source):
        return None, None, "source_disabled"
    spec = WatchSpec(
        "", page.source, page.kind, "GET", page.url, page.page_kind, source_ref=page.source_ref
    )
    if matching_pauses(
        conn,
        for_watch(
            conn,
            source=page.source,
            watch_id=spec.watch_id,
            page_kind=page.page_kind,
            watch_kind=page.kind,
            host="web.archive.org",
        ),
        now=clock.now(),
    ):
        return None, None, "operator_pause"
    if item.event.last_day >= clock.now().date():
        return None, None, "event_not_historical"
    if reason := phase_two_gate(
        conn, source=page.source, source_ref=page.source_ref, page_kind=page.page_kind
    ):
        return None, None, reason
    actual = conn.execute(
        "SELECT e.event_id,e.year,e.event_month,e.end_date FROM source_event_map m JOIN events e USING(event_id) WHERE m.source=? AND m.source_ref=?",
        (page.source, page.source_ref),
    ).fetchone()
    if actual is None or tuple(actual) != (
        item.event.event_id,
        item.event.year,
        item.event.event_month,
        item.event.end_date.isoformat() if item.event.end_date else None,
    ):
        return None, None, "plan_mapping_changed"
    capture, state = next_capture(conn, item, now=clock.now())
    if capture is None:
        return None, None, state
    if not _parent_ready(database, item, plan, now=clock.now()):
        return None, None, "parent_document_pending"
    spec = replace(spec, archive_url=capture.archive_url)
    existing = conn.execute(
        "SELECT state,next_check_at,paused_until,archive_url FROM watches WHERE watch_id=?",
        (spec.watch_id,),
    ).fetchone()
    advancing = bool(
        existing
        and existing[0] == "backfill"
        and existing[3]
        and existing[3] != capture.archive_url
    )
    if existing and (
        existing[0] in {"gone", "paused"}
        or (existing[2] and datetime.fromisoformat(existing[2]) > clock.now())
        or (
            existing[0] != "sealed"
            and not advancing
            and existing[1]
            and datetime.fromisoformat(existing[1]) > clock.now()
        )
    ):
        return None, None, "watch_cooldown"
    return spec, capture, "eligible"


def offers(
    database: Database,
    config: Config,
    clock: Clock,
    *,
    plan: PlatformPlan | None = None,
    run_id: str | None = None,
    timing: OfferTiming | None = None,
) -> tuple[WatchSpec, ...]:
    """Expose the finite eligible plan to ordinary rotation without scheduling it.

    Plan order admits newer events first. All waiting candidates remain visible
    together, so that order cannot override an already enrolled event turn.
    Dispatch repeats the year, parent, capture, and source gates before mutation.
    """
    if timing is not None:
        timing.begin(connection=database.connection, config=config, clock=clock, run_id=run_id)
    plan = plan or retained_plan(database.connection, history_start=config.history_start)
    options = [(item, _candidate(database, config, clock, item, plan)) for item in plan.pages]
    archive_sources = {item.page.source for item, (spec, _, _) in options if spec is not None}
    result: dict[str, WatchSpec] = {}
    for item, (spec, _, reason) in options:
        if (
            spec is None
            and reason in {"capture_gap_exhausted", "operator_pause"}
            and item.page.source not in archive_sources
        ):
            from swingset.history.origin_dispatch import candidate

            spec, _, _ = candidate(database, config, clock, item, plan, run_id=run_id)
        if spec is not None:
            result.setdefault(spec.watch_id, spec)
    if timing is not None:
        timing.finish(result)
    return tuple(result.values())


def offer(
    database: Database,
    config: Config,
    clock: Clock,
    *,
    plan: PlatformPlan | None = None,
    run_id: str | None = None,
) -> WatchSpec | None:
    """Compatibility for callers requesting only the plan's first candidate."""
    return next(iter(offers(database, config, clock, plan=plan, run_id=run_id)), None)


def dispatch_one(
    database: Database,
    config: Config,
    clock: Clock,
    run_id: str,
    *,
    deadline: datetime,
    fetcher: Fetcher,
    plan: PlatformPlan | None = None,
    allocated: bool = False,
    target_watch_id: str | None = None,
) -> Dispatch:
    """Create/advance at most one watch atomically, then perform at most one gated fetch.

    The caller owns the database process lock. An allocated turn may coexist with
    unrelated backlog; direct calls retain the downstream reserve. Snapshots and
    selected generations are the durable resume ledger in both cases.
    """
    if clock.now() >= deadline:
        return Dispatch("wall_clock_reserve")
    if not allocated and (reason := _busy(database, config, clock, deadline)):
        return Dispatch(reason)
    plan = plan or retained_plan(database.connection, history_start=config.history_start)
    conn = database.connection
    gaps = list(plan.findings)
    chosen: tuple[PlannedPage, Capture | None, WatchSpec] | None = None
    blocked = "no_eligible_capture"
    from swingset.schedule.event_timing_observer import checkpoint, resume

    checkpoint()
    try:
        with database.transaction():
            if not allocated and (reason := _busy(database, config, clock, deadline)):
                return Dispatch(reason)
            options = [
                (item, _candidate(database, config, clock, item, plan)) for item in plan.pages
            ]
            archive_sources = {
                item.page.source for item, (spec, _, _) in options if spec is not None
            }
            for item, (spec, capture, reason) in options:
                page = item.page
                archive_reason = reason
                proposal: OriginProposal | None = None
                if (
                    spec is None
                    and reason in {"capture_gap_exhausted", "operator_pause"}
                    and item.page.source not in archive_sources
                ):
                    from swingset.history.origin_dispatch import candidate

                    spec, proposal, reason = candidate(
                        database, config, clock, item, plan, run_id=run_id
                    )
                if spec is None:
                    blocked = reason
                    if archive_reason == "capture_gap_exhausted" and item.captures:
                        gaps.append(
                            Finding(
                                "history_archive_gap",
                                "event",
                                item.event.event_id,
                                "warning",
                                "Platform capture alternatives are exhausted or incomplete",
                                {
                                    "source": page.source,
                                    "url": page.url,
                                    "year": item.event.year,
                                    "attempts_available": len(item.captures),
                                },
                            )
                        )
                    elif reason == "parent_document_pending":
                        gaps.append(
                            Finding(
                                "history_archive_gap",
                                "event",
                                item.event.event_id,
                                "warning",
                                "Archived sheet waits for its admitted parent declaration",
                                {
                                    "source": page.source,
                                    "url": page.url,
                                    "parent_url": page.parent_url,
                                    "year": item.event.year,
                                },
                            )
                        )
                    continue
                if chosen is not None or (
                    target_watch_id is not None and spec.watch_id != target_watch_id
                ):
                    continue
                # The gate and this mutation share BEGIN IMMEDIATE; changed policies or
                # year acceptance can never leave a newly admitted control behind.
                scheduled = False
                if proposal is not None:
                    from swingset.history.origin_dispatch import schedule

                    schedule(conn, spec, proposal, run_id=run_id, now=clock.now())
                    scheduled = True
                else:
                    scheduled = schedule_capture(conn, spec, now=clock.now())
                if scheduled:
                    conn.execute("UPDATE watches SET priority=6 WHERE watch_id=?", (spec.watch_id,))
                    replace_findings(
                        conn,
                        owner_kind="acquisition_gate",
                        owner_id=spec.watch_id,
                        findings=(),
                        opened_at=clock.now().isoformat(),
                        run_id=run_id,
                    )
                    chosen = item, capture, spec
            replace_findings(
                conn,
                owner_kind="platform_backfill",
                owner_id="retained_plan",
                findings=tuple(gaps),
                opened_at=clock.now().isoformat(),
                run_id=run_id,
            )
    finally:
        resume()
    if chosen is None:
        return Dispatch(blocked)
    _, capture, spec = chosen
    # Direct calls leave two minutes for downstream bookkeeping; fair allocation
    # already reserves offline time. The client retains every actual host gate.
    try:
        result = fetcher.fetch(
            spec.watch_id,
            get_page_kind(spec.parser),
            run_id,
            deadline=deadline if allocated else deadline - timedelta(seconds=120),
        )
    finally:
        if capture is None:
            with database.transaction():
                conn.execute(
                    "UPDATE history_origin_intents SET dispatch_run_id=NULL WHERE watch_id=? AND dispatch_run_id=?",
                    (spec.watch_id, run_id),
                )
    if not result.skipped:
        with database.transaction():
            refresh_policy(
                conn, config, spec.watch_id, clock.now(), outcome=result.classification.outcome
            )
    return Dispatch("dispatched", spec.watch_id, capture, result)
