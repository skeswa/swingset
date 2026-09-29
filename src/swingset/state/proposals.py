"""Mapping proposals: `source_event_proposals` (migration 35).

A `project/mapping/<source>` unit proposes an event for each listing of its
source: the matched catalog event or a generated id, how it matched, and the
placeholder event the listing would create when nothing matches
(`placeholder_json`). `replace_source` writes a source's whole set;
`for_event` gives the proposals a `project/resolution/<event_id>` unit reads.
This module holds no policy.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import astuple, dataclass, fields


@dataclass(frozen=True)
class Proposal:
    source: str
    source_ref: str
    event_id: str
    match_method: str
    match_confidence: float
    placeholder_json: str | None = None


_COLUMNS = tuple(field.name for field in fields(Proposal))
_SELECT = f"SELECT {','.join(_COLUMNS)} FROM source_event_proposals"


def replace_source(conn: sqlite3.Connection, source: str, rows: Iterable[Proposal]) -> None:
    """Make `rows` the whole proposal set of `source`, in the caller's transaction."""
    rows = list(rows)
    foreign = sorted({row.source for row in rows if row.source != source})
    if foreign:
        raise ValueError(f"proposals for {source} name other sources: {foreign}")
    conn.execute("DELETE FROM source_event_proposals WHERE source=?", (source,))
    conn.executemany(
        f"INSERT INTO source_event_proposals({','.join(_COLUMNS)}) "
        f"VALUES ({','.join('?' * len(_COLUMNS))})",
        (astuple(row) for row in rows),
    )


def for_source(conn: sqlite3.Connection, source: str) -> list[Proposal]:
    return [
        Proposal(*row)
        for row in conn.execute(f"{_SELECT} WHERE source=? ORDER BY source_ref", (source,))
    ]


def for_event(conn: sqlite3.Connection, event_id: str) -> list[Proposal]:
    """The listings proposed to `event_id`, in (source, source_ref) order."""
    return [
        Proposal(*row)
        for row in conn.execute(
            f"{_SELECT} WHERE event_id=? ORDER BY source,source_ref", (event_id,)
        )
    ]


def event_ids(conn: sqlite3.Connection) -> list[str]:
    """Every proposed event id, sorted."""
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT event_id FROM source_event_proposals ORDER BY event_id"
        )
    ]
