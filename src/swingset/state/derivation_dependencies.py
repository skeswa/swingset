"""The acyclic, conservative dependency graph for retained derivation scopes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from functools import partial
from typing import Any

from swingset.model.schema import TABLES

from .derivation_query import memo
from .work import WorkUnit

BASE = frozenset({"calendar", "source_index", "dancer"})
PROJECT = BASE | {"inventory", "map", "event", "source_event", "history"}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def scopes(conn: sqlite3.Connection, stage: str) -> Iterator[WorkUnit]:
    yield from memo(conn, ("scopes", stage), lambda: tuple(_scopes(conn, stage)))


def _scopes(conn: sqlite3.Connection, stage: str) -> Iterator[WorkUnit]:
    units = {
        WorkUnit(*row)
        for row in conn.execute(
            "SELECT stage,unit_kind,unit_id FROM derivation_scopes WHERE stage=? "
            "UNION SELECT stage,unit_kind,unit_id FROM pending_work WHERE stage=?",
            (stage, stage),
        )
    }
    if stage == "project":
        physical = {
            WorkUnit(stage, str(kind), str(identifier))
            for kind, identifier in conn.execute(
                "SELECT DISTINCT scope_kind,scope_id FROM observations UNION SELECT DISTINCT scope_kind,scope_id FROM canonical_scope_rows"
            )
            if kind in PROJECT
        }
        units.update(physical)
        units.update(
            WorkUnit(stage, "source_index", str(row[0]))
            for row in conn.execute("SELECT DISTINCT scope_id FROM source_event_scope_rows")
        )
        units.update(
            WorkUnit(stage, "event", str(row[0]))
            for row in conn.execute(
                "SELECT event_id FROM events UNION SELECT event_id FROM source_event_map"
            )
        )
        if (
            physical
            or any(item.unit_kind in {"inventory", "map", "history"} for item in units)
            or conn.execute(
                "SELECT 1 FROM source_events UNION SELECT 1 FROM registry_placements UNION SELECT 1 FROM events LIMIT 1"
            ).fetchone()
        ):
            units.update(WorkUnit(stage, kind, "all") for kind in ("inventory", "map", "history"))
    elif stage == "link":
        units.update(
            WorkUnit(stage, "event", str(row[0]))
            for row in conn.execute(
                "SELECT event_id FROM events UNION SELECT event_id FROM entries UNION SELECT event_id FROM judges "
                "UNION SELECT substr(subject_id,1,instr(subject_id,'/')-1) FROM identity_links WHERE instr(subject_id,'/')>0"
            )
        )
        units.update(
            WorkUnit(stage, "event", str(row[0]))
            for row in conn.execute(
                "SELECT unit_id FROM derivation_scopes WHERE stage='project' AND unit_kind='event'"
            )
        )
    elif stage == "build":
        units.add(WorkUnit("build", "release", "all"))
    order = {
        "calendar": 0,
        "source_index": 0,
        "dancer": 0,
        "inventory": 1,
        "map": 2,
        "event": 3,
        "source_event": 4,
        "history": 5,
    }
    yield from sorted(
        units, key=lambda unit: (order.get(unit.unit_kind, 9), unit.unit_kind, unit.unit_id)
    )


def _project_subset(conn: sqlite3.Connection, kinds: frozenset[str]) -> tuple[WorkUnit, ...]:
    def selected() -> tuple[WorkUnit, ...]:
        if kinds == frozenset({"map"}):
            return _map_scopes(conn)
        if kinds == frozenset({"dancer"}):
            return _stored_project_kind(conn, "dancer")
        if kinds == frozenset({"history"}):
            history = set(_stored_project_kind(conn, "history"))
            # Every inferred shared scope has the same existence predicate.
            # Reuse the exact map predicate, including physical-only inputs.
            if _map_scopes(conn):
                history.add(WorkUnit("project", "history", "all"))
            return tuple(sorted(history, key=lambda item: item.unit_id))
        return tuple(item for item in scopes(conn, "project") if item.unit_kind in kinds)

    return memo(
        conn,
        ("project_scope_subset", kinds),
        selected,
    )


def dancer_prerequisites(conn: sqlite3.Connection) -> tuple[WorkUnit, ...]:
    """The exact shared dancer cohort used by every link prerequisite query."""
    return _project_subset(conn, frozenset({"dancer"}))


def _stored_project_kind(conn: sqlite3.Connection, kind: str) -> tuple[WorkUnit, ...]:
    """Exact registered, queued and physical subset; no inferred source-index rows."""
    return tuple(
        WorkUnit("project", kind, str(row[0]))
        for row in conn.execute(
            "SELECT unit_id FROM derivation_scopes WHERE stage='project' AND unit_kind=? "
            "UNION SELECT unit_id FROM pending_work WHERE stage='project' AND unit_kind=? "
            "UNION SELECT scope_id FROM observations WHERE scope_kind=? "
            "UNION SELECT scope_id FROM canonical_scope_rows WHERE scope_kind=? ORDER BY 1",
            (kind, kind, kind, kind),
        )
    )


def _project_event(conn: sqlite3.Connection, identifier: str) -> WorkUnit | None:
    """Check one event's catalog membership without loading every event or dancer."""
    present = conn.execute(
        "SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=? "
        "UNION ALL SELECT 1 FROM pending_work WHERE stage='project' AND unit_kind='event' AND unit_id=? "
        "UNION ALL SELECT 1 FROM observations WHERE scope_kind='event' AND scope_id=? "
        "UNION ALL SELECT 1 FROM canonical_scope_rows WHERE scope_kind='event' AND scope_id=? "
        "UNION ALL SELECT 1 FROM events WHERE event_id=? "
        "UNION ALL SELECT 1 FROM source_event_map WHERE event_id=? LIMIT 1",
        (identifier,) * 6,
    ).fetchone()
    return WorkUnit("project", "event", identifier) if present else None


def _map_scopes(conn: sqlite3.Connection) -> tuple[WorkUnit, ...]:
    """Read the exact map subset without enumerating unrelated output ownership.

    Event workers check this dependency repeatedly inside write transactions,
    where catalog memoization is deliberately unavailable. Preserve every
    registered, queued, physical, and inferred map scope from `_scopes`.
    """
    identifiers = {
        str(row[0])
        for row in conn.execute(
            "SELECT unit_id FROM derivation_scopes WHERE stage='project' AND unit_kind='map' "
            "UNION SELECT unit_id FROM pending_work WHERE stage='project' AND unit_kind='map' "
            "UNION SELECT scope_id FROM observations WHERE scope_kind='map' "
            "UNION SELECT scope_id FROM canonical_scope_rows WHERE scope_kind='map'"
        )
    }
    physical_kinds = ",".join("?" for _ in PROJECT)
    if (
        identifiers
        or conn.execute(
            "SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind IN ('inventory','map','history') "
            "UNION ALL SELECT 1 FROM pending_work WHERE stage='project' AND unit_kind IN ('inventory','map','history') "
            f"UNION ALL SELECT 1 FROM observations WHERE scope_kind IN ({physical_kinds}) "
            f"UNION ALL SELECT 1 FROM canonical_scope_rows WHERE scope_kind IN ({physical_kinds}) "
            "UNION ALL SELECT 1 FROM source_events "
            "UNION ALL SELECT 1 FROM registry_placements "
            "UNION ALL SELECT 1 FROM events LIMIT 1",
            (*PROJECT, *PROJECT),
        ).fetchone()
    ):
        identifiers.add("all")
    return tuple(WorkUnit("project", "map", identifier) for identifier in sorted(identifiers))


def prerequisites(conn: sqlite3.Connection, unit: WorkUnit) -> tuple[WorkUnit, ...]:
    if unit.stage == "project":
        if unit.unit_kind in BASE:
            return ()
        if unit.unit_kind == "inventory":
            return _project_subset(conn, BASE)
        if unit.unit_kind == "map":
            return (WorkUnit("project", "inventory", "all"),)
        if unit.unit_kind in {"event", "source_event"}:
            parents = _project_subset(conn, frozenset({"map"}))
            if unit.unit_kind == "source_event":
                return (
                    *parents,
                    *(
                        WorkUnit("project", "event", str(row[0]))
                        for row in conn.execute(
                            "SELECT DISTINCT event_id FROM source_event_map WHERE source_ref=? ORDER BY event_id",
                            (unit.unit_id,),
                        )
                    ),
                )
            return parents
        if unit.unit_kind == "history":
            return _project_subset(conn, BASE | {"inventory", "map", "event", "source_event"})
    if unit.stage == "link":
        event = memo(
            conn,
            ("project_event_scope", unit.unit_id),
            lambda: _project_event(conn, unit.unit_id),
        )
        # Preserve the catalog's dancer/event/history ordering while sharing
        # its broad registry and history subsets across every event candidate.
        return (
            *dancer_prerequisites(conn),
            *((event,) if event is not None else ()),
            *_project_subset(conn, frozenset({"history"})),
        )
    if unit.stage == "build" and unit.unit_kind == "release":
        return (*scopes(conn, "project"), *scopes(conn, "link"))
    return ()


def _raw(conn: sqlite3.Connection, unit: WorkUnit) -> list[dict[str, Any]]:
    scopes_to_read = [(unit.unit_kind, unit.unit_id)]
    if unit.unit_kind == "event":
        scopes_to_read.extend(
            ("source_event", str(row[0]))
            for row in conn.execute(
                "SELECT DISTINCT source_ref FROM source_event_map WHERE event_id=? ORDER BY source_ref",
                (unit.unit_id,),
            )
        )
    result = []
    watches: set[str] = set()
    for scope_kind, scope_id in scopes_to_read:
        for row in conn.execute(
            "SELECT o.*,s.body_sha256,s.observed_at,s.fetched_at,s.via,w.source AS watch_source,w.kind AS watch_kind,w.parser AS watch_parser FROM observations o JOIN snapshots s USING(snapshot_id) JOIN watches w USING(watch_id) WHERE o.scope_kind=? AND o.scope_id=? ORDER BY o.observation_id",
            (scope_kind, scope_id),
        ):
            watches.add(str(row["watch_id"]))
            result.append(
                {
                    "kind": "observation",
                    "key": row["observation_id"],
                    "snapshot_id": row["snapshot_id"],
                    "body_sha256": row["body_sha256"],
                    "payload_sha256": hashlib.sha256(row["payload_json"].encode()).hexdigest(),
                    "extract_version": row["extract_version"],
                    "parser_version": row["parser_version"],
                    "observed_at": row["observed_at"] or row["fetched_at"],
                    "via": row["via"],
                    "source": row["watch_source"],
                    "watch_kind": row["watch_kind"],
                    "watch_parser": row["watch_parser"],
                    "observation_kind": row["kind"],
                    "scope": [row["scope_kind"], row["scope_id"]],
                    "seq": row["seq"],
                }
            )
    for watch in sorted(watches):
        result.extend(
            {
                "kind": "source_selection",
                "key": row[0],
                "generation_id": row[1],
                "legacy_snapshot_id": row[2],
            }
            for row in conn.execute(
                "SELECT unit_key,accepted_generation_id,legacy_snapshot_id FROM source_units WHERE watch_id=? ORDER BY unit_key",
                (watch,),
            )
        )
    return result


def dependencies(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    selected: Callable[[sqlite3.Connection, WorkUnit], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, tuple[dict[str, Any], ...]]]:
    result: list[dict[str, Any]] = []
    sets: dict[str, tuple[dict[str, Any], ...]] = {}

    def group(name: str, members: list[dict[str, Any]]) -> None:
        ordered = tuple(sorted(members, key=_canonical))
        identifier = _digest(ordered)
        sets[identifier] = ordered
        result.append({"kind": "dependency_set", "key": name, "fingerprint": identifier})

    required = prerequisites(conn, unit)
    for stage, kind in sorted({(item.stage, item.unit_kind) for item in required}):
        members = tuple(item for item in required if (item.stage, item.unit_kind) == (stage, kind))
        # Base registry sets are shared by every event. Cache one exact ordered
        # manifest in this read snapshot, instead of re-reading it per event.
        shared = unit.unit_kind in {"inventory", "history", "release"} or (
            unit.stage == "link" and kind == "dancer"
        )
        key = (
            ("selected_members", stage, kind) if shared else ("selected_members", stage, kind, unit)
        )
        ordered, identifier = memo(conn, key, partial(_selected_members, conn, members, selected))
        sets[identifier] = ordered
        result.append(
            {
                "kind": "dependency_set",
                "key": "selected_" + stage + "_" + kind,
                "fingerprint": identifier,
            }
        )
    if unit.stage == "project":
        if unit.unit_kind == "source_index":
            group(
                "source_observations",
                memo(conn, "all_index_raw", lambda: _raw_kind(conn, "source_index")),
            )
        elif unit.unit_kind in BASE | {"event", "source_event"}:
            group("source_observations", _raw(conn, unit))
        if unit.unit_kind == "inventory":
            group("calendar_observations", _raw_kind(conn, "calendar"))
            group(
                "registry_inventory",
                _table_rows(conn, "registry_placements", excluded={"event_id"}),
            )
            group("source_inventory", _table_rows(conn, "source_events"))
            group(
                "source_snapshot_times",
                [
                    {"kind": "snapshot", "key": row[0], "observed_at": row[1]}
                    for row in conn.execute(
                        "SELECT snapshot_id,coalesce(observed_at,fetched_at) FROM snapshots WHERE snapshot_id IN (SELECT snapshot_id FROM source_events) ORDER BY snapshot_id"
                    )
                ],
            )
        if unit.unit_kind == "history":
            group("year_acceptance", _table_rows(conn, "history_acceptance"))
            group(
                "year_findings",
                [
                    {"kind": "finding", "key": row[0], "owner_kind": row[1], "owner_id": row[2]}
                    for row in conn.execute(
                        "SELECT finding_id,owner_kind,owner_id FROM findings WHERE owner_kind IN ('history_year','phase1_year') AND closed_at IS NULL ORDER BY finding_id"
                    )
                ],
            )
            group(
                "snapshot_transport",
                [
                    {"kind": "snapshot", "key": row[0], "via": row[1]}
                    for row in conn.execute(
                        "SELECT snapshot_id,via FROM snapshots WHERE snapshot_id IN (SELECT snapshot_id FROM events UNION SELECT snapshot_id FROM registry_placements UNION SELECT snapshot_id FROM contests UNION SELECT snapshot_id FROM entries UNION SELECT snapshot_id FROM rounds) ORDER BY snapshot_id"
                    )
                ],
            )
        if unit.unit_kind in {"event", "source_event"}:
            condition = "event_id=?" if unit.unit_kind == "event" else "source_ref=?"
            group(
                "source_mapping",
                [
                    {
                        "kind": "mapping",
                        "key": [row[0], row[1]],
                        "event_id": row[2],
                        "method": row[3],
                        "confidence": row[4],
                    }
                    for row in conn.execute(
                        "SELECT * FROM source_event_map WHERE "
                        + condition
                        + " ORDER BY source,source_ref",
                        (unit.unit_id,),
                    )
                ],
            )
    elif unit.stage == "link":
        group("source_observations", _raw(conn, WorkUnit("project", "event", unit.unit_id)))
        group(
            "reference_migrations",
            memo(
                conn,
                "reference_migrations",
                lambda: _table_rows(conn, "identity_reference_migrations"),
            ),
        )
        # Include the entire registry revision, not merely existing candidates.
        result.extend(
            {"kind": "revision", "key": row[0], "value": row[1]}
            for row in conn.execute(
                "SELECT name,value FROM revisions WHERE name IN ('dancers','identity_decisions') ORDER BY name"
            )
        )
        for table in ("events", "entries", "judges", "contests", "placements", "rounds"):
            ignored = set(TABLES[table].owner_columns) | {
                "first_seen_at",
                "last_seen_at",
                "run_id",
                "asserted_at",
            }
            where = (
                "event_id=?"
                if table != "rounds"
                else "contest_id IN (SELECT contest_id FROM contests WHERE event_id=?)"
            )
            group(
                "canonical_" + table,
                [
                    {
                        "kind": "canonical",
                        "key": [table, *(row[key] for key in TABLES[table].primary_key)],
                        "fingerprint": _digest(
                            {key: row[key] for key in row.keys() if key not in ignored}
                        ),
                    }
                    for row in conn.execute(
                        f"SELECT * FROM {table} WHERE {where} ORDER BY {','.join(TABLES[table].primary_key)}",
                        (unit.unit_id,),
                    )
                ],
            )
        group(
            "missing_identity",
            [
                {"kind": "finding", "key": row[0]}
                for row in conn.execute(
                    "SELECT DISTINCT f.finding_id FROM findings f JOIN rounds r ON r.round_id=f.subject_id "
                    "JOIN contests c ON c.contest_id=r.contest_id WHERE f.kind='missing_identity' "
                    "AND f.subject_kind='round' AND f.closed_at IS NULL AND r.round_type='prelim' "
                    "AND c.event_id=? ORDER BY f.finding_id",
                    (unit.unit_id,),
                )
            ],
        )
        result.extend(
            {"kind": "journal", "key": row[0], "value": row[1]}
            for row in conn.execute(
                "SELECT key,value FROM meta WHERE key='identity_journal_digest'"
            )
        )
    elif unit.stage == "build":
        if unit.unit_kind == "release":
            result.extend(
                {"kind": "revision", "key": row[0], "value": row[1]}
                for row in conn.execute("SELECT name,value FROM revisions ORDER BY name")
            )
        result.extend(
            {"kind": "input", "key": row[0], "value": row[1]}
            for row in conn.execute(
                "SELECT key,value FROM meta WHERE key IN ('input_bundle_hash','identity_journal_digest') ORDER BY key"
            )
        )
        from swingset.admission.support import selection_digest

        result.append({"kind": "admission", "key": "selection", "value": selection_digest(conn)})
    return result, sets


def _selected_members(
    conn: sqlite3.Connection,
    members: tuple[WorkUnit, ...],
    selected: Callable[[sqlite3.Connection, WorkUnit], dict[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], str]:
    ordered = tuple(sorted((selected(conn, item) for item in members), key=_canonical))
    return ordered, _digest(ordered)


def _raw_kind(conn: sqlite3.Connection, kind: str) -> list[dict[str, Any]]:
    return [
        item
        for row in conn.execute(
            "SELECT DISTINCT scope_id FROM observations WHERE scope_kind=? ORDER BY scope_id",
            (kind,),
        )
        for item in _raw(conn, WorkUnit("project", kind, str(row[0])))
    ]


def _table_rows(
    conn: sqlite3.Connection, table: str, excluded: set[str] | None = None
) -> list[dict[str, Any]]:
    ignored = excluded or set()
    columns = [
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})") if row[1] not in ignored
    ]
    return [
        {"kind": "input_row", "table": table, "row": dict(row)}
        for row in conn.execute(
            f"SELECT {','.join(columns)} FROM {table} ORDER BY {','.join(columns)}"
        )
    ]
