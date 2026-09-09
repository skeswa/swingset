"""Durable, coalescing work and atomic input acceptance."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from .db import Database


@dataclass(frozen=True, slots=True, order=True)
class WorkUnit:
    stage: str
    unit_kind: str
    unit_id: str


def enqueue(conn: sqlite3.Connection, units: Iterable[WorkUnit], *, enqueued_at: str) -> None:
    conn.executemany(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES (?,?,?,?) ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET enqueued_at=excluded.enqueued_at",
        ((unit.stage, unit.unit_kind, unit.unit_id, enqueued_at) for unit in units),
    )


def accept_inputs(
    database: Database,
    consumer: str,
    inputs: Mapping[str, str],
    affected: Callable[[str, str | None, str, sqlite3.Connection], Iterable[WorkUnit]],
    *,
    accepted_at: str | None = None,
) -> set[str]:
    """Accept changed digests and their complete invalidation set atomically."""
    timestamp = accepted_at or _now()
    changed: set[str] = set()
    with database.transaction() as conn:
        for name, digest in inputs.items():
            row = conn.execute(
                "SELECT digest FROM accepted_inputs WHERE consumer=? AND input_name=?",
                (consumer, name),
            ).fetchone()
            old = None if row is None else str(row[0])
            if old == digest:
                continue
            enqueue(conn, affected(name, old, digest, conn), enqueued_at=timestamp)
            conn.execute(
                "INSERT INTO accepted_inputs(consumer,input_name,digest) VALUES (?,?,?) ON CONFLICT(consumer,input_name) DO UPDATE SET digest=excluded.digest",
                (consumer, name, digest),
            )
            changed.add(name)
    return changed


def accept_input(
    database: Database,
    consumer: str,
    input_name: str,
    digest: str,
    units: Iterable[WorkUnit],
    *,
    accepted_at: str | None = None,
) -> bool:
    materialized = tuple(units)
    changed = accept_inputs(
        database,
        consumer,
        {input_name: digest},
        lambda _name, _old, _new, _conn: materialized,
        accepted_at=accepted_at,
    )
    return input_name in changed


def next_work(conn: sqlite3.Connection, stage: str) -> WorkUnit | None:
    order = (
        "CASE unit_kind WHEN 'map' THEN 0 ELSE 1 END,enqueued_at,unit_kind,unit_id"
        if stage == "project"
        else "enqueued_at,unit_kind,unit_id"
    )
    row = conn.execute(
        f"SELECT stage,unit_kind,unit_id FROM pending_work WHERE stage=? ORDER BY {order} LIMIT 1",
        (stage,),
    ).fetchone()
    return None if row is None else WorkUnit(str(row[0]), str(row[1]), str(row[2]))


def complete(
    database: Database, unit: WorkUnit, write: Callable[[sqlite3.Connection], None]
) -> None:
    """Commit a unit's output, downstream work, and completion together."""
    with database.transaction() as conn:
        exists = conn.execute(
            "SELECT 1 FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
            (unit.stage, unit.unit_kind, unit.unit_id),
        ).fetchone()
        if exists is None:
            return
        write(conn)
        conn.execute(
            "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
            (unit.stage, unit.unit_kind, unit.unit_id),
        )


def bump_revision(conn: sqlite3.Connection, name: str) -> None:
    cursor = conn.execute("UPDATE revisions SET value=value+1 WHERE name=?", (name,))
    if cursor.rowcount != 1:
        raise ValueError(f"unknown revision: {name}")


def affected_work(conn: sqlite3.Connection, input_name: str) -> Iterable[WorkUnit]:
    """The centralized invalidation map from the state contract."""
    if input_name.startswith(("extract_version:", "version/extract/")):
        kind = input_name.rsplit(":" if ":" in input_name else "/", 1)[1]
        conn.execute(
            "UPDATE watches SET extract_version=NULL WHERE parser=? OR kind=?", (kind, kind)
        )
        rows = conn.execute(
            "SELECT snapshot_id FROM snapshots WHERE watch_id IN (SELECT watch_id FROM watches WHERE parser=? OR kind=?)",
            (kind, kind),
        )
        return tuple(WorkUnit("parse", "snapshot", str(row[0])) for row in rows)
    if input_name.startswith(("parser_version:", "version/parser/")):
        kind = input_name.rsplit(":" if ":" in input_name else "/", 1)[1]
        rows = conn.execute(
            "SELECT snapshot_id FROM snapshots WHERE watch_id IN (SELECT watch_id FROM watches WHERE parser=? OR kind=?)",
            (kind, kind),
        )
        return tuple(WorkUnit("parse", "snapshot", str(row[0])) for row in rows)
    if input_name in {
        "projector_version",
        "projection_vocabulary",
        "event_vocabulary",
        "version/projector",
    }:
        units = {
            WorkUnit("project", str(row[0]), str(row[1]))
            for row in conn.execute("SELECT DISTINCT scope_kind,scope_id FROM observations")
        }
        units.update(
            WorkUnit("project", str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT DISTINCT scope_kind,scope_id FROM canonical_scope_rows WHERE scope_kind IN ('calendar','event','dancer')"
            )
        )
        units.update(
            WorkUnit("project", "source_index", str(row[0]))
            for row in conn.execute("SELECT DISTINCT scope_id FROM source_event_scope_rows")
        )
        if (
            units
            or conn.execute(
                "SELECT 1 FROM source_events UNION SELECT 1 FROM source_event_map LIMIT 1"
            ).fetchone()
            is not None
        ):
            units.add(WorkUnit("project", "map", "all"))
        return tuple(sorted(units))
    if input_name in {
        "event_aliases",
        "source_urls",
        "overrides/event_aliases.csv",
        "overrides/source_urls.csv",
    }:
        has_map_inputs = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM observations WHERE scope_kind IN ('calendar','source_index')) OR EXISTS(SELECT 1 FROM source_events) OR EXISTS(SELECT 1 FROM source_event_map)"
        ).fetchone()
        if not bool(has_map_inputs[0]):
            return ()
        return (WorkUnit("project", "map", "all"),)
    if input_name in {
        "weights",
        "nicknames",
        "identity_overrides",
        "linker_version",
        "link/weights.toml",
        "overrides/nicknames.csv",
        "overrides/identity_overrides.csv",
        "version/linker",
    }:
        rows = conn.execute(
            "SELECT event_id FROM events UNION SELECT substr(subject_id,1,instr(subject_id,'/')-1) FROM identity_links WHERE instr(subject_id,'/')>0"
        )
        return tuple(WorkUnit("link", "event", str(row[0])) for row in rows)
    return ()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
