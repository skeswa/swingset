"""Versioned adapter accounting, independent of the observation writer.

These contracts describe captured single documents. A listing is never an
authoritative enumeration of an entire web site. Unknown shapes fail closed.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from swingset.sources.base import ParseContext, ParseResult
from swingset.sources.common import absolute
from swingset.sources.records import FileRow, SourceEventRow

from .accounting import account_keys
from .registry_contract import registry_accounting
from .report import Coverage, Field, Guard, Report, evaluate
from .round_contract import round_accounting

CONTRACT_VERSION = "4"
KINDS = frozenset(
    {
        "wsdc_registry.dancer",
        "eepro.index",
        "eepro.autoindex",
        "eepro.round",
        "scoringdance.sitemap",
        "scoringdance.recent",
        "scoringdance.event",
        "scoringdance.round",
        "wdr.rounds",
    }
)


def contract_version(page_kind: str) -> str:
    """A changed page contract does not invalidate unrelated reviewed contracts."""
    return (
        "5"
        if page_kind == "eepro.autoindex"
        else CONTRACT_VERSION
        if page_kind in KINDS
        else "unassessed"
    )


def inspect(ctx: ParseContext, body: bytes, extract: Any, result: ParseResult) -> Report:
    fields: list[Field] = []
    guards: list[Guard] = []
    source_count: int | None = None
    interpreted = len(result.observations)
    terminal: str | None = "captured_document_end"
    removal = "watch"
    children: tuple[str, ...] = ()
    parsed_children: tuple[str, ...] = ()
    known = ctx.kind in KINDS
    guards.append(
        Guard("contract_unassessed", known, "This page kind has an explicit versioned contract")
    )
    if ctx.kind == "wsdc_registry.dancer":
        source_count, interpreted = registry_accounting(extract, result, fields, guards)
        removal = "none"  # A lookup can never delete historical registry facts.
    elif ctx.kind in {
        "eepro.index",
        "eepro.autoindex",
        "scoringdance.recent",
        "scoringdance.sitemap",
    }:
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
                o.payload.date_raw
                for o in result.observations
                if isinstance(o.payload, SourceEventRow)
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
                            o.payload.url
                            for o in result.observations
                            if isinstance(o.payload, FileRow)
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
        parsed_children = tuple(watch.url for watch in result.watches)
        removal = "none"
    elif ctx.kind == "scoringdance.event":
        account_keys(extract, {"name", "date", "rounds", "unpublished"}, "$", fields)
        rounds = extract.get("rounds", []) if isinstance(extract, dict) else []
        source_count = len(rounds)
        payload = next((o.payload for o in result.observations if o.kind == "event_sheet"), None)
        interpreted = len(getattr(payload, "round_links", ()))
        children = tuple(
            absolute(ctx.url, str(item["href"]))
            for item in rounds
            if isinstance(item, dict) and "href" in item
        )
        parsed_children = tuple(watch.url for watch in result.watches)
        for index, item in enumerate(rounds):
            account_keys(item, {"id", "name", "href"}, f"rounds[{index}]", fields)
        guards.append(
            Guard(
                "required_date_missing",
                bool(isinstance(extract, dict) and extract.get("date")),
                "An event document requires its source date",
            )
        )
        # An unpublished page is a dated observation, not a withdrawal of rounds.
        removal = "none"
    elif ctx.kind in {"eepro.round", "scoringdance.round", "wdr.rounds"}:
        source_count, interpreted = round_accounting(ctx.kind, extract, result, fields, guards)
        if ctx.kind == "wdr.rounds":
            removal = "none"  # Unresolved outcomes/partner ownership cannot retire old facts.
        fields.append(
            Field(
                "event_date",
                "excluded",
                "Round documents inherit occurrence association from the separately assessed event listing",
            )
        )
        if not interpreted:
            terminal = None  # Coming soon / legitimate_empty never grants authority.
    else:
        fields.append(
            Field("$", "unknown", "No accounting contract has been reviewed for this page kind")
        )
        removal = "none"
    text = body.decode("utf-8", "replace")
    if re.search(r"rel\s*=\s*['\"]?next\b|[?&](?:page|cursor)=", text, re.I):
        terminal = None
        guards.append(
            Guard(
                "pagination_unassembled",
                False,
                "Pagination links require an assembled and revalidated manifest",
            )
        )
    for warning in result.warnings:
        excluded = ctx.kind == "wdr.rounds" and warning.code in {
            "wdr_s_callback_unverified",
            "wdr_finals_bib_unverified",
        }
        fields.append(
            Field(
                f"warning:{warning.code}",
                "excluded" if excluded else "unknown",
                warning.message
                + (
                    "; retained as a finding; no inferred outcome/partner bib or removal authority"
                    if excluded
                    else ""
                ),
            )
        )
    coverage = Coverage(
        (ctx.snapshot_id,),
        (ctx.snapshot_id,),
        terminal,
        source_count,
        interpreted,
        children,
        parsed_children,
    )
    if result.interpretation is not None:
        declared = result.interpretation
        fields.extend(declared.fields)
        guards.extend(declared.guards)
        guards.append(
            Guard(
                "declaration_mismatch",
                declared.page_kind == ctx.kind
                and declared.contract_version == contract_version(ctx.kind)
                and declared.coverage.source_count == source_count
                and declared.coverage.interpreted_count == interpreted
                and declared.coverage.listed_children == children,
                "Archived structure independently corroborates the adapter declaration",
            )
        )
    return evaluate(
        ctx.kind,
        contract_version(ctx.kind),
        tuple(dict.fromkeys(fields)),
        coverage,
        guards=tuple(dict.fromkeys(guards)),
        removal=removal,
    )
