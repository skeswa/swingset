"""Coverage by the source and transport that actually support each fact."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from swingset.state.work import bump_revision

TIERS = ("registry_only", "index_only", "sheets_partial", "sheets_complete")


def rebuild_coverage(conn: sqlite3.Connection, *, now: str) -> None:
    """Rebuild every year's coverage rows and sheet tiers (the legacy global pass)."""
    _rebuild(conn, now=now, year=None)


def rebuild_year_coverage(conn: sqlite3.Connection, year: int, *, now: str) -> bool:
    """Rebuild one year's coverage rows and its events' sheet tiers.

    The same rules as `rebuild_coverage`, reading only that year's events, their
    contests, entries and rounds, and the registry placements associated with
    them, whatever month the placement reports: a December event's placements
    can fall in January (plan F17). Only that year's rows are replaced; each
    keeps its `last_changed_at` while its values stay the same. Returns
    whether any coverage row changed.
    """
    return _rebuild(conn, now=now, year=year)


def _rebuild(conn: sqlite3.Connection, *, now: str, year: int | None) -> bool:
    from .history import _inventory_digest

    # One year, or every year. The event filter names the events whose rows
    # count; placements, contests, entries and rounds follow their event.
    events_where, parameters = ("WHERE year=?", (year,)) if year is not None else ("", ())
    owned = (
        " AND event_id IN (SELECT event_id FROM events WHERE year=?)" if year is not None else ""
    )
    events = {
        str(row["event_id"]): dict(row)
        for row in conn.execute("SELECT * FROM events " + events_where, parameters)
    }
    groups: dict[tuple[int, str, str], dict[str, set[str]]] = defaultdict(
        lambda: {name: set() for name in ("events", "contests", "rounds", "entries")}
    )
    facts: list[tuple[str, str, str, str, str]] = []

    def record(table: str, event_id: str, key: str, source: str, snapshot: str) -> None:
        if event_id in events:
            facts.append((table, event_id, key, source, snapshot))

    for event_id, event in events.items():
        record("events", event_id, event_id, str(event["source"]), str(event["snapshot_id"]))
    for registry_record in conn.execute(
        "SELECT DISTINCT event_id,source,snapshot_id FROM registry_placements "
        "WHERE event_id IS NOT NULL" + owned,
        parameters,
    ):
        record(
            "events",
            str(registry_record[0]),
            str(registry_record[0]),
            str(registry_record[1]),
            str(registry_record[2]),
        )
    for table, key in (("contests", "contest_id"), ("entries", "entry_id")):
        for row in conn.execute(
            f"SELECT event_id,{key},source,snapshot_id FROM {table} WHERE 1" + owned, parameters
        ):
            record(table, *(str(value) for value in row))
    round_events: dict[str, set[str]] = defaultdict(set)
    for round_record in conn.execute(
        "SELECT c.event_id,r.round_id,r.source,r.snapshot_id FROM rounds r "
        "JOIN contests c USING(contest_id)"
        + (" WHERE c.event_id IN (SELECT event_id FROM events WHERE year=?)" if owned else ""),
        parameters,
    ):
        record("rounds", *(str(value) for value in round_record))
        round_events[str(round_record[0])].add(str(round_record[1]))
    via = _transport(conn, {fact[4] for fact in facts}, whole=year is None)
    for table, event_id, key, source, snapshot in facts:
        transport = via.get(snapshot, "manual" if snapshot == "override" else "origin")
        group = groups[int(events[event_id]["year"]), source, transport]
        group["events"].add(event_id)
        group[table].add(key)

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
        for row in conn.execute("SELECT * FROM coverage " + events_where, parameters)
    }
    desired: dict[tuple[int, str, str], dict[str, Any]] = {}
    acceptance = {
        int(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT year,inventory_digest FROM history_acceptance " + events_where, parameters
        )
    }
    accepted_years = {
        accepted
        for accepted, digest in acceptance.items()
        if digest == _inventory_digest(conn, accepted)
    }
    year_findings = {
        int(row[0]): int(row[1])
        for row in conn.execute(
            "SELECT owner_id,COUNT(*) FROM findings WHERE owner_kind IN ('history_year','phase1_year') "
            "AND closed_at IS NULL"
            + (" AND owner_id=?" if year is not None else "")
            + " GROUP BY owner_id",
            (str(year),) if year is not None else (),
        )
    }
    for (group_year, source, transport), counts in sorted(groups.items()):
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
            "year": group_year,
            "source": source,
            "via": transport,
            **{name: len(values) for name, values in counts.items()},
            **{f"events_{tier}": count for tier, count in tiers.items()},
            "events_day_precision": sum(event["date_precision"] == "day" for event in group_events),
            "events_listed_only": sum(event["held"] != "held" for event in group_events),
            "events_accepted": int(
                group_year in accepted_years and not year_findings.get(group_year)
            ),
            "expected_rounds": None,
            "parsed_rounds": len(counts["rounds"]),
            "unresolved_findings": year_findings.get(group_year, 0),
        }
        previous = old.get((group_year, source, transport))
        unchanged = previous is not None and all(
            previous[name] == value for name, value in result_row.items()
        )
        result_row["last_changed_at"] = (
            previous["last_changed_at"] if unchanged and previous else now
        )
        desired[group_year, source, transport] = result_row
    if old == desired:
        return False
    conn.execute("DELETE FROM coverage " + events_where, parameters)
    for persisted in desired.values():
        columns = ",".join(persisted)
        placeholders = ",".join("?" for _ in persisted)
        conn.execute(
            f"INSERT INTO coverage({columns}) VALUES ({placeholders})", tuple(persisted.values())
        )
    bump_revision(conn, "canonical")
    return True


def _transport(conn: sqlite3.Connection, snapshots: set[str], *, whole: bool) -> dict[str, str]:
    """How each snapshot was fetched; one year reads only its own snapshots."""
    if whole:
        return {
            str(row[0]): str(row[1])
            for row in conn.execute("SELECT snapshot_id,via FROM snapshots")
        }
    return {
        str(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT snapshot_id,via FROM snapshots WHERE snapshot_id IN "
            "(SELECT value FROM json_each(?))",
            (json.dumps(sorted(snapshots)),),
        )
    }
