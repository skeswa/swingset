"""Transactional replacement of canonical projection scopes."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from itertools import batched
from typing import Any

from swingset.model.canonical import CanonicalRecord
from swingset.model.schema import RECORD_TABLE, TABLES
from swingset.state import engine
from swingset.state.findings import Finding, replace_findings
from swingset.state.work import WorkUnit, bump_revision, enqueue


@dataclass(frozen=True, slots=True)
class Projection:
    rows: tuple[CanonicalRecord, ...] = ()
    findings: tuple[Finding, ...] = ()


_INSERT_ORDER = (
    "events",
    "contests",
    "rounds",
    "entries",
    "judges",
    "heats",
    "callback_marks",
    "callbacks",
    "placements",
    "final_marks",
    "dancers",
    "registry_placements",
)
_DELETE_ORDER = tuple(reversed(_INSERT_ORDER))


def replace_scope(
    conn: engine.Connection,
    *,
    scope_kind: str,
    scope_id: str,
    projection: Projection,
    run_id: str,
    projected_at: str,
    enqueue_links: bool = True,
) -> bool:
    """Replace all rows owned by one canonical scope in the caller transaction."""
    desired: dict[str, dict[str, CanonicalRecord]] = {}
    for record in projection.rows:
        table = RECORD_TABLE[type(record).__name__.casefold()]
        key_json = _key_json(record.key())
        desired.setdefault(table, {})[key_json] = record
    existing: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT table_name, record_key FROM canonical_scope_rows WHERE scope_kind = %s AND scope_id = %s",
        (scope_kind, scope_id),
    ):
        existing.setdefault(str(row[0]), set()).add(str(row[1]))
    changed = False
    for table in _DELETE_ORDER:
        removed = existing.get(table, set()) - set(desired.get(table, {}))
        schema = TABLES[table]
        for key_json in removed:
            key = json.loads(key_json)
            where = " AND ".join(f"{column}=%s" for column in schema.primary_key)
            conn.execute(
                (
                    "DELETE FROM canonical_scope_rows WHERE scope_kind = %s AND scope_id = %s AND table_name = "
                    "%s AND record_key = %s"
                ),
                (scope_kind, scope_id, table, key_json),
            )
            if (
                conn.execute(
                    "SELECT 1 AS _value_0 FROM canonical_scope_rows WHERE table_name = %s AND record_key = %s LIMIT 1",
                    (table, key_json),
                ).fetchone()
                is not None
            ):
                continue
            if table in {"entries", "judges"}:
                subject_kind = "entry" if table == "entries" else "judge"
                removed_links = conn.execute(
                    "DELETE FROM identity_links WHERE subject_kind = %s AND subject_id = %s",
                    (subject_kind, key[0]),
                ).rowcount
                removed_candidates = conn.execute(
                    "DELETE FROM link_candidates WHERE subject_kind = %s AND subject_id = %s",
                    (subject_kind, key[0]),
                ).rowcount
                if removed_links or removed_candidates:
                    bump_revision(conn, "links")
            if table == "events":
                # A registry placement points at its event without a cascade.
                # When the live calendar drops a past event that the registry
                # has already reported, detach the placement; the inventory
                # projection that follows creates the registry-backed event
                # and links the placement to it again (D-0191).
                conn.execute(
                    "UPDATE registry_placements SET event_id = NULL WHERE event_id = %s", (key[0],)
                )
            conn.execute(f"DELETE FROM {table} WHERE {where}", tuple(key))
            changed = True
    for table in _INSERT_ORDER:
        records = desired.get(table, {})
        for group in batched(records.values(), 1024):
            changed |= _upsert_batch(conn, table, group)
        for keys in batched(records, 1024):
            conn.execute(
                (
                    "INSERT INTO canonical_scope_rows (scope_kind, scope_id, table_name, record_key) "
                    "SELECT %s, %s, %s, record_key FROM unnest(%s::text[]) AS keys(record_key) "
                    "ON CONFLICT DO NOTHING"
                ),
                (scope_kind, scope_id, table, list(keys)),
            )
    finding_change = replace_findings(
        conn,
        owner_kind="projection",
        owner_id=f"{scope_kind}:{scope_id}",
        findings=projection.findings,
        opened_at=projected_at,
        run_id=run_id,
    )
    if changed:
        revision = "dancers" if scope_kind == "dancer" else "canonical"
        bump_revision(conn, revision)
        if enqueue_links:
            if scope_kind == "event":
                enqueue(conn, (WorkUnit("link", "event", scope_id),), enqueued_at=projected_at)
            elif scope_kind == "dancer":
                events = (
                    WorkUnit("link", "event", str(row[0]))
                    for row in conn.execute("SELECT event_id FROM events")
                )
                enqueue(conn, events, enqueued_at=projected_at)
    return changed or finding_change


def replace_source_event_map(
    conn: engine.Connection, rows: tuple[tuple[str, str, str, str, float], ...]
) -> tuple[str, ...]:
    """Replace the matching map and return affected old and new event ids."""
    old = {
        (str(row[0]), str(row[1])): str(row[2])
        for row in conn.execute("SELECT source, source_ref, event_id FROM source_event_map")
    }
    new = {(source, source_ref): event for source, source_ref, event, _method, _confidence in rows}
    affected = sorted(
        {event for key, event in old.items() if new.get(key) != event}
        | {event for key, event in new.items() if old.get(key) != event}
    )
    current_full = [
        tuple(row)
        for row in conn.execute(
            "SELECT source, source_ref, event_id, match_method, match_confidence FROM source_event_map "
            "ORDER BY source NULLS FIRST, source_ref NULLS FIRST"
        )
    ]
    desired_full = sorted(rows)
    if current_full != desired_full:
        conn.execute("DELETE FROM source_event_map")
        conn.executemany(
            (
                "INSERT INTO source_event_map (source, source_ref, event_id, match_method, match_confidence) "
                "VALUES (%s, %s, %s, %s, %s)"
            ),
            desired_full,
        )
        bump_revision(conn, "source_event_map")
    return tuple(affected)


def _upsert_batch(
    conn: engine.Connection, table: str, records: tuple[CanonicalRecord, ...]
) -> bool:
    """Read and write bounded groups, preserving Python's semantic comparison.

    Canonical insert/update triggers affect bookkeeping, not another desired
    canonical row. Projection records are already materialized and deduplicated.
    """
    keys = TABLES[table].primary_key
    placeholders = "(" + ",".join("%s" for _ in keys) + ")"
    old_rows = {
        tuple(row[key] for key in keys): row
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE ({','.join(keys)}) IN "
            f"({','.join(placeholders for _ in records)})",
            tuple(value for record in records for value in record.key()),
        )
    }
    prepared = []
    for record in records:
        values = _changed_values(table, record, old_rows.get(record.key()))
        if values is not None:
            prepared.append(values)
    if not prepared:
        return False
    columns = tuple(prepared[0])
    placeholders = "(" + ",".join("%s" for _ in columns) + ")"
    updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in keys)
    conn.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES "
        f"{','.join(placeholders for _ in prepared)} "
        f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET {updates}",
        tuple(row[column] for row in prepared for column in columns),
    )
    return True


def _changed_values(
    table: str, record: CanonicalRecord, old: engine.Row | None
) -> dict[str, Any] | None:
    values = {
        field.name: _sql_value(getattr(record, field.name)) for field in dataclasses.fields(record)
    }
    schema = TABLES[table]
    if old is not None:
        values["first_seen_at"] = old["first_seen_at"]
        for column in schema.owner_columns:
            values[column] = old[column]
        semantic_columns = set(values) - {"first_seen_at", "last_seen_at", "run_id"}
        if all(old[column] == values[column] for column in semantic_columns):
            return None
    return values


def _sql_value(value: Any) -> Any:
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def _key_json(key: tuple[object, ...]) -> str:
    return json.dumps(key, separators=(",", ":"), ensure_ascii=False)
