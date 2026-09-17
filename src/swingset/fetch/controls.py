"""Request issuance and its control admission share one durable transaction."""

from __future__ import annotations

from uuid import uuid4

from swingset.clock import Clock
from swingset.fetch.classify import Classification
from swingset.fetch.limits import RequestContext
from swingset.fetch.politeness import Gate, Grant, Paused, Wait
from swingset.schedule.fairness import record_request, request_denial
from swingset.state.control_scopes import for_watch
from swingset.state.controls import ControlPaused, admission, settle
from swingset.state.db import Database


class _NotIssued(Exception):
    def __init__(self, grant: Wait | Paused) -> None:
        self.grant = grant


def issue(
    database: Database,
    gate: Gate,
    clock: Clock,
    *,
    host: str,
    source: str,
    watch: object,
    page_kind: str,
    crawl_delay: float,
    sweep: bool,
    request_url: str | None = None,
    context: RequestContext | None = None,
) -> tuple[Grant | Wait | Paused, str | None]:
    from swingset.schedule.event_timing_observer import checkpoint, resume

    checkpoint()
    action_id = "request_" + uuid4().hex
    scope = for_watch(
        database.connection,
        source=source,
        watch_id=getattr(watch, "watch_id", None),
        page_kind=page_kind,
        watch_kind=getattr(watch, "kind", None),
        host=host,
    )
    acquired = False
    try:
        with admission(
            database,
            action_id=action_id,
            action_kind="request",
            scope=scope,
            now=clock.now(),
        ):
            if context:
                reason = context.check(database.connection, request_url or "", clock.now())
                if reason:
                    raise _NotIssued(Paused(reason))
            if reason := request_denial(
                database.connection,
                gate.config,
                watch_id=getattr(watch, "watch_id", None),
                host=host,
                now=clock.now(),
            ):
                raise _NotIssued(Paused(reason))
            from swingset.history.origin_dispatch import record_request as record_origin_request
            from swingset.history.origin_dispatch import request_gate

            if reason := request_gate(
                database.connection,
                watch,
                now=clock.now(),
                history_start=gate.config.history_start,
                actual_host=host,
            ):
                raise _NotIssued(Paused(reason))
            grant = gate.acquire(host, source=source, crawl_delay=crawl_delay, sweep=sweep)
            acquired = isinstance(grant, Grant)
            if not isinstance(grant, Grant):
                # Waiting is not an issued attempt. Roll back the speculative
                # admission rather than append an action for every clock tick.
                raise _NotIssued(grant)
            assert grant.debited_at is not None
            record_request(
                database.connection,
                gate.config,
                action_id=action_id,
                host=host,
                watch_id=getattr(watch, "watch_id", None),
                source=source,
                now=grant.debited_at,
            )
            record_origin_request(
                database.connection, watch, action_id=action_id, now=grant.debited_at
            )
        if context:
            context.admitted_attempts += 1
        resume()
        return grant, action_id
    except _NotIssued as deferred:
        # Retain a host's diagnostic row without claiming a robots check or an
        # issued request when its budget/cooldown deferred the operation.
        database.connection.execute("INSERT OR IGNORE INTO hosts(host) VALUES (?)", (host,))
        resume()
        return deferred.grant, None
    except ControlPaused:
        resume()
        return Paused("operator"), None
    except BaseException:
        # No HTTP call follows an unsuccessful admission commit. Roll back a
        # still-open transaction, and release only the in-memory host claim.
        # A committed debit is conservatively retained if commit acknowledgment
        # failed; an uncommitted debit disappears with the rollback.
        if acquired:
            gate.discard(host)
        if database.connection.in_transaction:
            database.connection.rollback()
        if database.connection.execute(
            "SELECT 1 FROM execution_admissions WHERE action_id=?", (action_id,)
        ).fetchone():
            if context:
                context.admitted_attempts += 1
                context.uncertain_admissions += 1
            with database.transaction() as conn:
                settle(conn, action_id, now=clock.now(), outcome="not_issued")
        raise


def release(
    database: Database,
    gate: Gate,
    clock: Clock,
    action_id: str,
    host: str,
    outcome: Classification,
    *,
    body_bytes: int,
    request_day: str,
) -> None:
    from swingset.schedule.event_timing_observer import checkpoint

    checkpoint()
    with database.transaction() as conn:
        gate.release(host, outcome, body_bytes=body_bytes, request_day=request_day)
        conn.execute(
            "UPDATE scheduler_requests SET body_bytes=body_bytes+? WHERE action_id=?",
            (body_bytes, action_id),
        )
        settle(conn, action_id, now=clock.now(), outcome=outcome.outcome.value)
