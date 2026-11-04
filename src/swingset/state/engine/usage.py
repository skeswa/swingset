"""Server catalog measurements independent of operator filesystem quota checks."""

from __future__ import annotations

from typing import Any

from .connection import Connection


def database_usage(conn: Connection) -> dict[str, Any]:
    """Allocated database and application-relation bytes from the server.

    WAL belongs to the cluster and dead-tuple space is not a SQLite free list;
    neither can be attributed exactly to this database, so both are unknown.
    """
    database_bytes, relation_bytes = conn.execute(
        "SELECT pg_database_size(current_database()), "
        "coalesce(sum(pg_total_relation_size(c.oid)),0)::bigint "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind IN ('r','m') AND n.nspname IN "
        "('fetch','plan','evidence','derive','project','release','ops')"
    ).fetchone()
    return {
        "file_bytes": int(database_bytes),
        "bytes_in_use": int(relation_bytes),
        "wal_bytes": None,
        "free_list_bytes": None,
    }
