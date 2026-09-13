"""Read-snapshot-local memoization; discarded after the work query finishes."""

from __future__ import annotations

import sqlite3
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
