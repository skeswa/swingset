"""Reader-writer file locks over one state directory (D-0215).

Two pairs of files, each an intent and a lock:

- `state.intent` and `state.lock` guard the state. An exclusive holder (the
  cycle, every CLI mutation, backup, restore, `gc`, `accept-year`) takes the
  intent exclusive, then the lock exclusive, and keeps both. A loop takes its
  own `loop-<name>.lock` exclusive, then the intent shared, then the lock
  shared, and lets the intent go. A waiting exclusive holder owns the intent,
  so no new loop tick starts while it waits, and it is never starved.
- `heavy.intent` and `heavy.lock` keep memory-heavy work apart: release takes
  them exclusive, derive shared.

Loops never wait: every shared step is non-blocking. `flock` locks belong to
the open file, so two handles in one process conflict like two processes.
"""

from __future__ import annotations

import fcntl
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from swingset.clock import Clock, SystemClock

STATE_INTENT = "state.intent"
STATE_LOCK = "state.lock"
HEAVY_INTENT = "heavy.intent"
HEAVY_LOCK = "heavy.lock"
CONTROL_LOCK = "control.lock"
#: The service's `uv sync` lock (nix/module.nix): a lock, never state.
VENV_LOCK = "venv.lock"
#: Release waits this long for derive's project units to finish.
HEAVY_TIMEOUT_SECONDS = 900.0

_LOOP_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FIXED_NAMES = frozenset(
    {STATE_INTENT, STATE_LOCK, HEAVY_INTENT, HEAVY_LOCK, CONTROL_LOCK, VENV_LOCK}
)


def is_lock_file(name: str) -> bool:
    """Whether a top-level state file is one of these locks, never state."""
    return name in _FIXED_NAMES or (
        name.startswith("loop-") and name.endswith(".lock") and bool(_LOOP_NAME.match(name[5:-5]))
    )


def loop_lock_name(loop: str) -> str:
    if not _LOOP_NAME.match(loop):
        raise ValueError(f"invalid loop name: {loop!r}")
    return f"loop-{loop}.lock"


class LockBusy(RuntimeError):
    """A lock was held elsewhere; `reason` says which one."""

    def __init__(self, reason: str, path: Path, *, label: str | None = None) -> None:
        super().__init__(f"{label or reason}: {path}")
        self.reason = reason
        self.path = path


@dataclass
class Held:
    """Locks this process holds, released in reverse order."""

    mode: str
    handles: list[IO[bytes]] = field(default_factory=list)

    def release(self) -> None:
        while self.handles:
            handle = self.handles.pop()
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _try(handle: IO[bytes], operation: int) -> bool:
    try:
        fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _wait(
    handle: IO[bytes], operation: int, *, timeout: float | None, clock: Clock, started: float
) -> bool:
    """Poll a non-blocking lock until it is taken or the shared budget is spent.

    `timeout` 0 is one try; None waits without end. The clock is the caller's,
    so a test clock can run the wait.
    """
    while True:
        if _try(handle, operation):
            return True
        if timeout is not None and clock.now().timestamp() - started >= timeout:
            return False
        clock.sleep(0.1)


def exclusive(
    state_dir: Path,
    *,
    intent: str = STATE_INTENT,
    lock: str = STATE_LOCK,
    timeout: float | None = 0,
    clock: Clock | None = None,
    busy: str = "in use",
    label: str | None = "state is already in use",
) -> Held:
    """Take the intent, then the lock, both exclusive, within one timeout."""
    active = clock or SystemClock()
    started = active.now().timestamp()
    held = Held("exclusive")
    try:
        for name in (intent, lock):
            handle = (state_dir / name).open("a+b")
            if not _wait(handle, fcntl.LOCK_EX, timeout=timeout, clock=active, started=started):
                handle.close()
                raise LockBusy(busy, state_dir, label=label)
            held.handles.append(handle)
    except BaseException:
        held.release()
        raise
    return held


def shared(
    state_dir: Path,
    *,
    intent: str = STATE_INTENT,
    lock: str = STATE_LOCK,
    loop: str | None = None,
    pending: str = "writer pending",
    running: str = "writer running",
) -> Held:
    """Take the loop's own lock, the intent, then the lock, never waiting.

    The intent is released once the lock is shared: it only proves that no
    exclusive holder was waiting when this one arrived.
    """
    held = Held("shared")
    try:
        if loop is not None:
            own_path = state_dir / loop_lock_name(loop)
            own = own_path.open("a+b")
            if not _try(own, fcntl.LOCK_EX):
                own.close()
                raise LockBusy("loop running", own_path)
            held.handles.append(own)
        with (state_dir / intent).open("a+b") as intent_handle:
            if not _try(intent_handle, fcntl.LOCK_SH):
                raise LockBusy(pending, state_dir)
            try:
                handle = (state_dir / lock).open("a+b")
                if not _try(handle, fcntl.LOCK_SH):
                    handle.close()
                    raise LockBusy(running, state_dir)
                held.handles.append(handle)
            finally:
                fcntl.flock(intent_handle.fileno(), fcntl.LOCK_UN)
    except BaseException:
        held.release()
        raise
    return held


def try_loop(state_dir: Path, loop: str) -> Held | None:
    """Take another loop's own lock without waiting; None while it runs.

    The drill holds `loop-backup.lock` this way, so a backup set is never
    written, or pruned, beside it (phase 4).
    """
    path = state_dir / loop_lock_name(loop)
    handle = path.open("a+b")
    if not _try(handle, fcntl.LOCK_EX):
        handle.close()
        return None
    return Held("exclusive", [handle])


def heavy_exclusive(
    state_dir: Path, *, timeout: float = HEAVY_TIMEOUT_SECONDS, clock: Clock | None = None
) -> Held:
    """Release holds this while it builds; derive then runs parse units only."""
    return exclusive(
        state_dir,
        intent=HEAVY_INTENT,
        lock=HEAVY_LOCK,
        timeout=timeout,
        clock=clock,
        busy="heavy work running",
        label=None,
    )


def heavy_shared(state_dir: Path) -> Held:
    """Derive holds this for a tick that runs units; without it the tick starts none."""
    return shared(
        state_dir,
        intent=HEAVY_INTENT,
        lock=HEAVY_LOCK,
        pending="heavy work pending",
        running="heavy work running",
    )
