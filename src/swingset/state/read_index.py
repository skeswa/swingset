"""The reverse index of link read sets: `derivation_reads` (migration 35).

One row per read key of a unit's current generation. A read key names what
the unit read: `project/<kind>/<id>` (a generation), `content:<name>/<id>` (a
content digest) or `pool:<event>` (a pool version). `replace` writes a unit's
whole set at completion or cutoff; `dependents` answers which units read a
key. The table is not a dispatch proof input. This module holds no policy.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from swingset.state.work import WorkUnit


def replace(conn: sqlite3.Connection, unit: WorkUnit, keys: Iterable[str]) -> None:
    """Make `keys` the whole read set of `unit`, in the caller's transaction."""
    conn.execute(
        "DELETE FROM derivation_reads WHERE stage=? AND unit_kind=? AND unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
    )
    conn.executemany(
        "INSERT INTO derivation_reads(stage,unit_kind,unit_id,read_key) VALUES (?,?,?,?)",
        ((unit.stage, unit.unit_kind, unit.unit_id, key) for key in sorted(set(keys))),
    )


def keys(conn: sqlite3.Connection, unit: WorkUnit) -> frozenset[str]:
    """The recorded read set of `unit`; empty when it has none."""
    return frozenset(
        str(row[0])
        for row in conn.execute(
            "SELECT read_key FROM derivation_reads WHERE stage=? AND unit_kind=? AND unit_id=?",
            (unit.stage, unit.unit_kind, unit.unit_id),
        )
    )


def dependents(conn: sqlite3.Connection, read_key: str) -> list[WorkUnit]:
    """Every unit whose read set names `read_key`, in key order."""
    return [
        WorkUnit(str(stage), str(kind), str(unit_id))
        for stage, kind, unit_id in conn.execute(
            "SELECT stage,unit_kind,unit_id FROM derivation_reads WHERE read_key=? "
            "ORDER BY stage,unit_kind,unit_id",
            (read_key,),
        )
    ]


def count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM derivation_reads").fetchone()[0])
