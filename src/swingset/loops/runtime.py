"""One loop tick: a shared open, a run record, per-loop recovery and contexts.

Each loop (`acquire`, `plan`, `derive`, `release`, `maintain`) is a short
systemd tick that holds `state.lock` shared and its own `loop-<name>.lock`
exclusive (D-0215). `tick` opens the state, recovers the loop's own dead
runs, captures the input bundle, sets the tick's contexts, and on the way out
writes `runs/<run_id>.json`, the `runs` row and the `loop_state` row.

A publication in flight fences every semantic write (F6), `runs` and
`loop_state` included, so a tick that finds one writes nothing to the
database: it skips, and its record lives only in `runs/<run_id>.json`.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from swingset.clock import Clock
from swingset.fetch.archive import canonical, durable_write
from swingset.log import log
from swingset.model.ids import run_id as make_run_id
from swingset.state import loop_state, queue, write_deadline
from swingset.state.db import Database, open_for_loop, reset_write_counters
from swingset.state.inputs import InputBundle

if TYPE_CHECKING:
    from swingset.publish.service import Hub

LOOPS = ("acquire", "plan", "derive", "release", "maintain")
#: Only derive accepts a new input bundle; plan and release wait for it.
ACCEPTS_INPUTS = frozenset({"derive"})
NEEDS_ACCEPTED = frozenset({"plan", "release"})
#: Loops whose units answer the pacing gate (schedule/pacing.holds).
PACED = frozenset({"plan", "derive"})
#: Loops whose phase-two year gate reads facts only, no derivation predicate
#: (D-0218): acquire and plan fetch and plan by it, and derive's parse
#: writers insert archive watches by it, so all three agree.
FACTS_ONLY = frozenset({"acquire", "plan", "derive"})
PUBLICATION_IN_FLIGHT = "publication_in_flight"
INPUTS_NOT_ACCEPTED = "inputs_not_accepted"


def publication_active(conn: sqlite3.Connection) -> bool:
    """Whether a publication admission is active, so its fence stops writes (F6)."""
    return (
        conn.execute(
            "SELECT 1 FROM execution_admissions WHERE state='active' "
            "AND action_kind IN ('publish','publication') LIMIT 1"
        ).fetchone()
        is not None
    )


@dataclass
class Tick:
    """What a loop body gets: the open state, its run and its clock."""

    loop: str
    database: Database
    run_id: str
    clock: Clock
    started: datetime
    deadline: datetime
    should_stop: Callable[[], bool]
    timer: bool = False
    transport: httpx.BaseTransport | None = None
    hub: Hub | None = None
    #: False only for a release tick that publishes (`--publish`).
    dry_run: bool = True
    bundle: InputBundle | None = None
    #: Set before the body runs when the tick must do nothing.
    skipped: str | None = None
    failed: bool = False
    counters: Counter[str] = field(default_factory=Counter)
    #: Loop-specific keys for the run record.
    summary: dict[str, Any] = field(default_factory=dict)
    #: The written run record, once the tick has ended.
    record: dict[str, Any] | None = None

    def stop(self) -> bool:
        return self.should_stop() or self.clock.now() >= self.deadline

    def remaining(self) -> float:
        return max(0.0, (self.deadline - self.clock.now()).total_seconds())

    def progress(self, n: int = 1) -> None:
        """Count work done; a tick that moved it sets `last_progress_at`."""
        self.counters["progress"] += n


def note_skip(tick: Tick, reason: str, *, skipped: bool) -> None:
    """Keep the `loop:<name>` guard for a whole-tick skip, with its expiry (D-0219).

    A skip records or refreshes the row; a tick past the reason deletes it.
    Housekeeping: a write that fails is noted in the summary, never fatal.
    """
    from swingset.state import guards

    database = tick.database
    try:
        if not guards.available(database.connection):
            return
        if skipped:
            with database.transaction() as conn:
                guards.loop_skip(conn, tick.loop, reason, now=tick.clock.now())
        elif guards.get(database.connection, f"loop:{tick.loop}", reason) is not None:
            with database.transaction() as conn:
                guards.loop_ran(conn, tick.loop, reason)
    except (sqlite3.Error, write_deadline.WriteDeadlineExceeded) as exc:
        tick.summary["guard_error"] = f"{type(exc).__name__}: {exc}"[:200]


def peak_rss_bytes() -> int | None:
    """This process's peak resident set, in bytes (the tick's, one process per tick)."""
    try:
        import resource
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError):
        return None
    # Linux reports kibibytes, macOS bytes.
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


def _settle_dead_admissions(database: Database, loop: str, *, now: datetime) -> int:
    """Settle the active admissions of this loop's open runs, which are dead.

    This process holds the loop's lock, so no run of the loop is alive. The
    admission tables are control tables, never fenced, so this runs even
    while a publication is in flight. A publication admission carries no
    run; only release publishes among the loops, so release also settles an
    active one as uncertain, as `recover_admissions` does.
    """
    from swingset.state.controls import settle

    conn = database.connection
    rows = conn.execute(
        "SELECT a.action_id,a.action_kind,w.outcome AS work_outcome "
        "FROM execution_admissions a JOIN runs r ON r.run_id=a.run_id "
        "LEFT JOIN work_attempts w ON w.attempt_id=a.work_attempt_id "
        "WHERE r.loop=? AND r.finished_at IS NULL AND a.state='active'",
        (loop,),
    ).fetchall()
    if loop == "release":
        rows += conn.execute(
            "SELECT action_id,action_kind,NULL AS work_outcome FROM execution_admissions "
            "WHERE state='active' AND action_kind IN ('publish','publication')"
        ).fetchall()
    if not rows:
        return 0
    with database.transaction() as write:
        for row in rows:
            publication = row["action_kind"] in {"publish", "publication"}
            outcome = (
                row["work_outcome"]
                if row["work_outcome"] not in {None, "running"}
                else "process_interrupted"
            )
            settle(
                write,
                row["action_id"],
                now=now,
                outcome="receipt_reconciliation_required" if publication else outcome,
                uncertain=publication,
            )
    return len(rows)


def _recover_dead_runs(
    database: Database, loop: str, run_id: str, *, now: datetime
) -> dict[str, int]:
    """Close this loop's other open runs, inside the caller's transaction.

    A run whose record file says it finished (its last row write met a
    publication fence) gets that record; any other is failed with
    `process_interrupted`. Its running attempts are finished `interrupted`,
    each a counted `process_interrupted` failure of its unit, and a lease it
    holds with no running attempt (a death in the unit's admission) counts
    the same, as a takeover would do (D-0216). Nothing is counted when the
    run ended on its own or was asked to stop first (`queue.stop_marker`);
    its leases are released.
    """
    from swingset.state.work import WorkUnit

    conn = database.connection
    counts = {"runs": 0, "completed_from_record": 0, "attempts": 0, "leases": 0}
    dead = conn.execute(
        "SELECT run_id,summary_json FROM runs WHERE loop=? AND finished_at IS NULL AND run_id<>?",
        (loop, run_id),
    ).fetchall()
    for row in dead:
        dead_id = str(row["run_id"])
        path = database.state_dir / "runs" / f"{dead_id}.json"
        record: dict[str, Any] | None = None
        try:
            record = json.loads(path.read_bytes()) if path.is_file() else None
        except (OSError, ValueError):
            record = None
        # A run that wrote its record ended on its own; an attempt it left
        # running met a fence or a busy timeout. Any other run died. A record
        # a recovery wrote before its transaction rolled back is no ending.
        ended = (
            record is not None
            and bool(record.get("finished_at"))
            and record.get("error") != queue.INTERRUPTED_REASON
        )
        # A run asked to stop (a deploy) that outlasted its stop timeout did
        # not die of its unit: nothing it held is counted.
        counted = not ended and not queue.run_stopped(database, dead_id)
        if record is not None and ended:
            counts["completed_from_record"] += 1
        else:
            record = json.loads(row["summary_json"]) if row["summary_json"] else {}
            record.update(
                run_id=dead_id,
                loop=loop,
                failed=True,
                error="process_interrupted",
                finished_at=now.isoformat(),
            )
            durable_write(path, canonical(record))
        conn.execute(
            "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
            (record["finished_at"], json.dumps(record, sort_keys=True), dead_id),
        )
        counts["runs"] += 1
        for attempt in conn.execute(
            "SELECT * FROM work_attempts WHERE run_id=? AND outcome='running' ORDER BY attempt_id",
            (dead_id,),
        ).fetchall():
            # A death counts as a failure of the unit, so a unit that kills
            # its process backs off and quarantines (D-0216).
            failure = queue.interrupted(database, attempt, now=now, died=counted)
            counts["attempts"] += 1
            if failure.quarantined:
                counts["quarantined"] = counts.get("quarantined", 0) + 1
        for lease in conn.execute(
            "SELECT stage,unit_kind,unit_id FROM work_queue WHERE lease_owner=?",
            (f"{loop}:{dead_id}",),
        ).fetchall():
            unit = WorkUnit(*lease)
            row = queue.get(conn, unit)
            if counted and row is not None:
                # No running attempt is left, so the run died in the unit's
                # admission, between its claim and its attempt (derive_one).
                failure = queue.died_unclaimed(database, row, now=now)
                counts["admissions"] = counts.get("admissions", 0) + 1
                if failure.quarantined:
                    counts["quarantined"] = counts.get("quarantined", 0) + 1
            else:
                queue.release(conn, unit, now)
            counts["leases"] += 1
    return counts


def _finish(
    tick: Tick, *, fired_before: int, refused: Counter[str] | None, error: str | None
) -> dict[str, Any]:
    """Write the run record, the `runs` row and `loop_state`; return the record."""
    database, clock = tick.database, tick.clock
    finished = clock.now()
    summary: dict[str, Any] = dict(tick.summary)
    summary.update(
        loop=tick.loop,
        run_id=tick.run_id,
        started_at=tick.started.isoformat(),
        counters=dict(sorted(tick.counters.items())),
        progress=tick.counters.get("progress", 0),
        failed=tick.failed or error is not None,
        error=error,
        stopped=tick.stop(),
        skipped=tick.skipped,
        duration_seconds=(finished - tick.started).total_seconds(),
        control_timeouts=tick.summary.get("control_timeouts", 0),
        peak_rss_bytes=peak_rss_bytes(),
        finished_at=finished.isoformat(),
    )
    summary.update(write_deadline.run_counters(fired_before))
    if refused is not None:
        summary["phase_two_refusals"] = dict(sorted(refused.items()))
    durable_write(database.state_dir / "runs" / f"{tick.run_id}.json", canonical(summary))
    if tick.skipped == PUBLICATION_IN_FLIGHT:
        # No `runs` row was started: the fence stops every write (F6).
        log("loop", **summary)
        tick.record = summary
        return summary
    try:
        with database.transaction() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
                (summary["finished_at"], json.dumps(summary, sort_keys=True), tick.run_id),
            )
            previous = loop_state.get(conn, tick.loop)
            loop_state.upsert(
                conn,
                loop_state.LoopStateRow(
                    loop=tick.loop,  # type: ignore[arg-type]
                    updated_at=summary["finished_at"],
                    run_id=tick.run_id,
                    started_at=summary["started_at"],
                    finished_at=summary["finished_at"],
                    failed=int(summary["failed"]),
                    error=error,
                    counters_json=json.dumps(summary["counters"], sort_keys=True),
                    last_progress_at=(
                        summary["finished_at"]
                        if summary["progress"]
                        else previous.last_progress_at
                        if previous is not None
                        else None
                    ),
                ),
            )
    except (sqlite3.Error, write_deadline.WriteDeadlineExceeded) as exc:
        # The record file stands; the loop's next tick copies it into the row.
        summary["record_error"] = f"{type(exc).__name__}: {exc}"[:200]
        log("loop_record_failed", loop=tick.loop, run_id=tick.run_id, error=str(exc)[:300])
    log("loop", **summary)
    tick.record = summary
    return summary


@contextmanager
def tick(
    name: str,
    *,
    state: Path,
    config_dir: Path,
    overrides_dir: Path,
    clock: Clock,
    budget: float,
    should_stop: Callable[[], bool] = lambda: False,
    timer: bool = False,
    transport: httpx.BaseTransport | None = None,
    hub: Hub | None = None,
    dry_run: bool = True,
) -> Iterator[Tick]:
    """Run one tick of loop `name`; the body checks `tick.skipped` first.

    `DatabaseLockedError` escapes before anything is written: the loop is
    already running, a writer holds or waits for the state, or a migration
    is pending elsewhere. The caller skips the tick.
    """
    if name not in LOOPS:
        raise ValueError(f"unknown loop: {name}")
    database = open_for_loop(state, name, clock=clock)
    try:
        started = clock.now()
        deadline = started + timedelta(seconds=budget)
        # The run record carries this tick's write timings (D-0217).
        reset_write_counters()
        fired_before = write_deadline.fired()
        settled = _settle_dead_admissions(database, name, now=started)

        def make(run: str, **extra: Any) -> Tick:
            return Tick(
                loop=name,
                database=database,
                run_id=run,
                clock=clock,
                started=started,
                deadline=deadline,
                should_stop=should_stop,
                timer=timer,
                transport=transport,
                hub=hub,
                dry_run=dry_run,
                **extra,
            )

        fenced = publication_active(database.connection)
        if not fenced:
            try:
                with database.transaction() as conn:
                    run_id = database.start_run(started, dry_run=dry_run)
                    conn.execute("UPDATE runs SET loop=? WHERE run_id=?", (name, run_id))
                    recovered = _recover_dead_runs(database, name, run_id, now=started)
            except sqlite3.DatabaseError as exc:
                # A publication admitted since the check fences the run's start.
                if "active publication fences" not in str(exc):
                    raise
                fenced = True
        if fenced:
            skipped = make(f"{make_run_id(started)}-{name}", skipped=PUBLICATION_IN_FLIGHT)
            try:
                yield skipped
            finally:
                _finish(skipped, fired_before=fired_before, refused=None, error=None)
            return
        current = make(run_id)
        current.summary["recovered"] = {"admissions": settled, **recovered}
        error: str | None = None
        refused: Counter[str] | None = None
        scoped = ExitStack()
        try:
            from swingset.history.acquisition import phase_two_refusals
            from swingset.schedule import pacing
            from swingset.state.inputs import accept, capture
            from swingset.versions import versions

            refused = scoped.enter_context(phase_two_refusals())
            scoped.enter_context(queue.owning(name, run_id))
            if name in FACTS_ONLY:
                from swingset.project.history import facts_only_year_gate

                scoped.enter_context(facts_only_year_gate())
            bundle = capture(config_dir, overrides_dir, database.state_dir, versions())
            current.bundle = bundle
            if name in PACED:
                scoped.enter_context(pacing.configured(bundle.config.scheduler, clock.now))
            if name in ACCEPTS_INPUTS:
                current.summary["accepted_inputs"] = sorted(accept(database, bundle, clock))
            elif name in NEEDS_ACCEPTED:
                row = database.connection.execute(
                    "SELECT value FROM meta WHERE key='input_bundle_hash'"
                ).fetchone()
                if row is None or str(row[0]) != bundle.digest:
                    current.skipped = INPUTS_NOT_ACCEPTED
                note_skip(current, INPUTS_NOT_ACCEPTED, skipped=current.skipped is not None)
            yield current
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            scoped.close()
            _finish(current, fired_before=fired_before, refused=refused, error=error)
    finally:
        database.close()
