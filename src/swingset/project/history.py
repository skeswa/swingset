"""Registry occurrence inventory, conservative listing reconciliation, and year gates."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import date, datetime
from hashlib import sha256
from uuid import uuid4

from swingset.model.history import HISTORY_START
from swingset.model.ids import series_slug
from swingset.model.observations import decode_payload
from swingset.sources.records import CalendarRow
from swingset.state.findings import Finding, replace_findings
from swingset.state.work import WorkUnit, bump_revision, enqueue

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
            "SELECT event_id FROM events WHERE series_id=? AND event_month=?", (series, month)
        ).fetchall()
        if not existing:
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
                continue
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
        payload = CalendarRow(
            "calendar_row",
            str(row[0]),
            str(row[1]),
            str(row[5]),
            "registry",
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
    for payload, row in _current_listings(listings, observation_times):
        month = payload.end_date_raw[:7]
        year = int(month[:4])
        normalized = series_slug(payload.name_raw)
        aliased = _alias_for(alias_map, normalized, month)
        candidates = {aliased} if aliased else names.get(normalized, set())
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
        matches = list(
            conn.execute(
                "SELECT event_id FROM events WHERE series_id=? AND event_month IN (?,?) AND held='held'",
                (series, month, _next_month(month)),
            )
        )
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
                    {"year": year, "event_ids": [r[0] for r in matches]},
                ),
            )
            continue
        event_id = str(matches[0][0]) if matches else f"{month}-{normalized}"
        resolved.setdefault(event_id, []).append((payload, row))
    for event_id, dated_candidates in resolved.items():
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
        series = _alias_for(alias_map, normalized, payload.end_date_raw[:7]) or next(
            iter(names[normalized])
        )
        source = str(row[4])
        previous = conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        sources = {str(evidence[4]) for _, evidence in dated_candidates}
        histories = {_history_source(item) for item in sources}
        if previous is not None:
            sources.update(json.loads(previous["sources"]))
            histories.update(json.loads(previous["history_source"]))
            if previous["held"] == "held":
                histories.add("registry")
        cancelled = any(
            "cancel" in value.casefold() or "hiatus" in value.casefold()
            for value in (payload.name_raw, *payload.row_classes)
        )
        status_raw = payload.event_type_raw.casefold().replace("-", " ")
        status = (
            "trial"
            if "trial" in status_raw
            else "registry"
            if status_raw in {"registry", "registry event"}
            else "unknown"
        )
        month = payload.end_date_raw[:7]
        precision = "day" if len(payload.end_date_raw) == 10 else "month"
        start = payload.start_date_raw if precision == "day" else None
        end = payload.end_date_raw if precision == "day" else None
        conn.execute(
            """INSERT INTO events(event_id,series_id,name,year,start_date,end_date,website,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id,event_month,date_precision,held,coverage_tier,history_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET wsdc_status=CASE WHEN events.held='held' THEN events.wsdc_status ELSE excluded.wsdc_status END,source=excluded.source,snapshot_id=excluded.snapshot_id,parser_version=excluded.parser_version,sources=excluded.sources,last_seen_at=excluded.last_seen_at,run_id=excluded.run_id,start_date=excluded.start_date,end_date=excluded.end_date,year=excluded.year,website=COALESCE(excluded.website,events.website),date_precision=excluded.date_precision,coverage_tier=CASE WHEN events.coverage_tier='registry_only' THEN 'index_only' ELSE events.coverage_tier END,history_source=excluded.history_source,held=CASE WHEN events.held='held' THEN 'held' ELSE excluded.held END WHERE events.start_date IS NOT excluded.start_date OR events.end_date IS NOT excluded.end_date OR events.source IS NOT excluded.source OR events.snapshot_id IS NOT excluded.snapshot_id OR events.history_source IS NOT excluded.history_source OR events.sources IS NOT excluded.sources OR events.date_precision IS NOT excluded.date_precision OR (events.held!='held' AND events.wsdc_status IS NOT excluded.wsdc_status)""",
            (
                event_id,
                series,
                payload.name_raw,
                year,
                start,
                end,
                payload.website,
                status,
                json.dumps(sorted(sources)),
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
                json.dumps(sorted(histories)),
            ),
        )
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
    # Rewrite only associations that change. Rewriting all ~200k rows each run
    # fired the history fence trigger per row inside the 45-second write
    # bound (D-0199).
    association = (
        "(SELECT e.event_id FROM events e WHERE e.series_id=registry_placements.series_id "
        "AND e.event_month=substr(registry_placements.event_month,1,7) "
        "ORDER BY e.event_id LIMIT 1)"
    )
    conn.execute(
        f"UPDATE registry_placements SET event_id={association} "
        f"WHERE event_month>=? AND event_id IS NOT {association}",
        (history_start.strftime("%Y-%m"),),
    )
    rebuild_coverage(conn, now=now)


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
    add or move occurrences and remain blocking regardless of year.

    With derivations, the history unit only runs finalize_history: it writes
    registry_placements.event_id, events.coverage_tier and coverage, none of
    which _inventory_digest reads. Every fetched sheet re-queues it, so letting
    it block would close the gate that fetches the next sheet (D-0198). The
    legacy path rebuilds the inventory and still blocks.

    An inventory the pacing gate holds (D-0210), one not current and made
    within its interval, writes nothing until that interval ends, and the map
    behind it needs a current inventory, so neither blocks: the gate reads
    the committed event list, a fact (I5), and the accepted digest still
    closes a year whose list the paced inventory later changes. A current inventory holds nothing, so a pending map can run
    and blocks. Only a run that names its scheduler (schedule/pacing) holds
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
        if kind == "history" and derived:
            continue
        if held and kind in {"inventory", "map"}:
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
) -> None:
    """Accept the exact inventory and its coverage output in one bounded write."""
    from swingset.state.derivations import available
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
                with materializing(
                    conn, WorkUnit("project", "history", "all"), now=accepted_at, run_id=run_id
                ):
                    rebuild_coverage(conn, now=accepted_at)
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
