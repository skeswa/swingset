"""Source-event mapping: one proposal set per source, one resolution per event.

`project/mapping/<source>` proposes a target for each listing of its source
(`propose`): an override; else the held occurrence of the listing's series
that the inventory gave the listing's source and dates (`alias`, D-0253);
else the one catalog event whose name and dates match; else a generated id
with the placeholder event the listing would create. It writes the source's
rows of `source_event_proposals`.

`project/resolution/<event_id>` turns the proposals naming one event into
that event's `source_event_map` rows and, when the event is not a catalog
event, its placeholder `events` row in scope `resolution/<event_id>`
(`resolve`). A resolution with no proposal deletes its rows. The first run
of a resolution also takes its event's row over from the retired
`unmatched_source_events/all` and `override_events/all` scopes.

Neither re-projects events nor reconciles registry placements: events depend
on their resolution, and the years own the association (plan F17, F19).
"""

import calendar
import csv
import dataclasses
import io
import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Protocol

from swingset.model.canonical import Event
from swingset.model.ids import event_id, series_id
from swingset.normalize.events import event_dates_overlap, normalize_event_name
from swingset.state import proposals
from swingset.state.proposals import Proposal
from swingset.state.work import WorkUnit, bump_revision, enqueue

from .history import ListingMatch, alias_map, match_listing, registry_names
from .materialization import helper_recipe, materializing
from .writer import Projection, replace_scope

#: Scopes that owned placeholder events before the map split. Each
#: resolution releases its event's row from them on its first run.
LEGACY_SCOPES = ("unmatched_source_events", "override_events")
_OVERRIDE_FILES = ("overrides/event_aliases.csv", "overrides/source_urls.csv")
_SERIES_ALIASES = "overrides/series_aliases.csv"
#: Processing clocks are filled in when a resolution writes the event, so a
#: proposal does not change with the clock.
_CLOCKS = ("first_seen_at", "last_seen_at", "run_id")


class InputBundleLike(Protocol):
    @property
    def files(self) -> Mapping[str, bytes]: ...


def overrides(files: Mapping[str, bytes]) -> dict[tuple[str, str], str]:
    """Every override target by (source, source_ref); a later row wins."""
    result: dict[tuple[str, str], str] = {}
    for name in _OVERRIDE_FILES:
        for row in _csv(files, name):
            source, ref, target = row.get("source"), row.get("source_ref"), row.get("event_id")
            if source == "worlddanceregistry":
                source = "wdr"
            if not ref and source == "wdr" and row.get("url"):
                from swingset.sources.wdr import source_ref_from_url

                ref = source_ref_from_url(row["url"])
            if source and ref and target:
                result[(source, ref)] = target
    return result


def override_sources(files: Mapping[str, bytes]) -> tuple[str, ...]:
    """The sources the override files name; each has a mapping unit."""
    return tuple(sorted({source for source, _ in overrides(files)}))


def propose(
    conn: sqlite3.Connection,
    source: str,
    bundle: InputBundleLike,
    now: str,
    projector_version: int,
) -> bool:
    """Write the proposals of one source; returns whether they changed.

    Queues a hint for the resolution of every event a changed proposal names,
    before and after; currency decides whether each runs.
    """
    targets = {
        ref: target for (name, ref), target in overrides(bundle.files).items() if name == source
    }
    catalog = list(
        conn.execute(
            "SELECT event_id,name,start_date,end_date FROM events WHERE wsdc_status!='unknown' "
            "ORDER BY event_id"
        )
    )
    listings = {
        str(row[0]): row
        for row in conn.execute(
            "SELECT source_ref,name_raw,start_date,end_date,snapshot_id,parser_version "
            "FROM source_events WHERE source=? ORDER BY source_ref",
            (source,),
        )
    }
    rows: list[Proposal] = []
    for ref, target in sorted(targets.items()):
        listing = listings.get(ref)
        placeholder = _override_placeholder(source, target, listing, projector_version)
        rows.append(Proposal(source, ref, target, "override", 1.0, placeholder))
    series_rule = _SeriesRule(conn, source, bundle.files.get(_SERIES_ALIASES, b""))
    for ref, listing in listings.items():
        if ref in targets:
            continue
        name, start, end = listing[1], listing[2], listing[3]
        occurrence = series_rule.event(name, start, end)
        if occurrence is not None:
            rows.append(Proposal(source, ref, occurrence, "alias", 1.0))
            continue
        candidates = [
            event
            for event in catalog
            if _name(str(event[1])) == _name(str(name or ""))
            and _overlaps(start, end, event[2], event[3])
        ]
        if len(candidates) == 1:
            rows.append(Proposal(source, ref, str(candidates[0][0]), "name_date", 1.0))
        elif not candidates and name and end:
            last = date.fromisoformat(str(end))
            first = date.fromisoformat(str(start or end))
            generated = Event(
                event_id=event_id(last, str(name)),
                series_id=series_id(str(name)),
                name=str(name),
                year=last.year,
                start_date=first.isoformat(),
                end_date=last.isoformat(),
                wsdc_status="unknown",
                sources=(source,),
                source=source,
                snapshot_id=str(listing[4]),
                parser_version=str(listing[5] or projector_version),
                first_seen_at="",
                last_seen_at="",
                run_id="",
            )
            rows.append(
                Proposal(source, ref, generated.event_id, "name_date", 0.5, _encode(generated))
            )
    before = {(row.source, row.source_ref): row for row in proposals.for_source(conn, source)}
    after = {(row.source, row.source_ref): row for row in rows}
    if before == after:
        return False
    proposals.replace_source(conn, source, rows)
    affected = {
        row.event_id
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
        for row in (before.get(key), after.get(key))
        if row is not None
    }
    enqueue(
        conn,
        (WorkUnit("project", "resolution", item) for item in sorted(affected)),
        enqueued_at=now,
    )
    return True


def resolve(conn: sqlite3.Connection, identifier: str, now: str, run_id: str) -> bool:
    """Write one event's map rows and its placeholder row; returns whether any changed.

    The event row follows the map's rules: a generated id takes the
    inventory's row when the inventory owns it (a registry-enriched
    collision), otherwise the listing's placeholder, the last in (source,
    source_ref) order; else an override keeps the row already there or makes
    its placeholder; else a catalog match keeps a row this scope already
    owned. A placeholder keeps the live row's `coverage_tier`, which its year
    owns (F23).
    """
    proposed = proposals.for_event(conn, identifier)
    live = conn.execute("SELECT * FROM events WHERE event_id=?", (identifier,)).fetchone()
    generated = [row for row in proposed if row.match_method != "override" and row.placeholder_json]
    overridden = [row for row in proposed if row.match_method == "override"]
    matched = [
        row for row in proposed if row.match_method != "override" and not row.placeholder_json
    ]
    event: Event | None = None
    if generated:
        inventory = _inventory_event(conn, identifier)
        event = (
            _stored_event(inventory)
            if inventory is not None
            else _decode(str(generated[-1].placeholder_json), now, run_id)
        )
    elif overridden:
        if live is not None:
            event = _stored_event(live)
        else:
            placeholders = [row for row in overridden if row.placeholder_json]
            if placeholders:
                event = _decode(str(placeholders[-1].placeholder_json), now, run_id)
    elif matched and live is not None and _owned(conn, identifier):
        # Registry enrichment can make a listing's own placeholder a catalog
        # event that the listing then matches; this scope keeps owning it.
        event = _stored_event(live)
    if event is not None and live is not None:
        event = dataclasses.replace(event, coverage_tier=str(live["coverage_tier"]))
    changed = replace_scope(
        conn,
        scope_kind="resolution",
        scope_id=identifier,
        projection=Projection((event,) if event is not None else ()),
        run_id=run_id,
        projected_at=now,
        enqueue_links=False,
    )
    changed |= _release_legacy(conn, identifier)
    moved = _replace_map_rows(
        conn,
        identifier,
        [
            (row.source, row.source_ref, identifier, row.match_method, row.match_confidence)
            for row in proposed
        ],
    )
    if changed or moved:
        enqueue(
            conn,
            (WorkUnit("project", "event", item) for item in sorted({identifier, *moved})),
            enqueued_at=now,
        )
    return changed or bool(moved)


def project_map(
    conn: sqlite3.Connection, bundle: InputBundleLike, now: str, run_id: str, projector_version: int
) -> bool:
    """Run every mapping unit, then every resolution, each as its own unit.

    A drain for tests and helper bundles; the scheduler runs the units one at
    a time. Events are not re-projected here.
    """
    from swingset.state.derivation_dependencies import mapping_units, resolution_units
    from swingset.state.read_sets import recording

    recipe = helper_recipe(conn, bundle.files, "project")
    changed = False
    sources = {unit.unit_id for unit in mapping_units(conn)} | set(override_sources(bundle.files))
    for source in sorted(sources):
        mapping = WorkUnit("project", "mapping", source)
        with (
            materializing(conn, mapping, now=now, run_id=run_id, recipe=recipe),
            recording(conn, mapping),
        ):
            changed |= propose(conn, source, bundle, now, projector_version)
    for unit in resolution_units(conn):
        with (
            materializing(conn, unit, now=now, run_id=run_id, recipe=recipe),
            recording(conn, unit),
        ):
            changed |= resolve(conn, unit.unit_id, now, run_id)
    return changed


class _SeriesRule:
    """The inventory's series-and-month rule for one source's listings (D-0253).

    A listing goes to the held occurrence `history.match_listing` names, but
    only when the inventory gave that occurrence this source and dates that
    overlap the listing's: then the inventory attached the listing there and
    the contests follow its header facts. Registry names come from the
    inventory's output (`history.registry_names`).
    """

    def __init__(self, conn: sqlite3.Connection, source: str, aliases: bytes) -> None:
        self._conn = conn
        self._source = source
        self._aliases = alias_map(aliases)
        self._names: dict[str, set[str]] | None = None

    def event(self, name: object, start: object, end: object) -> str | None:
        if not name or not start or not end:
            return None
        if self._names is None:
            self._names = registry_names(self._conn)
        match: ListingMatch = match_listing(
            self._conn, self._aliases, self._names, str(name), str(end)[:7]
        )
        if match.event_id is None:
            return None
        row = self._conn.execute(
            "SELECT sources,start_date,end_date FROM events WHERE event_id=?", (match.event_id,)
        ).fetchone()
        if row is None or self._source not in json.loads(str(row[0])):
            return None
        if not _overlaps(start, end, row[1], row[2]):
            return None
        return match.event_id


def _owned(conn: sqlite3.Connection, identifier: str) -> bool:
    """Whether this resolution or a retired map scope owns the event's row."""
    return (
        conn.execute(
            "SELECT 1 FROM canonical_scope_rows WHERE table_name='events' AND record_key=json_array(?) "
            "AND ((scope_kind='resolution' AND scope_id=?) "
            "OR (scope_kind IN ('unmatched_source_events','override_events') AND scope_id='all')) "
            "LIMIT 1",
            (identifier, identifier),
        ).fetchone()
        is not None
    )


def _release_legacy(conn: sqlite3.Connection, identifier: str) -> bool:
    """Drop a retired map scope's ownership of the event, as `replace_scope` drops a row.

    A row no scope owns any more is deleted, and registry placements that
    named it are detached (D-0191).
    """
    key = json.dumps([identifier], separators=(",", ":"))
    released = 0
    for scope_kind in LEGACY_SCOPES:
        released += conn.execute(
            "DELETE FROM canonical_scope_rows WHERE scope_kind=? AND scope_id='all' "
            "AND table_name='events' AND record_key=?",
            (scope_kind, key),
        ).rowcount
    if (
        not released
        or conn.execute(
            "SELECT 1 FROM canonical_scope_rows WHERE table_name='events' AND record_key=? LIMIT 1",
            (key,),
        ).fetchone()
    ):
        return False
    conn.execute("UPDATE registry_placements SET event_id=NULL WHERE event_id=?", (identifier,))
    if conn.execute("DELETE FROM events WHERE event_id=?", (identifier,)).rowcount:
        bump_revision(conn, "canonical")
        return True
    return False


def _replace_map_rows(
    conn: sqlite3.Connection,
    identifier: str,
    rows: Iterable[tuple[str, str, str, str, float]],
) -> set[str]:
    """Make ``rows`` the event's map rows; returns the events whose rows moved.

    A listing another event held moves here; that event's own resolution
    finds no proposal for it. Only this event's rows are deleted.
    """
    desired = {(row[0], row[1]): row for row in rows}
    current = {
        (str(row[0]), str(row[1])): (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            float(row[4]),
        )
        for row in conn.execute(
            "SELECT m.source,m.source_ref,m.event_id,m.match_method,m.match_confidence "
            "FROM source_event_map m WHERE m.event_id=? OR EXISTS (SELECT 1 FROM "
            "source_event_proposals p WHERE p.event_id=? AND p.source=m.source "
            "AND p.source_ref=m.source_ref)",
            (identifier, identifier),
        )
    }
    moved: set[str] = set()
    for key, row in current.items():
        if key not in desired and row[2] == identifier:
            conn.execute("DELETE FROM source_event_map WHERE source=? AND source_ref=?", key)
            moved.add(identifier)
    for key, row in desired.items():
        if current.get(key) == row:
            continue
        if key in current:
            moved.add(current[key][2])
        conn.execute(
            "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) "
            "VALUES (?,?,?,?,?) ON CONFLICT(source,source_ref) DO UPDATE SET "
            "event_id=excluded.event_id,match_method=excluded.match_method,"
            "match_confidence=excluded.match_confidence",
            row,
        )
        moved.add(identifier)
    if moved:
        bump_revision(conn, "source_event_map")
    return moved


def _override_placeholder(
    source: str, target: str, listing: sqlite3.Row | None, projector_version: int
) -> str | None:
    """The event an override target gets when no row exists; None for an unparseable id."""
    try:
        year, month = (int(value) for value in target.split("-", 2)[:2])
    except ValueError:
        return None
    display_name = (
        str(listing[1])
        if listing is not None and listing[1]
        else target.split("-", 2)[-1].replace("-", " ").title()
    )
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return _encode(
        Event(
            event_id=target,
            series_id=series_id(display_name),
            name=display_name,
            year=year,
            start_date=first.isoformat(),
            end_date=last.isoformat(),
            wsdc_status="unknown",
            sources=(source,),
            source=source,
            snapshot_id="override",
            parser_version=str(projector_version),
            first_seen_at="",
            last_seen_at="",
            run_id="",
        )
    )


def _encode(event: Event) -> str:
    values = {key: value for key, value in event.values().items() if key not in _CLOCKS}
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decode(payload: str, now: str, run_id: str) -> Event:
    values: dict[str, Any] = json.loads(payload)
    for key in ("sources", "history_source"):
        values[key] = tuple(values.get(key, ()))
    return Event(**values, first_seen_at=now, last_seen_at=now, run_id=run_id)


def _inventory_event(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
    # Match materialization.output_rows(inventory): history enrichment or its
    # durable ownership makes this an inventory record, not a placeholder.
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM events WHERE event_id=? AND (history_source!='[]' OR EXISTS "
        "(SELECT 1 FROM canonical_scope_rows c WHERE c.scope_kind='history' "
        "AND c.scope_id='all' AND c.table_name='events' "
        "AND c.record_key=json_array(events.event_id)))",
        (identifier,),
    ).fetchone()
    return row


def _stored_event(row: sqlite3.Row) -> Event:
    sources = json.loads(str(row["sources"]))
    return Event(
        event_id=str(row["event_id"]),
        series_id=str(row["series_id"]),
        name=str(row["name"]),
        year=int(row["year"]),
        start_date=row["start_date"],
        end_date=row["end_date"],
        event_month=str(row["event_month"]),
        date_precision=str(row["date_precision"]),
        held=str(row["held"]),
        coverage_tier=str(row["coverage_tier"]),
        history_source=tuple(json.loads(row["history_source"])),
        city=None if row["city"] is None else str(row["city"]),
        region=None if row["region"] is None else str(row["region"]),
        country=None if row["country"] is None else str(row["country"]),
        website=None if row["website"] is None else str(row["website"]),
        wsdc_status=str(row["wsdc_status"]),
        sources=tuple(str(value) for value in sources),
        live_window_start=None
        if row["live_window_start"] is None
        else str(row["live_window_start"]),
        live_window_end=None if row["live_window_end"] is None else str(row["live_window_end"]),
        source=str(row["source"]),
        snapshot_id=str(row["snapshot_id"]),
        parser_version=str(row["parser_version"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        run_id=str(row["run_id"]),
    )


def _csv(files: Mapping[str, bytes], name: str) -> list[dict[str, str]]:
    body = files.get(name, b"")
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig")))) if body.strip() else []


def _name(value: str) -> str:
    return normalize_event_name(value)


def _overlaps(a_start: object, a_end: object, b_start: object, b_end: object) -> bool:
    if None in {a_start, a_end, b_start, b_end}:
        return False
    return event_dates_overlap(
        date.fromisoformat(str(a_start)),
        date.fromisoformat(str(a_end)),
        date.fromisoformat(str(b_start)),
        date.fromisoformat(str(b_end)),
    )
