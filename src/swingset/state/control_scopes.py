"""Shared execution dependencies for admission and operator status."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .controls import ActionScope, matching_pauses
from .work import WorkUnit


def _finding_kinds(conn: sqlite3.Connection, identifiers: set[str]) -> set[str]:
    result: set[str] = set()
    for identifier in identifiers:
        result.update(
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT kind FROM findings WHERE closed_at IS NULL "
                "AND (watch_id=? OR snapshot_id=? OR subject_id=?)",
                (identifier, identifier, identifier),
            )
        )
    return result


def for_watch(
    conn: sqlite3.Connection,
    *,
    source: str,
    watch_id: str | None = None,
    page_kind: str | None = None,
    watch_kind: str | None = None,
    host: str | None = None,
) -> ActionScope:
    if watch_id:
        row = conn.execute(
            "SELECT source,parser,kind FROM watches WHERE watch_id=?", (watch_id,)
        ).fetchone()
        if row:
            source, page_kind, watch_kind = str(row[0]), str(row[1]), str(row[2])
    kinds = _finding_kinds(conn, {watch_id} if watch_id else set())
    if source == "wsdc_registry":
        kinds.add("source_id_checked")
    elif watch_kind == "round" or (page_kind or "").endswith((".round", ".rounds")):
        kinds.add("round_observations")
    else:
        kinds.add("source_event_mapping")
    return ActionScope(
        sources=frozenset({source} if source else ()),
        kinds=frozenset(kinds),
        host=host,
        all_sources=not bool(source),
    )


def _event_sources(conn: sqlite3.Connection, event_id: str) -> set[str]:
    result = {
        str(row[0])
        for row in conn.execute(
            "SELECT source FROM source_event_map WHERE event_id=? UNION SELECT source FROM entries WHERE event_id=? "
            "UNION SELECT source FROM judges WHERE event_id=?",
            (event_id, event_id, event_id),
        )
    }
    row = conn.execute("SELECT sources,source FROM events WHERE event_id=?", (event_id,)).fetchone()
    if row:
        result.update(json.loads(row[0]))
        result.add(str(row[1]))
    return result


def _all_event_sources(conn: sqlite3.Connection) -> set[str]:
    result: set[str] = set()
    for source, sources in conn.execute("SELECT DISTINCT source,sources FROM events"):
        result.add(str(source))
        result.update(json.loads(sources))
    return result


def _map_sources(conn: sqlite3.Connection) -> set[str]:
    # The existing map unit can replace any source membership and rebuild its
    # event, then reconcile registry placements. Retired canonical sources also
    # remain dependencies until their supported output is removed atomically.
    return (
        {"wsdc_registry"}
        | _all_event_sources(conn)
        | {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT w.source FROM observations o JOIN watches w USING(watch_id) "
                "WHERE o.scope_kind IN ('calendar','source_index','event','source_event') "
                "UNION SELECT source FROM source_events UNION SELECT source FROM source_event_map "
                "UNION SELECT DISTINCT source FROM entries UNION SELECT DISTINCT source FROM judges"
            )
        }
    )


def for_unit(conn: sqlite3.Connection, unit: WorkUnit) -> ActionScope:
    kinds = {"work_attempt"} | _finding_kinds(conn, {unit.unit_id})
    sources: set[str] = set()
    if unit.stage == "parse":
        row = conn.execute(
            "SELECT w.watch_id,w.source,w.parser,w.kind FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=?",
            (unit.unit_id,),
        ).fetchone()
        if row:
            scope = for_watch(
                conn, source=row[1], watch_id=row[0], page_kind=row[2], watch_kind=row[3]
            )
            sources.update(scope.sources)
            kinds.update(scope.kinds)
        # Parse and admission form one atomic unit and can require local artifact
        # recovery. These dependencies cannot be split after admission begins.
        kinds.update({"archive_artifact", "admission_blocked", "parse_failure"})
    elif unit.stage == "link":
        sources.update(_event_sources(conn, unit.unit_id))
        sources.add("wsdc_registry")
        kinds.update({"first_point_reconsideration", "source_id_checked"})
    elif unit.unit_kind in {"map", "inventory", "history"}:
        sources.update(
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT w.source FROM observations o JOIN watches w USING(watch_id) WHERE o.scope_kind IN ('calendar','source_index') "
                "UNION SELECT source FROM source_events UNION SELECT source FROM source_event_map"
            )
        )
        if unit.unit_kind in {"inventory", "history"}:
            sources.add("wsdc_registry")
            sources.update(_all_event_sources(conn))
        else:
            sources.update(_map_sources(conn))
            kinds.add("round_observations")
        kinds.update({"source_event_mapping", "registry_event_association"})
    else:
        sources.update(
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT w.source FROM observations o JOIN watches w USING(watch_id) WHERE o.scope_kind=? AND o.scope_id=?",
                (unit.unit_kind, unit.unit_id),
            )
        )
        if unit.unit_kind == "event":
            sources.update(_event_sources(conn, unit.unit_id))
            kinds.add("round_observations")
        elif unit.unit_kind == "source_event":
            mapped = list(
                conn.execute(
                    "SELECT source,event_id FROM source_event_map WHERE source_ref=?",
                    (unit.unit_id,),
                )
            )
            for source, event_id in mapped:
                sources.add(str(source))
                sources.update(_event_sources(conn, str(event_id)))
            selected_source = conn.execute(
                "SELECT DISTINCT w.source FROM observations o JOIN watches w USING(watch_id) WHERE o.scope_kind='source_event' AND o.scope_id=? ORDER BY w.source LIMIT 1",
                (unit.unit_id,),
            ).fetchone()
            if not mapped or (
                selected_source and selected_source[0] not in {row[0] for row in mapped}
            ):
                # _project_source_event invokes the global map when no mapping
                # exists. That fallback cannot execute under a narrower name.
                sources.update(_map_sources(conn))
                kinds.add("registry_event_association")
            kinds.update({"round_observations", "source_event_mapping"})
        elif unit.unit_kind == "dancer":
            sources.add("wsdc_registry")
            sources.update(_all_event_sources(conn))
            kinds.update({"source_id_checked", "registry_event_association"})
        elif unit.unit_kind == "calendar":
            # Live calendar projection immediately re-associates registry rows.
            if unit.unit_id != "wsdc-history":
                sources.add("wsdc_registry")
                sources.update(_all_event_sources(conn))
                kinds.add("registry_event_association")
            kinds.add("source_event_mapping")
        else:
            kinds.add("source_event_mapping")
    sources.discard("")
    return ActionScope(sources=frozenset(sources), kinds=frozenset(kinds), all_sources=not sources)


def for_publication(conn: sqlite3.Connection, candidate: Path | None = None) -> ActionScope:
    # A release is one unsplittable snapshot. Suppression/closure checks continue
    # while its commit waits for any paused dependency.
    return ActionScope(all_sources=True, all_kinds=True)


def for_requirement(conn: sqlite3.Connection, row: Mapping[str, Any]) -> ActionScope:
    kind = str(row["kind"])
    evidence = json.loads(str(row.get("evidence_json") or "{}"))
    if kind == "work_attempt" and all(key in evidence for key in ("stage", "unit_kind", "unit_id")):
        return for_unit(
            conn, WorkUnit(evidence["stage"], evidence["unit_kind"], evidence["unit_id"])
        )
    source = row.get("source")
    sources = {str(source)} if source else set()
    watch_id = row.get("watch_id") or evidence.get("watch_id")
    if watch_id:
        scope = for_watch(conn, source=str(source or ""), watch_id=str(watch_id))
        sources.update(scope.sources)
    if kind == "archive_artifact":
        sources.update(
            str(item[0])
            for item in conn.execute(
                "SELECT DISTINCT w.source FROM snapshots s JOIN watches w USING(watch_id) WHERE s.body_sha256=?",
                (evidence.get("body_sha256", row.get("subject_id")),),
            )
        )
    return ActionScope(sources=frozenset(sources), kinds=frozenset({kind}), all_sources=not sources)


def unit_allowed(conn: sqlite3.Connection, unit: WorkUnit, *, now: datetime) -> bool:
    if conn.execute("SELECT 1 FROM operator_pauses LIMIT 1").fetchone() is None:
        return True
    return not matching_pauses(conn, for_unit(conn, unit), now=now)
