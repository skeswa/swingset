"""Registry occurrence inventory, conservative listing reconciliation, and year gates."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from swingset.model.history import HISTORY_START
from swingset.model.ids import series_slug
from swingset.model.observations import decode_payload
from swingset.sources.records import CalendarRow
from swingset.state.findings import Finding, replace_findings
from swingset.state.work import WorkUnit, bump_revision, enqueue

#: Whether a calendar listing that prints a cancel or hiatus note keeps its
#: own row instead of joining its edition's (D-0260). Turn off when the
#: calendar contract (D-0257) is activated, so the twins merge.
KEEP_CANCELLED_TWINS = True

TIERS = ("registry_only", "index_only", "sheets_partial", "sheets_complete")


def _history_source(source: str) -> str:
    return {
        "wsdc_registry": "registry",
        "wsdc_calendar": "calendar",
        "wsdc_newsletter": "newsletter",
        "swingdancecouncil": "swingdancecouncil",
        "steprightsolutions": "steprightsolutions",
    }.get(source, "platform")


def _next_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    return f"{year + (number == 12):04d}-{number % 12 + 1:02d}"


def prepare_inventory(
    conn: sqlite3.Connection,
    *,
    now: str,
    run_id: str,
    aliases: bytes = b"",
    history_start: date = HISTORY_START,
    reviews: bytes = b"",
) -> bool:
    """Create missing occurrence rows; enrich stable ids without guessing series names."""
    before = _inventory_state(conn)
    alias_map = _alias_map(aliases)
    dismissed = dismissed_findings(reviews)
    occurrences = list(
        conn.execute(
            "SELECT series_id,event_month,series_name_raw,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id FROM registry_placements ORDER BY last_seen_at,snapshot_id"
        )
    )
    by_occurrence = {
        (str(r[0]), str(r[1])[:7]): r
        for r in occurrences
        if str(r[1])[:7] >= history_start.strftime("%Y-%m")
    }
    names: dict[str, set[str]] = {}
    for (series, month), row in by_occurrence.items():
        name = str(row[2])
        names.setdefault(series_slug(name), set()).add(series)
        conn.execute(
            "INSERT INTO series(series_id,name) VALUES (?,?) ON CONFLICT(series_id) DO UPDATE SET name=excluded.name",
            (series, name),
        )
        existing = conn.execute(
            "SELECT event_id,held,sources FROM events WHERE series_id=? AND event_month=? "
            "ORDER BY event_id",
            (series, month),
        ).fetchall()
        if not any(row[1] == "held" for row in existing):
            # Existing published calendar editions keep their identifiers when
            # a later registry occurrence supplies the series and reporting month.
            legacy = [
                event
                for event in conn.execute(
                    "SELECT event_id,name,event_month,series_id FROM events WHERE series_id NOT LIKE 'wsdc-%'"
                )
                if (
                    series_slug(str(event[1])) == series_slug(name)
                    or _alias_for(alias_map, series_slug(str(event[1])), str(event[2])[:7])
                    == series
                )
                and len(str(event[2])) == 7
                and (str(event[2]) == month or _next_month(str(event[2])) == month)
            ]
            if len(legacy) == 1:
                conn.execute(
                    "UPDATE events SET series_id=?,event_month=?,held='held' WHERE event_id=?",
                    (series, month, legacy[0][0]),
                )
            elif existing:
                # A listing printed before the registry reported its edition,
                # of a series the registry already knew, wrote these rows
                # `listed`. The occurrence adopts one: a row the WSDC calendar
                # listed, else the first id; never a cancelled row (D-0256).
                listed = [row for row in existing if row[1] == "listed"]
                calendar = [row for row in listed if "wsdc_calendar" in json.loads(row[2] or "[]")]
                adopted = (calendar or listed)[:1]
                for event in adopted:
                    conn.execute("UPDATE events SET held='held' WHERE event_id=?", (event[0],))
            else:
                # Reuse the v1 identifier where its exact edition name and month agree.
                event_id = f"{month}-{series_slug(name)}"
                collision = conn.execute(
                    "SELECT series_id FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if (
                    collision is not None
                    and str(collision[0]).startswith("wsdc-")
                    and collision[0] != series
                ):
                    event_id += "-" + series
                conn.execute(
                    """INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id,event_month,date_precision,held,coverage_tier,history_source)
                    VALUES (?,?,?,?,NULL,NULL,'registry','["wsdc_registry"]','wsdc_registry',?,?,?,?,?,?,'month','held','registry_only','["registry"]')
                    ON CONFLICT(event_id) DO UPDATE SET series_id=excluded.series_id,held='held',event_month=excluded.event_month""",
                    (
                        event_id,
                        series,
                        name,
                        int(month[:4]),
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        run_id,
                        month,
                    ),
                )
        # A registry occurrence is a registry event. A row adopted above, or
        # earlier, kept the `unknown` of the placeholder or listing it began
        # as; a trial label stays (D-0253).
        conn.execute(
            "UPDATE events SET wsdc_status='registry' WHERE series_id=? AND event_month=? "
            "AND held='held' AND wsdc_status='unknown'",
            (series, month),
        )
    # History membership outlives the rolling calendar window. Its scope keeps
    # registry-backed editions from being deleted when a newer index omits them.
    for event in conn.execute(
        "SELECT event_id FROM events WHERE held='held' AND series_id LIKE 'wsdc-%'"
    ):
        conn.execute(
            "INSERT OR IGNORE INTO canonical_scope_rows(scope_kind,scope_id,table_name,record_key) VALUES ('history','all','events',?)",
            (json.dumps([str(event[0])], separators=(",", ":")),),
        )
    findings: dict[int, list[Finding]] = {}
    listings: dict[tuple[str, str, str, str], tuple[CalendarRow, sqlite3.Row]] = {}
    observation_times = {
        str(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT snapshot_id,COALESCE(observed_at,fetched_at) FROM snapshots"
        )
    }
    for row in conn.execute(
        "SELECT o.kind,o.payload_json,o.snapshot_id,o.parser_version,w.source,COALESCE(s.observed_at,s.fetched_at) AS observed FROM observations o JOIN snapshots s USING(snapshot_id) JOIN watches w USING(watch_id) WHERE o.scope_kind='calendar' ORDER BY observed,s.snapshot_id,o.seq"
    ):
        payload = decode_payload(str(row[0]), str(row[1]))
        if not isinstance(payload, CalendarRow):
            continue
        month = payload.end_date_raw[:7]
        if month < history_start.strftime("%Y-%m"):
            continue
        listings[
            (
                series_slug(payload.name_raw),
                payload.start_date_raw,
                payload.end_date_raw,
                str(row[4]),
            )
        ] = (payload, row)
    for row in conn.execute(
        "SELECT se.name_raw,se.start_date,se.snapshot_id,se.parser_version,se.source,se.end_date,se.location_raw,se.url FROM source_events se JOIN snapshots s USING(snapshot_id) WHERE se.start_date IS NOT NULL AND se.end_date IS NOT NULL ORDER BY COALESCE(s.observed_at,s.fetched_at),se.snapshot_id"
    ):
        if not row[0] or str(row[5])[:7] < history_start.strftime("%Y-%m"):
            continue
        # A results platform does not say whether an event is a WSDC event;
        # its status comes from the series it resolves to (D-0253).
        payload = CalendarRow(
            "calendar_row",
            str(row[0]),
            str(row[1]),
            str(row[5]),
            "",
            str(row[6] or ""),
            str(row[7]),
            None,
            (),
        )
        listings[
            (
                series_slug(payload.name_raw),
                payload.start_date_raw,
                payload.end_date_raw,
                str(row[4]),
            )
        ] = (payload, row)
    resolved: dict[str, list[tuple[CalendarRow, sqlite3.Row]]] = {}
    editions: dict[tuple[str, str], dict[str, list[tuple[CalendarRow, sqlite3.Row]]]] = {}
    for payload, row in _current_listings(listings, observation_times):
        month = payload.end_date_raw[:7]
        year = int(month[:4])
        match = match_listing(conn, alias_map, names, payload.name_raw, month)
        normalized, candidates = match.slug, match.series
        if len(candidates) != 1:
            _open(
                findings,
                year,
                dismissed,
                Finding(
                    "series_alias",
                    "history_listing",
                    f"{month}:{normalized}",
                    "warning",
                    "Historical listing needs a reviewed series alias",
                    {
                        "year": year,
                        "printed_name": payload.name_raw,
                        "candidates": sorted(candidates),
                    },
                    snapshot_id=str(row[2]),
                    suggested_override=f"{payload.name_raw},listed-{normalized},{row[4]},review series identity",
                ),
            )
            continue
        series = next(iter(candidates))
        conn.execute(
            "INSERT OR IGNORE INTO series(series_id,name) VALUES (?,?)", (series, payload.name_raw)
        )
        matches = match.events
        if len(matches) > 1:
            _open(
                findings,
                year,
                dismissed,
                Finding(
                    "event_alias",
                    "history_listing",
                    f"{month}:{normalized}",
                    "warning",
                    "Listing matches multiple registry occurrences",
                    {"year": year, "event_ids": list(matches)},
                ),
            )
            continue
        if not matches:
            # An edition the registry has not reported: its listings of one
            # series and month are one edition, whatever note a name gained
            # ("Bavarian Open (Cancelled due to Covid-19)", D-0260).
            kind = "platform" if _history_source(str(row[4])) == "platform" else "calendar"
            editions.setdefault((series, month), {}).setdefault(kind, []).append((payload, row))
            continue
        resolved.setdefault(matches[0], []).append((payload, row))
    superseded: set[str] = set()
    edition_series: dict[str, str] = {}
    for (series, month), kinds in editions.items():
        group, platforms = kinds.get("calendar", []), kinds.get("platform", [])
        # A results listing joins the edition only when its dates touch a
        # calendar-family listing's, the test the map applies (`near`).
        apart = [
            item
            for item in platforms
            if not any(
                near(item[0].start_date_raw, item[0].end_date_raw, p.start_date_raw, p.end_date_raw)
                for p, _ in group
            )
        ]
        platforms = [item for item in platforms if item not in apart]
        for payload, row in apart:
            resolved.setdefault(f"{month}-{series_slug(payload.name_raw)}", []).append(
                (payload, row)
            )
        if KEEP_CANCELLED_TWINS:
            # Until the calendar contract is active the release publishes a
            # calendar row from its baseline, so a merged twin would take its
            # cancelled status out of the release: a listing that prints a
            # cancel or hiatus note keeps the row its name gives (D-0260).
            for payload, row in [item for item in group if _marked(item[0])]:
                resolved.setdefault(f"{month}-{series_slug(payload.name_raw)}", []).append(
                    (payload, row)
                )
            group = [item for item in group if not _marked(item[0])]
        if not group or _two_editions(group):
            # A results listing alone keeps its own row, and one capture that
            # dates two spans that do not nest (Korea Westival 2020) is two
            # listings: each keeps the id its name gives, as before.
            for payload, row in group + platforms:
                event_id = f"{month}-{series_slug(payload.name_raw)}"
                resolved.setdefault(event_id, []).append((payload, row))
            continue
        event_id, others = _edition_id(conn, series, month, group, platforms)
        resolved.setdefault(event_id, []).extend(group + platforms)
        edition_series[event_id] = series
        superseded.update(others)
    for event_id, dated_candidates in resolved.items():
        edition = list(dated_candidates)
        place = _calendar_place(edition, observation_times)
        payload, row = max(
            dated_candidates,
            key=lambda item: (observation_times.get(str(item[1][2]), ""), str(item[1][2])),
        )
        year = int(payload.end_date_raw[:4])
        # Two editions coexisting in a capture (or different end months in
        # the registry window) are ambiguous. Older conflicting source facts
        # are superseded by the winning observed_at, not fetched_at.
        spans = {(p.start_date_raw, p.end_date_raw) for p, _ in dated_candidates}
        snapshot_spans: dict[str, set[tuple[str, str]]] = {}
        for candidate, evidence in dated_candidates:
            snapshot_spans.setdefault(str(evidence[2]), set()).add(
                (candidate.start_date_raw, candidate.end_date_raw)
            )
        ambiguous = (
            any(len(values) > 1 for values in snapshot_spans.values())
            or len({p.end_date_raw[:7] for p, _ in dated_candidates}) > 1
        )
        # A results platform often records one day of a weekend that a listing
        # dates in full. Spans that nest inside one widest span are one
        # edition; the widest span is its dates (D-0184). Two spans that
        # overlap only partly, or not at all, stay a review finding.
        widest = _widest_span(spans)
        if ambiguous and widest is not None:
            ambiguous = False
            dated_candidates = [
                item
                for item in dated_candidates
                if (item[0].start_date_raw, item[0].end_date_raw) == widest
            ] or dated_candidates
        if ambiguous:
            _open(
                findings,
                year,
                dismissed,
                Finding(
                    "event_alias",
                    "event",
                    event_id,
                    "warning",
                    "Multiple dated editions match one registry occurrence",
                    {"year": year, "date_spans": sorted(spans)},
                ),
            )
            continue
        normalized = series_slug(payload.name_raw)
        series = edition_series.get(event_id) or (
            _alias_for(alias_map, normalized, payload.end_date_raw[:7])
            or next(iter(names[normalized]))
        )
        source = str(row[4])
        previous = conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        # Every listing of the edition contributed, the one the widest span
        # left out too, and a held row is the registry's (D-0260).
        sources = {str(evidence[4]) for _, evidence in edition}
        histories = {_history_source(item) for item in sources}
        if previous is not None:
            sources.update(json.loads(previous["sources"]))
            histories.update(json.loads(previous["history_source"]))
            if previous["held"] == "held":
                sources.add("wsdc_registry")
                histories.add("registry")
        cancelled = _cancelled(edition, observation_times)
        status = _listing_status(payload, source, series, edition, observation_times)
        month = payload.end_date_raw[:7]
        precision = "day" if len(payload.end_date_raw) == 10 else "month"
        start = payload.start_date_raw if precision == "day" else None
        end = payload.end_date_raw if precision == "day" else None
        if (
            start is not None
            and end is not None
            and widest is not None
            and len(widest[0]) == 10
            and widest[0] <= start <= end <= widest[1]
        ):
            # A results site dates one day of the weekend a listing of the
            # same edition dates in full: the edition keeps the full span
            # (D-0184, D-0260).
            start, end = widest
            year = int(end[:4])
        conn.execute(
            f"""INSERT INTO events(event_id,series_id,name,year,start_date,end_date,website,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id,event_month,date_precision,held,coverage_tier,history_source,city,region,country)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET city=COALESCE(excluded.city,events.city),region=COALESCE(excluded.region,events.region),country=COALESCE(excluded.country,events.country), wsdc_status=CASE WHEN events.held='held' THEN events.wsdc_status ELSE excluded.wsdc_status END,source=excluded.source,snapshot_id=excluded.snapshot_id,parser_version=excluded.parser_version,sources=excluded.sources,last_seen_at=excluded.last_seen_at,run_id=excluded.run_id,start_date=excluded.start_date,end_date=excluded.end_date,year=excluded.year,website=COALESCE(excluded.website,events.website),date_precision=excluded.date_precision,coverage_tier=CASE WHEN events.coverage_tier='registry_only' THEN 'index_only' ELSE events.coverage_tier END,history_source=excluded.history_source,held=CASE WHEN events.held='held' THEN 'held' ELSE excluded.held END WHERE events.start_date IS NOT excluded.start_date OR events.end_date IS NOT excluded.end_date OR events.source IS NOT excluded.source OR events.snapshot_id IS NOT excluded.snapshot_id OR {_list_differs("history_source")} OR {_list_differs("sources")} OR events.date_precision IS NOT excluded.date_precision OR (events.held!='held' AND (events.wsdc_status IS NOT excluded.wsdc_status OR events.held IS NOT excluded.held)) OR (excluded.city IS NOT NULL AND events.city IS NOT excluded.city) OR (excluded.region IS NOT NULL AND events.region IS NOT excluded.region) OR (excluded.country IS NOT NULL AND events.country IS NOT excluded.country)""",
            (
                event_id,
                series,
                payload.name_raw,
                year,
                start,
                end,
                payload.website,
                status,
                _json_list(sources),
                source,
                row[2],
                row[3],
                now,
                now,
                run_id,
                month,
                precision,
                "cancelled" if cancelled else "listed",
                "index_only",
                _json_list(histories),
                *place,
            ),
        )
    held_months = {
        (str(row[0]), str(row[1]))
        for row in conn.execute("SELECT series_id,event_month FROM events WHERE held='held'")
    }
    stale = {
        str(row[0])
        for row in conn.execute(
            "SELECT event_id,series_id,event_month,history_source FROM events WHERE held!='held'"
        )
        if (str(row[1]), str(row[2])) in held_months and json.loads(str(row[3] or "[]"))
    }
    for event_id in sorted((superseded | stale) - set(resolved)):
        # A twin row of a merged edition goes, and so does a listing's row
        # beside its held occurrence that no listing writes any more, unless
        # something else holds it: a scope, results, judges, a listing's map
        # row or proposal, an origin intent, or registry placements (D-0260).
        twin = conn.execute("SELECT held FROM events WHERE event_id=?", (event_id,)).fetchone()
        if twin is None:
            continue
        if not conn.execute(
            "SELECT 1 FROM canonical_scope_rows WHERE table_name='events' "
            "AND record_key=json_array(?1) UNION ALL "
            + " UNION ALL ".join(
                f"SELECT 1 FROM {table} WHERE event_id=?1"
                for table in (
                    "contests",
                    "judges",
                    "registry_placements",
                    "source_event_map",
                    "source_event_proposals",
                    "history_origin_intents",
                )
            )
            + " LIMIT 1",
            (event_id,),
        ).fetchone():
            conn.execute("DELETE FROM events WHERE event_id=?", (event_id,))
    for year in range(history_start.year, int(now[:4]) + 1):
        replace_findings(
            conn,
            owner_kind="history_year",
            owner_id=str(year),
            findings=tuple(findings.get(year, ())),
            opened_at=now,
            run_id=run_id,
        )
    changed = before != _inventory_state(conn)
    if changed:
        bump_revision(conn, "canonical")
        bump_revision(conn, "dancers")
        enqueue(
            conn,
            (
                WorkUnit("link", "event", str(row[0]))
                for row in conn.execute("SELECT event_id FROM events")
            ),
            enqueued_at=now,
        )
    return changed


def finalize_history(
    conn: sqlite3.Connection, *, now: str, history_start: date = HISTORY_START
) -> None:
    """Associate occurrences and count coverage after map and event output settle."""
    # Placements join the held occurrence of their series and month, else the
    # first row (D-0256). Rewrite only associations that change. Rewriting all ~200k rows each run
    # fired the history fence trigger per row inside the 45-second write
    # bound (D-0199).
    association = (
        "(SELECT e.event_id FROM events e WHERE e.series_id=registry_placements.series_id "
        "AND e.event_month=substr(registry_placements.event_month,1,7) "
        "ORDER BY e.held!='held',e.event_id LIMIT 1)"
    )
    conn.execute(
        f"UPDATE registry_placements SET event_id={association} "
        f"WHERE event_month>=? AND event_id IS NOT {association}",
        (history_start.strftime("%Y-%m"),),
    )
    rebuild_coverage(conn, now=now)


def finalize_year(
    conn: sqlite3.Connection, year: int, *, now: str, history_start: date = HISTORY_START
) -> bool:
    """Associate one year's occurrences and count that year's coverage (F17).

    The association is `finalize_history`'s, restricted to placements whose
    reporting month falls in ``year``; an occurrence and its event share a
    month, so the years split it exactly. A changed association moves the
    canonical revision, so a cutoff that rewrote it moves the year replay
    epoch: the year of a December event counts that event's January
    placements. Coverage is `rebuild_year_coverage`. Returns whether any live
    row changed.
    """
    from .coverage import rebuild_year_coverage

    association = (
        "(SELECT e.event_id FROM events e WHERE e.series_id=registry_placements.series_id "
        "AND e.event_month=substr(registry_placements.event_month,1,7) "
        "ORDER BY e.held!='held',e.event_id LIMIT 1)"
    )
    moved = conn.execute(
        f"UPDATE registry_placements SET event_id={association} "
        f"WHERE event_month>=? AND substr(event_month,1,4)=? AND event_id IS NOT {association}",
        (history_start.strftime("%Y-%m"), f"{year:04d}"),
    ).rowcount
    if moved:
        bump_revision(conn, "canonical")
    return rebuild_year_coverage(conn, year, now=now) or bool(moved)


def reconcile_history(
    conn: sqlite3.Connection,
    *,
    now: str,
    run_id: str,
    aliases: bytes = b"",
    history_start: date = HISTORY_START,
    reviews: bytes = b"",
) -> bool:
    """Compatibility entry point for a complete inventory and coverage refresh."""
    changed = prepare_inventory(
        conn, now=now, run_id=run_id, aliases=aliases, reviews=reviews, history_start=history_start
    )
    finalize_history(conn, now=now, history_start=history_start)
    return changed


@dataclass(frozen=True)
class ListingMatch:
    """Where the series-and-month rule puts one printed listing (D-0253).

    ``series`` holds every candidate series; the listing has a series only
    when it holds one. ``events`` are that series' held occurrences in the
    listing's end month or the next.
    """

    slug: str
    series: frozenset[str]
    events: tuple[str, ...]

    @property
    def event_id(self) -> str | None:
        """The one held occurrence, or None when there is none or the match is ambiguous."""
        return self.events[0] if len(self.series) == 1 and len(self.events) == 1 else None


def match_listing(
    conn: sqlite3.Connection,
    aliases: AliasMap,
    names: Mapping[str, Iterable[str]],
    name: str,
    month: str,
) -> ListingMatch:
    """The series of a printed listing name and that series' held occurrences.

    The series is the reviewed `series_aliases.csv` row for the name's slug
    in ``month``, else every registry series printed with that slug
    (``names``). With one series, its events are the held occurrences in
    ``month`` or the next: the registry reports an event in its end month or
    the month after. The inventory puts a listing's header facts on that
    event and the map puts its contests there, so both call this (D-0253).
    """
    slug = series_slug(name)
    aliased = _alias_for(aliases, slug, month)
    series = frozenset({aliased} if aliased else names.get(slug, ()))
    if len(series) != 1:
        return ListingMatch(slug, series, ())
    events = tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT event_id FROM events WHERE series_id=? AND event_month IN (?,?) AND held='held'",
            (next(iter(series)), month, _next_month(month)),
        )
    )
    return ListingMatch(slug, series, events)


def registry_names(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Registry series by printed-name slug, read from the inventory's output.

    The map's stand-in for the names the inventory reads from
    `registry_placements`, which would tie every mapping to every dancer:
    each registry series' stored name and the name of each held occurrence.
    """
    names: dict[str, set[str]] = {}
    for series, name in conn.execute(
        "SELECT series_id,name FROM series WHERE series_id LIKE 'wsdc-%' "
        "UNION SELECT series_id,name FROM events WHERE held='held' AND series_id LIKE 'wsdc-%'"
    ):
        names.setdefault(series_slug(str(name)), set()).add(str(series))
    return names


def alias_map(aliases: bytes) -> AliasMap:
    """The reviewed series aliases of `overrides/series_aliases.csv`."""
    return _alias_map(aliases)


def _edition_id(
    conn: sqlite3.Connection,
    series: str,
    month: str,
    group: list[tuple[CalendarRow, sqlite3.Row]],
    platforms: list[tuple[CalendarRow, sqlite3.Row]],
) -> tuple[str, list[str]]:
    """The one event id of an unreported edition, and the twin rows it replaces.

    Every listing of a series in one end month is the same edition, its
    results listings included. Its id is the oldest, then shortest, of the
    existing unheld rows that a calendar-family listing's name gives or that
    only such listings wrote for that series and month, so the id a release
    already carries stays; else an existing row a results listing's name
    gives; else the shortest id a calendar-family name gives. The rows only
    calendar-family listings wrote, other than it, are its twins (D-0260).
    """

    def natural(items: list[tuple[CalendarRow, sqlite3.Row]]) -> set[str]:
        return {f"{month}-{series_slug(payload.name_raw)}" for payload, _ in items}

    named, results = natural(group), natural(platforms)
    rows = {
        str(row[0]): (str(row[2] or ""), len(str(row[0])), str(row[0]), str(row[1] or "[]"), row[3])
        for row in conn.execute(
            "SELECT event_id,history_source,first_seen_at,series_id FROM events "
            f"WHERE held!='held' AND ((series_id=? AND event_month=?) OR event_id IN "
            f"({','.join('?' * len(named | results))}))",
            (series, month, *sorted(named | results)),
        )
    }
    listing_only = {
        key
        for key, row in rows.items()
        if row[4] == series and not {"platform", "registry"} & set(json.loads(row[3]))
    }
    preferred = sorted(row[:3] for key, row in rows.items() if key in named | listing_only)
    others = sorted(row[:3] for key, row in rows.items() if key in results)
    if preferred:
        event_id = preferred[0][2]
    elif others:
        event_id = others[0][2]
    else:
        event_id = min(named, key=lambda value: (len(value), value))
    return event_id, sorted(listing_only - {event_id})


def near(a_start: object, a_end: object, b_start: object, b_end: object) -> bool:
    """Whether two dated spans overlap or lie within a day of each other.

    A results site may date a Thursday party the calendar leaves out (WCS
    Party 2024: scoring.dance Sep 26, calendar Sep 27 to 30). The inventory
    groups an unreported edition's listings and the map sends their results
    by this one test (D-0260).
    """
    try:
        values = [date.fromisoformat(str(value)[:10]) for value in (a_start, a_end, b_start, b_end)]
    except (TypeError, ValueError):
        return False
    slack = timedelta(days=1)
    return values[0] <= values[3] + slack and values[2] <= values[1] + slack


def _marked(payload: CalendarRow) -> bool:
    """Whether a listing prints a cancel or hiatus note."""
    return any(
        word in value.casefold()
        for value in (payload.name_raw, *payload.row_classes)
        for word in ("cancel", "hiatus")
    )


def _two_editions(group: list[tuple[CalendarRow, sqlite3.Row]]) -> bool:
    """Whether one capture in ``group`` dates spans that are not one edition."""
    spans: dict[str, set[tuple[str, str]]] = {}
    for payload, row in group:
        spans.setdefault(str(row[2]), set()).add((payload.start_date_raw, payload.end_date_raw))
    return any(len(values) > 1 and _widest_span(values) is None for values in spans.values())


def _calendar_place(
    candidates: list[tuple[CalendarRow, sqlite3.Row]], observation_times: dict[str, str]
) -> tuple[str | None, str | None, str | None]:
    """City, region and country from the newest WSDC calendar listing of an edition.

    Parsed as the calendar projection parses them (`project/events`). A flag
    that is not an alpha-3 code (`transparent`, blank) gives no country.
    """
    calendars = [item for item in candidates if str(item[1][4]) == "wsdc_calendar"]
    if not calendars:
        return None, None, None
    payload = max(
        calendars,
        key=lambda item: (observation_times.get(str(item[1][2]), ""), str(item[1][2])),
    )[0]
    parts = [part.strip() for part in payload.location_raw.split(",")]
    city = parts[0] or None
    region = (", ".join(parts[1:-1]) or None) if len(parts) > 2 else None
    flag = payload.country_code_raw
    country = flag if isinstance(flag, str) and re.fullmatch(r"[A-Z]{3}", flag) else None
    return city, region if city else None, country


def _calendar_status(payload: CalendarRow) -> str:
    raw = payload.event_type_raw.casefold().replace("-", " ")
    return (
        "trial"
        if "trial" in raw
        else "registry"
        if raw in {"registry", "registry event"}
        else "unknown"
    )


def _status_listings(
    candidates: list[tuple[CalendarRow, sqlite3.Row]],
) -> list[tuple[CalendarRow, sqlite3.Row]]:
    """The listings that speak for an edition's status: the WSDC calendar's,
    else every calendar-family listing. A newsletter is read years after its
    issue, so its observation time says nothing about which listing is newer,
    and it does not print trial or cancelled marks reliably (D-0260)."""
    family = [item for item in candidates if _history_source(str(item[1][4])) != "platform"]
    return [item for item in family if str(item[1][4]) == "wsdc_calendar"] or family


def _trial(payload: CalendarRow) -> bool:
    return "trial" in payload.event_type_raw.casefold() or any(
        "trial" in value.casefold() for value in payload.row_classes
    )


def _cancelled(
    candidates: list[tuple[CalendarRow, sqlite3.Row]], observation_times: dict[str, str]
) -> bool:
    """Whether an edition was cancelled (D-0260).

    A cancel mark on any listing that speaks for the edition cancels it. A
    hiatus mark seen before the edition began cancels it too (`Sea to Sky
    Seattle (Hiatus)`). One seen once it had begun is about later seasons
    (`Swing Niagara Dance Champ's (ON HIATUS)` on 2013-09-21, with the
    2013 dates) and cancels only when every such listing carries one.
    """
    listings = _status_listings(candidates) or candidates

    def marks(payload: CalendarRow, word: str) -> bool:
        return any(word in value.casefold() for value in (payload.name_raw, *payload.row_classes))

    def before_start(payload: CalendarRow, row: sqlite3.Row) -> bool:
        seen = observation_times.get(str(row[2]), "")
        return bool(seen) and seen[:10] < payload.start_date_raw[:10]

    return (
        any(marks(payload, "cancel") for payload, _ in listings)
        or any(marks(payload, "hiatus") and before_start(payload, row) for payload, row in listings)
        or all(marks(payload, "hiatus") for payload, _ in listings)
    )


def _listing_status(
    payload: CalendarRow,
    source: str,
    series: str,
    candidates: list[tuple[CalendarRow, sqlite3.Row]],
    observation_times: dict[str, str],
) -> str:
    """The WSDC status a listing gives its event.

    The listings that speak for the edition (`_status_listings`) print it: a
    trial mark on any of them makes it `trial`, else the newest one's type
    decides (D-0260). A results platform prints none: with no such listing
    its event is `registry` for a registry series and `unknown` for any
    other (D-0253).
    """
    listings = _status_listings(candidates)
    if not listings:
        if _history_source(source) != "platform":
            return _calendar_status(payload)
        return "registry" if series.startswith("wsdc-") else "unknown"
    if any(_trial(item[0]) for item in listings):
        return "trial"
    newest = max(
        listings,
        key=lambda item: (observation_times.get(str(item[1][2]), ""), str(item[1][2])),
    )
    return _calendar_status(newest[0])


def _current_listings(
    listings: dict[tuple[str, str, str, str], tuple[CalendarRow, sqlite3.Row]],
    observation_times: dict[str, str],
) -> list[tuple[CalendarRow, sqlite3.Row]]:
    newest: dict[tuple[str, str, str], tuple[str, str]] = {}
    for payload, row in listings.values():
        key = str(row[4]), series_slug(payload.name_raw), payload.end_date_raw[:7]
        precedence = observation_times.get(str(row[2]), ""), str(row[2])
        newest[key] = max(newest.get(key, ("", "")), precedence)
    return [
        (payload, row)
        for payload, row in listings.values()
        if (observation_times.get(str(row[2]), ""), str(row[2]))
        == newest[str(row[4]), series_slug(payload.name_raw), payload.end_date_raw[:7]]
    ]


def _list_differs(column: str) -> str:
    """SQL: the stored and the new JSON list of ``column`` differ as values.

    A row stored as `["a", "b"]` by the inventory before F28's fix equals
    `["a","b"]`: only a changed value rewrites the row, so a text-only
    difference neither moves the canonical revision nor refreshes the row's
    other columns (its parser version, say) from a newer listing.
    """
    stored, new = f"events.{column}", f"excluded.{column}"
    return (
        f"(CASE WHEN json_valid({stored}) THEN json({stored}) ELSE {stored} END) IS NOT "
        f"(CASE WHEN json_valid({new}) THEN json({new}) ELSE {new} END)"
    )


def _json_list(values: Iterable[str]) -> str:
    """A sorted JSON list in the one text form every writer of `events` uses.

    `project/writer` stores lists compact; this upsert once stored them with
    spaces, so the inventory and a resolution or calendar rewrote each
    other's text. Each inventory re-run with unchanged output then moved the
    canonical revision and replayed every unit behind it (F28).
    """
    return json.dumps(sorted(values), separators=(",", ":"), ensure_ascii=False)


def _inventory_state(conn: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in conn.execute(
            "SELECT event_id,series_id,name,year,start_date,end_date,website,wsdc_status,sources,source,snapshot_id,parser_version,event_month,date_precision,held,coverage_tier,history_source FROM events ORDER BY event_id"
        )
    )


def _inventory_digest(conn: sqlite3.Connection, year: int | None = None) -> str:
    query = (
        "SELECT event_id,series_id,start_date,end_date,event_month,held,date_precision FROM events"
    )
    rows = conn.execute(
        query + (" WHERE year=?" if year is not None else "") + " ORDER BY event_id",
        (year,) if year is not None else (),
    ).fetchall()
    return sha256(json.dumps([tuple(row) for row in rows]).encode()).hexdigest()


AliasMap = dict[str, list[tuple[str, str, str]]]


def dismissed_findings(reviews: bytes) -> set[tuple[str, str]]:
    """(kind, subject_id) pairs a reviewer dismissed in overrides/finding_reviews.csv."""
    return {
        (row["kind"].strip(), row["subject_id"].strip())
        for row in csv.DictReader(io.StringIO(reviews.decode()))
        if row.get("decision", "").strip() == "dismiss"
    }


def _open(
    findings: dict[int, list[Finding]],
    year: int,
    dismissed: set[tuple[str, str]],
    finding: Finding,
) -> None:
    """Record a finding unless a reviewer dismissed this exact subject (D-0187)."""
    if (finding.kind, finding.subject_id) not in dismissed:
        findings.setdefault(year, []).append(finding)


def _alias_map(aliases: bytes) -> AliasMap:
    """Printed-name slug to (from_month, to_month, series_id) rows.

    A row with empty months applies to every month. A row with months applies
    to listings whose reporting month falls inside them, so one printed name
    can point at the registry id that held it in each era (D-0187).
    """
    result: AliasMap = {}
    for row in csv.DictReader(io.StringIO(aliases.decode())):
        result.setdefault(series_slug(row["printed_name"]), []).append(
            (
                (row.get("from_month") or "").strip(),
                (row.get("to_month") or "").strip(),
                row["series_id"].strip(),
            )
        )
    return result


def _alias_for(alias_map: AliasMap, slug: str, month: str) -> str | None:
    """The series for a slug in a month; a dated row beats an undated one."""
    fallback = None
    for start, end, series in alias_map.get(slug, ()):
        if not start and not end:
            fallback = fallback or series
        elif (not start or start <= month) and (not end or month <= end):
            return series
    return fallback


def _widest_span(spans: set[tuple[str, str]]) -> tuple[str, str] | None:
    """The one dated span that contains every other, or None when spans disagree.

    A month-only span (``yyyy-mm`` on both ends, the registry's reporting
    month) belongs to the edition whose dated span starts or ends in that
    month; New Year events that cross a month boundary are the common case.
    """
    dated = {span for span in spans if len(span[0]) == 10 and len(span[1]) == 10}
    months = {span[0] for span in spans if len(span[0]) == 7 and span[0] == span[1]}
    if len(dated) + len(months) != len(spans):
        return None
    if not dated:
        return next(iter(spans)) if len(spans) == 1 else None
    widest = max(dated, key=lambda span: (span[1], "" if span[0] else "~"))
    widest = min((span for span in dated if span[1] == widest[1]), key=lambda span: span[0])
    for start, end in dated:
        if not (widest[0] <= start and end <= widest[1]):
            return None
    if any(month not in {widest[0][:7], widest[1][:7]} for month in months):
        return None
    return widest


_PROJECTION_ANSWERS: ContextVar[dict[int, bool] | None] = ContextVar(
    "projection_answers", default=None
)


@contextmanager
def projection_answer_cached() -> Iterator[None]:
    """Answer the pending-projection question once for a whole pass.

    The question walks every pending derivation unit and costs seconds on a
    large database. A pass over thousands of plan pages, or a review over
    many event sites, asks it through several call paths; inside this
    context every path gets the first answer (D-0195). Nothing inside a pass
    changes the answer, because the pass itself projects nothing. Nested
    passes share the outer answer.
    """
    if _PROJECTION_ANSWERS.get() is not None:
        yield
        return
    token = _PROJECTION_ANSWERS.set({})
    try:
        yield
    finally:
        _PROJECTION_ANSWERS.reset(token)


_FACTS_ONLY: ContextVar[bool] = ContextVar("facts_only_year_gate", default=False)


@contextmanager
def facts_only_year_gate() -> Iterator[None]:
    """Judge the phase-two year gate on facts alone (D-0218).

    Inside this context `pending_inventory_projection` answers False without
    walking the pending derivation units, and `phase_two_allowed` ignores the
    pending question: the gate reads the committed event list, its accepted
    digest, open findings and the H7/H10 switches, all facts (I5). The
    acquire, plan and derive loops run inside it, so no derivation predicate
    runs in acquisition. The cycle and `accept-year` keep the pending check.
    """
    token = _FACTS_ONLY.set(True)
    try:
        yield
    finally:
        _FACTS_ONLY.reset(token)


def facts_only() -> bool:
    """Whether the year gate is judged on facts alone (`facts_only_year_gate`)."""
    return _FACTS_ONLY.get()


def pending_inventory_projection(conn: sqlite3.Connection) -> bool:
    """Whether a pending unit may change the event list; False on facts alone."""
    if facts_only():
        return False
    return projection_pending(conn)


def projection_pending(conn: sqlite3.Connection) -> bool:
    """The pending-projection answer whatever the gate's mode.

    `accept-year` refuses on it, and the plan loop records it as
    information. Inside `projection_answer_cached` it is computed once.
    """
    answers = _PROJECTION_ANSWERS.get()
    if answers is not None:
        key = id(conn)
        if key not in answers:
            answers[key] = _pending_inventory_projection(conn)
        return answers[key]
    return _pending_inventory_projection(conn)


def _pending_inventory_projection(conn: sqlite3.Connection) -> bool:
    """Only known result-only dispatches are proven neutral for every year's inventory.

    process._replace_event projects no Event records, hence writes none of the
    _inventory_digest columns. Its writer can still retire prior scope-owned
    Event rows, so those legacy scopes fail closed. A source_event is neutral
    only when dispatch already has its unambiguous mapping; otherwise it may
    invoke the global map projector. Registry, discovery and unknown units can
    add or move occurrences and remain blocking regardless of year. An
    unmapped source_event still blocks, though it now writes nothing and
    waits for its mapping; a pending mapping or resolution blocks, as the map
    did.

    With derivations, a year unit only runs finalize_year: it writes
    registry_placements.event_id, events.coverage_tier and coverage, none of
    which _inventory_digest reads. Every fetched sheet re-queues its year, so
    letting it block would close the gate that fetches the next sheet (D-0198).
    The legacy history path rebuilds the inventory and still blocks.

    An inventory the pacing gate holds (D-0210), one not current and made
    within its interval, writes nothing until that interval ends, and the
    mappings and resolutions behind it need a current inventory, so none of
    them blocks: the gate reads the committed event list, a fact (I5), and
    the accepted digest still closes a year whose list the paced inventory
    later changes. A current inventory holds nothing, so a pending mapping or
    resolution can run and blocks. Only a run that names its scheduler (schedule/pacing) holds
    one. A unit whose code scope is not accepted counts as pending
    (`derivations.pending_units`) and is judged like any other.
    """
    from swingset.schedule import pacing
    from swingset.state.derivations import available, pending_units

    derived = available(conn)
    held = derived and pacing.holds(conn, pacing.INVENTORY)
    units = (
        ((unit.unit_kind, unit.unit_id) for unit in pending_units(conn, "project"))
        if derived
        else conn.execute("SELECT unit_kind,unit_id FROM pending_work WHERE stage='project'")
    )
    for kind, identifier in units:
        if kind in {"history", "year"} and derived:
            continue
        if held and kind in {"inventory", "map", "mapping", "resolution"}:
            continue
        event_id = identifier
        if kind == "source_event":
            sources = conn.execute(
                "SELECT DISTINCT w.source FROM observations o JOIN watches w USING(watch_id) "
                "WHERE o.scope_kind='source_event' AND o.scope_id=?",
                (identifier,),
            ).fetchall()
            if len(sources) != 1:
                return True
            mapped = conn.execute(
                "SELECT event_id FROM source_event_map WHERE source=? AND source_ref=?",
                (sources[0][0], identifier),
            ).fetchone()
            if mapped is None:
                return True
            event_id = mapped[0]
        elif kind != "event":
            return True
        if not conn.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
            return True
        if conn.execute(
            "SELECT 1 FROM canonical_scope_rows WHERE scope_kind='event' AND scope_id=? AND table_name='events' LIMIT 1",
            (event_id,),
        ).fetchone():
            return True
    return False


def accept_year(
    conn: sqlite3.Connection,
    year: int,
    *,
    accepted_by: str,
    accepted_at: str,
    run_id: str | None = None,
    history_start: date = HISTORY_START,
) -> None:
    """Accept the exact inventory and derive its year in one bounded write.

    With derivations the year's unit, `project/year/<year>`, runs inside its
    own capture and recording, so its acceptance and coverage become one
    generation of that year alone; a caller that set up `chunking` for that
    unit stages its output.
    """
    from swingset.state.derivations import available
    from swingset.state.read_sets import recording
    from swingset.state.write_deadline import bounded_write

    from .materialization import materializing

    at = datetime.fromisoformat(accepted_at)
    if at.tzinfo is None:
        raise ValueError("year acceptance requires a timezone-aware timestamp")
    savepoint = "year_acceptance_" + uuid4().hex
    boundary = nullcontext() if conn.in_transaction else bounded_write(conn)
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        with boundary:
            if not accepted_by.strip():
                raise ValueError("year acceptance requires a named owner")
            # Accepting always asks, even inside the facts-only gate.
            if projection_pending(conn):
                raise ValueError("event-list projection is pending")
            if conn.execute(
                "SELECT 1 FROM findings WHERE owner_kind IN ('history_year','phase1_year') AND owner_id=? AND closed_at IS NULL",
                (str(year),),
            ).fetchone():
                raise ValueError("year has unresolved event-list findings")
            if not conn.execute("SELECT 1 FROM events WHERE year=?", (year,)).fetchone():
                raise ValueError("cannot accept a year without event evidence")
            conn.execute(
                "INSERT OR REPLACE INTO history_acceptance VALUES (?,?,?,?)",
                (year, accepted_at, accepted_by, _inventory_digest(conn, year)),
            )
            if available(conn):
                if run_id is None:
                    run_id = "history_acceptance_" + uuid4().hex
                    conn.execute(
                        "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES (?,?,?,0,?)",
                        (
                            run_id,
                            accepted_at,
                            accepted_at,
                            json.dumps(
                                {
                                    "action": "accept_year",
                                    "year": year,
                                    "accepted_by": accepted_by,
                                },
                                sort_keys=True,
                            ),
                        ),
                    )
                unit = WorkUnit("project", "year", str(year))
                with (
                    materializing(conn, unit, now=accepted_at, run_id=run_id),
                    recording(conn, unit),
                ):
                    finalize_year(conn, year, now=accepted_at, history_start=history_start)
            else:
                rebuild_coverage(conn, now=accepted_at)
    except BaseException:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE {savepoint}")


def phase_two_allowed(conn: sqlite3.Connection, year: int, *, pending: bool | None = None) -> bool:
    """Whether phase-two acquisition may start for ``year``.

    ``pending`` lets a caller that asks about many years answer the
    projection question once; it costs seconds on a large database. Inside
    `facts_only_year_gate` the question is not asked.
    """
    if facts_only():
        pending = False
    elif pending is None:
        pending = pending_inventory_projection(conn)
    if pending:
        return False
    return year_gate_reason(conn, year) is None


#: Why the year gate is closed, in the order `year_gate_reason` reports them.
YEAR_GATE_REASONS = (
    "year_unaccepted",
    "acceptance_digest_changed",
    "year_findings_open",
    "h7_h10_not_deployed",
)


def year_gate_reason(conn: sqlite3.Connection, year: int) -> str | None:
    """The first fact that closes phase two for ``year``, or None when it is open.

    Facts only: acceptance, the accepted digest against the committed event
    list, open event-list findings, and the H7 and H10 switches. The pending
    projection is the caller's question (`phase_two_allowed`).
    """
    accepted = conn.execute(
        "SELECT inventory_digest FROM history_acceptance WHERE year=?", (year,)
    ).fetchone()
    if not accepted:
        return "year_unaccepted"
    if accepted[0] != _inventory_digest(conn, year):
        return "acceptance_digest_changed"
    if conn.execute(
        "SELECT 1 FROM findings WHERE owner_kind IN ('history_year','phase1_year') AND owner_id=? AND closed_at IS NULL",
        (str(year),),
    ).fetchone():
        return "year_findings_open"
    deployed = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT key,value FROM meta WHERE key IN ('h7_deployed','h10_deployed')"
        )
    }
    if deployed.get("h7_deployed") != "true" or deployed.get("h10_deployed") != "true":
        return "h7_h10_not_deployed"
    return None


def rebuild_coverage(conn: sqlite3.Connection, *, now: str) -> None:
    from .coverage import rebuild_coverage as rebuild

    rebuild(conn, now=now)
