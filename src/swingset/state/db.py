"""SQLite lifecycle, migrations, process locks, and transactions."""

from __future__ import annotations

import contextlib
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

from swingset.clock import Clock, SystemClock
from swingset.model.ids import run_id as make_run_id

from .rwlock import Held, LockBusy, exclusive, shared

SCHEMA_VERSION = 36
#: Seconds SQLite waits for another process's write before `database is locked`.
#: Writes elsewhere may take up to the write bound, so this is longer (D-0215).
BUSY_TIMEOUT_SECONDS = 70.0
MIGRATION_PENDING = "migration pending"


class DatabaseLockedError(RuntimeError):
    """The state's locks are held elsewhere; `reason` says why."""

    def __init__(self, message: str, *, reason: str = "in use") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class WriteCounters:
    """This process's write transactions: lock waits apart from the bounded body."""

    writes: int = 0
    write_wait_seconds_total: float = 0.0
    write_wait_seconds_max: float = 0.0
    write_seconds_max: float = 0.0
    write_lock_timeouts: int = 0


_counters = WriteCounters()


def write_counters() -> dict[str, float | int]:
    """A copy of the process counters, rounded for a run record."""
    values = asdict(_counters)
    return {
        key: round(value, 3) if isinstance(value, float) else value for key, value in values.items()
    }


def reset_write_counters() -> None:
    global _counters
    _counters = WriteCounters()


class Database:
    """A state database and the locks its handle holds.

    The locks cover the object's lifetime. `exclusive` is the one data writer
    (the cycle and every CLI mutation); `shared` is a loop, one of several.
    SQLite still provides transactional safety; the locks keep commands from
    competing semantically.
    """

    def __init__(
        self,
        state_dir: Path,
        connection: sqlite3.Connection,
        locks: Held | None,
        *,
        loop: str | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.connection = connection
        self._locks = locks
        self.loop = loop
        self._savepoint = 0

    @property
    def lock_mode(self) -> str | None:
        """`exclusive`, `shared`, or None for an unlocked handle."""
        return None if self._locks is None else self._locks.mode

    @property
    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row[0]) if row is not None else 0

    @property
    def holds_writer_lock(self) -> bool:
        """Whether this handle took `state.lock`, not just opened the file.

        Retention removal asks, because a removal that is not the one data
        writer could race a cycle that is still writing what it is removing.
        A loop's shared lock is not the writer lock.
        """
        return self.lock_mode == "exclusive"

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
        if not immediate:
            try:
                with self._read_snapshot():
                    self.connection.execute("BEGIN")
                    yield self.connection
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self._commit()
            return
        from .write_deadline import bounded_write

        # Waiting for another process's write is not this write's time: take
        # SQLite's writer lock first, under the busy timeout, and bound only
        # the body (D-0215).
        waited_from = time.monotonic()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if "locked" in str(error) or "busy" in str(error):
                _counters.write_lock_timeouts += 1
            raise
        began = time.monotonic()
        waited = began - waited_from
        _counters.writes += 1
        _counters.write_wait_seconds_total += waited
        _counters.write_wait_seconds_max = max(_counters.write_wait_seconds_max, waited)
        try:
            try:
                with bounded_write(self.connection):
                    yield self.connection
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self._commit()
        finally:
            _counters.write_seconds_max = max(_counters.write_seconds_max, time.monotonic() - began)

    @contextlib.contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        """A second, read-only connection pinned to one committed snapshot.

        It sees what was committed when it opened and nothing written after,
        by this handle or any other. It takes no write lock, so this handle
        can write in short transactions while it is open.
        """
        row = self.connection.execute("PRAGMA database_list").fetchone()
        location = str(row[2]) if row is not None else ""
        if not location:
            raise ValueError("a reader needs a database file")
        conn = sqlite3.connect(
            Path(location).as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=BUSY_TIMEOUT_SECONDS,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN")
            # The first read pins the snapshot.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            yield conn
        finally:
            conn.close()

    def _commit(self) -> None:
        try:
            self.connection.commit()
        except BaseException:
            # A deferred constraint fails at COMMIT, and SQLite leaves the
            # transaction open when it does. Roll it back so the failure
            # cannot be inherited by the next caller as a savepoint.
            self.connection.rollback()
            raise

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
        if self._locks is not None:
            self._locks.release()

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
    mode: Literal["exclusive", "shared"] = "exclusive",
    loop: str | None = None,
    lock_timeout: float | None = 0,
    busy_timeout: float = BUSY_TIMEOUT_SECONDS,
    clock: Clock | None = None,
    read_only: bool = False,
    allow_restore_pending: bool = False,
) -> Database:
    """Open and migrate a state directory or explicit ``.sqlite`` path.

    `exclusive` (the default) waits up to `lock_timeout` for the state locks
    and migrates. `shared` is for one named loop: it never waits and never
    migrates; an older schema raises `DatabaseLockedError` with reason
    "migration pending" (see `open_for_loop`).
    """
    if mode not in {"exclusive", "shared"}:
        raise ValueError(f"unknown lock mode: {mode}")
    if mode == "shared" and (loop is None or not lock or read_only):
        raise ValueError("a shared open is a loop's: it needs a loop name and the lock")
    if mode == "exclusive" and loop is not None:
        raise ValueError("only a shared open names a loop")
    requested = Path(path)
    db_path = requested if requested.suffix in {".sqlite", ".db"} else requested / "state.sqlite"
    state_dir = db_path.parent
    if not read_only:
        state_dir.mkdir(parents=True, exist_ok=True)
    if not read_only and not allow_restore_pending and (state_dir / "RESTORE_PENDING").exists():
        raise RuntimeError(f"restore verification is pending: {state_dir}")
    locks: Held | None = None
    if lock:
        try:
            locks = (
                shared(state_dir, loop=loop)
                if mode == "shared"
                else exclusive(state_dir, timeout=lock_timeout, clock=clock)
            )
        except LockBusy as error:
            raise DatabaseLockedError(str(error), reason=error.reason) from error
    try:
        if read_only:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None)
        else:
            conn = sqlite3.connect(db_path, isolation_level=None, timeout=busy_timeout)
    except BaseException:
        if locks is not None:
            locks.release()
        raise
    database = Database(state_dir, conn, locks, loop=loop)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
        if mode == "shared":
            # Holding `state.lock` shared means no restore is running now.
            if not allow_restore_pending and (state_dir / "RESTORE_PENDING").exists():
                raise RuntimeError(f"restore verification is pending: {state_dir}")
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            if current < SCHEMA_VERSION:
                # Migrations need the one writer; the caller hands off.
                raise DatabaseLockedError(
                    f"{MIGRATION_PENDING}: schema {current} < {SCHEMA_VERSION}: {state_dir}",
                    reason=MIGRATION_PENDING,
                )
            _revoke_stale_payload_removal(database)
        elif not read_only:
            from .control_lock import control_lock

            with control_lock(state_dir, timeout=60 if lock_timeout is None else lock_timeout):
                if not allow_restore_pending and (state_dir / "RESTORE_PENDING").exists():
                    raise RuntimeError(f"restore verification is pending: {state_dir}")
                _migrate(database, clock=clock)
                _revoke_stale_payload_removal(database)
    except BaseException:
        database.close()
        raise
    return database


def open_for_loop(
    path: str | Path,
    loop: str,
    *,
    clock: Clock | None = None,
    busy_timeout: float = BUSY_TIMEOUT_SECONDS,
) -> Database:
    """Open shared for a loop, migrating first through one exclusive try.

    A loop that loses the race to migrate, or finds a writer, raises
    `DatabaseLockedError` and skips its tick.
    """
    try:
        return open_database(path, mode="shared", loop=loop, clock=clock, busy_timeout=busy_timeout)
    except DatabaseLockedError as error:
        if error.reason != MIGRATION_PENDING:
            raise
    open_database(path, lock_timeout=0, clock=clock, busy_timeout=busy_timeout).close()
    return open_database(path, mode="shared", loop=loop, clock=clock, busy_timeout=busy_timeout)


def _migrate(database: Database, *, clock: Clock | None = None) -> None:
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
            if version == 30:
                from .finding_reference_migration import backfill_finding_references

                # Which digests a finding relies on was decided by looking at
                # the files on disk, so the backfill needs the state directory.
                backfill_finding_references(conn, database.state_dir)
            if version == 32:
                from .derivation_payload_migration import intern_derivation_payloads

                # SQLite cannot hash, so the fill, the comparison of every
                # generation label, and the drop of the old table happen here,
                # inside the migration transaction. Interning was the last
                # migration, so that schemas 30 and 31 deploy without it, until
                # 33 (journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md,
                # journal/decisions/0206-add-migration-33-after-interning.md).
                intern_derivation_payloads(conn)
            if version == 34:
                from .queue import seed

                # Each pending unit whose last attempt failed gets its queue row,
                # so a blocked parse waits on a clock from the first loop tick and
                # a failing unit keeps its count, short of quarantine.
                seed(conn, (clock or SystemClock()).now())
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


#: Delete gates whose permission row must never outlive its transaction
#: (D-0136), with the log event a stale row writes when it is cleared.
_GATES = (
    ("derivation_payload_removal_authority", "derivation-payload-removal-grant-revoked"),
    ("storage_eviction_authority", "storage-eviction-grant-revoked"),
)


def _revoke_stale_payload_removal(database: Database) -> None:
    """Close a delete gate if a grant outlived its writer.

    The grant row cannot commit while foreign keys are on, so a row here means
    some writer committed it with them off. Each gate is the last guard on what
    it protects (derivation payloads; row references, dependency sets, eviction
    markers and packed bodies from schema 36), so it is closed here rather than
    left open for whichever process opens the database next.
    """
    conn = database.connection
    for table, event in _GATES:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is None:
            continue
        stale = conn.execute(f"SELECT reason,granted_at FROM {table}").fetchall()
        if not stale:
            continue
        with database.transaction() as txn:
            txn.execute(f"DELETE FROM {table}")
        from swingset.log import log

        for reason, granted_at in stale:
            log(event, reason=reason, granted_at=granted_at)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
