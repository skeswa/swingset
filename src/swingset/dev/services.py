"""Own the two local Postgres services; never operate on unrelated containers."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

POSTGRES_IMAGE = (
    "postgres:17.11@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675"
)
LABEL = "io.swingset.development"
SETTINGS = {
    "timezone": "UTC",
    "log_statement": "none",
    "log_min_error_statement": "panic",
    "log_parameter_max_length": "0",
    "log_parameter_max_length_on_error": "0",
}
TEST_SETTINGS = {**SETTINGS, "fsync": "off", "full_page_writes": "off", "synchronous_commit": "off"}
LIFECYCLE_SETTINGS = {
    **SETTINGS,
    "fsync": "on",
    "full_page_writes": "on",
    "synchronous_commit": "on",
}
ROLE = "swingset_test_admin"


def home() -> Path:
    return Path(os.environ.get("SWINGSET_DEV_HOME", "~/.local/share/swingset")).expanduser()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError("Invalid local development metadata")
    return value


def write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["docker", *arguments], text=True, capture_output=True, check=False, timeout=90
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Local Postgres needs a running Docker service") from exc
    if check and result.returncode:
        # Docker diagnostics may contain environment values or credentials.
        raise RuntimeError(f"Docker {' '.join(arguments[:2])} failed; inspect the local service")
    return result


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run_alive(record: dict[str, Any]) -> bool:
    return alive(int(record["pid"])) or _group_alive(int(record["process_group"]))


def active_runs() -> list[dict[str, Any]]:
    records = []
    for path in (home() / "test-runs").glob("*.json"):
        try:
            record = _json(path)
            if run_alive(record):
                records.append(record)
        except (OSError, ValueError, KeyError):
            # Malformed ownership is not permission to delete anything.
            raise RuntimeError(
                "Invalid test-run ownership metadata; inspect local development state"
            ) from None
    return records


@dataclass(frozen=True)
class Service:
    name: str
    kind: str
    port: int
    password: str = field(repr=False)
    image: str = POSTGRES_IMAGE
    host: str = "127.0.0.1"
    user: str = ROLE
    worker_password: str = field(default="", repr=False)
    control_password: str = field(default="", repr=False)

    def dsn(self, database: str = "postgres", *, role: str = "admin") -> str:
        from psycopg.conninfo import make_conninfo

        if role not in {"admin", "worker", "control"}:
            raise ValueError("unknown local database role")
        return make_conninfo(
            host=self.host,
            port=self.port,
            user=self.user if role == "admin" else f"swingset_test_{role}",
            password=self.password if role == "admin" else getattr(self, f"{role}_password"),
            dbname=database,
        )

    def connect(self, database: str = "postgres") -> psycopg.Connection[Any]:
        import psycopg

        return psycopg.connect(self.dsn(database), autocommit=True, connect_timeout=3)


def _roles_signature(conn: psycopg.Connection[Any]) -> str:
    rows = conn.execute(
        "SELECT r.rolname,r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,r.rolcanlogin,r.rolreplication,r.rolbypassrls,s.setconfig,r.rolpassword FROM pg_authid r LEFT JOIN pg_db_role_setting s ON s.setrole=r.oid AND s.setdatabase=0 WHERE r.rolname IN ('swingset_owner','swingset_worker','swingset_control','swingset_reader','swingset_test_worker','swingset_test_control') ORDER BY r.rolname"
    ).fetchall()
    memberships = conn.execute(
        "SELECT member.rolname,granted.rolname,m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member JOIN pg_roles granted ON granted.oid=m.roleid WHERE member.rolname IN ('swingset_test_worker','swingset_test_control') ORDER BY member.rolname,granted.rolname"
    ).fetchall()
    return hashlib.sha256(json.dumps([rows, memberships], sort_keys=True).encode()).hexdigest()


def _configuration(kind: str) -> str:
    from swingset.state import postgres_schema

    value = {
        "image": POSTGRES_IMAGE,
        "kind": kind,
        "service_protocol": 2,
        "settings": LIFECYCLE_SETTINGS
        if kind == "physical"
        else (TEST_SETTINGS if kind == "test" else SETTINGS),
        "role": ROLE,
        "roles": getattr(postgres_schema, "roles_checksum", lambda: "roles-v1")(),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _ci_prefix(kind: str) -> str:
    if kind not in {"test", "lifecycle", "physical"}:
        raise ValueError("Only disposable test services can supply CI credentials")
    return "SWINGSET_TEST" if kind == "test" else "SWINGSET_TEST_LIFECYCLE"


def ci_environment(service: Service) -> dict[str, str]:
    """Private host-to-test configuration; never print or retain its credentials."""
    prefix = _ci_prefix(service.kind)
    with service.connect() as conn:
        signature = _roles_signature(conn)
    return {
        "CI": "true",
        prefix + "_SERVICE_DSN": service.dsn(),
        prefix + "_SERVICE_IMAGE": service.image,
        prefix + "_ROLE_SIGNATURE": signature,
        prefix + "_WORKER_PASSWORD": service.worker_password,
        prefix + "_CONTROL_PASSWORD": service.control_password,
    }


def _configured_ci_service(kind: str) -> Service | None:
    prefix = _ci_prefix(kind)
    dsn = os.environ.get(prefix + "_SERVICE_DSN")
    if dsn is None:
        return None
    if (
        os.environ.get("CI") != "true"
        or os.environ.get(prefix + "_SERVICE_IMAGE") != POSTGRES_IMAGE
    ):
        raise RuntimeError("External test services require CI and the pinned image identity")
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(dsn)
    required = ("host", "port", "user", "password")
    if any(not info.get(key) for key in required) or any(
        not os.environ.get(prefix + suffix)
        for suffix in ("_ROLE_SIGNATURE", "_WORKER_PASSWORD", "_CONTROL_PASSWORD")
    ):
        raise RuntimeError("External test service connection is incomplete")
    return Service(
        f"ci-{kind}-postgres",
        kind,
        int(str(info["port"])),
        str(info["password"]),
        host=str(info["host"]),
        user=str(info["user"]),
        worker_password=os.environ[prefix + "_WORKER_PASSWORD"],
        control_password=os.environ[prefix + "_CONTROL_PASSWORD"],
    )


def configured_ci_service() -> Service | None:
    return _configured_ci_service("test")


def configured_ci_lifecycle_service() -> Service | None:
    return _configured_ci_service("lifecycle")


def _validate_service(service: Service) -> None:
    with service.connect() as conn:
        settings = {
            "test": TEST_SETTINGS,
            "lifecycle": LIFECYCLE_SETTINGS,
            "physical": LIFECYCLE_SETTINGS,
        }.get(service.kind, SETTINGS)
        for key, value in settings.items():
            found = conn.execute("SELECT current_setting(%s)", (key,)).fetchone()
            if found is None or str(found[0]).casefold() != value.casefold():
                raise RuntimeError(
                    f"Local Postgres setting {key} differs from the pinned configuration"
                )
        version = conn.execute("SHOW server_version_num").fetchone()
        if version is None or int(version[0]) != 170011:
            raise RuntimeError("Local Postgres does not match pinned version 17.11")
        external = service.name in {"ci-test-postgres", "ci-lifecycle-postgres"}
        expected = (
            os.environ.get(_ci_prefix(service.kind) + "_ROLE_SIGNATURE") if external else None
        )
        if external and (not expected or _roles_signature(conn) != expected):
            raise RuntimeError(
                "External test service role definitions differ from the pinned configuration"
            )


def _ensure_test_cpu_limit(container: dict[str, Any], configuration: str) -> None:
    """Reconcile only the known disposable container, preserving its data."""
    identifier = container.get("Id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise RuntimeError("Test CPU update requires its exact container identity")

    def validate(value: dict[str, Any]) -> None:
        config = value.get("Config", {})
        labels = config.get("Labels") or {}
        limits = value.get("HostConfig", {})
        if (
            value.get("Id") != identifier
            or value.get("Name") != "/swingset-test-postgres"
            or config.get("Image") != POSTGRES_IMAGE
            or labels.get(LABEL) != "test"
            or labels.get(LABEL + ".configuration") != configuration
            or limits.get("Memory") != 2 * 1024**3
            or limits.get("Tmpfs", {}).get("/var/lib/postgresql/data") != "rw,size=1536m"
            or limits.get("NanoCpus") not in (2_000_000_000, 4_000_000_000, 6_000_000_000)
        ):
            raise RuntimeError(
                "Test CPU update refuses unexpected container resources or ownership"
            )

    validate(container)
    if container["HostConfig"]["NanoCpus"] == 6_000_000_000:
        return
    current = json.loads(_docker("container", "inspect", identifier).stdout)[0]
    validate(current)
    if current["HostConfig"]["NanoCpus"] == 6_000_000_000:
        return
    _docker("update", "--cpus", "6", identifier)
    updated = json.loads(_docker("container", "inspect", identifier).stdout)[0]
    validate(updated)
    if updated["HostConfig"]["NanoCpus"] != 6_000_000_000 or any(
        updated["HostConfig"].get(key) != current["HostConfig"].get(key)
        for key in ("Memory", "MemorySwap", "Tmpfs", "Binds")
    ):
        raise RuntimeError("Test CPU update did not preserve the pinned container resources")


def ensure_service(kind: str = "test") -> Service:
    """Validate or start only our labelled service; persistent dev data survives.

    Incompatible disposable test services may be replaced only while no run
    owner is alive. Persistent services require an explicit migration instead.
    """
    if kind == "physical":
        from .physical import ensure

        return ensure()
    if kind == "test" and (external := configured_ci_service()) is not None:
        _validate_service(external)
        return external
    if kind not in {"test", "dev"}:
        raise ValueError("service kind must be test or dev")
    name = f"swingset-{kind}-postgres"
    root = home()
    path = root / f"{kind}-service.json"
    port = 15432 if kind == "test" else 15433
    with lock(root / "services.lock"):
        configuration = _configuration(kind)
        record = _json(path) if path.exists() else {"password": secrets.token_hex(24)}
        record.setdefault("worker_password", secrets.token_hex(24))
        record.setdefault("control_password", secrets.token_hex(24))
        service = Service(
            name,
            kind,
            port,
            record["password"],
            worker_password=record["worker_password"],
            control_password=record["control_password"],
        )
        write_private(path, {**record, "configuration": configuration})
        inspected = _docker("container", "inspect", name, check=False)
        if inspected.returncode == 0:
            container = json.loads(inspected.stdout)[0]
            labels = container["Config"].get("Labels") or {}
            if labels.get(LABEL) != kind:
                raise RuntimeError(f"Container {name} is not owned by Swingset")
            valid = (
                labels.get(LABEL + ".configuration") == configuration
                and container["Config"]["Image"] == POSTGRES_IMAGE
            )
            if not valid:
                if kind != "test" or active_runs():
                    raise RuntimeError(
                        "Local Postgres configuration changed while data or live test owners remain"
                    )
                _docker("rm", "--force", name)
                inspected = subprocess.CompletedProcess([], 1, "", "")
            elif not container["State"]["Running"]:
                _docker("start", name)
        created = bool(inspected.returncode)
        if created:
            write_private(path, {**record, "configuration": configuration})
            settings = TEST_SETTINGS if kind == "test" else SETTINGS
            arguments = [
                "run",
                "--detach",
                "--name",
                name,
                "--label",
                f"{LABEL}={kind}",
                "--label",
                f"{LABEL}.configuration={configuration}",
                "--publish",
                f"127.0.0.1:{port}:5432",
                "--memory",
                "2g" if kind == "test" else "4g",
                "--cpus",
                "6" if kind == "test" else "2",
                "--env",
                f"POSTGRES_USER={ROLE}",
                "--env",
                f"POSTGRES_PASSWORD={service.password}",
                "--env",
                "POSTGRES_INITDB_ARGS=--locale=C --encoding=UTF8",
            ]
            if kind == "test":
                arguments.extend(["--tmpfs", "/var/lib/postgresql/data:rw,size=1536m"])
            else:
                arguments.extend(["--volume", "swingset-dev-postgres:/var/lib/postgresql/data"])
            arguments.extend([POSTGRES_IMAGE, "postgres"])
            for key, value in settings.items():
                arguments.extend(["-c", f"{key}={value}"])
            _docker(*arguments)
        import psycopg

        deadline = time.monotonic() + 25
        while True:
            try:
                with service.connect() as conn:
                    for key, value in (TEST_SETTINGS if kind == "test" else SETTINGS).items():
                        found = conn.execute("SELECT current_setting(%s)", (key,)).fetchone()
                        if found is None or str(found[0]).casefold() != value.casefold():
                            raise RuntimeError(
                                f"Local Postgres setting {key} differs from the pinned configuration"
                            )
                    version = conn.execute("SHOW server_version_num").fetchone()
                    if version is None or int(version[0]) != 170011:
                        raise RuntimeError("Local Postgres does not match pinned version 17.11")
                    from psycopg import sql

                    from swingset.state import postgres_schema

                    if created:
                        postgres_schema.install_roles(conn)
                        for role, password in (
                            ("worker", service.worker_password),
                            ("control", service.control_password),
                        ):
                            login = f"swingset_test_{role}"
                            conn.execute(
                                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                                    sql.Identifier(login), sql.Literal(password)
                                )
                            )
                            conn.execute(
                                sql.SQL("GRANT {} TO {}").format(
                                    sql.Identifier(f"swingset_{role}"), sql.Identifier(login)
                                )
                            )
                            for key, value in {
                                **SETTINGS,
                                "idle_in_transaction_session_timeout": "0",
                            }.items():
                                conn.execute(
                                    sql.SQL("ALTER ROLE {} SET {} = {}").format(
                                        sql.Identifier(login),
                                        sql.Identifier(key),
                                        sql.Literal(value),
                                    )
                                )
                        record["roles_signature"] = _roles_signature(conn)
                        write_private(path, {**record, "configuration": configuration})
                    elif record.get("roles_signature") != _roles_signature(conn):
                        raise RuntimeError(
                            "Local Postgres role definitions changed; stop run owners before resetting the test service"
                        )
                if kind == "test" and not created:
                    _ensure_test_cpu_limit(container, configuration)
                return service
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Local Postgres did not become ready within 25 seconds"
                    ) from None
                time.sleep(0.1)


def template_name() -> str:
    from swingset.state import postgres_schema

    return (
        "sst_template_"
        + hashlib.sha256(
            (
                POSTGRES_IMAGE + postgres_schema.migration_checksum() + _configuration("test")
            ).encode()
        ).hexdigest()[:20]
    )


def ensure_template(service: Service) -> str:
    from psycopg import sql

    from swingset.state import postgres_schema

    name = template_name()
    with lock(home() / "templates.lock"), service.connect() as admin:
        row = admin.execute(
            "SELECT datistemplate FROM pg_database WHERE datname=%s", (name,)
        ).fetchone()
        if row is not None:
            if not row[0]:
                raise RuntimeError("Existing Postgres test template is incomplete")
            return name
        admin.execute(
            sql.SQL(
                "CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
            ).format(sql.Identifier(name))
        )
        try:
            with service.connect(name) as conn:
                postgres_schema.migrate(conn)
                postgres_schema.seed_environment(conn, environment="dev")
            admin.execute(
                sql.SQL("ALTER DATABASE {} WITH IS_TEMPLATE true ALLOW_CONNECTIONS false").format(
                    sql.Identifier(name)
                )
            )
        except BaseException:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            raise
    return name


def _wait_for_lifecycle_lease(lease: psycopg.Connection[Any]) -> None:
    deadline = time.monotonic() + 600
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for the physical test service lease")
        row = lease.execute("SELECT pg_try_advisory_lock(1397969989, 1818846821)").fetchone()
        if row is not None and row[0]:
            if time.monotonic() >= deadline:
                lease.execute("SELECT pg_advisory_unlock(1397969989, 1818846821)")
                raise TimeoutError("Timed out waiting for the physical test service lease")
            return
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


@contextmanager
def lifecycle_service() -> Iterator[Service]:
    """A separate durable throwaway cluster for server lifecycle fault tests.

    Its anonymous volume survives a container restart and is removed with its
    owning container. The warm test and development containers are untouched.
    """
    if (external := configured_ci_lifecycle_service()) is not None:
        _validate_service(external)
        # No transaction remains open while the case runs: the lease must not
        # itself pin xmin during a physical-space assertion. Session ownership
        # also serializes future physical cases sharing this dedicated cluster.
        with external.connect() as lease:
            _wait_for_lifecycle_lease(lease)
            try:
                yield external
            finally:
                lease.execute("SELECT pg_advisory_unlock(1397969989, 1818846821)")
        return
    name = "swingset-fault-postgres-" + secrets.token_hex(12)
    password, worker, control = (secrets.token_hex(24) for _ in range(3))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    arguments = [
        "run",
        "--detach",
        "--name",
        name,
        "--label",
        f"{LABEL}=lifecycle",
        "--label",
        f"{LABEL}.pid={os.getpid()}",
        "--label",
        f"{LABEL}.process-group={os.getpgrp()}",
        "--publish",
        f"127.0.0.1:{port}:5432",
        "--memory",
        "2g",
        "--cpus",
        "2",
        "--volume",
        "/var/lib/postgresql/data",
        "--env",
        f"POSTGRES_USER={ROLE}",
        "--env",
        f"POSTGRES_PASSWORD={password}",
        "--env",
        "POSTGRES_INITDB_ARGS=--locale=C --encoding=UTF8",
        POSTGRES_IMAGE,
        "postgres",
    ]
    for key, value in LIFECYCLE_SETTINGS.items():
        arguments.extend(["-c", f"{key}={value}"])
    ownership = home() / "fault-runs" / f"{name}.json"
    write_private(ownership, {"pid": os.getpid(), "process_group": os.getpgrp(), "name": name})
    try:
        _docker(*arguments)
        inspected = json.loads(_docker("container", "inspect", name).stdout)[0]
        published = inspected["NetworkSettings"]["Ports"]["5432/tcp"][0]
        if published["HostIp"] != "127.0.0.1":
            raise RuntimeError("Fault test Postgres did not bind to localhost")
        service = Service(
            name,
            "lifecycle",
            int(published["HostPort"]),
            password,
            worker_password=worker,
            control_password=control,
        )
        import psycopg
        from psycopg import sql

        from swingset.state import postgres_schema

        deadline = time.monotonic() + 25
        while True:
            try:
                with service.connect() as conn:
                    postgres_schema.install_roles(conn)
                    for role, secret in (("worker", worker), ("control", control)):
                        login = f"swingset_test_{role}"
                        conn.execute(
                            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                                sql.Identifier(login), sql.Literal(secret)
                            )
                        )
                        conn.execute(
                            sql.SQL("GRANT {} TO {}").format(
                                sql.Identifier(f"swingset_{role}"), sql.Identifier(login)
                            )
                        )
                        for key, value in {
                            **SETTINGS,
                            "idle_in_transaction_session_timeout": "0",
                        }.items():
                            conn.execute(
                                sql.SQL("ALTER ROLE {} SET {} = {}").format(
                                    sql.Identifier(login), sql.Identifier(key), sql.Literal(value)
                                )
                            )
                _validate_service(service)
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Fault test Postgres did not become ready") from None
                time.sleep(0.1)
        yield service
    finally:
        inspected = _docker("container", "inspect", name, check=False)
        if inspected.returncode == 0:
            labels = json.loads(inspected.stdout)[0]["Config"].get("Labels") or {}
            if labels.get(LABEL) != "lifecycle" or labels.get(LABEL + ".pid") != str(os.getpid()):
                raise RuntimeError("Refusing to remove a fault container owned by another process")
            _docker("rm", "--force", "--volumes", name)
        ownership.unlink(missing_ok=True)


def clean_dead_lifecycle(*, owner_group: int | None = None) -> int:
    cleaned = 0
    for path in (home() / "fault-runs").glob("*.json"):
        record = _json(path)
        if owner_group is not None and record["process_group"] != owner_group:
            continue
        if run_alive(record):
            continue
        name = record["name"]
        if (
            not isinstance(name, str)
            or not name.startswith("swingset-fault-postgres-")
            or path.name != name + ".json"
        ):
            raise RuntimeError("Invalid lifecycle test ownership metadata")
        inspected = _docker("container", "inspect", name, check=False)
        if inspected.returncode == 0:
            labels = json.loads(inspected.stdout)[0]["Config"].get("Labels") or {}
            if labels.get(LABEL) != "lifecycle" or labels.get(LABEL + ".pid") != str(record["pid"]):
                raise RuntimeError("Lifecycle container identity differs from its ownership record")
            _docker("rm", "--force", "--volumes", name)
        path.unlink()
        cleaned += 1
    return cleaned


def restart_lifecycle(service: Service) -> None:
    """Fault injection can restart only the caller's own throwaway cluster."""
    if service.kind != "lifecycle" or not service.name.startswith("swingset-fault-postgres-"):
        raise RuntimeError("Lifecycle tests cannot restart warm services")
    inspected = json.loads(_docker("container", "inspect", service.name).stdout)[0]
    labels = inspected["Config"].get("Labels") or {}
    if labels.get(LABEL) != "lifecycle" or labels.get(LABEL + ".pid") != str(os.getpid()):
        raise RuntimeError("Lifecycle test container belongs to another process")
    _docker("restart", service.name)
    import psycopg

    deadline = time.monotonic() + 25
    while True:
        try:
            _validate_service(service)
            return
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Fault test Postgres did not recover after restart") from None
            time.sleep(0.1)
