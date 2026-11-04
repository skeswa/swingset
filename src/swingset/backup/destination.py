"""Resolve an explicitly provisioned empty database for an operator restore."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

from swingset.state import engine


@contextlib.contextmanager
def owner(state: Path, dsn: str | None = None) -> Iterator[engine.Connection]:
    configured = dsn or os.environ.get("SWINGSET_OWNER_DATABASE_URL")
    if not configured and os.environ.get("SWINGSET_TEST_RUN"):
        from swingset.dev.testing import provision_empty

        configured = provision_empty(state)
    if not configured:
        raise ValueError("restore requires a fresh database and SWINGSET_OWNER_DATABASE_URL")
    with engine.connect(configured, application_name="swingset-restore") as conn:
        conn.state_dir = state
        if (state / "database.json").exists():
            from psycopg.conninfo import conninfo_to_dict

            worker = conninfo_to_dict(engine.resolve_dsn(state))
            if any(
                str(worker.get(key, "")) != str(getattr(conn.info, key))
                for key in ("host", "port", "dbname")
            ):
                raise ValueError("restore owner and worker database destinations differ")
        yield conn
