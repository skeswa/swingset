"""Withdraw revoked interpretations from a pinned public graph without new facts."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from swingset.admission.support import interpretation_support

Rows = dict[str, list[Mapping[str, Any]]]
#: Rows of any mapping type; the methods below only read and replace them.
AnyRows = dict[str, list[Any]]
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


class Revocations:
    """Explicit revocations, checked once per snapshot and parser.

    `close_revoked_support` applies them to a whole release; the streamed
    assembly (`assembly.py`) applies the same steps to one partition at a
    time, with the release-wide sets (revoked dancers, registry ids, the
    events left) computed first. Counters accumulate across calls.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.cache: dict[tuple[str, str], bool] = {}
        self.reasons: Counter[str] = Counter()
        self.claims: Counter[str] = Counter()

    def key(self, row: Mapping[str, Any]) -> tuple[str, str] | None:
        snapshot = row.get("snapshot_id")
        if not snapshot:
            return None
        return str(snapshot), str(row.get("parser_version") or "")

    def check(self, key: tuple[str, str]) -> bool:
        if key not in self.cache:
            self.cache[key] = (
                interpretation_support(self.conn, key[0], parser_version=key[1])["state"]
                == "revoked"
            )
        return self.cache[key]

    def revoked(self, row: Mapping[str, Any]) -> bool:
        key = self.key(row)
        return key is not None and self.check(key)

    def omitted_events(self, tables: AnyRows) -> set[str]:
        """Events a revoked row names, closed over rows that name several events."""
        scopes = _EventScopes(tables)
        omitted: set[str] = set()
        for name in _EVENT_TABLES:
            for row in tables.get(name, []):
                if self.revoked(row):
                    omitted.update(scopes.events(row))
                    self.reasons["explicit_source_revocation"] += 1
        # Shared structural references cannot leave one event with partial scores.
        changed = True
        while changed:
            changed = False
            for name in _EVENT_TABLES:
                for row in tables.get(name, []):
                    linked = scopes.events(row)
                    if linked & omitted and not linked <= omitted:
                        omitted.update(linked)
                        changed = True
        return omitted

    def close_events(self, tables: AnyRows, omitted: set[str]) -> None:
        """Remove revoked event rows and every row of an omitted event."""
        scopes = _EventScopes(tables)
        for name in _EVENT_TABLES:
            if name in tables:
                tables[name] = [
                    row
                    for row in tables[name]
                    if not self.revoked(row) and not scopes.events(row) & omitted
                ]

    def close_registry(
        self, tables: AnyRows, revoked_dancers: set[Any], event_ids: set[Any]
    ) -> None:
        """Remove revoked dancers and registry facts; null associations left outside."""
        if "dancers" in tables:
            tables["dancers"] = [
                row for row in tables["dancers"] if row["wsdc_id"] not in revoked_dancers
            ]
        if "registry_placements" in tables:
            tables["registry_placements"] = [
                row
                for row in tables["registry_placements"]
                if not self.revoked(row) and row["wsdc_id"] not in revoked_dancers
            ]
        for index, row in enumerate(tables.get("registry_placements", [])):
            if row.get("event_id") is not None and row["event_id"] not in event_ids:
                tables["registry_placements"][index] = {**row, "event_id": None}
                self.claims["registry_event_association"] += 1

    def withdraw(self, tables: AnyRows, revoked_dancers: set[Any], registry_ids: set[Any]) -> None:
        _withdraw_identities(tables, revoked_dancers, registry_ids, self.claims)


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
    closure = Revocations(conn)
    revoked_dancers = {row["wsdc_id"] for row in tables.get("dancers", []) if closure.revoked(row)}
    revoked_registry_rows = [
        row for row in tables.get("registry_placements", []) if closure.revoked(row)
    ]
    revoked_registry_ids = {row["wsdc_id"] for row in revoked_registry_rows}
    closure.reasons["registry_fact_revoked"] = len(revoked_registry_rows)
    omitted_events = closure.omitted_events(tables)
    closure.reasons["event_scope_omitted"] = len(omitted_events)
    closure.reasons["dancer_support_revoked"] = len(revoked_dancers)
    omitted_years = {
        row.get("year") for row in tables.get("events", []) if row.get("event_id") in omitted_events
    }
    closure.close_events(tables, omitted_events)
    closure.close_registry(
        tables, revoked_dancers, {row["event_id"] for row in tables.get("events", [])}
    )
    closure.withdraw(tables, revoked_dancers, revoked_registry_ids)
    _close_subjects(tables, baseline_tables)
    # Counts/acceptance derived from an omitted event graph cannot be retained.
    if "coverage" in tables:
        tables["coverage"] = [
            row for row in tables["coverage"] if row.get("year") not in omitted_years
        ]
    closure.reasons["support_checks"] = len(closure.cache)
    return SupportClosure(
        tables,
        {
            name: count - len(tables[name])
            for name, count in initial.items()
            if count != len(tables[name])
        },
        dict(sorted(closure.claims.items())),
        {name: count for name, count in sorted(closure.reasons.items()) if count},
    )


def _withdraw_identities(
    tables: AnyRows, dancers: set[Any], registry_ids: set[Any], counts: Counter[str]
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


def close_subjects(tables: AnyRows) -> None:
    """Identity claims about a subject the release no longer holds go."""
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


#: The structural tables whose removed ids close review items.
REVIEW_SUBJECTS = (
    ("events", "event_id"),
    ("contests", "contest_id"),
    ("rounds", "round_id"),
    ("entries", "entry_id"),
    ("judges", "judge_id"),
)


def removed_ids(
    before: Mapping[str, Sequence[Mapping[str, Any]]],
    after: Mapping[str, Sequence[Mapping[str, Any]]],
) -> set[str]:
    """Structural ids the closure removed, which close their review items."""
    result: set[str] = set()
    for name, key in REVIEW_SUBJECTS:
        previous = {str(row[key]) for row in before.get(name, [])}
        current = {str(row[key]) for row in after.get(name, [])}
        result.update(previous - current)
    return result


def _close_subjects(tables: Rows, baseline: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    close_subjects(tables)
    if "review_queue" in tables:
        removed = removed_ids(baseline, tables)
        tables["review_queue"] = [
            row for row in tables["review_queue"] if str(row.get("subject_id")) not in removed
        ]
