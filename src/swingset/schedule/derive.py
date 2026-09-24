"""One isolated derivation with durable evidence and atomic attempt completion."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from swingset.clock import Clock
from swingset.fetch.archive import Archive, ArtifactUnavailable
from swingset.schedule.parse import parse_snapshot
from swingset.state.attempts import SupersededWorkError, begin_attempt, finish_attempt
from swingset.state.control_scopes import for_unit
from swingset.state.controls import ControlPaused, admission, bind_work_attempt, settle
from swingset.state.db import Database
from swingset.state.inputs import InputBundle
from swingset.state.work import WorkUnit
from swingset.state.write_deadline import WriteDeadlineExceeded

# The history projection stores ~200k rows in one write: 55 to 82 seconds on
# the 2026-09-24 database. It runs at most twice a day (D-0199), so it gets a
# named allowance instead of the 45-second bound (D-0198).
HISTORY_WRITE_SECONDS = 300.0

# Retry delays for a unit whose write overran the deadline: 15 minutes,
# doubling per consecutive overrun, never more than six hours apart.
DEADLINE_RETRY_FIRST = timedelta(minutes=15)
DEADLINE_RETRY_LONGEST = timedelta(hours=6)


def deadline_backoff(database: Database, unit: WorkUnit) -> timedelta:
    """Delay before the next attempt, from this unit's run of consecutive overruns."""
    overruns = 0
    for (reason,) in database.connection.execute(
        "SELECT reason_code FROM work_attempts WHERE stage=? AND unit_kind=? AND unit_id=? "
        "AND outcome!='running' ORDER BY attempt_id DESC LIMIT 8",
        (unit.stage, unit.unit_kind, unit.unit_id),
    ):
        if reason != "write_deadline_exceeded":
            break
        overruns += 1
    delay: timedelta = DEADLINE_RETRY_FIRST * (2**overruns)
    return min(delay, DEADLINE_RETRY_LONGEST)


@dataclass(frozen=True)
class DerivationOutcome:
    source: str | None = None
    failed: bool = False
    reason: str | None = None


def derive_one(
    database: Database,
    archive: Archive,
    unit: WorkUnit,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
) -> DerivationOutcome:
    action_id = "work_" + uuid4().hex
    try:
        with admission(
            database,
            action_id=action_id,
            action_kind=unit.stage,
            scope=for_unit(database.connection, unit),
            now=clock.now(),
            run_id=run_id,
        ) as conn:
            from swingset.state import derivations

            selection = (
                derivations.capture(conn, unit, now=clock.now())
                if unit.stage in {"project", "link"} and derivations.available(conn)
                else None
            )
            attempt = begin_attempt(
                database,
                unit,
                now=clock.now(),
                run_id=run_id,
                fingerprint=selection.fingerprint if selection else None,
            )
            bind_work_attempt(conn, action_id, attempt.attempt_id)
    except ControlPaused:
        return DerivationOutcome(reason="operator_pause")
    source_row = (
        database.connection.execute(
            "SELECT w.source FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=?",
            (unit.unit_id,),
        ).fetchone()
        if unit.stage == "parse"
        else None
    )
    source = str(source_row[0]) if source_row else None
    from contextlib import nullcontext

    from swingset.state.write_deadline import allowance

    extended = (
        allowance(HISTORY_WRITE_SECONDS)
        if (unit.stage, unit.unit_kind) == ("project", "history")
        else nullcontext()
    )
    try:
        with extended, database.transaction():
            if unit.stage == "parse":
                parsed = parse_snapshot(database, archive, unit, clock, run_id)
                source = parsed.source
                if parsed.failed or parsed.selection:
                    reason = "parse_failed" if parsed.failed else f"admission_{parsed.selection}"
                    finish_attempt(
                        database,
                        attempt,
                        outcome="superseded" if parsed.selection == "superseded" else "blocked",
                        reason_code=reason,
                        now=clock.now(),
                    )
                    settle(database.connection, action_id, now=clock.now(), outcome=reason)
                    return DerivationOutcome(source, parsed.failed, reason)
            elif unit.stage == "project":
                from swingset.project import process_unit

                process_unit(database, unit, bundle, clock, run_id, selection=selection)
            elif unit.stage == "link":
                from swingset.link import link_event

                link_event(database, unit.unit_id, bundle, clock, run_id, selection=selection)
            else:
                raise ValueError(f"unsupported derivation stage: {unit.stage}")
            finish_attempt(
                database,
                attempt,
                outcome="succeeded",
                reason_code="output_committed",
                now=clock.now(),
            )
            settle(database.connection, action_id, now=clock.now(), outcome="output_committed")
        return DerivationOutcome(source)
    except ArtifactUnavailable as exc:
        with database.transaction() as conn:
            finish_attempt(
                database,
                attempt,
                outcome="unavailable",
                reason_code="artifact_unavailable",
                now=clock.now(),
                evidence={
                    "kind": exc.kind,
                    "sha256": exc.sha256,
                    "reason": exc.reason,
                    "recovery_attempts": exc.attempts,
                },
            )
            settle(conn, action_id, now=clock.now(), outcome="artifact_unavailable")
        return DerivationOutcome(source, True, "artifact_unavailable")
    except SupersededWorkError:
        with database.transaction() as conn:
            finish_attempt(
                database,
                attempt,
                outcome="superseded",
                reason_code="inputs_changed",
                now=clock.now(),
            )
            settle(conn, action_id, now=clock.now(), outcome="inputs_changed")
        return DerivationOutcome(source, False, "inputs_changed")
    except Exception as exc:
        # A unit's transaction has rolled back. Keep its token and diagnostic,
        # then allow independent units to use already committed evidence.
        transient = isinstance(exc, OSError)
        # A write that overran the bound rolled back whole. It is not a defect
        # in the inputs, so it retries with a doubling delay instead of waiting
        # for an input change that may never come (D-0198).
        overran = isinstance(exc, WriteDeadlineExceeded)
        # A retained page whose parser this runtime does not know (a manual
        # artifact on its own replay path, D-0097) is parked for a month rather
        # than retried and counted as a failure every cycle (D-0182).
        unsupported = isinstance(exc, KeyError) and "unknown page kind" in str(exc)
        reason = (
            "write_deadline_exceeded"
            if isinstance(exc, WriteDeadlineExceeded)
            else "unit_io_error"
            if transient
            else "unsupported_page_kind"
            if unsupported
            else "unit_exception"
        )
        retry_at = (
            clock.now() + timedelta(seconds=60)
            if transient
            else clock.now() + timedelta(days=30)
            if unsupported
            else clock.now() + deadline_backoff(database, unit)
            if overran
            else None
        )
        with database.transaction() as conn:
            finish_attempt(
                database,
                attempt,
                outcome="transient" if transient or unsupported or overran else "blocked",
                reason_code=reason,
                now=clock.now(),
                retry_at=retry_at,
                evidence={"exception": type(exc).__name__, "message": str(exc)},
            )
            settle(conn, action_id, now=clock.now(), outcome=reason)
        return DerivationOutcome(source, not unsupported, reason)
