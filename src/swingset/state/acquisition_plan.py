"""The acquisition plan: year and page rows, each with a reason.

The plan loop writes one `acquisition_plan` row per year (`year:<yyyy>`) and
per planned page (`page:<watch_id>`); `reason` is `eligible` or the gate's
stop reason (migration 34). This module reads and writes rows; planning and
acquisition decide what goes in them (D-0218).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import astuple, dataclass, fields
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from swingset.state.db import Database

PlanKind = Literal["year", "page"]
ELIGIBLE = "eligible"
#: Rows per write transaction when a plan is larger.
CHUNK_ROWS = 5000


@dataclass(frozen=True)
class PlanRow:
    plan_key: str
    kind: PlanKind
    reason: str
    planned_at: str
    run_id: str
    year: int | None = None
    source: str | None = None
    source_ref: str | None = None
    page_kind: str | None = None
    watch_kind: str | None = None
    url: str | None = None
    archive_url: str | None = None
    event_id: str | None = None
    rank: int | None = None
    detail_json: str | None = None


_COLUMNS = tuple(field.name for field in fields(PlanRow))


def year_key(year: int) -> str:
    return f"year:{year}"


def page_key(watch_id: str) -> str:
    return f"page:{watch_id}"


def get(conn: sqlite3.Connection, plan_key: str) -> PlanRow | None:
    row = conn.execute(
        f"SELECT {','.join(_COLUMNS)} FROM acquisition_plan WHERE plan_key=?", (plan_key,)
    ).fetchone()
    return None if row is None else PlanRow(*row)


def upsert(conn: sqlite3.Connection, row: PlanRow) -> None:
    updates = ",".join(f"{name}=excluded.{name}" for name in _COLUMNS if name != "plan_key")
    conn.execute(
        f"INSERT INTO acquisition_plan({','.join(_COLUMNS)}) "
        f"VALUES ({','.join('?' * len(_COLUMNS))}) "
        f"ON CONFLICT(plan_key) DO UPDATE SET {updates}",
        astuple(row),
    )


def delete(conn: sqlite3.Connection, plan_key: str) -> bool:
    return conn.execute("DELETE FROM acquisition_plan WHERE plan_key=?", (plan_key,)).rowcount > 0


def counts_by_reason(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """Rows per (kind, reason)."""
    return {
        (str(kind), str(reason)): int(count)
        for kind, reason, count in conn.execute(
            "SELECT kind,reason,count(*) FROM acquisition_plan GROUP BY kind,reason "
            "ORDER BY kind,reason"
        )
    }


def eligible_pages(conn: sqlite3.Connection) -> list[PlanRow]:
    """Eligible page rows in plan order (`rank`), what acquire offers."""
    return [
        PlanRow(*row)
        for row in conn.execute(
            f"SELECT {','.join(_COLUMNS)} FROM acquisition_plan "
            "WHERE kind='page' AND reason=? ORDER BY rank,plan_key",
            (ELIGIBLE,),
        )
    ]


def write(
    database: Database,
    rows: Sequence[PlanRow],
    *,
    run_id: str,
    prune_pages: bool,
    chunk: int = CHUNK_ROWS,
) -> dict[str, int]:
    """Upsert one plan pass's rows, ``chunk`` rows per transaction.

    The last transaction deletes the year rows this pass did not write and,
    when ``prune_pages`` (the pass was not cut by its deadline), the page
    rows it did not write, so a truncated pass keeps the older rows it did
    not reach.
    """
    written = deleted = 0
    starts = range(0, len(rows), chunk) if rows else range(1)
    last = starts[-1]
    for start in starts:
        with database.transaction() as conn:
            for row in rows[start : start + chunk]:
                upsert(conn, row)
                written += 1
            if start == last:
                deleted += conn.execute(
                    "DELETE FROM acquisition_plan WHERE kind='year' AND run_id<>?", (run_id,)
                ).rowcount
                if prune_pages:
                    deleted += conn.execute(
                        "DELETE FROM acquisition_plan WHERE kind='page' AND run_id<>?", (run_id,)
                    ).rowcount
    return {"written": written, "deleted": deleted, "transactions": len(starts)}
