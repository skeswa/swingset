"""Lazy test database provisioning shared by pytest and its child processes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from . import services

RUN_ENV = "SWINGSET_TEST_RUN"
OWNER_ENV = "SWINGSET_TEST_OWNER"
RUN_PATTERN = re.compile(r"[0-9a-f]{24}\Z")
_READY: dict[tuple[int, Path, str, str], tuple[services.Service, str]] = {}


def _record_path(identifier: str) -> Path:
    if not RUN_PATTERN.fullmatch(identifier):
        raise RuntimeError("Invalid Postgres test-run identity")
    return services.home() / "test-runs" / f"{identifier}.json"


def begin_run() -> str:
    """Register a run without importing psycopg or contacting Docker."""
    identifier = uuid.uuid4().hex[:24]
    with services.lock(services.home() / "services.lock"):
        services.write_private(
            _record_path(identifier),
            {"pid": os.getpid(), "process_group": os.getpgrp(), "databases": {}},
        )
    return identifier


def _read(identifier: str) -> dict[str, Any]:
    path = _record_path(identifier)
    if not path.is_file():
        raise RuntimeError("Postgres test owner metadata is missing")
    record = json.loads(path.read_text())
    if not services.alive(int(record["pid"])):
        raise RuntimeError("Postgres test owner is no longer running")
    if not isinstance(record, dict):
        raise RuntimeError("Invalid Postgres test owner metadata")
    return record


def resolve_state(path: Path) -> str | None:
    """Clone the immutable template on the first open of each test state path."""
    identifier = os.environ.get(RUN_ENV)
    if identifier is None:
        return None
    from psycopg import sql

    from swingset.state import postgres_schema
    from swingset.state.engine import write_settings

    state = path.resolve()
    record_path = _record_path(identifier)
    name = f"sst_{identifier}_{hashlib.sha256(str(state).encode()).hexdigest()[:20]}"
    cache_key = (
        os.getpid(),
        services.home(),
        postgres_schema.migration_checksum(),
        postgres_schema.roles_checksum(),
    )
    if cache_key not in _READY:
        service = services.ensure_service()
        _READY[cache_key] = (service, services.ensure_template(service))
    service, template = _READY[cache_key]
    with services.lock(record_path.with_suffix(".lock")):
        record = _read(identifier)
        if name not in record["databases"]:
            with service.connect() as admin:
                existing = admin.execute(
                    "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
                ).fetchone()
                if existing:
                    raise RuntimeError("Unowned database collides with this test state")
                record["databases"][name] = {
                    "path": str(state),
                    "owner": os.environ.get(OWNER_ENV, "session"),
                }
                services.write_private(record_path, record)
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                        sql.Identifier(name), sql.Identifier(template)
                    )
                )
            with service.connect(name) as conn:
                postgres_schema.seed_environment(
                    conn, environment="dev", database_id=uuid.uuid4().hex
                )
        dsn = service.dsn(name, role="worker")
        write_settings(state, dsn, environment="dev", control_dsn=service.dsn(name, role="control"))
        return dsn


def provision_empty(path: Path) -> str:
    """Explicit restore target: a fresh owned database with no application schema."""
    identifier = os.environ.get(RUN_ENV)
    if identifier is None:
        raise RuntimeError("Empty test provisioning requires an explicit test run")
    from psycopg import sql

    from swingset.state.engine import write_settings

    state = path.resolve()
    if (state / "database.json").exists():
        raise ValueError("Restore target already has database settings")
    name = f"sst_{identifier}_{hashlib.sha256(str(state).encode()).hexdigest()[:20]}"
    record_path = _record_path(identifier)
    service = services.ensure_service()
    with services.lock(record_path.with_suffix(".lock")):
        record = _read(identifier)
        if name in record["databases"]:
            raise ValueError("Restore target is already owned by this test run")
        with service.connect() as admin:
            if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                raise RuntimeError("Unowned database collides with this restore target")
            record["databases"][name] = {
                "path": str(state),
                "owner": os.environ.get(OWNER_ENV, "session"),
            }
            services.write_private(record_path, record)
            admin.execute(
                sql.SQL(
                    "CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
                ).format(sql.Identifier(name))
            )
        write_settings(
            state,
            service.dsn(name, role="worker"),
            environment="dev",
            control_dsn=service.dsn(name, role="control"),
        )
    return service.dsn(name)


def retire_state(path: Path) -> None:
    """Retire one closed restore target before its owner reuses that files path."""
    identifier = os.environ.get(RUN_ENV)
    if identifier is None:
        raise RuntimeError("Test database retirement requires an explicit test run")
    from psycopg import sql

    state = path.resolve()
    name = f"sst_{identifier}_{hashlib.sha256(str(state).encode()).hexdigest()[:20]}"
    record_path = _record_path(identifier)
    service = services.ensure_service()
    with services.lock(record_path.with_suffix(".lock")):
        record = _read(identifier)
        owned = record["databases"].get(name)
        if owned is None:
            return
        if owned["path"] != str(state) or owned["owner"] != os.environ.get(OWNER_ENV, "session"):
            raise RuntimeError("Only this test owner may retire its restore target")
        marker = state / "database.json"
        if marker.is_file():
            settings = json.loads(marker.read_text())
            if settings.get("dsn") != service.dsn(name, role="worker"):
                raise RuntimeError("Restore target settings changed before retirement")
        with service.connect() as admin:
            # Omitting FORCE makes an accidentally live first restore fail.
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        if marker.is_file():
            marker.unlink()
        del record["databases"][name]
        services.write_private(record_path, record)


def _drop(identifier: str, *, owner: str | None, dead: bool = False) -> None:
    from psycopg import sql

    path = _record_path(identifier)
    with services.lock(path.with_suffix(".lock")):
        if not path.exists():
            return
        record = json.loads(path.read_text())
        if dead:
            if services.run_alive(record):
                raise RuntimeError("Refusing to remove a live test owner's databases")
        elif int(record["pid"]) != os.getpid():
            raise RuntimeError("Only a test owner may clean its databases")
        databases = {
            name: value
            for name, value in record["databases"].items()
            if owner is None or value["owner"] == owner
        }
        if databases:
            service = services.configured_ci_service()
            if service is None:
                service_record = json.loads((services.home() / "test-service.json").read_text())
                service = services.Service(
                    "swingset-test-postgres",
                    "test",
                    15432,
                    service_record["password"],
                    worker_password=service_record["worker_password"],
                    control_password=service_record["control_password"],
                )
            with service.connect() as admin:
                for name, value in databases.items():
                    if not name.startswith(f"sst_{identifier}_") or not re.fullmatch(
                        r"sst_[0-9a-f]{24}_[0-9a-f]{20}", name
                    ):
                        raise RuntimeError(
                            "Refusing database cleanup outside this run's exact ownership"
                        )
                    admin.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(name)
                        )
                    )
                    marker = Path(value["path"]) / "database.json"
                    if marker.is_file():
                        data = json.loads(marker.read_text())
                        if data.get("dsn") == service.dsn(name, role="worker"):
                            marker.unlink()
                    del record["databases"][name]
        if owner is None:
            path.unlink()
        else:
            services.write_private(path, record)


def finish_test(identifier: str, owner: str) -> None:
    _drop(identifier, owner=owner)


def finish_run(identifier: str) -> None:
    _drop(identifier, owner=None)


def clean_dead_runs(*, owner_group: int | None = None) -> int:
    """Remove exact leftover databases only after both owner and group exit."""
    count = 0
    for path in (services.home() / "test-runs").glob("*.json"):
        record = json.loads(path.read_text())
        if owner_group is not None and record["process_group"] != owner_group:
            continue
        if not services.run_alive(record):
            _drop(path.stem, owner=None, dead=True)
            count += 1
    return count
