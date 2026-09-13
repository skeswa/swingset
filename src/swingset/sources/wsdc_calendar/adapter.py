"""WSDC calendar adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from selectolax.parser import HTMLParser, Node

from ..base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseResult,
    ParseWarning,
    WatchSpec,
)
from ..common import text
from ..records import CalendarRow
from .legacy import fullcalendar_rows, map_rows

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}


def _dates(raw: str) -> tuple[str, str]:
    single = re.fullmatch(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", raw)
    if single:
        month, day, year = single.groups()
        value = date(int(year), _MONTHS[month], int(day)).isoformat()
        return value, value
    historical = re.fullmatch(
        r"(\d{1,2})(?:st|nd|rd|th) ([A-Za-z]+), (\d{4}) To (\d{1,2})(?:st|nd|rd|th) ([A-Za-z]+), (\d{4})",
        raw,
    )
    if historical:
        day1, month1, year1, day2, month2, year2 = historical.groups()
        return date(int(year1), _MONTHS[month1[:3]], int(day1)).isoformat(), date(
            int(year2), _MONTHS[month2[:3]], int(day2)
        ).isoformat()
    same_month = re.fullmatch(r"([A-Z][a-z]{2}) (\d+) - (\d+), (\d{4})", raw)
    if same_month:
        month, start, end, year = same_month.groups()
        return date(int(year), _MONTHS[month], int(start)).isoformat(), date(
            int(year), _MONTHS[month], int(end)
        ).isoformat()
    same_year = re.fullmatch(r"([A-Z][a-z]{2}) (\d+) - ([A-Z][a-z]{2}) (\d+), (\d{4})", raw)
    if same_year:
        month1, day1, month2, day2, year = same_year.groups()
        return date(int(year), _MONTHS[month1], int(day1)).isoformat(), date(
            int(year), _MONTHS[month2], int(day2)
        ).isoformat()
    explicit_years = re.fullmatch(
        r"([A-Z][a-z]{2}) (\d+),? (\d{4}) - ([A-Z][a-z]{2}) (\d+),? (\d{4})", raw
    )
    if explicit_years:
        month1, day1, year1, month2, day2, year2 = explicit_years.groups()
        return date(int(year1), _MONTHS[month1], int(day1)).isoformat(), date(
            int(year2), _MONTHS[month2], int(day2)
        ).isoformat()
    raise ExtractError(f"unknown calendar date: {raw!r}")


class EventsPage:
    kind = "wsdc_calendar.events"
    EXTRACT_VERSION = 4
    PARSER_VERSION = 7
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        embedded = fullcalendar_rows(source)
        if embedded is None:
            embedded = map_rows(source)
        if embedded is not None:
            return embedded
        rows: list[JsonValue] = []
        tree = HTMLParser(source)
        for row in tree.css("tr"):
            cells = [node for node in row.iter() if node.tag == "td"]
            classes = (row.attributes.get("class") or "").split()
            legacy = "tr_events_load" in classes
            if len(cells) < 3:
                continue
            name_node = cells[1].css_first(".event_name")
            if name_node is None and not legacy:
                continue
            name_node = name_node if name_node is not None else cells[1]
            anchor = name_node.css_first("a")
            type_node = cells[1].css_first(".event_type")
            name = text((anchor if anchor is not None else name_node).text(separator=" "))
            event_type = text(type_node.text(separator=" ")) if type_node else ""
            if legacy:
                event_type = text(cells[1].text(separator=" "))
                if event_type.startswith(name):
                    event_type = event_type[len(name) :].strip()
            country = re.search(r'[?&]country=([^&"\']*)', row.html or "", re.I)
            rows.append(
                {
                    "classes": sorted(classes),
                    "date": text(cells[0].text(separator=" ")),
                    "name": name,
                    "website": anchor.attributes.get("href") if anchor else None,
                    "event_type": event_type,
                    "location": text(cells[2].text(separator=" ")),
                    "country_code": country.group(1) if country else None,
                }
            )
        # The 2016 page contains 20 table rows and the complete listing as cards.
        # Preserve the full cards; their visible dates are independent of JS.
        cards: list[JsonValue] = []
        for card in tree.css("#event_list .box"):
            start = card.css_first(".top .left")
            end = card.css_first(".top .right")
            label = card.css_first(".top_text")
            anchor = label.css_first("a") if label else None
            location = card.css_first(".box2 .text2")
            if start is None or end is None or label is None:
                raise ExtractError("incomplete legacy calendar card")
            name = text((anchor if anchor else label).text(separator=" "))

            def card_date(node: Node) -> str:
                raw = text(node.text(separator=" "))
                match = re.fullmatch(r"(\d{1,2})(?: 00:00:00)? ([A-Za-z]{3}), (\d{4})", raw)
                if not match:
                    raise ExtractError(f"unknown calendar card date: {raw!r}")
                return f"{match[2]} {match[1]} {match[3]}"

            cards.append(
                {
                    "classes": [],
                    "date": card_date(start) + " - " + card_date(end),
                    "name": name,
                    "website": anchor.attributes.get("href") if anchor else None,
                    "event_type": text(label.text(separator=" "))[len(name) :].strip(),
                    "location": text(location.text(separator=" ")) if location else "",
                    "country_code": None,
                }
            )
        if cards:
            rows = cards
        if not rows:
            if re.search(
                r"event[-_](?:calendar|map)|tribe-events|Events Calendar|WSDC Event List",
                source,
                re.I,
            ):
                return []
            raise ExtractError("calendar contains no event rows")
        return rows

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, list):
            raise ExtractError("calendar extract is not a list")
        observations: list[Observation] = []
        warnings: list[ParseWarning] = []
        for item in extract:
            if not isinstance(item, dict):
                raise ExtractError("calendar row is not an object")
            date_raw = str(item.get("date", ""))
            try:
                start_date, end_date = _dates(date_raw)
            except (ValueError, KeyError) as error:
                warnings.append(ParseWarning("unrecognized_listing_date", str(error), item))
                continue
            payload = CalendarRow(
                kind="calendar_row",
                name_raw=str(item.get("name", "")),
                start_date_raw=start_date,
                end_date_raw=end_date,
                event_type_raw=str(item.get("event_type", "")),
                location_raw=str(item.get("location", "")),
                website=item.get("website") if isinstance(item.get("website"), str) else None,
                country_code_raw=item.get("country_code")
                if isinstance(item.get("country_code"), str)
                else None,
                row_classes=tuple(str(x) for x in item.get("classes", []) if isinstance(x, str)),
            )
            observations.append(
                Observation(ObservationScope("calendar", "wsdc"), payload.kind, payload)
            )
        return ParseResult(
            tuple(observations), warnings=tuple(warnings), legitimate_empty=not extract
        )

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()


@dataclass(frozen=True)
class CalendarSource:
    name: str = "wsdc_calendar"
    hosts: frozenset[str] = frozenset({"worldsdc.com"})
    page_kinds = {EventsPage.kind: EventsPage()}

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return [
            WatchSpec(
                "wsdc_calendar",
                self.name,
                "index",
                "GET",
                "https://worldsdc.com/events/",
                EventsPage.kind,
            )
        ]


SOURCE = CalendarSource()
