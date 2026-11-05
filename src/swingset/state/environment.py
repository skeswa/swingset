"""Stored database identity governs every external effect, independently of flags."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx


class EnvironmentRefused(RuntimeError):
    """An external effect does not belong to this database's stored environment."""


class FixtureIO:
    """Explicit marker for an in-memory fixture implementation of a non-HTTP API."""


class RehearsalIO:
    """An adapter must authorize the stored UUID and operation against its policy."""

    def authorize(self, database_id: str, operation: str) -> None:
        raise EnvironmentRefused("this adapter has no rehearsal authorization policy")


@dataclass(frozen=True)
class Identity:
    environment: str
    database_id: str


def identity(connection: Any) -> Identity:
    row = connection.execute(
        "SELECT environment,database_id FROM ops.environment_identity WHERE singleton=1"
    ).fetchone()
    if row is None or row[0] not in {"dev", "dryrun", "production"}:
        raise EnvironmentRefused("Database environment identity is missing or invalid")
    try:
        UUID(str(row[1]))
    except (ValueError, TypeError, AttributeError):
        raise EnvironmentRefused("Database environment identity is missing or invalid") from None
    stored = Identity(str(row[0]), str(row[1]))
    requested = os.environ.get("SWINGSET_ENV")
    if requested is not None and requested != stored.environment:
        raise EnvironmentRefused("SWINGSET_ENV conflicts with the database's stored environment")
    return stored


def ensure_dev(connection: Any) -> Identity:
    stored = identity(connection)
    if stored.environment != "dev":
        raise EnvironmentRefused("This command requires a development database")
    return stored


def ensure_external_allowed(connection: Any, operation: str, transport: object = None) -> None:
    stored = identity(connection)
    if stored.environment == "production":
        return
    if isinstance(transport, (httpx.MockTransport, FixtureIO)):
        return
    if stored.environment == "dryrun" and isinstance(transport, RehearsalIO):
        transport.authorize(stored.database_id, operation)
        return
    if stored.environment == "dryrun":
        raise EnvironmentRefused("this adapter cannot authorize rehearsal external effects")
    raise EnvironmentRefused(f"A development database cannot perform real {operation}")
