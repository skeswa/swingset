"""One isolated derivation with durable evidence and atomic attempt completion."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from swingset import cutoff
from swingset.clock import Clock
from swingset.fetch.archive import Archive, ArtifactUnavailable
from swingset.schedule.parse import parse_snapshot
from swingset.state import queue
from swingset.state.attempts import (
    Attempt,
    Outcome,
    SupersededWorkError,
    begin_attempt,
    finish_attempt,
)
from swingset.state.control_lock import ControlTimeout
from swingset.state.control_scopes import for_unit
from swingset.state.controls import ControlPaused, admission, bind_work_attempt, settle
from swingset.state.db import Database
from swingset.state.inputs import InputBundle
from swingset.state.work import WorkUnit
from swingset.state.write_deadline import WriteDeadlineExceeded


def _inputs_unchanged(database: Database, unit: WorkUnit, fingerprint: str) -> bool:
    """Whether the unit's input fingerprint still equals its attempt's.

    A fingerprint that cannot be formed counts as changed.
    """
    from swingset.state import derivations
    from swingset.state.work_fingerprints import input_fingerprint

    conn = database.connection
    try:
        if unit.stage in {"project", "link"} and derivations.available(conn):
            return derivations.desired(conn, unit).fingerprint == fingerprint
        return input_fingerprint(conn, unit) == fingerprint
    except Exception:
        return False


def _cutoff_on(bundle: InputBundle) -> bool:
    """The bundle's `[derivation] cutoff` switch; on when it carries no config.

    A parse reads nothing else from its bundle, and some callers pass none.
    """
    config = getattr(bundle, "config", None)
    derivation = getattr(config, "derivation", None)
    return True if derivation is None else bool(derivation.cutoff)


@dataclass(frozen=True)
class DerivationOutcome:
    source: str | None = None
    failed: bool = False
    reason: str | None = None
    #: A waiting unit's retry ended the way it waited: not a failure (D-0216).
    waiting_repeat: bool = False
    #: This failure quarantined the unit.
    quarantined: bool = False


class _LeaseBusy(Exception):
    """Another live owner leases the unit; the admission rolls back."""


def _failed(
    database: Database,
    attempt: Attempt,
    action_id: str,
    clock: Clock,
    *,
    outcome: Outcome,
    reason: str,
    evidence: dict[str, Any] | None = None,
    exception: str | None = None,
) -> queue.Failure:
    """Record a failure in the queue and finish the attempt with its clock."""
    with database.transaction() as conn:
        failure = queue.fail(
            conn,
            attempt.unit,
            reason=reason,
            fingerprint=attempt.fingerprint,
            now=clock.now(),
            exception=exception,
            waiting=outcome in {"blocked", "unavailable"},
            error=str((evidence or {}).get("message") or (evidence or {}).get("reason") or "")
            or None,
        )
        finish_attempt(
            database,
            attempt,
            outcome=outcome,
            reason_code=reason,
            now=clock.now(),
            retry_at=failure.next_attempt_at if outcome == "transient" else None,
            evidence=evidence,
        )
        settle(conn, action_id, now=clock.now(), outcome=reason)
    return failure


def _fenced(exc: BaseException) -> bool:
    """A write that met an active publication's fence (state/publication_fence)."""
    return isinstance(exc, sqlite3.DatabaseError) and "active publication fences" in str(exc)


def _locked(exc: BaseException) -> bool:
    """`BEGIN IMMEDIATE` or a write outwaited the busy timeout behind another process."""
    message = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in message or "busy" in message)


def _admission_failed(
    database: Database, unit: WorkUnit, clock: Clock, exc: Exception
) -> DerivationOutcome:
    """A unit whose admission raised (its capture, lease or attempt) fails on the queue.

    The admission rolled back, so no attempt exists; the queue row alone
    carries the failure's clock, and selection honours it (`queue.holds`).
    Without it one defective unit would be chosen first on every tick (I1, I3).
    """
    from swingset.state.work_fingerprints import input_fingerprint

    try:
        fingerprint: str | None = input_fingerprint(database.connection, unit)
    except Exception:
        fingerprint = None
    try:
        with database.transaction() as conn:
            failure = queue.fail(
                conn,
                unit,
                reason="unit_exception",
                fingerprint=fingerprint,
                now=clock.now(),
                exception=type(exc).__name__,
                error=f"admission: {exc}",
            )
    except Exception as recorded:
        if _fenced(recorded):
            return DerivationOutcome(reason=queue.FENCED_REASON)
        if _locked(recorded):
            return DerivationOutcome(reason=queue.LOCK_REASON)
        raise
    return DerivationOutcome(failed=True, reason="unit_exception", quarantined=failure.quarantined)


def derive_one(
    database: Database,
    archive: Archive,
    unit: WorkUnit,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    *,
    on_admit: Callable[[sqlite3.Connection], None] | None = None,
) -> DerivationOutcome:
    """Derive one unit: admit and lease it, run it, and record its outcome.

    ``on_admit`` joins the claim transaction that commits the unit's lease
    before its admission (the offline loop rotates its kind group there, so
    a unit that kills its process does not keep its group first).

    Outcomes that admitted nothing carry only a reason: `operator_pause`,
    `control_timeout`, `lease_busy`, `publication_fenced` and
    `write_lock_timeout` (the unit retries soon, nothing counted), and
    `process_interrupted` (a takeover counted a dead attempt; the unit waits
    on that failure's clock).
    """
    action_id = "work_" + uuid4().hex
    owner = queue.current_owner(run_id, exclusive=database.holds_writer_lock)
    # The claim commits the lease (and `on_admit`) before any heavy work, so
    # a process that dies inside the admission leaves a lease of a dead run
    # with no running attempt. Recovery counts that as the unit's death
    # (`queue.lease`, the loops' `_recover_dead_runs`): it backs off and
    # quarantines like a death inside the attempt (I1, I3).
    try:
        with database.transaction() as conn:
            lease = queue.lease(database, unit, owner=owner, now=clock.now())
            if lease.busy:
                raise _LeaseBusy
            if lease.held is None and on_admit is not None:
                on_admit(conn)
    except _LeaseBusy:
        return DerivationOutcome(reason="lease_busy")
    except Exception as exc:
        if _fenced(exc):
            return DerivationOutcome(reason=queue.FENCED_REASON)
        if _locked(exc):
            return DerivationOutcome(reason=queue.LOCK_REASON)
        return _admission_failed(database, unit, clock, exc)
    if lease.held is not None:
        return DerivationOutcome(
            reason=queue.INTERRUPTED_REASON, quarantined=lease.held.quarantined
        )

    def unclaimed(reason: str) -> DerivationOutcome:
        # Nothing ran: give the lease back. Housekeeping; a lease left behind
        # belongs to a run that ends on its own, so it is released uncounted.
        try:
            with database.transaction() as write:
                queue.release(write, unit, clock.now())
        except (sqlite3.Error, WriteDeadlineExceeded):
            pass
        return DerivationOutcome(reason=reason)

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
                after_takeover=lease.took_over,
            )
            bind_work_attempt(conn, action_id, attempt.attempt_id)
    except ControlPaused:
        return unclaimed("operator_pause")
    except ControlTimeout:
        # Another process held the control lock past the admission timeout:
        # nothing was written; the unit is skipped this time.
        return unclaimed("control_timeout")
    except Exception as exc:
        # The admission rolled back. A publication or a busy writer is not
        # the unit's fault: skip it, write nothing (F6, F7).
        if _fenced(exc):
            return unclaimed(queue.FENCED_REASON)
        if _locked(exc):
            return unclaimed(queue.LOCK_REASON)
        return _admission_failed(database, unit, clock, exc)
    source_row = (
        database.connection.execute(
            "SELECT w.source FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=?",
            (unit.unit_id,),
        ).fetchone()
        if unit.stage == "parse"
        else None
    )
    source = str(source_row[0]) if source_row else None
    from swingset.state import derivations

    def succeeded(conn: sqlite3.Connection) -> None:
        finish_attempt(
            database,
            attempt,
            outcome="succeeded",
            reason_code="output_committed",
            now=clock.now(),
        )
        queue.succeed(conn, unit)
        settle(conn, action_id, now=clock.now(), outcome="output_committed", forget=True)

    def process() -> DerivationOutcome:
        nonlocal source
        try:
            with (
                cutoff.configured(_cutoff_on(bundle)),
                derivations.chunking(database.connection, unit) as chunk,
            ):
                with database.transaction() as conn:
                    queued = conn.execute(
                        "SELECT enqueued_at FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                        (unit.stage, unit.unit_kind, unit.unit_id),
                    ).fetchone()
                    if unit.stage == "parse":
                        parsed = parse_snapshot(database, archive, unit, clock, run_id)
                        source = parsed.source
                        if parsed.failed or parsed.selection:
                            reason = (
                                "parse_failed" if parsed.failed else f"admission_{parsed.selection}"
                            )
                            if parsed.selection == "superseded":
                                queue.release(conn, unit, clock.now())
                                finish_attempt(
                                    database,
                                    attempt,
                                    outcome="superseded",
                                    reason_code=reason,
                                    now=clock.now(),
                                )
                                settle(conn, action_id, now=clock.now(), outcome=reason)
                                return DerivationOutcome(source, parsed.failed, reason)
                            # A blocked parse waits for input on a clock (D-0216).
                            waited = _failed(
                                database,
                                attempt,
                                action_id,
                                clock,
                                outcome="blocked",
                                reason=reason,
                            )
                            return DerivationOutcome(
                                source,
                                parsed.failed and not waited.repeat,
                                reason,
                                waiting_repeat=waited.repeat,
                            )
                    elif unit.stage == "project":
                        from swingset.project import process_unit

                        process_unit(database, unit, bundle, clock, run_id, selection=selection)
                    elif unit.stage == "link":
                        from swingset.link import link_event

                        link_event(
                            database, unit.unit_id, bundle, clock, run_id, selection=selection
                        )
                    else:
                        raise ValueError(f"unsupported derivation stage: {unit.stage}")
                    if chunk.deferred is None:
                        succeeded(conn)
                    elif queued is not None:
                        # Step A of a staged completion (WP4): the unit is not done
                        # until step C, so its hint stays until then.
                        conn.execute(
                            "INSERT OR IGNORE INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
                            "VALUES (?,?,?,?)",
                            (unit.stage, unit.unit_kind, unit.unit_id, queued[0]),
                        )
                if chunk.deferred is not None:
                    # A committed the live rows. Store the output in chunks, then
                    # flip the pointer and finish the attempt in one transaction.
                    derivations.finish_building(database, chunk.deferred, commit=succeeded)
            if unit.stage in {"project", "link"}:
                # The unit finished: an earlier A's building row for other inputs
                # is settled (I6). Housekeeping; a failure is retried next run.
                try:
                    derivations.collect_orphans(database, unit, keep=None)
                except (sqlite3.Error, WriteDeadlineExceeded):
                    pass
            return DerivationOutcome(source)
        except ArtifactUnavailable as exc:
            waited = _failed(
                database,
                attempt,
                action_id,
                clock,
                outcome="unavailable",
                reason="artifact_unavailable",
                evidence={
                    "kind": exc.kind,
                    "sha256": exc.sha256,
                    "reason": exc.reason,
                    "recovery_attempts": exc.attempts,
                },
            )
            return DerivationOutcome(
                source,
                not waited.repeat,
                "artifact_unavailable",
                waiting_repeat=waited.repeat,
            )
        except SupersededWorkError:
            # A supersession re-opens only when the unit's inputs move. When they
            # have not (a relabel, a fence, "a newer attempt owns this work"),
            # nothing would ever re-open it: it fails on the queue's clock instead
            # (I3), and quarantines if it keeps happening (D-0216).
            if _inputs_unchanged(database, unit, attempt.fingerprint):
                spurious = _failed(
                    database,
                    attempt,
                    action_id,
                    clock,
                    outcome="transient",
                    reason="inputs_changed_spurious",
                )
                return DerivationOutcome(
                    source, False, "inputs_changed_spurious", quarantined=spurious.quarantined
                )
            with database.transaction() as conn:
                queue.release(conn, unit, clock.now())
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
            if _fenced(exc) or _locked(exc):
                # A fenced or busy write: the unit did nothing wrong, and the
                # queue and attempt tables are fenced too. Write nothing: the
                # admission and attempt stay open for this loop's next tick,
                # whose recovery closes them uncounted (F6, F7).
                return DerivationOutcome(
                    source, reason=queue.FENCED_REASON if _fenced(exc) else queue.LOCK_REASON
                )
            # A unit's transaction has rolled back. Keep its token and diagnostic,
            # then allow independent units to use already committed evidence.
            # A retained page whose parser this runtime does not know (a manual
            # artifact on its own replay path, D-0097) is parked for a month rather
            # than retried and counted as a failure every cycle (D-0182).
            unsupported = isinstance(exc, KeyError) and "unknown page kind" in str(exc)
            from swingset.state.derivations import NondeterministicOutputError

            reason = (
                "write_deadline_exceeded"
                if isinstance(exc, WriteDeadlineExceeded)
                else "nondeterministic_output"
                if isinstance(exc, NondeterministicOutputError)
                else "unit_io_error"
                if isinstance(exc, OSError)
                else "unsupported_page_kind"
                if unsupported
                else "unit_exception"
            )
            # Every failure gets a clock from the queue (I3, D-0216): a write that
            # overran its bound, or code that raised, backs off 15 min doubling to
            # 6 h and quarantines at the third failure of one class; an I/O error
            # retries in a minute and quarantines at the twentieth. A deploy that
            # fixes the code is picked up without an operator retry.
            failure = _failed(
                database,
                attempt,
                action_id,
                clock,
                outcome="transient",
                reason=reason,
                exception=type(exc).__name__,
                evidence={"exception": type(exc).__name__, "message": str(exc)},
            )
            return DerivationOutcome(
                source, failure.counted and not unsupported, reason, quarantined=failure.quarantined
            )

    try:
        return process()
    except Exception as exc:
        # A handler's own write met the publication fence or the busy
        # timeout: nothing more is written (the queue and attempt tables are
        # fenced too); this loop's next tick closes the attempt uncounted.
        if _fenced(exc) or _locked(exc):
            return DerivationOutcome(
                source, reason=queue.FENCED_REASON if _fenced(exc) else queue.LOCK_REASON
            )
        raise
