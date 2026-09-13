"""Registry content checks joined to accepted interpretations, without moving claims."""

from __future__ import annotations

import sqlite3
import zlib
from datetime import datetime
from typing import cast

from swingset.fetch.archive import Archive
from swingset.model.observations import decode_payload
from swingset.sources import get_page_kind
from swingset.state.db import Database


def _interpretation(
    conn: sqlite3.Connection, archive: Archive, snapshot_id: str, body_sha256: str | None
) -> tuple[str | None, str, str | None, str | None]:
    row = conn.execute(
        "SELECT s.*,w.parser FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    if row is None or body_sha256 is None:
        return None, "missing_content_reference", None, None
    extract_version, parser_version = row["extract_version"], row["parser_version"]
    try:
        archive.read_body(body_sha256)
        if row["extract_sha256"]:
            archive.read_extract(str(row["extract_sha256"]))
    except FileNotFoundError:
        return None, "missing_artifact", extract_version, parser_version
    except (EOFError, OSError, ValueError, zlib.error):
        return None, "corrupt_artifact", extract_version, parser_version
    if body_sha256 != row["body_sha256"]:
        return None, "content_mismatch", extract_version, parser_version
    if row["parse_status"] == "failed":
        return None, "interpretation_failed", extract_version, parser_version
    if row["parse_status"] not in {"ok", "parsed"}:
        return None, "interpretation_pending", extract_version, parser_version
    page = get_page_kind(str(row["parser"]))
    if (str(extract_version), str(parser_version)) != (
        str(page.EXTRACT_VERSION),
        str(page.PARSER_VERSION),
    ):
        return None, "interpretation_stale", extract_version, parser_version
    for observation in conn.execute(
        "SELECT kind,payload_json FROM observations WHERE watch_id=? AND snapshot_id=?",
        (row["watch_id"], snapshot_id),
    ):
        payload = decode_payload(str(observation[0]), str(observation[1]))
        outcome = getattr(payload, "outcome", None)
        if outcome in {"found", "not_found"}:
            return str(outcome), "verified", extract_version, parser_version
    return None, "interpretation_missing", extract_version, parser_version


def record_check(
    conn: sqlite3.Connection,
    archive: Archive,
    *,
    watch_id: str,
    checked_at: str,
    http_status: int | None,
    body_sha256: str | None,
    successful: bool,
    snapshot_id: str | None = None,
    failure_reason: str = "content_check_failed",
) -> int:
    """Call in the fetch transaction; pending parses complete the same records."""
    if snapshot_id is None and body_sha256 is not None:
        prior = conn.execute(
            "SELECT snapshot_id FROM snapshots WHERE watch_id=? AND body_sha256=? "
            "AND classification IN ('Ok','NotModified') ORDER BY fetched_at DESC LIMIT 1",
            (watch_id, body_sha256),
        ).fetchone()
        snapshot_id = str(prior[0]) if prior else None
    outcome, reason, extract_version, parser_version = None, failure_reason, None, None
    if successful:
        if snapshot_id is None:
            reason = "missing_content_reference"
        else:
            outcome, reason, extract_version, parser_version = _interpretation(
                conn, archive, snapshot_id, body_sha256
            )
    cursor = conn.execute(
        "INSERT INTO registry_verifications(watch_id,checked_at,http_status,body_sha256,"
        "snapshot_id,extract_version,parser_version,outcome,usable,reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            watch_id,
            checked_at,
            http_status,
            body_sha256,
            snapshot_id,
            extract_version,
            parser_version,
            outcome,
            int(outcome is not None),
            reason,
        ),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def finish_interpretation(conn: sqlite3.Connection, archive: Archive, snapshot_id: str) -> None:
    """Use the original successful check time even when parsing happens later."""
    snapshot = conn.execute(
        "SELECT watch_id FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
    ).fetchone()
    if snapshot is None:
        return
    for row in conn.execute(
        "SELECT verification_id,body_sha256 FROM registry_verifications "
        "WHERE watch_id=? AND snapshot_id=? AND reason IN ('interpretation_pending','interpretation_stale',"
        "'interpretation_failed','interpretation_missing','verified')",
        (snapshot["watch_id"], snapshot_id),
    ).fetchall():
        outcome, reason, extract_version, parser_version = _interpretation(
            conn, archive, snapshot_id, row["body_sha256"]
        )
        conn.execute(
            "UPDATE registry_verifications SET outcome=?,usable=?,reason=?,extract_version=?,"
            "parser_version=? WHERE verification_id=?",
            (outcome, int(outcome is not None), reason, extract_version, parser_version, row[0]),
        )


def usable_verification(conn: sqlite3.Connection, watch_id: str) -> sqlite3.Row | None:
    """Current interpreter labels are a compatibility gate until H15 recipes."""
    watch = conn.execute("SELECT parser FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
    if watch is None:
        return None
    page = get_page_kind(str(watch[0]))
    return cast(
        sqlite3.Row | None,
        conn.execute(
            "SELECT * FROM registry_verifications WHERE watch_id=? AND usable=1 "
            "AND extract_version=? AND parser_version=? ORDER BY julianday(checked_at) DESC,"
            "verification_id DESC LIMIT 1",
            (watch_id, str(page.EXTRACT_VERSION), str(page.PARSER_VERSION)),
        ).fetchone(),
    )


def verification_summary(
    conn: sqlite3.Connection, now: datetime, *, stale_after_seconds: float = 365 * 86400
) -> dict[str, object]:
    """A fake-clock-friendly age and alert over the known registry watch universe."""
    ages = []
    unknown = 0
    for watch in conn.execute("SELECT watch_id FROM watches WHERE source='wsdc_registry'"):
        check = usable_verification(conn, str(watch[0]))
        if check is None:
            unknown += 1
        else:
            ages.append(
                max(0.0, (now - datetime.fromisoformat(check["checked_at"])).total_seconds())
            )
    oldest = max(ages) if ages else None
    return {
        "oldest_usable_verification_age_seconds": oldest,
        "unknown_watches": unknown,
        "stale_watches": sum(age > stale_after_seconds for age in ages),
        "alert": unknown > 0 or (oldest is not None and oldest > stale_after_seconds),
    }


def recover_registry_verifications(database: Database) -> None:
    """Recover only witnessed checks; neither last_checked_at nor now is evidence."""
    conn = database.connection
    if conn.execute("SELECT 1 FROM meta WHERE key='registry_verification_migrated'").fetchone():
        return
    archive = Archive(database.state_dir)
    with database.transaction():
        for row in conn.execute(
            "SELECT s.* FROM snapshots s JOIN watches w USING(watch_id) "
            "WHERE w.source='wsdc_registry' AND s.parse_status IN ('ok','parsed') "
            "AND s.classification IN ('Ok','NotModified') ORDER BY s.fetched_at"
        ).fetchall():
            record_check(
                conn,
                archive,
                watch_id=str(row["watch_id"]),
                checked_at=str(row["fetched_at"]),
                http_status=row["http_status"],
                body_sha256=row["body_sha256"],
                successful=True,
                snapshot_id=str(row["snapshot_id"]),
            )
        conn.execute("INSERT INTO meta(key,value) VALUES ('registry_verification_migrated','1')")
