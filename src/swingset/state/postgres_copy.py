"""Complete, private Postgres copies for differential verification.

The source stays open and read-only. Scratch administration uses a separate
credential; worker logins never receive database-creation rights.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from . import postgres_schema as schema
from .engine import Connection, resolve_control_dsn, resolve_dsn, write_settings


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


def _process_start(pid: int) -> str | None:
    try:
        # starttime follows state through itrealvalue in Linux procfs; splitting
        # after the final ')' permits spaces and parentheses in process names.
        values = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return values[19]
    except (OSError, IndexError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _incarnation(pid: int, group: int) -> dict[str, Any]:
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        namespace = os.readlink("/proc/self/ns/pid")
    except OSError:
        boot, namespace = None, None
    return {
        "boot": boot,
        "namespace": namespace,
        "pid_start": _process_start(pid),
        "group_start": _process_start(group),
    }


_DIRECTORY_MARKER = ".swingset-scratch-owner.json"
_STAGING_NAME = re.compile(r"\.install-[a-zA-Z0-9_-]{8,64}")


def _directory(path: Path, root: Path) -> tuple[Path, Path]:
    """Only a real, directly contained install job can transfer its ownership."""
    if root.is_symlink() or path.is_symlink() or ".." in path.parts:
        raise ValueError("Scratch directory ownership refuses symlinks and parent traversal")
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if (
        not resolved_root.is_dir()
        or resolved_root == Path(resolved_root.anchor)
        or not resolved.is_dir()
        or resolved.parent != resolved_root
        or path.parent.is_symlink()
        or not _STAGING_NAME.fullmatch(resolved.name)
    ):
        raise ValueError("Scratch directory must be a direct owned install job under its root")
    return resolved, resolved_root


def _marker(entry: dict[str, Any], owner: dict[str, Any]) -> dict[str, Any]:
    return {
        **entry,
        "format": "swingset-scratch-directory-v1",
        "token": owner["token"],
        "pid": owner["pid"],
        "process_group": owner["process_group"],
    }


def _read_marker(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        found = os.fstat(stream.fileno())
        if not stat.S_ISREG(found.st_mode) or found.st_uid != os.getuid() or found.st_mode & 0o077:
            raise RuntimeError("Scratch directory ownership marker is not private")
        body = stream.read(4097)
    if len(body) > 4096:
        raise RuntimeError("Scratch directory ownership marker is invalid")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("Scratch directory ownership marker is invalid")
    return value


def _publish_marker(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Publish without replacing another owner's marker, even if it appeared
        # between validation and registration. A killed writer leaves no partial marker.
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)
    _sync_directory(path.parent)


def _admin_dsn(worker: str) -> str:
    configured = os.environ.get("SWINGSET_SCRATCH_ADMIN_DSN")
    if configured is None and os.environ.get("SWINGSET_TEST_RUN"):
        from swingset.dev.services import ensure_service

        configured = ensure_service().dsn()
    if configured is None:
        raise RuntimeError("Graph verification requires explicit SWINGSET_SCRATCH_ADMIN_DSN")
    source, admin = conninfo_to_dict(worker), conninfo_to_dict(configured)
    if (source.get("host"), source.get("port", "5432")) != (
        admin.get("host"),
        admin.get("port", "5432"),
    ):
        raise ValueError("Scratch administrator must connect to the source Postgres server")
    return configured


class Copies:
    """One owner's full frozen copy and quiescent per-process template clones."""

    def __init__(self, state: Path) -> None:
        self.worker_dsn = resolve_dsn(state)
        self.control_dsn = resolve_control_dsn(state)
        self.admin_dsn = _admin_dsn(self.worker_dsn)
        self._initialize_owner()

    @classmethod
    def for_admin(
        cls,
        admin_dsn: str,
        *,
        registry_root: Path | None = None,
        checker_lock_fd: int | None = None,
    ) -> Copies:
        """An owner-only scratch job needs no worker or controller credential."""
        instance = cls.__new__(cls)
        instance.admin_dsn = admin_dsn
        instance._initialize_owner(registry_root=registry_root, checker_lock_fd=checker_lock_fd)
        return instance

    def _initialize_owner(
        self, *, registry_root: Path | None = None, checker_lock_fd: int | None = None
    ) -> None:
        endpoint = conninfo_to_dict(self.admin_dsn)
        self.endpoint = hashlib.sha256(
            json.dumps(
                {key: endpoint.get(key) for key in ("host", "port", "user")}, sort_keys=True
            ).encode()
        ).hexdigest()
        self.identifier = uuid.uuid4().hex[:24]
        if checker_lock_fd is not None and registry_root is None:
            raise ValueError("Checker ownership requires its durable registry root")
        configured_root = os.environ.get("SWINGSET_COPY_ROOT")
        if (
            registry_root is None
            and configured_root is not None
            and (not configured_root or not Path(configured_root).is_absolute())
        ):
            raise ValueError("Scratch owner registry must be an explicit absolute directory")
        if registry_root is not None:
            root = Path(registry_root)
        elif configured_root is not None:
            root = Path(configured_root)
        else:
            root = Path("/tmp") / f"swingset-pg-copies-{os.getuid()}"
        if root.is_symlink():
            raise ValueError("Scratch owner registry refuses symlinks")
        root.mkdir(mode=0o700, exist_ok=True)
        found = root.stat()
        if found.st_uid != os.getuid() or found.st_mode & 0o077:
            raise ValueError("Scratch owner registry must be private")
        self.path = root.resolve(strict=True) / (self.identifier + ".json")
        self._checker_lock_fd = checker_lock_fd
        self._process_identity = (
            _incarnation(os.getpid(), os.getpgrp())
            if registry_root is not None or configured_root is not None
            else None
        )
        if registry_root is not None or configured_root is not None:
            _sync_directory(root.parent)
        self.record: dict[str, Any] = {
            "pid": os.getpid(),
            "process_group": os.getpgrp(),
            "endpoint": self.endpoint,
            "token": self.identifier,
            "databases": [],
            "directories": [],
        }
        if self._process_identity is not None:
            self.record["incarnation"] = self._process_identity
        if checker_lock_fd is not None:
            self.record["checker_lock"] = self._checker_lock()

    def _checker_lock(self) -> dict[str, int] | None:
        if self._checker_lock_fd is None:
            return None
        found = os.fstat(self._checker_lock_fd)
        named = (self.path.parent / "checker.lock").stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(found.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or found.st_uid != os.getuid()
            or found.st_mode & 0o077
            or (found.st_dev, found.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise RuntimeError("Checker cleanup requires its exact private durable lock")
        # Reassert ownership on the caller's still-open description; never
        # release it here, because the server holds it for its whole lifetime.
        fcntl.flock(self._checker_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return {"device": found.st_dev, "inode": found.st_ino}

    def _owner_dead(self, record: dict[str, Any]) -> bool:
        if "process_group" not in record:
            return False  # Old records lacking the child-liveness fence stay untouched.
        pid, group = int(record["pid"]), int(record["process_group"])
        old = record.get("incarnation")
        current = self._process_identity
        if current is not None and not isinstance(old, dict):
            return False
        if current is None and old is not None:
            return False
        if isinstance(old, dict) and current is not None:
            same_namespace = (old.get("boot"), old.get("namespace")) == (
                current.get("boot"),
                current.get("namespace"),
            )
            if not same_namespace:
                # The checker executes as Linux namespace init. Its death kills
                # that namespace's children; a new namespace may reuse PID1.
                # The exact persistent singleton lock proves the old checker
                # no longer owns the service. Other namespaces fail closed.
                return bool(
                    pid == group == 1
                    and old.get("namespace")
                    and current.get("namespace")
                    and old.get("namespace") != current.get("namespace")
                    and old.get("boot")
                    and old.get("boot") == current.get("boot")
                    and record.get("checker_lock") is not None
                    and record["checker_lock"] == self._checker_lock()
                )
            pid_alive, group_alive = _alive(pid), _group_alive(group)
            if pid_alive and old.get("pid_start"):
                actual = _process_start(pid)
                if actual is not None and actual != old["pid_start"]:
                    pid_alive = False
            if group_alive and old.get("group_start"):
                actual = _process_start(group)
                if actual is not None and actual != old["group_start"]:
                    group_alive = False
            return not pid_alive and not group_alive
        return not _alive(pid) and not _group_alive(group)

    @contextmanager
    def _admin(self, database: str | None = None) -> Iterator[Any]:
        dsn = self.admin_dsn if database is None else make_conninfo(self.admin_dsn, dbname=database)
        try:
            with psycopg.connect(
                dsn,
                autocommit=True,
                options="-c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0",
            ) as conn:
                yield conn
        except psycopg.Error as error:
            raise RuntimeError(
                f"Scratch Postgres operation failed (SQLSTATE {error.sqlstate or 'unknown'})"
            ) from None

    def _drop(self, record: dict[str, Any]) -> None:
        with self._admin() as admin:
            for name in reversed(record["databases"]):
                if not re.fullmatch(r"sst_copy_[0-9a-f]{24}_[0-9]+", name):
                    raise ValueError("Scratch ownership record names an invalid database")
                found = admin.execute(
                    "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",
                    (name,),
                ).fetchone()
                if found is None:
                    continue
                if found[0] != "swingset-scratch:" + record["token"]:
                    raise RuntimeError("Scratch database ownership changed; refusing cleanup")
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))

    def own_directory(self, path: Path, *, root: Path) -> None:
        """Register a private install tree before the owner job assumes cleanup."""
        if not self.path.is_file():
            raise RuntimeError("Scratch directory ownership requires an active owner context")
        path, root = _directory(Path(path), Path(root))
        marker = path / _DIRECTORY_MARKER
        for owned in self.record["directories"]:
            if owned["path"] == str(path):
                if _read_marker(marker) != _marker(owned, self.record):
                    raise RuntimeError("Scratch directory ownership changed; refusing adoption")
                return
        if marker.exists() or marker.is_symlink():
            raise ValueError("Scratch directory already has an ownership marker")
        found = path.stat()
        entry = {
            "path": str(path),
            "root": str(root),
            "nonce": uuid.uuid4().hex,
            "device": found.st_dev,
            "inode": found.st_ino,
        }
        self.record["directories"].append(entry)
        _write(self.path, self.record)
        try:
            _publish_marker(marker, _marker(entry, self.record))
        except BaseException:
            self.record["directories"].remove(entry)
            _write(self.path, self.record)
            raise

    def _cleanup(self, record: dict[str, Any]) -> None:
        self._drop(record)
        for entry in reversed(record.get("directories", [])):
            original = Path(entry["path"])
            if not original.exists() and not original.is_symlink():
                continue
            path, root = _directory(original, Path(entry["root"]))
            found = path.stat()
            if (
                str(path) != entry["path"]
                or str(root) != entry["root"]
                or (found.st_dev, found.st_ino) != (entry["device"], entry["inode"])
                or _read_marker(path / _DIRECTORY_MARKER) != _marker(entry, record)
            ):
                raise RuntimeError("Scratch directory ownership changed; refusing cleanup")
            shutil.rmtree(path)
            _sync_directory(root)

    def __enter__(self) -> Copies:
        for path in self.path.parent.glob("*.json"):
            record = json.loads(path.read_text())
            if record.get("endpoint") == self.endpoint and self._owner_dead(record):
                self._cleanup(record)
                path.unlink()
                _sync_directory(path.parent)
        _write(self.path, self.record)
        return self

    def __exit__(self, *args: object) -> None:
        self._cleanup(self.record)
        self.path.unlink(missing_ok=True)
        _sync_directory(self.path.parent)

    def _create(self, *, template: str = "template0") -> str:
        name = f"sst_copy_{self.identifier}_{len(self.record['databases'])}"
        self.record["databases"].append(name)
        _write(self.path, self.record)
        with self._admin() as admin:
            admin.execute("SET statement_timeout='10s'")
            admin.execute(
                sql.SQL(
                    "CREATE DATABASE {} TEMPLATE {} ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
                ).format(sql.Identifier(name), sql.Identifier(template))
            )
            admin.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(name), sql.Literal("swingset-scratch:" + self.identifier)
                )
            )
        return name

    def _settings(self, state: Path, name: str) -> None:
        write_settings(
            state,
            make_conninfo(self.worker_dsn, dbname=name),
            environment="dev",
            control_dsn=make_conninfo(self.control_dsn, dbname=name),
        )

    @contextmanager
    def empty(self) -> Iterator[Any]:
        """Yield a fresh C/UTF8 administrator connection and remove it on exit."""
        name = self._create()
        try:
            with self._admin(name) as connection:
                yield connection
        finally:
            self._drop({**self.record, "databases": [name]})

    def freeze(self, source: Connection, state: Path) -> None:
        """Stream every physical source row from one pinned read-only snapshot."""
        name = self._create()
        phases = schema.migration_phases()
        with self._admin(name) as destination:
            with destination.transaction():
                destination.execute(phases["tables"], prepare=False)
                schema.assign_owners(destination)
            with source.read_snapshot():
                sequence_floors = {}
                sequence_names = source.execute(
                    "SELECT pg_get_serial_sequence(format('%%I.%%I',n.nspname,c.relname),a.attname) AS sequence FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid WHERE c.relkind='r' AND a.attnum>0 AND a.attidentity<>'' AND n.nspname=ANY(%s)",
                    (sorted(set(schema.TABLE_NAMESPACES.values())),),
                )
                for (sequence,) in sequence_names:
                    if sequence is not None:
                        last, called = source.execute(
                            sql.SQL("SELECT last_value,is_called FROM {}").format(sql.SQL(sequence))
                        ).fetchone()
                        sequence_floors[sequence] = int(last) if called else 0
                for table in schema.reference_manifest()["tables"]:
                    columns = (
                        *schema.semantic_columns(table),
                        *(("rowid",) if table in schema.ROWID_TABLES else ()),
                    )
                    projection = sql.SQL(",").join(map(sql.Identifier, columns))
                    relation = sql.Identifier(schema.namespace(table), table)
                    read = sql.SQL(
                        "COPY (SELECT {} FROM {}) TO STDOUT WITH (FORMAT binary)"
                    ).format(projection, relation)
                    write = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT binary)").format(
                        relation, projection
                    )
                    with (
                        psycopg.Cursor(source) as reader,
                        destination.cursor() as writer,
                        reader.copy(read) as output,
                        writer.copy(write) as target,
                    ):
                        for block in output:
                            target.write(block)
            with destination.transaction():
                destination.execute(phases["constraints"], prepare=False)
                destination.execute(phases["triggers"], prepare=False)
                if phases["forward"]:
                    destination.execute(phases["forward"], prepare=False)
                schema.seed_environment(destination, environment="dev")
                schema.stamp_schema(destination)
                schema.grant_roles(destination)
                sequences = destination.execute(
                    "SELECT n.nspname,c.relname,a.attname,pg_get_serial_sequence(format('%%I.%%I',n.nspname,c.relname),a.attname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid WHERE c.relkind='r' AND a.attnum>0 AND a.attidentity<>'' AND n.nspname=ANY(%s)",
                    (sorted(set(schema.TABLE_NAMESPACES.values())),),
                ).fetchall()
                for namespace, table, column, sequence in sequences:
                    maximum = destination.execute(
                        sql.SQL("SELECT max({}) FROM {}.{}").format(
                            sql.Identifier(column), sql.Identifier(namespace), sql.Identifier(table)
                        )
                    ).fetchone()[0]
                    floor = max(int(maximum or 0), sequence_floors.get(sequence, 0))
                    destination.execute(
                        "SELECT setval(%s,%s,%s)", (sequence, max(1, floor), floor > 0)
                    )
        self._settings(state, name)

    def clone(self, source: Path, state: Path) -> None:
        """Clone only this owner's quiescent frozen scratch database."""
        from .engine import read_settings

        name = str(conninfo_to_dict(read_settings(source)["dsn"])["dbname"])
        if name not in self.record["databases"]:
            raise ValueError("Scratch template is not owned by this verification")
        copied = self._create(template=name)
        with self._admin(copied) as conn:
            schema.seed_environment(conn, environment="dev")
        self._settings(state, copied)
