"""The acyclic, conservative dependency graph for retained derivation scopes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from functools import partial
from typing import Any

from .derivation_query import memo
from .work import WorkUnit

BASE = frozenset({"calendar", "source_index", "dancer"})
PROJECT = BASE | {"inventory", "mapping", "resolution", "event", "source_event", "year"}
#: Kinds no longer derived. Their scope rows and generations stay (retention
#: and old release closures read them), but `scopes()` never yields them, so
#: nothing schedules, registers or waits for them. `project/history/all`
#: became one unit per year (plan F17); `project/map/all` became one mapping
#: unit per source and one resolution per event (F19).
RETIRED_KINDS = frozenset({("project", "history"), ("project", "map")})
RETIRED = frozenset({WorkUnit("project", "history", "all"), WorkUnit("project", "map", "all")})
#: Scopes that owned placeholder events before the map split (`project/map`).
LEGACY_MAP_SCOPES = ("unmatched_source_events", "override_events")


def retired(unit: WorkUnit) -> bool:
    """Whether ``unit`` belongs to a retired kind, or is a year unit with no year."""
    return (unit.stage, unit.unit_kind) in RETIRED_KINDS or (
        (unit.stage, unit.unit_kind) == ("project", "year") and not _is_year(unit.unit_id)
    )


def _is_year(value: object) -> bool:
    return isinstance(value, str) and len(value) == 4 and value.isdigit()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def store_set(
    conn: sqlite3.Connection, set_id: str, manifest_json: str, *, created_at: str
) -> None:
    """Insert one dependency set once, stamped with when it was first written.

    `created_at` (schema 36) lets the collector leave a set alone for a day,
    so a set a run captured but has not yet named survives (D-0231). A set
    already there keeps its first stamp. An older schema has no column; the
    set is written without it.
    """
    try:
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json,"
            "created_at) VALUES (?,?,?)",
            (set_id, manifest_json, created_at),
        )
    except sqlite3.OperationalError as error:
        if "created_at" not in str(error):
            raise
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
            "VALUES (?,?)",
            (set_id, manifest_json),
        )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def scopes(conn: sqlite3.Connection, stage: str) -> Iterator[WorkUnit]:
    yield from memo(conn, ("scopes", stage), lambda: tuple(_scopes(conn, stage)))


def _scopes(conn: sqlite3.Connection, stage: str) -> Iterator[WorkUnit]:
    units = {
        unit
        for unit in (
            WorkUnit(*row)
            for row in conn.execute(
                "SELECT stage,unit_kind,unit_id FROM derivation_scopes WHERE stage=? "
                "UNION SELECT stage,unit_kind,unit_id FROM pending_work WHERE stage=?",
                (stage, stage),
            )
        )
        if not retired(unit)
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
        units.update(mapping_units(conn))
        units.update(resolution_units(conn))
        # Mappings and resolutions read the inventory, so it exists with them.
        if (
            physical
            or any(item.unit_kind in {"inventory", "mapping", "resolution"} for item in units)
            or conn.execute(
                "SELECT 1 FROM source_events UNION SELECT 1 FROM registry_placements UNION SELECT 1 FROM events LIMIT 1"
            ).fetchone()
        ):
            units.add(WorkUnit(stage, "inventory", "all"))
        units.update(WorkUnit(stage, "year", year) for year in _year_ids(conn))
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
        "mapping": 2,
        "resolution": 3,
        "event": 4,
        "source_event": 5,
        "year": 6,
    }
    yield from sorted(
        units, key=lambda unit: (order.get(unit.unit_kind, 9), unit.unit_kind, unit.unit_id)
    )


def _project_subset(conn: sqlite3.Connection, kinds: frozenset[str]) -> tuple[WorkUnit, ...]:
    def selected() -> tuple[WorkUnit, ...]:
        if kinds == frozenset({"mapping"}):
            return mapping_units(conn)
        return tuple(item for item in scopes(conn, "project") if item.unit_kind in kinds)

    return memo(
        conn,
        ("project_scope_subset", kinds),
        selected,
    )


def _year_ids(conn: sqlite3.Connection) -> list[str]:
    """Every year a unit exists for (plan F17).

    The years of events, by `year` and by reporting month; the reporting
    years of placements already associated with an event (a stale
    association is undone only by its year); years with coverage rows; and
    years already registered or queued, so a year whose last event left runs
    once more and clears its rows. A placement year with neither events nor
    associations has nothing to associate, so it needs no unit.
    """
    return sorted(
        str(row[0])
        for row in conn.execute(
            "SELECT CAST(year AS TEXT) FROM events WHERE year IS NOT NULL "
            "UNION SELECT substr(event_month,1,4) FROM events WHERE length(event_month)>=4 "
            "UNION SELECT substr(event_month,1,4) FROM registry_placements "
            "WHERE event_id IS NOT NULL "
            "UNION SELECT CAST(year AS TEXT) FROM coverage "
            "UNION SELECT unit_id FROM derivation_scopes WHERE stage='project' AND unit_kind='year' "
            "UNION SELECT unit_id FROM pending_work WHERE stage='project' AND unit_kind='year'"
        )
        if row[0] is not None and _is_year(str(row[0]))
    )


def year_units(conn: sqlite3.Connection) -> tuple[WorkUnit, ...]:
    """Every year unit, in year order."""
    return tuple(WorkUnit("project", "year", year) for year in _year_ids(conn))


def event_years(conn: sqlite3.Connection, event_id: str) -> tuple[str, ...]:
    """The years whose units own an event's facts: its `year` and its reporting month's.

    They differ for a December event reported in January: coverage counts it
    in its year, and the January year associates its placements.
    """
    row = conn.execute(
        "SELECT CAST(year AS TEXT),substr(event_month,1,4) FROM events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    if row is None:
        return ()
    return tuple(sorted({str(value) for value in row if _is_year(str(value))}))


def _year_events(conn: sqlite3.Connection, year: str) -> tuple[WorkUnit, ...]:
    """The event units a year reads: its events and those reported in its months."""
    return tuple(
        WorkUnit("project", "event", str(row[0]))
        for row in conn.execute(
            "SELECT event_id FROM events WHERE year=? OR substr(event_month,1,4)=? "
            "ORDER BY event_id",
            (int(year), year),
        )
    )


def year_hints(conn: sqlite3.Connection, unit: WorkUnit) -> tuple[WorkUnit, ...]:
    """The year units a project unit's rows touch; hints only, currency decides.

    An event (or a source event's events): the event's years. A dancer: the
    reporting years of its placements and the years of the events they name.
    A resolution: its event's years. The global kinds (calendar, inventory):
    every year; a mapping none (its resolutions hint). Called before and
    after the unit writes, so a row that moved years hints both.
    """
    if unit.stage != "project":
        return ()
    kind = unit.unit_kind
    if kind in {"calendar", "inventory"}:
        return year_units(conn)
    years: set[str] = set()
    if kind in {"event", "resolution"}:
        years.update(event_years(conn, unit.unit_id))
    elif kind == "source_event":
        for (event_id,) in conn.execute(
            "SELECT DISTINCT event_id FROM source_event_map WHERE source_ref=?", (unit.unit_id,)
        ):
            years.update(event_years(conn, str(event_id)))
    elif kind == "dancer" and unit.unit_id.isdigit():
        # A reporting year with no event reported in it has nothing to
        # associate; hinting it would make a unit of every registry year.
        years.update(
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT substr(p.event_month,1,4) FROM registry_placements p "
                "WHERE p.wsdc_id=? AND EXISTS (SELECT 1 FROM events e "
                "WHERE substr(e.event_month,1,4)=substr(p.event_month,1,4)) "
                "UNION SELECT DISTINCT CAST(e.year AS TEXT) FROM registry_placements p "
                "JOIN events e USING(event_id) WHERE p.wsdc_id=?",
                (int(unit.unit_id), int(unit.unit_id)),
            )
            if row[0] is not None
        )
    return tuple(WorkUnit("project", "year", year) for year in sorted(years) if _is_year(year))


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


_NO_PROPOSALS = (
    "(SELECT NULL AS source,NULL AS source_ref,NULL AS event_id,NULL AS match_method,"
    "NULL AS match_confidence,NULL AS placeholder_json WHERE 0)"
)


def _execute(
    conn: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] | dict[str, Any] = ()
) -> list[sqlite3.Row]:
    """Run a query over the proposals, which read as empty before schema 35.

    Old-schema tests and checkpoints run current code on a database without
    the table (migration 35 adds it).
    """
    try:
        return conn.execute(sql, parameters).fetchall()
    except sqlite3.OperationalError as error:
        if "source_event_proposals" not in str(error):
            raise
        return conn.execute(
            sql.replace("source_event_proposals", _NO_PROPOSALS), parameters
        ).fetchall()


def mapping_units(conn: sqlite3.Connection) -> tuple[WorkUnit, ...]:
    """One mapping unit per source: its listings, proposals, map rows and registrations.

    A source named only by an override file gets its unit from the input
    acceptance that queues it (`state/inputs.accept`).
    """
    return tuple(
        WorkUnit("project", "mapping", str(row[0]))
        for row in _execute(
            conn,
            "SELECT source FROM source_events UNION SELECT source FROM source_event_proposals "
            "UNION SELECT source FROM source_event_map "
            "UNION SELECT unit_id FROM derivation_scopes WHERE stage='project' AND unit_kind='mapping' "
            "UNION SELECT unit_id FROM pending_work WHERE stage='project' AND unit_kind='mapping' "
            "UNION SELECT scope_id FROM canonical_scope_rows WHERE scope_kind='mapping' "
            "UNION SELECT scope_id FROM observations WHERE scope_kind='mapping' "
            "ORDER BY 1",
        )
    )


def resolution_units(conn: sqlite3.Connection) -> tuple[WorkUnit, ...]:
    """One resolution unit per event a listing names, maps or once did.

    Proposed and mapped events, the events of resolution scope rows and of
    the retired map scopes (a first run takes those rows over), and every
    registered or queued resolution, so one whose proposals left runs once
    more and deletes its rows.
    """
    return tuple(
        WorkUnit("project", "resolution", str(row[0]))
        for row in _execute(
            conn,
            "SELECT event_id FROM source_event_proposals UNION SELECT event_id FROM source_event_map "
            "UNION SELECT scope_id FROM canonical_scope_rows WHERE scope_kind='resolution' "
            "UNION SELECT scope_id FROM observations WHERE scope_kind='resolution' "
            "UNION SELECT json_extract(record_key,'$[0]') FROM canonical_scope_rows "
            "WHERE scope_kind IN ('unmatched_source_events','override_events') AND scope_id='all' "
            "AND table_name='events' "
            "UNION SELECT unit_id FROM derivation_scopes WHERE stage='project' AND unit_kind='resolution' "
            "UNION SELECT unit_id FROM pending_work WHERE stage='project' AND unit_kind='resolution' "
            "ORDER BY 1",
        )
        if row[0] is not None
    )


def resolution_unit(conn: sqlite3.Connection, event_id: str) -> WorkUnit | None:
    """The event's resolution unit when it has one (the same sets as `resolution_units`)."""
    present = _execute(
        conn,
        "SELECT 1 FROM source_event_proposals WHERE event_id=:e "
        "UNION ALL SELECT 1 FROM source_event_map WHERE event_id=:e "
        "UNION ALL SELECT 1 FROM canonical_scope_rows WHERE scope_kind='resolution' AND scope_id=:e "
        "UNION ALL SELECT 1 FROM observations WHERE scope_kind='resolution' AND scope_id=:e "
        "UNION ALL SELECT 1 FROM canonical_scope_rows WHERE scope_kind IN "
        "('unmatched_source_events','override_events') AND scope_id='all' "
        "AND table_name='events' AND record_key=json_array(:e) "
        "UNION ALL SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='resolution' "
        "AND unit_id=:e "
        "UNION ALL SELECT 1 FROM pending_work WHERE stage='project' AND unit_kind='resolution' "
        "AND unit_id=:e LIMIT 1",
        {"e": event_id},
    )
    return WorkUnit("project", "resolution", event_id) if present else None


def resolution_proposals(conn: sqlite3.Connection, event_id: str) -> list[list[Any]]:
    """The proposals naming an event: a resolution's content input."""
    return [
        list(row)
        for row in _execute(
            conn,
            "SELECT source,source_ref,event_id,match_method,match_confidence,placeholder_json "
            "FROM source_event_proposals WHERE event_id=? ORDER BY source,source_ref",
            (event_id,),
        )
    ]


def waits_for(conn: sqlite3.Connection, unit: WorkUnit) -> tuple[WorkUnit, ...]:
    """Units that must be current before ``unit`` runs, beyond its prerequisites.

    A resolution waits for the mappings of the sources that propose its
    event or whose map rows name it, so it never deletes a row before that
    source has proposed again (the switch from the map, a listing that
    moved). They are not inputs: the proposals are, as content, and its own
    run deletes such rows.
    """
    if (unit.stage, unit.unit_kind) != ("project", "resolution"):
        return ()
    return tuple(
        WorkUnit("project", "mapping", str(row[0]))
        for row in _execute(
            conn,
            "SELECT source FROM source_event_proposals WHERE event_id=? "
            "UNION SELECT source FROM source_event_map WHERE event_id=? ORDER BY 1",
            (unit.unit_id, unit.unit_id),
        )
    )


def _index_scopes(conn: sqlite3.Connection, source: str) -> tuple[WorkUnit, ...]:
    """The source-index pages that list a source's events (index scopes are pages, F19)."""
    return tuple(
        WorkUnit("project", "source_index", str(row[0]))
        for row in conn.execute(
            "SELECT DISTINCT scope_id FROM source_event_scope_rows WHERE source=? ORDER BY 1",
            (source,),
        )
    )


def prerequisites(conn: sqlite3.Connection, unit: WorkUnit) -> tuple[WorkUnit, ...]:
    if unit.stage == "project":
        if unit.unit_kind in BASE:
            return ()
        if unit.unit_kind == "inventory":
            return _project_subset(conn, BASE)
        if unit.unit_kind == "mapping":
            # Its source's index pages, and the catalog: every calendar and
            # the inventory. Its listings are a content group too.
            return (
                *_project_subset(conn, frozenset({"calendar"})),
                *_index_scopes(conn, unit.unit_id),
                WorkUnit("project", "inventory", "all"),
            )
        if unit.unit_kind == "resolution":
            # The rows it may keep: every calendar and the inventory. The
            # proposals naming its event are a content group, so a mapping
            # that re-mints with other listings' changes leaves it current;
            # it waits for the mappings all the same (`waits_for`).
            return (
                *_project_subset(conn, frozenset({"calendar"})),
                WorkUnit("project", "inventory", "all"),
            )
        if unit.unit_kind == "event":
            resolution = resolution_unit(conn, unit.unit_id)
            return (resolution,) if resolution is not None else ()
        if unit.unit_kind == "source_event":
            # The resolutions and events of the events its listing maps to.
            # Unmapped, it has none and waits for its mapping (F19).
            events = [
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT event_id FROM source_event_map WHERE source_ref=? ORDER BY event_id",
                    (unit.unit_id,),
                )
            ]
            return (
                *(
                    resolution
                    for event in events
                    if (resolution := resolution_unit(conn, event)) is not None
                ),
                *(WorkUnit("project", "event", event) for event in events),
            )
        if unit.unit_kind == "year":
            # The inventory, every calendar, and the events of the year or
            # reported in its months with their resolutions (F17, F19).
            # Placements, acceptance, findings and transport are content
            # groups; dancers reach it through their replay epoch.
            members = _year_events(conn, unit.unit_id)
            return (
                *_project_subset(conn, frozenset({"calendar"})),
                WorkUnit("project", "inventory", "all"),
                *(
                    resolution
                    for member in members
                    if (resolution := resolution_unit(conn, member.unit_id)) is not None
                ),
                *members,
            )
    if unit.stage == "link":
        event = memo(
            conn,
            ("project_event_scope", unit.unit_id),
            lambda: _project_event(conn, unit.unit_id),
        )
        # The dancers its read set names that have a unit, its resolution and
        # its event, in catalog order (plan F20): never the whole registry.
        # A printed id the registry lacks has no unit, so it is no
        # prerequisite; its key reads the row's absence, and the dancer's
        # first pointer queues the link (F27). No year: the link reads its
        # registry association as content (`content:registry_event`), and a
        # year that moves it queues the link (`hint_state`), so a year held
        # behind the inventory never holds a link.
        resolution = resolution_unit(conn, unit.unit_id)
        return (
            *dancer_units(
                conn,
                [
                    key.removeprefix(DANCER)
                    for key in sorted(recorded_reads(conn, unit))
                    if key.startswith(DANCER)
                ],
            ),
            *((resolution,) if resolution is not None else ()),
            *((event,) if event is not None else ()),
        )
    if unit.stage == "build" and unit.unit_kind == "release":
        return (*scopes(conn, "project"), *scopes(conn, "link"))
    return ()


def dancer_units(conn: sqlite3.Connection, identifiers: Iterable[str]) -> tuple[WorkUnit, ...]:
    """The dancer units of ``identifiers`` that exist: registered, queued or observed.

    An id with none of these (a WSDC id a sheet prints that the registry
    lacks) has no unit that could ever become current (F27).
    """
    wanted = list(dict.fromkeys(str(value) for value in identifiers))
    if not wanted:
        return ()
    found = {
        str(row[0])
        for row in conn.execute(
            "SELECT unit_id FROM derivation_scopes WHERE stage='project' AND unit_kind='dancer' "
            "AND unit_id IN (SELECT value FROM json_each(:ids)) "
            "UNION SELECT unit_id FROM pending_work WHERE stage='project' AND unit_kind='dancer' "
            "AND unit_id IN (SELECT value FROM json_each(:ids)) "
            "UNION SELECT scope_id FROM observations WHERE scope_kind='dancer' "
            "AND scope_id IN (SELECT value FROM json_each(:ids))",
            {"ids": json.dumps(wanted)},
        )
    }
    return tuple(WorkUnit("project", "dancer", value) for value in wanted if value in found)


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


# The prerequisite kinds whose replay epoch a unit's certificate reads
# whether or not that kind has any scope yet (derivation_signatures.certificate
# reads them by epoch). A source_event's events depend on its mapping, so both
# sides read them only when mapped.
_REPLAYED: dict[tuple[str, str], frozenset[str]] = {
    ("project", "inventory"): BASE,
    ("project", "mapping"): frozenset({"calendar", "inventory", "source_index"}),
    ("project", "resolution"): frozenset({"calendar", "inventory"}),
    ("project", "event"): frozenset({"resolution"}),
    ("project", "source_event"): frozenset({"resolution"}),
    # A dancer's reconcile can move `event_id` under a cutoff (F23): the year
    # learns of it through the dancer epoch, not through its own content.
    ("project", "year"): frozenset({"calendar", "dancer", "inventory", "resolution", "event"}),
    # A link reads no per-kind epoch: its event entry carries that event's own
    # replay version, and its other reads are generations and content (F20).
}


def replay_dependents(stage: str, kind: str) -> tuple[tuple[str, str], ...]:
    """The (stage, kind) pairs whose fingerprints read this kind's replay epoch."""
    if stage != "project":
        return ()
    return tuple(sorted(key for key, kinds in _REPLAYED.items() if kind in kinds))


def replayed_kinds(unit: WorkUnit) -> set[tuple[str, str]]:
    """Replay epochs the certificate reads even when a kind has no scopes.

    Without these, an empty cohort's epoch moved the certificate but not the
    fingerprint: the fast path missed forever while the slow path said current.
    """
    return {("project", kind) for kind in _REPLAYED.get((unit.stage, unit.unit_kind), ())}


def dependencies(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    selected: Callable[[sqlite3.Connection, WorkUnit], dict[str, Any]],
    *,
    reads: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, tuple[dict[str, Any], ...]]]:
    """A unit's dependency entries and the groups they name.

    A link's come from its read set (``reads``, else its recorded one):
    one entry per read key, and its source observations (plan F20).
    """
    if unit.stage == "link":
        return _link_dependencies(conn, unit, reads)
    result: list[dict[str, Any]] = []
    sets: dict[str, tuple[dict[str, Any], ...]] = {}

    def group(name: str, members: list[dict[str, Any]]) -> None:
        ordered = tuple(sorted(members, key=_canonical))
        identifier = _digest(ordered)
        sets[identifier] = ordered
        result.append({"kind": "dependency_set", "key": name, "fingerprint": identifier})

    required = prerequisites(conn, unit)
    replayed = replay_versions(conn)
    present = {(item.stage, item.unit_kind) for item in required}
    for stage, kind in sorted(present | replayed_kinds(unit)):
        # A direct entry, never a selected_* member: group fingerprints stay
        # generation ids only, and a cutoff that changed live rows still
        # reaches every dependent through this per-kind epoch.
        result.append(
            {"kind": "replay", "key": [stage, kind], "version": replayed.get((stage, kind), 0)}
        )
        if (stage, kind) not in present:
            continue
        members = tuple(item for item in required if (item.stage, item.unit_kind) == (stage, kind))
        # Base registry sets are shared by every event. Cache one exact ordered
        # manifest in this read snapshot, instead of re-reading it per event.
        shared = (
            unit.unit_kind in {"inventory", "release"}
            or (unit.unit_kind == "year" and kind not in {"event", "resolution"})
            or unit.unit_kind == "resolution"
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
                _rows_digest(_table_rows(conn, "registry_placements", excluded={"event_id"})),
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
        if unit.unit_kind == "mapping":
            group(
                "source_listings",
                _table_rows(
                    conn,
                    "source_events",
                    excluded={"first_seen_at", "last_seen_at", "run_id"},
                    where="source=?",
                    parameters=(unit.unit_id,),
                ),
            )
        if unit.unit_kind == "resolution":
            group(
                "resolution_proposals",
                [
                    {"kind": "proposal", "key": row[:2], "row": row}
                    for row in resolution_proposals(conn, unit.unit_id)
                ],
            )
        if unit.unit_kind == "year":
            year = unit.unit_id
            group("year_placements", _rows_digest(_year_placements(conn, year)))
            group(
                "year_acceptance",
                _table_rows(conn, "history_acceptance", where="year=?", parameters=(int(year),)),
            )
            group(
                "year_findings",
                [
                    {"kind": "finding", "key": row[0], "owner_kind": row[1], "owner_id": row[2]}
                    for row in conn.execute(
                        "SELECT finding_id,owner_kind,owner_id FROM findings WHERE owner_kind IN ('history_year','phase1_year') AND owner_id=? AND closed_at IS NULL ORDER BY finding_id",
                        (year,),
                    )
                ],
            )
            group(
                "snapshot_transport",
                [
                    {"kind": "snapshot", "key": row[0], "via": row[1]}
                    for row in conn.execute(
                        "SELECT snapshot_id,via FROM snapshots WHERE snapshot_id IN ("
                        "SELECT snapshot_id FROM events WHERE year=:year "
                        "UNION SELECT snapshot_id FROM registry_placements WHERE substr(event_month,1,4)=:text "
                        f"UNION SELECT snapshot_id FROM registry_placements WHERE {_ELSEWHERE} "
                        f"UNION SELECT snapshot_id FROM contests WHERE event_id IN ({_EVENTS}) "
                        f"UNION SELECT snapshot_id FROM entries WHERE event_id IN ({_EVENTS}) "
                        "UNION SELECT r.snapshot_id FROM rounds r JOIN contests c USING(contest_id) "
                        f"WHERE c.event_id IN ({_EVENTS})) ORDER BY snapshot_id",
                        {"year": int(year), "text": year},
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


def replay_versions(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """Every per-kind replay epoch; a missing token is version 0."""
    versions = {}
    for key, version in conn.execute(
        "SELECT input_key,version FROM derivation_input_versions WHERE namespace='replayed'"
    ):
        stage, kind = json.loads(key)
        versions[(str(stage), str(kind))] = int(version)
    return versions


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


def _rows_digest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One member naming a large content group's rows by count and digest.

    A group's members are stored as a dependency set; storing the 198,000
    registry placement rows themselves cost about 93 MB each time one dancer
    changed and the inventory ran (13 year groups about 30 MB more). The digest
    moves with any row exactly as the rows would; nothing reads the rows back.
    """
    ordered = sorted(_canonical(item) for item in rows)
    return [
        {
            "kind": "rows_digest",
            "rows": len(ordered),
            "digest": hashlib.sha256("\n".join(ordered).encode()).hexdigest(),
        }
    ]


def _table_rows(
    conn: sqlite3.Connection,
    table: str,
    excluded: set[str] | frozenset[str] | None = None,
    *,
    where: str = "1",
    parameters: tuple[Any, ...] | dict[str, Any] = (),
) -> list[dict[str, Any]]:
    ignored = excluded or set()
    columns = [
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})") if row[1] not in ignored
    ]
    return [
        {"kind": "input_row", "table": table, "row": dict(row)}
        for row in conn.execute(
            f"SELECT {','.join(columns)} FROM {table} WHERE {where} ORDER BY {','.join(columns)}",
            parameters,
        )
    ]


# A year's events, and the placements reported in another year that name one
# of them (a December event's January placements), as SQL over `:year`
# (the integer) and `:text` (the four digits).
_EVENTS = "SELECT event_id FROM events WHERE year=:year"
_ELSEWHERE = f"substr(event_month,1,4)!=:text AND event_id IN ({_EVENTS})"


def _associated_elsewhere(conn: sqlite3.Connection, year: str) -> list[list[Any]]:
    """Keys and associations of placements reported in another year that name the year's events."""
    return [
        list(row)
        for row in conn.execute(
            "SELECT wsdc_id,role,series_id,event_month,division,dance_style,event_id "
            f"FROM registry_placements WHERE {_ELSEWHERE} "
            "ORDER BY wsdc_id,role,series_id,event_month,division,dance_style",
            {"year": int(year), "text": year},
        )
    ]


def _year_placements(conn: sqlite3.Connection, year: str) -> list[dict[str, Any]]:
    """The placement rows a year reads, without the column it writes (F23).

    Placements reported in the year leave out `event_id`, which the year
    writes. Placements reported in another year that name one of the year's
    events keep it: another year wrote it, and the year counts them.
    Membership never depends on a value the year itself writes.
    """
    named = {"year": int(year), "text": year}
    return [
        *_table_rows(
            conn,
            "registry_placements",
            excluded={"event_id"},
            where="substr(event_month,1,4)=:text",
            parameters=named,
        ),
        *_table_rows(conn, "registry_placements", where=_ELSEWHERE, parameters=named),
    ]


# ---------------------------------------------------------------------------
# Link read sets (plan F20)
# ---------------------------------------------------------------------------

EVENT = "project/event/"
DANCER = "project/dancer/"
CONTENT = "content:"
BINDING = "content:binding_ref/"
#: The dependency group of a link's `content:binding_ref` entries.
BINDING_GROUP = "binding_refs"
POOL = "pool:"
#: The read key families a link's read set holds (`read_sets.RULES` names them):
#: its event's generation and replay version, the dancers it used, content
#: digests of its other inputs, and its pool version.
READ_FAMILIES = frozenset(
    {
        "project/event",
        "project/dancer",
        "content:event_row",
        "content:registry_event",
        "content:missing_identity",
        "content:journal",
        "content:binding_ref",
        "content:table",
        "pool",
    }
)
#: Pool versions, one per event, that a dancer's pool probe bumps (`link/pool`).
POOL_NAMESPACE = "link_pool"
#: Replay versions of single event units. A link's event entry reads its own
#: event's, not the kind's epoch, so an event cutoff that rewrote live rows
#: re-queues that event's link only.
UNIT_REPLAY = "replayed_unit"
_CLOCKS = frozenset({"first_seen_at", "last_seen_at", "run_id", "asserted_at"})


def default_reads(event_id: str) -> frozenset[str]:
    """The read set of a link that never ran: every key but the dancers and
    source references a run names (its event, its row, its registry
    placements, its missing-identity findings, the journal, its pool)."""
    return frozenset(
        {
            EVENT + event_id,
            f"{CONTENT}event_row/{event_id}",
            f"{CONTENT}registry_event/{event_id}",
            f"{CONTENT}missing_identity/{event_id}",
            f"{CONTENT}journal",
            POOL + event_id,
        }
    )


def recorded_reads(conn: sqlite3.Connection, unit: WorkUnit) -> frozenset[str]:
    """The read set of a link's current generation, else its default.

    `derivation_reads` holds it (completion and cutoff replace it). Without
    rows there, as before schema 35, it is read back from the dependency
    manifest of the pointer's newest record: its newest equivalence, else the
    generation. A generation from before read sets names none, so its link
    takes the default and runs again.
    """

    def read() -> frozenset[str]:
        from . import read_index

        try:
            keys = read_index.keys(conn, unit)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            keys = frozenset()
        return keys or _manifest_reads(conn, unit) or default_reads(unit.unit_id)

    return memo(conn, ("link_reads", unit), read)


def _manifest_reads(conn: sqlite3.Connection, unit: WorkUnit) -> frozenset[str]:
    row = conn.execute(
        "SELECT s.materialized_generation_id,g.dependency_set_id FROM derivation_scopes s "
        "JOIN derivation_generations g ON g.generation_id=s.materialized_generation_id "
        "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
    ).fetchone()
    if row is None:
        return frozenset()
    set_id = str(row[1])
    try:
        newer = conn.execute(
            "SELECT dependency_set_id FROM derivation_equivalences WHERE stage=? AND unit_kind=? "
            "AND unit_id=? AND generation_id=? ORDER BY created_at DESC LIMIT 1",
            (unit.stage, unit.unit_kind, unit.unit_id, row[0]),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        newer = None
    if newer is not None:
        set_id = str(newer[0])
    manifest = conn.execute(
        "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?", (set_id,)
    ).fetchone()
    keys = set()
    items = json.loads(manifest[0]) if manifest else []
    for item in list(items):
        if item.get("kind") == "dependency_set" and item.get("key") == BINDING_GROUP:
            members = conn.execute(
                "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
                (item["fingerprint"],),
            ).fetchone()
            items.extend(json.loads(members[0]) if members else [])
    for item in items:
        kind = item.get("kind")
        if kind in {"derivation", "absent"} and item.get("key", [None])[0] == "project":
            keys.add("/".join(str(part) for part in item["key"]))
        elif kind == "content":
            keys.add(CONTENT + str(item["key"]))
        elif kind == "pool":
            keys.add(POOL + str(item["key"]))
    return frozenset(keys)


def unit_replay_version(conn: sqlite3.Connection, unit: WorkUnit) -> int:
    row = conn.execute(
        "SELECT version FROM derivation_input_versions WHERE namespace=? AND input_key=?",
        (UNIT_REPLAY, _canonical((unit.stage, unit.unit_kind, unit.unit_id))),
    ).fetchone()
    return int(row[0]) if row else 0


def link_inputs(conn: sqlite3.Connection, event_id: str, keys: Iterable[str]) -> dict[str, Any]:
    """The current value of each read key of `link/event/<event_id>`.

    A project key's value is the scope's selected generation (an event's
    with its own replay version), a content key's a digest of the rows it
    covers, and a pool key's the event's pool version. A dancer row that no
    dancer unit has materialized (written by other means) is read as a
    digest of its row instead. Values of the same keys at the link's load
    and at its commit must be equal.
    """
    wanted = sorted(set(keys))
    values: dict[str, Any] = {}
    dancers = [key.removeprefix(DANCER) for key in wanted if key.startswith(DANCER)]
    if dancers:
        found = {
            str(row[0]): row[1]
            for row in conn.execute(
                "SELECT unit_id,materialized_generation_id FROM derivation_scopes "
                "WHERE stage='project' AND unit_kind='dancer' "
                "AND unit_id IN (SELECT value FROM json_each(?))",
                (json.dumps(dancers),),
            )
        }
        values.update({DANCER + dancer: found.get(dancer) for dancer in dancers})
        unscoped = [dancer for dancer in dancers if found.get(dancer) is None]
        if unscoped:
            cursor = conn.execute(
                "SELECT * FROM dancers WHERE CAST(wsdc_id AS TEXT) IN "
                "(SELECT value FROM json_each(?))",
                (json.dumps(unscoped),),
            )
            names = [column[0] for column in cursor.description]
            rows = {
                str(row[0]): _digest(
                    {
                        name: value
                        for name, value in zip(names, row, strict=True)
                        if name not in _CLOCKS
                    }
                )
                for row in cursor
            }
            values.update({DANCER + dancer: ["row", rows.get(dancer)] for dancer in unscoped})
    refs = [key.removeprefix(BINDING) for key in wanted if key.startswith(BINDING)]
    if refs:
        values.update(_binding_refs(conn, event_id, refs))
    for key in wanted:
        if key not in values:
            values[key] = _read_value(conn, key)
    return values


def _read_value(conn: sqlite3.Connection, key: str) -> Any:
    from .derivations import selected_generation

    if key.startswith(EVENT):
        event = WorkUnit("project", "event", key.removeprefix(EVENT))
        return [selected_generation(conn, event), unit_replay_version(conn, event)]
    if key.startswith(POOL):
        row = conn.execute(
            "SELECT version FROM derivation_input_versions WHERE namespace=? AND input_key=?",
            (POOL_NAMESPACE, key.removeprefix(POOL)),
        ).fetchone()
        return int(row[0]) if row else 0
    name, _, argument = key.removeprefix(CONTENT).partition("/")
    if not key.startswith(CONTENT):
        raise ValueError(f"unknown read key {key}")
    if name == "event_row":
        # The row is a calendar's, the inventory's or a resolution's, not the
        # event unit's; its tier is the year's.
        cursor = conn.execute("SELECT * FROM events WHERE event_id=?", (argument,))
        row = cursor.fetchone()
        if row is None:
            return None
        names = [column[0] for column in cursor.description]
        return _digest(
            {
                column: value
                for column, value in zip(names, row, strict=True)
                if column not in _CLOCKS | {"coverage_tier"}
            }
        )
    if name == "registry_event":
        return _digest(
            _table_rows(
                conn, "registry_placements", _CLOCKS, where="event_id=?", parameters=(argument,)
            )
        )
    if name == "missing_identity":
        return [
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT f.finding_id FROM findings f JOIN rounds r ON r.round_id=f.subject_id "
                "JOIN contests c ON c.contest_id=r.contest_id WHERE f.kind='missing_identity' "
                "AND f.subject_kind='round' AND f.closed_at IS NULL AND r.round_type='prelim' "
                "AND c.event_id=? ORDER BY f.finding_id",
                (argument,),
            )
        ]
    if name == "journal":
        return memo(conn, "link_journal", lambda: _journal(conn))
    if name == "binding_ref":
        return _binding_refs(conn, None, [argument])[key]
    if name == "table" and argument.isidentifier():
        # A read no rule maps: the whole table (`read_set_widened`). Every
        # widened link's currency reads it, so one read snapshot digests it once.
        return memo(
            conn,
            ("content_table", argument),
            lambda: _digest(_table_rows(conn, argument, _CLOCKS)),
        )
    raise ValueError(f"unknown read key {key}")


def _journal(conn: sqlite3.Connection) -> list[Any]:
    """The accepted identity journal: its digest, its decision revision and migrations."""
    digest = conn.execute("SELECT value FROM meta WHERE key='identity_journal_digest'").fetchone()
    revision = conn.execute(
        "SELECT value FROM revisions WHERE name='identity_decisions'"
    ).fetchone()
    return [
        None if digest is None else str(digest[0]),
        None if revision is None else int(revision[0]),
        _digest(_table_rows(conn, "identity_reference_migrations")),
    ]


def _binding_refs(
    conn: sqlite3.Connection, event_id: str | None, refs: list[str]
) -> dict[str, str]:
    """Each source reference's bindings to subjects of other events.

    The link binds its own subjects, so their rows are its output. What it
    reads of another event's subject bound to the same reference is the
    binding, whether that subject still exists, and its resolution's reason
    (`DecisionResolver.binding_problem`).
    """
    grouped: dict[str, list[list[Any]]] = {ref: [] for ref in refs}
    for row in conn.execute(
        "SELECT b.ref_id,b.binding_id,b.subject_kind,b.subject_id,b.event_id,b.snapshot_id,"
        "b.semantic_hash,EXISTS(SELECT 1 FROM entries WHERE entry_id=b.subject_id) "
        "OR EXISTS(SELECT 1 FROM judges WHERE judge_id=b.subject_id),"
        "(SELECT r.reason FROM identity_link_resolutions r WHERE r.subject_kind=b.subject_kind "
        "AND r.subject_id=b.subject_id) FROM identity_reference_bindings b "
        "WHERE b.ref_id IN (SELECT value FROM json_each(?)) AND b.event_id IS NOT ? "
        "ORDER BY b.ref_id,b.binding_id",
        (json.dumps(refs), event_id),
    ):
        grouped[str(row[0])].append(list(row[1:]))
    return {BINDING + ref: _digest(rows) for ref, rows in grouped.items()}


def read_entries(key: str, value: Any) -> list[dict[str, Any]]:
    """The dependency entries of one read key and its value.

    A project scope without a generation is named `absent`, with the row
    digest read instead, not as a derivation, so no release closure or
    retention walk follows it.
    """
    if key.startswith("project/"):
        _, kind, identifier = key.split("/", 2)
        generation = value[0] if kind == "event" else value
        entries: list[dict[str, Any]] = [
            {
                "kind": "derivation",
                "key": ["project", kind, identifier],
                "generation_id": generation,
            }
            if isinstance(generation, str)
            else {"kind": "absent", "key": ["project", kind, identifier], "value": generation}
        ]
        if kind == "event":
            entries.append(
                {"kind": "replay", "key": ["project", kind, identifier], "version": value[1]}
            )
        return entries
    if key.startswith(POOL):
        return [{"kind": "pool", "key": key.removeprefix(POOL), "version": value}]
    return [{"kind": "content", "key": key.removeprefix(CONTENT), "digest": value}]


def _link_dependencies(
    conn: sqlite3.Connection, unit: WorkUnit, reads: Iterable[str] | None
) -> tuple[list[dict[str, Any]], dict[str, tuple[dict[str, Any], ...]]]:
    keys = frozenset(reads) if reads is not None else recorded_reads(conn, unit)
    values = link_inputs(conn, unit.unit_id, keys)
    result = [
        entry
        for key in sorted(keys)
        if not key.startswith(BINDING)
        for entry in read_entries(key, values[key])
    ]
    sets: dict[str, tuple[dict[str, Any], ...]] = {}

    def group(name: str, members: Iterable[dict[str, Any]]) -> None:
        ordered = tuple(sorted(members, key=_canonical))
        identifier = _digest(ordered)
        sets[identifier] = ordered
        result.append({"kind": "dependency_set", "key": name, "fingerprint": identifier})

    # One per subject, and they rarely change: a group is stored once and
    # shared by every generation that read the same bindings.
    group(
        BINDING_GROUP,
        (
            entry
            for key in keys
            if key.startswith(BINDING)
            for entry in read_entries(key, values[key])
        ),
    )
    # The event's source observations stay a group: the release closure
    # follows them to the snapshots its rows cite.
    group("source_observations", _raw(conn, WorkUnit("project", "event", unit.unit_id)))
    return result, sets


def read_hints(conn: sqlite3.Connection, key: str, *, enqueued_at: str) -> None:
    """Queue the units whose read set names `key` (a hint; currency decides).

    A unit already queued keeps its time. Before schema 35 there is no index.
    """
    try:
        conn.execute(
            "INSERT OR IGNORE INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
            "SELECT stage,unit_kind,unit_id,? FROM derivation_reads WHERE read_key=?",
            (enqueued_at, key),
        )
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise


def hint_state(conn: sqlite3.Connection, unit: WorkUnit) -> frozenset[tuple[str | None, str]]:
    """Rows a project unit may rewrite that links read as content, by event.

    A dancer rewrites its registry placements and their association; a year
    the association of placements reported in it; a calendar any
    association (its reconcile) and its events' rows; the inventory events'
    rows; a resolution its event's row. Compared before and after the unit,
    the events of rows that differ are the links whose `content:registry_event`
    or `content:event_row` moved (`hinted_events`). A pointer that moved
    queues its readers at completion instead.
    """
    if unit.stage != "project":
        return frozenset()
    kind, identifier = unit.unit_kind, unit.unit_id
    state: set[tuple[str | None, str]] = set()
    if kind == "dancer" and identifier.isdigit():
        # Its own rows, whole: points and results are content too.
        state.update(
            (item["row"]["event_id"], _canonical(item["row"]))
            for item in _table_rows(
                conn,
                "registry_placements",
                _CLOCKS,
                where="wsdc_id=?",
                parameters=(int(identifier),),
            )
        )
    association = {
        "year": ("substr(event_month,1,4)=?", (identifier,)),
        "calendar": ("event_id IS NOT NULL", ()),
        "resolution": ("event_id=?", (identifier,)),
    }.get(kind)
    if association is not None and (kind != "year" or _is_year(identifier)):
        # Only the association: the key and the event it names.
        state.update(
            (row[0], str(row[1]))
            for row in conn.execute(
                "SELECT event_id,wsdc_id||'|'||role||'|'||series_id||'|'||event_month||'|'||"
                "division||'|'||dance_style FROM registry_placements WHERE " + association[0],
                association[1],
            )
        )
    rows = {
        "calendar": ("1", ()),
        "inventory": ("1", ()),
        "resolution": ("event_id=?", (identifier,)),
    }.get(kind)
    if rows is not None:
        state.update(
            (item["row"]["event_id"], _canonical(item["row"]))
            for item in _table_rows(
                conn, "events", _CLOCKS | {"coverage_tier"}, where=rows[0], parameters=rows[1]
            )
        )
    return frozenset(state)


def hinted_events(
    before: frozenset[tuple[str | None, str]], after: frozenset[tuple[str | None, str]]
) -> set[str]:
    """The events named by rows that differ between two `hint_state` reads."""
    return {str(event) for event, _ in before ^ after if event is not None}
