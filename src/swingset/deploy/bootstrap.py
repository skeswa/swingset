"""Run trusted platform administration before an application owner imports data.

Only a dedicated platform job in the exact Postgres container's network
namespace receives this private loopback DSN file. Application and migration
containers never receive the shared cluster administrator credential.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from swingset.state import postgres_bootstrap, postgres_schema


def _target(mode: str, database: str) -> None:
    if mode == "roles":
        if database != "postgres":
            raise RuntimeError("Capability role installation requires the postgres database")
    elif mode == "tables":
        # This is the same identifier contract as Rhuidean's onboarding helper.
        if re.fullmatch(r"swingset[a-z0-9_]{0,47}", database) is None:
            raise RuntimeError("Table preparation requires an explicit Swingset database")
    else:
        raise RuntimeError("Unknown platform bootstrap mode")


def _admin_dsn(path: Path, database: str) -> str:
    if path.is_symlink():
        raise RuntimeError("Platform administrator DSN must be a private regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 0 < metadata.st_size <= 65536
        ):
            raise RuntimeError("Platform administrator DSN file ownership, mode or size differs")
        try:
            dsn = stream.read(65537).decode().strip()
            details = conninfo_to_dict(dsn)
        except (UnicodeError, psycopg.Error):
            raise RuntimeError("Platform administrator DSN is invalid") from None
    if not dsn or details.get("dbname") != database:
        raise RuntimeError("Platform administrator DSN must explicitly name the target database")
    if (
        details.get("host") != "127.0.0.1"
        or details.get("port") != "5432"
        or any(key in details for key in ("hostaddr", "service"))
    ):
        raise RuntimeError(
            "Platform administrator DSN must use the dedicated Postgres namespace loopback endpoint"
        )
    return dsn


def bootstrap(mode: str, admin_dsn_file: Path, database: str) -> dict[str, Any]:
    """Validate the actual target before calling the pinned administrative APIs."""
    _target(mode, database)
    dsn = _admin_dsn(admin_dsn_file, database)
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as connection:
            row = connection.execute(
                "SELECT current_database(),d.oid,pg_get_userbyid(d.datdba),d.datcollate,d.datctype,r.rolsuper "
                "FROM pg_database d JOIN pg_roles r ON r.rolname=current_user "
                "WHERE d.datname=current_database()"
            ).fetchone()
            if row is None or row[0] != database or row[5] is not True:
                raise RuntimeError(
                    "Platform bootstrap requires a cluster administrator on the exact target database"
                )
            if mode == "tables":
                # The maintained helper creates owner=swingset_owner and C/C.
                if tuple(row[2:5]) != ("swingset_owner", "C", "C"):
                    raise RuntimeError(
                        "Swingset table preparation requires its pinned database owner and C locale"
                    )
                marker = postgres_bootstrap.prepare(connection)
                return {
                    "mode": mode,
                    "database": {"name": database, "oid": row[1]},
                    "binding": marker,
                }
            with connection.transaction():
                postgres_schema.install_roles(connection)
                count_row = connection.execute(
                    "SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)",
                    (list(postgres_schema.ROLE_NAMES),),
                ).fetchone()
                if count_row is None:
                    raise RuntimeError("Platform capability role count is unavailable")
                count = count_row[0]
                if count != len(postgres_schema.ROLE_NAMES):
                    raise RuntimeError("Platform capability role installation is incomplete")
            return {
                "mode": mode,
                "database": {"name": database, "oid": row[1]},
                "role_count": count,
                "roles_sha256": postgres_schema.roles_checksum(),
            }
    except psycopg.Error:
        # Driver diagnostics can contain connection details; never echo them.
        raise RuntimeError("Platform bootstrap database operation failed") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("roles", "tables"), required=True)
    parser.add_argument("--admin-dsn-file", type=Path, required=True)
    parser.add_argument("--database", required=True)
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            bootstrap(arguments.mode, arguments.admin_dsn_file, arguments.database), sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
