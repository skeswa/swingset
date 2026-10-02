"""The maintain loop: daily housekeeping of the shared state (D-0215).

It checkpoints the WAL without waiting for readers, prunes guard rows
nobody has seen for a week, the run records of idle loop ticks (and stop
markers) after 14 days and of every loop tick after 30, counts building
generations nothing is finishing and partial generations nothing names,
compacts loop `runs` rows older than 30 days, drops queue rows whose unit
has nothing left to do, and removes partition artifacts nothing has named
for a day. It reports the size cap and the controls, then runs the storage
tasks of phase 4 (`[storage]` in sources.toml): an eviction batch
(`storage.evict`), a body-packing batch (`storage.pack_bodies`), and the
collector's full sweep when due (`collector.sweep`, weekly). Once a week it
re-derives a sample of current link and year units and compares their output
(`state/verify`, summary key `verify`); once a week, when the budget left
allows, it restores the newest backup set into scratch and checks it
(`backup/drill`, summary key `drill`), before verify once it is a day overdue.
Each of these tasks is isolated: one that raises is recorded as
`{failed, error}` and the tick goes on (I1). Each task done is one unit of
progress, and each checks `tick.stop()` first.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from swingset.loops.runtime import LOOPS, Tick
from swingset.state import guards, queue

#: A guard row not seen for this long is dropped; its reason has gone.
GUARD_UNSEEN = timedelta(days=7)
#: Idle loop-tick records older than this leave `runs/`; their rows stay.
IDLE_RECORD_AGE = timedelta(days=14)
#: Every loop-tick record leaves `runs/` at this age, so the directory the
#: watchdog reads stays bounded (about 600 ticks a day); the rows stay.
RECORD_AGE = timedelta(days=30)
#: A building generation untouched for this long is an orphan.
BUILDING_STALE = timedelta(hours=24)
#: A weekly task is due this much before a whole interval, so a tick that
#: starts a few minutes earlier than last week's still runs it.
DUE_SLACK = timedelta(hours=12)
#: The restore drill starts only with this much of the tick's budget left:
#: it cannot stop part way, and the unit's timeout is 30 minutes past it.
DRILL_MIN_REMAINING = 1800.0


def run(tick: Tick) -> None:
    if tick.skipped:
        return
    database, clock, summary = tick.database, tick.clock, tick.summary
    assert tick.bundle is not None
    conn = database.connection
    now = clock.now()
    # PASSIVE never waits for a reader or writer; it copies what it can.
    busy, frames, copied = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    wal = database.state_dir / "state.sqlite-wal"
    summary["wal"] = {
        "busy": int(busy),
        "frames": int(frames),
        "checkpointed": int(copied),
        "bytes": wal.stat().st_size if wal.is_file() else 0,
    }
    tick.progress()
    if tick.stop():
        return
    with database.transaction() as write:
        # A `request_error` row keeps its failure count until a week past
        # its expiry, so a watch held a week is held a week again (D-0219).
        unseen = queue.at(now - GUARD_UNSEEN)
        pruned = write.execute(
            "DELETE FROM guard_reasons WHERE last_seen_at < ? AND (reason<>? OR expires_at < ?)",
            (unseen, guards.REQUEST_ERROR, unseen),
        ).rowcount
    summary["guards_pruned"] = int(pruned)
    tick.progress()
    if tick.stop():
        return
    summary["records_pruned"] = prune_idle_records(database.state_dir / "runs", now=now)
    with database.transaction() as write:
        summary["run_rows_compacted"] = compact_run_rows(write, now=now)
    tick.progress()
    stale = queue.at(now - BUILDING_STALE)
    rows, orphans = conn.execute(
        "SELECT count(*),coalesce(sum(updated_at < ?),0) FROM derivation_building", (stale,)
    ).fetchone()
    summary["building"] = {"rows": int(rows), "stale": int(orphans)}
    from swingset.state.derivations import holding_generations, incomplete_generations

    # Partial generations whose building row is gone, evicted ones not
    # counted. The weekly collector removes the references of those older
    # than a day and leaves their labels, so the watchdog's
    # [incomplete-generations] reads `holding`: those that still hold some.
    counts = incomplete_generations(conn)
    summary["incomplete_generations"] = {
        **counts,
        "holding": holding_generations(conn) if counts["rows"] else 0,
    }
    tick.progress()
    if tick.stop():
        return
    summary["queue_orphans"] = queue.collect_orphans(database, now=now)
    tick.progress()
    if tick.stop():
        return
    from swingset.state.controls import status
    from swingset.state.retention import enforce_size_cap

    # The size cap and the controls come before the storage tasks, so a
    # storage task that runs long or fails never hides a pause.
    summary["retention_cap"] = enforce_size_cap(
        database.state_dir,
        max_database_bytes=tick.bundle.config.retention.max_database_bytes,
        now=clock.now(),
    )
    summary["controls"] = status(conn, now=clock.now())
    summary["guard_reasons"] = guards.counts_by_reason(conn)
    tick.progress()
    if tick.stop():
        return
    from swingset.state import collector, storage

    # Each storage task is isolated (I1): one that raises is recorded as
    # `{failed, error}` and the next runs. The watchdog reads each one's line.
    # Partition artifacts no current partition and no retained release
    # manifest has named for a day, and crashed writes (phase 3, WP6;
    # the collector's two classes since phase 4, WP5).
    summary["artifacts"] = isolated(
        database, lambda: collector.collect_artifacts(database, now=now)
    )
    tick.progress()
    if tick.stop():
        return
    from swingset.backup.segments import clean_abandoned

    # A killed backup's scratch (a whole database copy) and half-written
    # segments, older than a day (phase 4).
    summary["backup_scratch"] = isolated(
        database, lambda: clean_abandoned(database.state_dir / "checkpoints", now=now)
    )
    policy = tick.bundle.config.storage
    # Old cold generations' references into packs (D-0229), bounded per tick.
    summary["evict"] = isolated(
        database,
        lambda: storage.evict(
            database, now=clock.now(), budget_refs=policy.evict_refs_per_tick, stop=tick.stop
        ),
    )
    tick.progress()
    if tick.stop():
        return
    # Parse bodies and large build recipes no reader protects any more.
    summary["pack_bodies"] = isolated(
        database,
        lambda: storage.pack_bodies(
            database, now=clock.now(), budget_rows=policy.pack_rows_per_tick, stop=tick.stop
        ),
    )
    tick.progress()
    if tick.stop():
        return
    if collector_due(conn, now=clock.now(), every_days=policy.collect_every_days):
        summary["collector"] = sweep(database, now=clock.now(), stop=tick.stop)
        tick.progress()
        if tick.stop():
            return
    from swingset.backup import drill
    from swingset.state import verify

    # The longest steps, and the only ones that may run for minutes. Each
    # verify unit is its own write, rolled back; a mismatch is
    # `[read-set-miss]`. A weekly restore drill of the newest backup set
    # (D-0232) runs when the budget left allows it, after verify, unless it
    # is overdue: then it goes first, so a verify that never completes cannot
    # starve it. A failure is `[restore-drill-failed]`; each is isolated.
    drill_due = drill.due(conn, now=clock.now(), every_days=policy.drill_every_days)
    first = drill_due and drill.overdue(conn, now=clock.now(), every_days=policy.drill_every_days)

    def restore_drill() -> None:
        if tick.remaining() < DRILL_MIN_REMAINING:
            summary["drill"] = {"skipped": "short_budget"}
            return
        summary["drill"] = isolated(
            database,
            lambda: drill.run(database, now=clock.now(), drill_dir=Path(policy.drill_dir)),
        )
        tick.progress()

    if first:
        restore_drill()
        if tick.stop():
            return
    bundle = tick.bundle
    if verify.due(conn, now=clock.now()):
        summary["verify"] = isolated(
            database,
            lambda: verify.run(database, bundle, clock, tick.run_id, stop=tick.stop),
        )
        tick.progress()
    if tick.stop():
        return
    if drill_due and not first:
        restore_drill()


#: What a storage task's failure is: anything but a stop of the process.
TASK_ERRORS = (RuntimeError, ValueError, KeyError, TypeError, OSError, sqlite3.Error)


def isolated(database: Any, task: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one storage task; one that raises is `{failed: true, error}`, not raised.

    Each task writes in its own short transactions, which roll back on an
    error, so the next task starts from a consistent state. A task's own
    `failed` counts the items it isolated; `error` says the task itself
    stopped (`[storage-failed]`).
    """
    try:
        return task()
    except TASK_ERRORS as error:
        if database.connection.in_transaction:
            database.connection.rollback()
        return {"failed": True, "error": f"{type(error).__name__}: {error}"[:300]}


def collector_due(conn: sqlite3.Connection, *, now: datetime, every_days: int) -> bool:
    """Whether no maintain tick recorded a complete collector sweep within
    `every_days` (less `DUE_SLACK`). A sweep cut short or failed does not
    count: the next tick sweeps again."""
    since = (now - (timedelta(days=every_days) - DUE_SLACK)).astimezone(UTC).isoformat()
    row = conn.execute(
        "SELECT 1 FROM runs WHERE loop='maintain' AND finished_at > ? "
        "AND json_valid(summary_json) "
        "AND json_extract(summary_json,'$.collector.complete') = 1 LIMIT 1",
        (since,),
    ).fetchone()
    return row is None


def sweep(database: Any, *, now: datetime, stop: Callable[[], bool]) -> dict[str, Any]:
    """The collector's full sweep (D-0231), as the summary records it.

    A sweep that raises is recorded as failed, not raised, so the rest of the
    tick still runs; the watchdog reports it as `[collector-failed]`.
    """
    from swingset.state import collector

    try:
        result = collector.sweep(database, now=now, stop=stop)
    except TASK_ERRORS as error:
        if database.connection.in_transaction:
            database.connection.rollback()
        return {
            "failed": True,
            "complete": False,
            "error": f"{type(error).__name__}: {error}"[:300],
        }
    classes = result.get("classes") or {}
    result["complete"] = not any(
        isinstance(entry, dict) and entry.get("stopped") for entry in classes.values()
    )
    # A class that raised was isolated (`collector.CLASS_ERRORS`); the sweep
    # ran the rest, and the tick reports it as `[collector-failed]`.
    raised = [
        f"{cls}: {entry['stopped'].removeprefix('error: ')}"
        for cls, entry in classes.items()
        if isinstance(entry, dict) and str(entry.get("stopped") or "").startswith("error: ")
    ]
    result["failed"] = bool(raised)
    if raised:
        result["error"] = "; ".join(raised)[:300]
    return result


#: Loop `runs` rows compacted per maintain tick, so one write stays short.
COMPACT_BATCH = 5000


def compact_run_rows(conn: sqlite3.Connection, *, now: datetime) -> int:
    """Shrink the summaries of loop `runs` rows older than 30 days to a stub (I6).

    About 600 loop rows a day would otherwise keep a whole summary each
    forever. The stub keeps the loop, whether it failed, its progress and
    when it finished; cycle and command rows are kept whole.
    """
    return int(
        conn.execute(
            "UPDATE runs SET summary_json=json_object('loop',loop,'failed',"
            "json_extract(summary_json,'$.failed'),'progress',"
            "json_extract(summary_json,'$.progress'),'finished_at',finished_at,"
            "'compacted',json('true')) WHERE rowid IN (SELECT rowid FROM runs "
            "WHERE loop IS NOT NULL AND finished_at IS NOT NULL AND finished_at < ? "
            "AND json_valid(summary_json) AND json_extract(summary_json,'$.compacted') IS NULL "
            "LIMIT ?)",
            ((now - RECORD_AGE).isoformat(), COMPACT_BATCH),
        ).rowcount
    )


def prune_idle_records(runs: Path, *, now: datetime) -> int:
    """Delete loop-tick records older than 14 days that did no work and did not fail,
    every loop-tick record older than 30 days, and stop markers older than 14 days.

    Cycle and command records carry no loop and are kept, as is every `runs` row.
    """
    if not runs.is_dir():
        return 0
    cutoff = now - IDLE_RECORD_AGE
    oldest = now - RECORD_AGE
    removed = 0
    for path in runs.glob("run_*.json"):
        try:
            record = json.loads(path.read_bytes())
            finished = datetime.fromisoformat(str(record["finished_at"]))
            idle = record.get("loop") in LOOPS and (
                finished < oldest
                or (not record.get("failed") and not record.get("progress") and finished < cutoff)
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if idle:
            path.unlink(missing_ok=True)
            removed += 1
    # Stop markers (`queue.stop_marker`) matter only to the next tick's
    # recovery; they leave with the idle records.
    for path in runs.glob("run_*.stop"):
        try:
            old = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo) < cutoff
        except OSError:
            continue
        if old:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
