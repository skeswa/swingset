"""Print results-platform links found on the websites of held events without results.

WDR has no index, and no loop scans event sites (D-0256), so a WSDC event
whose results sit on `scores.worlddanceregistry.com` stays uncovered until
someone adds its `overrides/source_urls.csv` row. This tool finds those rows
for events that are already over.

It opens a state database read-only and picks held `wsdc-*` events with a
calendar website that ended inside `--from`/`--to`, have no contest and no
`source_urls.csv` row (or the events named with `--event`). For each site it
reads robots.txt, then the home page, then at most `--pages` pages on the same
site whose link text or address mentions results or scores. It never
requests a results platform, an image, a script, a style sheet or a PDF; it
reports their links instead.

Fetching follows `docs/reference/fetching.md`: the project User-Agent, one
request at a time, at least `--gap` seconds (never under 2) between requests
to a host, or the robots.txt `Crawl-delay` if larger, `Accept-Encoding:
gzip`, no cookies, a 30 s timeout. A 4xx robots.txt allows all; a failed one
skips the host. A 403, 429 or 5xx stops that host for the run.

Each output line has `event_id`, `website`, `requests`, `pages`, `wdr`
(UUID to the pages that link it), `wdr_made` (the day each UUID was made),
`platform_links`, `result_pages` (same-site result pages and documents not
read, robots.txt-disallowed ones included) and `source_urls_csv`. A draft
row needs exactly one WDR id made in the 400 days before the edition ended:
results pages often still link an earlier edition (`draft_refused` says why
not). A reviewer opens the WDR page, checks its name and dates against the
event, then copies the row into `overrides/source_urls.csv`.

    python -m journal.tools.collection.event_site_links --state <db or copy> \\
        [--from 2026-01-01] [--to 2026-10-15] [--event <id> ...] [--limit 20] \\
        [--pages 2] [--gap 5] [--max-requests 20] \\
        [--overrides overrides/source_urls.csv] [--out links.jsonl] [--list]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from protego import Protego

from journal.tools.collection.event_site_review import open_state, read_overrides
from swingset.fetch.client import USER_AGENT

FLOOR_SECONDS = 2.0
MAX_BODY_BYTES = 2 * 1024 * 1024
TIMEOUT = httpx.Timeout(30.0)
WDR_HOST = "scores.worlddanceregistry.com"
UUID = re.compile(r"^/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)")
#: Hosts the tool reports and never requests: results platforms and the
#: registry, whose own rules belong to their adapters.
PLATFORMS = {
    "scores.worlddanceregistry.com": "wdr",
    "worlddanceregistry.com": "wdr",
    "scoring.dance": "scoringdance",
    "eepro.com": "eepro",
    "danceconvention.net": "danceconvention",
    "steprightsolutions.com": "steprightsolutions",
    "drive.google.com": "drive",
    "worldsdc.com": "wsdc",
}
#: How long before an edition ends its WDR event may be made.
WDR_LEAD_DAYS = 400
REDIRECTS = frozenset({301, 302, 303, 307, 308})
RESULT_WORDS = re.compile(r"result|score|callback|placement|winner", re.IGNORECASE)
ASSET = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|ico|css|js|mjs|woff2?|ttf|otf|eot|mp4|mov|mp3|zip)$",
    re.IGNORECASE,
)


def platform_of(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").casefold()
    for name, label in PLATFORMS.items():
        if host == name or host.endswith("." + name):
            return label
    return None


def wdr_uuid(url: str) -> str | None:
    parts = urlsplit(url)
    if (parts.hostname or "").casefold() != WDR_HOST:
        return None
    match = UUID.match(parts.path)
    return match.group(1) if match else None


def uuid_date(uuid: str) -> str | None:
    """The day a version-1 UUID was made (WDR ids are), to check it against the event."""
    parts = uuid.split("-")
    if len(parts) != 5 or not parts[2].startswith("1"):
        return None
    ticks = (int(parts[2][1:], 16) << 48) | (int(parts[1], 16) << 32) | int(parts[0], 16)
    return (date(1582, 10, 15) + timedelta(microseconds=ticks // 10)).isoformat()


def same_site(a: str, b: str) -> bool:
    def bare(url: str) -> str:
        return (urlsplit(url).hostname or "").casefold().removeprefix("www.")

    return bare(a) == bare(b) != ""


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def anchors(base: str, body: str) -> list[tuple[str, str]]:
    """Absolute http(s) links of a page and their text, without fragments."""
    parser = _Anchors()
    parser.feed(body)
    result = []
    for href, text in parser.links:
        url = urldefrag(urljoin(base, href.strip()))[0]
        if urlsplit(url).scheme in {"http", "https"}:
            result.append((url, text))
    return result


@dataclass
class Fetcher:
    """One request at a time, robots.txt first, a gap per host and a budget."""

    gap: float = 5.0
    max_requests: int = 20
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    requests: list[str] = field(default_factory=list)
    _robots: dict[str, tuple[Protego | None, float]] = field(default_factory=dict)
    _last: dict[str, float] = field(default_factory=dict)
    _stopped: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.gap = max(self.gap, FLOOR_SECONDS)
        self._client = httpx.Client(
            transport=self.transport,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, url: str) -> tuple[int, str, str | None]:
        """GET within the budget and gap; (status, text, Location)."""
        if len(self.requests) >= self.max_requests:
            raise BudgetSpent
        host = urlsplit(url).hostname or ""
        wait = self._last.get(host, -1e9) + self._delay(host) - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.requests.append(url)
        try:
            with self._client.stream("GET", url) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BODY_BYTES:
                        break
                    chunks.append(chunk)
                body = b"".join(chunks)
                status, location = response.status_code, response.headers.get("location")
                encoding = response.encoding or "utf-8"
        except httpx.HTTPError:
            status, body, location, encoding = 599, b"", None, "utf-8"
        finally:
            self._client.cookies.clear()
            self._last[host] = self.clock()
        if status == 403 or status == 429 or status >= 500:
            self._stopped.add(host)
        return status, body.decode(encoding, "replace"), location

    def _delay(self, host: str) -> float:
        robots = self._robots.get(host)
        return max(self.gap, robots[1] if robots else 0.0)

    def allowed(self, url: str) -> bool:
        """Read and keep the host's robots.txt; False when it forbids or failed."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host in self._stopped:
            return False
        if host not in self._robots:
            robots = f"{parts.scheme}://{parts.netloc}/robots.txt"
            status, text, location = self._request(robots)
            for _ in range(3):
                # RFC 9309 follows redirects; the file found governs this host.
                if status not in REDIRECTS or not location:
                    break
                robots = urljoin(robots, location)
                if platform_of(robots):
                    break
                status, text, location = self._request(robots)
            if status == 200:
                parsed = Protego.parse(text)
                delay = parsed.crawl_delay(USER_AGENT) or 0
                self._robots[host] = (parsed, float(delay))
            elif 400 <= status < 500:
                self._robots[host] = (None, 0.0)
            else:
                self._stopped.add(host)
                return False
        rules = self._robots[host][0]
        return rules is None or rules.can_fetch(url, USER_AGENT)

    def get(self, url: str) -> tuple[int, str, str]:
        """(status, text, final URL) after at most three redirects; 0 when skipped."""
        for _ in range(4):
            if platform_of(url) or ASSET.search(urlsplit(url).path) or not self.allowed(url):
                return 0, "", url
            status, text, location = self._request(url)
            if status in REDIRECTS and location:
                url = urljoin(url, location)
                continue
            return status, text, url
        return 0, "", url


class BudgetSpent(Exception):
    """The run's request budget is spent."""


def select(
    conn: sqlite3.Connection,
    *,
    start: date,
    end: date,
    events: tuple[str, ...] = (),
    overrides: tuple[dict[str, str], ...] = (),
    limit: int = 20,
) -> list[tuple[str, str, str | None]]:
    """(event_id, website, end_date) of held WSDC events whose results are missing."""
    if events:
        rows = conn.execute(
            "SELECT event_id,website,end_date FROM events WHERE event_id IN "
            f"({','.join('?' * len(events))}) AND website IS NOT NULL ORDER BY end_date,event_id",
            events,
        ).fetchall()
        return [(str(row[0]), str(row[1]), row[2]) for row in rows]
    covered = {row.get("event_id", "") for row in overrides}
    rows = conn.execute(
        "SELECT e.event_id,e.website,e.end_date FROM events e WHERE e.held='held' AND e.series_id LIKE 'wsdc-%' "
        "AND e.website IS NOT NULL AND e.end_date BETWEEN ? AND ? "
        "AND NOT EXISTS (SELECT 1 FROM contests c WHERE c.event_id=e.event_id) "
        "ORDER BY e.end_date,e.event_id",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [(str(row[0]), str(row[1]), row[2]) for row in rows if row[0] not in covered][:limit]


def website_url(website: str) -> str | None:
    """The first http(s) address of a calendar website cell, or None."""
    for token in website.split():
        url = token if "://" in token else "https://" + token
        parts = urlsplit(url)
        if parts.scheme in {"http", "https"} and parts.hostname and "." in parts.hostname:
            return url
    return None


def scan(
    fetcher: Fetcher, event_id: str, website: str, *, end: str | None = None, pages: int = 2
) -> dict[str, Any]:
    """Read one site's home page and up to `pages` result-like pages.

    A WDR id is a version-1 UUID that carries the day it was made. A site's
    results page often still links an earlier edition, so the draft row needs
    an id made within `WDR_LEAD_DAYS` before the edition ended (`end`).
    """
    row: dict[str, Any] = {
        "event_id": event_id,
        "website": website,
        "pages": [],
        "wdr": {},
        "platform_links": [],
        "result_pages": [],
        "source_urls_csv": None,
    }
    start = len(fetcher.requests)
    home = website_url(website)
    queue = [home] if home else []
    seen: set[str] = set()
    platform: set[tuple[str, str]] = set()
    candidates: list[str] = []
    try:
        while queue:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            status, text, final = fetcher.get(url)
            row["pages"].append({"url": url, "status": status, "final": final})
            if status != 200:
                continue
            for link, label in anchors(final, text):
                uuid = wdr_uuid(link)
                if uuid:
                    row["wdr"].setdefault(uuid, [])
                    if final not in row["wdr"][uuid]:
                        row["wdr"][uuid].append(final)
                kind = platform_of(link)
                if uuid or kind == "wsdc":
                    continue
                if kind:
                    platform.add((kind, link))
                elif (
                    same_site(link, final)
                    and RESULT_WORDS.search(link + " " + label)
                    and not ASSET.search(urlsplit(link).path)
                ):
                    if link not in candidates and link not in seen:
                        candidates.append(link)
            if url == home:
                readable = [link for link in candidates if not _document(link)]
                queue.extend(readable[:pages])
    except BudgetSpent:
        row["budget_spent"] = True
    row["requests"] = len(fetcher.requests) - start
    row["platform_links"] = sorted(link for _, link in platform)
    read = {page["url"] for page in row["pages"] if page["status"] == 200}
    row["result_pages"] = [link for link in candidates if link not in read]
    row["wdr_made"] = {uuid: uuid_date(uuid) for uuid in row["wdr"]}
    current = [
        uuid
        for uuid, made in row["wdr_made"].items()
        if end is None
        or (
            made is not None
            and date.fromisoformat(end) - timedelta(days=WDR_LEAD_DAYS)
            <= date.fromisoformat(made)
            <= date.fromisoformat(end)
        )
    ]
    if row["wdr"] and len(current) != 1:
        row["draft_refused"] = "no_wdr_id_of_this_edition" if not current else "several_wdr_ids"
    if len(current) == 1:
        uuid, found_on = current[0], row["wdr"][current[0]]
        output = io.StringIO(newline="")
        csv.writer(output, lineterminator="").writerow(
            (
                event_id,
                "worlddanceregistry",
                "event",
                f"https://{WDR_HOST}/{uuid}/rounds/routeInfo.json",
                "wdr.rounds",
                f"event-site link scan {date.today().isoformat()}: linked from {found_on[0]}; "
                "review the WDR event name and dates",
            )
        )
        row["source_urls_csv"] = output.getvalue()
    return row


def _document(url: str) -> bool:
    return urlsplit(url).path.casefold().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx"))


def write_rows(rows: Iterable[dict[str, Any]], stream: Any) -> None:
    for row in rows:
        stream.write(json.dumps(row, sort_keys=True) + "\n")


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, required=True, help="state directory or database")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--event", action="append", default=[], help="an event id; repeatable")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--gap", type=float, default=5.0)
    parser.add_argument("--max-requests", type=int, default=20)
    parser.add_argument("--overrides", type=Path, help="reviewed source_urls.csv")
    parser.add_argument("--out", type=Path, help="JSON lines file")
    parser.add_argument("--list", action="store_true", help="print the selection; fetch nothing")
    args = parser.parse_args(argv)
    conn = open_state(args.state)
    try:
        chosen = select(
            conn,
            start=args.start,
            end=args.end,
            events=tuple(args.event),
            overrides=read_overrides(args.overrides),
            limit=args.limit,
        )
    finally:
        conn.close()
    rows: list[dict[str, Any]] = []
    if args.list:
        rows = [
            {"event_id": event, "website": site, "end_date": end} for event, site, end in chosen
        ]
    else:
        fetcher = Fetcher(gap=args.gap, max_requests=args.max_requests, transport=transport)
        try:
            for event, site, end in chosen:
                rows.append(scan(fetcher, event, site, end=end, pages=args.pages))
                if len(fetcher.requests) >= fetcher.max_requests:
                    break
        finally:
            fetcher.close()
    if args.out is None:
        write_rows(rows, sys.stdout)
    else:
        with args.out.open("w") as stream:
            write_rows(rows, stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
