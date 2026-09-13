"""Pure platform inventory planning; URLs never create event identities or controls."""

from __future__ import annotations

import calendar
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from swingset.fetch.wayback import Capture, select_captures
from swingset.state.findings import Finding

PLATFORMS = frozenset({"eepro", "scoringdance", "dcn", "wdr"})


@dataclass(frozen=True)
class KnownEvent:
    event_id: str
    source: str
    source_ref: str
    year: int
    event_month: str
    end_date: date | None = None

    @property
    def last_day(self) -> date:
        year, month = map(int, self.event_month.split("-"))
        return self.end_date or date(year, month, calendar.monthrange(year, month)[1])


@dataclass(frozen=True)
class Page:
    source: str
    source_ref: str
    url: str
    page_kind: str
    kind: str
    parent_url: str | None = None


@dataclass(frozen=True)
class PlannedPage:
    event: KnownEvent
    page: Page
    captures: tuple[Capture, ...]


@dataclass(frozen=True)
class PlatformPlan:
    pages: tuple[PlannedPage, ...]
    findings: tuple[Finding, ...]


def recognize(source: str, url: str) -> Page | None:
    """Recognize documented platform paths only; a recognizable slug is not a mapping."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.query or parsed.fragment:
        return None
    if source == "eepro" and parsed.hostname in {"eepro.com", "www.eepro.com"}:
        match = re.fullmatch(r"/results/([^/]+)/(.*)", parsed.path)
        if match:
            slug, filename = match.groups()
            parent = url[: len(url) - len(filename)] if filename else url
            if not filename:
                return Page(source, f"eepro:{slug}", url, "eepro.autoindex", "autoindex")
            if filename.lower().endswith((".html", ".htm")) and "/" not in filename:
                return Page(source, f"eepro:{slug}", url, "eepro.round", "round", parent)
    if source == "scoringdance" and parsed.hostname == "scoring.dance":
        match = re.fullmatch(r"/[^/]+/events/(\d+)/results/", parsed.path)
        if match:
            return Page(source, f"scoringdance:{match[1]}", url, "scoringdance.event", "event")
        # Round IDs alone do not identify their event. An accepted event document must
        # supply that relation through declared_pages instead of guessing it here.
    if source == "wdr" and parsed.hostname == "scores.worlddanceregistry.com":
        from swingset.sources.wdr.adapter import source_ref_from_url

        try:
            ref = source_ref_from_url(url)
        except ValueError:
            return None
        if parsed.path.endswith("/rounds/routeInfo.json"):
            return Page(source, ref, url, "wdr.rounds", "event")
    if source == "dcn" and parsed.hostname == "danceconvention.net":
        match = re.fullmatch(
            r"/eventdirector/[^/]+/eventpage/(\d+)(?:-[^/]+)?/results", parsed.path
        )
        if match:
            return Page(source, f"dcn:{match[1]}", url, "dcn.event", "event")
    return None


def plan_platform(
    events: tuple[KnownEvent, ...],
    captures: tuple[tuple[str, Capture], ...],
    *,
    declared_pages: tuple[Page, ...] = (),
    history_start: date = date(2010, 1, 1),
) -> PlatformPlan:
    """Newest year, richest source, all event documents before their round documents."""
    bindings = {(event.source, event.source_ref): event for event in events}
    pages = {(page.source, page.url): page for page in declared_pages}
    grouped: dict[tuple[str, str], list[Capture]] = defaultdict(list)
    for source, capture in captures:
        if (
            source not in PLATFORMS
            or capture.status != 200
            or capture.mimetype
            not in {
                "text/html",
                "application/xhtml+xml",
                "application/pdf",
                "application/json",
                "text/json",
            }
        ):
            continue
        grouped[source, capture.url].append(capture)
        if page := recognize(source, capture.url):
            pages.setdefault((source, capture.url), page)
    findings = []
    planned = []
    for key, page in sorted(pages.items()):
        event = bindings.get((page.source, page.source_ref))
        if event is None:
            findings.append(
                Finding(
                    "history_unmapped_sheet",
                    "source_event",
                    page.source_ref,
                    "warning",
                    "Archived platform evidence has no accepted event mapping",
                    {"source": page.source, "url": page.url, "source_ref": page.source_ref},
                )
            )
            continue
        if event.last_day < history_start:
            continue
        available = select_captures(tuple(grouped.get(key, ())), event.last_day)
        if not available:
            findings.append(
                Finding(
                    "history_archive_gap",
                    "event",
                    event.event_id,
                    "warning",
                    "Advertised platform page has no retained archive capture",
                    {"source": page.source, "url": page.url, "year": event.year},
                )
            )
        planned.append(PlannedPage(event, page, available))
    # Captured sheets without a declared event relationship remain explicit review work.
    for (source, url), values in grouped.items():
        if (source, url) not in pages and any(
            word in urlsplit(url).path.lower()
            for word in ("round", "result", "score", "final", "prelim")
        ):
            findings.append(
                Finding(
                    "history_unmapped_sheet",
                    "source_url",
                    url,
                    "warning",
                    "Archived sheet locator needs a platform event relationship",
                    {"source": source, "url": url, "captures": len(values)},
                )
            )
    breadth = Counter(
        (year, source)
        for year, source, _ in {
            (item.event.year, item.event.source, item.event.event_id)
            for item in planned
            if item.captures
        }
    )
    planned.sort(
        key=lambda item: (
            -item.event.year,
            -breadth[item.event.year, item.event.source],
            item.event.source,
            item.page.kind == "round",
            item.event.event_id,
            item.page.url,
        )
    )
    return PlatformPlan(tuple(planned), tuple(findings))


def retained_plan(
    conn: sqlite3.Connection, *, history_start: date = date(2010, 1, 1)
) -> PlatformPlan:
    """Read the retained inventory and selected parent declarations; no watches are inserted."""
    from swingset.admission.generations import deserialize_result

    events = tuple(
        KnownEvent(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            int(row[3]),
            str(row[4]),
            date.fromisoformat(row[5]) if row[5] else None,
        )
        for row in conn.execute(
            "SELECT e.event_id,m.source,m.source_ref,e.year,e.event_month,e.end_date FROM source_event_map m JOIN events e USING(event_id)"
        )
        if row[1] in PLATFORMS
    )
    captures = tuple(
        (
            str(row[0]),
            Capture(
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                int(row[5]),
                int(row[6]),
            ),
        )
        for row in conn.execute(
            "SELECT source,url,timestamp,digest,mimetype,length,status FROM archive_captures"
        )
        if row[0] in PLATFORMS
    )
    declarations: list[Page] = []
    for row in conn.execute("SELECT source,url FROM source_events"):
        if row[0] in PLATFORMS and (page := recognize(str(row[0]), str(row[1]))):
            declarations.append(page)
    for row in conn.execute(
        "SELECT g.result_json,w.url,w.source FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id JOIN watches w ON w.watch_id=u.watch_id WHERE g.state='accepted'"
    ):
        if row[2] not in PLATFORMS:
            continue
        if parent := recognize(str(row[2]), str(row[1])):
            declarations.append(parent)
        result = deserialize_result(str(row[0]))
        declarations.extend(
            Page(
                watch.source,
                watch.source_ref or "",
                watch.url,
                watch.parser,
                watch.kind,
                str(row[1]) if watch.kind == "round" else None,
            )
            for watch in result.watches
            if watch.source in PLATFORMS
        )
    return plan_platform(
        events, captures, declared_pages=tuple(declarations), history_start=history_start
    )
