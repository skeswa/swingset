"""The former WSDC member-event listing; unknown dates remain month precise."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from selectolax.parser import HTMLParser

from swingset.sources.base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseResult,
    ParseWarning,
    WatchSpec,
)
from swingset.sources.common import text
from swingset.sources.records import CalendarRow
from swingset.sources.wsdc_calendar.adapter import _dates


def historical_dates(raw: str) -> tuple[str, str]:
    cleaned = re.sub(r"[.*]", "", raw).strip()
    tbd = re.fullmatch(r"([A-Za-z]+) TBD,? (\d{4})", cleaned)
    if tbd:
        month = datetime.strptime(tbd[1][:3], "%b").month
        value = f"{int(tbd[2]):04d}-{month:02d}"
        return value, value
    cleaned = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", cleaned)
    cleaned = re.sub(r"\s*-\s*", " - ", cleaned)
    cleaned = re.sub(r"([A-Za-z]+)(?=\d)", r"\1 ", cleaned)
    cleaned = re.sub(
        r"\b(January|February|March|April|June|July|August|September|Sept|October|November|December)\b",
        lambda m: m[1][:3],
        cleaned,
    )
    start, end = _dates(cleaned)
    if start > end:
        start = f"{int(start[:4]) - 1:04d}" + start[4:]
    return start, end


class EventsPage:
    kind = "swingdancecouncil.events"
    EXTRACT_VERSION = 2
    PARSER_VERSION = 2
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        html = body.decode("utf-8", "replace")
        rows = []
        for row in HTMLParser(html).css("tr"):
            cells = [node for node in row.iter() if node.tag == "td"]
            if len(cells) < 3:
                continue
            date_text = text(cells[0].text(separator=" "))
            if not re.match(r"^[A-Za-z]{3,9}\.?\s+(?:\d|TBD)", date_text):
                continue
            link = cells[1].css_first("a[href]")
            rows.append(
                {
                    "date": date_text,
                    "name": text(cells[1].text(separator=" ")),
                    "location": text(cells[2].text(separator=" ")),
                    "website": link.attributes.get("href") if link else None,
                }
            )
        if not rows and not re.search(
            r"Member (?:Registry|Sponsored) Events|Other Member Sponsored", html, re.I
        ):
            raise ExtractError("not a recognized old WSDC listing")
        return rows

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, list):
            raise ExtractError("old WSDC listing must contain rows")
        result = []
        warnings = []
        for item in extract:
            try:
                start, end = historical_dates(item["date"])
            except ValueError as error:
                warnings.append(ParseWarning("unrecognized_listing_date", str(error), item))
                continue
            payload = CalendarRow(
                "calendar_row",
                item["name"],
                start,
                end,
                "trial" if "nonregupcomingevents" in ctx.url.lower() else "registry",
                item["location"],
                item["website"],
                None,
                (),
            )
            result.append(
                Observation(ObservationScope("calendar", "wsdc-history"), payload.kind, payload)
            )
        return ParseResult(tuple(result), warnings=tuple(warnings), legitimate_empty=not extract)

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()


@dataclass(frozen=True)
class HistoricalCouncilSource:
    name: str = "swingdancecouncil"
    hosts: frozenset[str] = frozenset({"swingdancecouncil.com"})
    page_kinds = {EventsPage.kind: EventsPage()}

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return []


SOURCE = HistoricalCouncilSource()
