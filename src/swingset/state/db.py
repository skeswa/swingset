"""SQLite lifecycle, migrations, process lock, and transactions."""

from __future__ import annotations

import contextlib
import fcntl
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Self

from swingset.clock import Clock, SystemClock
from swingset.model.ids import run_id as make_run_id

SCHEMA_VERSION = 29


class DatabaseLockedError(RuntimeError):
    pass


class Database:
    """An exclusively-owned state database.

    The lock covers the object's lifetime. SQLite still provides transactional
    safety; the lock prevents two pipeline commands from competing semantically.
    """

    def __init__(
        self, state_dir: Path, connection: sqlite3.Connection, lock_file: IO[bytes] | None
    ) -> None:
        self.state_dir = state_dir
        self.connection = connection
        self._lock_file = lock_file
        self._savepoint = 0

    @property
    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row[0]) if row is not None else 0

    @contextlib.contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Commit all writes or roll them all back; nested calls use savepoints."""
        if self.connection.in_transaction:
            self._savepoint += 1
            name = f"swingset_{self._savepoint}"
            self.connection.execute(f"SAVEPOINT {name}")
            try:
                yield self.connection
            except BaseException:
                self.connection.execute(f"ROLLBACK TO {name}")
                self.connection.execute(f"RELEASE {name}")
                raise
            else:
                self.connection.execute(f"RELEASE {name}")
            return
        try:
            from .write_deadline import bounded_write

            boundary = bounded_write(self.connection) if immediate else self._read_snapshot()
            with boundary:
                self.connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield self.connection
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    @contextlib.contextmanager
    def _read_snapshot(self) -> Iterator[None]:
        """An unbounded read may never upgrade to an unbounded SQLite writer."""
        previous = int(self.connection.execute("PRAGMA query_only").fetchone()[0])
        self.connection.execute("PRAGMA query_only=ON")
        try:
            yield
        finally:
            self.connection.execute("PRAGMA query_only=ON" if previous else "PRAGMA query_only=OFF")

    def start_run(self, started_at: datetime, *, dry_run: bool = False) -> str:
        base = make_run_id(started_at)
        with self.transaction() as conn:
            identifier = base
            suffix = 2
            while conn.execute("SELECT 1 FROM runs WHERE run_id=?", (identifier,)).fetchone():
                identifier = f"{base}-{suffix}"
                suffix += 1
            conn.execute(
                "INSERT INTO runs(run_id, started_at, dry_run) VALUES (?,?,?)",
                (identifier, _iso(started_at), int(dry_run)),
            )
        return identifier

    def close(self) -> None:
        self.connection.close()
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()

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
    lock: bool = True,
    lock_timeout: float | None = 0,
    clock: Clock | None = None,
    read_only: bool = False,
    allow_restore_pending: bool = False,
) -> Database:
    """Open and migrate a state directory or explicit ``.sqlite`` path."""
    requested = Path(path)
    db_path = requested if requested.suffix in {".sqlite", ".db"} else requested / "state.sqlite"
    state_dir = db_path.parent
    if not read_only:
        state_dir.mkdir(parents=True, exist_ok=True)
    if not read_only and not allow_restore_pending and (state_dir / "RESTORE_PENDING").exists():
        raise RuntimeError(f"restore verification is pending: {state_dir}")
    lock_path = state_dir / "state.lock"
    lock_file: IO[bytes] | None = None
    if lock:
        lock_file = lock_path.open("a+b")
        active_clock = clock or SystemClock()
        started_at = active_clock.now()
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if (
                    lock_timeout is not None
                    and (active_clock.now() - started_at).total_seconds() >= lock_timeout
                ):
                    lock_file.close()
                    raise DatabaseLockedError(f"state is already in use: {state_dir}") from error
                active_clock.sleep(0.1)
    if read_only:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None)
    else:
        conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
    database = Database(state_dir, conn, lock_file)
    try:
        if not read_only:
            from .control_lock import control_lock

            with control_lock(state_dir, timeout=60 if lock_timeout is None else lock_timeout):
                if not allow_restore_pending and (state_dir / "RESTORE_PENDING").exists():
                    raise RuntimeError(f"restore verification is pending: {state_dir}")
                _migrate(database)
    except BaseException:
        database.close()
        raise
    return database


def _migrate(database: Database) -> None:
    current = int(database.connection.execute("PRAGMA user_version").fetchone()[0])
    migrations = Path(__file__).with_name("migrations")
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"database schema {current} is newer than supported {SCHEMA_VERSION}")
    if 12 <= current < SCHEMA_VERSION:
        from .controls import recover_admissions

        # The writer and lifecycle locks exclude surviving workers. A process
        # may have died with a remote publication in flight; retain uncertainty
        # while releasing its stale semantic fence before schema writes.
        with database.transaction() as conn:
            recover_admissions(conn, now=datetime.now(UTC))
    for version in range(current + 1, SCHEMA_VERSION + 1):
        matches = sorted(migrations.glob(f"{version:04d}_*.sql"))
        if len(matches) != 1:
            raise RuntimeError(f"expected one migration for schema {version}, found {len(matches)}")
        script = matches[0].read_text()
        # executescript commits implicitly, so migration files carry all of their
        # changes as a single explicit transaction. Disable FK actions while
        # rebuilding parent tables, then validate before committing the schema.
        conn = database.connection
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{script}")
            if version == 2:
                from swingset.state.verification import recover_registry_verifications

                recover_registry_verifications(database)
            if version >= 12:
                from .publication_fence import install_publication_fences

                install_publication_fences(conn)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"migration {version} violates foreign keys: {violations[:5]}")
            conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
            conn.execute(f"PRAGMA user_version={version}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
