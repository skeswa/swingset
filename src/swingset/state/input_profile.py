"""Measure normal input acceptance on one owned copy, then roll it back."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from swingset.clock import Clock
from swingset.fetch.archive import canonical, digest, durable_write

from . import engine, inputs, postgres_schema, verify_graph
from .db import Database, open_database
from .inputs import InputBundle
from .postgres_copy import Copies
from .write_deadline import WRITE_SECONDS, WriteDeadlineExceeded

FORMAT = "swingset-input-acceptance-profile-v1"
_MAX_STATEMENTS = 10000
_TABLES = (
    "watches",
    "accepted_inputs",
    "pending_work",
    "work_generations",
    "history_dispatch_fence",
    "meta",
    "identity_decisions",
    "identity_source_refs",
    "identity_journal_acceptances",
)


class _Rollback(Exception):
    pass


def _fingerprint(database: Database) -> str:
    tables = {}
    for name in _TABLES:
        tables[name] = tuple(
            database.connection.execute(
                "SELECT count(*),md5(coalesce(string_agg(h,'' ORDER BY h),'')) "
                "FROM (SELECT md5(row_to_json(t)::text) AS h FROM "
                + postgres_schema.qualified(name)
                + " AS t) AS rows"
            ).fetchone()
        )
    return digest(canonical(tables))


def _category(query: str) -> str:
    if query.startswith("UPDATE WATCHES"):
        return "watch_update"
    if query.startswith("INSERT INTO PENDING_WORK"):
        return "enqueue"
    if query.startswith("INSERT INTO ACCEPTED_INPUTS"):
        return "accepted_digest"
    if query.startswith("SELECT") and "SNAPSHOTS" in query:
        return "snapshot_select"
    return "other"


def _trial(database: Database, action: Callable[[], object]) -> dict[str, Any]:
    connection = database.connection
    original = connection.execute
    statements: list[dict[str, Any]] = []
    current: dict[str, str | None] = {"consumer": None, "input_name": None}

    def execute(query: Any, params: Any = None, **kwargs: Any) -> Any:
        text = str(query).strip().upper()
        if text.startswith(("ROLLBACK", "RELEASE")):
            return original(query, params, **kwargs)
        if len(statements) >= _MAX_STATEMENTS:
            raise RuntimeError("input profiling statement bound exceeded")
        if text.startswith("SELECT DIGEST FROM ACCEPTED_INPUTS") and params is not None:
            current.update(consumer=str(params[0]), input_name=str(params[1]))
        started = time.monotonic()
        error = None
        result = None
        try:
            result = original(query, params, **kwargs)
            return result
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            statements.append(
                {
                    **current,
                    "category": _category(text),
                    "seconds": time.monotonic() - started,
                    "rows": None if result is None else result.rowcount,
                    "error_type": error,
                }
            )

    before = _fingerprint(database)
    complete = False
    error = None
    started = time.monotonic()
    try:
        # Replace only this connection's method; other connections and input
        # callbacks keep their ordinary behavior. Do no disk I/O in the write.
        with patch.object(connection, "execute", new=execute):
            with database.transaction():
                action()
                complete = True
                raise _Rollback()
    except _Rollback:
        pass
    except WriteDeadlineExceeded as exc:
        error = type(exc).__name__
    seconds = time.monotonic() - started
    after = _fingerprint(database)
    if before != after:
        raise RuntimeError("input profiling rollback fingerprint differs")
    return {
        "acceptance_complete": complete,
        "error_type": error,
        "seconds": seconds,
        "write_budget_seconds": WRITE_SECONDS,
        "rollback_fingerprint_equal": True,
        "fingerprint": before,
        "statements": statements,
    }


def _worker_session(database: Database, worker: str) -> None:
    connection = database.connection
    if not connection.execute(
        "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
    ).fetchone()[0]:
        raise ValueError("function profiling requires an isolated scratch administrator")
    connection.execute("SET track_functions='pl'")
    connection.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(worker)))
    capabilities = connection.execute(
        "SELECT current_user,session_user,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,"
        "pg_has_role(current_user,'swingset_owner','USAGE') AS owner_role,"
        "pg_has_role(current_user,'swingset_worker','USAGE') AS worker_role "
        "FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if tuple(capabilities[:2]) != (worker, worker) or any(capabilities[2:7]) or not capabilities[7]:
        raise ValueError("profiling did not enter the unprivileged worker session")


def _functions(database: Database) -> list[dict[str, Any]]:
    # The trial is over and rolled back before regaining the scratch login.
    # Flush only this backend's counters; never reset cluster statistics.
    connection = database.connection
    connection.execute("RESET SESSION AUTHORIZATION")
    connection.execute("SELECT pg_stat_force_next_flush()")
    connection.execute("SELECT pg_stat_clear_snapshot()")
    rows = connection.execute(
        "SELECT schemaname,funcname,calls,total_time,self_time FROM pg_stat_user_functions "
        "WHERE calls>0 ORDER BY self_time DESC,schemaname,funcname"
    )
    return [
        dict(zip(("schema", "function", "calls", "total_ms", "self_ms"), tuple(row), strict=True))
        for row in rows
    ]


def validate_paths(
    checkpoint: Path, receipt_path: Path, *, capture_state: Path | None = None
) -> Path:
    """Admit every output before capture; legacy file aliases name their parent."""
    source = engine.state_directory(checkpoint).resolve()

    def overlaps(left: Path, right: Path) -> bool:
        return left.is_relative_to(right) or right.is_relative_to(left)

    capture = None if capture_state is None else engine.state_directory(capture_state).resolve()
    if capture is not None and overlaps(source, capture):
        raise ValueError("profiling needs a separate writable capture state")
    configured = os.environ.get("SWINGSET_COPY_ROOT")
    if not configured or not Path(configured).is_absolute():
        raise ValueError("input profiling requires an explicit fresh copy registry")
    registry = Path(configured)
    if registry.is_symlink() or (registry.exists() and any(registry.iterdir())):
        raise ValueError("input profiling refuses an existing ownership registry")
    registry = registry.resolve()
    output = receipt_path.resolve()
    if overlaps(source, registry) or overlaps(source, output):
        raise ValueError("profiling outputs must be outside the retained checkpoint")
    if overlaps(registry, output) or (capture is not None and overlaps(registry, capture)):
        raise ValueError("profiling registry must be separate from capture and receipt")
    scratch = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()).resolve()
    if scratch.is_relative_to(source):
        raise ValueError("profiling scratch must be outside the retained checkpoint")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise FileExistsError("profiling receipt already exists")
    return scratch


def run(
    checkpoint: Path,
    bundle: InputBundle,
    clock: Clock,
    *,
    receipt_path: Path,
) -> dict[str, Any]:
    """Profile a dev checkpoint without changing it or accepting a partial bundle.

    An explicit empty external registry survives failed cleanup. The temporary
    files and newly owned database belong only to this invocation. Function
    tracking is set by the scratch login; acceptance runs as the worker login.
    A completed diagnostic is never a graph-verification receipt.
    """
    scratch = validate_paths(checkpoint, receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    value: dict[str, Any] = {
        "format": FORMAT,
        "diagnostic_complete": False,
        "graph_proof": False,
        "phase": "admission",
        "limitations": [
            "selected-table rollback fingerprints are not a full typed audit",
            "function tracking and buffered timing add diagnostic overhead",
            "function total time includes nested calls; do not sum it",
        ],
    }
    with receipt_path.open("x") as stream:
        os.chmod(receipt_path, 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(receipt_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

    def record(phase: str) -> None:
        value["phase"] = phase
        durable_write(receipt_path, canonical(value))

    try:
        with (
            open_database(
                checkpoint, read_only=True, lock=False, allow_checkpoint_schema=True
            ) as source,
            tempfile.TemporaryDirectory(prefix="swingset-input-profile-", dir=scratch) as temporary,
        ):
            if source.environment != "dev":
                raise ValueError("input profiling requires a private dev checkpoint")
            with Copies(checkpoint) as copies:
                frozen = Path(temporary) / "frozen"
                value.update(
                    owner_token=copies.identifier, ownership_registry=str(copies.path.parent)
                )
                record("freeze")
                copies.freeze(source.connection, frozen)
                owned = copies.record["databases"]
                if len(owned) != 1:
                    raise RuntimeError("input profiling requires exactly one owned copy")
                database_name = owned[0]
                worker = conninfo_to_dict(copies.worker_dsn).get("user")
                if not isinstance(worker, str) or not worker:
                    raise ValueError("input profiling requires an explicit worker login")
                admin = make_conninfo(copies.admin_dsn, dbname=database_name)
                with open_database(frozen, dsn=admin, lock=False) as database:
                    if (
                        database.connection.info.dbname != database_name
                        or database.environment != "dev"
                    ):
                        raise ValueError("profiling destination identity differs")
                    _worker_session(database, worker)
                    expected = verify_graph.enumerate_units(database.connection)
                    value.update(
                        expected_unit_count=len(expected),
                        unit_set_digest=digest(canonical(expected)),
                    )
                    record("acceptance")
                    value["trial"] = _trial(
                        database, lambda: inputs.accept(database, bundle, clock)
                    )
                    value["functions"] = _functions(database)
                    record("cleanup")
            value["owned_database_cleanup_complete"] = True
        value["diagnostic_complete"] = True
        record("complete")
    except BaseException as exc:
        value["error_type"] = type(exc).__name__
        record("failed")
        raise
    return value
