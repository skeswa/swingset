"""Calendar and source-index projections."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from swingset.model.canonical import Event
from swingset.model.ids import event_id, series_id
from swingset.model.observations import decode_payload
from swingset.sources.records import CalendarRow, SourceEventRow
from swingset.state.work import WorkUnit, bump_revision, enqueue

from .writer import Projection


@dataclass(frozen=True, slots=True)
class SourceEventEvidence:
    payload: SourceEventRow
    source: str
    scope_id: str
    snapshot_id: str
    parser_version: str
    fetched_at: str
    parser: str

    @property
    def precedence(self) -> tuple[int, str, str]:
        specificity = (
            3
            if self.parser == "event" or self.parser.endswith(".event")
            else 2
            if self.parser == "recent" or self.parser.endswith(".recent")
            else 1
        )
        return specificity, self.fetched_at, self.snapshot_id


def project_calendar(conn: sqlite3.Connection, scope_id: str, now: str, run_id: str) -> Projection:
    current: dict[str, tuple[CalendarRow, sqlite3.Row]] = {}
    for row in conn.execute(
        """SELECT o.kind,o.payload_json,o.snapshot_id,o.parser_version,w.source,s.fetched_at
        FROM observations o JOIN watches w USING(watch_id) JOIN snapshots s USING(snapshot_id)
        WHERE o.scope_kind='calendar' AND o.scope_id=? ORDER BY s.fetched_at,s.snapshot_id,o.seq""",
        (scope_id,),
    ):
        payload = decode_payload(str(row[0]), str(row[1]))
        if isinstance(payload, CalendarRow):
            end = parse_date(payload.end_date_raw)
            current[f"{end:%Y-%m}:{payload.name_raw.casefold()}"] = payload, row
    occurrences: dict[str, int] = {}
    result: list[Event] = []
    for payload, row in current.values():
        start, end = parse_date(payload.start_date_raw), parse_date(payload.end_date_raw)
        base = f"{end:%Y-%m}:{payload.name_raw.casefold()}"
        occurrences[base] = occurrences.get(base, 0) + 1
        location = [part.strip() for part in payload.location_raw.split(",")]
        city = (location[0] or None) if location else None
        region = (", ".join(location[1:-1]) or None) if len(location) > 2 else None
        status = (
            "trial"
            if "trial" in payload.event_type_raw.casefold()
            or any("trial" in item.casefold() for item in payload.row_classes)
            else "registry"
        )
        result.append(
            Event(
                event_id=event_id(end, payload.name_raw, occurrences[base]),
                series_id=series_id(payload.name_raw),
                name=payload.name_raw,
                year=end.year,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                city=city,
                region=region,
                country=payload.country_code_raw,
                website=payload.website,
                wsdc_status=status,
                sources=(str(row[4]),),
                live_window_start=(
                    datetime.combine(start, datetime.min.time(), UTC) - timedelta(hours=36)
                ).isoformat(),
                live_window_end=(
                    datetime.combine(end, datetime.min.time(), UTC) + timedelta(hours=48)
                ).isoformat(),
                source=str(row[4]),
                snapshot_id=str(row[2]),
                parser_version=str(row[3]),
                first_seen_at=now,
                last_seen_at=now,
                run_id=run_id,
            )
        )
    return Projection(tuple(result))


def project_source_index(conn: sqlite3.Connection, scope_id: str, now: str, run_id: str) -> bool:
    """Reconcile one owner's membership, then select facts across all owners."""
    evidence = _source_event_evidence(conn)
    desired_keys = {
        (item.source, item.payload.source_ref) for item in evidence if item.scope_id == scope_id
    }
    old_keys = {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT source,source_ref FROM source_event_scope_rows WHERE scope_id=?", (scope_id,)
        )
    }
    for key in old_keys - desired_keys:
        conn.execute(
            "DELETE FROM source_event_scope_rows WHERE scope_id=? AND source=? AND source_ref=?",
            (scope_id, *key),
        )
    for key in desired_keys:
        conn.execute(
            "INSERT OR IGNORE INTO source_event_scope_rows(scope_id,source,source_ref) VALUES (?,?,?)",
            (scope_id, *key),
        )
    affected = old_keys | desired_keys
    changed = False
    for key in affected:
        candidates = [item for item in evidence if (item.source, item.payload.source_ref) == key]
        if not candidates:
            if conn.execute(
                "DELETE FROM source_events WHERE source=? AND source_ref=?", key
            ).rowcount:
                changed = True
            continue
        winner = max(candidates, key=lambda item: item.precedence)
        payload = winner.payload
        parsed = nullable_date(payload.date_raw)
        values = (
            winner.source,
            payload.source_ref,
            payload.name_raw,
            parsed,
            parsed,
            None,
            payload.url,
            winner.snapshot_id,
            winner.parser_version,
            now,
            now,
            run_id,
        )
        old = conn.execute(
            "SELECT name_raw,start_date,end_date,location_raw,url,snapshot_id,parser_version FROM source_events WHERE source=? AND source_ref=?",
            key,
        ).fetchone()
        if old is not None and tuple(old) == values[2:9]:
            continue
        conn.execute(
            """INSERT INTO source_events(source,source_ref,name_raw,start_date,end_date,location_raw,url,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_ref) DO UPDATE SET name_raw=excluded.name_raw,start_date=excluded.start_date,end_date=excluded.end_date,location_raw=excluded.location_raw,url=excluded.url,snapshot_id=excluded.snapshot_id,parser_version=excluded.parser_version,last_seen_at=excluded.last_seen_at,run_id=excluded.run_id""",
            values,
        )
        changed = True
    if changed:
        bump_revision(conn, "source_events")
        enqueue(conn, (WorkUnit("project", "map", "all"),), enqueued_at=now)
    return changed


def _source_event_evidence(conn: sqlite3.Connection) -> list[SourceEventEvidence]:
    result: list[SourceEventEvidence] = []
    for row in conn.execute("""SELECT o.kind,o.payload_json,o.scope_id,o.snapshot_id,o.parser_version,w.source,s.fetched_at,w.parser
        FROM observations o JOIN watches w USING(watch_id) JOIN snapshots s USING(snapshot_id)
        WHERE o.scope_kind='source_index' ORDER BY s.fetched_at,s.snapshot_id,o.seq"""):
        payload = decode_payload(str(row[0]), str(row[1]))
        if isinstance(payload, SourceEventRow):
            result.append(
                SourceEventEvidence(
                    payload,
                    str(row[5]),
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[6]),
                    str(row[7]),
                )
            )
    return result


def parse_date(raw: str) -> date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognized source date: {raw}")


def nullable_date(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    try:
        return parse_date(raw).isoformat()
    except ValueError:
        return None
