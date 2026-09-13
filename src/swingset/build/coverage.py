"""Describe a pinned release's evidence, omissions, and bounded public health.

The caller supplies final selected tables and a read snapshot at the cutoff.
This module reads retained metadata only; it neither fetches nor advances any
admission, interpretation, identity, or publication pointer.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from swingset.model.schema import PUBLIC_SCOPE_TABLES

from .builder import BuildInput
from .schema import PRIMARY_KEYS, SCHEMAS

HEALTH_CADENCE_SECONDS = 86400
METHOD = "selected-generation-and-retained-inventory-v1"
_COUNTS = (
    "discovered_units",
    "acquired_units",
    "interpreted_units",
    "mapped_units",
    "withheld_units",
    "unavailable_units",
    "unassessed_units",
)
_FACT_COUNTS = ("events", "contests", "rounds", "entries")
Scope = tuple[str, str, str, str]


def _at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def health_token(
    conn: sqlite3.Connection, *, now: datetime, cadence_seconds: int = HEALTH_CADENCE_SECONDS
) -> str:
    """Daily initial cadence plus immediate material changes; no success-poll churn."""
    if now.tzinfo is None or cadence_seconds <= 0:
        raise ValueError("public health requires an aware clock and positive cadence")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    material: dict[str, Any] = {}
    queries = {
        "watches": "SELECT source,kind,state,ever_ok,COUNT(*) FROM watches GROUP BY source,kind,state,ever_ok ORDER BY source,kind,state,ever_ok",
        "source_generations": "SELECT page_kind,state,COUNT(*) FROM source_generations GROUP BY page_kind,state ORDER BY page_kind,state",
        "findings": "SELECT source,kind,state,COUNT(*) FROM findings WHERE closed_at IS NULL GROUP BY source,kind,state ORDER BY source,kind,state",
        "registry_verifications": "SELECT w.source,v.usable,v.reason,COUNT(*) FROM registry_verifications v JOIN watches w USING(watch_id) WHERE v.verification_id=(SELECT max(v2.verification_id) FROM registry_verifications v2 WHERE v2.watch_id=v.watch_id) GROUP BY w.source,v.usable,v.reason ORDER BY w.source,v.usable,v.reason",
    }
    for table, query in queries.items():
        if table in tables:
            material[table] = [tuple(row) for row in conn.execute(query)]
    return _digest(
        {
            "method": METHOD,
            "cadence_seconds": cadence_seconds,
            "bucket": int(now.timestamp()) // cadence_seconds,
            "material": material,
        }
    )


class _Coverage:
    def __init__(
        self,
        conn: sqlite3.Connection,
        data: BuildInput,
        cutoff: datetime,
        closure: Mapping[str, Any],
        sources: Collection[str],
        selected_mapping: Iterable[Mapping[str, Any]],
    ):
        self.conn, self.data, self.cutoff, self.closure = conn, data, cutoff, closure
        self.selected_mapping = tuple(selected_mapping)
        self.events = {str(row["event_id"]): row for row in data.tables.get("events", ())}
        self.contests = {
            str(row["contest_id"]): row.get("event_id") for row in data.tables.get("contests", ())
        }
        self.snapshots = {str(row["snapshot_id"]): row for row in data.tables.get("snapshots", ())}
        self.assertions = {
            (str(row.get("subject_kind")), str(row.get("subject_id"))): row
            for row in data.tables.get("identity_links", ())
        }
        self.snapshot_events: dict[str, set[str]] = defaultdict(set)
        self.sets: dict[Scope, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.reasons: dict[Scope, set[str]] = defaultdict(set)
        self.times: dict[Scope, list[datetime]] = defaultdict(list)
        self.verifications: dict[Scope, list[datetime]] = defaultdict(list)
        self.event_status: dict[str, str] = {}
        self.link_status: dict[str, str] = {}
        self.snapshot_status: dict[str, str] = {}
        self.snapshot_parsers: dict[str, set[str]] = defaultdict(set)
        self.selected_units: dict[str, str] = {}
        self.missing: dict[Scope, set[str]] = defaultdict(set)
        self.selected_snapshots: set[str] = set()
        self.selected_source_generations: set[str] = set()
        self.existing = {
            (int(row["year"]), str(row["source"]), str(row["via"])): row
            for row in data.tables.get("coverage", ())
            if row.get("scope_kind") in (None, "year") and row.get("year") is not None
        }
        for source in sources:
            self.sets[("source", source, source, "unknown")]
        for year, source, via in self.existing:
            self.sets[("year", str(year), source, via)]
        for item in closure.get("source_support", ()):
            if isinstance(item, Mapping):
                if item.get("snapshot_id"):
                    self.selected_snapshots.add(str(item["snapshot_id"]))
                    snapshot_id = str(item["snapshot_id"])
                    status = str(item.get("state") or "legacy_unassessed")
                    prior_status = self.snapshot_status.get(snapshot_id)
                    if prior_status != "revoked" and (status != "accepted" or prior_status is None):
                        self.snapshot_status[snapshot_id] = status
                for receipt in item.get("source_generations", ()):
                    if isinstance(receipt, Mapping) and receipt.get("generation_id"):
                        generation_id = str(receipt["generation_id"])
                        if generation_id in self.selected_source_generations:
                            continue
                        self.selected_source_generations.add(generation_id)
                        generation = conn.execute(
                            "SELECT unit_key FROM source_generations WHERE generation_id=?",
                            (generation_id,),
                        ).fetchone()
                        if generation is not None:
                            self.selected_units[str(generation[0])] = generation_id
                for field in ("generation_id", "accepted_generation_id"):
                    if item.get(field):
                        self.selected_source_generations.add(str(item[field]))

    def scopes(self, source: str, via: str, event_ids: Collection[str] = ()) -> list[Scope]:
        result: set[Scope] = {("source", source, source, via)}
        for event_id in event_ids:
            event = self.events.get(event_id)
            if event is None:
                continue
            result.add(("event", event_id, source, via))
            if event.get("year") is not None:
                result.add(("year", str(event["year"]), source, via))
        return sorted(result)

    def _record(self, row: Mapping[str, Any], metric: str, key: str, event_id: str | None) -> None:
        source, snapshot_id = str(row.get("source") or "unknown"), str(row.get("snapshot_id") or "")
        snapshot = self.snapshots.get(snapshot_id, {})
        via = str(snapshot.get("via") or ("manual" if snapshot_id == "override" else "origin"))
        if snapshot_id:
            self.selected_snapshots.add(snapshot_id)
            if row.get("parser_version") is not None:
                self.snapshot_parsers[snapshot_id].add(str(row["parser_version"]))
            if event_id:
                self.snapshot_events[snapshot_id].add(event_id)
        for scope in self.scopes(source, via, [event_id] if event_id else []):
            self.sets[scope][metric].add(key)
            if event_id:
                self.sets[scope]["events"].add(event_id)
            observed = _at(
                snapshot.get("observed_at")
                or snapshot.get("captured_at")
                or snapshot.get("fetched_at")
            )
            if observed is not None and observed <= self.cutoff:
                self.times[scope].append(observed)
            else:
                self.reasons[scope].add("evidence_time_unknown")

    def facts(self) -> None:
        for table, key in (
            ("events", "event_id"),
            ("contests", "contest_id"),
            ("rounds", "round_id"),
            ("entries", "entry_id"),
        ):
            for row in self.data.tables.get(table, ()):
                event_id = row.get("event_id") or self.contests.get(str(row.get("contest_id")))
                self._record(row, table, str(row[key]), str(event_id) if event_id else None)
        for row in self.data.tables.get("registry_placements", ()):
            if row.get("event_id"):
                self._record(row, "events", str(row["event_id"]), str(row["event_id"]))
        for table, key in (("entries", "entry_id"), ("judges", "judge_id")):
            for row in self.data.tables.get(table, ()):
                self._record(row, "identity_subjects", str(row[key]), str(row["event_id"]))
                assertion = self.assertions.get(
                    ("entry" if table == "entries" else "judge", str(row[key])), {}
                )
                if (
                    row.get("wsdc_id") is None
                    and assertion.get("wsdc_id") is not None
                    and assertion.get("acceptance_state") != "accepted"
                ):
                    self._record(row, "withheld_identities", str(row[key]), str(row["event_id"]))
                if row.get("wsdc_id") is not None:
                    self._record(row, "resolved_identities", str(row[key]), str(row["event_id"]))
        for row in self.data.tables.get("dancers", ()):
            self._record(row, "dancer_records", str(row["wsdc_id"]), None)

    def inventory(self) -> None:
        tables = {
            row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "watches" not in tables:
            return
        latest = {
            str(row["watch_id"]): dict(row)
            for row in self.conn.execute(
                "SELECT * FROM (SELECT s.*,row_number() OVER(PARTITION BY watch_id ORDER BY julianday(fetched_at) DESC,snapshot_id DESC) AS ordinal FROM snapshots s WHERE julianday(fetched_at)<=julianday(?)) WHERE ordinal=1",
                (self.cutoff.isoformat(),),
            )
        }
        acquired_snapshots = {
            str(row["watch_id"]): dict(row)
            for row in self.conn.execute(
                "SELECT * FROM (SELECT s.*,row_number() OVER(PARTITION BY watch_id ORDER BY julianday(fetched_at) DESC,snapshot_id DESC) AS ordinal FROM snapshots s WHERE body_sha256 IS NOT NULL AND http_status IN (200,304) AND julianday(fetched_at)<=julianday(?)) WHERE ordinal=1",
                (self.cutoff.isoformat(),),
            )
        }
        watches = {
            str(row["watch_id"]): dict(row) for row in self.conn.execute("SELECT * FROM watches")
        }
        units: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if "source_units" in tables:
            for row in self.conn.execute(
                "WITH newest AS (SELECT unit_key,state,json_extract(recipe_json,'$.context.snapshot_id') AS input_snapshot_id,row_number() OVER(PARTITION BY unit_key ORDER BY julianday(created_at) DESC,generation_id DESC) AS ordinal FROM source_generations WHERE julianday(created_at)<=julianday(?)) SELECT u.*,g.state AS accepted_state,n.state AS latest_state,n.input_snapshot_id FROM source_units u LEFT JOIN source_generations g ON g.generation_id=u.accepted_generation_id LEFT JOIN newest n ON n.unit_key=u.unit_key AND n.ordinal=1",
                (self.cutoff.isoformat(),),
            ):
                units[str(row["watch_id"])].append(dict(row))
        verified: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if "registry_verifications" in tables:
            for row in self.conn.execute(
                "SELECT watch_id,snapshot_id,body_sha256,parser_version,checked_at FROM registry_verifications WHERE usable=1 AND julianday(checked_at)<=julianday(?)",
                (self.cutoff.isoformat(),),
            ):
                verified[str(row["watch_id"])].append(dict(row))
        mapping: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in self.selected_mapping:
            if str(row["event_id"]) in self.events:
                mapping[str(row["source"]), str(row["source_ref"])].add(str(row["event_id"]))
        for watch_id, watch in watches.items():
            snapshot = latest.get(watch_id, {})
            source = str(watch["source"])
            via = str(
                acquired_snapshots.get(watch_id, {}).get("via")
                or snapshot.get("via")
                or ("wayback" if watch.get("archive_url") else "origin")
            )
            event_ids = set(mapping.get((source, str(watch.get("source_ref"))), ()))
            event_ids.update(self.snapshot_events.get(str(snapshot.get("snapshot_id")), ()))
            event_ids.update(
                self.snapshot_events.get(str(watch.get("current_observation_snapshot_id")), ())
            )
            for unit in units.get(watch_id) or [{"unit_key": watch_id}]:
                key = str(unit["unit_key"])
                accepted = self.selected_units.get(key) or unit.get("accepted_generation_id")
                admitted = bool(accepted and accepted in self.selected_source_generations)
                selected = (
                    str(snapshot.get("snapshot_id")) in self.selected_snapshots
                    or str(watch.get("current_observation_snapshot_id")) in self.selected_snapshots
                )
                interpreted = admitted or bool(
                    selected and watch.get("current_observation_snapshot_id")
                )
                acquisition: Mapping[str, Any] = acquired_snapshots.get(watch_id, {})
                if key != watch_id and unit.get("input_snapshot_id"):
                    acquisition = self.snapshots.get(str(unit["input_snapshot_id"]), {})
                acquired = bool(
                    acquisition.get("body_sha256") and acquisition.get("http_status") in (200, 304)
                )
                unavailable = watch.get("state") in (
                    "gone",
                    "unavailable",
                    "unpublished",
                ) or snapshot.get("http_status") in (404, 410)
                withheld = (
                    unit.get("accepted_state") == "revoked"
                    or unit.get("latest_state") in {"needs_review", "waiting_for_inputs", "revoked"}
                    or bool(acquired and snapshot.get("parse_status") == "failed")
                )
                metrics = {
                    "discovered_units": True,
                    "acquired_units": acquired,
                    "interpreted_units": interpreted,
                    "mapped_units": interpreted and bool(event_ids),
                    "withheld_units": withheld,
                    "unavailable_units": unavailable,
                    "unassessed_units": interpreted and not admitted,
                }
                for scope in self.scopes(source, via, event_ids):
                    for name, present in metrics.items():
                        if present:
                            self.sets[scope][name].add(key)
                    if not acquired:
                        self.reasons[scope].add("body_not_acquired")
                    if acquired and not interpreted:
                        self.reasons[scope].add("interpretation_not_selected")
                    if withheld:
                        self.reasons[scope].add("interpretation_withheld")
                    if unavailable:
                        self.reasons[scope].add("source_unavailable")
                    if interpreted and not admitted:
                        self.reasons[scope].add("legacy_unassessed")
                    matching = [
                        _at(row["checked_at"])
                        for row in verified.get(watch_id, ())
                        if str(row["snapshot_id"]) in self.selected_snapshots
                        and str(row["parser_version"])
                        in self.snapshot_parsers[str(row["snapshot_id"])]
                        and row["body_sha256"]
                        == self.snapshots.get(str(row["snapshot_id"]), {}).get("body_sha256")
                    ]
                    valid = [value for value in matching if value is not None]
                    if valid:
                        self.verifications[scope].append(max(valid))

    def omissions(self) -> None:
        for item in self.closure.get("inventory", ()):
            if not isinstance(item, Mapping):
                continue
            status = str(item.get("status") or "unknown")
            if status == "selected":
                continue
            kind, identifier = str(item.get("unit_kind") or ""), str(item.get("unit_id") or "")
            source = str(item.get("source") or ("wsdc_registry" if kind == "dancer" else "unknown"))
            if kind == "snapshot":
                retained = self.conn.execute(
                    "SELECT w.source FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=?",
                    (identifier,),
                ).fetchone()
                if retained is not None:
                    source = str(retained[0])
            event_ids = [identifier] if kind == "event" else []
            if kind == "event":
                if item.get("stage") == "link":
                    self.link_status[identifier] = status
                else:
                    self.event_status[identifier] = status
                source = str(self.events.get(identifier, {}).get("source") or source)
            scopes = self.scopes(source, "unknown", event_ids)
            # An omitted event must remain named even when no event row survived.
            if kind == "event" and identifier not in self.events:
                scopes.append(("event", identifier, source, "unknown"))
            for scope in scopes:
                self.sets[scope]
                self.missing[scope].add(
                    f"{item.get('stage')}:{kind}"
                    if kind == "dancer"
                    else f"{item.get('stage')}:{kind}:{identifier}"
                )
                self.reasons[scope].add(str(item.get("reason") or "scope_not_selected"))
                if status in {"withheld", "unavailable"}:
                    self.sets[scope][status + "_scopes"].add(
                        f"{item.get('stage')}:{kind}:{identifier}"
                    )
                elif status == "retained":
                    self.reasons[scope].add("retained_previous_generation")

    def rows(self) -> list[dict[str, Any]]:
        result = []
        for scope, values in sorted(self.sets.items()):
            if (
                scope[0] == "source"
                and scope[3] == "unknown"
                and not any(values.values())
                and not self.reasons[scope]
                and any(
                    other[0] == "source" and other[2] == scope[2] and other[3] != "unknown"
                    for other in self.sets
                )
            ):
                continue
            kind, identifier, source, via = scope
            year = (
                int(identifier)
                if kind == "year"
                else self.events.get(identifier, {}).get("year")
                if kind == "event"
                else None
            )
            old = self.existing.get((int(identifier), source, via), {}) if kind == "year" else {}
            row: dict[str, Any] = {name: old.get(name) for name in SCHEMAS["coverage"].names}
            row.update(
                {
                    name: len(values[name])
                    for name in (
                        *_FACT_COUNTS,
                        *_COUNTS,
                        "resolved_identities",
                        "identity_subjects",
                        "withheld_identities",
                        "withheld_scopes",
                        "unavailable_scopes",
                    )
                }
            )
            for name in (
                "events_registry_only",
                "events_index_only",
                "events_sheets_partial",
                "events_sheets_complete",
                "events_day_precision",
                "events_listed_only",
                "parsed_rounds",
                "unresolved_findings",
            ):
                row[name] = int(old.get(name) or 0)
            if kind != "year":
                scoped_events = [self.events[key] for key in values["events"] if key in self.events]
                for tier in ("registry_only", "index_only", "sheets_partial", "sheets_complete"):
                    row["events_" + tier] = sum(
                        event.get("coverage_tier") == tier for event in scoped_events
                    )
                row["events_day_precision"] = sum(
                    event.get("date_precision") == "day" for event in scoped_events
                )
                row["events_listed_only"] = sum(
                    event.get("held") != "held" for event in scoped_events
                )
                row["parsed_rounds"] = len(values["rounds"])
            reasons = sorted(self.reasons[scope])
            status = next(
                (
                    state
                    for state, count in (
                        (
                            "withheld",
                            row["withheld_units"]
                            + row["withheld_scopes"]
                            + row["withheld_identities"],
                        ),
                        ("unavailable", row["unavailable_units"] + row["unavailable_scopes"]),
                        ("resolved", row["resolved_identities"]),
                        ("mapped", row["mapped_units"]),
                        ("interpreted", row["interpreted_units"]),
                        ("acquired", row["acquired_units"]),
                        ("discovered", row["discovered_units"]),
                    )
                    if count
                ),
                "unknown",
            )
            row.update(
                scope_kind=kind,
                scope_id=identifier,
                source=source,
                via=via,
                year=year,
                scope_status=status,
                scope_reasons=reasons,
                missing_scopes=sorted(self.missing[scope]),
                discovery_denominator=None,
                discovery_universe="unknown",
                acquisition_denominator=row["discovered_units"],
                interpretation_denominator=row["acquired_units"],
                mapping_denominator=row["interpreted_units"],
                evidence_cutoff=self.cutoff,
                evidence_observed_at=max(self.times[scope], default=None),
                usable_verified_at=max(self.verifications[scope], default=None),
                health_as_of=datetime.fromtimestamp(
                    int(self.cutoff.timestamp()) // HEALTH_CADENCE_SECONDS * HEALTH_CADENCE_SECONDS,
                    UTC,
                ),
                method=METHOD,
                population="unit metrics: distinct retained source units; scope metrics: omitted derivation scopes; identity metrics: selected entry and judge subjects",
                uncertainty="unknown discovery universe; counts are not reviewed identity accuracy",
                events_accepted=bool(old.get("events_accepted")) if kind == "year" else None,
                last_changed_at=_at(old.get("last_changed_at")) or self.cutoff,
            )
            result.append(row)
        return result


def enrich_coverage(
    conn: sqlite3.Connection,
    data: BuildInput,
    *,
    cutoff: datetime,
    closure: Mapping[str, Any] | None = None,
    sources: Collection[str] = (),
    selected_mapping: Iterable[Mapping[str, Any]] | None = None,
) -> BuildInput:
    """Attach public disclosure after closure and identity restriction, in one read snapshot."""
    if cutoff.tzinfo is None:
        raise ValueError("coverage cutoff requires timezone-aware time")
    cutoff = cutoff.astimezone(UTC)
    if not sources:
        from swingset.sources import sources as registered_sources

        sources = tuple(source.name for source in registered_sources())
    coverage = _Coverage(conn, data, cutoff, closure or {}, sources, selected_mapping or ())
    coverage.facts()
    coverage.inventory()
    coverage.omissions()
    tables = dict(data.tables)
    for table in PUBLIC_SCOPE_TABLES:
        annotated = []
        for original in tables.get(table, ()):
            row = dict(original)
            event_id = str(
                row.get("event_id") or coverage.contests.get(str(row.get("contest_id"))) or ""
            )
            snapshot = coverage.snapshots.get(str(row.get("snapshot_id")), {})
            observed = _at(
                snapshot.get("observed_at")
                or snapshot.get("captured_at")
                or snapshot.get("fetched_at")
            )
            row["evidence_observed_at"] = (
                observed if observed is not None and observed <= cutoff else None
            )
            row["scope_status"] = coverage.event_status.get(
                event_id,
                coverage.snapshot_status.get(str(row.get("snapshot_id")), "legacy_unassessed"),
            )
            if table in {"entries", "judges", "placements"} and coverage.link_status.get(
                event_id
            ) in {"withheld", "unavailable"}:
                row["scope_status"] = "identity_withheld"
            subject_kind = "entry" if table == "entries" else "judge" if table == "judges" else None
            if subject_kind:
                assertion = coverage.assertions.get(
                    (subject_kind, str(row.get(subject_kind + "_id"))), {}
                )
                if (
                    row.get("wsdc_id") is None
                    and assertion.get("wsdc_id") is not None
                    and assertion.get("acceptance_state") != "accepted"
                ):
                    row["scope_status"] = "identity_withheld"
            annotated.append(row)
        tables[table] = annotated
    tables["coverage"] = coverage.rows()
    return replace(data, tables=tables, schemas=SCHEMAS, primary_keys=PRIMARY_KEYS)


def refresh_identity_counts(rows: Mapping[str, list[dict[str, Any]]]) -> None:
    """Recount final public facts after suppression learns historical aliases.

    Retained acquisition evidence, omission reasons and timestamps stay pinned.
    Only counts supported by emitted rows, and their resulting status, change.
    """
    events = {str(row["event_id"]): row for row in rows.get("events", ())}
    contests = {str(row["contest_id"]): row.get("event_id") for row in rows.get("contests", ())}
    snapshots = {str(row["snapshot_id"]): row for row in rows.get("snapshots", ())}
    assertions = {
        (row["subject_kind"], row["subject_id"]): row for row in rows.get("identity_links", ())
    }
    counts: dict[Scope, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    def record(row: Mapping[str, Any], metric: str, identifier: str) -> None:
        source = str(row.get("source") or "unknown")
        snapshot_id = str(row.get("snapshot_id") or "")
        via = str(
            snapshots.get(snapshot_id, {}).get("via")
            or ("manual" if snapshot_id == "override" else "origin")
        )
        event_id = str(row.get("event_id") or contests.get(str(row.get("contest_id"))) or "")
        scopes = [("source", source, source, via)]
        if event_id in events:
            scopes.append(("event", event_id, source, via))
            if events[event_id].get("year") is not None:
                scopes.append(("year", str(events[event_id]["year"]), source, via))
        for scope in scopes:
            counts[scope][metric].add(identifier)
            if event_id:
                counts[scope]["events"].add(event_id)

    for table, key in (
        ("events", "event_id"),
        ("contests", "contest_id"),
        ("rounds", "round_id"),
        ("entries", "entry_id"),
    ):
        for row in rows.get(table, ()):
            record(row, table, str(row[key]))
    for row in rows.get("registry_placements", ()):
        if row.get("event_id"):
            record(row, "events", str(row["event_id"]))
    for table, kind, key in (("entries", "entry", "entry_id"), ("judges", "judge", "judge_id")):
        for row in rows.get(table, ()):
            identifier = str(row[key])
            record(row, "identity_subjects", identifier)
            assertion = assertions.get((kind, identifier), {})
            if row.get("wsdc_id") is not None:
                record(row, "resolved_identities", identifier)
            elif (
                assertion.get("wsdc_id") is not None
                and assertion.get("acceptance_state") != "accepted"
            ):
                record(row, "withheld_identities", identifier)
    for row in rows.get("coverage", ()):
        if row.get("scope_kind") not in {"source", "year", "event"}:
            continue
        values = counts[
            (str(row["scope_kind"]), str(row["scope_id"]), str(row["source"]), str(row["via"]))
        ]
        changed_events = row.get("events") != len(values["events"])
        for metric in (
            *_FACT_COUNTS,
            "identity_subjects",
            "resolved_identities",
            "withheld_identities",
        ):
            row[metric] = len(values[metric])
        if row["scope_kind"] != "year" or changed_events:
            scoped_events = [events[key] for key in values["events"] if key in events]
            for tier in ("registry_only", "index_only", "sheets_partial", "sheets_complete"):
                row["events_" + tier] = sum(
                    event.get("coverage_tier") == tier for event in scoped_events
                )
            row["events_day_precision"] = sum(
                event.get("date_precision") == "day" for event in scoped_events
            )
            row["events_listed_only"] = sum(event.get("held") != "held" for event in scoped_events)
            row["parsed_rounds"] = len(values["rounds"])
        row["scope_status"] = next(
            (
                status
                for status, total in (
                    (
                        "withheld",
                        sum(
                            int(row.get(field) or 0)
                            for field in (
                                "withheld_units",
                                "withheld_scopes",
                                "withheld_identities",
                            )
                        ),
                    ),
                    (
                        "unavailable",
                        sum(
                            int(row.get(field) or 0)
                            for field in ("unavailable_units", "unavailable_scopes")
                        ),
                    ),
                    ("resolved", row["resolved_identities"]),
                    ("mapped", row.get("mapped_units")),
                    ("interpreted", row.get("interpreted_units")),
                    ("acquired", row.get("acquired_units")),
                    ("discovered", row.get("discovered_units")),
                )
                if total
            ),
            "unknown",
        )
