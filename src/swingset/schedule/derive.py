"""One isolated derivation with durable evidence and atomic attempt completion."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

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
from swingset.state.control_scopes import for_unit
from swingset.state.controls import ControlPaused, matching_pauses
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
    """Another live owner leases the unit; the claim rolls back."""


def _same_error(
    previous: queue.QueueRow | None, failure: queue.Failure, error: str | None
) -> queue.Failure:
    """A waiting failure whose class and message match the last one is a repeat.

    `queue.fail` counts a waiting failure as fresh whenever the unit's input
    fingerprint changed. A deploy changes many fingerprints, so a page that
    fails the same way again would fail the next small derive tick. The
    message decides instead: the same class and the same message (as the
    queue row keeps it, 500 characters) make a repeat, whatever the
    fingerprint. With no earlier message to compare, the failure is fresh.
    The wait clock stays as `queue.fail` set it.
    """
    if (
        failure.repeat
        or failure.state != "waiting_input"
        or failure.error_class == queue.UNSUPPORTED_REASON
        or previous is None
        or previous.attempts < 1
        or previous.error_class != failure.error_class
        or not error
        or previous.last_error != error[:500]
    ):
        return failure
    return replace(failure, counted=False, repeat=True)


def _parse_error(conn: sqlite3.Connection, snapshot_id: str) -> str | None:
    """The message of the snapshot's open parse failure, which `parse_snapshot` wrote."""
    row = conn.execute(
        "SELECT summary FROM findings WHERE snapshot_id=? AND owner_kind='parse_failure' "
        "AND closed_at IS NULL ORDER BY opened_at DESC LIMIT 1",
        (snapshot_id,),
    ).fetchone()
    return str(row[0]) if row is not None and row[0] else None


def _failed(
    database: Database,
    attempt: Attempt,
    clock: Clock,
    *,
    outcome: Outcome,
    reason: str,
    evidence: dict[str, Any] | None = None,
    exception: str | None = None,
) -> queue.Failure:
    """Record a failure in the queue and finish the attempt with its clock."""
    error = str((evidence or {}).get("message") or (evidence or {}).get("reason") or "") or None
    with database.transaction() as conn:
        previous = queue.get(conn, attempt.unit)
        failure = queue.fail(
            conn,
            attempt.unit,
            reason=reason,
            fingerprint=attempt.fingerprint,
            now=clock.now(),
            exception=exception,
            waiting=outcome in {"blocked", "unavailable"},
            error=error,
        )
        failure = _same_error(previous, failure, error)
        finish_attempt(
            database,
            attempt,
            outcome=outcome,
            reason_code=reason,
            now=clock.now(),
            retry_at=failure.next_attempt_at if outcome == "transient" else None,
            evidence=evidence,
        )
    return failure


def _fenced(exc: BaseException) -> bool:
    """A write that met an active publication's fence (state/publication_fence)."""
    return isinstance(exc, sqlite3.DatabaseError) and "active publication fences" in str(exc)


def _close_fenced(database: Database, attempt: Attempt, clock: Clock, reason: str) -> None:
    """Close a unit whose write met a publication fence, uncounted, if it can.

    A narrow publication (plan phase 3) leaves the queue and attempt tables
    writable: the attempt ends `interrupted` and the unit retries in a
    minute, as the next tick's recovery would. Under a full fence this write
    is fenced too and nothing is written: this loop's next tick closes them
    (F6).
    """
    try:
        with database.transaction() as conn:
            failure = queue.fail(
                conn, attempt.unit, reason=reason, fingerprint=attempt.fingerprint, now=clock.now()
            )
            finish_attempt(
                database,
                attempt,
                outcome="interrupted",
                reason_code=reason,
                now=clock.now(),
                retry_at=max(failure.next_attempt_at, clock.now() + queue.DEFAULT.takeover_retry),
            )
    except (sqlite3.Error, WriteDeadlineExceeded, ValueError):
        pass


def _locked(exc: BaseException) -> bool:
    """`BEGIN IMMEDIATE` or a write outwaited the busy timeout behind another process."""
    message = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in message or "busy" in message)


def _admission_failed(
    database: Database, unit: WorkUnit, clock: Clock, exc: Exception
) -> DerivationOutcome:
    """A unit whose admission raised (its capture, lease or attempt) fails on the queue.

    The admission transaction rolled back, so no attempt exists; the queue
    row alone carries the failure's clock, and selection honours it
    (`queue.holds`).
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
    """Derive one unit: lease and admit it, run it, and record its outcome.

    ``on_admit`` joins the claim transaction that commits the unit's lease
    before its admission (the offline loop rotates its kind group there, so
    a unit that kills its process does not keep its group first).

    The admission writes no ledger row and takes no control lock (D-0238):
    one `BEGIN IMMEDIATE` checks the unit's pauses, captures its inputs and
    begins its attempt. A pause commits with `BEGIN IMMEDIATE` on the same
    database, so it either lands first and holds the unit, or lands after
    and sees the lease as draining.

    Outcomes that admitted nothing carry only a reason: `operator_pause`,
    `lease_busy`, `publication_fenced` and `write_lock_timeout` (the unit
    retries soon, nothing counted), and `process_interrupted` (a takeover
    counted a dead attempt; the unit waits on that failure's clock).
    """
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
        if database.connection.in_transaction:
            raise ValueError("admission must precede the unit's write transaction")
        # The unit's scope can take a second to read (a DISTINCT over its
        # observations), so it is read before the write lock is taken. The
        # pause rows are read inside, serialized with `change_control`; a
        # pause that appeared in between gets its scope read there (I2).
        scope = (
            for_unit(database.connection, unit)
            if database.connection.execute("SELECT 1 FROM operator_pauses LIMIT 1").fetchone()
            else None
        )
        with database.transaction() as conn:
            pauses = (
                matching_pauses(
                    conn, scope if scope is not None else for_unit(conn, unit), now=clock.now()
                )
                if conn.execute("SELECT 1 FROM operator_pauses LIMIT 1").fetchone()
                else []
            )
            if pauses:
                raise ControlPaused(pauses)
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
    except ControlPaused:
        return unclaimed("operator_pause")
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
                                return DerivationOutcome(source, parsed.failed, reason)
                            # A blocked parse waits for input on a clock (D-0216).
                            # The extract or parse error rides in the attempt's
                            # evidence, so the next failure can be compared.
                            message = _parse_error(conn, unit.unit_id) if parsed.failed else None
                            waited = _failed(
                                database,
                                attempt,
                                clock,
                                outcome="blocked",
                                reason=reason,
                                evidence={"message": message} if message else None,
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
                clock,
                outcome="unavailable",
                reason="artifact_unavailable",
                evidence={
                    # The message names the artifact, so a different lost
                    # artifact is a new failure, not a repeat (D-0249).
                    "message": str(exc),
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
            return DerivationOutcome(source, False, "inputs_changed")
        except Exception as exc:
            if _fenced(exc) or _locked(exc):
                # A fenced or busy write: the unit did nothing wrong. Close it
                # uncounted where the queue is writable (a narrow fence);
                # otherwise the lease and attempt stay open for this loop's
                # next tick, whose recovery closes them uncounted.
                reason = queue.FENCED_REASON if _fenced(exc) else queue.LOCK_REASON
                if _fenced(exc):
                    _close_fenced(database, attempt, clock, reason)
                return DerivationOutcome(source, reason=reason)
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
            reason = queue.FENCED_REASON if _fenced(exc) else queue.LOCK_REASON
            if _fenced(exc):
                _close_fenced(database, attempt, clock, reason)
            return DerivationOutcome(source, reason=reason)
        raise
