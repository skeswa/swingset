"""Postgres catalog queries shared by state readers and diagnostics."""

from __future__ import annotations

from .engine import Connection


def table_names(conn: Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relkind IN ('r','p') AND pg_table_is_visible(c.oid) "
            "AND n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND n.nspname NOT LIKE 'pg_toast%%'"
        )
    }


def columns(conn: Connection, table: str) -> tuple[str, ...]:
    from .postgres_schema import ROWID_TABLES

    return tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT attname FROM pg_catalog.pg_attribute "
            "WHERE attrelid=to_regclass(%s) AND attnum>0 AND NOT attisdropped ORDER BY attnum",
            (table,),
        )
        if not (str(row[0]) == "rowid" and table.rsplit(".", 1)[-1] in ROWID_TABLES)
    )


def version(conn: Connection) -> int:
    row = conn.execute("SELECT value FROM ops.meta WHERE key='schema_version'").fetchone()
    if row is None:
        raise RuntimeError("database schema identity is missing")
    return int(row[0])
