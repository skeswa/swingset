"""Archived source vocabulary, deliberately awaiting canonical admission."""

from dataclasses import dataclass

from swingset.model.observations import register_observation_type
from swingset.sources.records import ResultTable


@register_observation_type
@dataclass(frozen=True)
class StepRightIndexRow:
    kind: str
    source_event_ref: str
    series_name_raw: str
    location_raw: str
    year_raw: str
    url: str


@dataclass(frozen=True)
class StepRightRoundLink:
    contest_name_raw: str
    round_name_raw: str
    source_round_ref: str
    url: str


@register_observation_type
@dataclass(frozen=True)
class StepRightEventSheet:
    kind: str
    source_event_ref: str
    name_raw: str
    date_raw: str | None
    round_links: tuple[StepRightRoundLink, ...]
    round_listing_status: str = "listed_links"


@dataclass(frozen=True)
class StepRightTable:
    table: ResultTable
    anonymous_judge_columns: tuple[int, ...]
    anonymous_judge_ids: tuple[str, ...]
    named_panel_raw: tuple[str, ...]
    chief_judge_raw: str | None
    marks_attributed: bool = False
    promotion: str = "unknown"
    bib_ownership: str = "unknown"
    judge_notes_raw: tuple[str, ...] = ()


@register_observation_type
@dataclass(frozen=True)
class StepRightRoundSheet:
    kind: str
    source_event_ref: str
    source_round_ref: str
    contest_name_raw: str
    round_name_raw: str
    tables: tuple[StepRightTable, ...]
    callback_legend: str


def callback_mark(raw: str) -> str | None:
    """Only verified legend values; an alternate has no invented rank."""
    return {"1": "yes", "2": "alt", "3": "no"}.get(raw.strip())
