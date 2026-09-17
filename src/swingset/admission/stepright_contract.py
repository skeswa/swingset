"""Fail-closed accounting for the retained Step Right document shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from swingset.sources.base import ParseContext, ParseResult
from swingset.sources.steprightsolutions.records import (
    StepRightEventSheet,
    StepRightIndexRow,
    StepRightRoundSheet,
)

from .accounting import account_keys
from .report import Field, Guard

KINDS = frozenset(
    {
        "steprightsolutions.index",
        "steprightsolutions.event",
        "steprightsolutions.round",
    }
)

KNOWN_BOUNDED_WARNINGS = frozenset(
    {
        "steprightsolutions_promotion_unknown",
        "steprightsolutions_bib_ownership_unknown",
        "steprightsolutions_round_listing_absent",
    }
)


@dataclass(frozen=True)
class Accounting:
    fields: tuple[Field, ...]
    guards: tuple[Guard, ...]
    source_count: int | None
    interpreted_count: int
    listed_children: tuple[str, ...] = ()
    interpreted_children: tuple[str, ...] = ()
    terminal: str | None = "captured_document_end"


def accounting(ctx: ParseContext, extract: Any, result: ParseResult) -> Accounting:
    if ctx.kind == "steprightsolutions.index":
        return _index(extract, result)
    if ctx.kind == "steprightsolutions.event":
        return _event(extract, result)
    if ctx.kind == "steprightsolutions.round":
        return _round(ctx, extract, result)
    raise ValueError(f"unsupported Step Right contract kind: {ctx.kind}")


def _index(extract: Any, result: ParseResult) -> Accounting:
    fields: list[Field] = []
    guards: list[Guard] = []
    account_keys(extract, {"rows", "contract_witness"}, "$", fields)
    rows = extract.get("rows") if isinstance(extract, dict) else None
    witness = extract.get("contract_witness") if isinstance(extract, dict) else None
    if not isinstance(rows, list):
        rows = []
    if not isinstance(witness, dict):
        witness = {}
    account_keys(
        witness,
        {"event_block_count", "date_anchor_count", "external_event_sites"},
        "$.contract_witness",
        fields,
    )
    for index, row in enumerate(rows):
        account_keys(row, {"url", "ref", "series", "location", "year"}, f"$.rows[{index}]", fields)
    external = witness.get("external_event_sites")
    if not isinstance(external, list):
        external = []
        fields.append(
            Field(
                "$.contract_witness.external_event_sites",
                "unknown",
                "External index links must be an ordered list",
            )
        )
    for index, item in enumerate(external):
        account_keys(
            item, {"label", "url"}, f"$.contract_witness.external_event_sites[{index}]", fields
        )
        fields.append(
            Field(
                f"$.contract_witness.external_event_sites[{index}]",
                "excluded",
                "External Event Site navigation is outside Step Right event ownership",
            )
        )
    guards.extend(
        (
            Guard(
                "stepright_index_block_count",
                witness.get("event_block_count") == 10,
                "The reviewed index has exactly 10 event blocks",
            ),
            Guard(
                "stepright_index_anchor_count",
                witness.get("date_anchor_count") == 32,
                "The reviewed date areas have exactly 32 anchors",
            ),
            Guard(
                "stepright_index_owned_count",
                len(rows) == 22,
                "Exactly 22 date anchors are Step Right event rows",
            ),
            Guard(
                "stepright_index_external_count",
                len(external) == 10
                and all(
                    isinstance(item, dict) and item.get("label") == "Event Site"
                    for item in external
                ),
                "Exactly 10 anchors are labeled external Event Site links",
            ),
        )
    )
    children = tuple(str(row.get("url", "")) for row in rows if isinstance(row, dict))
    parsed = tuple(
        observation.payload.url
        for observation in result.observations
        if isinstance(observation.payload, StepRightIndexRow)
    )
    return Accounting(tuple(fields), tuple(guards), len(rows), len(parsed), children, parsed)


def _event(extract: Any, result: ParseResult) -> Accounting:
    fields: list[Field] = []
    guards: list[Guard] = []
    account_keys(
        extract,
        {"name", "date", "links", "round_listing_status", "contract_witness"},
        "$",
        fields,
    )
    links = extract.get("links") if isinstance(extract, dict) else None
    witness = extract.get("contract_witness") if isinstance(extract, dict) else None
    status = extract.get("round_listing_status") if isinstance(extract, dict) else None
    if not isinstance(links, list):
        links = []
    if not isinstance(witness, dict):
        witness = {}
    account_keys(
        witness,
        {
            "main_panel_count",
            "contest_count",
            "main_round_link_count",
            "main_unique_round_link_count",
            "sidebar_duplicate_count",
            "sidebar_round_links_match_main",
            "unique_round_links_outside_main",
        },
        "$.contract_witness",
        fields,
    )
    for index, link in enumerate(links):
        account_keys(
            link,
            {"contest", "round", "url", "ref", "round_ref"},
            f"$.links[{index}]",
            fields,
        )
    payload = next(
        (
            observation.payload
            for observation in result.observations
            if isinstance(observation.payload, StepRightEventSheet)
        ),
        None,
    )
    if status == "listed_links":
        outside = witness.get("unique_round_links_outside_main")
        guards.extend(
            (
                Guard(
                    "stepright_event_main_panel",
                    witness.get("main_panel_count") == 1,
                    "The reviewed event has one main results panel",
                ),
                Guard(
                    "stepright_event_contest_count",
                    witness.get("contest_count") == 6,
                    "The main panel has exactly six contest headings",
                ),
                Guard(
                    "stepright_event_round_count",
                    witness.get("main_round_link_count") == len(links) == 12,
                    "The main panel has exactly 12 owned round links",
                ),
                Guard(
                    "stepright_event_round_unique",
                    witness.get("main_unique_round_link_count") == 12
                    and len({str(link.get("url")) for link in links if isinstance(link, dict)})
                    == 12,
                    "All 12 main-panel round links are unique",
                ),
                Guard(
                    "stepright_event_sidebar_duplicates",
                    witness.get("sidebar_duplicate_count") == 12
                    and witness.get("sidebar_round_links_match_main") is True,
                    "The 12 sidebar round links duplicate the main-panel links",
                ),
                Guard(
                    "stepright_event_outside_main",
                    outside == [],
                    "No unique owned round link exists outside the main panel",
                ),
            )
        )
        fields.append(
            Field(
                "sidebar_round_links",
                "excluded",
                "Sidebar round links duplicate the exact main-panel enumeration",
            )
        )
        children = tuple(str(link.get("url", "")) for link in links if isinstance(link, dict))
        interpreted = tuple(link.url for link in payload.round_links) if payload is not None else ()
        return Accounting(
            tuple(fields), tuple(guards), len(links), len(interpreted), children, interpreted
        )
    guards.extend(
        (
            Guard(
                "stepright_event_metadata_status",
                status == "no_round_links",
                "The only reviewed non-enumerating event status is no_round_links",
            ),
            Guard(
                "stepright_event_metadata_shape",
                witness.get("main_panel_count") == 0
                and witness.get("contest_count") == 0
                and witness.get("main_round_link_count") == 0
                and witness.get("main_unique_round_link_count") == 0
                and witness.get("sidebar_duplicate_count") == 0
                and witness.get("sidebar_round_links_match_main") is True
                and witness.get("unique_round_links_outside_main") == [],
                "The reviewed 2013 capture contains metadata and no round enumeration",
            ),
            Guard(
                "stepright_event_metadata_payload",
                payload is not None
                and not payload.round_links
                and payload.round_listing_status == "no_round_links",
                "The metadata-only interpretation must not manufacture a round enumeration",
            ),
        )
    )
    fields.append(
        Field(
            "round_enumeration",
            "excluded",
            "The 2013 metadata-only capture makes no completeness or removal claim",
        )
    )
    return Accounting(tuple(fields), tuple(guards), 1, int(payload is not None))


def _round(ctx: ParseContext, extract: Any, result: ParseResult) -> Accounting:
    fields: list[Field] = []
    guards: list[Guard] = []
    account_keys(extract, {"contest", "round", "tables"}, "$", fields)
    tables = extract.get("tables") if isinstance(extract, dict) else None
    if not isinstance(tables, list):
        tables = []
    payload = next(
        (
            observation.payload
            for observation in result.observations
            if isinstance(observation.payload, StepRightRoundSheet)
        ),
        None,
    )
    path = urlsplit(ctx.url).path.rstrip("/").split("/")
    round_ref = path[-1] if len(path) >= 2 and path[-2] == "round" else ""
    expected_rows = {"507": 25, "508": 8}.get(round_ref)
    is_final = round_ref == "508"
    expected_table_count = 1 if is_final else 2
    expected_headings = [""] if is_final else ["Leaders (10)", "Followers (15)"]
    expected_raw_headers = (
        ["BIB", "Leader", "Follower", "Judge Placements *", "Placement"]
        if is_final
        else ["BIB#", "Name", "Judge Scores *", "Total"]
    )
    expected_width = 9 if is_final else 8
    row_count = 0
    unknown_callback = False
    for table_index, table in enumerate(tables):
        account_keys(
            table,
            {"heading", "rows", "panel", "chief", "judge_notes"},
            f"$.tables[{table_index}]",
            fields,
        )
        rows = table.get("rows") if isinstance(table, dict) else None
        if not isinstance(rows, list):
            rows = []
        row_count += max(0, len(rows) - 1)
        headers = rows[0] if rows and isinstance(rows[0], list) else []
        header_text = [cell.get("text") for cell in headers if isinstance(cell, dict)]
        group_index = 3 if is_final else 2
        group_attrs = (
            headers[group_index].get("attributes", {})
            if len(headers) > group_index and isinstance(headers[group_index], dict)
            else {}
        )
        guards.extend(
            (
                Guard(
                    "stepright_round_heading",
                    table_index < len(expected_headings)
                    and table.get("heading") == expected_headings[table_index],
                    "Role headings establish the reviewed preliminary table ownership",
                ),
                Guard(
                    "stepright_round_group_header",
                    header_text == expected_raw_headers
                    and isinstance(group_attrs, dict)
                    and group_attrs.get("colspan") == "5",
                    "The raw table has the exact five-judge grouped header",
                ),
                Guard(
                    "stepright_round_row_width",
                    all(isinstance(row, list) and len(row) == expected_width for row in rows[1:]),
                    "Every raw BIB row has the reviewed expanded width",
                ),
            )
        )
        if is_final:
            guards.append(
                Guard(
                    "stepright_round_final_legend",
                    "title" not in group_attrs,
                    "Finals placements do not claim the preliminary callback legend",
                )
            )
            placements = [
                row[-1].get("text")
                for row in rows[1:]
                if isinstance(row, list) and row and isinstance(row[-1], dict)
            ]
            guards.append(
                Guard(
                    "stepright_round_final_placement",
                    placements == ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"],
                    "Final placement rows are complete and ordered first through eighth",
                )
            )
            for row_index, row in enumerate(rows[1:]):
                for column in range(3, 8):
                    value = _cell_text(row, column)
                    known = bool(re.fullmatch(r"[1-8]", value))
                    fields.append(
                        Field(
                            f"$.tables[{table_index}].rows[{row_index}].judge[{column - 3}]={value}",
                            "handled" if known else "unknown",
                            "Printed final judge placement"
                            if known
                            else "Unknown final judge placement value",
                        )
                    )
        else:
            guards.append(
                Guard(
                    "stepright_round_callback_legend",
                    isinstance(group_attrs, dict)
                    and re.sub(r"\s+", "", str(group_attrs.get("title", ""))).lower()
                    == "1=yes,2=alt,3=no",
                    "The grouped header declares the exact 1/2/3 callback legend",
                )
            )
            for row_index, row in enumerate(rows[1:]):
                for column in range(2, 7):
                    value = _cell_text(row, column)
                    known = value in {"1", "2", "3"}
                    unknown_callback |= not known
                    fields.append(
                        Field(
                            f"$.tables[{table_index}].rows[{row_index}].callback[{column - 2}]={value}",
                            "handled" if known else "unknown",
                            "Callback value declared by the grouped legend"
                            if known
                            else "Unknown callback sub-value",
                        )
                    )
        fields.extend(
            (
                Field(
                    f"$.tables[{table_index}].panel",
                    "excluded",
                    "Printed roster is retained but does not own anonymous judge columns",
                ),
                Field(
                    f"$.tables[{table_index}].chief",
                    "excluded",
                    "Chief-judge metadata does not attribute the anonymous marks",
                ),
                Field(
                    f"$.tables[{table_index}].judge_notes",
                    "excluded",
                    "Presentation note is retained without adding score semantics",
                ),
                Field(
                    f"$.tables[{table_index}].row_class",
                    "excluded",
                    "Highlight classes do not establish promotion or removal",
                ),
            )
        )
    interpreted_count = (
        sum(len(table.table.rows) for table in payload.tables) if payload is not None else 0
    )
    guards.extend(
        (
            Guard(
                "stepright_round_identity",
                expected_rows is not None
                and payload is not None
                and payload.source_event_ref == "steprightsolutions:asianopen2013"
                and payload.source_round_ref == round_ref,
                "Only retained rounds 507 and 508 have reviewed event ownership",
            ),
            Guard(
                "stepright_round_table_count",
                len(tables) == expected_table_count,
                "The retained round has the exact reviewed table count",
            ),
            Guard(
                "stepright_round_bib_count",
                expected_rows is not None and row_count == expected_rows,
                "Raw BIB rows match the exact retained round count",
            ),
            Guard(
                "stepright_round_callback_values",
                not unknown_callback,
                "Every preliminary callback sub-value is declared by the 1/2/3 legend",
            ),
        )
    )
    fields.append(
        Field(
            "bib_ownership" if is_final else "promotion",
            "excluded",
            "A finals BIB is not assigned to either partner without cross-page evidence"
            if is_final
            else "Highlighted preliminary rows do not independently prove advancement",
        )
    )
    return Accounting(tuple(fields), tuple(guards), row_count, interpreted_count)


def _cell_text(row: Any, column: int) -> str:
    if not isinstance(row, list) or column >= len(row) or not isinstance(row[column], dict):
        return ""
    return str(row[column].get("text", ""))
