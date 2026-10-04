"""Round sheet accounting with source-specific columns and ragged judge panels."""

import re
from typing import Any

from swingset.sources.base import ParseResult
from swingset.sources.records import ResultRow, ResultTable, RoundSheet

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
            # Contract 5 only rescues a scoring.dance column contract 4 left
            # unknown, so an accepted round's accounting stays byte-identical.
            if not known and kind == "scoringdance.round":
                rescued = _scoringdance_column(index, table, headers, column, guards)
                if rescued is not None:
                    fields.extend(rescued)
                    continue
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


# scoring.dance contract 5 (D-0240): columns contract 4 left unknown.
_ADJUSTMENT = re.compile(r"[+-]\d+(?:\.\d+)?")
_JUDGE_CODE = re.compile(r"[a-z]{1,3}")
_OUTCOME_NOTE = re.compile(r"\bdq\b|diss|disq|scrat|injur|withdr|fail|^sw$")
#: Known remark values: (class, pattern on the casefolded text, placed rows only).
_REMARKS = (
    ("chief_judge_adjustment", re.compile(r"^cja$"), False),
    ("tie_break_note", re.compile(r"^(?:based on hj score|cj points)$"), False),
    ("tie_break_score", re.compile(r"^\d+(?:\.\d+)?$"), False),
    ("video_review_pass", re.compile(r"^vr ?pass$"), False),
    ("violation_badge", re.compile(r"^(?:v\d|vp)$"), True),
    ("review_note", re.compile(r"^(?:rv|vr)\b|violation|\blifts?\b|review"), True),
)
_REMARK_REASONS = {
    "chief_judge_adjustment": "CJA badge beside a chief-judge adjustment; the outcome comes from the row data-state",
    "tie_break_note": "Tie-break note; the outcome comes from the row data-state",
    "tie_break_score": "Tie-break score; the outcome comes from the row data-state and sums derive from individual marks",
    "video_review_pass": "Video review passed; the displayed result stands",
    "violation_badge": "Violation badge on a placed row; the displayed placement already includes the drop",
    "review_note": "Review or violation note on a placed row; the displayed placement is the source result",
    "eliminated_outcome_note": "Disqualification, scratch or injury note on a row the source already shows as eliminated",
}


def _scoringdance_column(
    index: int, table: ResultTable, headers: list[str], column: int, guards: list[Guard]
) -> list[Field] | None:
    """Account for a reviewed scoring.dance column, or return None to leave it unknown."""
    header = headers[column]
    attrs = dict(table.headers[column].attributes)
    path = f"table[{index}].header[{column}]={header}"
    values = [
        (number, row, (row.cells[column].text or "").strip())
        for number, row in enumerate(table.rows, 1)
        if column < len(row.cells)
    ]
    if (
        not header
        and not attrs
        and column
        and headers[column - 1] == "σ"
        and all(_ADJUSTMENT.fullmatch(value) for _, _, value in values if value)
    ):
        return [
            Field(
                path,
                "excluded",
                "Chief-judge adjustment to the displayed Σ; outcome from row data-state; sums derive from individual marks",
            )
        ]
    if (
        not header
        and "title" not in attrs
        and attrs.get("class") == "text-center;"
        and column == len(headers) - 1
    ):
        return _scoringdance_remarks(index, table, headers, path, values, guards)
    follower = next((i for i, h in enumerate(headers) if h == "follower"), None)
    placement = next((i for i, h in enumerate(headers) if h == "placement"), None)
    if (
        follower is not None
        and placement is not None
        and follower < column < placement
        and _JUDGE_CODE.fullmatch(header)
        and "title" not in attrs
        and all(re.fullmatch(r"\d*", value) for _, _, value in values)
    ):
        return [
            Field(
                path,
                "excluded",
                "Anonymized per-page judge code; no judge identity; placement handled",
            )
        ]
    return None


def _scoringdance_remarks(
    index: int,
    table: ResultTable,
    headers: list[str],
    path: str,
    values: list[tuple[int, ResultRow, str]],
    guards: list[Guard],
) -> list[Field]:
    """Each remark value is excluded by class, blocks as a conflict, or stays unknown."""
    placement = next((i for i, h in enumerate(headers) if h == "placement"), None)
    bib = next((i for i, h in enumerate(headers) if "bib" in h), None)
    fields = [Field(path, "excluded", "Source remark column; each value is accounted as a remark")]
    conflicts: list[str] = []
    for number, row, value in values:
        if not value:
            continue
        state = next(
            (v for cell in row.cells for k, v in cell.attributes if k == "row-data-state"), ""
        )
        place = (
            (row.cells[placement].text or "").strip()
            if placement is not None and placement < len(row.cells)
            else ""
        )
        text = " ".join(value.split()).casefold()
        if _OUTCOME_NOTE.search(text):
            if state or place:
                bib_text = (
                    (row.cells[bib].text or "").strip()
                    if bib is not None and bib < len(row.cells)
                    else ""
                )
                conflicts.append(
                    f"table[{index}] row {number} (bib {bib_text or '?'}, {state or place}): {value}"
                )
            else:
                fields.append(
                    Field(
                        f"table[{index}].remark.eliminated_outcome_note",
                        "excluded",
                        _REMARK_REASONS["eliminated_outcome_note"],
                    )
                )
            continue
        known = next(
            (
                name
                for name, pattern, placed_only in _REMARKS
                if pattern.search(text) and (place or not placed_only)
            ),
            None,
        )
        fields.append(
            Field(f"table[{index}].remark.{known}", "excluded", _REMARK_REASONS[known])
            if known is not None
            else Field(f"table[{index}].remark={value}", "unknown", "Unrecognized source remark")
        )
    guards.append(
        Guard(
            "source_remark_outcome_conflict",
            not conflicts,
            "A disqualification, scratch, injury, failed review or SW remark cannot sit on a row "
            "the source shows as called back, alternate or placed"
            + ("; " + "; ".join(conflicts) if conflicts else ""),
        )
    )
    return fields


def _wdr_value_known(cell: dict[str, Any], method: Any) -> bool:
    code, value = cell.get("t"), str(cell.get("v", ""))
    if code == 2:
        # S<n> is retained with a finding, never converted into elimination/rank.
        return value in {"Y", "A1", "A2", "A3", "N"} or bool(re.fullmatch(r"S\d+", value))
    if code in {8, 11}:
        return bool(method == "Average Raw Scores")
    if code == 9 and method != "Average Raw Scores":
        return (
            # `|SVW` is the legend's "Swing Violation - Warning": no placement
            # change. SV1-SV3 move placements and stay unknown (D-0240).
            bool(re.fullmatch(r"\d+(?:\|SVW)?", value))
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
