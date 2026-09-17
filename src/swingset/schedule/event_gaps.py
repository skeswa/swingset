"""Sample unavailable and unsupported gaps without creating successful stage progress."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from swingset.admission.page_evidence import Session
from swingset.admission.unavailable_evidence import metadata_valid
from swingset.admission.unsupported_evidence import metadata_valid as unsupported_valid
from swingset.fetch.archive import canonical, digest

FORMAT = "event-page-gaps-v2"


def accounted(
    interpreted: bool | None, unavailable: bool | None, unsupported: bool | None = False
) -> bool | None:
    """Verified interpretation or an explicit proved gap accounts for one obligation."""
    if any(
        value is not None and type(value) is not bool
        for value in (interpreted, unavailable, unsupported)
    ):
        raise ValueError("page accounting requires tri-state booleans")
    if interpreted is True or unavailable is True or unsupported is True:
        return True
    return False if interpreted is False and unavailable is False and unsupported is False else None


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='event_gap_observations'").fetchone()
        is not None
    )


def revision(session: Session, source: str, cache: dict[str, int | None]) -> int | None:
    """Capture a source domain once per caller-owned read snapshot."""
    if source not in cache:
        if not available(session.conn):
            cache[source] = None
        else:
            rows = session.read(
                "SELECT revision FROM event_gap_revisions WHERE source=?",
                (source,),
                ("revision",),
                cap=1,
            )
            cache[source] = rows[0]["revision"] if rows else 0
    return cache[source]


def unchanged(conn: sqlite3.Connection, batch: dict[str, Any]) -> bool:
    if not available(conn) or batch.get("gap_revision") is None:
        return False
    row = conn.execute(
        "SELECT revision FROM event_gap_revisions WHERE source=?", (batch["source"],)
    ).fetchone()
    return bool(batch["gap_revision"] == (row[0] if row else 0))


def observation(evidence: dict[str, Any], source_revision: int | None) -> dict[str, Any]:
    value = {
        key: evidence[key]
        for key in (
            "request_id",
            "request",
            "cutoff",
            "acquired",
            "interpreted",
            "unavailable",
            "unavailability_support",
            "unsupported",
            "unsupported_support",
        )
    }
    value["format"] = FORMAT
    return dict(
        availability=value["unavailable"],
        source_revision=source_revision,
        evidence_json=canonical(value).decode(),
        evidence_digest=digest(canonical(value)),
    )


def load(session: Session, batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if batch.get("gap_revision") is None:
        return {}
    columns = (
        "request_id",
        "enumeration_id",
        "observed_at",
        "valid_until",
        "availability",
        "token_json",
        "source_revision",
        "evidence_json",
        "evidence_digest",
    )
    rows = session.read(
        "SELECT " + ",".join(columns) + " FROM event_gap_observations "
        "WHERE source=? AND source_ref=? AND enumeration_id=?",
        (batch["source"], batch["source_ref"], batch["enumeration_id"]),
        columns,
        cap=128,
    )
    return {row["request_id"]: row for row in rows}


def value(
    row: dict[str, Any],
    request: dict[str, Any],
    source_revision: int | None,
    *,
    classification: str = "unavailable",
) -> bool | None:
    """Validate retained metadata; caller separately checks fence and bounded TTL."""
    try:
        evidence = json.loads(row["evidence_json"])
        availability = row["availability"]
        if (
            type(row["source_revision"]) is not int
            or row["source_revision"] != source_revision
            or not isinstance(evidence, dict)
            or evidence.keys()
            != {
                "format",
                "request_id",
                "request",
                "cutoff",
                "acquired",
                "interpreted",
                "unavailable",
                "unavailability_support",
                "unsupported",
                "unsupported_support",
            }
            or evidence["format"] != FORMAT
            or evidence["request"] != request
            or evidence["request_id"] != row["request_id"]
            or evidence["cutoff"] != row["observed_at"]
            or digest(canonical(evidence)) != row["evidence_digest"]
            or (availability is not None and type(availability) not in (int, bool))
            or availability not in (None, 0, 1)
            or evidence["unavailable"] is not (None if availability is None else bool(availability))
            or not metadata_valid(evidence)
            or not unsupported_valid(evidence)
        ):
            return None
        result: bool | None = evidence[classification]
        return result
    except (ValueError, KeyError, TypeError, RecursionError):
        return None


def persist(
    conn: sqlite3.Connection, batch: dict[str, Any], *, now: datetime, max_age: float
) -> None:
    """Use the observer transaction after both ordinary and gap fences were checked."""
    if not unchanged(conn, batch):
        return
    conn.execute(
        "DELETE FROM event_gap_observations WHERE source=? AND source_ref=? AND enumeration_id IS NOT ?",
        (batch["source"], batch["source_ref"], batch["enumeration_id"]),
    )
    for page in batch["pages"]:
        gap = page.get("gap")
        if gap is None:
            continue
        conn.execute(
            "INSERT INTO event_gap_observations VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source,source_ref,request_id) DO UPDATE SET "
            "enumeration_id=excluded.enumeration_id,observed_at=excluded.observed_at,"
            "valid_until=excluded.valid_until,availability=excluded.availability,token_json=excluded.token_json,"
            "source_revision=excluded.source_revision,evidence_json=excluded.evidence_json,evidence_digest=excluded.evidence_digest",
            (
                batch["source"],
                batch["source_ref"],
                page["request_id"],
                batch["enumeration_id"],
                now.isoformat(),
                (now + timedelta(seconds=max_age)).isoformat(),
                gap["availability"],
                canonical(batch["token"]).decode(),
                batch["gap_revision"],
                gap["evidence_json"],
                gap["evidence_digest"],
            ),
        )
