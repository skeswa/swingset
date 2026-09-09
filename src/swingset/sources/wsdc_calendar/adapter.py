"""WSDC calendar adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseResult,
    WatchSpec,
)
from ..common import attr, tags, text
from ..records import CalendarRow

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}


def _dates(raw: str) -> tuple[str, str]:
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
        r"([A-Z][a-z]{2}) (\d+) (\d{4}) - ([A-Z][a-z]{2}) (\d+) (\d{4})", raw
    )
    if explicit_years:
        month1, day1, year1, month2, day2, year2 = explicit_years.groups()
        return date(int(year1), _MONTHS[month1], int(day1)).isoformat(), date(
            int(year2), _MONTHS[month2], int(day2)
        ).isoformat()
    raise ExtractError(f"unknown calendar date: {raw!r}")


class EventsPage:
    kind = "wsdc_calendar.events"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        rows: list[JsonValue] = []
        for attrs, inner, _ in tags(source, "tr"):
            if "event_name" not in inner:
                continue
            cells = tags(inner, "td")
            name_match = re.search(
                r'class=["\']event_name["\'][^>]*>(.*?)</div>', inner, re.I | re.S
            )
            type_match = re.search(
                r'class=["\']event_type["\'][^>]*>(.*?)</div>', inner, re.I | re.S
            )
            anchor = re.search(
                r"<a\b([^>]*)>(.*?)</a>", name_match.group(1) if name_match else "", re.I | re.S
            )
            country = re.search(r'[?&]country=([^&"\']*)', inner, re.I)
            rows.append(
                {
                    "classes": sorted((attr(attrs, "class") or "").split()),
                    "date": cells[0][2] if cells else "",
                    "name": text(
                        re.sub(
                            r"<[^>]+>",
                            " ",
                            anchor.group(2)
                            if anchor
                            else (name_match.group(1) if name_match else ""),
                        )
                    ),
                    "website": attr(anchor.group(1), "href") if anchor else None,
                    "event_type": text(re.sub(r"<[^>]+>", " ", type_match.group(1)))
                    if type_match
                    else "",
                    "location": cells[2][2] if len(cells) > 2 else "",
                    "country_code": country.group(1) if country else None,
                }
            )
        if not rows:
            raise ExtractError("calendar contains no event rows")
        return rows

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, list):
            raise ExtractError("calendar extract is not a list")
        observations: list[Observation] = []
        for item in extract:
            if not isinstance(item, dict):
                raise ExtractError("calendar row is not an object")
            date_raw = str(item.get("date", ""))
            start_date, end_date = _dates(date_raw)
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
        return ParseResult(tuple(observations))

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
