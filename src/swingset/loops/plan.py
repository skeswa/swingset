"""The plan loop: event bookkeeping, then the acquisition plan (D-0218).

A plan tick runs the event bookkeeping stages as the cycle runs them, the
requirements scan and the registry verification summary, then evaluates
the phase-two year gate and the retained history plan on facts alone and
writes one `acquisition_plan` row per year and per planned page, each with
its reason. Acquire offers the eligible page rows; dispatch re-checks each
from facts before any mutation.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from swingset.log import log
from swingset.loops.runtime import PUBLICATION_IN_FLIGHT, Tick, publication_active
from swingset.schedule.acquisition import Bookkeeping
from swingset.state import acquisition_plan
from swingset.state.acquisition_plan import ELIGIBLE, PlanRow
from swingset.state.controls import ControlPaused

#: Seconds of the tick kept for the plan write after the page pass.
WRITE_RESERVE_SECONDS = 30.0


def run(tick: Tick) -> None:
    """One plan tick. Progress is plan rows written."""
    if tick.skipped:
        return
    database, clock, summary = tick.database, tick.clock, tick.summary
    assert tick.bundle is not None
    bundle = tick.bundle
    summary.update(
        stage_errors=[],
        held_operations=[],
        stages_disabled=bundle.config.stages.disabled(),
    )

    def held(action: str, exc: ControlPaused) -> None:
        summary["held_operations"].append(
            {"action": action, "reason": "operator_pause", "pauses": exc.pauses}
        )

    def stop() -> bool:
        # A publication fences every write, so nothing starts under one (F6).
        if publication_active(database.connection):
            tick.counters[PUBLICATION_IN_FLIGHT] = 1
            return True
        return tick.stop()

    def contained(stage: str, call: Callable[[], object]) -> None:
        # A defect in one bookkeeping stage is a stage error of this tick;
        # the plan is still written, so acquire never reads a frozen plan.
        try:
            call()
        except Exception as exc:
            summary["stage_errors"].append(
                {"stage": stage, "error": f"{type(exc).__name__}: {exc}"[:200]}
            )
            log("stage_failed", stage=stage, error=str(exc)[:300])

    stages = Bookkeeping(
        database, bundle, clock, tick.run_id, summary, deadline=tick.deadline, held=held
    )
    if not stop():
        contained("event_enumerations", stages.enumerations)
        contained("event_pressure_bootstrap", stages.pressure_bootstrap)
        contained("event_blocker_observations", stages.blocker_observations)
    if not stop():
        contained("requirements_scan", stages.requirements_scan)
    if not stop():
        contained("event_site_review", stages.site_review)
    from swingset.state.verification import verification_summary

    def verification() -> None:
        summary["registry_verification"] = verification_summary(database.connection, clock.now())

    contained("registry_verification", verification)
    if not stop():
        contained("event_pressure_refresh", stages.pressure_refresh)
    if not stop():
        contained("event_progress_refresh", stages.progress_refresh)
    if not stop():
        _plan(tick)
    tick.failed = bool(summary["stage_errors"])


def _plan(tick: Tick) -> None:
    """Evaluate years and pages on facts alone and write their rows."""
    from swingset.history.backfill import plan_pages
    from swingset.project.history import (
        projection_answer_cached,
        projection_pending,
        year_gate_reason,
    )

    database, clock, summary = tick.database, tick.clock, tick.summary
    assert tick.bundle is not None
    config = tick.bundle.config
    conn = database.connection
    began = clock.now()
    planned_at = began.isoformat()
    with projection_answer_cached():
        # Information only: the facts-only gate does not ask it (D-0218), so
        # its failure is noted and never stops the plan.
        pending: bool | None
        try:
            pending = projection_pending(conn)
        except Exception as exc:
            pending = None
            summary["projection_pending_error"] = f"{type(exc).__name__}: {exc}"[:200]
        rows: list[PlanRow] = []
        for (year,) in conn.execute(
            "SELECT DISTINCT year FROM events WHERE year IS NOT NULL ORDER BY year"
        ).fetchall():
            accepted = conn.execute(
                "SELECT inventory_digest FROM history_acceptance WHERE year=?", (year,)
            ).fetchone()
            rows.append(
                PlanRow(
                    acquisition_plan.year_key(int(year)),
                    "year",
                    year_gate_reason(conn, int(year)) or ELIGIBLE,
                    planned_at,
                    tick.run_id,
                    year=int(year),
                    detail_json=json.dumps(
                        {
                            "accepted_digest": accepted[0] if accepted else None,
                            "projection_pending": pending,
                        },
                        sort_keys=True,
                    ),
                )
            )
        pages = plan_pages(
            database,
            config,
            clock,
            run_id=tick.run_id,
            deadline=tick.deadline - timedelta(seconds=WRITE_RESERVE_SECONDS),
        )
    for planned in pages.pages:
        item, page, spec = planned.item, planned.item.page, planned.spec
        detail: dict[str, Any] = {}
        if spec is not None:
            detail["via"] = "archive" if spec.archive_url else "origin"
        if planned.origin_reason is not None:
            detail["origin_reason"] = planned.origin_reason
        rows.append(
            PlanRow(
                acquisition_plan.page_key(planned.watch_id),
                "page",
                planned.reason,
                planned_at,
                tick.run_id,
                year=item.event.year,
                source=page.source,
                source_ref=page.source_ref,
                page_kind=page.page_kind,
                watch_kind=page.kind,
                url=page.url,
                archive_url=spec.archive_url if spec is not None else None,
                event_id=item.event.event_id,
                rank=planned.rank,
                detail_json=json.dumps(detail, sort_keys=True) if detail else None,
            )
        )
    written = acquisition_plan.write(
        database, rows, run_id=tick.run_id, prune_pages=not pages.truncated
    )
    tick.progress(written["written"])
    summary["plan"] = {
        **written,
        "truncated": pages.truncated,
        "evaluated": pages.evaluated,
        "projection_pending": pending,
        "seconds": (clock.now() - began).total_seconds(),
        "rows": {
            f"{kind}/{reason}": count
            for (kind, reason), count in acquisition_plan.counts_by_reason(conn).items()
        },
    }
