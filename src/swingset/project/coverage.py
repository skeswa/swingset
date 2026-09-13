"""Coverage by the source and transport that actually support each fact."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from swingset.state.work import bump_revision

TIERS = ("registry_only", "index_only", "sheets_partial", "sheets_complete")


def rebuild_coverage(conn: sqlite3.Connection, *, now: str) -> None:
    from .history import _inventory_digest

    events = {str(row["event_id"]): dict(row) for row in conn.execute("SELECT * FROM events")}
    via = {
        str(row[0]): str(row[1]) for row in conn.execute("SELECT snapshot_id,via FROM snapshots")
    }
    groups: dict[tuple[int, str, str], dict[str, set[str]]] = defaultdict(
        lambda: {name: set() for name in ("events", "contests", "rounds", "entries")}
    )

    def record(table: str, event_id: str, key: str, source: str, snapshot: str) -> None:
        event = events.get(event_id)
        if event is None:
            return
        transport = via.get(snapshot, "manual" if snapshot == "override" else "origin")
        group = groups[int(event["year"]), source, transport]
        group["events"].add(event_id)
        group[table].add(key)

    for event_id, event in events.items():
        record("events", event_id, event_id, str(event["source"]), str(event["snapshot_id"]))
    for registry_record in conn.execute(
        "SELECT DISTINCT event_id,source,snapshot_id FROM registry_placements WHERE event_id IS NOT NULL"
    ):
        record(
            "events",
            str(registry_record[0]),
            str(registry_record[0]),
            str(registry_record[1]),
            str(registry_record[2]),
        )
    for table, key in (("contests", "contest_id"), ("entries", "entry_id")):
        for row in conn.execute(f"SELECT event_id,{key},source,snapshot_id FROM {table}"):
            record(table, *(str(value) for value in row))
    round_events: dict[str, set[str]] = defaultdict(set)
    for round_record in conn.execute(
        "SELECT c.event_id,r.round_id,r.source,r.snapshot_id FROM rounds r "
        "JOIN contests c USING(contest_id)"
    ):
        record("rounds", *(str(value) for value in round_record))
        round_events[str(round_record[0])].add(str(round_record[1]))

    # Enumeration closure belongs to H16. Retained round rows establish partial
    # sheet coverage; their count alone never establishes completeness.
    for event_id, event in events.items():
        if round_events[event_id] and event["coverage_tier"] in {"registry_only", "index_only"}:
            conn.execute(
                "UPDATE events SET coverage_tier='sheets_partial' WHERE event_id=?", (event_id,)
            )
            event["coverage_tier"] = "sheets_partial"

    old = {
        tuple(row[field] for field in ("year", "source", "via")): dict(row)
        for row in conn.execute("SELECT * FROM coverage")
    }
    desired: dict[tuple[int, str, str], dict[str, Any]] = {}
    acceptance = {
        int(row[0]): str(row[1])
        for row in conn.execute("SELECT year,inventory_digest FROM history_acceptance")
    }
    accepted_years = {
        year for year, digest in acceptance.items() if digest == _inventory_digest(conn, year)
    }
    year_findings = {
        int(row[0]): int(row[1])
        for row in conn.execute(
            "SELECT owner_id,COUNT(*) FROM findings WHERE owner_kind IN ('history_year','phase1_year') AND closed_at IS NULL GROUP BY owner_id"
        )
    }
    for (year, source, transport), counts in sorted(groups.items()):
        group_events = [events[event_id] for event_id in counts["events"]]
        tiers = {tier: 0 for tier in TIERS}
        for event in group_events:
            if round_events[str(event["event_id"])] & counts["rounds"]:
                tier = "sheets_partial"
            elif source == "wsdc_registry" and json.loads(event["history_source"]) == ["registry"]:
                tier = "registry_only"
            else:
                tier = "index_only"
            tiers[tier] += 1
        result_row: dict[str, Any] = {
            "year": year,
            "source": source,
            "via": transport,
            **{name: len(values) for name, values in counts.items()},
            **{f"events_{tier}": count for tier, count in tiers.items()},
            "events_day_precision": sum(event["date_precision"] == "day" for event in group_events),
            "events_listed_only": sum(event["held"] != "held" for event in group_events),
            "events_accepted": int(year in accepted_years and not year_findings.get(year)),
            "expected_rounds": None,
            "parsed_rounds": len(counts["rounds"]),
            "unresolved_findings": year_findings.get(year, 0),
        }
        previous = old.get((year, source, transport))
        unchanged = previous is not None and all(
            previous[name] == value for name, value in result_row.items()
        )
        result_row["last_changed_at"] = (
            previous["last_changed_at"] if unchanged and previous else now
        )
        desired[year, source, transport] = result_row
    if old == desired:
        return
    conn.execute("DELETE FROM coverage")
    for persisted in desired.values():
        columns = ",".join(persisted)
        placeholders = ",".join("?" for _ in persisted)
        conn.execute(
            f"INSERT INTO coverage({columns}) VALUES ({placeholders})", tuple(persisted.values())
        )
    bump_revision(conn, "canonical")
