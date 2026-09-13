"""Bound worker write phases so waiting operator controls can acquire SQLite."""

from __future__ import annotations

import contextlib
import signal
import sqlite3
import threading
import time
from collections.abc import Iterator
from types import FrameType

WRITE_SECONDS = 45.0


class WriteDeadlineExceeded(RuntimeError):
    """The atomic unit must roll back and await a changed recipe or explicit retry."""


class _Expired(BaseException):
    # Parser exception handlers must not turn an interrupted unit into success.
    pass


@contextlib.contextmanager
def bounded_write(conn: sqlite3.Connection) -> Iterator[None]:
    """Nested writes inherit the caller's bound; callers invoke this only once.

    The pipeline owns its SQLite writer on the main process thread. Operator
    controls use a separate connection and do not enter this worker boundary.
    Read snapshots and explicit maintenance migrations do not enter it either.
    """
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("bounded pipeline writes require the process main thread")
    started = time.monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    # Do not silently replace a caller's earlier deadline or recurring alarm.
    if previous_interval or 0 < previous_delay <= WRITE_SECONDS:
        raise RuntimeError("pipeline write deadline conflicts with an active earlier timer")
    expired = False

    def timeout(_number: int, _frame: FrameType | None) -> None:
        nonlocal expired
        expired = True
        raise _Expired()

    def progress() -> int:
        nonlocal expired
        expired = expired or time.monotonic() - started >= WRITE_SECONDS
        return int(expired)

    signal.signal(signal.SIGALRM, timeout)
    conn.set_progress_handler(progress, 1000)
    signal.setitimer(signal.ITIMER_REAL, WRITE_SECONDS)
    try:
        yield
        if expired or time.monotonic() - started >= WRITE_SECONDS:
            raise _Expired()
    except BaseException as error:
        if expired:
            raise WriteDeadlineExceeded(
                f"atomic write exceeded {WRITE_SECONDS:g} seconds; output rolled back"
            ) from error
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        conn.set_progress_handler(None, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.000001, previous_delay - (time.monotonic() - started)),
                previous_interval,
            )
