"""The release loop: reconcile, then build and publish on the build interval.

An hourly tick (D-0215). It waits for derive to accept the input bundle
(runtime), refuses to build on a nearly full disk, reconciles a pending
publication when a hub is configured, builds a correction when the baseline
needs one, and builds a candidate unless the build interval or the backlog
defers it. A whole or closure build, and a correction, hold the heavy lock
exclusive, so derive starts no unit meanwhile (F10). A streamed
(partitioned) build holds it too while `[release] streamed_lock` is true,
the default: the rehearsal measured 6.7 GB before F26's fix, and the lock
comes off only once the worker measures it under 2 GB beside derive.
The build and publish steps are the cycle's
(`schedule/cycle.correct`, `build_and_publish`); a partitioned build puts its
release manifest and stage 1's counts in the summary's `partitions` (D-0224).
Every build records its gate verdict in `verdict`; a refused one counts in
`refused`, publishes nothing and is not a failure (D-0225).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from swingset.build.builder import BuildError
from swingset.build.closure_manifest import ClosureError
from swingset.log import log
from swingset.loops.runtime import Tick, note_skip
from swingset.publish.service import reconcile
from swingset.schedule.cycle import (
    build_and_publish,
    build_deferred_for_backlog,
    build_deferred_for_interval,
    correct,
)
from swingset.schedule.fairness import backpressure
from swingset.state import guards, rwlock
from swingset.state.controls import ControlPaused
from swingset.state.work import unfinished_units

Held = Callable[[str, ControlPaused], None]

#: The worker's disk quota as `swingset-disk` caches it: `USED CAP [READ_AT]`
#: in bytes (nix/module.nix). Absent on a machine without a quota.
DISK_FILE = Path("/run/swingset-disk")
DISK = "disk"
#: The watchdog and backup refresh the reading every 30 minutes.
DISK_STALE = timedelta(hours=1)
HEAVY_BUSY = "heavy_busy"
#: The guard a disk refusal leaves on `loop:release`; it clears on the next
#: tick with room (D-0219).
DISK_GUARD = "release_disk_low"
SUBJECT = "loop:release"
#: Release paths whose build holds the heavy lock (build/service.release_path).
HEAVY_PATHS = frozenset({"whole", "closure"})
#: Reconcile outcomes that moved publication forward.
LANDED = frozenset({"promoted", "recovered", "published"})


def free_bytes(path: Path = DISK_FILE) -> tuple[int, str | None] | None:
    """Free bytes and read time from the cached quota line, or None."""
    try:
        fields = path.read_text().split()
        used, cap = int(fields[0]), int(fields[1])
    except (OSError, ValueError, IndexError):
        return None
    return cap - used, fields[2] if len(fields) > 2 else None


def _stale(read_at: str | None, now: datetime) -> bool:
    """A reading older than `DISK_STALE` is unknown; one with no time is taken as is."""
    if read_at is None:
        return False
    try:
        moment = datetime.fromisoformat(read_at.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = datetime.fromtimestamp(float(read_at), UTC)
        except ValueError:
            return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return now - moment > DISK_STALE


def _disk(tick: Tick, path: Path) -> bool:
    """Record the disk reading; refuse the tick and leave a guard when low."""
    assert tick.bundle is not None
    database, now = tick.database, tick.clock.now()
    minimum = tick.bundle.config.scheduler.release_min_free_bytes
    reading = free_bytes(path)
    stale = reading is not None and _stale(reading[1], now)
    tick.summary["disk"] = (
        None
        if reading is None
        else {
            "free_bytes": reading[0],
            "read_at": reading[1],
            "minimum": minimum,
            # An old reading is unknown: the tick neither refuses nor trusts it.
            "stale": stale,
        }
    )
    low = reading is not None and not stale and reading[0] < minimum
    conn = database.connection
    if low:
        with database.transaction() as write:
            guards.flush(
                write, loop="release", seen={(SUBJECT, DISK_GUARD): 1}, requested=(), now=now
            )
        tick.skipped = DISK
        log("release_disk_low", free_bytes=reading[0] if reading else None, minimum=minimum)
    elif guards.get(conn, SUBJECT, DISK_GUARD) is not None:
        with database.transaction() as write:
            guards.delete(write, SUBJECT, DISK_GUARD)
    return not low


def run(tick: Tick, *, disk_file: Path | None = None) -> None:
    """One release tick. Progress is a build, a publication or a reconcile."""
    if tick.skipped:
        return
    disk_file = DISK_FILE if disk_file is None else disk_file
    database, clock, summary = tick.database, tick.clock, tick.summary
    assert tick.bundle is not None
    bundle, hub, dry_run = tick.bundle, tick.hub, tick.dry_run
    conn = database.connection
    summary.update(dry_run=dry_run, stages=[], held_operations=[])
    if not _disk(tick, disk_file):
        return

    def held(action: str, exc: ControlPaused) -> None:
        summary["held_operations"].append(
            {"action": action, "reason": "operator_pause", "pauses": exc.pauses}
        )

    if hub is not None:
        reconciled = reconcile(database.state_dir, hub, dry_run=dry_run)
        summary["reconciliation"] = asdict(reconciled)
        if reconciled.commit:
            summary["publish_commit"] = reconciled.commit
        if reconciled.state in LANDED:
            tick.counters["reconciled"] += 1
            tick.progress()
    # A correction publishes to the hub, so it runs only with one, and only
    # while work is queued, as in the cycle.
    correcting = hub is not None and next(iter(unfinished_units(conn)), None) is not None
    deferred: str | None = None
    if build_deferred_for_interval(conn, bundle.config, now=clock.now()):
        deferred = "interval"
    elif build_deferred_for_backlog(
        database.state_dir,
        bundle.config,
        pressure=backpressure(conn, bundle.config, now=clock.now()),
        now=clock.now(),
    ):
        deferred = "backlog"
    # The watchdog's [loop-idle] window for release (nix/watchdog.py).
    summary["build_interval_seconds"] = bundle.config.scheduler.build_interval_seconds
    if deferred is not None:
        summary["build_deferred"] = deferred
    if tick.stop() or (deferred is not None and not correcting):
        return
    if correcting:
        correcting = _correction_needed(tick)
    if deferred is not None and not correcting:
        tick.failed = "correction_error" in summary
        return
    from swingset.build.service import release_path

    path = release_path(database, bundle)
    # A correction reads the baseline whole, and whole and closure builds
    # hold every table in memory; a streamed build takes the heavy lock
    # while `streamed_lock` says so (its measured peak beside derive decides).
    heavy_path = path in HEAVY_PATHS or (
        path == "partitioned" and bundle.config.release.streamed_lock
    )
    summary["heavy_lock"] = correcting or (deferred is None and heavy_path)
    heavy: rwlock.Held | None = None
    if summary["heavy_lock"]:
        waited_from = clock.now()
        try:
            heavy = rwlock.heavy_exclusive(
                database.state_dir,
                timeout=min(rwlock.HEAVY_TIMEOUT_SECONDS, tick.remaining()),
                clock=clock,
            )
        except rwlock.LockBusy:
            heavy = None
        summary["heavy_wait_seconds"] = (clock.now() - waited_from).total_seconds()
        if heavy is None:
            note_skip(tick, HEAVY_BUSY, skipped=True)
            tick.skipped = HEAVY_BUSY
            tick.counters[HEAVY_BUSY] += 1
            return
    note_skip(tick, HEAVY_BUSY, skipped=False)
    try:
        if correcting and not tick.stop():
            summary["build_attempted"] = True
            _correct(tick, held)
        if deferred is None and not tick.stop():
            summary["build_attempted"] = True
            _build(tick, held)
    finally:
        if heavy is not None:
            heavy.release()
    tick.failed = "build_error" in summary or "correction_error" in summary


def _correction_needed(tick: Tick) -> bool:
    """Whether the baseline needs a correction; an error is reported, not raised."""
    from swingset.build.service import correction_needed

    assert tick.bundle is not None
    try:
        return correction_needed(tick.database, tick.bundle)
    except (BuildError, ClosureError) as exc:
        tick.summary["correction_error"] = f"{type(exc).__name__}: {exc}"
        log("correction_build_failed", error=str(exc)[:300])
        return False


def _correct(tick: Tick, held: Held) -> None:
    """Build and publish the correction `_correction_needed` found."""
    assert tick.bundle is not None
    try:
        correct(
            tick.database,
            tick.bundle,
            tick.clock,
            tick.run_id,
            tick.summary,
            hub=tick.hub,
            dry_run=tick.dry_run,
        )
    except ControlPaused as exc:
        held("correction_build", exc)
        return
    except (BuildError, ClosureError) as exc:
        # Reported, not fatal: the normal build below still runs (D-0187).
        tick.summary["correction_error"] = f"{type(exc).__name__}: {exc}"
        log("correction_build_failed", error=str(exc)[:300])
        return
    tick.counters["corrections"] += 1
    tick.progress()
    if tick.summary.get("correction_commit"):
        tick.counters["publications"] += 1
        tick.progress()


def _build(tick: Tick, held: Held) -> None:
    assert tick.bundle is not None
    try:
        candidate = build_and_publish(
            tick.database,
            tick.bundle,
            tick.clock,
            tick.run_id,
            tick.summary,
            hub=tick.hub,
            dry_run=tick.dry_run,
        )
    except ControlPaused as exc:
        held("build", exc)
        return
    except (BuildError, ClosureError) as exc:
        # A build that cannot assemble a consistent candidate is reported, not
        # fatal; derive brings the stale generation up to date (D-0187). It is
        # still a failure (D-0199).
        tick.summary["build_error"] = f"{type(exc).__name__}: {exc}"
        log("build_failed", error=str(exc)[:300])
        return
    if (tick.summary.get("verdict") or {}).get("state") == "refused":
        # Not a failure: the candidate stays and nothing is submitted (D-0225).
        tick.counters["refused"] += 1
    if candidate.reused:
        tick.counters["reused"] += 1
    else:
        tick.counters["builds"] += 1
        tick.progress()
    if (tick.summary.get("publication") or {}).get("commit"):
        tick.counters["publications"] += 1
        tick.progress()
