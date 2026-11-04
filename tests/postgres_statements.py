"""Observe one real Postgres connection in tests without enabling SQL logging."""

from contextlib import contextmanager
from unittest.mock import patch

import pytest


@contextmanager
def observing(connection, callback):
    with connection.cursor() as cursor:
        cursor_type = type(cursor)
    original = cursor_type.execute

    def execute(cursor, statement, parameters=None, **kwargs):
        if cursor.connection is connection:
            callback(
                statement if isinstance(statement, str) else statement.as_string(connection),
                parameters,
            )
        return original(cursor, statement, parameters, **kwargs)

    with patch.object(cursor_type, "execute", execute):
        yield


def contents(connection):
    """Hash every physical application row for a read-only fixture assertion."""
    from swingset.state import postgres_schema
    from swingset.state.postgres_import import _postgres_rows, _row_digest

    with connection.read_snapshot():
        result = {
            table: _row_digest(_postgres_rows(connection, table, physical=True))
            for table in postgres_schema.reference_manifest()["tables"]
        }
        result["environment"] = tuple(
            connection.execute(
                "SELECT environment,database_id,created_at FROM ops.environment_identity"
            ).fetchone()
        )
    return result


def assert_integrity(connection):
    """Check installed schema and actual composite foreign-key rows after a crash."""
    from psycopg import sql

    from swingset.state import postgres_schema as schema

    assert schema.verify(connection)["equal"]
    for table, specification in schema.reference_manifest()["tables"].items():
        groups = {}
        for key in specification["foreign_keys"]:
            groups.setdefault(key["id"], []).append(key)
        for parts in groups.values():
            parts.sort(key=lambda item: item["seq"])
            present = sql.SQL(" AND ").join(
                sql.SQL("source.{} IS NOT NULL").format(sql.Identifier(item["from"]))
                for item in parts
            )
            matching = sql.SQL(" AND ").join(
                sql.SQL("target.{}=source.{}").format(
                    sql.Identifier(item["to"]), sql.Identifier(item["from"])
                )
                for item in parts
            )
            statement = sql.SQL(
                "SELECT 1 FROM {} source WHERE {} AND NOT EXISTS(SELECT 1 FROM {} target WHERE {}) LIMIT 1"
            ).format(
                sql.Identifier(schema.namespace(table), table),
                present,
                sql.Identifier(schema.namespace(parts[0]["table"]), parts[0]["table"]),
                matching,
            )
            assert connection.execute(statement).fetchone() is None, (table, parts[0]["table"])


@pytest.fixture
def empty_connections(tmp_path):
    """Owned native databases for readers that deliberately lack modern tables."""
    from swingset.dev.testing import provision_empty
    from swingset.state import engine

    opened = []

    def create():
        owner = provision_empty(tmp_path / f"native-empty-{len(opened)}")
        connection = engine.connect(owner)
        connection.execute("SET search_path=public,pg_catalog")
        opened.append(connection)
        return connection

    try:
        yield create
    finally:
        for connection in opened:
            connection.close()
