"""Print event-site `source_urls.csv` proposals from a state copy's CDX evidence.

Offline and read-only. The tool opens a copy of the state database with
`mode=ro` (and `immutable=1` when no write-ahead log holds data), reads the
retained filtered CDX receipts and bodies under the copy's `archive-cdx/` and
`blobs/`, and prints one JSON line per event outcome. It makes no request and
writes no finding, cursor, `meta` row or file inside the state. Years are
judged on facts alone (`year_gate_reason` under `facts_only_year_gate`, as the
plan loop does); an event in a closed year yields nothing.

Each row has `event_id`, `year` and `reason`. A proposal row adds `url`,
`captures`, `candidate_event_ids`, `parser`, `parser_available` and
`source_urls_csv`: a draft row only when the binding is unique, the format is
single and the URL is HTTPS (`reason` `draft_override`), else null. Gap rows
are `no_retained_hits` (no evidence; not proof of absence) and
`invalid_evidence` (`error` says why).

    python -m journal.tools.collection.event_site_review --state <copy> \\
        [--events 10] [--year 2019] [--after <event_id>] \\
        [--overrides overrides/source_urls.csv] [--out proposals.jsonl]

A reviewer checks the capture, the event binding and the parser, then copies
`source_urls_csv` into `overrides/source_urls.csv` and edits its notes column.
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
from collections.abc import Iterable
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from swingset.fetch.archive import Archive
from swingset.history.event_sites import Hit, Site, _matches, retained_hits
from swingset.model.history import HISTORY_START
from swingset.project.history import facts_only_year_gate, year_gate_reason
from swingset.sources import get_page_kind

MAX_EVENTS = 1000


def open_state(path: Path) -> sqlite3.Connection:
    """A read-only connection to a state directory's or file's database."""
    database = path if path.suffix in {".sqlite", ".db"} else path / "state.sqlite"
    if not database.is_file():
        raise FileNotFoundError(f"no state database at {database}")
    wal = database.with_name(database.name + "-wal")
    # `immutable` skips locking and the -shm file, so nothing is created. It
    # would ignore committed frames still in a WAL, so plain `mode=ro` then.
    flags = "mode=ro" if wal.is_file() and wal.stat().st_size else "mode=ro&immutable=1"
    conn = sqlite3.connect(f"file:{database}?{flags}", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def candidates(sites: tuple[Site, ...], hit: Hit) -> tuple[str, ...]:
    """Events whose site and capture window hold `hit`; a URL year narrows them."""
    try:
        path = urlsplit(hit.url).path
    except ValueError:
        return ()
    years = {int(year) for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", path)}
    found = [
        site
        for site in sites
        if _matches(site, hit.url) and int(hit.timestamp[:4]) in {site.year, site.year + 1}
    ]
    if years:
        found = [site for site in found if years == {site.year}]
    return tuple(sorted({site.event_id for site in found}))


def proposals(
    site: Site,
    sites: tuple[Site, ...],
    hits: tuple[Hit, ...],
    *,
    overrides: tuple[dict[str, str], ...] = (),
) -> list[dict[str, Any]]:
    """One row per retained URL bound to `site`; a draft CSV line only when unique."""
    result = []
    for url in sorted({hit.url for hit in hits}):
        group = tuple(hit for hit in hits if hit.url == url)
        bound = sorted({event for hit in group for event in candidates(sites, hit)})
        if site.event_id not in bound:
            continue
        existing = [row for row in overrides if row.get("url") == url]
        if existing and all(row.get("event_id") == site.event_id for row in existing):
            continue  # Reviewed already.
        formats = {hit.mimetype for hit in group}
        parser = "generic.pdf_table" if formats == {"application/pdf"} else "generic.html_table"
        reason = "draft_override"
        if bound != [site.event_id] or existing or len(formats) != 1:
            reason = "ambiguous_event_or_format"
        elif urlsplit(url).scheme != "https":
            reason = "http_locator_requires_review"
        try:
            get_page_kind(parser)
            available = True
        except KeyError:
            available = False
        line = None
        if reason == "draft_override":
            output = io.StringIO(newline="")
            csv.writer(output, lineterminator="").writerow(
                (
                    site.event_id,
                    "generic",
                    "round",
                    url,
                    parser,
                    "CDX candidate: review event binding, content and parser admission before use",
                )
            )
            line = output.getvalue()
        result.append(
            {
                "event_id": site.event_id,
                "year": site.year,
                "website": site.website,
                "url": url,
                "reason": reason,
                "candidate_event_ids": bound,
                "parser": parser,
                "parser_available": available,
                "captures": [asdict(hit) for hit in group],
                "source_urls_csv": line,
            }
        )
    return result


def review(
    conn: sqlite3.Connection,
    state_dir: Path,
    *,
    events: int = 10,
    year: int | None = None,
    after: str = "",
    overrides: tuple[dict[str, str], ...] = (),
    history_start: date = HISTORY_START,
    wall_seconds: float = 60,
) -> list[dict[str, Any]]:
    """Rows for the first `events` open-year event sites after `after`, by id."""
    if not 1 <= events <= MAX_EVENTS:
        raise ValueError(f"--events must be 1 to {MAX_EVENTS}")
    deadline = time.monotonic() + wall_seconds
    sites = tuple(
        Site(str(row[0]), int(row[1]), str(row[2]))
        for row in conn.execute(
            "SELECT event_id,year,website FROM events WHERE year>=? AND website IS NOT NULL ORDER BY event_id",
            (history_start.year,),
        )
        if row[2]
    )
    with facts_only_year_gate():
        open_years = {
            each for each in {site.year for site in sites} if year_gate_reason(conn, each) is None
        }
    cohort = [
        site
        for site in sites
        if site.event_id > after
        and site.prefix
        and site.year in open_years
        and (year is None or site.year == year)
    ][:events]
    archive = Archive(state_dir)
    rows: list[dict[str, Any]] = []
    for site in cohort:
        base = {"event_id": site.event_id, "year": site.year, "website": site.website}
        try:
            hits = retained_hits(conn, archive, site, deadline=deadline)
            found = proposals(site, sites, hits, overrides=overrides)
            if not hits:
                found = [{**base, "reason": "no_retained_hits", "search_complete": False}]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            found = [{**base, "reason": "invalid_evidence", "error": str(exc)}]
        rows.extend(found)
    return rows


def read_overrides(path: Path | None) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    with path.open(newline="") as stream:
        return tuple(csv.DictReader(stream))


def write_rows(rows: Iterable[dict[str, Any]], stream: Any) -> None:
    for row in rows:
        stream.write(json.dumps(row, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, required=True, help="state copy directory or file")
    parser.add_argument("--events", type=int, default=10)
    parser.add_argument("--year", type=int)
    parser.add_argument("--after", default="", help="start after this event_id")
    parser.add_argument("--overrides", type=Path, help="reviewed source_urls.csv")
    parser.add_argument("--history-start", type=date.fromisoformat, default=HISTORY_START)
    parser.add_argument("--wall-seconds", type=float, default=60)
    parser.add_argument("--out", type=Path, help="JSON lines file, outside the state")
    args = parser.parse_args(argv)
    path = args.state if args.state.suffix not in {".sqlite", ".db"} else args.state.parent
    if args.out is not None and args.out.resolve().is_relative_to(path.resolve()):
        parser.error("--out must be outside the state directory")
    conn = open_state(args.state)
    try:
        rows = review(
            conn,
            path,
            events=args.events,
            year=args.year,
            after=args.after,
            overrides=read_overrides(args.overrides),
            history_start=args.history_start,
            wall_seconds=args.wall_seconds,
        )
    finally:
        conn.close()
    if args.out is None:
        write_rows(rows, sys.stdout)
    else:
        with args.out.open("w") as stream:
            write_rows(rows, stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
