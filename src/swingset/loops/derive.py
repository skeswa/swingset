"""The derive loop: parse, project and link whatever is runnable (D-0215)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

from swingset.fetch.archive import Archive
from swingset.fetch.recovery import LocalCheckpointRecovery
from swingset.log import log
from swingset.loops.runtime import PUBLICATION_IN_FLIGHT, Tick, publication_active
from swingset.schedule.offline import Offline, registry_crosscheck
from swingset.state import derivations, queue, rwlock
from swingset.state.control_scopes import for_unit, unit_allowed
from swingset.state.controls import ControlPaused, matching_pauses
from swingset.state.db import SCHEMA_VERSION
from swingset.state.work import WorkUnit, unfinished_units
from swingset.state.write_deadline import WriteDeadlineExceeded

#: Seconds of a tick given to rebuilding lost parse hints.
HINT_RECOVERY_SECONDS = 2.0


def run(tick: Tick) -> None:
    """One derive tick. Progress is units completed."""
    if tick.skipped:
        return
    database, clock, summary = tick.database, tick.clock, tick.summary
    assert tick.bundle is not None
    conn = database.connection
    summary.update(
        stages=[],
        unit_failures=[],
        readiness_errors={},
        stage_errors=[],
        held_operations=[],
        waiting_repeats=0,
        control_timeouts=0,
        queue={"woken": 0, "lease_busy": 0, "quarantined": []},
    )
    try:
        replayed_before: dict[str, int] | None = derivations.replay_epochs(conn)
    except Exception as exc:
        summary["derivation_error"] = f"{type(exc).__name__}: {exc}"[:200]
        replayed_before = None

    def contained(stage: str, call: Callable[[], object]) -> None:
        # A bookkeeping write that overruns is noted, not fatal (D-0201).
        try:
            call()
        except WriteDeadlineExceeded as exc:
            summary["stage_errors"].append({"stage": stage, "error": str(exc)})
            log("stage_failed", stage=stage, error=str(exc)[:300])

    def wake() -> None:
        summary["queue"]["woken"] = queue.wake(database, clock.now())

    contained("queue_wake", wake)
    from swingset.state.retention import enforce_size_cap

    # The cap pause is unchanged: it pauses every loop (open decision 3).
    summary["retention_cap"] = enforce_size_cap(
        database.state_dir,
        max_database_bytes=tick.bundle.config.retention.max_database_bytes,
        now=clock.now(),
    )
    seconds = min(HINT_RECOVERY_SECONDS, tick.remaining())
    if seconds > 0 and not tick.should_stop():
        from swingset.state.work import recover_parse_hints

        def hints() -> None:
            summary["parse_hint_recovery"] = recover_parse_hints(
                database, now=clock.now(), wall_seconds=seconds
            )

        contained("parse_hint_recovery", hints)
    # Release holds the heavy lock while it builds. A build peaks near 6.3 GB
    # and parse-only derive ticks beside it reached 2 to 3.9 GB in the WP14
    # rehearsal, more than the 8 GB worker has, so derive starts no unit until
    # the build ends (F10). Parse work waits; acquisition's backpressure
    # counts it as before.
    heavy: rwlock.Held | None = None
    try:
        heavy = rwlock.heavy_shared(database.state_dir)
    except rwlock.LockBusy:
        tick.counters["heavy_deferred"] += 1
        summary["heavy_deferred"] = True
    if heavy is not None:
        try:
            _offline(tick)
        finally:
            heavy.release()
    summary["derivation"] = derivations.cycle_progress(conn, tick.run_id, replayed_before)
    summary["queue_states"] = queue.counts_by_state(conn)
    summary["held_units"] = held_units(conn, clock.now())


#: At most this many held units are named in a derive record.
HELD_EXAMPLES = 20


def held_units(conn: sqlite3.Connection, now: datetime) -> dict[str, object]:
    """How many unfinished units an operator pause holds, with a few named.

    Walking every unfinished unit costs seconds on production, so it runs
    only while a pause exists, and names at most `HELD_EXAMPLES`.
    """
    if not conn.execute("SELECT 1 FROM operator_pauses LIMIT 1").fetchone():
        return {"count": 0, "units": []}
    count = 0
    named: list[dict[str, object]] = []
    for unit in unfinished_units(conn):
        if unit_allowed(conn, unit, now=now):
            continue
        count += 1
        if len(named) < HELD_EXAMPLES:
            named.append(
                {**asdict(unit), "pauses": matching_pauses(conn, for_unit(conn, unit), now=now)}
            )
    return {"count": count, "units": named}


def _offline(tick: Tick) -> None:
    database, clock, summary = tick.database, tick.clock, tick.summary
    assert tick.bundle is not None
    recovery = LocalCheckpointRecovery(
        database.state_dir / "checkpoints", maximum_schema_version=SCHEMA_VERSION
    )
    archive = Archive(database.state_dir, recovery=recovery)

    def allowed(unit: WorkUnit) -> bool:
        return unit_allowed(database.connection, unit, now=clock.now())

    def held(action: str, exc: ControlPaused) -> None:
        summary["held_operations"].append(
            {"action": action, "reason": "operator_pause", "pauses": exc.pauses}
        )

    def stop() -> bool:
        # A publication fences every write, so no unit starts under one (F6).
        if publication_active(database.connection):
            tick.counters[PUBLICATION_IN_FLIGHT] = 1
            return True
        return tick.stop()

    offline = Offline(
        database,
        archive,
        tick.bundle,
        clock,
        tick.run_id,
        summary,
        allowed=allowed,
    )
    offline.run(stop)
    if not stop():
        registry_crosscheck(database, archive, clock, tick.run_id, held=held)
    tick.progress(offline.completed)
    tick.counters["completed"] += offline.completed
    tick.counters["failed_units"] += len(summary["unit_failures"])
    summary["parse_errors"] = dict(offline.failures)
    summary["restored_artifacts"] = recovery.restored
    tick.failed = (
        offline.failed() or bool(summary["stage_errors"]) or bool(summary["readiness_errors"])
    )
