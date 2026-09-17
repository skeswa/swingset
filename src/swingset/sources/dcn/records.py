"""DCN source observations awaiting canonical projection and kind admission."""

from dataclasses import dataclass

from swingset.model.observations import register_observation_type


@dataclass(frozen=True)
class DcnIndexEvent:
    source_event_ref: str
    name_raw: str
    start_date_raw: str
    end_date_raw: str
    location_raw: str
    venue_name_raw: str
    affiliations: tuple[str, ...]
    results: bool
    published: bool
    online: bool
    schedule: bool
    frequent: bool
    event_url: str
    banner_url_raw: str | None
    square_image_raw: str | None


@register_observation_type
@dataclass(frozen=True)
class DcnIndexSheet:
    kind: str
    events: tuple[DcnIndexEvent, ...]
    available_years: tuple[int, ...]
    load_year_url: str
    enumeration_status: str = "listed_only"


@register_observation_type
@dataclass(frozen=True)
class DcnEventMetadata:
    kind: str
    source_event_ref: str
    name_raw: str
    start_date_raw: str
    end_date_raw: str
    location_raw: str
    results_url: str | None
    results_availability: str = "unknown"


@dataclass(frozen=True)
class DcnContestLocator:
    contest_id: str
    name_raw: str
    url: str


@dataclass(frozen=True)
class DcnResultRow:
    bib_raw: str
    names_raw: str
    member_names_raw: tuple[str, ...]
    placement_raw: str
    placement_low: int
    placement_high: int
    bib_ownership: str = "row"
    promotion: str = "unknown"


@dataclass(frozen=True)
class DcnResultTable:
    label_raw: str
    role: str | None
    rows: tuple[DcnResultRow, ...]
    score_pdf_url: str
    population_completeness: str = "unknown"


@register_observation_type
@dataclass(frozen=True)
class DcnLegacyResults:
    kind: str
    source_event_ref: str
    event_name_raw: str
    contest_id: str
    contest_name_raw: str
    contest_locators: tuple[DcnContestLocator, ...]
    tables: tuple[DcnResultTable, ...]
    event_results_completeness: str = "unknown"
    score_pdf_interpretation: str = "unassessed"


@dataclass(frozen=True)
class DcnScorePdfJudge:
    code_raw: str
    name_raw: str


@dataclass(frozen=True)
class DcnScorePdfValue:
    column_raw: str
    value_raw: str
    mark_kind: str | None = None
    alternate_rank_raw: str | None = None


@dataclass(frozen=True)
class DcnScorePdfRow:
    bib_raw: str
    member_names_raw: tuple[str, ...]
    role: str | None
    judge_values: tuple[DcnScorePdfValue, ...]
    numbered_values: tuple[DcnScorePdfValue, ...]
    result_raw: str
    remarks_raw: str
    line_raw: str
    continuation_line_raw: str | None
    bib_ownership: str = "row"
    promotion: str = "unknown"


@register_observation_type
@dataclass(frozen=True)
class DcnScorePdfPage:
    kind: str
    source_body_sha256: str
    source_url: str
    source_event_ref: str
    source_round_id: str
    page_number: int
    heading_raw: str
    event_name_raw: str
    role: str | None
    legend_raw: str
    judges: tuple[DcnScorePdfJudge, ...]
    column_headers_raw: tuple[str, ...]
    rows: tuple[DcnScorePdfRow, ...]
    remarks_header_raw: str | None
    disclaimer_raw: str | None
    page_text_raw: str
    population_completeness: str = "unknown"
