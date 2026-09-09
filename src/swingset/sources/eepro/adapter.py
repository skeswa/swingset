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
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        rows: list[JsonValue] = []
        for attrs, inner, visible in tags(source, "a"):
            href = attr(attrs, "href") or ""
            slug = query_value(href, "event")
            if slug:
                parent = source[
                    max(0, source.find(inner) - 180) : source.find(inner) + len(inner) + 180
                ]
                date = re.search(
                    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}[^<]{0,30}\d{4}",
                    parent,
                    re.I,
                )
                rows.append(
                    {
                        "slug": slug,
                        "name": visible,
                        "date": date.group(0).strip() if date else None,
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
    EXTRACT_VERSION = 1
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
                    "modified": cells[-2][2] if len(cells) >= 2 else None,
                    "size": cells[-1][2] if cells else None,
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
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "validators"

    def extract(self, body: bytes) -> JsonValue:
        source = body.decode("utf-8", "replace")
        result: list[JsonValue] = []
        for _, inner, visible in tags(source, "table"):
            heading_match = re.search(r"Division:\s*([^<\r\n]+)", inner, re.I)
            heading = heading_match.group(1).strip() if heading_match else visible[:150]
            parsed = _table(inner, heading)
            if len(parsed["rows"]) >= 2:
                result.append(parsed)
        if not result:
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
            if (
                len(parsed_rows[0]) == 1
                and first_text is not None
                and first_text.lower().startswith("division:")
            ):
                parsed_rows = parsed_rows[1:]
            if len(parsed_rows) < 2:
                continue
            heading = str(item.get("heading", ""))
            split = re.search(
                r"\b(Prelims?|Finals?|Semi(?:final)?s?|Quarterfinals?)\b", heading, re.I
            )
            round_name = split.group(1) if split else heading
            contest = heading[: split.start()].strip(" -") if split else heading
            table = ResultTable(
                heading, parsed_rows[0], tuple(ResultRow(row) for row in parsed_rows[1:])
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
        return ParseResult(tuple(sheets))

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
