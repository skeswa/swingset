"""Pure scoring.dance adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseResult,
    WatchSpec,
)
from ..common import absolute, attr, canonical_attrs, tags
from ..records import Cell, EventSheet, ResultRow, ResultTable, RoundSheet, SourceEventRow

EVENT_RE = re.compile(r"/events/(\d+)(?:/|$)")
ROUND_RE = re.compile(r"/results/([^/?#]+)\.html")


def _links(body: bytes, pattern: re.Pattern[str]) -> list[dict[str, str]]:
    result = []
    for attrs, _, visible in tags(body.decode("utf-8", "replace"), "a"):
        href = attr(attrs, "href") or ""
        if match := pattern.search(href):
            result.append({"id": match.group(1), "name": visible, "href": href})
    if not result:
        raise ExtractError("expected scoring.dance links are missing")
    return result


class SitemapPage:
    kind = "scoringdance.sitemap"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        ids = sorted(set(EVENT_RE.findall(body.decode("utf-8", "replace"))), key=int)
        if not ids:
            raise ExtractError("sitemap has no event ids")
        return ids

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        observations = []
        watches = []
        for event_id in extract:
            ref = f"scoringdance:{event_id}"
            url = f"https://scoring.dance/enUS/events/{event_id}/results/"
            payload = SourceEventRow("source_event_row", ref, None, None, url)
            observations.append(
                Observation(ObservationScope("source_index", "scoringdance"), payload.kind, payload)
            )
            watches.append(
                WatchSpec("", "scoringdance", "event", "GET", url, EventPage.kind, source_ref=ref)
            )
        return ParseResult(tuple(observations), tuple(watches))

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()


class RecentPage(SitemapPage):
    kind = "scoringdance.recent"
    EXTRACT_VERSION = 2
    PARSER_VERSION = 2

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        match = re.search(r"this\.events\s*=\s*this\.processEvents\((\[.*?\])\);", source, re.S)
        if match is None:
            raise ExtractError("recent page event data is missing")
        try:
            events = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ExtractError("recent page event data is invalid") from exc
        return [
            {
                "id": str(event["id"]),
                "name": str(event["name"]),
                "date": str(event.get("date_formatted", "")),
                "href": f"/enUS/events/{event['id']}/results/",
            }
            for event in events
            if isinstance(event, dict) and event.get("id") is not None
        ]

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        observations = []
        for item in extract:
            ref = f"scoringdance:{item['id']}"
            payload = SourceEventRow(
                "source_event_row", ref, item["name"], item["date"], absolute(ctx.url, item["href"])
            )
            observations.append(
                Observation(ObservationScope("source_index", "scoringdance"), payload.kind, payload)
            )
        return ParseResult(tuple(observations))


class EventPage(SitemapPage):
    kind = "scoringdance.event"
    EXTRACT_VERSION = 3
    PARSER_VERSION = 3

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        title_match = re.search(
            r'<meta\s+property="og:title"\s+content="([^"]+)"', source, re.I
        )
        name = title_match.group(1).strip() if title_match else None
        if name:
            name = re.sub(r"\s+results\s*$", "", name, flags=re.I)
        date_match = re.search(r"\bat\s+(\d{2}/\d{2}/\d{4})\s*\.", source, re.I)
        return {
            "name": name,
            "date": date_match.group(1) if date_match else None,
            "rounds": _links(body, ROUND_RE),
        }

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, dict) or not isinstance(extract.get("rounds"), list):
            raise ExtractError("scoring.dance event extract is invalid")
        ref = ctx.source_ref or "scoringdance:unknown"
        rounds = extract["rounds"]
        pairs = tuple(
            (str(item["id"]), str(item["name"])) for item in rounds if isinstance(item, dict)
        )
        watches = tuple(
            WatchSpec(
                "",
                "scoringdance",
                "round",
                "GET",
                absolute(ctx.url, item["href"]),
                RoundPage.kind,
                source_ref=ref,
            )
            for item in rounds
            if isinstance(item, dict)
        )
        payload = EventSheet("event_sheet", ref, None, pairs)
        return ParseResult(
            (
                Observation(ObservationScope("source_event", ref), payload.kind, payload),
                Observation(
                    ObservationScope("source_index", ref),
                    "source_event_row",
                    SourceEventRow(
                        "source_event_row",
                        ref,
                        str(extract["name"]) if extract.get("name") else None,
                        str(extract["date"]) if extract.get("date") else None,
                        ctx.url,
                    ),
                ),
            ),
            watches,
        )


class RoundPage(SitemapPage):
    kind = "scoringdance.round"
    EXTRACT_VERSION = 3
    PARSER_VERSION = 3

    def extract(self, body: bytes) -> JsonValue:
        result = []
        source = body.decode("utf-8", "replace")
        title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', source, re.I)
        heading = title_match.group(1).split(" - ", 1)[0] if title_match else ""
        for attrs, markup, _visible in tags(source, "table"):
            if "table" not in (attr(attrs, "class") or "").split():
                continue
            rows = []
            for row_attrs, row_markup, _ in tags(markup, "tr"):
                cells = []
                for cell_tag in ("th", "td"):
                    for cell_attrs, cell_markup, value in tags(row_markup, cell_tag):
                        attributes = canonical_attrs(
                            cell_attrs, ("title", "data-wsdc", "data-state", "class")
                        )
                        if state := attr(row_attrs, "data-state"):
                            attributes["row-data-state"] = state
                        if "data-wsdc" not in attributes:
                            for child_attrs, _, _ in tags(cell_markup, "a"):
                                if wsdc_id := attr(child_attrs, "data-wsdc"):
                                    attributes["data-wsdc"] = wsdc_id
                                    break
                        if not value:
                            for image_attrs, _, _ in tags(cell_markup, "img"):
                                if alt := attr(image_attrs, "alt"):
                                    value = alt
                                    break
                        cells.append({"text": value, "attributes": attributes})
                if cells:
                    rows.append(cells)
            result.append({"heading": heading, "rows": rows})
        if not result:
            raise ExtractError("round has no result table")
        # scoring.dance renders competitor headings as blank cells. Their
        # position is stable: prelims split roles into two tables; finals put
        # both roles in one row. Give the shared projector explicit captions.
        is_final = bool(re.search(r"\bfinal\b", heading, re.I))
        for table_number, table in enumerate(result):
            table_rows = table.get("rows")
            if (
                not isinstance(table_rows, list)
                or not table_rows
                or not isinstance(table_rows[0], list)
            ):
                continue
            headers = table_rows[0]
            if len(headers) > 1 and isinstance(headers[1], dict):
                headers[1]["text"] = "Leader" if is_final or table_number == 0 else "Follower"
            if is_final and len(headers) > 2 and isinstance(headers[2], dict):
                headers[2]["text"] = "Follower"
        return result

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        ref = ctx.source_ref or "scoringdance:unknown"
        output = []
        for number, item in enumerate(extract):
            rows = [
                tuple(Cell(cell["text"], tuple(sorted(cell["attributes"].items()))) for cell in row)
                for row in item["rows"]
            ]
            if len(rows) < 2:
                continue
            table = ResultTable(item["heading"], rows[0], tuple(ResultRow(row) for row in rows[1:]))
            round_ref = urlparse(ctx.url).path.rsplit("/", 1)[-1].removesuffix(".html")
            heading = str(item["heading"])
            round_match = re.search(
                r"\b(prelim|semi(?:final)?|quarterfinal|final)\b", heading, re.I
            )
            contest = heading[: round_match.start()].strip() if round_match else heading
            round_name = round_match.group(1) if round_match else heading
            payload = RoundSheet(
                "round_sheet", ref, f"{round_ref}#{number}", contest, round_name, (table,)
            )
            output.append(Observation(ObservationScope("source_event", ref), payload.kind, payload))
        return ParseResult(tuple(output))


@dataclass(frozen=True)
class ScoringDanceSource:
    name: str = "scoringdance"
    hosts: frozenset[str] = frozenset({"scoring.dance"})
    page_kinds = {
        SitemapPage.kind: SitemapPage(),
        RecentPage.kind: RecentPage(),
        EventPage.kind: EventPage(),
        RoundPage.kind: RoundPage(),
    }

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return [
            WatchSpec(
                "", self.name, "index", "GET", "https://scoring.dance/sitemap.xml", SitemapPage.kind
            ),
            WatchSpec(
                "", self.name, "index", "GET", "https://scoring.dance/enUS/recent", RecentPage.kind
            ),
        ]


SOURCE = ScoringDanceSource()
