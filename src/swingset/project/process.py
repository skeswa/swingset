"""Transactional dispatch for durable projection work."""

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC
from typing import TYPE_CHECKING, Protocol

from swingset.clock import Clock
from swingset.model.history import HISTORY_START
from swingset.state.db import Database
from swingset.state.read_sets import guarding, recording
from swingset.state.work import WorkUnit, enqueue

from .contests import project_event
from .events import project_calendar, project_source_index
from .map import propose, resolve
from .materialization import continuity_rows, helper_recipe, materializing
from .registry import project_dancer
from .registry_events import reconcile_registry_events
from .writer import replace_scope

if TYPE_CHECKING:
    from swingset.state.derivations import Selection

PROJECTOR_VERSION = 20


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
    """Commit a unit's whole scope and its completion as one transaction.

    The projector body runs inside `recording`, which notes the canonical
    tables it reads (`state/read_sets`). A completed unit queues hints for the
    year units its rows touch (`derivation_dependencies.year_hints`) and for
    the links whose registry placements or event row it changed; currency
    decides whether they run. A retired unit (`project/history/all`, `project/map/all`) never
    runs: its hint is dropped.
    """
    now = clock.now().astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with database.transaction() as conn, guarding(conn):
        from swingset.state.derivation_dependencies import hint_state, retired, year_hints
        from swingset.state.derivations import available

        derived = available(conn)
        if derived and retired(unit):
            conn.execute(
                "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                (unit.stage, unit.unit_kind, unit.unit_id),
            )
            return False
        if (
            not derived
            and conn.execute(
                "SELECT 1 FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                (unit.stage, unit.unit_kind, unit.unit_id),
            ).fetchone()
            is None
        ):
            return False
        if unit.unit_kind == "calendar" and selection is None and derived:
            from swingset.state.derivations import current_for

            if current_for(conn, unit, recipe=helper_recipe(conn, bundle.files, "project")):
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
        prepared = unit.unit_kind == "source_event" and selection is None and derived
        touched = year_hints(conn, unit) if derived else ()
        hinted = hint_state(conn, unit) if derived else frozenset()
        prepared_changed = False
        if prepared:
            with recording(conn, unit):
                prepared_changed = _dispatch(conn, unit, bundle, now, run_id)
        with materializing(
            conn,
            unit,
            now=now,
            run_id=run_id,
            selection=selection,
            recipe=helper_recipe(conn, bundle.files, "project"),
        ) as captured:
            if prepared:
                changed = prepared_changed
            else:
                with recording(conn, unit):
                    changed = _dispatch(conn, unit, bundle, now, run_id, selection=captured)
            if derived:
                enqueue(conn, sorted({*touched, *year_hints(conn, unit)}), enqueued_at=now)
                _hint_links(conn, unit, hinted, now)
            elif unit.unit_kind != "history":
                enqueue(conn, (WorkUnit("project", "history", "all"),), enqueued_at=now)
            conn.execute(
                "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                (unit.stage, unit.unit_kind, unit.unit_id),
            )
        return changed


def _hint_links(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    before: "frozenset[tuple[str | None, str]]",
    now: str,
) -> None:
    """Queue the links whose registry placements or event row this unit changed.

    Their content reads moved (plan F20, `derivation_dependencies.hint_state`).
    A pointer that moved queues its readers at completion instead.
    """
    from swingset.state.derivation_dependencies import hint_state, hinted_events

    events = hinted_events(before, hint_state(conn, unit))
    conn.executemany(
        "INSERT OR IGNORE INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
        "VALUES ('link','event',?,?)",
        ((event, now) for event in sorted(events)),
    )


def _dispatch(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    bundle: InputBundleLike,
    now: str,
    run_id: str,
    *,
    selection: "Selection | None" = None,
) -> bool:
    if unit.unit_kind == "year":
        from .history import finalize_year

        config = getattr(bundle, "config", None)
        finalize_year(
            conn,
            int(unit.unit_id),
            now=now,
            history_start=getattr(config, "history_start", HISTORY_START),
        )
        return False
    if unit.unit_kind in {"inventory", "history"}:
        from .history import prepare_inventory, reconcile_history

        config = getattr(bundle, "config", None)
        history_start = getattr(config, "history_start", HISTORY_START)
        # history/all is retired with derivations (process_unit drops it);
        # only a database without them still reconciles it whole.
        prepare = prepare_inventory if unit.unit_kind == "inventory" else reconcile_history
        return prepare(
            conn,
            now=now,
            run_id=run_id,
            aliases=bundle.files.get("overrides/series_aliases.csv", b""),
            reviews=bundle.files.get("overrides/finding_reviews.csv", b""),
            history_start=history_start,
        )
    if unit.unit_kind == "mapping":
        return propose(conn, unit.unit_id, bundle, now, PROJECTOR_VERSION)
    if unit.unit_kind == "resolution":
        return resolve(conn, unit.unit_id, now, run_id)
    if unit.unit_kind == "calendar":
        return _replace_calendar(conn, unit.unit_id, now, run_id, selection=selection)
    if unit.unit_kind == "source_index":
        return project_source_index(conn, unit.unit_id, now, run_id)
    if unit.unit_kind == "event":
        return _replace_event(conn, unit.unit_id, now, run_id)
    if unit.unit_kind == "dancer":
        return _replace_dancer(conn, unit.unit_id, bundle, now, run_id)
    if unit.unit_kind == "source_event":
        return _project_source_event(conn, unit.unit_id, bundle, now, run_id)
    raise LookupError(f"no projector for {unit.unit_kind}:{unit.unit_id}")


def _replace_dancer(
    conn: sqlite3.Connection, scope_id: str, bundle: InputBundleLike, now: str, run_id: str
) -> bool:
    """Project one registry dancer and probe the link pools its change reaches.

    With derivations, links learn of a dancer through their read sets (its
    readers are queued when its pointer moves) and through the pool probe,
    so the write does not queue every link (plan F20).
    """
    from swingset.link import pool
    from swingset.state.derivations import available

    derived = available(conn)
    owned = _dancer_ids(conn, scope_id) if derived else set()
    before = {number: pool.signature(conn, number) for number in owned}
    changed = replace_scope(
        conn,
        scope_kind="dancer",
        scope_id=scope_id,
        projection=project_dancer(conn, scope_id, now, run_id),
        run_id=run_id,
        projected_at=now,
        enqueue_links=not derived,
    )
    reconciled = reconcile_registry_events(
        conn, reconciled_at=now, run_id=run_id, wsdc_id=int(scope_id)
    )
    if derived:
        names = pool.nicknames(bundle.files)
        for number in sorted(owned | _dancer_ids(conn, scope_id)):
            pool.probe(
                conn,
                number,
                before.get(number),
                pool.signature(conn, number),
                nicknames=names,
                now=now,
            )
    return reconciled or changed


def _dancer_ids(conn: sqlite3.Connection, scope_id: str) -> set[int]:
    """The registry ids whose rows the dancer scope owns, and its own."""
    ids = {int(scope_id)} if scope_id.isdigit() else set()
    ids.update(
        int(json.loads(str(row[0]))[0])
        for row in conn.execute(
            "SELECT record_key FROM canonical_scope_rows WHERE scope_kind='dancer' "
            "AND scope_id=? AND table_name='dancers'",
            (scope_id,),
        )
    )
    return ids


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
    # An unmapped listing waits for its mapping and resolution (plan F19):
    # its `source_mapping` group changes when they map it.
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
