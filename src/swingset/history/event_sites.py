"""Event-site CDX query specifications and verified retained hits.

Turning hits into reviewable `source_urls.csv` proposals is the offline
event-site review tool in `journal/tools/collection/`; no loop runs it.
"""

from __future__ import annotations

import gzip
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from swingset.fetch.archive import Archive, digest
from swingset.fetch.wayback import parse_cdx

WORDS = ("result", "score", "callback", "prelim", "final")
HTML_FILTER = "original:.*(result|score|callback|prelim|final).*"
MAX_RECEIPTS = 64
MAX_BODY_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Site:
    event_id: str
    year: int
    website: str

    @property
    def prefix(self) -> str | None:
        try:
            parsed = urlsplit(self.website)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                return None
            if parsed.port not in {None, 80 if parsed.scheme == "http" else 443}:
                return None
        except ValueError:
            return None
        return parsed.hostname + parsed.path.rstrip("/") + "/*"


@dataclass(frozen=True)
class Hit:
    url: str
    timestamp: str
    mimetype: str
    digest: str
    query_id: str
    receipt_sha256: str
    body_sha256: str
    query_url: str


def query_url(site: Site, year: int, mime: str, *, page: int | None) -> str:
    """One exact filtered request; None selects the required count probe."""
    if site.prefix is None or year not in {site.year, site.year + 1}:
        raise ValueError("CDX query is outside the event website/year scope")
    if mime not in {"application/pdf", "text/html"} or (page is not None and page < 0):
        raise ValueError("unsupported CDX filter or page")
    params: list[tuple[str, str | int]] = [
        ("url", site.prefix),
        ("filter", "statuscode:200"),
        ("filter", "mimetype:" + mime),
    ]
    if mime == "text/html":
        params.append(("filter", HTML_FILTER))
    params.extend((("from", year), ("to", year), ("collapse", "digest")))
    if page is None:
        params.append(("showNumPages", "true"))
    else:
        params.extend(
            (
                ("output", "json"),
                ("fl", "timestamp,original,digest,mimetype,length"),
                ("page", page),
            )
        )
    return "https://web.archive.org/cdx/search/cdx?" + urlencode(params)


def query_urls(site: Site, *, history_start: date = date(2010, 1, 1)) -> tuple[str, ...]:
    """Reviewable first-page specifications only; never submits requests."""
    if site.year < history_start.year or site.prefix is None:
        return ()
    return tuple(
        query_url(site, year, mime, page=0)
        for year in (site.year, site.year + 1)
        for mime in ("application/pdf", "text/html")
    )


def validate_query(url: str, site: Site, year: int, *, page: int | None) -> str:
    """Reject extra/duplicate fields, endpoints, credentials and mismatched filters."""
    try:
        request = urlsplit(url)
        params = parse_qs(request.query, keep_blank_values=True)
        if (
            request.scheme != "https"
            or request.hostname != "web.archive.org"
            or request.port not in {None, 443}
            or request.username
            or request.password
            or request.fragment
            or request.path != "/cdx/search/cdx"
        ):
            raise ValueError("CDX receipt uses an unreviewed endpoint")
        for mime in ("application/pdf", "text/html"):
            expected = parse_qs(urlsplit(query_url(site, year, mime, page=page)).query)
            if {k: sorted(v) for k, v in params.items()} == {
                k: sorted(v) for k, v in expected.items()
            }:
                return mime
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid filtered CDX request") from exc
    raise ValueError("CDX receipt is not the supported filtered request")


def _matches(site: Site, url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if (
            site.prefix is None
            or parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            return False
        if parsed.port not in {None, 80 if parsed.scheme == "http" else 443}:
            return False
        return ((parsed.hostname or "") + parsed.path).startswith(site.prefix[:-1])
    except ValueError:
        return False


def _receipt_hits(
    archive: Archive, query: sqlite3.Row, receipt_path: Path, site: Site
) -> tuple[Hit, ...]:
    if receipt_path.stat().st_size > 128 * 1024:
        raise ValueError("CDX receipt exceeds review bound")
    raw = receipt_path.read_bytes()
    receipt = json.loads(raw)
    if (
        receipt["query_id"] != query["query_id"]
        or receipt["http_status"] != 200
        or receipt.get("complete") is False
        or receipt.get("failure_reason")
        or str(receipt["page"]) != receipt_path.stem
    ):
        raise ValueError("CDX request receipt identity does not match")
    mime = validate_query(str(receipt["url"]), site, int(query["year"]), page=int(receipt["page"]))
    body_sha = str(receipt["body_sha256"])
    with gzip.open(archive.blob_path(body_sha), "rb") as stream:
        body = stream.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES or digest(body) != body_sha:
        raise ValueError("CDX body exceeds review bound or fails digest verification")
    result = []
    for capture in parse_cdx(body):
        if (
            capture.mimetype != mime
            or int(capture.timestamp[:4]) != int(query["year"])
            or not _matches(site, capture.url)
        ):
            continue
        if mime == "text/html" and not any(
            word in urlsplit(capture.url).path.casefold() for word in WORDS
        ):
            continue
        result.append(
            Hit(
                capture.url,
                capture.timestamp,
                mime,
                capture.digest,
                str(query["query_id"]),
                digest(raw),
                body_sha,
                str(receipt["url"]),
            )
        )
    return tuple(result)


def retained_hits(
    conn: sqlite3.Connection, archive: Archive, site: Site, *, deadline: float | None = None
) -> tuple[Hit, ...]:
    """Verify the actual filtered CDX request and exact retained body for each hit."""
    hits = set()
    receipts = 0
    for query in conn.execute(
        "SELECT * FROM archive_queries WHERE source='event_sites' AND prefix=? AND year IN (?,?) ORDER BY year,query_id",
        (site.prefix, site.year, site.year + 1),
    ):
        identifier = str(query["query_id"])
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("CDX query identifier is not a retained receipt key")
        pages = int(query["next_page"])
        if pages < 0 or query["total_pages"] is None or pages > int(query["total_pages"]):
            raise ValueError("CDX page progress is invalid")
        receipts += pages
        if receipts > MAX_RECEIPTS:
            raise ValueError("CDX receipt cohort exceeds review bound")
        for page in range(pages):
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError("CDX review wall clock exhausted")
            for hit in _receipt_hits(
                archive,
                query,
                archive.state_dir / "archive-cdx" / identifier / f"{page}.json",
                site,
            ):
                # A capture's first DB pointer is stable across refreshes; every
                # query retains its own raw page receipt as independent provenance.
                if (
                    conn.execute(
                        "SELECT 1 FROM archive_captures WHERE source='event_sites' AND url=? AND timestamp=? AND digest=? AND mimetype=? AND status=200",
                        (hit.url, hit.timestamp, hit.digest, hit.mimetype),
                    ).fetchone()
                    is None
                ):
                    raise ValueError("CDX body hit is not retained in the capture inventory")
                hits.add(hit)
    return tuple(sorted(hits, key=lambda hit: (hit.url, hit.timestamp, hit.query_id)))
