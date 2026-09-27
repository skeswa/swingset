"""The maintain loop: daily housekeeping of the shared state (D-0215).

It checkpoints the WAL without waiting for readers, prunes guard rows
nobody has seen for a week, the run records of idle loop ticks (and stop
markers) after 14 days and of every loop tick after 30, counts building
generations nothing is finishing and partial generations nothing names,
compacts loop `runs` rows older than 30 days, drops queue rows whose unit
has nothing left to do, and reports the size cap and the controls. Each task done is one unit of progress.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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
    from swingset.state.derivations import incomplete_generations

    # Partial generations whose building row is gone: nothing collects them
    # until phase 4, so the count is watched ([incomplete-generations]).
    summary["incomplete_generations"] = incomplete_generations(conn)
    tick.progress()
    if tick.stop():
        return
    summary["queue_orphans"] = queue.collect_orphans(database, now=now)
    tick.progress()
    from swingset.state.controls import status
    from swingset.state.retention import enforce_size_cap

    summary["retention_cap"] = enforce_size_cap(
        database.state_dir,
        max_database_bytes=tick.bundle.config.retention.max_database_bytes,
        now=clock.now(),
    )
    summary["controls"] = status(conn, now=clock.now())
    summary["guard_reasons"] = guards.counts_by_reason(conn)
    tick.progress()


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
