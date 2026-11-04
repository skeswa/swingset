"""Session advisory locks retain the state/intent/heavy ordering (D-0215)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from swingset.clock import Clock, SystemClock

from .engine import Connection
from .engine.locks import release, try_lock, wait_lock

STATE_INTENT = "state.intent"
STATE_LOCK = "state.lock"
HEAVY_INTENT = "heavy.intent"
HEAVY_LOCK = "heavy.lock"
CONTROL_LOCK = "control.lock"
VENV_LOCK = "venv.lock"
HEAVY_TIMEOUT_SECONDS = 900.0
_LOOP_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FIXED_NAMES = frozenset(
    {STATE_INTENT, STATE_LOCK, HEAVY_INTENT, HEAVY_LOCK, CONTROL_LOCK, VENV_LOCK}
)


def is_lock_file(name: str) -> bool:
    """Recognize retained legacy artifacts; Postgres never creates these files."""
    return name in _FIXED_NAMES or (
        name.startswith("loop-") and name.endswith(".lock") and bool(_LOOP_NAME.match(name[5:-5]))
    )


def loop_lock_name(loop: str) -> str:
    if not _LOOP_NAME.match(loop):
        raise ValueError(f"invalid loop name: {loop!r}")
    return f"loop-{loop}.lock"


class LockBusy(RuntimeError):
    def __init__(self, reason: str, path: Path, *, label: str | None = None) -> None:
        super().__init__(f"{label or reason}: {path}")
        self.reason, self.path = reason, path


@dataclass
class Held:
    mode: str
    connection: Connection
    names: list[tuple[str, bool]] = field(default_factory=list)

    def release(self) -> None:
        while self.names:
            name, shared = self.names.pop()
            release(self.connection, name, shared=shared)


def exclusive(
    state_dir: Path,
    *,
    connection: Connection,
    intent: str = STATE_INTENT,
    lock: str = STATE_LOCK,
    timeout: float | None = 0,
    clock: Clock | None = None,
    busy: str = "in use",
    label: str | None = "state is already in use",
) -> Held:
    timer = clock or SystemClock()
    started = timer.monotonic()
    held = Held("exclusive", connection)
    try:
        for name in (intent, lock):
            remaining = None if timeout is None else max(0, timeout - (timer.monotonic() - started))
            if not wait_lock(
                connection, name, timeout=remaining, sleep=timer.sleep, monotonic=timer.monotonic
            ):
                raise LockBusy(busy, state_dir, label=label)
            held.names.append((name, False))
    except BaseException:
        held.release()
        raise
    return held


def shared(
    state_dir: Path,
    *,
    connection: Connection,
    intent: str = STATE_INTENT,
    lock: str = STATE_LOCK,
    loop: str | None = None,
    pending: str = "writer pending",
    running: str = "writer running",
) -> Held:
    held = Held("shared", connection)
    try:
        if loop is not None:
            own = loop_lock_name(loop)
            if not try_lock(connection, own):
                raise LockBusy("loop running", state_dir)
            held.names.append((own, False))
        if not try_lock(connection, intent, shared=True):
            raise LockBusy(pending, state_dir)
        try:
            if not try_lock(connection, lock, shared=True):
                raise LockBusy(running, state_dir)
            held.names.append((lock, True))
        finally:
            release(connection, intent, shared=True)
    except BaseException:
        held.release()
        raise
    return held


def try_loop(state_dir: Path, loop: str, *, connection: Connection) -> Held | None:
    name = loop_lock_name(loop)
    return Held("exclusive", connection, [(name, False)]) if try_lock(connection, name) else None


def heavy_exclusive(
    state_dir: Path,
    *,
    connection: Connection,
    timeout: float = HEAVY_TIMEOUT_SECONDS,
    clock: Clock | None = None,
) -> Held:
    return exclusive(
        state_dir,
        connection=connection,
        intent=HEAVY_INTENT,
        lock=HEAVY_LOCK,
        timeout=timeout,
        clock=clock,
        busy="heavy work running",
        label=None,
    )


def heavy_shared(state_dir: Path, *, connection: Connection) -> Held:
    return shared(
        state_dir,
        connection=connection,
        intent=HEAVY_INTENT,
        lock=HEAVY_LOCK,
        pending="heavy work pending",
        running="heavy work running",
    )
