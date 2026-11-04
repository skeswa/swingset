"""Postgres value, snapshot, lock and failure contracts using real commits."""

import subprocess
import sys
import traceback

import psycopg
import pytest

from swingset.dev.services import ensure_service
from swingset.state import engine, write_deadline
from swingset.state.db import DatabaseLockedError, open_database


def test_rows_keep_index_name_mapping_and_value_iteration():
    row = engine.Row(("Identifier", "count", "absent"), ("雪", 7, None))
    assert tuple(row) == ("雪", 7, None)
    assert row["IDENTIFIER"] == row[0] == "雪"
    assert row[1:] == (7, None)
    assert dict(row) == {"Identifier": "雪", "count": 7, "absent": None}
    assert row.keys() == ["Identifier", "count", "absent"]
    with pytest.raises(IndexError):
        row["missing"]
    with pytest.raises(ValueError, match="duplicate"):
        engine.Row(("Value", "value"), (1, 2))


def test_connection_settings_are_private_and_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("SWINGSET_TEST_RUN", raising=False)
    monkeypatch.delenv("SWINGSET_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        engine.resolve_dsn(tmp_path)
    target = engine.write_settings(tmp_path, "postgresql://private.example/dev")
    assert target.stat().st_mode & 0o777 == 0o600
    assert engine.resolve_dsn(tmp_path) == "postgresql://private.example/dev"
    assert engine.resolve_dsn(tmp_path, "explicit") == "explicit"
    assert engine.read_settings(tmp_path)["environment"] == "dev"


def test_ordinary_open_refuses_schema_change_without_migrating(tmp_path):
    with open_database(tmp_path) as database:
        name = database.connection.info.dbname
    with ensure_service().connect(name) as admin:
        admin.execute("UPDATE ops.meta SET value='36' WHERE key='schema_version'")
    with pytest.raises(RuntimeError, match="schema|migration"):
        open_database(tmp_path)
    with ensure_service().connect(name) as admin:
        assert (
            admin.execute("SELECT value FROM ops.meta WHERE key='schema_version'").fetchone()[0]
            == "36"
        )


def test_environment_is_the_database_identity_not_a_shell_flag(tmp_path, monkeypatch):
    with open_database(tmp_path) as database:
        monkeypatch.setenv("SWINGSET_ENV", "production")
        assert database.environment == "dev"
    engine.write_settings(tmp_path, engine.resolve_dsn(tmp_path), environment="production")
    with pytest.raises(RuntimeError, match="conflict"):
        open_database(tmp_path)


def test_autocommit_outside_transactions_and_duplicate_labels_rejected(tmp_path):
    with open_database(tmp_path) as database:
        conn = database.connection
        assert conn.autocommit and not conn.in_transaction
        assert tuple(conn.execute("SELECT 7::bigint AS n,NULL::text AS missing").fetchone()) == (
            7,
            None,
        )
        assert not conn.in_transaction
        with pytest.raises(ValueError, match="duplicate"):
            conn.execute("SELECT 1 AS duplicate,2 AS DUPLICATE").fetchone()
        assert not conn.in_transaction


def test_reader_keeps_one_snapshot_while_writer_commits(tmp_path):
    with open_database(tmp_path, lock=False) as database:
        with database.transaction() as conn:
            conn.execute("INSERT INTO ops.meta(key,value) VALUES('snapshot-test','old')")
        with database.reader() as reader:
            assert reader.read_only_snapshot
            assert (
                reader.execute("SELECT value FROM ops.meta WHERE key='snapshot-test'").fetchone()[0]
                == "old"
            )
            with database.transaction() as conn:
                conn.execute("UPDATE ops.meta SET value='new' WHERE key='snapshot-test'")
            assert (
                reader.execute("SELECT value FROM ops.meta WHERE key='snapshot-test'").fetchone()[0]
                == "old"
            )
            with pytest.raises(engine.OperationalError):
                reader.execute("UPDATE ops.meta SET value='forbidden' WHERE key='snapshot-test'")
        assert (
            database.connection.execute(
                "SELECT value FROM ops.meta WHERE key='snapshot-test'"
            ).fetchone()[0]
            == "new"
        )


def test_read_transaction_cannot_upgrade_to_writer(tmp_path):
    with open_database(tmp_path) as database:
        with database.transaction(immediate=False):
            with pytest.raises(RuntimeError, match="cannot upgrade"):
                with database.transaction():
                    pytest.fail("a read snapshot yielded a writer")
        assert not database.connection.in_transaction


def test_waiting_writer_reads_the_preceding_commit(tmp_path):
    child_code = """
import sys
from swingset.state.db import open_database
with open_database(sys.argv[1],lock=False) as database:
    print("READY",flush=True)
    with database.transaction() as conn:
        row=conn.execute("SELECT value FROM ops.meta WHERE key='preceding-write'").fetchone()
        print(row[0],flush=True)
"""
    with open_database(tmp_path, lock=False) as database:
        with database.transaction() as conn:
            conn.execute("INSERT INTO ops.meta(key,value) VALUES('preceding-write','visible')")
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert child.stdout.readline().strip() == "READY"
            assert child.poll() is None
        output, error = child.communicate(timeout=20)
        assert child.returncode == 0, error
        assert output.strip() == "visible"


def test_session_lock_survives_commit_and_session_death_releases_it(tmp_path):
    with open_database(tmp_path, mode="shared", loop="derive") as database:
        with database.transaction() as conn:
            conn.execute("INSERT INTO ops.meta(key,value) VALUES('session-lock','committed')")
        with pytest.raises(DatabaseLockedError):
            open_database(tmp_path, mode="shared", loop="derive")
        with ensure_service().connect(database.connection.info.dbname) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(%s)", (database.connection.info.backend_pid,)
            )
        with pytest.raises(engine.ConnectionLost):
            database.connection.check_alive()
    with open_database(tmp_path, mode="shared", loop="derive"):
        pass


def test_server_sql_and_row_lock_wait_inherit_the_write_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.05)
    with open_database(tmp_path) as database:
        with pytest.raises(write_deadline.WriteDeadlineExceeded):
            with database.transaction() as conn:
                conn.execute("SELECT pg_sleep(2) AS slept")
        assert not database.connection.in_transaction
        with database.transaction() as conn:
            conn.execute("INSERT INTO ops.meta(key,value) VALUES('row-lock','old')")
        with ensure_service().connect(database.connection.info.dbname) as admin:
            admin.execute("BEGIN")
            admin.execute("UPDATE ops.meta SET value='held' WHERE key='row-lock'")
            with pytest.raises(write_deadline.WriteDeadlineExceeded):
                with database.transaction() as conn:
                    conn.execute("UPDATE ops.meta SET value='forbidden' WHERE key='row-lock'")
            admin.rollback()
        assert (
            database.connection.execute(
                "SELECT value FROM ops.meta WHERE key='row-lock'"
            ).fetchone()[0]
            == "old"
        )


def test_lost_commit_acknowledgement_is_uncertain_and_never_retried(tmp_path, monkeypatch):
    with open_database(tmp_path) as database:
        calls = []
        original = psycopg.Connection.commit

        def lose_ack(connection):
            calls.append(1)
            original(connection)
            raise psycopg.OperationalError("private connection detail")

        monkeypatch.setattr(psycopg.Connection, "commit", lose_ack)
        with pytest.raises(engine.CommitUncertain) as error:
            with database.transaction() as conn:
                conn.execute("INSERT INTO ops.meta(key,value) VALUES('uncertain-commit','landed')")
        assert calls == [1]
        assert database.connection.closed
        assert "private" not in str(error.value)
        monkeypatch.setattr(psycopg.Connection, "commit", original)
    with open_database(tmp_path) as reopened:
        assert (
            reopened.connection.execute(
                "SELECT value FROM ops.meta WHERE key='uncertain-commit'"
            ).fetchone()[0]
            == "landed"
        )


def test_constraint_exception_does_not_expose_private_values(tmp_path):
    secret = "suppressed-person-private-note"
    with open_database(tmp_path) as database:
        with pytest.raises(engine.IntegrityError) as caught:
            with database.transaction() as conn:
                conn.execute("INSERT INTO ops.meta(key,value) VALUES(%s,'first')", (secret,))
                conn.execute("INSERT INTO ops.meta(key,value) VALUES(%s,'second')", (secret,))
        rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
        assert secret not in rendered
        assert "23505" in str(caught.value)
        assert not database.connection.in_transaction


def test_executemany_reports_all_affected_rows(tmp_path):
    with open_database(tmp_path) as database:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                "INSERT INTO ops.meta(key,value) VALUES(%s,%s)",
                [("batch-one", "1"), ("batch-two", "2")],
            )
            assert cursor.rowcount == 2
        assert (
            database.connection.execute(
                "SELECT count(*) FROM ops.meta WHERE key LIKE 'batch-%%'"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize("command", ["COMMIT", "END TRANSACTION;"])
def test_explicit_commit_statements_classify_lost_ack_as_uncertain(tmp_path, monkeypatch, command):
    with open_database(tmp_path) as database:
        conn = database.connection
        conn.execute("BEGIN")
        conn.execute("INSERT INTO ops.meta(key,value) VALUES('explicit-commit','landed')")
        original = psycopg.Cursor.execute

        def lose_ack(self, query, *args, **kwargs):
            result = original(self, query, *args, **kwargs)
            if self.connection is conn and str(query) == command:
                conn.close()
                raise psycopg.OperationalError("connection closed after commit")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(psycopg.Cursor, "execute", lose_ack)
            with pytest.raises(engine.CommitUncertain):
                conn.execute(command)
    with open_database(tmp_path) as reopened:
        assert (
            reopened.connection.execute(
                "SELECT value FROM ops.meta WHERE key='explicit-commit'"
            ).fetchone()[0]
            == "landed"
        )


def test_planner_error_does_not_expose_private_constant(tmp_path):
    with open_database(tmp_path, lock=False) as database:
        with pytest.raises(engine.Error) as failure:
            database.connection.planned_relations("SELECT CAST('private-planner-value' AS bigint)")
        assert "private-planner-value" not in str(failure.value)
        assert "SQLSTATE 22P02" in str(failure.value)


def test_caught_statement_failure_cannot_be_reported_as_a_successful_commit(tmp_path):
    with open_database(tmp_path, lock=False) as database:
        with pytest.raises(engine.DatabaseError, match="transaction failed"):
            with database.transaction() as conn:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES('aborted-before-commit','discarded')"
                )
                try:
                    conn.execute("SELECT 1/0 AS invalid")
                except engine.DatabaseError:
                    pass
        assert not database.connection.in_transaction
        assert (
            database.connection.execute(
                "SELECT 1 FROM meta WHERE key='aborted-before-commit'"
            ).fetchone()
            is None
        )


def test_timeout_settings_reuse_keeps_the_absolute_check_on_every_query(tmp_path, monkeypatch):
    from swingset.state.engine import connection as module
    from swingset.state.write_deadline import WriteDeadlineExceeded, bounded_write

    with open_database(tmp_path) as database:
        conn = database.connection
        tick = [1000.0]
        applied = []
        original = psycopg.Cursor.execute

        def execute(cursor, query, *args, **kwargs):
            if isinstance(query, str) and query.startswith("SELECT set_config('statement_timeout'"):
                applied.append(args[0])
            return original(cursor, query, *args, **kwargs)

        monkeypatch.setattr(psycopg.Cursor, "execute", execute)
        monkeypatch.setattr(module.time, "monotonic", lambda: tick[0])
        with pytest.raises(WriteDeadlineExceeded):
            with conn.transaction(), bounded_write(conn, seconds=1):
                for _ in range(3):
                    assert conn.execute("SELECT 1 AS ready").fetchone()[0] == 1
                assert applied == [("1000", "1000")]
                tick[0] += 0.002
                conn.execute("SELECT 1 AS ready").fetchone()
                assert len(applied) == 2 and int(applied[-1][0]) <= 998
                tick[0] = 1001.0
                conn.execute("SELECT 1 AS too_late")
        assert len(applied) == 2  # Expiry refused the statement before another SQL call.
        assert not conn.in_transaction


def test_server_timeouts_survive_raw_savepoint_rollback_and_new_transaction(tmp_path, monkeypatch):
    from swingset.state.engine import connection as module
    from swingset.state.write_deadline import bounded_write

    with open_database(tmp_path) as database:
        conn = database.connection
        monkeypatch.setattr(module.time, "monotonic", lambda: 1000.0)

        class AbandonSavepoint(Exception):
            pass

        for _ in range(2):
            with conn.transaction(), bounded_write(conn, seconds=1):
                try:
                    with conn.transaction():
                        # The raw psycopg savepoint predates the first timeout SET.
                        conn.execute("SELECT 1 AS ready")
                        raise AbandonSavepoint()
                except AbandonSavepoint:
                    pass
                for name in ("statement_timeout", "lock_timeout"):
                    assert (
                        conn.execute(
                            "SELECT setting FROM pg_settings WHERE name=%s", (name,)
                        ).fetchone()[0]
                        == "1000"
                    )
                for name in ("statement_timeout", "lock_timeout"):
                    conn.execute("SELECT set_config(%s,'0',true)", (name,))
                    assert (
                        conn.execute(
                            "SELECT setting FROM pg_settings WHERE name=%s", (name,)
                        ).fetchone()[0]
                        == "1000"
                    )
            # The next iteration deliberately reuses the identical absolute
            # deadline and millisecond, across a real COMMIT and new BEGIN.
            assert not conn.in_transaction


def test_healthy_direct_close_acknowledges_locks_despite_expired_deadline(tmp_path, monkeypatch):
    from swingset.state.engine.locks import try_lock

    database = open_database(tmp_path)
    conn = database.connection
    dsn = engine.resolve_dsn(tmp_path)
    assert try_lock(conn, "direct-close-witness")
    calls = []
    original = psycopg.Cursor.execute

    def execute(cursor, query, *args, **kwargs):
        if cursor.connection is conn and query == "SELECT pg_advisory_unlock_all()":
            calls.append(query)
        return original(cursor, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    conn._write_deadline = -1.0
    try:
        with conn.observing(lambda *_args: pytest.fail("cleanup entered domain observation")):
            conn.close()
        assert conn.closed and not conn._session_locks
        assert calls == ["SELECT pg_advisory_unlock_all()"]
        with engine.connect(dsn) as other:
            assert try_lock(other, "direct-close-witness")
    finally:
        database.close()


def test_database_close_does_not_repeat_an_already_acknowledged_unlock(tmp_path, monkeypatch):
    database = open_database(tmp_path)
    conn = database.connection
    original = psycopg.Cursor.execute

    def execute(cursor, query, *args, **kwargs):
        if cursor.connection is conn and query == "SELECT pg_advisory_unlock_all()":
            pytest.fail("Held.release already acknowledged all owned session locks")
        return original(cursor, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    database.close()
    assert conn.closed


@pytest.mark.parametrize("status", ["transaction", "failed_transaction", "closed_unknown"])
def test_nonidle_or_closed_connections_close_without_cleanup_sql(tmp_path, monkeypatch, status):
    from swingset.state.engine.locks import try_lock

    with open_database(tmp_path):
        pass
    conn = engine.connect(engine.resolve_dsn(tmp_path))
    assert try_lock(conn, "nonidle-close")
    if status == "closed_unknown":
        psycopg.Connection.close(conn)
    else:
        conn.execute("BEGIN")
        if status == "failed_transaction":
            with pytest.raises(psycopg.errors.DivisionByZero):
                conn.execute("SELECT 1 / 0")
    original = psycopg.Cursor.execute

    def execute(cursor, query, *args, **kwargs):
        if cursor.connection is conn:
            pytest.fail("nonidle or closed connection attempted cleanup SQL")
        return original(cursor, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    conn.close()
    assert conn.closed and not conn._session_locks


@pytest.mark.parametrize("original_error", [engine.CommitUncertain, ValueError])
@pytest.mark.parametrize("cleanup_error", [psycopg.OperationalError, RuntimeError])
def test_cleanup_failure_closes_socket_without_masking_original_error(
    tmp_path, monkeypatch, original_error, cleanup_error
):
    database = open_database(tmp_path)
    conn = database.connection
    original = psycopg.Cursor.execute

    def execute(cursor, query, *args, **kwargs):
        if cursor.connection is conn and query == "SELECT pg_advisory_unlock_all()":
            raise cleanup_error("injected cleanup RPC failure")
        return original(cursor, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    try:
        with pytest.raises(original_error, match="original operation failed"):
            try:
                raise original_error("original operation failed")
            finally:
                conn.close()
        assert conn.closed and not conn._session_locks
    finally:
        database.close()
