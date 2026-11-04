"""Bound worker writes in Python and on the PostgreSQL server."""

from __future__ import annotations

import contextlib
import math
import signal
import threading
import time
from collections.abc import Iterator
from types import FrameType

from .engine import CommitUncertain, Connection, WriteTimeout

# A safety net, not the bound: every write is small by construction (I2), so
# a firing is an alarm (D-0217). No unit gets a longer limit.
WRITE_SECONDS = 60.0
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

    def timeout(_number: int, _frame: FrameType | None) -> None:
        nonlocal expired
        expired = True
        raise _Expired()

    signal.signal(signal.SIGALRM, timeout)
    previous_deadline = conn._write_deadline
    deadline = started + limit
    conn._write_deadline = (
        min(deadline, previous_deadline) if previous_deadline is not None else deadline
    )
    try:
        # Delivery can occur as arming returns after the process was paused.
        # Protect that boundary just like delivery during the write body.
        signal.setitimer(signal.ITIMER_REAL, limit)
        yield
        signal.setitimer(signal.ITIMER_REAL, 0)
        # A body that ran past the limit before the alarm could land (it lands
        # only between bytecodes) is cut off the same way.
        expired = expired or time.monotonic() - started >= limit
        if expired:
            raise _Expired()
    except BaseException as error:
        signal.setitimer(signal.ITIMER_REAL, 0)
        expired = expired or isinstance(error, WriteTimeout) or time.monotonic() >= deadline
        if expired:
            _fired += 1
            if isinstance(error, CommitUncertain):
                raise
            if conn._commit_serial != commits_before:
                conn.close()
                raise CommitUncertain(
                    "database commit crossed its acknowledgement deadline; reconcile retained state"
                ) from error
            raise WriteDeadlineExceeded(
                f"atomic write exceeded {limit:g} seconds; output rolled back"
            ) from error
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        conn._write_deadline = previous_deadline
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.000001, previous_delay - (time.monotonic() - started)),
                previous_interval,
            )
