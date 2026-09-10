"""Pure EEPro HTML extractors. No network access belongs here."""

from __future__ import annotations

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
from ..common import absolute, attr, canonical_attrs, query_value, tags
from ..records import Cell, FileRow, ResultRow, ResultTable, RoundSheet, SourceEventRow


def _table(table_html: str, heading: str) -> dict[str, JsonValue]:
    rows: list[JsonValue] = []
    for _, inner, _ in tags(table_html, "tr"):
        cells: list[JsonValue] = []
        for cell_tag in ("th", "td"):
            for attrs, _, visible in tags(inner, cell_tag):
                cells.append(
                    {
                        "text": visible,
                        "attributes": canonical_attrs(
                            attrs, ("title", "data-wsdc", "data-state", "class")
                        ),
                    }
                )
        if cells:
            rows.append(cells)
    return {"heading": heading, "rows": rows}


class IndexPage:
    kind = "eepro.index"
    EXTRACT_VERSION = 2
    PARSER_VERSION = 1
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        rows: list[JsonValue] = []
        for attrs, inner, visible in tags(source, "a"):
            href = attr(attrs, "href") or ""
            slug = query_value(href, "event")
            if slug:
                fields = {
                    (attr(div_attrs, "class") or ""): div_visible
                    for div_attrs, _, div_visible in tags(inner, "div")
                }
                name = fields.get("event-title", visible)
                date = fields.get("event-date")
                rows.append(
                    {
                        "slug": slug,
                        "name": name,
                        "date": date,
                        "url": href,
                    }
                )
        if not rows:
            raise ExtractError("EEPro index has no event links")
        return rows

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, list):
            raise ExtractError("EEPro index extract is not a list")
        observations: list[Observation] = []
        watches: list[WatchSpec] = []
        for item in extract:
            if not isinstance(item, dict):
                continue
            slug = str(item["slug"])
            ref = f"eepro:{slug}"
            url = absolute(ctx.url, str(item["url"]))
            payload = SourceEventRow(
                "source_event_row",
                ref,
                str(item["name"]),
                str(item["date"]) if item.get("date") else None,
                url,
            )
            observations.append(
                Observation(ObservationScope("source_index", "eepro"), payload.kind, payload)
            )
            directory = f"https://eepro.com/results/{slug}/"
            watches.append(
                WatchSpec(
                    f"eepro-autoindex-{slug}",
                    "eepro",
                    "autoindex",
                    "GET",
                    directory,
                    AutoIndexPage.kind,
                    source_ref=ref,
                )
            )
        return ParseResult(tuple(observations), tuple(watches))

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()


class AutoIndexPage:
    kind = "eepro.autoindex"
    EXTRACT_VERSION = 2
    PARSER_VERSION = 1
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        files: list[JsonValue] = []
        for _, inner, _ in tags(source, "tr"):
            cells = tags(inner, "td")
            link = re.search(r"<a\b([^>]*)>(.*?)</a>", inner, re.I | re.S)
            if link is None:
                continue
            name = re.sub(r"<[^>]+>", "", link.group(2)).strip()
            href = attr(link.group(1), "href") or ""
            if not re.search(r"\.(?:html?|pdf)$", name, re.I):
                continue
            files.append(
                {
                    "name": name,
                    "href": href,
                    # Standard Apache autoindex cells are icon, name,
                    # modified, size, and description.
                    "modified": cells[-3][2] if len(cells) >= 3 else None,
                    "size": cells[-2][2] if len(cells) >= 2 else None,
                }
            )
        if not files:
            raise ExtractError("EEPro directory has no result files")
        return files

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, list):
            raise ExtractError("EEPro autoindex extract is not a list")
        obs: list[Observation] = []
        watches: list[WatchSpec] = []
        ref = ctx.source_ref or "eepro:unknown"
        for item in extract:
            if not isinstance(item, dict):
                continue
            url = absolute(ctx.url, str(item["href"]))
            name = str(item["name"])
            payload = FileRow(
                "file_row",
                name,
                url,
                str(item["modified"]) if item.get("modified") else None,
                str(item["size"]) if item.get("size") else None,
            )
            obs.append(Observation(ObservationScope("source_event", ref), payload.kind, payload))
            if name.lower().endswith((".html", ".htm")):
                watches.append(
                    WatchSpec(
                        f"eepro-round-{ref.split(':', 1)[-1]}-{name}",
                        "eepro",
                        "round",
                        "GET",
                        url,
                        RoundPage.kind,
                        source_ref=ref,
                    )
                )
        return ParseResult(tuple(obs), tuple(watches))

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()


class RoundPage:
    kind = "eepro.round"
    EXTRACT_VERSION = 3
    PARSER_VERSION = 6
    change_mode = "validators"

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        result: list[JsonValue] = []
        for _, inner, visible in tags(source, "table"):
            table_rows = tags(inner, "tr")
            heading_cells = (
                tags(table_rows[0][1], "th") + tags(table_rows[0][1], "td") if table_rows else []
            )
            heading = heading_cells[0][2] if heading_cells else visible[:150]
            heading = re.sub(r"^Division:\s*", "", heading, flags=re.I)
            parsed = _table(inner, heading)
            if len(parsed["rows"]) >= 2:
                result.append(parsed)
        if not result:
            if re.search(r"<h1[^>]*>\s*Coming soon\s*</h1>", source, re.I):
                return []
            raise ExtractError("EEPro round has no result table")
        return result

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, list):
            raise ExtractError("EEPro round extract is not a list")
        ref = ctx.source_ref or "eepro:unknown"
        sheets: list[Observation] = []
        for index, item in enumerate(extract):
            if not isinstance(item, dict) or not isinstance(item.get("rows"), list):
                continue
            raw_rows = item["rows"]
            parsed_rows: list[tuple[Cell, ...]] = []
            for row in raw_rows:
                if not isinstance(row, list):
                    continue
                parsed_rows.append(
                    tuple(
                        Cell(
                            str(c.get("text", "")),
                            tuple(
                                sorted((str(k), str(v)) for k, v in c.get("attributes", {}).items())
                            ),
                        )
                        for c in row
                        if isinstance(c, dict)
                    )
                )
            if len(parsed_rows) < 2:
                continue
            first_text = parsed_rows[0][0].text if parsed_rows[0] else None
            if len(parsed_rows[0]) == 1 and first_text is not None:
                parsed_rows = parsed_rows[1:]
            if len(parsed_rows) < 2:
                continue
            heading_raw = str(item.get("heading", ""))
            heading = re.split(
                r"(?:[-–—]\s*)?\b\d+\s+competed\b|When\s+marks\b|(?:All\s+)?ties\s+broken\b|Y\s*=",
                heading_raw,
                maxsplit=1,
                flags=re.I,
            )[0].strip(" -")
            split = re.search(
                r"\b(Prelim(?:inar(?:y|ies))?s?|Quarters?|(?:Semi|Quarter)[ -]?final(?:ist)?s?|Semis?|Final(?:ist)?s?)\b",
                heading,
                re.I,
            )
            header_names = {cell.text.casefold() for cell in parsed_rows[0] if cell.text}
            final_layout = {"place", "marks sorted"} <= header_names
            round_name = split.group(1) if split else "Finals" if final_layout else heading
            contest = heading[: split.start()].strip(" -") if split else heading
            if split:
                qualifier = heading[split.end() :].strip(" -")
                if qualifier:
                    contest = f"{contest} {qualifier}"
            table = ResultTable(
                heading_raw, parsed_rows[0], tuple(ResultRow(row) for row in parsed_rows[1:])
            )
            payload = RoundSheet(
                "round_sheet",
                ref,
                f"{urlparse(ctx.url).path}#{index}",
                contest,
                round_name,
                (table,),
            )
            sheets.append(Observation(ObservationScope("source_event", ref), payload.kind, payload))
        return ParseResult(tuple(sheets), legitimate_empty=not extract)

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()


@dataclass(frozen=True)
class EEProSource:
    name: str = "eepro"
    hosts: frozenset[str] = frozenset({"eepro.com"})
    page_kinds = {
        IndexPage.kind: IndexPage(),
        AutoIndexPage.kind: AutoIndexPage(),
        RoundPage.kind: RoundPage(),
    }

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return [
            WatchSpec(
                "eepro-index",
                self.name,
                "index",
                "GET",
                "https://eepro.com/results/event.php",
                IndexPage.kind,
            )
        ]


SOURCE = EEProSource()
