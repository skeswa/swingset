"""One durable physical test service, with immutable ownership and clean catalog."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from pathlib import Path
from typing import Any

from . import services

NAME = "swingset-physical-postgres"
VOLUME = "swingset-physical-postgres"
PORT = 15434


def _path() -> Path:
    return services.home() / "physical-service.json"


def _write(value: dict[str, Any]) -> None:
    path = _path()
    services.write_private(path, value)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read() -> dict[str, Any]:
    path = _path()
    found = path.lstat()
    if (
        not stat.S_ISREG(found.st_mode)
        or found.st_uid != os.getuid()
        or found.st_mode & 0o077
        or found.st_size > 65536
    ):
        raise RuntimeError("Physical service ownership metadata is not private and regular")
    record = services._json(path)
    strings = (
        "instance",
        "container_id",
        "configuration",
        "system_identifier",
        "roles_signature",
        "volume_created",
        "password",
        "worker_password",
        "control_password",
    )
    if any(not isinstance(record.get(key), str) or not record[key] for key in strings):
        raise RuntimeError("Physical service connection or identity metadata is incomplete")
    if (
        record.get("format") != 1
        or not record.get("complete")
        or not re.fullmatch(r"[0-9a-f]{32}", record.get("instance") or "")
        or not re.fullmatch(r"[0-9a-f]{64}", record.get("container_id") or "")
        or record.get("configuration") != services._configuration("physical")
        or not isinstance(record.get("baseline"), list)
        or not record["baseline"]
        or not record.get("system_identifier")
        or not record.get("roles_signature")
        or not record.get("volume_created")
    ):
        raise RuntimeError(
            "Physical service metadata is incomplete or changed; inspect retained state"
        )
    return record


def _service(record: dict[str, Any]) -> services.Service:
    try:
        return services.Service(
            NAME,
            "physical",
            PORT,
            record["password"],
            worker_password=record["worker_password"],
            control_password=record["control_password"],
        )
    except KeyError:
        raise RuntimeError("Physical service connection metadata is incomplete") from None


def _labels(record: dict[str, Any]) -> dict[str, str]:
    return {
        services.LABEL: "physical",
        services.LABEL + ".instance": record["instance"],
        services.LABEL + ".configuration": record["configuration"],
    }


def _volume(record: dict[str, Any]) -> None:
    found = services._docker("volume", "inspect", VOLUME, check=False)
    if found.returncode:
        raise RuntimeError("Physical service's owned durable volume is missing")
    value = json.loads(found.stdout)[0]
    if (
        value.get("Name") != VOLUME
        or value.get("CreatedAt") != record.get("volume_created")
        or value.get("Labels") != _labels(record)
    ):
        raise RuntimeError("Physical service volume identity changed; refusing adoption")


def validate_container(value: dict[str, Any], record: dict[str, Any]) -> None:
    limits = value.get("HostConfig", {})
    ports = (limits.get("PortBindings", {}) or {}).get("5432/tcp")
    mounts = value.get("Mounts", [])
    if (
        value.get("Id") != record.get("container_id")
        or value.get("Name") != "/" + NAME
        or value.get("Config", {}).get("Image") != services.POSTGRES_IMAGE
        or value.get("Config", {}).get("Labels") != _labels(record)
        or limits.get("Memory") != 2 * 1024**3
        or limits.get("MemorySwap") != 2 * 1024**3
        or limits.get("NanoCpus") != 2_000_000_000
        or limits.get("Tmpfs")
        or limits.get("PidMode")
        or ports != [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}]
        or len(mounts) != 1
        or mounts[0].get("Type") != "volume"
        or mounts[0].get("Name") != VOLUME
        or mounts[0].get("Destination") != "/var/lib/postgresql/data"
        or mounts[0].get("RW") is not True
        or value.get("State", {}).get("Running")
        and (
            value.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp")
            != [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}]
        )
    ):
        raise RuntimeError("Physical service container identity or resource profile changed")


def validate_identity(service: services.Service, container: dict[str, Any]) -> None:
    record = _read()
    if service != _service(record):
        raise RuntimeError("Physical service connection differs from its ownership record")
    validate_container(container, record)
    if not container.get("State", {}).get("Running"):
        raise RuntimeError("Physical service's exact container is not running")


def inventory(connection: Any) -> list[list[Any]]:
    """Immutable database identities; transaction horizons/statistics are excluded."""
    return [
        list(row)
        for row in connection.execute(
            "SELECT oid::bigint,datname,pg_get_userbyid(datdba),pg_encoding_to_char(encoding),"
            "datcollate,datctype,datistemplate,datallowconn FROM pg_database ORDER BY datname"
        ).fetchall()
    ]


def verify_inventory(connection: Any, baseline: list[list[Any]]) -> None:
    if not baseline or inventory(connection) != baseline:
        raise RuntimeError(
            "Physical service retains databases outside its fresh baseline; review exact owners"
        )


def _system_identifier(connection: Any) -> str:
    row = connection.execute("SELECT system_identifier FROM pg_control_system()").fetchone()
    if row is None:
        raise RuntimeError("Physical PostgreSQL cluster identity is unavailable")
    return str(row[0])


def _assert_database(service: services.Service, record: dict[str, Any]) -> None:
    import psycopg

    try:
        with service.connect() as connection:
            if services._roles_signature(connection) != record["roles_signature"]:
                raise RuntimeError("Physical service roles changed; no automatic reset")
            if _system_identifier(connection) != record["system_identifier"]:
                raise RuntimeError("Physical PostgreSQL cluster identity changed")
            verify_inventory(connection, record["baseline"])
    except psycopg.Error:
        raise RuntimeError("Physical service inventory could not be verified") from None


def assert_clean(service: services.Service) -> None:
    import psycopg

    record = _read()
    if service != _service(record):
        raise RuntimeError("Physical service connection differs from its ownership record")
    inspected = services._docker("container", "inspect", record["container_id"], check=False)
    if inspected.returncode:
        raise RuntimeError("Physical service's exact container disappeared")
    validate_identity(service, json.loads(inspected.stdout)[0])
    _volume(record)
    try:
        services._validate_service(service)
    except psycopg.Error:
        raise RuntimeError("Physical service inventory could not be verified") from None
    _assert_database(service, record)


def _initialize(connection: Any, service: services.Service) -> None:
    from psycopg import sql

    from swingset.state import postgres_schema

    postgres_schema.install_roles(connection)
    for role, password in (
        ("worker", service.worker_password),
        ("control", service.control_password),
    ):
        login = f"swingset_test_{role}"
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(login), sql.Literal(password)
            )
        )
        connection.execute(
            sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(f"swingset_{role}"), sql.Identifier(login)
            )
        )
        for key, value in {**services.SETTINGS, "idle_in_transaction_session_timeout": "0"}.items():
            connection.execute(
                sql.SQL("ALTER ROLE {} SET {} = {}").format(
                    sql.Identifier(login), sql.Identifier(key), sql.Literal(value)
                )
            )


def ensure() -> services.Service:
    """Create once, then validate; drift and leftovers never authorize a reset."""
    with services.lock(services.home() / "services.lock"):
        path = _path()
        inspected = services._docker("container", "inspect", NAME, check=False)
        fresh = not path.exists() and not path.is_symlink()
        if fresh:
            volume = services._docker("volume", "inspect", VOLUME, check=False)
            if inspected.returncode == 0 or volume.returncode == 0:
                raise RuntimeError(
                    "Physical service objects exist without their private ownership record"
                )
            record: dict[str, Any] = {
                "format": 1,
                "complete": False,
                "instance": secrets.token_hex(16),
                "configuration": services._configuration("physical"),
                "password": secrets.token_hex(24),
                "worker_password": secrets.token_hex(24),
                "control_password": secrets.token_hex(24),
            }
            _write(record)  # Durable owner identity precedes either Docker resource.
            labels = [
                token
                for key, value in _labels(record).items()
                for token in ("--label", key + "=" + value)
            ]
            services._docker("volume", "create", *labels, VOLUME)
            record["volume_created"] = json.loads(
                services._docker("volume", "inspect", VOLUME).stdout
            )[0]["CreatedAt"]
            _write(record)
            arguments = [
                "run",
                "--detach",
                "--name",
                NAME,
                *labels,
                "--publish",
                f"127.0.0.1:{PORT}:5432",
                "--memory",
                "2g",
                "--memory-swap",
                "2g",
                "--cpus",
                "2",
                "--volume",
                VOLUME + ":/var/lib/postgresql/data",
                "--env",
                f"POSTGRES_USER={services.ROLE}",
                "--env",
                f"POSTGRES_PASSWORD={record['password']}",
                "--env",
                "POSTGRES_INITDB_ARGS=--locale=C --encoding=UTF8",
                services.POSTGRES_IMAGE,
                "postgres",
            ]
            for key, value in services.LIFECYCLE_SETTINGS.items():
                arguments.extend(["-c", f"{key}={value}"])
            services._docker(*arguments)
            inspected = services._docker("container", "inspect", NAME)
            record["container_id"] = json.loads(inspected.stdout)[0]["Id"]
            _write(record)
        else:
            record = _read()
            if inspected.returncode:
                raise RuntimeError(
                    "Physical service's recorded container is missing; no automatic replacement"
                )
        _volume(record)
        container = json.loads(inspected.stdout)[0]
        validate_container(container, record)
        if not container.get("State", {}).get("Running"):
            services._docker("start", record["container_id"])
            container = json.loads(
                services._docker("container", "inspect", record["container_id"]).stdout
            )[0]
            validate_container(container, record)
        service = _service(record)
        import psycopg

        deadline = time.monotonic() + 25
        while True:
            try:
                services._validate_service(service)
                with service.connect() as connection:
                    if fresh:
                        rows = inventory(connection)
                        if not rows or any(row[3:6] != ["UTF8", "C", "C"] for row in rows):
                            raise RuntimeError(
                                "Fresh physical databases require C collation and UTF8"
                            )
                        _initialize(connection, service)
                        record.update(
                            complete=True,
                            roles_signature=services._roles_signature(connection),
                            system_identifier=_system_identifier(connection),
                            baseline=inventory(connection),
                        )
                        _write(record)
                _assert_database(service, record)
                return service
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Physical test PostgreSQL did not become ready within 25 seconds"
                    ) from None
                time.sleep(0.1)
