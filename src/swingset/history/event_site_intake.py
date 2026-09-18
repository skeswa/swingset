"""Import finite, hash-pinned filtered CDX evidence without acquiring source pages.

The caller owns Database's writer lock. This is a local evidence importer, not
an HTTP executor: request budgets and page-kind admission are never synthesized.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from swingset.fetch.archive import Archive, digest, durable_write
from swingset.fetch.wayback import Capture, parse_cdx
from swingset.history.event_sites import (
    MAX_BODY_BYTES,
    MAX_RECEIPTS,
    Site,
    _matches,
    validate_query,
)
from swingset.project.history import phase_two_allowed
from swingset.state.controls import ActionScope, matching_pauses
from swingset.state.db import Database

FORMAT = "event-site-cdx-intake-v1"
MAX_TOTAL_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Receipt:
    page: str
    raw: bytes
    body: bytes
    fetched_at: str
    captures: tuple[Capture, ...]


@dataclass(frozen=True)
class Query:
    identifier: str
    year: int
    mime: str
    total_pages: int
    receipts: tuple[Receipt, ...]


@dataclass(frozen=True)
class IntakeResult:
    queries: int
    pages: int
    captures: int
    complete_queries: int
    dry_run: bool
    manifest_sha256: str


def _json(raw: bytes) -> dict[str, Any]:
    value: Any = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected an evidence object")
    return value


def _file(root: Path, relative: Path, limit: int) -> bytes:
    path = root / relative
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("evidence paths cannot contain symlinks")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("evidence file missing or exceeds bound")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("evidence file changed or exceeds bound")
    return raw


def _body(archive: Archive, sha256: str) -> bytes:
    path = archive.blob_path(sha256)
    compressed = _file(archive.state_dir, path.relative_to(archive.state_dir), MAX_BODY_BYTES)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        body = stream.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES or digest(body) != sha256:
        raise ValueError("CDX body exceeds bound or fails digest verification")
    return body


def page_count(body: bytes) -> int:
    value = json.loads(body)
    if isinstance(value, dict):
        value = value.get("pages")
    if value == []:
        value = 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RECEIPTS:
        raise ValueError("invalid or excessive filtered CDX page count")
    return int(value)


def page_captures(
    body: bytes, site: Site, year: int, mime: str, fetched: datetime
) -> tuple[Capture, ...]:
    captures = parse_cdx(body)
    for capture in captures:
        if (
            capture.status != 200
            or capture.mimetype != mime
            or int(capture.timestamp[:4]) != year
            or not _matches(site, capture.url)
        ):
            raise ValueError("CDX row escapes filtered website/year scope")
        if datetime.strptime(capture.timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC) > fetched:
            raise ValueError("capture time is later than its retained CDX response")
        if mime == "text/html" and not any(
            word in urlsplit(capture.url).path.casefold()
            for word in ("result", "score", "callback", "prelim", "final")
        ):
            raise ValueError("HTML capture lacks a result-path keyword")
    return captures


def _read(
    directory: Path,
    manifest_sha256: str,
    site: Site,
    *,
    now: datetime,
    deadline: float,
    manifest_path: Path,
) -> tuple[Query, ...]:
    raw = _file(directory, manifest_path, 128 * 1024)
    if digest(raw) != manifest_sha256:
        raise ValueError("intake manifest digest mismatch")
    manifest = _json(raw)
    if (
        manifest.get("format") != FORMAT
        or manifest.get("event_id") != site.event_id
        or manifest.get("year") != site.year
        or manifest.get("website") != site.website
    ):
        raise ValueError("intake manifest does not bind the exact canonical event")
    specs = manifest.get("queries")
    if not isinstance(specs, list) or not 1 <= len(specs) <= 4:
        raise ValueError("intake requires one to four filtered queries")
    queries = []
    identifiers: set[str] = set()
    recipes: set[tuple[int, str]] = set()
    total_bytes = 0
    total_receipts = 0
    total_pages_supplied = 0
    capture_keys: dict[tuple[str, str], Capture] = {}
    for spec in specs:
        identifier = str(spec["query_id"])
        if not re.fullmatch(r"[0-9a-f]{64}", identifier) or identifier in identifiers:
            raise ValueError("query IDs must be distinct retained SHA256 keys")
        identifiers.add(identifier)
        records = spec["receipts"]
        if not isinstance(records, list) or not records:
            raise ValueError("query needs its page-count probe")
        total_receipts += len(records)
        total_pages_supplied += len(records) - 1
        if total_receipts > MAX_RECEIPTS + 4 or total_pages_supplied > MAX_RECEIPTS:
            raise ValueError("intake receipt cohort exceeds bound")
        receipts = []
        year = 0
        mime = ""
        pages = 0
        for ordinal, record in enumerate(records):
            if time.monotonic() >= deadline:
                raise ValueError("intake wall clock exhausted")
            page = "probe" if ordinal == 0 else str(ordinal - 1)
            if record["page"] != page:
                raise ValueError("probe and contiguous pages must be supplied in order")
            receipt_raw = _file(
                directory, Path("archive-cdx") / identifier / (page + ".json"), 128 * 1024
            )
            if digest(receipt_raw) != record["sha256"]:
                raise ValueError("raw CDX receipt digest mismatch")
            receipt = _json(receipt_raw)
            if (
                receipt["query_id"] != identifier
                or str(receipt["page"]) != page
                or receipt["http_status"] != 200
                or receipt.get("complete") is False
                or receipt.get("failure_reason")
            ):
                raise ValueError("CDX receipt identity or status mismatch")
            fetched = datetime.fromisoformat(receipt["fetched_at"])
            if fetched.tzinfo is None or fetched > now:
                raise ValueError("CDX receipt time must be aware and no later than intake")
            request_year = int(parse_qs(urlsplit(receipt["url"]).query)["from"][0])
            request_mime = validate_query(
                receipt["url"], site, request_year, page=None if ordinal == 0 else ordinal - 1
            )
            if ordinal == 0:
                year, mime = request_year, request_mime
            elif (year, mime) != (request_year, request_mime):
                raise ValueError("CDX probe and page recipes differ")
            body_sha = str(receipt["body_sha256"])
            archive = Archive(directory)
            body = _body(archive, body_sha)
            total_bytes += len(body)
            if (
                len(body) > MAX_BODY_BYTES
                or total_bytes > MAX_TOTAL_BYTES
                or digest(body) != body_sha
            ):
                raise ValueError("CDX body exceeds bound or fails digest verification")
            captures: tuple[Capture, ...] = ()
            if ordinal == 0:
                pages = page_count(body)
            else:
                if ordinal > pages:
                    raise ValueError("page exceeds retained count probe")
                captures = page_captures(body, site, year, mime, fetched)
                for capture in captures:
                    key = (capture.url, capture.timestamp)
                    if key in capture_keys and capture_keys[key] != capture:
                        raise ValueError("conflicting capture rows inside retained package")
                    capture_keys[key] = capture
            receipts.append(Receipt(page, receipt_raw, body, receipt["fetched_at"], captures))
        if (year, mime) in recipes:
            raise ValueError("duplicate filter recipe in intake")
        recipes.add((year, mime))
        queries.append(Query(identifier, year, mime, pages, tuple(receipts)))
    return tuple(queries)


def _gate(conn: sqlite3.Connection, site: Site, now: datetime, history_start: date) -> None:
    row = conn.execute(
        "SELECT year,website FROM events WHERE event_id=?", (site.event_id,)
    ).fetchone()
    if row is None or (int(row[0]), row[1]) != (site.year, site.website):
        raise ValueError("canonical event website/year changed")
    if (
        site.year < history_start.year
        or site.prefix is None
        or not phase_two_allowed(conn, site.year)
    ):
        raise ValueError("history year is unaccepted or its proof is stale")
    scope = ActionScope(all_sources=True, kinds=frozenset({"source_event_mapping"}))
    if matching_pauses(conn, scope, now=now):
        raise ValueError("event-site evidence review is paused")


def _retain(path: Path, raw: bytes) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("destination evidence path contains a symlink")
    if path.exists():
        if _file(path.parent, Path(path.name), len(raw)) != raw:
            raise ValueError("immutable CDX receipt conflicts with retained provenance")
    else:
        durable_write(path, raw)


def ingest(
    database: Database,
    site: Site,
    *,
    directory: Path,
    manifest_sha256: str,
    now: datetime,
    dry_run: bool = True,
    history_start: date = date(2010, 1, 1),
    wall_seconds: float = 30,
    manifest_path: Path = Path("event-site-intake.json"),
) -> IntakeResult:
    """Verify a pinned local package; optionally retain it for ordinary review.

    Repeated imports preserve original receipt bytes. A partial query can resume
    only with the same probe and page prefix; a refresh needs a different query
    ID. Duplicate captures keep their first DB pointer, with every independent
    query receipt retaining exact row provenance. Neither is origin-absence proof.
    """
    if not 0 < wall_seconds <= 60 or now.tzinfo is None:
        raise ValueError("intake needs an aware clock and at most 60 seconds")
    if manifest_path.is_absolute() or ".." in manifest_path.parts:
        raise ValueError("manifest path must remain within its archive directory")
    deadline = time.monotonic() + wall_seconds
    try:
        queries = _read(
            directory,
            manifest_sha256,
            site,
            now=now,
            deadline=deadline,
            manifest_path=manifest_path,
        )
    except (KeyError, TypeError, OSError) as exc:
        raise ValueError("incomplete retained CDX package") from exc
    conn = database.connection
    _gate(conn, site, now, history_start)
    result = IntakeResult(
        len(queries),
        sum(len(q.receipts) - 1 for q in queries),
        sum(len(r.captures) for q in queries for r in q.receipts),
        sum(len(q.receipts) - 1 == q.total_pages for q in queries),
        dry_run,
        manifest_sha256,
    )
    # Validate all collisions before writing any referenced metadata.
    for query in queries:
        row = conn.execute(
            "SELECT * FROM archive_queries WHERE query_id=?", (query.identifier,)
        ).fetchone()
        if row is not None and (
            row["source"] != "event_sites"
            or row["prefix"] != site.prefix
            or int(row["year"]) != query.year
            or row["total_pages"] != query.total_pages
        ):
            raise ValueError("query identifier collides with another source or scope")
        if (
            row is not None
            and not (database.state_dir / "archive-cdx" / query.identifier / "probe.json").is_file()
        ):
            raise ValueError("existing query lacks its immutable probe receipt")
        for receipt in query.receipts:
            path = database.state_dir / "archive-cdx" / query.identifier / (receipt.page + ".json")
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                raise ValueError("destination evidence path contains a symlink")
            if (
                path.exists()
                and _file(path.parent, Path(path.name), len(receipt.raw)) != receipt.raw
            ):
                raise ValueError("query refresh cannot replace an earlier receipt")
            for capture in receipt.captures:
                if time.monotonic() >= deadline:
                    raise ValueError("intake wall clock exhausted")
                held = conn.execute(
                    "SELECT digest,status,mimetype,length FROM archive_captures WHERE source='event_sites' AND url=? AND timestamp=?",
                    (capture.url, capture.timestamp),
                ).fetchone()
                if held is not None and tuple(held) != (
                    capture.digest,
                    capture.status,
                    capture.mimetype,
                    capture.length,
                ):
                    raise ValueError("capture identity conflicts with retained evidence")
    if dry_run:
        return result
    archive = Archive(database.state_dir)
    for query in queries:
        for receipt in query.receipts:
            if time.monotonic() >= deadline:
                raise ValueError("intake wall clock exhausted")
            body_path = archive.blob_path(digest(receipt.body))
            if any(parent.is_symlink() for parent in (body_path, *body_path.parents)):
                raise ValueError("destination body path contains a symlink")
            if body_path.exists():
                _body(archive, digest(receipt.body))
            else:
                _retain(body_path, gzip.compress(receipt.body, mtime=0))
            _retain(
                database.state_dir / "archive-cdx" / query.identifier / (receipt.page + ".json"),
                receipt.raw,
            )
    manifest_raw = _file(directory, manifest_path, 128 * 1024)
    if digest(manifest_raw) != manifest_sha256:
        raise ValueError("intake manifest changed during inspection")
    _retain(
        database.state_dir / "history" / "event-site-intakes" / (manifest_sha256 + ".json"),
        manifest_raw,
    )
    with database.transaction():
        _gate(conn, site, now, history_start)
        if time.monotonic() >= deadline:
            raise ValueError("intake wall clock exhausted")
        for query in queries:
            progress = len(query.receipts) - 1
            finished = query.receipts[-1].fetched_at if progress == query.total_pages else None
            conn.execute(
                "INSERT INTO archive_queries VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(query_id) DO UPDATE SET next_page=MAX(next_page,excluded.next_page),completed_at=COALESCE(completed_at,excluded.completed_at)",
                (
                    query.identifier,
                    "event_sites",
                    site.prefix,
                    query.year,
                    progress,
                    query.total_pages,
                    finished,
                    query.receipts[0].fetched_at,
                ),
            )
            for receipt in query.receipts:
                for capture in receipt.captures:
                    if time.monotonic() >= deadline:
                        raise ValueError("intake wall clock exhausted")
                    conn.execute(
                        "INSERT INTO archive_captures VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(source,url,timestamp) DO NOTHING",
                        (
                            "event_sites",
                            capture.url,
                            capture.timestamp,
                            capture.digest,
                            capture.status,
                            capture.mimetype,
                            capture.length,
                            receipt.fetched_at,
                            query.identifier,
                        ),
                    )
    return result
