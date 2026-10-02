"""The derive loop: parse, project and link whatever is runnable (D-0215).

Release-lag knob (plan section 5, phase 3): while builds are due and the
release loop has not progressed for `release_lag_intervals` build intervals,
a tick leases only parse and link units, so project work does not crowd out
a release that is behind. It counts `release_lag` and keeps the guard
`loop:derive release_lag`, whose one-hour expiry the watchdog reads (I3).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from swingset.backup.recovery import local_recovery
from swingset.fetch.archive import Archive
from swingset.log import log
from swingset.loops.runtime import PUBLICATION_IN_FLIGHT, Tick, note_skip, publication_active
from swingset.schedule.offline import Offline, registry_crosscheck
from swingset.state import derivations, queue, rwlock
from swingset.state.control_scopes import for_unit, unit_allowed
from swingset.state.controls import ControlPaused, matching_pauses
from swingset.state.db import SCHEMA_VERSION
from swingset.state.work import WorkUnit, unfinished_units
from swingset.state.write_deadline import WriteDeadlineExceeded

if TYPE_CHECKING:
    from swingset.config import Config

#: Seconds of a tick given to rebuilding lost parse hints.
HINT_RECOVERY_SECONDS = 2.0
#: The guard reason, and counter, of a tick the release-lag knob narrowed.
RELEASE_LAG = "release_lag"
#: The stages a lagging release leaves derive.
LAG_STAGES = frozenset({"parse", "link"})


#: Release skip reasons that mean it lacked capacity derive can give back:
#: the heavy lock was busy. A pause, the disk guard or a publication in
#: flight is not helped by holding derive's project units.
CAPACITY_SKIPS = frozenset({"heavy_busy"})


def release_lag(
    conn: sqlite3.Connection, config: Config, *, now: datetime
) -> dict[str, Any] | None:
    """Whether release is behind, and whether derive holds project units for it.

    Release is behind when a build is due (none admitted within the build
    interval) and its last progress is older than `release_lag_intervals`
    build intervals. A completed build attempt counts as progress too: a
    reused candidate means release is current, though it writes nothing.
    With no progress on record the clock starts at release's first tick.
    None when it is not behind, the knob is 0, the interval unset, or release
    never ticked.

    Behind, the knob engages (`engaged`) only when release ticked within the
    build interval and a tick was skipped for capacity derive holds
    (`CAPACITY_SKIPS`): then holding project units helps it (I5). A release
    that is stopped, paused, guarded on disk or failing is reported with its
    `cause` and derive runs as usual.
    """
    from swingset.schedule.cycle import build_deferred_for_interval

    scheduler = config.scheduler
    intervals, interval = scheduler.release_lag_intervals, scheduler.build_interval_seconds
    if not intervals or interval is None:
        return None
    try:
        if build_deferred_for_interval(conn, config, now=now):
            return None
        row = conn.execute(
            "SELECT last_progress_at FROM loop_state WHERE loop='release'"
        ).fetchone()
        if row is None:
            return None
        completed = conn.execute(
            "SELECT MAX(settled_at) FROM execution_admissions "
            "WHERE action_kind='build' AND outcome='completed'"
        ).fetchone()[0]
        first = conn.execute("SELECT MIN(started_at) FROM runs WHERE loop='release'").fetchone()[0]
        recent = [
            str(value[0]) if value[0] is not None else None
            for value in conn.execute(
                "SELECT json_extract(summary_json,'$.skipped') FROM runs WHERE loop='release' "
                "AND finished_at > ? AND json_valid(summary_json) ORDER BY finished_at DESC",
                ((now - timedelta(seconds=interval)).isoformat(),),
            )
        ]
    except sqlite3.Error:
        return None
    marks = [str(value) for value in (row[0], completed) if value]
    since_text = max(marks, key=_moment) if marks else first
    if not since_text:
        return None
    since = _moment(str(since_text))
    limit = intervals * interval
    if (now - since).total_seconds() <= limit:
        return None
    capacity = sorted({value for value in recent if value in CAPACITY_SKIPS})
    cause = (
        capacity[0]
        if capacity
        else "release_not_ticking"
        if not recent
        else next((value for value in recent if value), "release_not_building")
    )
    return {
        "since": since.isoformat(),
        "release_lag_intervals": intervals,
        "build_interval_seconds": interval,
        "engaged": bool(capacity),
        "cause": cause,
    }


def _moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


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
    # Every build holds the heavy lock while `[release] streamed_lock` is
    # true (the default; a whole or closure build always): builds peaked at
    # 2 to 6.3 GB, and parse-only derive ticks beside one reached 2 to 3.9 GB
    # in the WP14 rehearsal, more than the 8 GB worker has, so derive starts
    # no unit until the build ends (F10). Parse work waits; acquisition's
    # backpressure counts it.
    lag = release_lag(conn, tick.bundle.config, now=clock.now())
    if lag is not None:
        summary[RELEASE_LAG] = lag
        log("release_lag", **lag)
    engaged = lag is not None and bool(lag["engaged"])
    if engaged:
        tick.counters[RELEASE_LAG] += 1
    note_skip(tick, RELEASE_LAG, skipped=engaged)
    heavy: rwlock.Held | None = None
    try:
        heavy = rwlock.heavy_shared(database.state_dir)
    except rwlock.LockBusy:
        tick.counters["heavy_deferred"] += 1
        summary["heavy_deferred"] = True
    if heavy is not None:
        try:
            _offline(tick, stages=LAG_STAGES if engaged else None)
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


def _offline(tick: Tick, *, stages: frozenset[str] | None = None) -> None:
    database, clock, summary = tick.database, tick.clock, tick.summary
    assert tick.bundle is not None
    recovery = local_recovery(database.state_dir, maximum_schema_version=SCHEMA_VERSION)
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
        stages=stages,
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
