"""Explicit owner migration; the worker open path only validates its schema."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import engine, postgres_schema
from .control_lock import control_lock
from .engine.locks import acquire_writer
from .engine.settings import SETTINGS
from .rwlock import exclusive


def run(
    state: Path, owner_dsn: str, *, environment: str | None = None, timeout: float = 60
) -> dict[str, Any]:
    if not owner_dsn:
        raise ValueError("SWINGSET_OWNER_DATABASE_URL is required for an explicit migration")
    with engine.connect(owner_dsn, application_name="swingset-migrate", timeout=timeout) as conn:
        conn.state_dir = state
        held = exclusive(state, connection=conn, timeout=timeout)
        try:
            with control_lock(state, connection=conn, timeout=timeout):
                conn.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
                try:
                    acquire_writer(conn)
                    from .postgres_bootstrap import prepared

                    initial_prepared = prepared(conn)
                    existing = not initial_prepared and (
                        conn.execute("SELECT to_regclass('ops.meta')").fetchone()[0] is not None
                    )
                    before = (
                        int(
                            conn.execute(
                                "SELECT value FROM ops.schema_identity() WHERE key='schema_version'"
                            ).fetchone()[0]
                        )
                        if existing
                        else 0
                    )
                    if before > postgres_schema.SCHEMA_VERSION:
                        raise RuntimeError("Postgres migration refuses a schema downgrade")
                    if existing:
                        logical_version = int(
                            conn.execute(
                                "SELECT value FROM ops.meta WHERE key='schema_version'"
                            ).fetchone()[0]
                        )
                        if logical_version > postgres_schema.SOURCE_SCHEMA_VERSION:
                            raise RuntimeError("Postgres migration refuses a schema downgrade")
                    if (state / SETTINGS).is_file():
                        with engine.connect(engine.resolve_dsn(state), read_only=True) as worker:
                            target_query = (
                                "SELECT oid,inet_server_addr(),inet_server_port() FROM pg_database WHERE datname=current_database()"
                                if initial_prepared
                                else "SELECT environment,database_id FROM ops.environment_identity WHERE singleton=1"
                            )
                            actual = conn.execute(target_query).fetchone()
                            expected = worker.execute(target_query).fetchone()
                            if actual is None or actual != expected:
                                raise ValueError("owner and worker database identities differ")
                    if (
                        existing
                        and conn.execute(
                            "SELECT to_regclass('ops.execution_admissions')"
                        ).fetchone()[0]
                        is not None
                    ):
                        from .controls import recover_admissions

                        recover_admissions(conn, now=datetime.now(UTC))
                    conn.commit()
                except BaseException:
                    if not conn.closed:
                        with contextlib.suppress(engine.Error):
                            conn.rollback()
                    raise
                conn.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
                try:
                    acquire_writer(conn)
                    result = postgres_schema.migrate(conn, environment=environment)
                    conn.commit()
                except BaseException:
                    if not conn.closed:
                        with contextlib.suppress(engine.Error):
                            conn.rollback()
                    raise
                return {
                    "schema_before": before,
                    "schema_after": result["schema_version"],
                    "migration_checksum": result["migration_checksum"],
                }
        finally:
            held.release()
