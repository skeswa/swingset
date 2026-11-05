"""Resolve an artifact directory to one explicitly configured Postgres database."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

SETTINGS = "database.json"
_resolver: Callable[[Path], str | None] | None = None


def state_directory(path: str | Path) -> Path:
    requested = Path(path)
    return requested.parent if requested.suffix in {".sqlite", ".db"} else requested


def configure_resolver(resolver: Callable[[Path], str | None] | None) -> None:
    """Install a local provisioning hook; normal application opens never create databases."""
    global _resolver
    _resolver = resolver


def read_settings(path: str | Path) -> dict[str, Any]:
    value = json.loads((state_directory(path) / SETTINGS).read_text())
    if not isinstance(value, dict) or not isinstance(value.get("dsn"), str):
        raise ValueError("invalid local database settings")
    if value.get("environment") not in {"dev", "dryrun", "production"}:
        raise ValueError("invalid local database environment")
    return value


def write_settings(
    path: str | Path, dsn: str, environment: str = "dev", *, control_dsn: str | None = None
) -> Path:
    if environment not in {"dev", "dryrun", "production"}:
        raise ValueError("invalid database environment")
    directory = state_directory(path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / SETTINGS
    temporary = directory / (SETTINGS + f".{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as output:
            settings = {"dsn": dsn, "environment": environment}
            if control_dsn is not None:
                settings["control_dsn"] = control_dsn
            json.dump(settings, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def resolve_dsn(path: str | Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    directory = state_directory(path)
    if (directory / SETTINGS).is_file():
        return str(read_settings(directory)["dsn"])
    if _resolver is not None:
        resolved = _resolver(directory)
        if resolved is not None:
            return resolved
    if os.environ.get("SWINGSET_TEST_RUN"):
        from swingset.dev.testing import resolve_state

        resolved = resolve_state(directory)
        if resolved is None:
            raise RuntimeError("test database provisioning is not configured")
        return resolved
    configured = os.environ.get("SWINGSET_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if configured:
        return configured
    raise RuntimeError("Postgres is not configured for this state directory")


def resolve_control_dsn(path: str | Path) -> str:
    """A controller has its own credential; worker credentials are never a fallback."""
    directory = state_directory(path)
    if (directory / SETTINGS).is_file():
        configured = read_settings(directory).get("control_dsn")
        if isinstance(configured, str) and configured:
            return configured
    configured = os.environ.get("SWINGSET_CONTROL_DATABASE_URL")
    if configured:
        return configured
    raise RuntimeError("control database credentials are not configured")
