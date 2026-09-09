"""Frozen source-vocabulary observation payloads."""

from dataclasses import dataclass

from swingset.model.observations import register_observation_type


@dataclass(frozen=True)
class CalendarRow:
    kind: str
    name_raw: str
    start_date_raw: str
    end_date_raw: str
    event_type_raw: str
    location_raw: str
    website: str | None
    country_code_raw: str | None
    row_classes: tuple[str, ...]


@dataclass(frozen=True)
class SourceEventRow:
    kind: str
    source_ref: str
    name_raw: str | None
    date_raw: str | None
    url: str


@dataclass(frozen=True)
class FileRow:
    kind: str
    name: str
    url: str
    modified_raw: str | None
    size_raw: str | None


@dataclass(frozen=True)
class RegistryPlacement:
    role_raw: str
    division_raw: str
    event_id_raw: str | None
    event_name_raw: str
    event_month_raw: str
    result_raw: str
    points: int
    dance_style_raw: str | None = None


@dataclass(frozen=True)
class DancerLookup:
    kind: str
    outcome: str
    requested_wsdc_id: int
    wsdc_id: int | None = None
    registry_internal_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    primary_role_raw: str | None = None
    is_pro: bool = False
    leader_required_raw: str | None = None
    leader_allowed_raw: str | None = None
    follower_required_raw: str | None = None
    follower_allowed_raw: str | None = None
    recent_year: int | None = None
    placements: tuple[RegistryPlacement, ...] = ()


@dataclass(frozen=True)
class Cell:
    text: str | None
    attributes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ResultRow:
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class ResultTable:
    heading_raw: str
    headers: tuple[Cell, ...]
    rows: tuple[ResultRow, ...]
    redacted: bool = False
    attribute_group: str | None = None


@dataclass(frozen=True)
class RoundSheet:
    kind: str
    source_event_ref: str
    source_round_ref: str
    contest_name_raw: str
    round_name_raw: str
    tables: tuple[ResultTable, ...]
    event_name_raw: str | None = None


@dataclass(frozen=True)
class EventSheet:
    kind: str
    source_event_ref: str
    name_raw: str | None
    round_links: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AwardRow:
    leader_name_raw: str
    follower_name_raw: str
    place_raw: str


@dataclass(frozen=True)
class AwardSheet:
    kind: str
    source_event_ref: str
    round_name_raw: str
    rows: tuple[AwardRow, ...]


# The storage codec owns the registry; source modules own these payload shapes.
register_observation_type(CalendarRow)
register_observation_type(SourceEventRow)
register_observation_type(FileRow)
register_observation_type(DancerLookup)
register_observation_type(RoundSheet)
register_observation_type(EventSheet)
register_observation_type(AwardSheet)
