"""Transactional dispatch for durable projection work."""

import sqlite3
from collections.abc import Mapping
from datetime import UTC
from typing import TYPE_CHECKING, Protocol

from swingset.clock import Clock
from swingset.model.history import HISTORY_START
from swingset.state.db import Database
from swingset.state.work import WorkUnit, enqueue

from .contests import project_event
from .events import project_calendar, project_source_index
from .map import project_map
from .materialization import continuity_rows, helper_recipe, materializing
from .registry import project_dancer
from .registry_events import reconcile_registry_events
from .writer import replace_scope

if TYPE_CHECKING:
    from swingset.state.derivations import Selection

PROJECTOR_VERSION = 19


class InputBundleLike(Protocol):
    @property
    def files(self) -> Mapping[str, bytes]: ...


def process_unit(
    database: Database,
    unit: WorkUnit,
    bundle: InputBundleLike,
    clock: Clock,
    run_id: str,
    *,
    selection: "Selection | None" = None,
) -> bool:
    """Commit a unit's whole scope and its completion as one transaction."""
    now = clock.now().astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with database.transaction() as conn:
        from swingset.state.derivations import available

        if (
            not available(conn)
            and conn.execute(
                "SELECT 1 FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                (unit.stage, unit.unit_kind, unit.unit_id),
            ).fetchone()
            is None
        ):
            return False
        if unit.unit_kind == "calendar" and selection is None and available(conn):
            from swingset.state.derivations import desired, selected_generation

            proposed = desired(conn, unit, recipe=helper_recipe(conn, bundle.files, "project"))
            if proposed.generation_id == selected_generation(conn, unit):
                # Inventory may have enriched this selected calendar's rows.
                # Replaying a current base generation must not overwrite it.
                conn.execute(
                    "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                    (unit.stage, unit.unit_kind, unit.unit_id),
                )
                return False
        # Direct source-event dispatch retains its historical inline behavior.
        # Its proxy receipt must capture the event generation actually produced
        # here, rather than the previous event pointer. Scheduled callers already
        # require that dependency current and retain their external stale fence.
        prepared = unit.unit_kind == "source_event" and selection is None and available(conn)
        prepared_changed = _dispatch(conn, unit, bundle, now, run_id) if prepared else False
        with materializing(
            conn,
            unit,
            now=now,
            run_id=run_id,
            selection=selection,
            recipe=helper_recipe(conn, bundle.files, "project"),
        ) as captured:
            changed = (
                prepared_changed
                if prepared
                else _dispatch(conn, unit, bundle, now, run_id, selection=captured)
            )
            if unit.unit_kind != "history":
                enqueue(conn, (WorkUnit("project", "history", "all"),), enqueued_at=now)
            conn.execute(
                "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                (unit.stage, unit.unit_kind, unit.unit_id),
            )
        return changed


def _dispatch(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    bundle: InputBundleLike,
    now: str,
    run_id: str,
    *,
    selection: "Selection | None" = None,
) -> bool:
    if unit.unit_kind in {"inventory", "history"}:
        from swingset.state.derivations import available

        from .history import finalize_history, prepare_inventory, reconcile_history

        config = getattr(bundle, "config", None)
        history_start = getattr(config, "history_start", HISTORY_START)
        if unit.unit_kind == "history" and available(conn):
            finalize_history(conn, now=now, history_start=history_start)
            return False
        prepare = prepare_inventory if unit.unit_kind == "inventory" else reconcile_history
        return prepare(
            conn,
            now=now,
            run_id=run_id,
            aliases=bundle.files.get("overrides/series_aliases.csv", b""),
            history_start=history_start,
        )
    if unit.unit_kind == "map":
        changed = False
        from swingset.state.derivations import available, pending_units

        roots = (
            [
                (root.unit_kind, root.unit_id)
                for root in pending_units(conn, "project")
                if root.unit_kind in {"calendar", "source_index"}
            ]
            if available(conn)
            else list(
                conn.execute(
                    "SELECT unit_kind,unit_id FROM pending_work WHERE stage='project' AND unit_kind IN ('calendar','source_index') ORDER BY unit_kind,unit_id"
                )
            )
        )
        for kind, identifier in roots:
            root = WorkUnit("project", kind, identifier)
            with materializing(
                conn,
                root,
                now=now,
                run_id=run_id,
                recipe=helper_recipe(conn, bundle.files, "project"),
            ) as captured:
                if kind == "calendar":
                    changed |= _replace_calendar(
                        conn, str(identifier), now, run_id, selection=captured
                    )
                else:
                    changed |= project_source_index(conn, str(identifier), now, run_id)
                conn.execute(
                    "DELETE FROM pending_work WHERE stage='project' AND unit_kind=? AND unit_id=?",
                    (kind, identifier),
                )
        return project_map(conn, bundle, now, run_id, PROJECTOR_VERSION) or changed
    if unit.unit_kind == "calendar":
        return _replace_calendar(conn, unit.unit_id, now, run_id, selection=selection)
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


def _replace_calendar(
    conn: sqlite3.Connection,
    scope_id: str,
    now: str,
    run_id: str,
    *,
    selection: "Selection | None" = None,
) -> bool:
    if scope_id == "wsdc-history":
        # Historical listings can have month precision and attach to stable
        # registry occurrences through the coalesced history projection.
        return False
    changed = replace_scope(
        conn,
        scope_kind="calendar",
        scope_id=scope_id,
        projection=project_calendar(
            conn, scope_id, now, run_id, continuity=continuity_rows(conn, selection, "events")
        ),
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
    if mapped is not None:
        event_id = str(mapped[0])
        with materializing(
            conn,
            WorkUnit("project", "event", event_id),
            now=now,
            run_id=run_id,
            recipe=helper_recipe(conn, bundle.files, "project"),
        ):
            changed = _replace_event(conn, event_id, now, run_id) or changed
    return changed
