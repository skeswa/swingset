from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import pyarrow as pa

from swingset.build.builder import PUBLISHED_TABLES, BuildInput
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
from swingset.model.schema import TABLES


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _convert(value: Any, data_type: pa.DataType) -> Any:
    if value is None:
        return None
    if pa.types.is_list(data_type) and isinstance(value, str):
        return json.loads(value)
    if pa.types.is_timestamp(data_type) and isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed
    if pa.types.is_date(data_type) and isinstance(value, str):
        return date.fromisoformat(value)
    if pa.types.is_boolean(data_type) and isinstance(value, int):
        return bool(value)
    return value


def _read_table(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    schema = SCHEMAS[table]
    available = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
    missing = set(schema.names) - available
    if missing:
        raise RuntimeError(f"SQLite table {table} is missing public columns: {sorted(missing)}")
    columns = [field.name for field in schema if field.name in available]
    if not columns:
        return []
    quoted = ", ".join(f'"{name}"' for name in columns)
    result: list[dict[str, Any]] = []
    for values in connection.execute(f'SELECT {quoted} FROM "{table}"'):
        row = dict(zip(columns, values, strict=True))
        result.append({field.name: _convert(row.get(field.name), field.type) for field in schema})
    return result


def _read_snapshots(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    schema = SCHEMAS["snapshots"]
    query = """
        SELECT s.snapshot_id, w.source, s.url, s.fetched_at, s.http_status,
               s.body_sha256, s.body_bytes, s.content_changed, w.parser,
               s.parser_version, s.parse_status
        FROM snapshots AS s JOIN watches AS w ON w.watch_id = s.watch_id
    """
    result: list[dict[str, Any]] = []
    for values in connection.execute(query):
        row = dict(zip(schema.names, values, strict=True))
        result.append({field.name: _convert(row[field.name], field.type) for field in schema})
    return result


def _review_id(kind: str, subject: str, evidence: str) -> str:
    return "review_" + hashlib.sha256(f"{kind}|{subject}|{evidence}".encode()).hexdigest()[:16]


def _review_queue(connection: sqlite3.Connection, existing: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if "findings" in existing:
        for row in connection.execute(
            "SELECT finding_id, kind, subject_id, summary, suggested_override, opened_at, run_id "
            "FROM findings WHERE closed_at IS NULL"
        ):
            items.append(
                {
                    "item_id": str(row[0]),
                    "kind": str(row[1]),
                    "subject_id": str(row[2]),
                    "summary": str(row[3]),
                    "suggested_override": row[4],
                    "opened_at": _convert(row[5], SCHEMAS["review_queue"].field("opened_at").type),
                    "run_id": str(row[6]),
                }
            )
    if "identity_links" in existing:
        for row in connection.execute(
            "SELECT subject_id, status, run_id, asserted_at FROM identity_links WHERE status='ambiguous'"
        ):
            subject = str(row[0])
            items.append(
                {
                    "item_id": _review_id("ambiguous_link", subject, str(row[2])),
                    "kind": "ambiguous_link",
                    "subject_id": subject,
                    "summary": "Identity link has multiple plausible registry matches",
                    "suggested_override": None,
                    "opened_at": _convert(row[3], SCHEMAS["review_queue"].field("opened_at").type),
                    "run_id": str(row[2]),
                }
            )
    if "contests" in existing:
        for row in connection.execute(
            "SELECT contest_id, run_id, first_seen_at FROM contests WHERE parse_status='unsupported'"
        ):
            subject = str(row[0])
            items.append(
                {
                    "item_id": _review_id("unsupported_contest", subject, str(row[1])),
                    "kind": "unsupported_contest",
                    "subject_id": subject,
                    "summary": "Contest format is not supported",
                    "suggested_override": None,
                    "opened_at": _convert(row[2], SCHEMAS["review_queue"].field("opened_at").type),
                    "run_id": str(row[1]),
                }
            )
    if {"source_events", "source_event_map"} <= existing:
        for row in connection.execute(
            "SELECT e.source, e.source_ref, e.run_id, e.first_seen_at FROM source_events e "
            "LEFT JOIN source_event_map m ON m.source=e.source AND m.source_ref=e.source_ref "
            "WHERE m.event_id IS NULL"
        ):
            subject = f"{row[0]}:{row[1]}"
            items.append(
                {
                    "item_id": _review_id("event_alias", subject, str(row[2])),
                    "kind": "event_alias",
                    "subject_id": subject,
                    "summary": "Source event is not matched to a calendar event",
                    "suggested_override": None,
                    "opened_at": _convert(row[3], SCHEMAS["review_queue"].field("opened_at").type),
                    "run_id": str(row[2]),
                }
            )
    return items


def read_build_input(connection: sqlite3.Connection, bundle: Any) -> BuildInput:
    """Capture every published DB dependency from the caller's read transaction."""
    existing = _tables(connection)
    pending = (
        connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()
        if "pending_work" in existing
        else (0,)
    )
    if pending is not None and int(pending[0]):
        from swingset.build.builder import BuildError

        raise BuildError("build is blocked by pending parse, project, or link work")
    rows = {
        name: (
            _read_snapshots(connection)
            if name == "snapshots" and "watches" in existing
            else _read_table(connection, name)
        )
        if name in existing
        else []
        for name in PUBLISHED_TABLES
    }
    rows["review_queue"] = _review_queue(connection, existing)
    revisions = (
        {
            str(name): int(value)
            for name, value in connection.execute("SELECT name, value FROM revisions")
        }
        if "revisions" in existing
        else {}
    )
    # The state schema owns persistent primary keys; assert the public mapping has not drifted.
    for name, table in TABLES.items():
        if name in PRIMARY_KEYS and PRIMARY_KEYS[name] != table.primary_key:
            raise RuntimeError(f"public primary key drift for {name}")
    hashes_value = getattr(bundle, "file_hashes", None)
    if hashes_value is None and isinstance(bundle, Mapping):
        hashes_value = bundle.get("file_hashes", {})
    if not isinstance(hashes_value, Mapping):
        raise TypeError("bundle file_hashes must be a mapping")
    return BuildInput(
        tables=rows,
        schemas=SCHEMAS,
        primary_keys=PRIMARY_KEYS,
        revisions=revisions,
        captured_file_hashes={str(k): str(v) for k, v in hashes_value.items()},
        input_bundle_hash=str(
            getattr(bundle, "digest", "")
            if not isinstance(bundle, Mapping)
            else bundle.get("hash", "")
        ),
    )
