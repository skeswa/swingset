"""Accounting for the eepro and scoring.dance listings: indexes, autoindexes, sitemaps.

Only these four kinds' parse scopes name this module (`admission/kinds.py`).
A listing never retires the events or files it no longer shows.
"""

from typing import Any
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from swingset.sources.base import ParseContext, ParseResult
from swingset.sources.common import absolute
from swingset.sources.records import FileRow, SourceEventRow

from .accounting import Accounting, account_keys
from .report import Field, Guard

#: Contract 5 for the eepro autoindex is the 2026-10-06 admission
#: adjudication (D-0240).
VERSIONS = {
    "eepro.autoindex": "5",
    "eepro.index": "4",
    "scoringdance.recent": "4",
    "scoringdance.sitemap": "4",
}
KINDS = frozenset(VERSIONS)


def accounting(ctx: ParseContext, body: bytes, extract: Any, result: ParseResult) -> Accounting:
    if ctx.kind not in KINDS:
        raise ValueError(f"unsupported listing contract kind: {ctx.kind}")
    fields: list[Field] = []
    guards: list[Guard] = []
    interpreted = len(result.observations)
    children: tuple[str, ...] = ()
    source_count = len(extract) if isinstance(extract, list) else None
    keys = (
        {"slug", "name", "date", "url"}
        if ctx.kind == "eepro.index"
        else {"name", "href", "modified", "size"}
        if ctx.kind == "eepro.autoindex"
        else {"id", "name", "date", "href"}
    )
    for index, item in enumerate(extract if isinstance(extract, list) else []):
        if ctx.kind == "scoringdance.sitemap":
            fields.append(
                Field(
                    str(index),
                    "handled" if str(item).isdigit() else "unknown",
                    "source event identifier",
                )
            )
        else:
            account_keys(item, keys, str(index), fields)
    if ctx.kind in {"eepro.index", "scoringdance.recent"}:
        dates = [
            o.payload.date_raw for o in result.observations if isinstance(o.payload, SourceEventRow)
        ]
        guards.append(
            Guard(
                "required_date_missing",
                len(dates) == interpreted and all(dates),
                "Event listing rows require source dates",
            )
        )
    else:
        fields.append(
            Field(
                "event_date",
                "excluded",
                "This document enumerates identifiers or files; event dates belong to the event page",
            )
        )
    # Recent/sitemap documents do not assert retirement of historical events.
    rows = extract if isinstance(extract, list) else []
    if ctx.kind == "eepro.index":
        children = tuple(
            f"https://eepro.com/results/{item['slug']}/"
            for item in rows
            if isinstance(item, dict) and "slug" in item
        )
    elif ctx.kind == "eepro.autoindex":
        file_urls = tuple(
            absolute(ctx.url, str(item["href"]))
            for item in rows
            if isinstance(item, dict) and "href" in item
        )
        if body:
            # This witness does not consume extractor rows or visible labels.
            file_urls = tuple(
                absolute(ctx.url, href)
                for node in HTMLParser(body).css("a[href]")
                if (href := node.attributes.get("href"))
                and urlparse(href).path.lower().endswith((".html", ".htm", ".pdf"))
            )
            source_count = len(file_urls)
            guards.append(
                Guard(
                    "autoindex_file_coverage",
                    file_urls
                    == tuple(
                        o.payload.url for o in result.observations if isinstance(o.payload, FileRow)
                    ),
                    "Every raw file href must survive as a FileRow in source order, regardless of display truncation",
                )
            )
        children = tuple(
            url for url in file_urls if urlparse(url).path.lower().endswith((".html", ".htm"))
        )
        fields.append(
            Field(
                "pdf_child_acquisition",
                "excluded",
                "PDF file rows remain observations; the HTML round adapter does not claim to acquire PDF children",
            )
        )
    elif ctx.kind == "scoringdance.sitemap":
        children = tuple(f"https://scoring.dance/enUS/events/{item}/results/" for item in rows)
    else:
        fields.append(
            Field(
                "child_acquisition",
                "excluded",
                "Recent event rows contribute discovery evidence; the sitemap owns event-watch enumeration",
            )
        )
    return Accounting(
        tuple(fields),
        tuple(guards),
        source_count,
        interpreted,
        children,
        tuple(watch.url for watch in result.watches),
        removal="none",
    )
