"""Offline DCN archive index and legacy event metadata; no discovery or joins."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from swingset.sources.base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseError,
    ParseResult,
    ParseWarning,
    WatchSpec,
)
from swingset.sources.common import text

from .legacy_results import LegacyResultsPage
from .nuxt import MAX_BODY_BYTES, evaluate_nuxt
from .records import DcnEventMetadata, DcnIndexEvent, DcnIndexSheet

HOST = "danceconvention.net"
ROOT = f"https://{HOST}"
EVENT_PATH = re.compile(
    r"^/eventdirector/en/eventpage/([0-9]{1,18})(?:-[^/?#;\s]+)?(?:/(info|results|partners))?/?$"
)
DATES = re.compile(
    r"(?P<start>[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})\s*-\s*(?P<end>[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}),\s*(?P<location>.+)"
)
ROW_FIELDS = frozenset(
    {
        "eventId",
        "name",
        "venueName",
        "startDate",
        "endDate",
        "location",
        "online",
        "schedule",
        "bannerUrl",
        "squareImage",
        "affiliations",
        "results",
        "published",
        "frequent",
        "eventPage",
    }
)
OPTIONAL_ROW_FIELDS = frozenset({"bannerUrl", "squareImage"})


def event_locator(href: str) -> tuple[str, str, str | None]:
    if any(char.isspace() or ord(char) < 32 for char in href):
        raise ExtractError("DCN event locator contains whitespace or controls")
    try:
        parsed = urlsplit(urljoin(ROOT, href))
    except ValueError as exc:
        raise ExtractError("DCN event locator is malformed") from exc
    if parsed.scheme != "https" or parsed.netloc != HOST or parsed.query or parsed.fragment:
        raise ExtractError("DCN event locator is outside the reviewed original source")
    path = re.sub(r";jsessionid=[A-Za-z0-9._-]+$", "", parsed.path)
    match = EVENT_PATH.fullmatch(path)
    if match is None:
        raise ExtractError("DCN event locator has an unsupported path")
    return f"dcn:{match[1]}", urlunsplit(("https", HOST, path.rstrip("/"), "", "")), match[2]


def checked_text(value: Any, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 4096 or (not empty and not value.strip()):
        raise ExtractError(f"DCN {field} is not supported source text")
    return value


def context_matches(ctx: ParseContext, kind: str) -> None:
    if ctx.source != "dcn" or ctx.kind != kind:
        raise ParseError("DCN parse context differs from the exact source kind")


class IndexPage:
    kind = "dcn.list"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "extract"

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()

    def extract(self, body: bytes) -> JsonValue:
        payload = evaluate_nuxt(body)
        try:
            render = payload["state"]["common"]["currentPageRenderData"]
            mirror = payload["data"][0]["renderData"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExtractError("DCN index render data is missing") from exc
        if payload.get("routePath") != "/en/eventsarchive" or render != mirror:
            raise ExtractError("DCN index route or duplicate payload views disagree")
        if not isinstance(render, dict) or set(render) != {
            "availableYears",
            "lastYearEvents",
            "loadYearUrl",
            "dcnetLogo",
        }:
            raise ExtractError("DCN index render fields changed")
        years = render["availableYears"]
        if (
            not isinstance(years, list)
            or not 1 <= len(years) <= 200
            or any(type(year) is not int or not 1900 <= year <= 2100 for year in years)
            or len(set(years)) != len(years)
        ):
            raise ExtractError("DCN available years are malformed")
        if render["loadYearUrl"] != ROOT + "/eventdirector/en/eventsarchive:loadyear":
            raise ExtractError("DCN year locator changed")
        rows = render["lastYearEvents"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            raise ExtractError("DCN empty or oversized index needs a reviewed contract")
        events = []
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or not ROW_FIELDS - OPTIONAL_ROW_FIELDS <= set(row) <= ROW_FIELDS
            ):
                raise ExtractError("DCN index event fields changed")
            identifier = row["eventId"]
            if type(identifier) is not int or not 0 < identifier < 10**18:
                raise ExtractError("DCN event identifier is invalid")
            ref, url, tab = event_locator(checked_text(row["eventPage"], "eventPage"))
            if ref != f"dcn:{identifier}" or tab is not None or ref in seen:
                raise ExtractError("DCN event identifier, URL or duplicate ownership disagrees")
            seen.add(ref)
            for key in (
                "name",
                "startDate",
                "endDate",
                "location",
                "venueName",
                "bannerUrl",
                "squareImage",
            ):
                if key in OPTIONAL_ROW_FIELDS and row.get(key) is None:
                    continue
                checked_text(row[key], key, empty=key in {"venueName", "bannerUrl", "squareImage"})
            for key in ("results", "published", "online", "schedule", "frequent"):
                if type(row[key]) is not bool:
                    raise ExtractError(f"DCN {key} is not a boolean")
            affiliations = row["affiliations"]
            if (
                not isinstance(affiliations, list)
                or len(affiliations) > 50
                or any(
                    not isinstance(item, str) or not item or len(item) > 128
                    for item in affiliations
                )
            ):
                raise ExtractError("DCN affiliations are malformed")
            events.append({**row, "source_event_ref": ref, "eventPage": url})
        return {"events": events, "available_years": years, "load_year_url": render["loadYearUrl"]}

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        context_matches(ctx, self.kind)
        try:
            parsed = urlsplit(ctx.url)
        except ValueError as exc:
            raise ParseError("DCN list context URL is malformed") from exc
        if (
            parsed.scheme != "https"
            or parsed.netloc != HOST
            or parsed.path.rstrip("/") != "/eventdirector/en/eventsarchive"
            or parsed.query
            or parsed.fragment
            or ctx.source_ref not in {None, "dcn"}
            or any(char.isspace() or ord(char) < 32 for char in ctx.url)
        ):
            raise ParseError("DCN list parser requires its reviewed original index URL")
        events = tuple(
            DcnIndexEvent(
                row["source_event_ref"],
                row["name"],
                row["startDate"],
                row["endDate"],
                row["location"],
                row["venueName"],
                tuple(row["affiliations"]),
                row["results"],
                row["published"],
                row["online"],
                row["schedule"],
                row["frequent"],
                row["eventPage"],
                row.get("bannerUrl"),
                row.get("squareImage"),
            )
            for row in extract["events"]
        )
        payload = DcnIndexSheet(
            "dcn_index_sheet", events, tuple(extract["available_years"]), extract["load_year_url"]
        )
        return ParseResult(
            (Observation(ObservationScope("source_index", "dcn"), payload.kind, payload),),
            warnings=(
                ParseWarning(
                    "dcn_canonical_admission_pending",
                    "Offline source observations; kind admission and canonical projection remain pending.",
                ),
            ),
        )


class LegacyEventPage(IndexPage):
    kind = "dcn.event_metadata"

    def extract(self, body: bytes) -> JsonValue:
        if len(body) > MAX_BODY_BYTES:
            raise ExtractError("DCN HTML exceeds body byte bound")
        try:
            tree = HTMLParser(body.decode("utf-8"))
        except UnicodeError as exc:
            raise ExtractError("DCN HTML is not valid UTF-8") from exc
        if any("window.__NUXT__" in node.text() for node in tree.css("script")):
            raise ExtractError("DCN Nuxt event bodies need their separate reviewed kind")
        headers = tree.css("h1.hidden-xs.hidden-sm, h4.hidden-md.hidden-lg")
        if len(headers) != 2 or not any(
            "t5/core/pageinit" in node.text() for node in tree.css("script")
        ):
            raise ExtractError("DCN legacy event metadata structure is missing")
        values = []
        for heading in headers:
            small = heading.css_first("small")
            match = DATES.fullmatch(text(small.text())) if small else None
            if match is None:
                raise ExtractError("DCN legacy event date/location line changed")
            title = HTMLParser(heading.html or "")
            for child in title.css("small, .pull-right"):
                child.decompose()
            name = checked_text(text(title.text()), "legacy event name")
            values.append({"name": name, **match.groupdict()})
        if values[0] != values[1]:
            raise ExtractError("DCN legacy desktop and mobile metadata disagree")
        links = []
        for anchor in tree.css("a[href]"):
            if text(anchor.text()) == "Contest results":
                ref, url, tab = event_locator(anchor.attributes["href"] or "")
                if tab != "results":
                    raise ExtractError("DCN results locator is not a results tab")
                if {"ref": ref, "url": url} not in links:
                    links.append({"ref": ref, "url": url})
        if len(links) > 1:
            raise ExtractError("DCN legacy results links disagree")
        return {**values[0], "results_links": links}

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        context_matches(ctx, self.kind)
        try:
            ref, _, tab = event_locator(ctx.url)
        except ExtractError as exc:
            raise ParseError(str(exc)) from exc
        if (
            tab not in {None, "info"}
            or (ctx.source_ref and ctx.source_ref != ref)
            or any(link["ref"] != ref for link in extract["results_links"])
        ):
            raise ParseError("DCN metadata event ownership disagrees")
        payload = DcnEventMetadata(
            "dcn_event_metadata",
            ref,
            extract["name"],
            extract["start"],
            extract["end"],
            extract["location"],
            extract["results_links"][0]["url"] if extract["results_links"] else None,
        )
        return ParseResult(
            (Observation(ObservationScope("source_event", ref), payload.kind, payload),),
            warnings=(
                ParseWarning(
                    "dcn_results_unassessed",
                    "Metadata and a results locator do not establish public results or score availability.",
                ),
                ParseWarning(
                    "dcn_canonical_admission_pending",
                    "Offline source observations; kind admission and canonical projection remain pending.",
                ),
            ),
        )


@dataclass(frozen=True)
class DCNSource:
    name: str = "dcn"
    hosts: frozenset[str] = frozenset({HOST})
    page_kinds = {
        IndexPage.kind: IndexPage(),
        LegacyEventPage.kind: LegacyEventPage(),
        LegacyResultsPage.kind: LegacyResultsPage(),
    }

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return []


SOURCE = DCNSource()
