"""Namespaced, database-local advisory locks on the caller's connection."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

from .connection import Connection


def lock_key(name: str) -> int:
    return int.from_bytes(
        hashlib.sha256(("swingset/" + name).encode()).digest()[:8], "big", signed=True
    )


def try_lock(conn: Connection, name: str, *, shared: bool = False) -> bool:
    key = lock_key(name)
    # Session advisory locks are re-entrant in Postgres. Our ownership protocol
    # deliberately is not: two operations on the same session must not overlap.
    if any(held_key == key for held_key, _ in conn._session_locks):
        return False
    function = "pg_try_advisory_lock_shared" if shared else "pg_try_advisory_lock"
    row = conn.execute(f"SELECT {function}(%s) AS acquired", (key,)).fetchone()
    acquired = row is not None and bool(row[0])
    if acquired:
        conn._session_locks.add((key, shared))
    return acquired


def release(conn: Connection, name: str, *, shared: bool = False) -> None:
    key = lock_key(name)
    if (key, shared) not in conn._session_locks:
        return
    conn._session_locks.remove((key, shared))
    if conn.closed:
        return  # PostgreSQL already freed every lock when this session died.
    function = "pg_advisory_unlock_shared" if shared else "pg_advisory_unlock"
    conn.execute(f"SELECT {function}(%s) AS released", (key,)).fetchone()


def wait_lock(
    conn: Connection,
    name: str,
    *,
    shared: bool = False,
    timeout: float | None = 0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    started = monotonic()
    while not try_lock(conn, name, shared=shared):
        if timeout is not None:
            remaining = timeout - (monotonic() - started)
            if remaining <= 0:
                return False
            sleep(min(0.02, remaining))
        else:
            sleep(0.02)
    return True


def acquire_writer(conn: Connection) -> None:
    """C is the first statement after a READ COMMITTED write BEGIN."""
    if not conn.in_transaction:
        raise RuntimeError("write lock requires an explicit transaction")
    conn.execute("SELECT pg_advisory_xact_lock(%s) AS acquired", (lock_key("C/write"),)).fetchone()
