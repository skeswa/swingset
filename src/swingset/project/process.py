"""Transactional dispatch for durable projection work."""

import sqlite3
from collections.abc import Mapping
from datetime import UTC
from typing import Protocol

from swingset.clock import Clock
from swingset.state.db import Database
from swingset.state.work import WorkUnit

from .contests import project_event
from .events import project_calendar, project_source_index
from .map import project_map
from .registry import project_dancer
from .registry_events import reconcile_registry_events
from .writer import replace_scope

PROJECTOR_VERSION = 13


class InputBundleLike(Protocol):
    @property
    def files(self) -> Mapping[str, bytes]: ...


def process_unit(
    database: Database, unit: WorkUnit, bundle: InputBundleLike, clock: Clock, run_id: str
) -> bool:
    """Commit a unit's whole scope and its completion as one transaction."""
    now = clock.now().astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with database.transaction() as conn:
        pending = conn.execute(
            "SELECT 1 FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
            (unit.stage, unit.unit_kind, unit.unit_id),
        ).fetchone()
        if pending is None:
            return False
        changed = _dispatch(conn, unit, bundle, now, run_id)
        conn.execute(
            "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
            (unit.stage, unit.unit_kind, unit.unit_id),
        )
        return changed


def _dispatch(
    conn: sqlite3.Connection, unit: WorkUnit, bundle: InputBundleLike, now: str, run_id: str
) -> bool:
    if unit.unit_kind == "map":
        changed = False
        roots = list(
            conn.execute(
                "SELECT unit_kind,unit_id FROM pending_work WHERE stage='project' AND unit_kind IN ('calendar','source_index') ORDER BY unit_kind,unit_id"
            )
        )
        for kind, identifier in roots:
            if kind == "calendar":
                changed |= _replace_calendar(conn, str(identifier), now, run_id)
            else:
                changed |= project_source_index(conn, str(identifier), now, run_id)
            conn.execute(
                "DELETE FROM pending_work WHERE stage='project' AND unit_kind=? AND unit_id=?",
                (kind, identifier),
            )
        return project_map(conn, bundle, now, run_id, PROJECTOR_VERSION) or changed
    if unit.unit_kind == "calendar":
        return _replace_calendar(conn, unit.unit_id, now, run_id)
    if unit.unit_kind == "source_index":
        return project_source_index(conn, unit.unit_id, now, run_id)
    if unit.unit_kind == "event":
        return _replace_event(conn, unit.unit_id, now, run_id)
    if unit.unit_kind == "dancer":
        changed = replace_scope(
            conn,
            scope_kind="dancer",
            scope_id=unit.unit_id,
            projection=project_dancer(conn, unit.unit_id, now, run_id),
            run_id=run_id,
            projected_at=now,
        )
        return (
            reconcile_registry_events(
                conn, reconciled_at=now, run_id=run_id, wsdc_id=int(unit.unit_id)
            )
            or changed
        )
    if unit.unit_kind == "source_event":
        return _project_source_event(conn, unit.unit_id, bundle, now, run_id)
    raise LookupError(f"no projector for {unit.unit_kind}:{unit.unit_id}")


def _replace_calendar(conn: sqlite3.Connection, scope_id: str, now: str, run_id: str) -> bool:
    changed = replace_scope(
        conn,
        scope_kind="calendar",
        scope_id=scope_id,
        projection=project_calendar(conn, scope_id, now, run_id),
        run_id=run_id,
        projected_at=now,
        enqueue_links=False,
    )
    return reconcile_registry_events(conn, reconciled_at=now, run_id=run_id) or changed


def _replace_event(conn: sqlite3.Connection, event_id: str, now: str, run_id: str) -> bool:
    return replace_scope(
        conn,
        scope_kind="event",
        scope_id=event_id,
        projection=project_event(conn, event_id, now, run_id),
        run_id=run_id,
        projected_at=now,
    )


def _project_source_event(
    conn: sqlite3.Connection, source_ref: str, bundle: InputBundleLike, now: str, run_id: str
) -> bool:
    source_row = conn.execute(
        "SELECT DISTINCT w.source FROM observations o JOIN watches w USING(watch_id) WHERE o.scope_kind='source_event' AND o.scope_id=? ORDER BY w.source LIMIT 1",
        (source_ref,),
    ).fetchone()
    if source_row is None:
        return False
    key = str(source_row[0]), source_ref
    mapped = conn.execute(
        "SELECT event_id FROM source_event_map WHERE source=? AND source_ref=?", key
    ).fetchone()
    changed = False
    if mapped is None:
        changed = project_map(conn, bundle, now, run_id, PROJECTOR_VERSION)
        mapped = conn.execute(
            "SELECT event_id FROM source_event_map WHERE source=? AND source_ref=?", key
        ).fetchone()
    return (
        _replace_event(conn, str(mapped[0]), now, run_id) if mapped is not None else False
    ) or changed
