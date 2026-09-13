"""World Dance Registry routeInfo.json adapters."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

from ..base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseResult,
    ParseWarning,
    WatchSpec,
    watch_has_success,
)
from ..interpretation import declared
from ..records import AwardRow, AwardSheet, Cell, ResultRow, ResultTable, RoundSheet

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def source_ref_from_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if not parts or not _UUID.fullmatch(parts[0]):
        raise ValueError(f"WDR URL has no event UUID: {url}")
    return f"wdr:{parts[0].lower()}"


def _json(body: bytes) -> JsonValue:
    try:
        value: JsonValue = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtractError("WDR response is not JSON") from exc
    if not isinstance(value, dict):
        raise ExtractError("WDR response is not an object")
    return value


class RoundsPage:
    kind = "wdr.rounds"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 3
    change_mode = "validators"

    def extract(self, body: bytes) -> JsonValue:
        root = _json(body)
        data = root.get("data")
        scores = data.get("scoresData") if isinstance(data, dict) else None
        if not isinstance(scores, dict) or not isinstance(scores.get("results"), list):
            raise ExtractError("WDR scoresData.results is missing")
        return scores

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, dict) or not isinstance(extract.get("results"), list):
            raise ExtractError("WDR rounds extract is invalid")
        ref = ctx.source_ref or source_ref_from_url(ctx.url)
        event_name = str(extract.get("eventName", ""))
        output: list[Observation] = []
        warnings: list[ParseWarning] = []
        for raw in extract["results"]:
            if not isinstance(raw, dict):
                continue
            round_name = str(raw.get("roundName", ""))
            parts = round_name.split(" - ")
            contest = " - ".join(parts[:-1]) if len(parts) > 1 else round_name
            round_label = parts[-1]
            redacted = bool(raw.get("redacted", False))
            attribute_group = raw.get("attributeGroup")
            known_nasde = {
                "id": 1,
                "name": "NASDE",
                "attributes": [
                    {"id": 1, "name": "SVW indicates Swing Violation - Warning"},
                    {"id": 2, "name": "SV1 indicates Swing Violation - 1 Placement Drop"},
                    {"id": 3, "name": "SV2 indicates Swing Violation - 3 Placement Drop"},
                    {"id": 4, "name": "SV3 indicates Swing Violation - 10 Placement Drop"},
                ],
            }
            if attribute_group is not None and attribute_group != known_nasde:
                warnings.append(
                    ParseWarning(
                        "wdr_attribute_group_unverified",
                        "Unrecognized WDR attributeGroup is preserved without interpretation",
                        {"round_id": raw.get("id"), "attributeGroup": attribute_group},
                    )
                )
            tables: list[ResultTable] = []
            for raw_table in raw.get("results", []):
                if not isinstance(raw_table, dict) or not isinstance(raw_table.get("data"), list):
                    continue
                rows: list[tuple[Cell, ...]] = []
                for row_index, raw_row in enumerate(raw_table["data"]):
                    if not isinstance(raw_row, list):
                        continue
                    cells: list[Cell] = []
                    for raw_cell in raw_row:
                        if not isinstance(raw_cell, dict):
                            continue
                        cell_type = raw_cell.get("t")
                        value = raw_cell.get("v")
                        if (
                            cell_type == 2
                            and isinstance(value, str)
                            and re.fullmatch(r"S\d+", value)
                        ):
                            warnings.append(
                                ParseWarning(
                                    "wdr_s_callback_unverified",
                                    "WDR S<n> callback value is preserved without interpretation",
                                    {"round_id": raw.get("id"), "value": value},
                                )
                            )
                        # A redacted competitor's zero judge values are masks, not marks.
                        if (
                            redacted
                            and row_index > 0
                            and cell_type == 9
                            and str(value) in {"0", "0.0", "0.00"}
                        ):
                            value = None
                        attrs = tuple(
                            sorted(
                                (str(k), str(v))
                                for k, v in raw_cell.items()
                                if k != "v" and v is not None
                            )
                        )
                        cells.append(Cell(None if value is None else str(value), attrs))
                    rows.append(tuple(cells))
                if rows:
                    tables.append(
                        ResultTable(
                            str(raw.get("roundSubHeader", "")),
                            rows[0],
                            tuple(ResultRow(row) for row in rows[1:]),
                            redacted,
                            str(raw.get("attributeGroup"))
                            if raw.get("attributeGroup") is not None
                            else None,
                        )
                    )
            payload = RoundSheet(
                "round_sheet",
                ref,
                str(raw.get("id", round_name)),
                contest,
                round_label,
                tuple(tables),
                event_name,
                str(raw["roundSubHeader"]) if raw.get("roundSubHeader") is not None else None,
            )
            output.append(Observation(ObservationScope("source_event", ref), payload.kind, payload))
            if "final" in round_label.casefold() and tables:
                warnings.append(
                    ParseWarning(
                        "wdr_finals_bib_unverified",
                        "WDR finals Bib # is preserved without assigning it to a partner",
                        {"round_id": raw.get("id")},
                    )
                )
        counts = Counter(warning.code for warning in warnings)
        unique = {warning.code: warning for warning in warnings}
        aggregated = tuple(
            ParseWarning(
                code, warning.message, {"occurrences": counts[code], "sample": warning.evidence}
            )
            for code, warning in sorted(unique.items())
        )
        return ParseResult(tuple(output), warnings=aggregated)

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset() if watch_has_success(watch) else frozenset({403})


class AwardsPage:
    kind = "wdr.awards"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "validators"

    def extract(self, body: bytes) -> JsonValue:
        root = _json(body)
        data = root.get("data")
        awards = data.get("awardsData") if isinstance(data, dict) else None
        if not isinstance(awards, dict) or not isinstance(awards.get("results"), list):
            raise ExtractError("WDR awardsData.results is missing")
        return awards

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, dict) or not isinstance(extract.get("results"), list):
            raise ExtractError("WDR awards extract is invalid")
        ref = ctx.source_ref or source_ref_from_url(ctx.url)
        output: list[Observation] = []
        for raw in extract["results"]:
            if not isinstance(raw, dict):
                continue
            data = raw.get("data", [])
            rows: list[AwardRow] = []
            if isinstance(data, list):
                for row in data[1:]:
                    if isinstance(row, list) and len(row) >= 3:
                        rows.append(AwardRow(str(row[0]), str(row[1]), str(row[2])))
            payload = AwardSheet("award_sheet", ref, str(raw.get("roundName", "")), tuple(rows))
            output.append(Observation(ObservationScope("source_event", ref), payload.kind, payload))
        return ParseResult(tuple(output))

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset() if watch_has_success(watch) else frozenset({403})


def watches_from_overrides(rows: object) -> list[WatchSpec]:
    result: list[WatchSpec] = []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict) or row.get("source") not in {"worlddanceregistry", "wdr"}:
            continue
        url = str(row.get("url", ""))
        ref = source_ref_from_url(url)
        uuid = ref.split(":", 1)[1]
        base = f"https://scores.worlddanceregistry.com/{uuid}"
        result.extend(
            (
                WatchSpec(
                    f"wdr-rounds-{uuid}",
                    "wdr",
                    "event",
                    "GET",
                    f"{base}/rounds/routeInfo.json",
                    RoundsPage.kind,
                    source_ref=ref,
                    notes=str(row.get("notes", "")) or None,
                ),
                WatchSpec(
                    f"wdr-awards-{uuid}",
                    "wdr",
                    "json",
                    "GET",
                    f"{base}/awards/routeInfo.json",
                    AwardsPage.kind,
                    source_ref=ref,
                    notes=str(row.get("notes", "")) or None,
                ),
            )
        )
    return result


@dataclass(frozen=True)
class WDRSource:
    name: str = "wdr"
    hosts: frozenset[str] = frozenset({"scores.worlddanceregistry.com"})
    page_kinds = {RoundsPage.kind: RoundsPage(), AwardsPage.kind: AwardsPage()}

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return watches_from_overrides(overrides)


SOURCE = WDRSource()
