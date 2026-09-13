"""Bounded CDX discovery and immutable capture selection; HTTP uses FetchClient's gate."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
from urllib.parse import urlencode, urlsplit

from swingset.sources.base import ExtractError


@dataclass(frozen=True)
class Capture:
    url: str
    timestamp: str
    digest: str
    mimetype: str
    length: int
    status: int = 200

    @property
    def archive_url(self) -> str:
        return replay_url(self.url, self.timestamp)


def replay_url(url: str, timestamp: str) -> str:
    if not re.fullmatch(r"\d{14}", timestamp):
        raise ValueError("capture timestamp must have fourteen digits")
    datetime.strptime(timestamp, "%Y%m%d%H%M%S")
    if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).hostname:
        raise ValueError("capture original URL must be HTTP(S)")
    return f"https://web.archive.org/web/{timestamp}id_/{url}"


def captured_at(headers: dict[str, str], archive_url: str) -> str:
    """Prefer final Memento time; redirect URL is a fallback for missing headers."""
    normalized = {key.lower(): value for key, value in headers.items()}
    if normalized.get("memento-datetime"):
        moment = parsedate_to_datetime(normalized["memento-datetime"])
        if moment.tzinfo is None:
            raise ValueError("Memento timestamp must include timezone")
    else:
        match = re.search(r"/web/(\d{14})", archive_url)
        if match is None:
            raise ValueError("archive response lacks a capture timestamp")
        moment = datetime.strptime(match[1], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def cdx_url(prefix: str, year: int, *, page: int | None = None) -> str:
    if not 1996 <= year <= 9999 or (page is not None and page < 0):
        raise ValueError("invalid CDX year or page")
    params: dict[str, str | int] = {
        "url": prefix,
        "output": "json",
        "filter": "statuscode:200",
        "fl": "timestamp,original,digest,mimetype,length",
        "from": year,
        "to": year,
        "collapse": "digest",
    }
    if page is None:
        # The special count response is a number; capture field projection
        # turns it into an unusable all-null CDX row on the live service.
        params.pop("fl")
        params.pop("output")
        params["showNumPages"] = "true"
    else:
        params["page"] = page
    return "https://web.archive.org/cdx/search/cdx?" + urlencode(params)


def parse_cdx(body: bytes) -> tuple[Capture, ...]:
    try:
        rows = json.loads(body)
        if not isinstance(rows, list):
            raise ValueError("CDX response must be an array")
        if not rows:
            return ()
        header = rows[0]
        if not isinstance(header, list) or not {
            "timestamp",
            "original",
            "digest",
            "mimetype",
            "length",
        } <= set(header):
            raise ValueError("CDX response has invalid header")
        result = []
        for values in rows[1:]:
            row = dict(zip(header, values, strict=True))
            capture = Capture(
                str(row["original"]),
                str(row["timestamp"]),
                str(row["digest"]),
                str(row["mimetype"]),
                int(row["length"]),
                int(row.get("statuscode", 200)),
            )
            replay_url(capture.url, capture.timestamp)
            if capture.length < 0:
                raise ValueError("negative capture length")
            result.append(capture)
        return tuple(result)
    except (ValueError, TypeError, KeyError) as exc:
        raise ExtractError(f"invalid CDX response: {exc}") from exc


def select_captures(captures: tuple[Capture, ...], event_end: date) -> tuple[Capture, ...]:
    """At most three distinct successful bodies, corrected captures first."""
    cutoff = (event_end + timedelta(days=30)).strftime("%Y%m%d") + "000000"
    candidates = sorted(
        (c for c in captures if c.status == 200),
        key=lambda c: (c.timestamp >= cutoff, c.timestamp),
        reverse=True,
    )
    selected: list[Capture] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.digest in seen:
            continue
        seen.add(candidate.digest)
        selected.append(candidate)
        if len(selected) == 3:
            break
    return tuple(selected)


def store_cdx_page(
    conn: sqlite3.Connection,
    *,
    source: str,
    prefix: str,
    year: int,
    page: int,
    total_pages: int,
    body: bytes,
    queried_at: str,
    query_id: str | None = None,
) -> str:
    """Persist one page and its resume cursor atomically under the caller's transaction."""
    if not 0 <= page < total_pages:
        raise ValueError("CDX page outside declared range")
    captures = parse_cdx(body)
    query_id = query_id or sha256(f"{source}\0{prefix}\0{year}".encode()).hexdigest()
    existing = conn.execute(
        "SELECT next_page FROM archive_queries WHERE query_id=?", (query_id,)
    ).fetchone()
    if page != (int(existing[0]) if existing else 0):
        raise ValueError("CDX pages must be accepted in order")
    conn.executemany(
        "INSERT OR IGNORE INTO archive_captures(source,url,timestamp,digest,status,mimetype,length,queried_at,cdx_query_id) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                source,
                c.url,
                c.timestamp,
                c.digest,
                c.status,
                c.mimetype,
                c.length,
                queried_at,
                query_id,
            )
            for c in captures
        ],
    )
    conn.execute(
        "INSERT INTO archive_queries(query_id,source,prefix,year,next_page,total_pages,completed_at,started_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(query_id) DO UPDATE SET next_page=excluded.next_page,total_pages=excluded.total_pages,completed_at=excluded.completed_at",
        (
            query_id,
            source,
            prefix,
            year,
            page + 1,
            total_pages,
            queried_at if page + 1 == total_pages else None,
            queried_at,
        ),
    )
    return query_id


def query_due(year: int, completed_at: str | None, now: datetime) -> bool:
    if completed_at is None:
        return True
    return year >= now.year - 1 and now - datetime.fromisoformat(completed_at) >= timedelta(days=90)


def schedule_capture(conn: sqlite3.Connection, spec: object, *, now: datetime) -> bool:
    """Select another immutable capture on the original URL's one watch.

    Index callers can walk every capture oldest first; score-sheet callers must
    pass the year/deployment gates enforced by watch insertion.
    """
    from swingset.history.acquisition import is_phase_one_index
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import WatchSpec

    if not isinstance(spec, WatchSpec) or not spec.archive_url:
        raise ValueError("capture scheduling requires an archive watch specification")
    if conn.execute(
        "SELECT 1 FROM snapshots WHERE url=? AND (archive_url=? OR requested_archive_url=?) AND http_status=200 AND classification='Ok'",
        (spec.url, spec.archive_url, spec.archive_url),
    ).fetchone():
        return False
    upsert_watch(conn, spec, now)
    conn.execute(
        "UPDATE watches SET archive_url=?,state='backfill',next_check_at=?,priority=? WHERE watch_id=?",
        (
            spec.archive_url,
            now.isoformat(),
            2 if is_phase_one_index(spec.source, spec.parser, spec.kind) else 6,
            spec.watch_id,
        ),
    )
    return True


def retry_capture(conn: sqlite3.Connection, watch_id: str, *, now: datetime) -> bool:
    """After a sheet parse failure, try the next distinct capture (three total)."""
    import calendar

    from swingset.history.acquisition import is_phase_one_index, phase_two_gate
    from swingset.sources.base import WatchSpec

    watch = conn.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
    if (
        watch is None
        or not watch["archive_url"]
        or is_phase_one_index(str(watch["source"]), str(watch["parser"]), str(watch["kind"]))
    ):
        return False
    event = conn.execute(
        "SELECT e.end_date,e.event_month,e.year FROM source_event_map m JOIN events e USING(event_id) WHERE m.source=? AND m.source_ref=?",
        (watch["source"], watch["source_ref"]),
    ).fetchone()
    if event is None or phase_two_gate(
        conn,
        source=str(watch["source"]),
        source_ref=watch["source_ref"],
        page_kind=str(watch["parser"]),
    ):
        return False
    failed = conn.execute(
        "SELECT COUNT(DISTINCT body_sha256) FROM snapshots WHERE watch_id=? AND via='wayback' AND http_status=200 AND classification='Ok' AND parse_status='failed'",
        (watch_id,),
    ).fetchone()[0]
    if failed >= 3:
        return False
    if event[0]:
        end = date.fromisoformat(event[0])
    else:
        year, month = map(int, str(event[1]).split("-"))
        end = date(year, month, calendar.monthrange(year, month)[1])
    captures = tuple(
        Capture(str(row[0]), str(row[1]), str(row[2]), str(row[3]), int(row[4]), int(row[5]))
        for row in conn.execute(
            "SELECT url,timestamp,digest,mimetype,length,status FROM archive_captures WHERE source=? AND url=?",
            (watch["source"], watch["url"]),
        )
    )
    for capture in select_captures(captures, end):
        if conn.execute(
            "SELECT 1 FROM snapshots WHERE watch_id=? AND (archive_url=? OR requested_archive_url=?) AND http_status=200 AND classification='Ok'",
            (watch_id, capture.archive_url, capture.archive_url),
        ).fetchone():
            continue
        return schedule_capture(
            conn,
            WatchSpec(
                watch_id,
                str(watch["source"]),
                str(watch["kind"]),
                "GET",
                str(watch["url"]),
                str(watch["parser"]),
                source_ref=watch["source_ref"],
                archive_url=capture.archive_url,
            ),
            now=now,
        )
    return False
