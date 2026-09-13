"""Short admission/control mutex, also fencing schema and restore replacement."""

from __future__ import annotations

import contextlib
import fcntl
import math
import time
from collections.abc import Iterator
from pathlib import Path


class ControlTimeout(RuntimeError):
    """No control mutation was committed before the servicing deadline."""


@contextlib.contextmanager
def control_lock(state_dir: Path, *, timeout: float = 60) -> Iterator[None]:
    """Never acquire this mutex while holding a SQLite write transaction.

    Lock order is state.lock (data writers only), control.lock, SQLite. A
    waiting control owns this mutex while an already-admitted unit commits;
    that unit must settle without acquiring this mutex.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("control timeout must be finite and nonnegative")
    started = time.monotonic()
    with (state_dir / "control.lock").open("a+b") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise ControlTimeout(
                        "control servicing timed out; no change was persisted"
                    ) from exc
                time.sleep(min(0.02, remaining))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
