"""The acquire loop: discovery, then one acquisition pass (D-0218).

An acquire tick discovers configured and registry watches, then runs the
acquisition pass shared with the cycle. Its history offers are the eligible
`acquisition_plan` rows in plan order, which the plan loop wrote; each
dispatch re-checks its target from facts before any mutation. The tick runs
inside the facts-only year gate, so no derivation predicate runs in it (I5).
"""

from __future__ import annotations

from datetime import datetime

from swingset.fetch.archive import Archive
from swingset.fetch.recovery import LocalCheckpointRecovery
from swingset.loops.runtime import PUBLICATION_IN_FLIGHT, Tick, publication_active
from swingset.schedule.acquisition import Acquisition, paused_hosts, request_failures
from swingset.sources.base import WatchSpec
from swingset.state import acquisition_plan
from swingset.state.acquisition_plan import PlanRow
from swingset.state.db import SCHEMA_VERSION


def spec(row: PlanRow) -> WatchSpec:
    """The watch spec an eligible page row offers, as `backfill.offers` built it."""
    assert row.source and row.watch_kind and row.url and row.page_kind
    return WatchSpec(
        "",
        row.source,
        row.watch_kind,
        "GET",
        row.url,
        row.page_kind,
        source_ref=row.source_ref,
        archive_url=row.archive_url,
    )


def run(tick: Tick) -> None:
    """One acquire tick. Progress is requests issued."""
    if tick.skipped:
        return
    database, clock, summary = tick.database, tick.clock, tick.summary
    assert tick.bundle is not None
    bundle = tick.bundle
    conn = database.connection
    summary.update(
        checked=0,
        changed=0,
        not_modified=0,
        nonce_unchanged=0,
        bytes_by_host={},
        history_dispatch=None,
        stage_errors=[],
        guards={"passes": 0, "recorded": 0, "cleared": 0},
        offered=0,
        stale_plan_rows=0,
    )

    def stop() -> bool:
        # A publication fences every write, so no request starts under one (F6).
        if publication_active(conn):
            tick.counters[PUBLICATION_IN_FLIGHT] = 1
            return True
        return tick.stop()

    if not stop():
        from swingset.schedule.discover import discover
        from swingset.schedule.registry import discover_registry

        discover(database, bundle, clock.now())
        if bundle.config.enabled("wsdc_registry"):
            discover_registry(database, clock.now())

    def offers(_until: datetime) -> list[WatchSpec]:
        """Eligible plan rows whose page is still in the retained plan."""
        from swingset.history.backfill import page_watch_id
        from swingset.history.platform import retained_plan

        rows = acquisition_plan.eligible_pages(conn)
        if not rows:
            return []
        # A page gone from the plan would make dispatch evaluate the whole
        # plan inside one write (D-0195); it waits for the next plan tick.
        current = {
            page_watch_id(item)
            for item in retained_plan(conn, history_start=bundle.config.history_start).pages
        }
        offered = []
        for row in rows:
            if row.plan_key.removeprefix("page:") in current:
                offered.append(spec(row))
            else:
                summary["stale_plan_rows"] += 1
        summary["offered"] = len(offered)
        return offered

    recovery = LocalCheckpointRecovery(
        database.state_dir / "checkpoints", maximum_schema_version=SCHEMA_VERSION
    )
    acquiring = Acquisition(
        database,
        bundle.config,
        clock,
        tick.run_id,
        summary,
        archive=Archive(database.state_dir, recovery=recovery),
        stop=stop,
        should_stop=tick.should_stop,
        transport=tick.transport,
        loop="acquire",
    )
    try:
        if not stop():
            _, seconds = acquiring.run(tick.deadline, offers)
            summary["acquisition_seconds"] = seconds
    finally:
        acquiring.close()
    summary["restored_artifacts"] = recovery.restored
    summary["paused_hosts"] = paused_hosts(database, clock.now())
    indexed = sum((summary.get("archive_index") or {}).values())
    tick.counters["requests"] += summary["checked"]
    tick.counters["index_pages"] += indexed
    tick.progress(summary["checked"] + indexed)
    tick.failed = (
        bool(summary["stage_errors"])
        or request_failures(summary)
        or any(row["pause_reason"] == "Blocked" for row in summary["paused_hosts"])
    )
