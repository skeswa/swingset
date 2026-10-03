"""Each loop's latest tick: run, times, failure and counters (migration 34).

One `loop_state` row per loop (`acquire`, `plan`, `derive`, `release`,
`maintain`). `cycle` stays a name only so the rows the removed cycle wrote
still read (D-0239); nothing writes it. This module reads and writes rows;
the loop runtime decides when.
"""

from __future__ import annotations

import sqlite3
from dataclasses import astuple, dataclass, fields
from typing import Literal

LoopName = Literal["acquire", "plan", "derive", "release", "maintain", "cycle"]
LOOPS: tuple[LoopName, ...] = ("acquire", "plan", "derive", "release", "maintain", "cycle")


@dataclass(frozen=True)
class LoopStateRow:
    loop: LoopName
    updated_at: str
    run_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    failed: int | None = None
    error: str | None = None
    counters_json: str = "{}"
    last_progress_at: str | None = None


_COLUMNS = tuple(field.name for field in fields(LoopStateRow))


def get(conn: sqlite3.Connection, loop: str) -> LoopStateRow | None:
    row = conn.execute(
        f"SELECT {','.join(_COLUMNS)} FROM loop_state WHERE loop=?", (loop,)
    ).fetchone()
    return None if row is None else LoopStateRow(*row)


def upsert(conn: sqlite3.Connection, row: LoopStateRow) -> None:
    updates = ",".join(f"{name}=excluded.{name}" for name in _COLUMNS if name != "loop")
    conn.execute(
        f"INSERT INTO loop_state({','.join(_COLUMNS)}) VALUES ({','.join('?' * len(_COLUMNS))}) "
        f"ON CONFLICT(loop) DO UPDATE SET {updates}",
        astuple(row),
    )


def delete(conn: sqlite3.Connection, loop: str) -> bool:
    return conn.execute("DELETE FROM loop_state WHERE loop=?", (loop,)).rowcount > 0


def all_rows(conn: sqlite3.Connection) -> list[LoopStateRow]:
    return [
        LoopStateRow(*row)
        for row in conn.execute(f"SELECT {','.join(_COLUMNS)} FROM loop_state ORDER BY loop")
    ]
