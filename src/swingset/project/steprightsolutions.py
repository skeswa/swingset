"""Conservative normalization of Step Right source records for projection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from swingset.sources.records import Cell, ResultTable, RoundSheet, SourceEventRow
from swingset.sources.steprightsolutions.records import (
    StepRightEventSheet,
    StepRightIndexRow,
    StepRightRoundSheet,
)


@dataclass(frozen=True, slots=True)
class StepRightSourceEvent:
    """Source-event fields supported by one Step Right observation."""

    row: SourceEventRow
    location_raw: str | None
    year: int | None


def source_event(payload: object, observed_url: str) -> StepRightSourceEvent | None:
    """Translate metadata without turning a printed year into a calendar day."""
    if isinstance(payload, StepRightIndexRow):
        raw_year = payload.year_raw.strip()
        year = int(raw_year) if re.fullmatch(r"\d{4}", raw_year) else None
        return StepRightSourceEvent(
            SourceEventRow(
                "source_event_row",
                payload.source_event_ref,
                payload.series_name_raw,
                raw_year,
                payload.url,
            ),
            payload.location_raw or None,
            year,
        )
    if isinstance(payload, StepRightEventSheet):
        return StepRightSourceEvent(
            SourceEventRow(
                "source_event_row",
                payload.source_event_ref,
                payload.name_raw,
                payload.date_raw,
                observed_url,
            ),
            None,
            None,
        )
    return None


def round_sheet(payload: object) -> RoundSheet | None:
    """Expose reviewed Step Right table structure to the common reconciler."""
    if not isinstance(payload, StepRightRoundSheet):
        return None
    round_name = payload.round_name_raw.casefold()
    final = "final" in round_name and not any(token in round_name for token in ("semi", "quarter"))
    tables: list[ResultTable] = []
    for source_table in payload.tables:
        headers = list(source_table.table.headers)
        for position, (column, token) in enumerate(
            zip(
                source_table.anonymous_judge_columns,
                source_table.anonymous_judge_ids,
                strict=False,
            ),
            1,
        ):
            if column < len(headers):
                headers[column] = Cell(
                    f"J{position}",
                    (("source-anonymous-id", token),),
                )
        if not final and source_table.bib_ownership in {"leader", "follower"}:
            name_column = next(
                (
                    index
                    for index, cell in enumerate(headers)
                    if (cell.text or "").strip().casefold() == "name"
                ),
                None,
            )
            if name_column is not None:
                headers[name_column] = Cell(
                    source_table.bib_ownership.title(), headers[name_column].attributes
                )
        tables.append(
            ResultTable(
                source_table.table.heading_raw,
                tuple(headers),
                source_table.table.rows,
            )
        )
    return RoundSheet(
        "round_sheet",
        payload.source_event_ref,
        payload.source_round_ref,
        payload.contest_name_raw,
        payload.round_name_raw,
        tuple(tables),
        scoring_method_raw=payload.callback_legend,
    )
