"""Transactional replacement of canonical projection scopes."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from swingset.model.canonical import CanonicalRecord
from swingset.model.schema import RECORD_TABLE, TABLES
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
    conn: sqlite3.Connection,
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
        "SELECT table_name,record_key FROM canonical_scope_rows WHERE scope_kind=? AND scope_id=?",
        (scope_kind, scope_id),
    ):
        existing.setdefault(str(row[0]), set()).add(str(row[1]))
    changed = False
    for table in _DELETE_ORDER:
        removed = existing.get(table, set()) - set(desired.get(table, {}))
        schema = TABLES[table]
        for key_json in removed:
            key = json.loads(key_json)
            where = " AND ".join(f"{column}=?" for column in schema.primary_key)
            conn.execute(
                "DELETE FROM canonical_scope_rows WHERE scope_kind=? AND scope_id=? AND table_name=? AND record_key=?",
                (scope_kind, scope_id, table, key_json),
            )
            if (
                conn.execute(
                    "SELECT 1 FROM canonical_scope_rows WHERE table_name=? AND record_key=? LIMIT 1",
                    (table, key_json),
                ).fetchone()
                is not None
            ):
                continue
            if table in {"entries", "judges"}:
                subject_kind = "entry" if table == "entries" else "judge"
                removed_links = conn.execute(
                    "DELETE FROM identity_links WHERE subject_kind=? AND subject_id=?",
                    (subject_kind, key[0]),
                ).rowcount
                removed_candidates = conn.execute(
                    "DELETE FROM link_candidates WHERE subject_kind=? AND subject_id=?",
                    (subject_kind, key[0]),
                ).rowcount
                if removed_links or removed_candidates:
                    bump_revision(conn, "links")
            conn.execute(f"DELETE FROM {table} WHERE {where}", tuple(key))
            changed = True
    for table in _INSERT_ORDER:
        for key_json, record in desired.get(table, {}).items():
            changed |= _upsert(conn, table, record)
            conn.execute(
                "INSERT OR IGNORE INTO canonical_scope_rows(scope_kind,scope_id,table_name,record_key) VALUES (?,?,?,?)",
                (scope_kind, scope_id, table, key_json),
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
    conn: sqlite3.Connection, rows: tuple[tuple[str, str, str, str, float], ...]
) -> tuple[str, ...]:
    """Replace the matching map and return affected old and new event ids."""
    old = {
        (str(row[0]), str(row[1])): str(row[2])
        for row in conn.execute("SELECT source,source_ref,event_id FROM source_event_map")
    }
    new = {(source, source_ref): event for source, source_ref, event, _method, _confidence in rows}
    affected = sorted(
        {event for key, event in old.items() if new.get(key) != event}
        | {event for key, event in new.items() if old.get(key) != event}
    )
    current_full = [
        tuple(row)
        for row in conn.execute(
            "SELECT source,source_ref,event_id,match_method,match_confidence FROM source_event_map ORDER BY source,source_ref"
        )
    ]
    desired_full = sorted(rows)
    if current_full != desired_full:
        conn.execute("DELETE FROM source_event_map")
        conn.executemany(
            "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) VALUES (?,?,?,?,?)",
            desired_full,
        )
        bump_revision(conn, "source_event_map")
    return tuple(affected)


def _upsert(conn: sqlite3.Connection, table: str, record: CanonicalRecord) -> bool:
    values = {
        field.name: _sql_value(getattr(record, field.name)) for field in dataclasses.fields(record)
    }
    schema = TABLES[table]
    where = " AND ".join(f"{column}=?" for column in schema.primary_key)
    key_values = tuple(values[column] for column in schema.primary_key)
    old = conn.execute(f"SELECT * FROM {table} WHERE {where}", key_values).fetchone()
    if old is not None:
        values["first_seen_at"] = old["first_seen_at"]
        for column in schema.owner_columns:
            values[column] = old[column]
        semantic_columns = set(values) - {"first_seen_at", "last_seen_at", "run_id"}
        if all(old[column] == values[column] for column in semantic_columns):
            return False
    columns = tuple(values)
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f"{column}=excluded.{column}" for column in columns if column not in schema.primary_key
    )
    conn.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT ({','.join(schema.primary_key)}) DO UPDATE SET {updates}",
        tuple(values[column] for column in columns),
    )
    return True


def _sql_value(value: Any) -> Any:
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def _key_json(key: tuple[object, ...]) -> str:
    return json.dumps(key, separators=(",", ":"), ensure_ascii=False)
