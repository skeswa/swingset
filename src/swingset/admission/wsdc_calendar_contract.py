"""Fail-closed accounting for the WSDC event calendar (D-0257).

`wsdc_calendar.events` reads every layout the calendar has had: today's
table and the 2016 `tr_events_load` table, the 2016 event cards,
FullCalendar's inline event array (2016 to 2022) and the map's marker calls
(2017 to 2022). The extract holds one row per listing. This module counts
the listings again from the body, without the extractor, and checks that
each one became a dated `calendar_row` or a bounded finding.

This module never imports the adapter: `contracts.py` imports it, and
`contracts.py` is in every recipe scope. The rules it repeats (the zstd
magic, the flag placeholders) are pinned equal to the adapter's by
`tests/test_history_sources.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from swingset.sources.base import ParseContext, ParseResult
from swingset.sources.records import CalendarRow

from .accounting import account_keys
from .report import Field, Guard

KINDS = frozenset({"wsdc_calendar.events"})

ROW_KEYS = frozenset(
    {"classes", "date", "name", "website", "event_type", "location", "country_code"}
)

#: Row classes the table prints. Trial and cancellation decide a row's
#: status, so an unseen class could change what the row means.
ROW_CLASSES = frozenset({"tr_events_load", "event-trial", "event-canceled", "event-unconfirmed"})

#: Listing types, compared casefolded with hyphens as spaces. A map marker
#: prints no type: its pin's file name stands in, and the pins `1` and
#: `white` and a bare pin folder (an empty type) carry none.
EVENT_TYPES = frozenset(
    {
        "",
        "registry event",
        "trial event",
        "registry event hiatus",
        "hiatus",
        "member activity",
        "non registry",
        "training multiday",
        "training multi day",
        "1",
        "white",
    }
)

#: Findings that withhold one listing and leave the rest of the capture
#: admissible: a known printed date format whose span is the source's own
#: error (`Feb 13 - 9, 2020`, FullCalendar's `2017-10-27 - 2017-10-02`). An
#: unknown format (`unrecognized_listing_date`) blocks.
KNOWN_BOUNDED_WARNINGS = frozenset({"impossible_listing_date"})

#: A zstd frame's first four bytes (the adapter's `_ZSTD_MAGIC`).
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_MAX_DECODED = 64 * 1024 * 1024

_QUOTE = "[\"']"


@dataclass(frozen=True)
class Accounting:
    fields: tuple[Field, ...]
    guards: tuple[Guard, ...]
    source_count: int | None
    interpreted_count: int
    terminal: str | None
    removal: str


def _text(body: bytes) -> str | None:
    """The body as text, a still zstd-compressed Wayback replay decoded; None if undecodable."""
    if body.startswith(ZSTD_MAGIC):
        import pyarrow as pa

        try:
            with pa.input_stream(pa.BufferReader(body), compression="zstd") as stream:
                body = stream.read(_MAX_DECODED + 1)
        except OSError:
            return None
        if len(body) > _MAX_DECODED:
            return None
    return bytes(body).decode("utf-8", "replace")


def listing_witness(body: bytes) -> tuple[str, int] | None:
    """The calendar layout and its listing count, read from the raw body.

    Counts are of source markup, not of extractor rows: FullCalendar
    `title` keys after the inline `events` array opens, numeric `addMarker`
    calls, `box` cards, else `event_name` cells plus `tr_events_load` rows.
    """
    text = _text(body)
    if text is None:
        return None
    if "fullCalendar(" in text:
        start = re.search(r"\bevents\s*:\s*\[", text)
        if start is None:
            return "fullcalendar", 0
        return "fullcalendar", len(
            re.findall(_QUOTE + r"?title" + _QUOTE + r"?\s*:", text[start.end() :])
        )
    markers = re.findall(r"addMarker\(\s*[-\d]", text)
    if markers:
        return "map", len(markers)
    cards = re.findall(r"class=" + _QUOTE + r"box" + _QUOTE, text)
    if cards and re.search(r"id=" + _QUOTE + r"event_list" + _QUOTE, text):
        return "cards", len(cards)
    names = re.findall(r"class=" + _QUOTE + r"event_name" + _QUOTE, text)
    legacy = re.findall(r"<tr\b[^>]*class=" + _QUOTE + r"[^\"']*\btr_events_load\b", text)
    return "table", len(names) + len(legacy)


def _ordered(row: CalendarRow) -> bool:
    try:
        return date.fromisoformat(row.start_date_raw) <= date.fromisoformat(row.end_date_raw)
    except ValueError:
        return False


def accounting(ctx: ParseContext, body: bytes, extract: Any, result: ParseResult) -> Accounting:
    if ctx.kind not in KINDS:
        raise ValueError(f"unsupported calendar contract kind: {ctx.kind}")
    fields: list[Field] = []
    rows = extract if isinstance(extract, list) else None
    if rows is None:
        fields.append(Field("$", "unknown", "Expected a list of calendar listings"))
        rows = []
    for index, row in enumerate(rows):
        account_keys(row, set(ROW_KEYS), f"$[{index}]", fields)
        if not isinstance(row, dict):
            continue
        for name in row.get("classes") or ():
            if name not in ROW_CLASSES:
                fields.append(Field(f"$[*].classes:{name}", "unknown", "Unreviewed row class"))
        kind = " ".join(str(row.get("event_type", "")).casefold().replace("-", " ").split())
        if kind not in EVENT_TYPES:
            fields.append(
                Field(f"$[*].event_type:{row.get('event_type')}", "unknown", "Unreviewed type")
            )
    calendar = [o.payload for o in result.observations if isinstance(o.payload, CalendarRow)]
    bounded = 0
    for warning in result.warnings:
        if warning.code in KNOWN_BOUNDED_WARNINGS:
            bounded += 1
            fields.append(
                Field(
                    f"warning:{warning.code}",
                    "excluded",
                    "The source printed a span that ends before it starts or names no real "
                    "day; the listing is withheld with its finding, never re-dated",
                )
            )
        elif warning.code == "unrecognized_country_flag":
            # The row publishes without a country; its other facts stand.
            fields.append(
                Field(
                    f"warning:{warning.code}",
                    "unknown",
                    warning.message + "; the row keeps no country",
                    critical=False,
                )
            )
        else:
            fields.append(Field(f"warning:{warning.code}", "unknown", warning.message))
    fields.extend(
        (
            Field(
                "omitted_listings",
                "excluded",
                "The calendar lists coming editions only; a listing it drops has passed, "
                "moved or gone, and is never read as a cancellation",
            ),
            Field(
                "country_flag_placeholder",
                "excluded",
                "An empty or `transparent` flag names no country; the row keeps none",
            ),
        )
    )
    witness = listing_witness(body)
    if witness is None:
        fields.append(Field("$body", "unknown", "The stored body does not decode"))
    guards = [
        Guard(
            "required_date_missing",
            all(row.start_date_raw and row.end_date_raw for row in calendar),
            "Every interpreted listing carries its printed start and end dates",
        ),
        Guard(
            "calendar_date_order",
            all(_ordered(row) for row in calendar),
            "Every interpreted listing is a real span that does not end before it starts",
        ),
        Guard(
            "calendar_name_missing",
            all(row.name_raw.strip() for row in calendar),
            "Every interpreted listing carries its printed name",
        ),
        Guard(
            "calendar_country_code",
            all(
                row.country_code_raw is None or re.fullmatch(r"[A-Z]{3}", row.country_code_raw)
                for row in calendar
            ),
            "A published country is an alpha-3 code from the row's flag, never a placeholder",
        ),
        Guard(
            "calendar_rows_accounted",
            len(calendar)
            + len(result.warnings)
            - sum(w.code == "unrecognized_country_flag" for w in result.warnings)
            == len(rows),
            "Each extracted listing is one calendar row or one date finding",
        ),
    ]
    interpreted = len(calendar) + bounded
    return Accounting(
        tuple(fields),
        tuple(guards),
        None if witness is None else witness[1],
        interpreted,
        # An empty listing is no witness that the calendar is empty.
        terminal="captured_document_end" if calendar else None,
        # A live capture replaces the previous live capture's rows on its
        # watch (D-0191); an archived capture is its own unit
        # (`watch_id/snapshot_id`) and replaces only itself. Neither removes
        # the other's rows (`state.observations`, D-0257).
        removal="watch",
    )
