"""The non-reentrant control-turn lock B precedes the writer lock C."""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator
from pathlib import Path

from .engine import Connection
from .engine.locks import lock_key, release, wait_lock


class ControlTimeout(RuntimeError):
    """No control mutation committed before the servicing deadline."""


def holds_control_lock(state_dir: Path, *, connection: Connection) -> bool:
    return (lock_key("B/control"), False) in connection._session_locks


@contextlib.contextmanager
def control_lock(state_dir: Path, *, connection: Connection, timeout: float = 60) -> Iterator[None]:
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("control timeout must be finite and nonnegative")
    if connection.in_transaction:
        raise RuntimeError("control lock B must be acquired before a write transaction")
    if not wait_lock(connection, "B/control", timeout=timeout):
        raise ControlTimeout("control servicing timed out; no change was persisted")
    try:
        yield
    finally:
        release(connection, "B/control")
