"""Preserve publication's semantic reservation while operator controls remain writable."""

import sqlite3

CONTROL_TABLES = frozenset(
    {
        "operator_pauses",
        "control_state",
        "control_events",
        "execution_admissions",
        "execution_dependencies",
    }
)


def install_publication_fences(conn: sqlite3.Connection) -> None:
    """Refresh coverage after every migration, including newly introduced tables."""
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        if table in CONTROL_TABLES:
            continue
        quoted_table = '"' + table.replace('"', '""') + '"'
        for action in ("INSERT", "UPDATE", "DELETE"):
            name = '"' + f"publication_fence_{table}_{action.lower()}".replace('"', '""') + '"'
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {quoted_table} "
                "WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) "
                "BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END"
            )
