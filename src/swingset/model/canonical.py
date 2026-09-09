"""Frozen canonical records on the projection side of the boundary."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    source: str
    snapshot_id: str
    parser_version: str
    first_seen_at: str
    last_seen_at: str
    run_id: str

    def key(self) -> tuple[object, ...]:
        raise NotImplementedError

    def values(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, slots=True, kw_only=True)
class Event(Provenance):
    event_id: str
    series_id: str
    name: str
    year: int
    start_date: str
    end_date: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    website: str | None = None
    wsdc_status: str = "unknown"
    sources: tuple[str, ...] = ()
    live_window_start: str | None = None
    live_window_end: str | None = None

    def key(self) -> tuple[object, ...]:
        return (self.event_id,)


@dataclass(frozen=True, slots=True, kw_only=True)
class Contest(Provenance):
    contest_id: str
    event_id: str
    name_raw: str
    division: str
    age_division: str
    contest_type: str
    partner_mode: str
    dance_style: str
    wsdc_points_eligible: bool
    combined_from: tuple[str, ...]
    parse_status: str
    source_contest_ref: str

    def key(self) -> tuple[object, ...]:
        return (self.contest_id,)


@dataclass(frozen=True, slots=True, kw_only=True)
class Round(Provenance):
    round_id: str
    contest_id: str
    round_type: str
    round_index: int
    name_raw: str
    scoring_method: str
    callback_legend: str
    judge_count: int
    chief_judge_id: str | None
    entry_count: int
    promoted_count: int | None
    source_round_ref: str
    score_sheet_url: str | None

    def key(self) -> tuple[object, ...]:
        return (self.round_id,)


@dataclass(frozen=True, slots=True, kw_only=True)
class Entry(Provenance):
    entry_id: str
    contest_id: str
    event_id: str
    role: str
    bib: str | None
    name_raw: str | None
    name_norm: str | None
    partner_name_raw: str | None = None
    city_raw: str | None = None
    country_raw: str | None = None
    wsdc_id: int | None = None
    link_status: str = "unmatched"
    link_confidence: float = 0.0
    partner_entry_id: str | None = None
    rounds_danced: tuple[str, ...] = ()
    best_round: str | None = None

    def key(self) -> tuple[object, ...]:
        return (self.entry_id,)


@dataclass(frozen=True, slots=True, kw_only=True)
class Heat(Provenance):
    heat_id: str
    round_id: str
    heat_number: int
    entry_id: str
    position: int | None

    def key(self) -> tuple[object, ...]:
        return (self.heat_id, self.entry_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class Judge(Provenance):
    judge_id: str
    event_id: str
    name_raw: str | None
    initials: str | None
    anonymous: bool
    wsdc_id: int | None = None

    def key(self) -> tuple[object, ...]:
        return (self.judge_id,)


@dataclass(frozen=True, slots=True, kw_only=True)
class CallbackMark(Provenance):
    round_id: str
    entry_id: str
    judge_id: str
    mark: str
    mark_raw: str
    mark_value: float

    def key(self) -> tuple[object, ...]:
        return (self.round_id, self.entry_id, self.judge_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class Callback(Provenance):
    round_id: str
    entry_id: str
    score_sum: float
    yes_count: int
    alt_count: int
    no_count: int
    outcome: str
    tie_break_applied: bool | None
    heat_number: int | None

    def key(self) -> tuple[object, ...]:
        return (self.round_id, self.entry_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class FinalMark(Provenance):
    round_id: str
    placement_id: str
    judge_id: str
    rank: int

    def key(self) -> tuple[object, ...]:
        return (self.round_id, self.placement_id, self.judge_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class Placement(Provenance):
    placement_id: str
    round_id: str
    contest_id: str
    event_id: str
    place: int
    leader_entry_id: str | None
    follower_entry_id: str | None
    couple_entry_id: str | None
    leader_wsdc_id: int | None = None
    follower_wsdc_id: int | None = None
    marks_sorted: str | None = None
    tally: tuple[int, ...] = ()
    registry_points_leader: int | None = None
    registry_points_follower: int | None = None
    registry_confirmed: bool = False
    points_matches_expected: bool | None = None

    def key(self) -> tuple[object, ...]:
        return (self.placement_id,)


@dataclass(frozen=True, slots=True, kw_only=True)
class Dancer(Provenance):
    wsdc_id: int
    first_name: str
    last_name: str
    name_norm: str
    is_pro: bool
    primary_role: str
    leader_required_level: str
    leader_allowed_level: str
    follower_required_level: str
    follower_allowed_level: str
    leader_highest_level: str
    leader_highest_points: int
    follower_highest_level: str
    follower_highest_points: int
    recent_year: int
    registry_internal_id: int
    registry_fetched_at: str
    merged_into_wsdc_id: int | None = None

    def key(self) -> tuple[object, ...]:
        return (self.wsdc_id,)


@dataclass(frozen=True, slots=True, kw_only=True)
class RegistryPlacement(Provenance):
    wsdc_id: int
    role: str
    dance_style: str
    division: str
    series_id: str
    series_name_raw: str
    event_month: str
    event_id: str | None
    result: str
    points: int

    def key(self) -> tuple[object, ...]:
        return (
            self.wsdc_id,
            self.role,
            self.series_id,
            self.event_month,
            self.division,
            self.dance_style,
        )


CanonicalRecord = (
    Event
    | Contest
    | Round
    | Entry
    | Heat
    | Judge
    | CallbackMark
    | Callback
    | FinalMark
    | Placement
    | Dancer
    | RegistryPlacement
)
