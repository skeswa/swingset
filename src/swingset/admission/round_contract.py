"""Round sheet accounting with source-specific columns and ragged judge panels."""

import re
from typing import Any

from swingset.sources.base import ParseResult
from swingset.sources.records import RoundSheet

from .accounting import account_keys
from .report import Field, Guard


def round_accounting(
    kind: str, extract: Any, result: ParseResult, fields: list[Field], guards: list[Guard]
) -> tuple[int | None, int]:
    sheets = [o.payload for o in result.observations if isinstance(o.payload, RoundSheet)]
    tables = [table for sheet in sheets for table in sheet.tables]
    interpreted = sum(len(table.rows) for table in tables)
    count = 0
    if kind == "wdr.rounds":
        # WDR's typed, ragged cells are accounted for explicitly, never padded.
        account_keys(extract, {"eventName", "results"}, "$", fields)
        raw_rounds = extract.get("results", []) if isinstance(extract, dict) else []
        for index, raw in enumerate(raw_rounds):
            account_keys(
                raw,
                {"id", "roundName", "redacted", "attributeGroup", "roundSubHeader", "results"},
                f"round[{index}]",
                fields,
            )
            method = raw.get("roundSubHeader") if isinstance(raw, dict) else None
            known_method = isinstance(method, str) and method in {
                "Placement Order",
                "Sum of Yes(10) / Alt 1(4.5) 2(4.3) 3(4.2) / No(0)",
                "Average Raw Scores",
            }
            fields.append(
                Field(
                    f"round[{index}].scoring_method={method}",
                    "excluded"
                    if method == "Average Raw Scores"
                    else "handled"
                    if known_method
                    else "unknown",
                    "Raw numeric scores/average/medal retained; canonical contest unsupported"
                    if method == "Average Raw Scores"
                    else "Explicit source scoring method"
                    if known_method
                    else "Unrecognized source scoring method",
                )
            )
            for table in raw.get("results", []) if isinstance(raw, dict) else []:
                account_keys(table, {"data"}, f"round[{index}].table", fields)
                rows = table.get("data", []) if isinstance(table, dict) else []
                count += max(0, len(rows) - 1)
                for row_index, row in enumerate(rows):
                    for cell in row if isinstance(row, list) else []:
                        account_keys(
                            cell, {"v", "t", "p", "s", "a"}, f"round[{index}].cell", fields
                        )
                        if isinstance(cell, dict) and cell.get("t") not in {
                            2,
                            3,
                            4,
                            5,
                            6,
                            7,
                            8,
                            9,
                            11,
                            12,
                        }:
                            fields.append(
                                Field(
                                    f"round[{index}].cell.type={cell.get('t')}",
                                    "unknown",
                                    "Unrecognized WDR cell type",
                                )
                            )
                        if (
                            row_index
                            and isinstance(cell, dict)
                            and not _wdr_value_known(cell, method)
                        ):
                            fields.append(
                                Field(
                                    f"round[{index}].cell.value={cell.get('v')}",
                                    "unknown",
                                    "Unrecognized critical WDR value for its scoring method",
                                )
                            )
    elif isinstance(extract, list):
        for index, item in enumerate(extract):
            account_keys(item, {"heading", "rows", "event_name"}, f"table[{index}]", fields)
            rows = item.get("rows", []) if isinstance(item, dict) else []
            has_title = (
                kind == "eepro.round"
                and bool(rows)
                and isinstance(rows[0], list)
                and len(rows[0]) == 1
            )
            count += max(0, len(rows) - 1 - int(has_title))
            for row in rows:
                for cell in row if isinstance(row, list) else []:
                    account_keys(cell, {"text", "attributes"}, f"table[{index}].cell", fields)
    else:
        return None, interpreted
    for index, table in enumerate(tables):
        headers = [(cell.text or "").casefold().strip() for cell in table.headers]
        competitor = next(
            (
                i
                for i, h in enumerate(headers)
                if any(w in h for w in ("competitor", "leader", "follower", "couple", "dancer"))
            ),
            None,
        )
        bib = next((i for i, h in enumerate(headers) if "bib" in h), None)
        for column, cell in enumerate(table.headers):
            header = headers[column]
            attrs = dict(cell.attributes)
            path = f"table[{index}].header[{column}]={header}"
            if kind == "wdr.rounds" and (
                (attrs.get("t"), header)
                in {("3", "#"), ("8", "average"), ("11", "medal"), ("5", "solo")}
            ):
                fields.append(
                    Field(
                        path,
                        "excluded",
                        "Displayed row ordinal; source order retained"
                        if header == "#"
                        else "Raw numeric or Solo evidence retained; canonical contest unsupported",
                    )
                )
                continue
            if kind == "eepro.round" and header == "avg":
                fields.append(
                    Field(
                        path,
                        "excluded",
                        "Literal Avg aggregate retained; numeric preliminary methods have no canonical scoring representation",
                    )
                )
                continue
            if not header and all(
                column >= len(row.cells) or not (row.cells[column].text or "").strip()
                for row in table.rows
            ):
                fields.append(
                    Field(
                        path,
                        "excluded",
                        "Empty presentation column; row outcome attributes remain separately interpreted",
                    )
                )
                continue
            if header in {"count", "counts (y-a-n)", "σ"}:
                fields.append(
                    Field(
                        path,
                        "excluded",
                        "Displayed source aggregate; canonical callback counts and sums derive from retained individual marks",
                    )
                )
                continue
            known = (
                bool(
                    re.search(
                        r"competitor|leader|follower|couple|dancer|bib|judge|place|result|final|marks|total|score|tally|callback|alternate|sum|rank|points",
                        header,
                    )
                )
                or header
                in {
                    "am",
                    "pro",
                    "yes",
                    "no",
                    "y",
                    "n",
                    "a",
                    "b",
                    "c",
                    "d",
                    "e",
                    "f",
                    "g",
                    "promote",
                    "alt",
                }
                or attrs.get("t") in {"2", "6", "9", "12"}
                or bool(attrs.get("title"))
                or bool(
                    kind == "eepro.round"
                    and competitor is not None
                    and bib is not None
                    and competitor < column < bib
                )
            )
            fields.append(
                Field(
                    path,
                    "handled" if known else "unknown",
                    "Explicit result, identity, or judge column"
                    if known
                    else "Unrecognized critical column",
                )
            )
        guards.append(
            Guard(
                "round_identity_missing",
                competitor is not None
                or any(h in {"am", "pro"} for h in headers)
                or (kind == "wdr.rounds" and "solo" in headers),
                "A round must identify its competitor columns",
            )
        )
        guards.append(
            Guard(
                "round_row_shape",
                all(len(row.cells) <= len(table.headers) for row in table.rows),
                "Extra cells cannot be silently discarded; ragged rows remain ragged",
            )
        )
    return count, interpreted


def _wdr_value_known(cell: dict[str, Any], method: Any) -> bool:
    code, value = cell.get("t"), str(cell.get("v", ""))
    if code == 2:
        # S<n> is retained with a finding, never converted into elimination/rank.
        return value in {"Y", "A1", "A2", "A3", "N"} or bool(re.fullmatch(r"S\d+", value))
    if code in {8, 11}:
        return bool(method == "Average Raw Scores")
    if code == 9 and method != "Average Raw Scores":
        return (
            bool(re.fullmatch(r"\d+", value))
            if method == "Placement Order"
            else value
            in {
                "0",
                "0.0",
                "0.00",
                "10",
                "10.0",
                "10.00",
                "4.5",
                "4.50",
                "4.3",
                "4.30",
                "4.2",
                "4.20",
            }
        )
    return True
