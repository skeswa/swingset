"""Bound worker writes in Python and on the PostgreSQL server."""

from __future__ import annotations

import contextlib
import math
import signal
import threading
import time
from collections.abc import Iterator
from types import FrameType

from .engine import CommitUncertain, Connection, ConnectionLost, WriteTimeout

# A safety net, not the bound: every write is small by construction (I2), so
# a firing is an alarm (D-0217). No unit gets a longer limit.
WRITE_SECONDS = 75.0
_fired = 0


def fired() -> int:
    """How many writes this process has cut off at the deadline."""
    return _fired


def run_counters(fired_before: int = 0) -> dict[str, float | int]:
    """The write counters a run record carries (D-0217).

    `fired_before` is `fired()` at the run's start; the timing counters come
    from `state/db.py`, which the run resets when it starts.
    """
    from .db import write_counters

    counters = write_counters()
    return {
        "write_deadline_fired": fired() - fired_before,
        "write_seconds_max": counters["write_seconds_max"],
        "write_wait_seconds_max": counters["write_wait_seconds_max"],
        "write_lock_timeouts": counters["write_lock_timeouts"],
    }


class WriteDeadlineExceeded(RuntimeError):
    """The atomic unit must roll back and await a changed recipe or explicit retry."""


class _Expired(KeyboardInterrupt):
    # Parser exception handlers must not turn an interrupted unit into success.
    pass


@contextlib.contextmanager
def bounded_write(conn: Connection, *, seconds: float | None = None) -> Iterator[None]:
    """Nested writes inherit the caller's bound; callers invoke this only once.

    The pipeline owns its Postgres writer on the main process thread. Operator
    controls use a separate connection and do not enter this worker boundary.
    Read snapshots and explicit maintenance migrations do not enter it either.
    The caller begins its transaction before entering, so time spent waiting
    for another process's write is not counted against the bound (D-0215).
    """
    limit = WRITE_SECONDS if seconds is None else seconds
    if not math.isfinite(limit) or not 0 < limit <= WRITE_SECONDS:
        raise ValueError("write deadline must be positive and cannot exceed the worker bound")
    if conn._write_deadline is not None:
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("bounded pipeline writes require the process main thread")
    global _fired
    started = time.monotonic()
    commits_before = conn._commit_serial
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    # Do not silently replace a caller's earlier deadline or recurring alarm.
    if previous_interval or 0 < previous_delay <= limit:
        raise RuntimeError("pipeline write deadline conflicts with an active earlier timer")
    expired = False
    cleaning = False
    error: BaseException | None = None

    def timeout(_number: int, _frame: FrameType | None) -> None:
        nonlocal expired
        expired = True
        # The body must be interrupted, but interrupting cancellation or error
        # translation would let this private KeyboardInterrupt escape instead
        # of rolling back as WriteDeadlineExceeded. Cleanup still records it.
        if not cleaning:
            raise _Expired()

    signal.signal(signal.SIGALRM, timeout)
    previous_deadline = conn._write_deadline
    deadline = started + limit
    conn._write_deadline = (
        min(deadline, previous_deadline) if previous_deadline is not None else deadline
    )
    try:
        try:
            try:
                # Delivery can occur as arming returns after a process pause.
                signal.setitimer(signal.ITIMER_REAL, limit)
                yield
            except BaseException as caught:
                error = caught
            finally:
                cleaning = True
                signal.setitimer(signal.ITIMER_REAL, 0)
        except _Expired as caught:
            # A one-shot owned alarm can land while entering the handler or
            # finally, before cleaning is set. Preserve the original failure
            # carried in its context, especially an uncertain commit or SIGINT.
            if not expired:
                raise
            if error is None:
                error = caught if caught.__context__ is None else caught.__context__
    finally:
        cleaning = True
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            conn._write_deadline = previous_deadline
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_delay:
                signal.setitimer(
                    signal.ITIMER_REAL,
                    max(0.000001, previous_delay - (time.monotonic() - started)),
                    previous_interval,
                )
    # Classify once, after cancellation and restoration. A late callback in
    # either cleanup block must still count and cannot turn an overrun green.
    expired = expired or isinstance(error, WriteTimeout) or time.monotonic() >= deadline
    if expired:
        _fired += 1
        if isinstance(error, _Expired):
            # Delivery can precede even the body handler's error assignment.
            # Its context still carries an in-flight fatal failure; do not
            # downgrade that failure into an ordinary retriable deadline.
            interrupted = error.__context__
            if isinstance(interrupted, ConnectionLost) or (
                isinstance(interrupted, (KeyboardInterrupt, SystemExit))
                and not isinstance(interrupted, _Expired)
            ):
                error = interrupted
        if isinstance(error, CommitUncertain):
            raise error
        if conn._commit_serial != commits_before:
            conn.close()
            raise CommitUncertain(
                "database commit crossed its acknowledgement deadline; reconcile retained state"
            ) from error
        if isinstance(error, (KeyboardInterrupt, SystemExit)) and not isinstance(error, _Expired):
            raise error
        if isinstance(error, ConnectionLost):
            raise error
        if conn.closed:
            raise ConnectionLost(
                "database session lost during write deadline; rollback could not be confirmed"
            ) from error
        raise WriteDeadlineExceeded(
            f"atomic write exceeded {limit:g} seconds; output rolled back"
        ) from error
    if error is not None:
        raise error
