"""The work queue overlay: leases, failure clocks, waiting and quarantine.

What needs doing stays `pending_work` plus derivation currency. A
`work_queue` row exists only while a unit is leased or failing, and is
deleted when the unit succeeds (migration 34, D-0216). `derive_one` leases a
unit in its admission transaction and records each outcome here; `wake` turns
due waiting and quarantined rows ready; `release_all` is the exclusive
process's global recovery.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import astuple, dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .work import WorkUnit

if TYPE_CHECKING:
    from .db import Database

QueueState = Literal["ready", "leased", "waiting_input", "quarantined"]
STATES: tuple[QueueState, ...] = ("ready", "leased", "waiting_input", "quarantined")

#: Failure reasons that mean "no input can make this pass yet": the unit waits
#: on a clock and is never quarantined.
WAITING_REASONS = frozenset(
    {
        "artifact_unavailable",
        "parse_failed",
        "admission_needs_review",
        "admission_waiting_for_inputs",
        "unsupported_page_kind",
    }
)
IO_REASON = "unit_io_error"
UNSUPPORTED_REASON = "unsupported_page_kind"
#: A write that met an active publication's fence: retried soon, never counted.
FENCED_REASON = "publication_fenced"
#: A write whose `BEGIN IMMEDIATE` outwaited the busy timeout behind another
#: process: retried soon, never counted. Contention is not the unit's fault.
LOCK_REASON = "write_lock_timeout"
#: Reasons that keep the unit's run of failures as it was.
UNCOUNTED_REASONS = frozenset({FENCED_REASON, LOCK_REASON})
#: The unit's process died inside an attempt (a kill, an OOM, a timeout): a
#: failure class of its own, so a unit that kills its process quarantines.
INTERRUPTED_REASON = "process_interrupted"
#: Why `wake` asks for a retry of a blocked or unavailable unit.
WAKE_REASON = "waiting_input_clock"


@dataclass(frozen=True)
class Policy:
    """Queue clocks and thresholds. Defaults are production's; tests override."""

    lease_seconds: float = 1800.0
    quarantine_after: int = 3
    io_quarantine_after: int = 20
    quarantine_retry: timedelta = timedelta(days=7)
    waiting_first: timedelta = timedelta(hours=24)
    waiting_longest: timedelta = timedelta(days=7)
    backoff_first: timedelta = timedelta(minutes=15)
    backoff_longest: timedelta = timedelta(hours=6)
    io_retry: timedelta = timedelta(seconds=60)
    unsupported_wait: timedelta = timedelta(days=30)
    fenced_retry: timedelta = timedelta(seconds=60)
    #: A running attempt whose lease died is closed as interrupted with this retry.
    takeover_retry: timedelta = timedelta(seconds=1)

    def quarantine_threshold(self, error_class: str | None) -> int:
        """Consecutive failures of one class that quarantine a unit."""
        return self.io_quarantine_after if error_class == IO_REASON else self.quarantine_after


@dataclass(frozen=True)
class QueueRow:
    stage: str
    unit_kind: str
    unit_id: str
    partition: str
    state: QueueState
    attempts: int
    error_class: str | None
    input_fingerprint: str | None
    lease_owner: str | None
    lease_until: str | None
    next_attempt_at: str | None
    last_reason: str | None
    last_error: str | None
    enqueued_at: str
    updated_at: str
    quarantined_at: str | None

    @property
    def unit(self) -> WorkUnit:
        return WorkUnit(self.stage, self.unit_kind, self.unit_id)


_COLUMNS = tuple(field.name for field in fields(QueueRow))
_KEY = ("stage", "unit_kind", "unit_id")


def at(moment: datetime) -> str:
    """A queue timestamp: UTC, fixed width, so text order is time order."""
    if moment.tzinfo is None:
        raise ValueError("queue times must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def partition_of(unit: WorkUnit) -> str:
    """The unit's partition: `<unit_kind>/<unit_id>`."""
    return f"{unit.unit_kind}/{unit.unit_id}"


def error_class(reason_code: str | None, evidence_json: str | None = None) -> str | None:
    """A failure's class: its reason, plus the exception type for `unit_exception`."""
    if reason_code != "unit_exception":
        return reason_code
    try:
        evidence = json.loads(evidence_json or "{}")
    except ValueError:
        return reason_code
    exception = evidence.get("exception") if isinstance(evidence, dict) else None
    return f"{reason_code}:{exception}" if isinstance(exception, str) and exception else reason_code


def get(conn: sqlite3.Connection, unit: WorkUnit) -> QueueRow | None:
    row = conn.execute(
        f"SELECT {','.join(_COLUMNS)} FROM work_queue WHERE stage=? AND unit_kind=? AND unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
    ).fetchone()
    return None if row is None else QueueRow(*row)


def upsert(conn: sqlite3.Connection, row: QueueRow) -> None:
    updates = ",".join(f"{name}=excluded.{name}" for name in _COLUMNS if name not in _KEY)
    conn.execute(
        f"INSERT INTO work_queue({','.join(_COLUMNS)}) VALUES ({','.join('?' * len(_COLUMNS))}) "
        f"ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET {updates}",
        astuple(row),
    )


def delete(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    cursor = conn.execute(
        "DELETE FROM work_queue WHERE stage=? AND unit_kind=? AND unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
    )
    return cursor.rowcount > 0


def counts_by_state(conn: sqlite3.Connection) -> dict[str, int]:
    """Rows per state, every state present."""
    counts: dict[str, int] = dict.fromkeys(STATES, 0)
    for state, count in conn.execute("SELECT state,count(*) FROM work_queue GROUP BY state"):
        counts[str(state)] = int(count)
    return counts


def counts_by_class(conn: sqlite3.Connection, state: QueueState | None = None) -> dict[str, int]:
    """Rows per error class, optionally in one state; no class counts as ''."""
    query = "SELECT coalesce(error_class,''),count(*) FROM work_queue"
    arguments: tuple[str, ...] = ()
    if state is not None:
        query += " WHERE state=?"
        arguments = (state,)
    return {
        str(name): int(count)
        for name, count in conn.execute(query + " GROUP BY 1 ORDER BY 1", arguments)
    }


def seed(conn: sqlite3.Connection, now: datetime, policy: Policy | None = None) -> dict[str, int]:
    """Give each failing pending unit its row; run once by migration 34.

    A unit whose latest attempt is blocked or unavailable, or parked as an
    unsupported page kind, waits for input on a clock of at least
    `policy.waiting_first`. A transient unit is ready at its retry time and
    keeps its run of same-class failures on the same fingerprint, capped one
    short of quarantine so the migration quarantines nothing. Units with no
    attempt, or whose latest attempt succeeded, is running, was interrupted
    or superseded, get no row. The walk is bounded by `pending_work`.
    """
    policy = policy or Policy()
    stamp = at(now)
    depth = max(policy.quarantine_after, policy.io_quarantine_after)
    seeded: dict[str, int] = dict.fromkeys(STATES, 0)
    pending = conn.execute(
        "SELECT stage,unit_kind,unit_id,enqueued_at FROM pending_work "
        "WHERE stage IN ('parse','project','link') ORDER BY stage,unit_kind,unit_id"
    ).fetchall()
    for stage, unit_kind, unit_id, enqueued_at in pending:
        attempts = conn.execute(
            "SELECT outcome,reason_code,evidence_json,retry_at,input_fingerprint "
            "FROM work_attempts WHERE stage=? AND unit_kind=? AND unit_id=? "
            "ORDER BY attempt_id DESC LIMIT ?",
            (stage, unit_kind, unit_id, depth),
        ).fetchall()
        if not attempts:
            continue
        outcome, reason, evidence, retry_at, fingerprint = attempts[0]
        if outcome not in {"blocked", "unavailable", "transient"}:
            continue
        failure = error_class(reason, evidence)
        retry = _parsed(retry_at)
        state: QueueState
        next_attempt: datetime | None
        if outcome != "transient" or reason in WAITING_REASONS:
            state = "waiting_input"
            count = 1
            next_attempt = max(retry or now, now + policy.waiting_first)
        else:
            state = "ready"
            run = 0
            for previous in attempts:
                if (
                    previous[0] != "transient"
                    or error_class(previous[1], previous[2]) != failure
                    or previous[4] != fingerprint
                ):
                    break
                run += 1
            count = min(run, policy.quarantine_threshold(failure) - 1)
            next_attempt = retry
        upsert(
            conn,
            QueueRow(
                stage=stage,
                unit_kind=unit_kind,
                unit_id=unit_id,
                partition=partition_of(WorkUnit(stage, unit_kind, unit_id)),
                state=state,
                attempts=count,
                error_class=failure,
                input_fingerprint=fingerprint,
                lease_owner=None,
                lease_until=None,
                next_attempt_at=None if next_attempt is None else at(next_attempt),
                last_reason=reason,
                last_error=_message(evidence),
                enqueued_at=enqueued_at,
                updated_at=stamp,
                quarantined_at=None,
            ),
        )
        seeded[state] += 1
    return seeded


def _parsed(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _message(evidence_json: str | None) -> str | None:
    try:
        evidence = json.loads(evidence_json or "{}")
    except ValueError:
        return None
    message = evidence.get("message") if isinstance(evidence, dict) else None
    return message[:500] if isinstance(message, str) and message else None


# Policy (D-0216). Tests replace DEFAULT with shorter clocks.
DEFAULT = Policy()


def backoff(attempts: int, policy: Policy | None = None) -> timedelta:
    """Delay after the `attempts`-th consecutive failure: 15 min doubling, at most 6 h."""
    policy = policy or DEFAULT
    delay: timedelta = policy.backoff_first * (2 ** max(0, attempts - 1))
    return min(delay, policy.backoff_longest)


def waiting_delay(attempts: int, policy: Policy | None = None) -> timedelta:
    """How long a unit waits for input after its `attempts`-th wait: 24 h doubling, at most 7 d."""
    policy = policy or DEFAULT
    delay: timedelta = policy.waiting_first * (2 ** max(0, attempts - 1))
    return min(delay, policy.waiting_longest)


@dataclass(frozen=True)
class Owner:
    """Who holds a lease: a loop (`cycle`, `cli` or a loop name) and its run."""

    loop: str
    run_id: str
    #: The process holds the exclusive state lock, so no other process runs.
    exclusive: bool = False

    @property
    def name(self) -> str:
        return f"{self.loop}:{self.run_id}"


_owner: ContextVar[Owner | None] = ContextVar("queue_owner", default=None)


@contextmanager
def owning(loop: str, run_id: str, *, exclusive: bool = False) -> Iterator[Owner]:
    """Lease units as `<loop>:<run_id>` inside this context."""
    owner = Owner(loop, run_id, exclusive)
    token = _owner.set(owner)
    try:
        yield owner
    finally:
        _owner.reset(token)


def current_owner(run_id: str, *, exclusive: bool = False) -> Owner:
    """The context's owner, or a CLI owner for `run_id` outside any context."""
    return _owner.get() or Owner("cli", run_id, exclusive)


def lease_dead(row: QueueRow, now: datetime, owner: Owner | None = None) -> bool:
    """Whether nothing alive holds this row's lease.

    A lease is dead once `lease_until` passes, when its owner is this
    process's own loop (the process holds that loop's lock, so the owner's
    run has ended), or when this process holds the exclusive lock.
    """
    if row.state != "leased" or row.lease_owner is None or row.lease_until is None:
        return True
    if owner is not None and (
        owner.exclusive
        or row.lease_owner == owner.name
        or row.lease_owner.split(":", 1)[0] == owner.loop
    ):
        return True
    return row.lease_until <= at(now)


def running_lease_dead(conn: sqlite3.Connection, unit: WorkUnit, now: datetime) -> bool:
    """Whether a unit's `running` attempt belongs to a dead lease (`attempts.eligible`).

    A running attempt with no leased row was begun outside the queue; only
    the exclusive process's recovery closes it, as before.
    """
    try:
        row = get(conn, unit)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False
        raise
    return row is not None and row.state == "leased" and lease_dead(row, now, _owner.get())


@dataclass(frozen=True)
class Lease:
    busy: bool = False
    #: A dead lease's running attempt was closed as interrupted.
    took_over: bool = False
    #: The takeover counted that death, so the unit waits on this failure's
    #: clock instead of running now; the row is not leased.
    held: Failure | None = None


def run_finished(database: Database, run_id: str | None) -> bool:
    """Whether a run ended on its own: its `runs` row or its record file is finished.

    A running attempt of a finished run was left by a write that met a
    publication fence or a busy timeout, not by the process dying. A tick
    whose row write met the fence finished only its record file.
    """
    if run_id is None:
        return False
    row = database.connection.execute(
        "SELECT finished_at FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is not None and row[0] is not None:
        return True
    try:
        record = json.loads((database.state_dir / "runs" / f"{run_id}.json").read_bytes())
    except (OSError, ValueError):
        return False
    # A recovery writes the dead run's record before its transaction commits;
    # if that rolled back, the record says `process_interrupted`: no ending.
    return (
        isinstance(record, dict)
        and bool(record.get("finished_at"))
        and record.get("error") != INTERRUPTED_REASON
    )


def stop_marker(state_dir: Path, run_id: str) -> Path:
    """The file a loop tick writes when systemd asks it to stop (SIGTERM).

    A tick killed after that request (a unit that outlasts `TimeoutStopSec`)
    did not die of its unit: its death is not counted.
    """
    return state_dir / "runs" / f"{run_id}.stop"


def run_stopped(database: Database, run_id: str | None) -> bool:
    """Whether a stop was requested of the run before it died (`stop_marker`)."""
    return run_id is not None and stop_marker(database.state_dir, run_id).is_file()


def death_counts(database: Database, run_id: str | None) -> bool:
    """Whether a dead run's open work counts as its unit's death.

    Not when the run ended on its own (an attempt left behind a fenced or
    busy write) or was asked to stop first (a deploy's stop timeout).
    """
    return not run_finished(database, run_id) and not run_stopped(database, run_id)


def owner_run(lease_owner: str | None) -> str | None:
    """The run id of a lease owner `<loop>:<run_id>`."""
    if not lease_owner or ":" not in lease_owner:
        return None
    return lease_owner.split(":", 1)[1]


def died_unclaimed(
    database: Database, row: QueueRow, *, now: datetime, policy: Policy | None = None
) -> Failure:
    """Count a dead run's lease with no running attempt as the unit's death.

    The run died between its claim and its attempt (`derive_one`'s
    admission: the capture, for example). Without this a unit that kills
    its process there would take every tick (I1, I3). The row's own
    fingerprint is kept: forming a new one could be what killed the process.
    Inside the caller's transaction.
    """
    return fail(
        database.connection,
        row.unit,
        reason=INTERRUPTED_REASON,
        fingerprint=row.input_fingerprint,
        now=now,
        error="the process died in the unit's admission",
        policy=policy,
    )


def interrupted(
    database: Database,
    attempt: sqlite3.Row,
    *,
    now: datetime,
    died: bool,
    policy: Policy | None = None,
) -> Failure:
    """Close a dead run's `running` attempt, inside the caller's transaction.

    ``died``: the run's process died inside the attempt (a kill, an OOM, a
    stop timeout). That is a failure of class `process_interrupted`: it backs
    off and quarantines at the third in a row, so a unit that kills its own
    process cannot take every tick (I1, I3). Otherwise the run ended on its
    own and left the attempt behind a fenced or busy write: the unit retries
    in a minute and nothing is counted.
    """
    from .attempts import Attempt, finish_attempt

    policy = policy or DEFAULT
    unit = WorkUnit(attempt["stage"], attempt["unit_kind"], attempt["unit_id"])
    failure = fail(
        database.connection,
        unit,
        reason=INTERRUPTED_REASON if died else FENCED_REASON,
        fingerprint=attempt["input_fingerprint"],
        now=now,
        policy=policy,
    )
    finish_attempt(
        database,
        Attempt(
            attempt["attempt_id"],
            unit,
            attempt["input_fingerprint"],
            attempt["work_token"],
            attempt["queue_generation"],
            attempt["retry_generation"],
            attempt["run_id"],
        ),
        outcome="interrupted",
        reason_code=INTERRUPTED_REASON,
        now=now,
        retry_at=max(failure.next_attempt_at, now + policy.takeover_retry),
    )
    return failure


def escaped(
    database: Database,
    unit: WorkUnit,
    run_id: str,
    exc: BaseException,
    *,
    now: datetime,
    policy: Policy | None = None,
) -> Failure | None:
    """Close the running attempt an escaped exception left, as the unit's failure.

    `derive_one` records every failure it can name. An exception that escapes
    it (a handler's own defect) leaves this run's attempt running; were it
    left, the next lease would close it as a run that ended on its own,
    uncounted, and the unit would never quarantine. It is a `unit_exception`
    of the escaping type instead, with its admission settled. Returns None
    when this run has no running attempt of the unit. Inside the caller's
    transaction.
    """
    from .attempts import Attempt, finish_attempt, latest_attempt
    from .controls import settle

    policy = policy or DEFAULT
    latest = latest_attempt(database.connection, unit)
    if latest is None or latest["outcome"] != "running" or latest["run_id"] != run_id:
        return None
    exception = type(exc).__name__
    failure = fail(
        database.connection,
        unit,
        reason="unit_exception",
        exception=exception,
        fingerprint=latest["input_fingerprint"],
        now=now,
        error=f"escaped: {exc}",
        policy=policy,
    )
    finish_attempt(
        database,
        Attempt(
            latest["attempt_id"],
            unit,
            latest["input_fingerprint"],
            latest["work_token"],
            latest["queue_generation"],
            latest["retry_generation"],
            latest["run_id"],
        ),
        outcome="transient",
        reason_code="unit_exception",
        now=now,
        retry_at=failure.next_attempt_at,
        evidence={"exception": exception, "message": str(exc)[:500]},
    )
    for (action_id,) in database.connection.execute(
        "SELECT action_id FROM execution_admissions WHERE work_attempt_id=? AND state='active'",
        (latest["attempt_id"],),
    ).fetchall():
        settle(database.connection, action_id, now=now, outcome="unit_exception")
    return failure


def lease(
    database: Database,
    unit: WorkUnit,
    *,
    owner: Owner,
    now: datetime,
    policy: Policy | None = None,
) -> Lease:
    """Lease `unit` inside the caller's admission transaction.

    A live lease of another owner leaves the unit alone (`busy`). A dead one
    is taken over: its running attempt is finished `interrupted` and its
    active admission settled in the same transaction. A death counts
    (`interrupted`), so the unit then waits on its failure clock (`held`).
    """
    from .attempts import latest_attempt
    from .controls import settle

    policy = policy or DEFAULT
    conn = database.connection
    if not conn.in_transaction:
        raise ValueError("a lease is taken inside the admission transaction")
    row = get(conn, unit)
    if row is not None and row.state == "leased":
        if row.lease_owner != owner.name and not lease_dead(row, now, owner):
            return Lease(busy=True)
        latest = latest_attempt(conn, unit)
        if latest is not None and latest["outcome"] == "running":
            failure = interrupted(
                database,
                latest,
                now=now,
                died=death_counts(database, latest["run_id"]),
                policy=policy,
            )
            for (action_id,) in conn.execute(
                "SELECT action_id FROM execution_admissions WHERE work_attempt_id=? "
                "AND state='active'",
                (latest["attempt_id"],),
            ).fetchall():
                settle(conn, action_id, now=now, outcome="process_interrupted")
            return Lease(took_over=True, held=failure)
        if row.lease_owner != owner.name and death_counts(database, owner_run(row.lease_owner)):
            # The owner died after its claim and before its attempt.
            return Lease(took_over=True, held=died_unclaimed(database, row, now=now, policy=policy))
    stamp = at(now)
    until = at(now + timedelta(seconds=policy.lease_seconds))
    if row is None:
        row = QueueRow(
            stage=unit.stage,
            unit_kind=unit.unit_kind,
            unit_id=unit.unit_id,
            partition=partition_of(unit),
            state="leased",
            attempts=0,
            error_class=None,
            input_fingerprint=None,
            lease_owner=owner.name,
            lease_until=until,
            next_attempt_at=None,
            last_reason=None,
            last_error=None,
            enqueued_at=stamp,
            updated_at=stamp,
            quarantined_at=None,
        )
    else:
        row = replace(
            row, state="leased", lease_owner=owner.name, lease_until=until, updated_at=stamp
        )
    upsert(conn, row)
    return Lease()


def holds(
    conn: sqlite3.Connection, unit: WorkUnit, now: datetime, fingerprint: Callable[[], str]
) -> bool:
    """Whether the unit's row keeps it out of selection now.

    Selection asks this when no attempt carries the clock: a failure before
    the attempt began (`derive_one`'s admission) writes only this row. A
    waiting or quarantined row, or a ready one not yet due, holds the unit
    while its inputs are the ones that failed; a row with no fingerprint
    holds it until its clock.
    """
    try:
        row = get(conn, unit)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False
        raise
    if row is None or row.state == "leased":
        return False
    if row.state == "ready" and (row.next_attempt_at is None or row.next_attempt_at <= at(now)):
        return False
    if row.input_fingerprint is None:
        return True
    try:
        return fingerprint() == row.input_fingerprint
    except Exception:
        return True


def collect_orphans(database: Database, *, now: datetime) -> int:
    """Delete rows whose unit has nothing left to do (I6); returns how many.

    A row outlives its unit when the unit stops being pending by another
    path: a superseded parse, a hint another pass dropped, a project or link
    unit that became current without running. Leased rows and units with a
    running attempt stay. A parse is pending by its `pending_work` row; a
    project or link unit also while it is not current.
    """
    from .derivations import available, current

    conn = database.connection
    rows = conn.execute(
        "SELECT q.stage,q.unit_kind,q.unit_id FROM work_queue q "
        "WHERE q.state<>'leased' AND NOT EXISTS (SELECT 1 FROM pending_work p "
        "WHERE p.stage=q.stage AND p.unit_kind=q.unit_kind AND p.unit_id=q.unit_id) "
        "AND NOT EXISTS (SELECT 1 FROM work_attempts a WHERE a.stage=q.stage "
        "AND a.unit_kind=q.unit_kind AND a.unit_id=q.unit_id AND a.outcome='running') "
        "ORDER BY q.stage,q.unit_kind,q.unit_id"
    ).fetchall()
    derived = available(conn)
    orphans = []
    for stage, unit_kind, unit_id in rows:
        unit = WorkUnit(stage, unit_kind, unit_id)
        if stage in {"project", "link"} and derived:
            try:
                if not current(conn, unit):
                    continue
            except Exception:
                continue
        orphans.append(unit)
    if not orphans:
        return 0
    with database.transaction() as write:
        return sum(
            write.execute(
                "DELETE FROM work_queue WHERE stage=? AND unit_kind=? AND unit_id=? "
                "AND state<>'leased'",
                (unit.stage, unit.unit_kind, unit.unit_id),
            ).rowcount
            for unit in orphans
        )


def succeed(conn: sqlite3.Connection, unit: WorkUnit) -> None:
    """The unit committed its output: it leaves the queue (inside that transaction)."""
    delete(conn, unit)


def release(conn: sqlite3.Connection, unit: WorkUnit, now: datetime) -> None:
    """Give a lease back without counting anything (a real supersession, a skip)."""
    row = get(conn, unit)
    if row is None:
        return
    if row.attempts == 0 and row.error_class is None:
        delete(conn, unit)
        return
    upsert(
        conn,
        replace(
            row,
            state="ready" if row.state == "leased" else row.state,
            lease_owner=None,
            lease_until=None,
            updated_at=at(now),
        ),
    )


def release_all(conn: sqlite3.Connection, now: datetime) -> int:
    """Global recovery by the exclusive process: every lease goes back to `ready`."""
    stamp = at(now)
    deleted = conn.execute(
        "DELETE FROM work_queue WHERE state='leased' AND attempts=0 AND error_class IS NULL"
    ).rowcount
    updated = conn.execute(
        "UPDATE work_queue SET state='ready',lease_owner=NULL,lease_until=NULL,updated_at=? "
        "WHERE state='leased'",
        (stamp,),
    ).rowcount
    return int(deleted) + int(updated)


def classify(reason: str, exception: str | None = None) -> str:
    """The failure class: the reason, plus `:<exception type>` for `unit_exception`."""
    return f"{reason}:{exception}" if reason == "unit_exception" and exception else reason


@dataclass(frozen=True)
class Failure:
    """What a failure did to the unit's row."""

    error_class: str
    state: QueueState
    attempts: int
    next_attempt_at: datetime
    #: Counts as a failure of this run: not a fenced write, not a waiting repeat.
    counted: bool
    #: A waiting unit's retry ended the way it waited (`waiting_repeats`).
    repeat: bool = False

    @property
    def quarantined(self) -> bool:
        return self.state == "quarantined"


def fail(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    *,
    reason: str,
    fingerprint: str | None,
    now: datetime,
    exception: str | None = None,
    waiting: bool = False,
    error: str | None = None,
    policy: Policy | None = None,
) -> Failure:
    """Record a failed attempt and return the clock `finish_attempt` gets.

    Consecutive failures count while the class and the input fingerprint
    stay the same; either change starts again at 1. `waiting` marks an
    outcome no retry fixes without new input (a blocked parse, a missing
    artifact): the unit waits 24 h doubling to 7 d and is never quarantined.
    """
    policy = policy or DEFAULT
    failure_class = classify(reason, exception)
    previous = get(conn, unit)
    stamp = at(now)
    base = QueueRow(
        stage=unit.stage,
        unit_kind=unit.unit_kind,
        unit_id=unit.unit_id,
        partition=partition_of(unit),
        state="ready",
        attempts=0,
        error_class=None,
        input_fingerprint=fingerprint,
        lease_owner=None,
        lease_until=None,
        next_attempt_at=None,
        last_reason=reason,
        last_error=error[:500] if error else None,
        enqueued_at=previous.enqueued_at if previous else stamp,
        updated_at=stamp,
        quarantined_at=None,
    )
    if reason in UNCOUNTED_REASONS:
        # The unit did nothing wrong: keep its run of failures as it was.
        clock = now + policy.fenced_retry
        kept = replace(
            base,
            attempts=previous.attempts if previous else 0,
            error_class=previous.error_class if previous else None,
            input_fingerprint=previous.input_fingerprint if previous else fingerprint,
            next_attempt_at=at(clock),
        )
        upsert(conn, kept)
        return Failure(failure_class, "ready", kept.attempts, clock, counted=False)
    same = (
        previous is not None
        and previous.error_class == failure_class
        and previous.input_fingerprint == fingerprint
        and previous.attempts > 0
    )
    count = previous.attempts + 1 if same and previous is not None else 1
    state: QueueState
    if reason == UNSUPPORTED_REASON:
        state, clock, counted = "waiting_input", now + policy.unsupported_wait, False
    elif waiting or reason in WAITING_REASONS:
        state, clock, counted = "waiting_input", now + waiting_delay(count, policy), not same
    elif count >= policy.quarantine_threshold(failure_class):
        state, clock, counted = "quarantined", now + policy.quarantine_retry, True
    elif failure_class == IO_REASON:
        state, clock, counted = "ready", now + policy.io_retry, True
    else:
        state, clock, counted = "ready", now + backoff(count, policy), True
    upsert(
        conn,
        replace(
            base,
            state=state,
            attempts=count,
            error_class=failure_class,
            next_attempt_at=at(clock),
            quarantined_at=stamp if state == "quarantined" else None,
        ),
    )
    repeat = state == "waiting_input" and same and reason != UNSUPPORTED_REASON
    return Failure(failure_class, state, count, clock, counted=counted, repeat=repeat)


def wake(
    database: Database,
    now: datetime,
    *,
    limit: int = 500,
    reason_code: str = WAKE_REASON,
) -> int:
    """Turn due waiting and quarantined rows `ready`; returns how many.

    A unit whose last attempt was blocked or unavailable is not eligible
    again without a retry request, so one is made; a quarantined unit's
    transient attempt already carries its clock.
    """
    from .attempts import latest_attempt, request_retry

    with database.transaction() as conn:
        rows = conn.execute(
            "SELECT stage,unit_kind,unit_id FROM work_queue "
            "WHERE state IN ('waiting_input','quarantined') AND next_attempt_at<=? "
            "ORDER BY next_attempt_at LIMIT ?",
            (at(now), limit),
        ).fetchall()
        stamp = at(now)
        for stage, unit_kind, unit_id in rows:
            unit = WorkUnit(stage, unit_kind, unit_id)
            conn.execute(
                "UPDATE work_queue SET state='ready',updated_at=? "
                "WHERE stage=? AND unit_kind=? AND unit_id=?",
                (stamp, stage, unit_kind, unit_id),
            )
            latest = latest_attempt(conn, unit)
            if latest is not None and latest["outcome"] in {"blocked", "unavailable"}:
                try:
                    request_retry(conn, unit, now=now, reason_code=reason_code)
                except ValueError:
                    # No work generation: nothing is queued for this unit.
                    pass
    return len(rows)


def requeue(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    by: str,
    error_class: str | None = None,
    unit: WorkUnit | None = None,
    stage: str | None = None,
    kind: str | None = None,
) -> list[WorkUnit]:
    """Operator retry: matching quarantined or waiting rows become ready with no count."""
    from .attempts import request_retry

    if (error_class is None) == (unit is None):
        raise ValueError("requeue needs exactly one of an error class or a unit")
    if not by.strip():
        raise ValueError("requeue needs --by")
    clauses = ["state IN ('quarantined','waiting_input')"]
    values: list[str] = []
    if unit is not None:
        clauses.append("stage=? AND unit_kind=? AND unit_id=?")
        values.extend((unit.stage, unit.unit_kind, unit.unit_id))
    if error_class is not None:
        clauses.append("error_class=?")
        values.append(error_class)
    if stage is not None:
        clauses.append("stage=?")
        values.append(stage)
    if kind is not None:
        clauses.append("unit_kind=?")
        values.append(kind)
    where = " AND ".join(clauses)
    units = [
        WorkUnit(*row)
        for row in conn.execute(
            f"SELECT stage,unit_kind,unit_id FROM work_queue WHERE {where} "
            "ORDER BY stage,unit_kind,unit_id",
            values,
        ).fetchall()
    ]
    stamp = at(now)
    for found in units:
        conn.execute(
            "UPDATE work_queue SET state='ready',attempts=0,next_attempt_at=?,"
            "quarantined_at=NULL,last_reason='operator_requeue',updated_at=? "
            "WHERE stage=? AND unit_kind=? AND unit_id=?",
            (stamp, stamp, found.stage, found.unit_kind, found.unit_id),
        )
        try:
            request_retry(conn, found, now=now, reason_code=f"operator_requeue by {by.strip()}")
        except ValueError:
            pass
    return units


def report(
    conn: sqlite3.Connection, now: datetime, *, state: QueueState | None = None
) -> dict[str, object]:
    """Counts by state and class, the oldest due ready row, and the units in
    `state` (quarantined by default), at most 200."""
    oldest = _parsed(
        conn.execute(
            "SELECT min(coalesce(next_attempt_at,updated_at)) FROM work_queue "
            "WHERE state='ready' AND coalesce(next_attempt_at,updated_at)<=?",
            (at(now),),
        ).fetchone()[0]
    )
    listed = conn.execute(
        "SELECT stage,unit_kind,unit_id,state,attempts,error_class,last_error,quarantined_at,"
        "next_attempt_at FROM work_queue WHERE state=coalesce(?,'quarantined') "
        "ORDER BY stage,unit_kind,unit_id LIMIT 200",
        (state,),
    ).fetchall()
    return {
        "states": counts_by_state(conn),
        "classes": counts_by_class(conn, state),
        "oldest_ready_seconds": None if oldest is None else (now - oldest).total_seconds(),
        "units": [
            {
                "unit": f"{row[0]}/{row[1]}/{row[2]}",
                "state": row[3],
                "attempts": row[4],
                "error_class": row[5],
                "last_error": row[6],
                "quarantined_at": row[7],
                "next_attempt_at": row[8],
            }
            for row in listed
        ],
    }
