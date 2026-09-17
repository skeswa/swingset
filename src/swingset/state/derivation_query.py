"""Read-snapshot-local memoization; discarded after the work query finishes."""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast


@dataclass
class _Cache:
    connection: sqlite3.Connection
    version: tuple[int, int]
    values: dict[object, Any] = field(default_factory=dict)


_active: ContextVar[_Cache | None] = ContextVar("derivation_query_cache", default=None)

CURRENTNESS_LIMIT = 2048


@dataclass
class _ReadSnapshot:
    connection: sqlite3.Connection
    total_changes: int
    data_version: int
    values: OrderedDict[object, bool] = field(default_factory=OrderedDict)


_snapshot: ContextVar[_ReadSnapshot | None] = ContextVar("derivation_read_snapshot", default=None)


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """Own one read transaction for selection, never alter a caller transaction.

    Returned work remains a hint: normal admission rechecks current controls and
    dependencies after this snapshot closes. No cache survives this boundary.
    """
    if conn.in_transaction:
        with query(conn):
            yield
        return
    previous = int(conn.execute("PRAGMA query_only").fetchone()[0])
    conn.execute("PRAGMA query_only=ON")
    token = None
    try:
        conn.execute("BEGIN")
        state = _ReadSnapshot(conn, *_version(conn))
        token = _snapshot.set(state)
        with query(conn):
            yield
        if conn.total_changes != state.total_changes:
            raise RuntimeError("read selection changed database state")
    finally:
        if token is not None:
            _snapshot.reset(token)
        conn.rollback()
        conn.execute("PRAGMA query_only=ON" if previous else "PRAGMA query_only=OFF")


def currentness(conn: sqlite3.Connection, key: object, compute: Callable[[], bool]) -> bool:
    """Bounded answers only inside an owned, still read-only selection snapshot."""
    state = _snapshot.get()
    if (
        state is None
        or state.connection is not conn
        or not conn.in_transaction
        or conn.total_changes != state.total_changes
        or not conn.execute("PRAGMA query_only").fetchone()[0]
    ):
        return compute()
    version = int(conn.execute("PRAGMA data_version").fetchone()[0])
    if version != state.data_version:
        state.values.clear()
        state.data_version = version
    if key in state.values:
        state.values.move_to_end(key)
        return state.values[key]
    value = compute()
    state.values[key] = value
    if len(state.values) > CURRENTNESS_LIMIT:
        state.values.popitem(last=False)
    return value


def _version(conn: sqlite3.Connection) -> tuple[int, int]:
    return conn.total_changes, int(conn.execute("PRAGMA data_version").fetchone()[0])


@contextmanager
def query(conn: sqlite3.Connection) -> Iterator[None]:
    previous = _active.get()
    if previous is not None and previous.connection is conn:
        yield
        return
    token = _active.set(_Cache(conn, _version(conn)))
    try:
        yield
    finally:
        _active.reset(token)


def memo[T](conn: sqlite3.Connection, key: object, compute: Callable[[], T]) -> T:
    cache = _active.get()
    # SAVEPOINT rollback does not decrement total_changes. Never retain cache
    # values across writes in a mutable transaction; read-only snapshots are safe.
    writable_transaction = (
        conn.in_transaction and not conn.execute("PRAGMA query_only").fetchone()[0]
    )
    if cache is None or cache.connection is not conn or writable_transaction:
        return compute()
    version = _version(conn)
    if version != cache.version:
        cache.values.clear()
        cache.version = version
    if key not in cache.values:
        cache.values[key] = compute()
    return cast(T, cache.values[key])
