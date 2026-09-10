"""Source-event matching map and atomic membership moves."""

import calendar
import csv
import io
import json
import sqlite3
from collections.abc import Mapping
from datetime import date
from typing import Protocol

from swingset.model.canonical import Event
from swingset.model.ids import event_id, series_id
from swingset.normalize.events import event_dates_overlap, normalize_event_name

from .contests import project_event
from .registry_events import reconcile_registry_events
from .writer import Projection, replace_scope, replace_source_event_map


class InputBundleLike(Protocol):
    @property
    def files(self) -> Mapping[str, bytes]: ...


def project_map(
    conn: sqlite3.Connection, bundle: InputBundleLike, now: str, run_id: str, projector_version: int
) -> bool:
    overrides: dict[tuple[str, str], str] = {}
    for row in _csv(bundle, "overrides/event_aliases.csv") + _csv(
        bundle, "overrides/source_urls.csv"
    ):
        source, ref, target = row.get("source"), row.get("source_ref"), row.get("event_id")
        if source == "worlddanceregistry":
            source = "wdr"
        if not ref and source == "wdr" and row.get("url"):
            from swingset.sources.wdr import source_ref_from_url

            ref = source_ref_from_url(row["url"])
        if source and ref and target:
            overrides[(source, ref)] = target
    events = list(
        conn.execute(
            "SELECT event_id,name,start_date,end_date FROM events WHERE wsdc_status!='unknown'"
        )
    )
    mapped: list[tuple[str, str, str, str, float]] = []
    unknown: list[Event] = []
    mapped.extend(
        (source, ref, target, "override", 1.0) for (source, ref), target in overrides.items()
    )
    placeholder_events: list[Event] = []
    for (source, ref), target in overrides.items():
        existing = conn.execute("SELECT * FROM events WHERE event_id=?", (target,)).fetchone()
        if existing is not None:
            placeholder_events.append(_stored_event(existing))
            continue
        try:
            year, month = (int(value) for value in target.split("-", 2)[:2])
        except ValueError:
            continue
        source_name = conn.execute(
            "SELECT name_raw,snapshot_id,parser_version FROM source_events WHERE source=? AND source_ref=?",
            (source, ref),
        ).fetchone()
        display_name = (
            str(source_name[0])
            if source_name and source_name[0]
            else target.split("-", 2)[-1].replace("-", " ").title()
        )
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        placeholder_events.append(
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
                first_seen_at=now,
                last_seen_at=now,
                run_id=run_id,
            )
        )
    replace_scope(
        conn,
        scope_kind="override_events",
        scope_id="all",
        projection=Projection(tuple(placeholder_events)),
        run_id=run_id,
        projected_at=now,
        enqueue_links=False,
    )
    for source_row in conn.execute(
        "SELECT source,source_ref,name_raw,start_date,end_date,snapshot_id,parser_version FROM source_events"
    ):
        key = str(source_row[0]), str(source_row[1])
        if key in overrides:
            continue
        candidates = [
            event
            for event in events
            if _name(str(event[1])) == _name(str(source_row[2] or ""))
            and _overlaps(source_row[3], source_row[4], event[2], event[3])
        ]
        if len(candidates) == 1:
            mapped.append((*key, str(candidates[0][0]), "name_date", 1.0))
            continue
        if not candidates and source_row[2] and source_row[4]:
            end, start = (
                date.fromisoformat(str(source_row[4])),
                date.fromisoformat(str(source_row[3] or source_row[4])),
            )
            generated = event_id(end, str(source_row[2]))
            mapped.append((*key, generated, "name_date", 0.5))
            unknown.append(
                Event(
                    event_id=generated,
                    series_id=series_id(str(source_row[2])),
                    name=str(source_row[2]),
                    year=end.year,
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                    wsdc_status="unknown",
                    sources=(key[0],),
                    source=key[0],
                    snapshot_id=str(source_row[5]),
                    parser_version=str(source_row[6] or projector_version),
                    first_seen_at=now,
                    last_seen_at=now,
                    run_id=run_id,
                )
            )
    desired_unknown_ids = {event.event_id for event in unknown}
    retained_unknown = list(unknown)
    retiring_unknown_ids: set[str] = set()
    for row in conn.execute(
        """SELECT e.* FROM canonical_scope_rows c JOIN events e
        ON c.table_name='events' AND c.record_key=json_array(e.event_id)
        WHERE c.scope_kind='unmatched_source_events' AND c.scope_id='all'"""
    ):
        stored = _stored_event(row)
        if stored.event_id not in desired_unknown_ids:
            retained_unknown.append(stored)
            owner_count = conn.execute(
                "SELECT count(*) FROM canonical_scope_rows WHERE table_name='events' "
                "AND record_key=json_array(?)",
                (stored.event_id,),
            ).fetchone()[0]
            if owner_count == 1:
                retiring_unknown_ids.add(stored.event_id)
    premap_unknown_changed = replace_scope(
        conn,
        scope_kind="unmatched_source_events",
        scope_id="all",
        projection=Projection(tuple(retained_unknown)),
        run_id=run_id,
        projected_at=now,
        enqueue_links=False,
    )
    before = [
        tuple(row)
        for row in conn.execute(
            "SELECT source,source_ref,event_id,match_method,match_confidence FROM source_event_map ORDER BY source,source_ref"
        )
    ]
    affected = replace_source_event_map(conn, tuple(mapped))
    for event in affected:
        replace_scope(
            conn,
            scope_kind="event",
            scope_id=event,
            projection=project_event(conn, event, now, run_id),
            run_id=run_id,
            projected_at=now,
        )
        conn.execute(
            "DELETE FROM pending_work WHERE stage='project' AND unit_kind='event' AND unit_id=?",
            (event,),
        )
    registry_changed = reconcile_registry_events(
        conn,
        reconciled_at=now,
        run_id=run_id,
        excluded_event_ids=frozenset(retiring_unknown_ids),
    )
    unknown_changed = replace_scope(
        conn,
        scope_kind="unmatched_source_events",
        scope_id="all",
        projection=Projection(tuple(unknown)),
        run_id=run_id,
        projected_at=now,
        enqueue_links=False,
    )
    return (
        premap_unknown_changed
        or registry_changed
        or unknown_changed
        or before != sorted(mapped)
    )


def _stored_event(row: sqlite3.Row) -> Event:
    sources = json.loads(str(row["sources"]))
    return Event(
        event_id=str(row["event_id"]),
        series_id=str(row["series_id"]),
        name=str(row["name"]),
        year=int(row["year"]),
        start_date=str(row["start_date"]),
        end_date=str(row["end_date"]),
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


def _csv(bundle: InputBundleLike, name: str) -> list[dict[str, str]]:
    body = bundle.files.get(name, b"")
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
