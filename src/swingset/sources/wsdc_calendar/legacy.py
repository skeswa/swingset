"""Retained calendar data embedded in older server-rendered pages."""

from __future__ import annotations

import ast
import json
import re
from datetime import date, timedelta
from urllib.parse import unquote, urlsplit

from selectolax.parser import HTMLParser

from swingset.sources.base import ExtractError, JsonValue
from swingset.sources.common import text


def fullcalendar_rows(source: str) -> list[JsonValue] | None:
    if "fullCalendar(" not in source:
        return None
    match = re.search(r"\bevents\s*:\s*(?=\[)", source)
    if match is None:
        raise ExtractError("FullCalendar has no captured inline event data")
    try:
        events, _ = json.JSONDecoder().raw_decode(source[match.end() :])
    except ValueError:
        events = _literal_events(source[match.end() :])
    if not isinstance(events, list):
        raise ExtractError("FullCalendar event data is not an array")
    rows: list[JsonValue] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("title"), str):
            raise ExtractError("FullCalendar event lacks its printed title")
        name, separator, event_type = event["title"].rpartition(" - ")
        if not separator:
            raise ExtractError("FullCalendar title lacks a recognized event type")
        raw = f"{event.get('start')} - {event.get('end')}"
        try:
            start = date.fromisoformat(str(event["start"]))
            # FullCalendar v2+ all-day end is exclusive. See fixture README.
            end = date.fromisoformat(str(event["end"])) - timedelta(days=1)
            if end < start:
                raise ValueError("event ends before it starts")
            raw = start.strftime("%b %d %Y") + " - " + end.strftime("%b %d %Y")
        except (ValueError, KeyError):
            # Keep malformed source dates for the adapter's row-level finding.
            pass
        rows.append(
            {
                "classes": [],
                "date": raw,
                "name": name,
                "website": event.get("url"),
                "event_type": event_type,
                "location": "",
                "country_code": None,
            }
        )
    return rows


_STRING = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''


def _literal_events(source: str) -> list[dict[str, str]]:
    """Read only object arrays of string values; never evaluate JavaScript."""
    field = re.compile(r"\s*(?:,\s*)?([A-Za-z]+)\s*:\s*(" + _STRING + r")")
    position = 1
    result: list[dict[str, str]] = []
    while True:
        space = re.match(r"[\s,]*", source[position:])
        position += space.end() if space else 0
        if source[position : position + 1] == "]":
            return result
        if source[position : position + 1] != "{":
            raise ExtractError("unsupported FullCalendar literal array")
        position += 1
        item = {}
        while match := field.match(source, position):
            try:
                item[match[1]] = ast.literal_eval(match[2].replace(r"\/", "/"))
            except (ValueError, SyntaxError) as error:
                raise ExtractError("invalid FullCalendar string literal") from error
            position = match.end()
        closing = re.match(r"\s*}", source[position:])
        if closing is None:
            raise ExtractError("unsupported FullCalendar event literal")
        position += closing.end()
        result.append(item)


def map_rows(source: str) -> list[JsonValue] | None:
    if not re.search(r"addMarker\(\s*[-\d]", source):
        return None
    call = re.compile(
        r"addMarker\(\s*[-\d.]+\s*,\s*[-\d.]+\s*,\s*("
        + _STRING
        + r")\s*,\s*("
        + _STRING
        + r")\s*\)"
    )
    rows: list[JsonValue] = []
    for match in call.finditer(source):
        try:
            markup, icon = [ast.literal_eval(value.replace(r"\/", "/")) for value in match.groups()]
        except (ValueError, SyntaxError) as error:
            raise ExtractError("invalid captured map marker string") from error
        node = HTMLParser(markup)
        anchor = node.css_first("a[href]")
        date_match = re.search(r"</a>\s*<br\s*/?>(.*?)<br", markup, re.I | re.S)
        if anchor is None or date_match is None:
            raise ExtractError("captured map marker lacks a name or printed date")
        rows.append(
            {
                "classes": [],
                "name": text(anchor.text(separator=" ")),
                "date": text(date_match[1]),
                "website": anchor.attributes.get("href"),
                "event_type": unquote(urlsplit(icon).path.rsplit("/", 1)[-1])
                .removesuffix(".png")
                .replace("-", " "),
                "location": "",
                "country_code": None,
            }
        )
    if len(rows) != len(re.findall(r"addMarker\(\s*[-\d]", source)):
        raise ExtractError("unrecognized captured map marker calls")
    return rows
