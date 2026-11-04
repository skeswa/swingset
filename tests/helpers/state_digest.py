"""Independent logical database fingerprints for read-only operator-tool checks."""

import hashlib

from psycopg import sql


def state_digest(conn):
    digest = hashlib.sha256()
    with conn.read_snapshot():
        tables = conn.execute(
            "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname=ANY(%s) ORDER BY n.nspname,c.relname",
            (["fetch", "plan", "evidence", "derive", "project", "release", "ops"],),
        ).fetchall()
        for number, (namespace, table) in enumerate(tables):
            digest.update(f"{namespace}.{table}\n".encode())
            with conn.cursor(name=f"state_digest_{number}") as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT to_jsonb(r)::text AS row_json FROM {}.{} r ORDER BY to_jsonb(r)::text"
                    ).format(sql.Identifier(namespace), sql.Identifier(table))
                )
                for (value,) in cursor:
                    digest.update(value.encode())
                    digest.update(b"\n")
    return digest.hexdigest()
