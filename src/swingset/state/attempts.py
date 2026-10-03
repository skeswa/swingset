"""Durable per-unit failures, retry eligibility, and fenced completion."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .db import Database
from .work import WorkUnit
from .work_fingerprints import input_fingerprint

Outcome = Literal["succeeded", "blocked", "transient", "unavailable", "interrupted", "superseded"]


class SupersededWorkError(RuntimeError):
    """The caller must roll back this unit's output before recording supersession."""


@dataclass(frozen=True)
class Attempt:
    attempt_id: int
    unit: WorkUnit
    fingerprint: str
    work_token: str
    queue_generation: int
    retry_generation: int
    run_id: str


def _key(unit: WorkUnit) -> tuple[str, str, str]:
    return unit.stage, unit.unit_kind, unit.unit_id


def _at(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("attempt times must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def latest_attempt(conn: sqlite3.Connection, unit: WorkUnit) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM work_attempts WHERE stage=? AND unit_kind=? AND unit_id=? ORDER BY attempt_id DESC LIMIT 1",
        _key(unit),
    ).fetchone()
    return row


def eligible(conn: sqlite3.Connection, unit: WorkUnit, fingerprint: str, now: datetime) -> bool:
    row = latest_attempt(conn, unit)
    if row is None or row["input_fingerprint"] != fingerprint:
        return True
    current = conn.execute(
        "SELECT retry_generation FROM work_generations WHERE stage=? AND unit_kind=? AND unit_id=?",
        _key(unit),
    ).fetchone()
    if current is not None and current[0] > row["retry_generation"]:
        return True
    if row["outcome"] == "succeeded":
        return True
    if row["outcome"] == "running":
        # A process that died mid-attempt leaves it running. Once its queue
        # lease is dead, the next lease closes it and retries (D-0216).
        from .queue import running_lease_dead

        return running_lease_dead(conn, unit, now)
    return (
        row["outcome"] in {"transient", "interrupted"}
        and row["retry_at"] is not None
        and datetime.fromisoformat(row["retry_at"]) <= now
    )


def begin_attempt(
    database: Database,
    unit: WorkUnit,
    *,
    now: datetime,
    run_id: str,
    fingerprint: str | None = None,
    after_takeover: bool = False,
) -> Attempt:
    """Commit before doing work, so process death leaves a recoverable attempt.

    `after_takeover`: the caller's lease just closed a dead lease's running
    attempt as interrupted (`queue.lease`), in this transaction, so its
    one-second retry clock does not make the unit wait.
    """
    at = _at(now)
    with database.transaction() as conn:
        fingerprint = fingerprint or input_fingerprint(conn, unit)
        if not after_takeover and not eligible(conn, unit, fingerprint, now):
            raise ValueError("work is not eligible for another attempt")
        from .derivations import available

        if unit.stage in {"project", "link"} and available(conn):
            # This compatibility token records the active attempt. It does not
            # create or discharge the derivation's desired/materialized work.
            conn.execute(
                "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                (*_key(unit), at),
            )
        row = conn.execute(
            "SELECT p.enqueued_at,g.generation,g.retry_generation FROM pending_work p JOIN work_generations g USING(stage,unit_kind,unit_id) WHERE p.stage=? AND p.unit_kind=? AND p.unit_id=?",
            _key(unit),
        ).fetchone()
        if row is None:
            raise ValueError("work is no longer pending")
        cursor = conn.execute(
            "INSERT INTO work_attempts(stage,unit_kind,unit_id,queue_generation,retry_generation,work_token,input_fingerprint,run_id,started_at,outcome) VALUES (?,?,?,?,?,?,?,?,?,'running')",
            (*_key(unit), row[1], row[2], row[0], fingerprint, run_id, at),
        )
        assert cursor.lastrowid is not None
        return Attempt(cursor.lastrowid, unit, fingerprint, row[0], row[1], row[2], run_id)


def finish_attempt(
    database: Database,
    attempt: Attempt,
    *,
    outcome: Outcome,
    reason_code: str,
    now: datetime,
    retry_at: datetime | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    """Failures retain pending work; success joins the caller's output transaction.

    A handled legacy parser may have deleted its item. Failure restores its
    original admission timestamp token without overwriting any newer work.
    Success after invalidation raises, so the caller rolls back stale output.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_.:-]*", reason_code):
        raise ValueError("reason_code must be a stable machine-readable code")
    at = _at(now)
    retry = _at(retry_at) if retry_at is not None else None
    if outcome in {"transient", "interrupted"}:
        if retry_at is None or retry_at <= now:
            raise ValueError("transient outcomes require a future retry deadline")
    elif retry_at is not None:
        raise ValueError("only transient or interrupted work has a retry deadline")
    if outcome == "succeeded" and not database.connection.in_transaction:
        raise ValueError("success must share the transaction that writes unit output")
    with database.transaction() as conn:
        previous = conn.execute(
            "SELECT outcome FROM work_attempts WHERE attempt_id=?", (attempt.attempt_id,)
        ).fetchone()
        if previous is None:
            raise ValueError("attempt does not exist")
        if previous[0] != "running":
            if previous[0] == outcome:
                return
            raise ValueError("attempt already has a different outcome")
        newest = latest_attempt(conn, attempt.unit)
        if newest is not None and newest["attempt_id"] != attempt.attempt_id:
            if outcome == "succeeded":
                raise SupersededWorkError("a newer attempt owns this work")
            # A restart can encounter an older running attempt after newer
            # inputs already completed. Preserve its history without reviving
            # completed work or replacing the newer attempt's outcome.
            conn.execute(
                "UPDATE work_attempts SET finished_at=?,outcome='superseded',reason_code='newer_attempt_exists',evidence_json=?,retry_at=NULL WHERE attempt_id=?",
                (at, json.dumps(evidence or {}, sort_keys=True), attempt.attempt_id),
            )
            return
        generation = conn.execute(
            "SELECT generation FROM work_generations WHERE stage=? AND unit_kind=? AND unit_id=?",
            _key(attempt.unit),
        ).fetchone()
        if outcome == "succeeded":
            from .derivations import available

            derived = attempt.unit.stage in {"project", "link"} and available(conn)
            if not derived and (generation is None or generation[0] != attempt.queue_generation):
                raise SupersededWorkError("work changed while this attempt was running")
            conn.execute(
                "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                _key(attempt.unit),
            )
        else:
            conn.execute(
                "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                (*_key(attempt.unit), attempt.work_token),
            )
        # The attempt row and the unit's work_queue row hold the failure. A
        # unit writes no finding or finding_support row (D-0237).
        conn.execute(
            "UPDATE work_attempts SET finished_at=?,outcome=?,reason_code=?,evidence_json=?,retry_at=? WHERE attempt_id=?",
            (
                at,
                outcome,
                reason_code,
                json.dumps(evidence or {}, sort_keys=True),
                retry,
                attempt.attempt_id,
            ),
        )


def request_retry(
    conn: sqlite3.Connection, unit: WorkUnit, *, now: datetime, reason_code: str
) -> None:
    """Explicit operator retry or verified artifact recovery, never a scan side effect."""
    if not reason_code:
        raise ValueError("a retry request requires its reason")
    cursor = conn.execute(
        "UPDATE work_generations SET generation=generation+1,retry_generation=retry_generation+1,retry_reason=?,retry_requested_at=? WHERE stage=? AND unit_kind=? AND unit_id=?",
        (reason_code, _at(now), *_key(unit)),
    )
    if cursor.rowcount != 1:
        raise ValueError("retry requires a known work unit")


def recover_interrupted(
    database: Database, *, now: datetime, retry_delay: timedelta = timedelta(seconds=60)
) -> int:
    """Called once after acquiring the process lock; no surviving worker may exist."""
    rows = list(
        database.connection.execute(
            "SELECT * FROM work_attempts WHERE outcome='running' ORDER BY attempt_id"
        )
    )
    for row in rows:
        attempt = Attempt(
            row["attempt_id"],
            WorkUnit(row["stage"], row["unit_kind"], row["unit_id"]),
            row["input_fingerprint"],
            row["work_token"],
            row["queue_generation"],
            row["retry_generation"],
            row["run_id"],
        )
        finish_attempt(
            database,
            attempt,
            outcome="interrupted",
            reason_code="process_interrupted",
            now=now,
            retry_at=now + retry_delay,
        )
    return len(rows)
