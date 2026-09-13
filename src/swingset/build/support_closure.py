"""Withdraw revoked interpretations from a pinned public graph without new facts."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from swingset.admission.support import interpretation_support

Rows = dict[str, list[Mapping[str, Any]]]
_EVENT_TABLES = frozenset(
    {
        "events",
        "contests",
        "rounds",
        "entries",
        "judges",
        "heats",
        "callback_marks",
        "callbacks",
        "final_marks",
        "placements",
    }
)


@dataclass(frozen=True)
class SupportClosure:
    tables: Rows
    removed_rows: dict[str, int]
    withdrawn_claims: dict[str, int]
    reasons: dict[str, int]


class _EventScopes:
    def __init__(self, tables: Rows) -> None:
        self.contests = {r["contest_id"]: r.get("event_id") for r in tables.get("contests", [])}
        self.rounds = {
            r["round_id"]: self.contests.get(r.get("contest_id")) for r in tables.get("rounds", [])
        }
        self.entries = {
            r["entry_id"]: r.get("event_id") or self.contests.get(r.get("contest_id"))
            for r in tables.get("entries", [])
        }
        self.judges = {r["judge_id"]: r.get("event_id") for r in tables.get("judges", [])}
        self.placements = {
            r["placement_id"]: r.get("event_id") or self.rounds.get(r.get("round_id"))
            for r in tables.get("placements", [])
        }

    def events(self, row: Mapping[str, Any]) -> set[str]:
        values = {
            row.get("event_id"),
            self.contests.get(row.get("contest_id")),
            self.rounds.get(row.get("round_id")),
            self.judges.get(row.get("judge_id")),
            self.judges.get(row.get("chief_judge_id")),
            self.placements.get(row.get("placement_id")),
        }
        values.update(
            self.entries.get(row.get(field))
            for field in (
                "entry_id",
                "partner_entry_id",
                "leader_entry_id",
                "follower_entry_id",
                "couple_entry_id",
            )
        )
        return {str(value) for value in values if value is not None}


def close_revoked_support(
    conn: sqlite3.Connection,
    baseline_tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> SupportClosure:
    """Close explicit revocations without changing the supplied baseline.

    The caller supplies the acknowledged baseline for a correction-only build.
    This function reads only admission decisions from the current database; it
    cannot select a replacement generation or add a public row. Missing or
    unassessed support is left to the caller's disclosed legacy policy.
    Returned reasons contain aggregate codes, never review notes or identifiers.
    """
    # Lists belong to this result; unchanged rows remain shared read-only values.
    tables: Rows = {name: list(rows) for name, rows in baseline_tables.items()}
    initial = {name: len(rows) for name, rows in tables.items()}
    reasons: Counter[str] = Counter()
    claims: Counter[str] = Counter()
    cache: dict[tuple[str, str], bool] = {}

    def revoked(row: Mapping[str, Any]) -> bool:
        snapshot = row.get("snapshot_id")
        if not snapshot:
            return False
        key = str(snapshot), str(row.get("parser_version") or "")
        if key not in cache:
            cache[key] = (
                interpretation_support(conn, key[0], parser_version=key[1])["state"] == "revoked"
            )
        return cache[key]

    scopes = _EventScopes(tables)
    omitted_events: set[str] = set()
    revoked_dancers = {row["wsdc_id"] for row in tables.get("dancers", []) if revoked(row)}
    revoked_registry_rows = [row for row in tables.get("registry_placements", []) if revoked(row)]
    revoked_registry_ids = {row["wsdc_id"] for row in revoked_registry_rows}
    reasons["registry_fact_revoked"] = len(revoked_registry_rows)
    for name in _EVENT_TABLES:
        for row in tables.get(name, []):
            if revoked(row):
                omitted_events.update(scopes.events(row))
                reasons["explicit_source_revocation"] += 1
    # Shared structural references cannot leave one event with partial scores.
    changed = True
    while changed:
        changed = False
        for name in _EVENT_TABLES:
            for row in tables.get(name, []):
                linked = scopes.events(row)
                if linked & omitted_events and not linked <= omitted_events:
                    omitted_events.update(linked)
                    changed = True
    reasons["event_scope_omitted"] = len(omitted_events)
    reasons["dancer_support_revoked"] = len(revoked_dancers)
    omitted_years = {
        row.get("year") for row in tables.get("events", []) if row.get("event_id") in omitted_events
    }
    for name in _EVENT_TABLES:
        if name in tables:
            tables[name] = [
                row
                for row in tables[name]
                if not revoked(row) and not scopes.events(row) & omitted_events
            ]
    if "dancers" in tables:
        tables["dancers"] = [
            row for row in tables["dancers"] if row["wsdc_id"] not in revoked_dancers
        ]
    if "registry_placements" in tables:
        tables["registry_placements"] = [
            row
            for row in tables["registry_placements"]
            if not revoked(row) and row["wsdc_id"] not in revoked_dancers
        ]
    event_ids = {row["event_id"] for row in tables.get("events", [])}
    for index, row in enumerate(tables.get("registry_placements", [])):
        if row.get("event_id") is not None and row["event_id"] not in event_ids:
            tables["registry_placements"][index] = {**row, "event_id": None}
            claims["registry_event_association"] += 1
    _withdraw_identities(tables, revoked_dancers, revoked_registry_ids, claims)
    _close_subjects(tables, baseline_tables)
    # Counts/acceptance derived from an omitted event graph cannot be retained.
    if "coverage" in tables:
        tables["coverage"] = [
            row for row in tables["coverage"] if row.get("year") not in omitted_years
        ]
    reasons["support_checks"] = len(cache)
    return SupportClosure(
        tables,
        {
            name: count - len(tables[name])
            for name, count in initial.items()
            if count != len(tables[name])
        },
        dict(sorted(claims.items())),
        {name: count for name, count in sorted(reasons.items()) if count},
    )


def _withdraw_identities(
    tables: Rows, dancers: set[Any], registry_ids: set[Any], counts: Counter[str]
) -> None:
    revoked_subjects = {
        (row.get("subject_kind"), row.get("subject_id"))
        for row in tables.get("identity_links", [])
        if row.get("method") == "registry_placement" and row.get("wsdc_id") in registry_ids
    }
    withdrawn_entries = set()
    for name in ("entries", "judges"):
        for index, row in enumerate(tables.get(name, [])):
            if (
                row.get("wsdc_id") in dancers
                or (
                    "entry" if name == "entries" else "judge",
                    row.get("entry_id" if name == "entries" else "judge_id"),
                )
                in revoked_subjects
            ):
                updated = {**row, "wsdc_id": None}
                if name == "entries":
                    withdrawn_entries.add(row["entry_id"])
                    updated.update(link_status="unmatched", link_confidence=0.0)
                tables[name][index] = updated
                counts["default_identity"] += 1
    for index, row in enumerate(tables.get("dancers", [])):
        if row.get("merged_into_wsdc_id") in dancers:
            tables["dancers"][index] = {**row, "merged_into_wsdc_id": None}
            counts["dancer_merge"] += 1
    for index, row in enumerate(tables.get("placements", [])):
        changed = False
        updates: dict[str, Any] = {}
        for role in ("leader", "follower"):
            identifier = row.get(f"{role}_wsdc_id")
            if identifier in dancers or row.get(f"{role}_entry_id") in withdrawn_entries:
                updates[f"{role}_wsdc_id"] = None
                counts["placement_identity"] += 1
                changed = True
            if identifier in dancers | registry_ids or (
                identifier is not None
                and updates.get(f"{role}_wsdc_id", row.get(f"{role}_wsdc_id")) is None
            ):
                if row.get(f"registry_points_{role}") is not None:
                    counts["registry_points"] += 1
                updates[f"registry_points_{role}"] = None
                changed = True
        if changed:
            tables["placements"][index] = {
                **row,
                **updates,
                "registry_confirmed": False,
                "points_matches_expected": None,
            }
    for index, row in enumerate(tables.get("identity_links", [])):
        if (
            row.get("wsdc_id") in dancers
            or (row.get("subject_kind"), row.get("subject_id")) in revoked_subjects
        ):
            updated = {
                **row,
                "wsdc_id": None,
                "status": "unmatched",
                "method": "none",
                "confidence": 0.0,
            }
            if "acceptance_state" in row:
                updated["acceptance_state"] = "revoked"
            tables["identity_links"][index] = updated
            counts["identity_assertion"] += 1
    for index, row in enumerate(tables.get("link_candidates", [])):
        if (row.get("subject_kind"), row.get("subject_id")) in revoked_subjects:
            tables["link_candidates"][index] = {**row, "chosen": False}
    if "link_candidates" in tables:
        tables["link_candidates"] = [
            row
            for row in tables["link_candidates"]
            if row.get("wsdc_id") not in dancers
            and not (row.get("wsdc_id") in registry_ids and row.get("registry_confirms"))
        ]


def _close_subjects(tables: Rows, baseline: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    subjects = {
        (kind, str(row[key]))
        for kind, name, key in (("entry", "entries", "entry_id"), ("judge", "judges", "judge_id"))
        for row in tables.get(name, [])
    }
    for name in ("identity_links", "link_candidates"):
        if name in tables:
            tables[name] = [
                row
                for row in tables[name]
                if (row.get("subject_kind"), str(row.get("subject_id"))) in subjects
            ]
    if "review_queue" in tables:
        removed_ids = set()
        for name, key in (
            ("events", "event_id"),
            ("contests", "contest_id"),
            ("rounds", "round_id"),
            ("entries", "entry_id"),
            ("judges", "judge_id"),
        ):
            previous = {str(row[key]) for row in baseline.get(name, [])}
            current = {str(row[key]) for row in tables.get(name, [])}
            removed_ids.update(previous - current)
        tables["review_queue"] = [
            row for row in tables["review_queue"] if str(row.get("subject_id")) not in removed_ids
        ]
