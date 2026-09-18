"""Short admission/control mutex, also fencing schema and restore replacement."""

from __future__ import annotations

import contextlib
import fcntl
import math
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path


class ControlTimeout(RuntimeError):
    """No control mutation was committed before the servicing deadline."""


#: Which thread currently holds which state directory's mutex. The mutex itself
#: is not re-entrant, and taking it twice in one thread still deadlocks: two
#: control mutations by one thread are two mutations, and the second must wait
#: like anyone else. This is here so that a caller which has already fenced a
#: whole operation with the mutex can ask, and keep its own fence instead of
#: taking a second one
#: ([D-0164](../../../journal/decisions/0164-a-checkpoint-holds-the-control-lock-across-its-closure-and-copy.md)).
_held: set[tuple[int, str]] = set()
_guard = threading.Lock()


def holds_control_lock(state_dir: Path) -> bool:
    """Whether this thread is already inside `control_lock` for this directory."""
    with _guard:
        return (threading.get_ident(), os.path.realpath(state_dir)) in _held


@contextlib.contextmanager
def control_lock(state_dir: Path, *, timeout: float = 60) -> Iterator[None]:
    """Never acquire this mutex while holding a SQLite write transaction.

    Lock order is state.lock (data writers only), control.lock, SQLite. A
    waiting control owns this mutex while an already-admitted unit commits;
    that unit must settle without acquiring this mutex.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("control timeout must be finite and nonnegative")
    key = (threading.get_ident(), os.path.realpath(state_dir))
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
        with _guard:
            _held.add(key)
        try:
            yield
        finally:
            with _guard:
                _held.discard(key)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
