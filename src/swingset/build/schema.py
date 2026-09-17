"""Public Arrow schemas and deterministic sort keys."""

from __future__ import annotations

import pyarrow as pa

from swingset.model.schema import COVERAGE_PUBLIC_COLUMNS, PUBLIC_SCOPE_COLUMNS, PUBLIC_SCOPE_TABLES

from .event_coverage import PUBLIC_FIELDS as EVENT_COVERAGE_FIELDS

S = pa.string()
I8, I16, I32 = pa.int8(), pa.int16(), pa.int32()
F32 = pa.float32()
TS = pa.timestamp("us", tz="UTC")
DATE = pa.date32()
LS = pa.list_(S)
LI8 = pa.list_(I8)
PROVENANCE = [
    ("source", S),
    ("snapshot_id", S),
    ("parser_version", S),
    ("first_seen_at", TS),
    ("last_seen_at", TS),
    ("run_id", S),
]


def schema(fields: list[tuple[str, pa.DataType]], *, provenance: bool = True) -> pa.Schema:
    return pa.schema(fields + (PROVENANCE if provenance else []))


SCHEMAS: dict[str, pa.Schema] = {
    "events": schema(
        [
            ("event_id", S),
            ("series_id", S),
            ("name", S),
            ("year", I16),
            ("start_date", DATE),
            ("end_date", DATE),
            ("event_month", S),
            ("date_precision", S),
            ("held", S),
            ("coverage_tier", S),
            ("history_source", LS),
            ("city", S),
            ("region", S),
            ("country", S),
            ("website", S),
            ("wsdc_status", S),
            ("sources", LS),
            ("live_window_start", TS),
            ("live_window_end", TS),
        ]
    ),
    "contests": schema(
        [
            ("contest_id", S),
            ("event_id", S),
            ("name_raw", S),
            ("division", S),
            ("age_division", S),
            ("contest_type", S),
            ("partner_mode", S),
            ("dance_style", S),
            ("wsdc_points_eligible", pa.bool_()),
            ("combined_from", LS),
            ("parse_status", S),
            ("source_contest_ref", S),
        ]
    ),
    "rounds": schema(
        [
            ("round_id", S),
            ("contest_id", S),
            ("round_type", S),
            ("round_index", I8),
            ("name_raw", S),
            ("scoring_method", S),
            ("callback_legend", S),
            ("judge_count", I8),
            ("chief_judge_id", S),
            ("entry_count", I32),
            ("promoted_count", I32),
            ("source_round_ref", S),
            ("score_sheet_url", S),
        ]
    ),
    "entries": schema(
        [
            ("entry_id", S),
            ("contest_id", S),
            ("event_id", S),
            ("role", S),
            ("bib", S),
            ("name_raw", S),
            ("name_norm", S),
            ("partner_name_raw", S),
            ("city_raw", S),
            ("country_raw", S),
            ("wsdc_id", I32),
            ("link_status", S),
            ("link_confidence", F32),
            ("partner_entry_id", S),
            ("rounds_danced", LS),
            ("best_round", S),
        ]
    ),
    "heats": schema(
        [("heat_id", S), ("round_id", S), ("heat_number", I16), ("entry_id", S), ("position", I16)]
    ),
    "judges": schema(
        [
            ("judge_id", S),
            ("event_id", S),
            ("name_raw", S),
            ("initials", S),
            ("anonymous", pa.bool_()),
            ("wsdc_id", I32),
        ]
    ),
    "callback_marks": schema(
        [
            ("round_id", S),
            ("entry_id", S),
            ("judge_id", S),
            ("mark", S),
            ("mark_raw", S),
            ("mark_value", F32),
        ]
    ),
    "callbacks": schema(
        [
            ("round_id", S),
            ("entry_id", S),
            ("score_sum", F32),
            ("yes_count", I8),
            ("alt_count", I8),
            ("no_count", I8),
            ("outcome", S),
            ("tie_break_applied", pa.bool_()),
            ("heat_number", I16),
        ]
    ),
    "final_marks": schema([("round_id", S), ("placement_id", S), ("judge_id", S), ("rank", I8)]),
    "placements": schema(
        [
            ("placement_id", S),
            ("round_id", S),
            ("contest_id", S),
            ("event_id", S),
            ("place", I8),
            ("leader_entry_id", S),
            ("follower_entry_id", S),
            ("couple_entry_id", S),
            ("leader_wsdc_id", I32),
            ("follower_wsdc_id", I32),
            ("marks_sorted", S),
            ("tally", LI8),
            ("registry_points_leader", I16),
            ("registry_points_follower", I16),
            ("registry_confirmed", pa.bool_()),
            ("points_matches_expected", pa.bool_()),
        ]
    ),
    "dancers": schema(
        [
            ("wsdc_id", I32),
            ("first_name", S),
            ("last_name", S),
            ("name_norm", S),
            ("is_pro", pa.bool_()),
            ("primary_role", S),
            ("leader_required_level", S),
            ("leader_allowed_level", S),
            ("follower_required_level", S),
            ("follower_allowed_level", S),
            ("leader_highest_level", S),
            ("leader_highest_points", I16),
            ("follower_highest_level", S),
            ("follower_highest_points", I16),
            ("recent_year", I16),
            ("registry_internal_id", I32),
            ("registry_fetched_at", TS),
            ("merged_into_wsdc_id", I32),
        ]
    ),
    "registry_placements": schema(
        [
            ("wsdc_id", I32),
            ("role", S),
            ("dance_style", S),
            ("division", S),
            ("series_id", S),
            ("series_name_raw", S),
            ("event_month", DATE),
            ("event_id", S),
            ("result", S),
            ("points", I16),
        ]
    ),
    "identity_links": schema(
        [
            ("link_id", S),
            ("subject_kind", S),
            ("subject_id", S),
            ("wsdc_id", I32),
            ("method", S),
            ("status", S),
            ("confidence", F32),
            ("constraints_applied", LS),
            ("source_ref_ids", LS),
            ("decision_ids", LS),
            ("acceptance_policy", S),
            ("acceptance_state", S),
            ("journal_digest", S),
            ("journal_generation", pa.int64()),
            ("asserted_at", TS),
            ("run_id", S),
        ],
        provenance=False,
    ),
    "link_candidates": schema(
        [
            ("subject_kind", S),
            ("subject_id", S),
            ("wsdc_id", I32),
            ("score", F32),
            ("name_similarity", F32),
            ("name_rarity", F32),
            ("division_ok", pa.bool_()),
            ("role_ok", pa.bool_()),
            ("recency_ok", pa.bool_()),
            ("geography", F32),
            ("bib_reuse", pa.bool_()),
            ("registry_confirms", pa.bool_()),
            ("source_id_confirms", pa.bool_()),
            ("rank", I16),
            ("chosen", pa.bool_()),
            ("run_id", S),
        ],
        provenance=False,
    ),
    "review_queue": schema(
        [
            ("item_id", S),
            ("kind", S),
            ("subject_id", S),
            ("summary", S),
            ("suggested_override", S),
            ("opened_at", TS),
            ("run_id", S),
        ],
        provenance=False,
    ),
    "changelog": schema(
        [
            ("changed_at", TS),
            ("run_id", S),
            ("table", S),
            ("record_key", S),
            ("field", S),
            ("old_value", S),
            ("new_value", S),
            ("change_type", S),
            ("reason", S),
        ],
        provenance=False,
    ),
    "snapshots": schema(
        [
            ("snapshot_id", S),
            ("source", S),
            ("url", S),
            ("fetched_at", TS),
            ("http_status", I16),
            ("body_sha256", S),
            ("body_bytes", pa.int64()),
            ("content_changed", pa.bool_()),
            ("parser", S),
            ("parser_version", S),
            ("parse_status", S),
            ("via", S),
            ("captured_at", TS),
            ("archive_url", S),
            ("observed_at", TS),
        ],
        provenance=False,
    ),
    "coverage": schema(
        [
            ("year", I16),
            ("source", S),
            ("via", S),
            ("events", I32),
            ("contests", I32),
            ("rounds", I32),
            ("entries", pa.int64()),
            ("events_registry_only", I32),
            ("events_index_only", I32),
            ("events_sheets_partial", I32),
            ("events_sheets_complete", I32),
            ("events_day_precision", I32),
            ("events_listed_only", I32),
            ("events_accepted", pa.bool_()),
            ("expected_rounds", I32),
            ("parsed_rounds", I32),
            ("unresolved_findings", I32),
            ("last_changed_at", TS),
        ],
        provenance=False,
    ),
}

# Public freshness is calculated from the pinned release and never written into
# canonical projection rows or their immutable generation payloads.
for _table in PUBLIC_SCOPE_TABLES:
    SCHEMAS[_table] = (
        SCHEMAS[_table]
        .append(pa.field("scope_status", S))
        .append(pa.field("evidence_observed_at", TS))
    )

for _name, _type in [
    ("scope_kind", S),
    ("scope_id", S),
    ("scope_status", S),
    ("scope_reasons", LS),
    ("missing_scopes", LS),
    ("discovered_units", pa.int64()),
    ("acquired_units", pa.int64()),
    ("interpreted_units", pa.int64()),
    ("mapped_units", pa.int64()),
    ("withheld_units", pa.int64()),
    ("unavailable_units", pa.int64()),
    ("unassessed_units", pa.int64()),
    ("resolved_identities", pa.int64()),
    ("identity_subjects", pa.int64()),
    ("withheld_identities", pa.int64()),
    ("withheld_scopes", pa.int64()),
    ("unavailable_scopes", pa.int64()),
    ("discovery_denominator", pa.int64()),
    ("discovery_universe", S),
    ("acquisition_denominator", pa.int64()),
    ("interpretation_denominator", pa.int64()),
    ("mapping_denominator", pa.int64()),
    ("evidence_cutoff", TS),
    ("evidence_observed_at", TS),
    ("usable_verified_at", TS),
    ("health_as_of", TS),
    ("method", S),
    ("population", S),
    ("uncertainty", S),
]:
    SCHEMAS["coverage"] = SCHEMAS["coverage"].append(pa.field(_name, _type))


for _name, _kind in EVENT_COVERAGE_FIELDS.items():
    SCHEMAS["coverage"] = SCHEMAS["coverage"].append(
        pa.field(_name, {"string": S, "strings": LS, "int": pa.int64(), "bool": pa.bool_()}[_kind])
    )


RELEASE_FIELDS: dict[str, tuple[str, ...]] = {
    **{table: tuple(sorted(PUBLIC_SCOPE_COLUMNS)) for table in PUBLIC_SCOPE_TABLES},
    "coverage": tuple(sorted(COVERAGE_PUBLIC_COLUMNS | EVENT_COVERAGE_FIELDS.keys())),
}


PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "events": ("event_id",),
    "contests": ("contest_id",),
    "rounds": ("round_id",),
    "entries": ("entry_id",),
    "heats": ("heat_id", "entry_id"),
    "judges": ("judge_id",),
    "callback_marks": ("round_id", "entry_id", "judge_id"),
    "callbacks": ("round_id", "entry_id"),
    "final_marks": ("round_id", "placement_id", "judge_id"),
    "placements": ("placement_id",),
    "dancers": ("wsdc_id",),
    "registry_placements": (
        "wsdc_id",
        "role",
        "series_id",
        "event_month",
        "division",
        "dance_style",
    ),
    "identity_links": ("link_id",),
    "link_candidates": ("subject_kind", "subject_id", "wsdc_id"),
    "review_queue": ("item_id",),
    "changelog": ("changed_at", "table", "record_key", "field"),
    "snapshots": ("snapshot_id",),
    "coverage": ("scope_kind", "scope_id", "source", "via"),
}
