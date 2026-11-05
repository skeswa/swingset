"""Postgres lifecycle, explicit schema checks, advisory locks and transactions."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

from swingset.clock import Clock
from swingset.model.ids import run_id as make_run_id

from . import engine
from .engine import Connection
from .engine.locks import acquire_writer
from .engine.settings import SETTINGS
from .rwlock import Held, LockBusy, exclusive, shared

SCHEMA_VERSION = 38
BUSY_TIMEOUT_SECONDS = 70.0
MIGRATION_PENDING = "migration pending"


class DatabaseLockedError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "in use") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class WriteCounters:
    writes: int = 0
    write_wait_seconds_total: float = 0.0
    write_wait_seconds_max: float = 0.0
    write_seconds_max: float = 0.0
    write_lock_timeouts: int = 0


_counters = WriteCounters()


def write_counters() -> dict[str, float | int]:
    return {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in asdict(_counters).items()
    }


def reset_write_counters() -> None:
    global _counters
    _counters = WriteCounters()


class Database:
    def __init__(
        self,
        state_dir: Path,
        connection: Connection,
        locks: Held | None,
        *,
        loop: str | None = None,
    ) -> None:
        self.state_dir, self.connection, self._locks, self.loop = state_dir, connection, locks, loop
        self.connection.state_dir = state_dir
        self.connection.restore_pending = (state_dir / "RESTORE_PENDING").is_file()
        self._savepoint = 0

    @property
    def lock_mode(self) -> str | None:
        return None if self._locks is None else self._locks.mode

    @property
    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM ops.schema_identity() WHERE key='schema_version'"
        ).fetchone()
        return int(row[0]) if row is not None else 0

    @property
    def environment(self) -> str:
        row = self.connection.execute(
            "SELECT environment FROM ops.environment_identity WHERE singleton=1"
        ).fetchone()
        if row is None or row[0] not in {"dev", "dryrun", "production"}:
            raise RuntimeError("database environment identity is missing or invalid")
        return str(row[0])

    @property
    def holds_writer_lock(self) -> bool:
        return self.lock_mode == "exclusive"

    @contextlib.contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[Connection]:
        conn = self.connection
        if conn.in_transaction:
            if immediate and conn.read_only_snapshot:
                raise RuntimeError("read snapshot cannot upgrade to a writer")
            self._savepoint += 1
            name = f"swingset_{self._savepoint}"
            conn.execute(f"SAVEPOINT {name}")
            try:
                yield conn
            except BaseException:
                if not conn.closed:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
                    conn.execute(f"RELEASE SAVEPOINT {name}")
                raise
            else:
                conn.execute(f"RELEASE SAVEPOINT {name}")
            return
        if not immediate:
            with conn.read_snapshot():
                yield conn
            return
        from .write_deadline import bounded_write

        waited_from = time.monotonic()
        conn.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        try:
            acquire_writer(conn)
        except BaseException as error:
            if isinstance(error, engine.LockTimeout):
                _counters.write_lock_timeouts += 1
            self._rollback()
            raise
        began = time.monotonic()
        waited = began - waited_from
        _counters.writes += 1
        _counters.write_wait_seconds_total += waited
        _counters.write_wait_seconds_max = max(_counters.write_wait_seconds_max, waited)
        try:
            with bounded_write(conn):
                yield conn
                deadline = conn._write_deadline
            # Validate the body before issuing COMMIT. A context exit failure
            # must still be able to roll back all output. COMMIT then inherits
            # the same absolute deadline, including its acknowledgement wait.
            assert deadline is not None
            from .write_deadline import WRITE_SECONDS

            remaining = min(WRITE_SECONDS, max(0.000001, deadline - time.monotonic()))
            with bounded_write(conn, seconds=remaining):
                conn._write_deadline = min(deadline, conn._write_deadline or deadline)
                self._commit()
        except BaseException:
            self._rollback()
            raise
        finally:
            _counters.write_seconds_max = max(_counters.write_seconds_max, time.monotonic() - began)

    @contextlib.contextmanager
    def reader(self) -> Iterator[Connection]:
        conn = engine.connect(
            self.connection.info.dsn,
            password=self.connection.info.password,
            application_name="swingset-reader",
            read_only=True,
        )
        conn.state_dir = self.state_dir
        conn.restore_pending = self.connection.restore_pending
        try:
            with conn.read_snapshot():
                yield conn
        finally:
            conn.close()

    def _rollback(self) -> None:
        if self.connection.closed:
            return
        try:
            self.connection.rollback()
        except engine.OperationalError as error:
            self.connection.close()
            raise engine.ConnectionLost(
                "database rollback could not be confirmed; operation stopped"
            ) from error

    def _commit(self) -> None:
        try:
            self.connection.commit()
        except engine.CommitUncertain:
            self.connection.close()
            raise
        except BaseException:
            self._rollback()
            raise

    def start_run(self, started_at: datetime, *, dry_run: bool = False) -> str:
        base = make_run_id(started_at)
        with self.transaction() as conn:
            identifier, suffix = base, 2
            while conn.execute(
                "SELECT 1 AS present FROM runs WHERE run_id=%s", (identifier,)
            ).fetchone():
                identifier, suffix = f"{base}-{suffix}", suffix + 1
            conn.execute(
                "INSERT INTO runs(run_id,started_at,dry_run) VALUES (%s,%s,%s)",
                (identifier, _iso(started_at), int(dry_run)),
            )
        return identifier

    def close(self) -> None:
        # A healthy idle session can acknowledge unlock before another session
        # opens the state. Socket close alone leaves a short backend-exit race.
        # An aborted or lost session must still close without cleanup SQL.
        try:
            if (
                self._locks is not None
                and not self.connection.closed
                and not self.connection.in_transaction
            ):
                self._locks.release()
        finally:
            self.connection.close()
            if self._locks is not None:
                self._locks.names.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_database(
    path: str | Path,
    *,
    dsn: str | None = None,
    lock: bool = True,
    mode: Literal["exclusive", "shared"] = "exclusive",
    loop: str | None = None,
    lock_timeout: float | None = 0,
    busy_timeout: float = BUSY_TIMEOUT_SECONDS,
    clock: Clock | None = None,
    read_only: bool = False,
    allow_restore_pending: bool = False,
) -> Database:
    if mode not in {"exclusive", "shared"}:
        raise ValueError(f"unknown lock mode: {mode}")
    if mode == "shared" and (loop is None or not lock or read_only):
        raise ValueError("a shared open is a loop's: it needs a loop name and the lock")
    if mode == "exclusive" and loop is not None:
        raise ValueError("only a shared open names a loop")
    directory = engine.state_directory(path)
    if not read_only:
        directory.mkdir(parents=True, exist_ok=True)
    if not allow_restore_pending and (directory / "RESTORE_PENDING").exists():
        raise RuntimeError(f"restore verification is pending: {directory}")
    conn = engine.connect(
        engine.resolve_dsn(directory, dsn),
        application_name=f"swingset-{loop or 'command'}",
        read_only=read_only,
        timeout=busy_timeout,
    )
    conn.state_dir = directory
    locks: Held | None = None
    try:
        if lock:
            try:
                locks = (
                    shared(directory, connection=conn, loop=loop)
                    if mode == "shared"
                    else exclusive(directory, connection=conn, timeout=lock_timeout, clock=clock)
                )
            except LockBusy as error:
                raise DatabaseLockedError(str(error), reason=error.reason) from error
        from .postgres_schema import validate_schema

        validate_schema(conn)
        database = Database(directory, conn, locks, loop=loop)
        # The marker is routing information, not authority. Always compare it
        # with the identity stored in the actual target database.
        if (directory / SETTINGS).exists():
            configured = engine.read_settings(directory)["environment"]
            if configured != database.environment:
                raise RuntimeError("local settings conflict with database environment identity")
        return database
    except BaseException:
        if locks is not None:
            locks.release()
        conn.close()
        raise


def open_for_loop(
    path: str | Path,
    loop: str,
    *,
    clock: Clock | None = None,
    busy_timeout: float = BUSY_TIMEOUT_SECONDS,
) -> Database:
    # Opening never migrates. A separate migration job owns that operation.
    return open_database(path, mode="shared", loop=loop, clock=clock, busy_timeout=busy_timeout)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
