"""Explicit connection-bound artifact access and injected observation clock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from swingset.clock import Clock
from swingset.fetch.archive import Archive
from swingset.state import engine


@dataclass(frozen=True)
class _Provider:
    connection: engine.Connection
    archive: Archive
    clock: Clock | None
    observed_at: datetime | None


_provider: ContextVar[_Provider | None] = ContextVar("event_artifact_provider", default=None)


@contextmanager
def artifact_source(
    conn: engine.Connection,
    archive: Archive,
    *,
    clock: Clock | None = None,
    observed_at: datetime | None = None,
) -> Iterator[None]:
    """Capture requires an injected clock; pinned validation only needs files."""
    if archive.recovery is not None:
        raise ValueError("release evidence cannot recover artifacts")
    if observed_at is not None and (observed_at.tzinfo is None or observed_at.utcoffset() is None):
        raise ValueError("artifact observation time must be timezone aware")
    token = _provider.set(_Provider(conn, archive, clock, observed_at))
    try:
        yield
    finally:
        _provider.reset(token)


def source(conn: engine.Connection) -> Archive | None:
    value = _provider.get()
    return value.archive if value is not None and value.connection is conn else None


def observation_time(conn: engine.Connection) -> datetime:
    value = _provider.get()
    if value is None or value.connection is not conn:
        raise ValueError("local evidence capture requires an injected observation clock")
    if value.observed_at is not None:
        return value.observed_at
    if value.clock is None:
        raise ValueError("local evidence capture requires an injected observation clock")
    return value.clock.now()
