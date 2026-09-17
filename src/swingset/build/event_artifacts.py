"""Explicit connection-bound artifact access and injected observation clock."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from swingset.clock import Clock
from swingset.fetch.archive import Archive


@dataclass(frozen=True)
class _Provider:
    connection: sqlite3.Connection
    archive: Archive
    clock: Clock | None


_provider: ContextVar[_Provider | None] = ContextVar("event_artifact_provider", default=None)


@contextmanager
def artifact_source(
    conn: sqlite3.Connection, archive: Archive, *, clock: Clock | None = None
) -> Iterator[None]:
    """Capture requires an injected clock; pinned validation only needs files."""
    if archive.recovery is not None:
        raise ValueError("release evidence cannot recover artifacts")
    token = _provider.set(_Provider(conn, archive, clock))
    try:
        yield
    finally:
        _provider.reset(token)


def source(conn: sqlite3.Connection) -> Archive | None:
    value = _provider.get()
    return value.archive if value is not None and value.connection is conn else None


def observation_time(conn: sqlite3.Connection) -> datetime:
    value = _provider.get()
    if value is None or value.connection is not conn or value.clock is None:
        raise ValueError("local evidence capture requires an injected observation clock")
    return value.clock.now()
