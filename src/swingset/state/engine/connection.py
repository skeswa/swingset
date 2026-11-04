"""Postgres connections, statement observation and immutable snapshot readers.

SQL is passed unchanged to psycopg. Every caller owns its PostgreSQL SQL;
this module contains no SQLite dialect emulation.
"""

from __future__ import annotations

import contextlib
import re
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Self

import psycopg
from psycopg import pq
from psycopg.abc import PQGen, QueryNoTemplate
from psycopg.pq.abc import PGresult

from .rows import Row, row_factory

Observer = Callable[[Any, Any], None]
SEARCH_PATH = 'pg_catalog,"fetch",plan,evidence,derive,project,release,ops,pg_temp'


class ConnectionLost(psycopg.OperationalError):
    """A session disappeared; its advisory locks disappeared with it."""


class CommitUncertain(ConnectionLost):
    """COMMIT might have landed; the caller must reconcile without repeating effects."""


class LockTimeout(psycopg.OperationalError):
    """The transaction could not acquire a lock before its deadline."""


class ReadOnlyViolation(psycopg.OperationalError):
    sqlstate = "25006"


class WriteTimeout(psycopg.OperationalError):
    """The server canceled a statement at the write deadline."""


def _connection_error(error: psycopg.Error, *, committing: bool = False) -> psycopg.Error:
    code = error.sqlstate
    if committing and code in {"55P03", "57014"}:
        return CommitUncertain("database commit acknowledgement deadline exceeded")
    if code == "55P03":
        return LockTimeout("database lock deadline exceeded")
    if code == "25006":
        return ReadOnlyViolation("readonly database snapshot cannot write")
    if code == "57014":
        return WriteTimeout("database statement deadline exceeded")
    if code in {"57P01", "57P02", "57P03"} or code is not None and code.startswith("08"):
        return (
            CommitUncertain("database commit acknowledgement lost")
            if committing
            else ConnectionLost("database session lost; operation stopped")
        )
    if isinstance(error, psycopg.OperationalError) and code is None:
        return (
            CommitUncertain("database commit acknowledgement lost")
            if committing
            else ConnectionLost("database connection lost; operation stopped")
        )
    # Constraint detail may contain suppressed identities or private notes.
    # SQLSTATE and the exception class carry the stable error category.
    primary = error.diag.message_primary or ""
    from .. import postgres_schema

    fixed = {
        value.replace("''", "'")
        for migration in postgres_schema.ROOT.glob("[0-9]*_*.sql")
        for value in re.findall(r"\bMESSAGE\s*=\s*'((?:''|[^'])*)'", migration.read_text())
    }
    message = (
        primary if primary in fixed else f"database operation failed (SQLSTATE {code or 'unknown'})"
    )
    return type(error)(message)


class Cursor(psycopg.Cursor[Row]):
    def execute(
        self,
        query: Any,
        params: Any = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> Self:
        conn = self.connection
        assert isinstance(conn, Connection)
        text = (
            query.as_string(conn)
            if hasattr(query, "as_string")
            else query.decode()
            if isinstance(query, bytes)
            else str(query)
        )
        configuring = re.search(r"\bset_config\s*\(", text, re.IGNORECASE) is not None
        boundary = (
            re.match(
                r"\s*(?:BEGIN|START|COMMIT|END|ROLLBACK|ABORT|SAVEPOINT|RELEASE|SET|RESET)\b",
                text,
                re.IGNORECASE,
            )
            is not None
        )
        if boundary or configuring:
            conn._timeout_settings = None
        committing = (
            re.fullmatch(
                r"\s*(?:COMMIT|END)(?:\s+(?:WORK|TRANSACTION))?\s*;?\s*", text, re.IGNORECASE
            )
            is not None
        )
        if committing and conn.info.transaction_status == pq.TransactionStatus.INERROR:
            raise psycopg.errors.InFailedSqlTransaction(
                "database transaction failed; output not committed"
            )
        try:
            conn._before(query, params)
        except BaseException:
            conn._timeout_settings = None
            raise
        try:
            super().execute(query, params, prepare=prepare, binary=binary)
        except (KeyboardInterrupt, SystemExit):
            conn._timeout_settings = None
            if committing:
                conn.close()
                raise CommitUncertain("database commit acknowledgement interrupted") from None
            raise
        except psycopg.Error as error:
            conn._timeout_settings = None
            mapped = _connection_error(error, committing=committing)
            if isinstance(mapped, ConnectionLost):
                conn.close()
            if mapped is error:
                raise
            raise mapped from None
        except BaseException:
            conn._timeout_settings = None
            raise
        conn._after(self.statusmessage or "", self.rowcount)
        if configuring:
            # set_config can change search_path just like SET. Bound parameter
            # names need the same conservative invalidation as literal names.
            conn._invalidate_plans()
        if boundary or configuring:
            conn._timeout_settings = None
        return self

    def fetchone(self) -> Any:
        return super().fetchone()

    def fetchmany(self, size: int = 0) -> list[Any]:
        return super().fetchmany(size or self.arraysize)

    def fetchall(self) -> list[Any]:
        return super().fetchall()

    def __next__(self) -> Any:
        return super().__next__()

    def executemany(self, query: Any, params_seq: Any, *, returning: bool = False) -> None:
        # Run the same observation and deadline boundary for every statement.
        # Callers needing bulk import use COPY, which streams without this API.
        if returning:
            raise ValueError("executemany RETURNING requires explicit per-row execute")
        affected = 0
        for parameters in params_seq:
            self.execute(query, parameters)
            affected += max(0, self.rowcount)
        self._rowcount = affected


class ServerCursor(psycopg.ServerCursor[Row]):
    def execute(
        self, query: Any, params: Any = None, *, binary: bool | None = None, **kwargs: Any
    ) -> Self:
        conn = self.connection
        assert isinstance(conn, Connection)
        conn._before(query, params)
        try:
            super().execute(query, params, binary=binary, **kwargs)
        except psycopg.Error as error:
            raise _connection_error(error) from None
        return self

    def fetchone(self) -> Any:
        try:
            return super().fetchone()
        except psycopg.Error as error:
            raise _connection_error(error) from None

    def fetchmany(self, size: int = 0) -> list[Any]:
        try:
            return super().fetchmany(size or self.arraysize)
        except psycopg.Error as error:
            raise _connection_error(error) from None

    def fetchall(self) -> list[Any]:
        try:
            return super().fetchall()
        except psycopg.Error as error:
            raise _connection_error(error) from None

    def __next__(self) -> Any:
        try:
            return super().__next__()
        except psycopg.Error as error:
            raise _connection_error(error) from None


class Connection(psycopg.Connection[Row]):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.state_dir: Path | None = None
        self.restore_pending = False
        self.cursor_factory = Cursor
        self.server_cursor_factory = ServerCursor
        self._observers: list[Observer] = []
        self._observing = False
        self._write_deadline: float | None = None
        self._timeout_settings: tuple[float, int] | None = None
        self._plans_changed_in_transaction = False
        self._serial = 0
        self._commit_serial = 0
        self._snapshot_depth = 0
        self._schema_serial = 0
        self._plans: dict[tuple[str, int], frozenset[str]] = {}
        self._session_locks: set[tuple[int, bool]] = set()

    def execute(
        self, query: Any, params: Any = None, *, prepare: bool | None = None, binary: bool = False
    ) -> Cursor:
        if self.closed:
            raise ConnectionLost("database session lost; operation stopped")
        cursor = super().execute(query, params, prepare=prepare, binary=binary)
        assert isinstance(cursor, Cursor)
        return cursor

    @property
    def in_transaction(self) -> bool:
        return self.info.transaction_status in {
            pq.TransactionStatus.INTRANS,
            pq.TransactionStatus.INERROR,
            pq.TransactionStatus.ACTIVE,
        }

    @property
    def total_changes(self) -> int:
        """A local invalidation serial, including writes later rolled back."""
        return self._serial

    @property
    def read_only_snapshot(self) -> bool:
        return self._snapshot_depth > 0

    @contextlib.contextmanager
    def observing(self, observer: Observer) -> Iterator[None]:
        self._observers.append(observer)
        try:
            yield
        finally:
            self._observers.remove(observer)

    def executemany(self, query: Any, params_seq: Any) -> Cursor:
        cursor = self.cursor()
        assert isinstance(cursor, Cursor)
        cursor.executemany(query, params_seq)
        return cursor

    def _before(self, query: Any, params: Any) -> None:
        if self.closed:
            raise ConnectionLost("database session lost; operation stopped")
        if isinstance(query, str) and re.fullmatch(
            r"\s*(?:ROLLBACK(?:\s+TO(?:\s+SAVEPOINT)?\s+[A-Za-z_][A-Za-z_0-9]*)?|RELEASE(?:\s+SAVEPOINT)?\s+[A-Za-z_][A-Za-z_0-9]*)\s*;?\s*",
            query,
            re.IGNORECASE,
        ):
            return  # cleanup must work in an aborted or expired transaction
        if self._write_deadline is not None and not self._observing:
            remaining = self._write_deadline - time.monotonic()
            if remaining <= 0:
                raise WriteTimeout("database write deadline exceeded")
            milliseconds = max(1, int(remaining * 1000))
            key = (self._write_deadline, milliseconds)
            if self._timeout_settings != key:
                try:
                    with psycopg.Cursor(self) as settings:
                        settings.execute(
                            "SELECT set_config('statement_timeout',%s,true) AS statement_timeout, set_config('lock_timeout',%s,true) AS lock_timeout",
                            (str(milliseconds), str(milliseconds)),
                        )
                except psycopg.Error as error:
                    mapped = _connection_error(error)
                    if isinstance(mapped, ConnectionLost):
                        self.close()
                    raise mapped from None
                self._timeout_settings = key
        if self._observing or not self._observers:
            return
        self._observing = True
        try:
            for observer in tuple(self._observers):
                observer(query, params)
        finally:
            self._observing = False

    def _invalidate_plans(self) -> None:
        self._schema_serial += 1
        self._plans.clear()
        self._plans_changed_in_transaction = self.in_transaction

    def _exec_command(
        self, command: QueryNoTemplate, result_format: pq.Format = pq.Format.TEXT
    ) -> PQGen[PGresult | None]:
        # psycopg's transaction contexts issue BEGIN/COMMIT/savepoint commands
        # directly, bypassing Cursor.execute. Rollback can restore local GUCs.
        self._timeout_settings = None
        if self._plans_changed_in_transaction:
            self._invalidate_plans()
        try:
            return (yield from super()._exec_command(command, result_format))
        finally:
            self._timeout_settings = None
            if not self.in_transaction:
                self._plans_changed_in_transaction = False

    def _after(self, status: str, count: int) -> None:
        head = status.split(" ", 1)[0]
        if head == "COMMIT":
            self._commit_serial += 1
        if head in {"CREATE", "ALTER", "DROP", "SET", "RESET"}:
            self._invalidate_plans()
        if head in {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "SET", "RESET"}:
            self._timeout_settings = None
        if head in {"COMMIT", "ROLLBACK"} and self._plans_changed_in_transaction:
            self._invalidate_plans()
        if head in {
            "INSERT",
            "UPDATE",
            "DELETE",
            "MERGE",
            "CREATE",
            "ALTER",
            "DROP",
            "TRUNCATE",
            "COPY",
        }:
            self._serial += max(1, count)

    def commit(self) -> None:
        self._timeout_settings = None
        if self.info.transaction_status == pq.TransactionStatus.INERROR:
            raise psycopg.errors.InFailedSqlTransaction(
                "database transaction failed; output not committed"
            )
        self._before("COMMIT", None)
        pending = self.in_transaction
        try:
            super().commit()
            if pending:
                self._commit_serial += 1
        except (KeyboardInterrupt, SystemExit):
            self.close()
            raise CommitUncertain("database commit acknowledgement interrupted") from None
        except psycopg.Error as error:
            mapped = _connection_error(error, committing=True)
            if isinstance(mapped, CommitUncertain):
                self.close()
            if mapped is error:
                raise
            raise mapped from None

    def rollback(self) -> None:
        self._timeout_settings = None
        try:
            super().rollback()
        except psycopg.Error as error:
            raise _connection_error(error) from None
        finally:
            self._timeout_settings = None

    def close(self) -> None:
        previous_exception = sys.exception()
        try:
            if (
                self._session_locks
                and not self.closed
                and self.info.transaction_status == pq.TransactionStatus.IDLE
            ):
                # Socket close alone leaves a backend-exit race. A healthy idle
                # session can confirm every session unlock before PQfinish.
                # Cleanup bypasses domain observation and an expired write bound.
                with psycopg.Cursor(self) as cleanup:
                    cleanup.execute("SELECT pg_advisory_unlock_all()")
                    cleanup.fetchone()
        except psycopg.Error:
            # Preserve the caller's uncertainty if cleanup cannot be confirmed.
            pass
        except BaseException:
            if previous_exception is None:
                raise
        finally:
            self._timeout_settings = None
            self._plans_changed_in_transaction = False
            self._plans.clear()
            try:
                super().close()
            finally:
                self._session_locks.clear()

    def check_alive(self) -> None:
        """Never reconnect after holding locks or issuing an external effect."""
        self.execute("SELECT 1 AS alive").fetchone()

    @contextlib.contextmanager
    def read_snapshot(self) -> Iterator[Connection]:
        if self.in_transaction:
            if not self.read_only_snapshot:
                # The writer lock already serializes writes for this transaction.
                yield self
                return
            self._snapshot_depth += 1
            try:
                yield self
            finally:
                self._snapshot_depth -= 1
            return
        self.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self._snapshot_depth = 1
        try:
            self.execute("SELECT pg_current_snapshot()::text AS snapshot").fetchone()
            yield self
        finally:
            self._snapshot_depth = 0
            if not self.closed:
                self.rollback()

    def planned_relations(self, query: Any, params: Any = None) -> frozenset[str]:
        """Use the planner's resolved relation names, including views and CTEs."""
        from psycopg._queries import PostgresQuery
        from psycopg.adapt import Transformer

        try:
            prepared = PostgresQuery(Transformer.from_context(self))
            prepared.convert(query, params)
            statement = prepared.query.decode(self.info.encoding)
            key = (statement, self._schema_serial)
            if key in self._plans:
                return self._plans[key]
            with psycopg.Cursor(self) as cursor:
                cursor.execute("EXPLAIN (GENERIC_PLAN, FORMAT JSON) " + statement)
                row = cursor.fetchone()
        except psycopg.Error as error:
            mapped = _connection_error(error)
            if isinstance(mapped, ConnectionLost):
                self.close()
            raise mapped from None
        names: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                name = value.get("Relation Name")
                if isinstance(name, str):
                    names.add(name)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        if row is not None:
            walk(row[0])
        found = frozenset(names)
        self._plans[key] = found
        return found


def connect(
    dsn: str,
    *,
    password: str | None = None,
    application_name: str = "swingset",
    read_only: bool = False,
    timeout: float = 70.0,
) -> Connection:
    from psycopg.conninfo import make_conninfo

    if password is not None:
        dsn = make_conninfo(dsn, password=password)
    try:
        conn = Connection.connect(
            dsn,
            autocommit=True,
            row_factory=row_factory,
            application_name=application_name,
            options=f"-c lock_timeout={max(1, int(timeout * 1000))} -c idle_in_transaction_session_timeout=7200000 -c transaction_timeout=7200000",
        )
    except psycopg.Error as error:
        raise _connection_error(error) from None
    try:
        conn.execute("SELECT set_config('search_path',%s,false) AS search_path", (SEARCH_PATH,))
        if read_only:
            conn.execute("SET default_transaction_read_only=on")
        return conn
    except BaseException:
        conn.close()
        raise
