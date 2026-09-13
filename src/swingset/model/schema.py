"""Canonical SQLite table metadata used by projection and build."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from swingset.build.schema import PRIMARY_KEYS as ARROW_PRIMARY_KEYS
    from swingset.build.schema import SCHEMAS as ARROW_SCHEMAS

__all__ = ["ARROW_PRIMARY_KEYS", "ARROW_SCHEMAS", "RECORD_TABLE", "TABLES", "TableSchema"]


__all__ = ["ARROW_PRIMARY_KEYS", "ARROW_SCHEMAS", "RECORD_TABLE", "TABLES", "TableSchema"]


@dataclass(frozen=True, slots=True)
class TableSchema:
    name: str
    primary_key: tuple[str, ...]
    owner_columns: tuple[str, ...]
    scope_column: str | None


# These columns describe a pinned public release, not mutable canonical facts.
PUBLIC_SCOPE_TABLES = frozenset(
    {
        "events",
        "contests",
        "rounds",
        "entries",
        "judges",
        "placements",
        "dancers",
        "registry_placements",
    }
)
PUBLIC_SCOPE_COLUMNS = frozenset({"scope_status", "evidence_observed_at"})
COVERAGE_PUBLIC_COLUMNS = frozenset(
    {
        "scope_kind",
        "scope_id",
        "scope_status",
        "scope_reasons",
        "missing_scopes",
        "discovered_units",
        "acquired_units",
        "interpreted_units",
        "mapped_units",
        "withheld_units",
        "unavailable_units",
        "unassessed_units",
        "resolved_identities",
        "identity_subjects",
        "withheld_identities",
        "withheld_scopes",
        "unavailable_scopes",
        "discovery_denominator",
        "discovery_universe",
        "acquisition_denominator",
        "interpretation_denominator",
        "mapping_denominator",
        "evidence_cutoff",
        "evidence_observed_at",
        "usable_verified_at",
        "health_as_of",
        "method",
        "population",
        "uncertainty",
    }
)


TABLES: dict[str, TableSchema] = {
    "events": TableSchema("events", ("event_id",), (), "event_id"),
    "contests": TableSchema("contests", ("contest_id",), (), "event_id"),
    "rounds": TableSchema("rounds", ("round_id",), (), "contest_id"),
    "entries": TableSchema(
        "entries", ("entry_id",), ("wsdc_id", "link_status", "link_confidence"), "event_id"
    ),
    "heats": TableSchema("heats", ("heat_id", "entry_id"), (), "round_id"),
    "judges": TableSchema("judges", ("judge_id",), ("wsdc_id",), "event_id"),
    "callback_marks": TableSchema(
        "callback_marks", ("round_id", "entry_id", "judge_id"), (), "round_id"
    ),
    "callbacks": TableSchema("callbacks", ("round_id", "entry_id"), (), "round_id"),
    "final_marks": TableSchema(
        "final_marks", ("round_id", "placement_id", "judge_id"), (), "round_id"
    ),
    "placements": TableSchema(
        "placements",
        ("placement_id",),
        (
            "leader_wsdc_id",
            "follower_wsdc_id",
            "registry_points_leader",
            "registry_points_follower",
            "registry_confirmed",
            "points_matches_expected",
        ),
        "event_id",
    ),
    "dancers": TableSchema("dancers", ("wsdc_id",), (), None),
    "registry_placements": TableSchema(
        "registry_placements",
        ("wsdc_id", "role", "series_id", "event_month", "division", "dance_style"),
        ("event_id",),
        "event_id",
    ),
}

RECORD_TABLE: dict[str, str] = {name.rstrip("s"): name for name in TABLES}
RECORD_TABLE.update(
    {
        "entry": "entries",
        "callbackmark": "callback_marks",
        "finalmark": "final_marks",
        "registryplacement": "registry_placements",
    }
)


def __getattr__(name: str) -> Any:
    """Load Arrow's optional schema catalog without importing build eagerly."""
    if name in {"ARROW_SCHEMAS", "ARROW_PRIMARY_KEYS"}:
        from swingset.build import schema as arrow

        return arrow.SCHEMAS if name == "ARROW_SCHEMAS" else arrow.PRIMARY_KEYS
    raise AttributeError(name)
