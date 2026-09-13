"""Idempotent watch discovery and one-check scheduling after every response."""

import json
import random
import re
import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from swingset.config import Config
from swingset.fetch.classify import Outcome
from swingset.schedule.confirmation import pending_confirmation_events
from swingset.schedule.policy import policy
from swingset.sources.base import WatchSpec
from swingset.state.verification import usable_verification


def upsert_watch(
    conn: sqlite3.Connection,
    spec: WatchSpec,
    now: datetime,
    *,
    snapshot_id: str | None = None,
    parent_watch_id: str | None = None,
) -> bool:
    if not spec.archive_url:
        return _insert_watch(conn, spec, now, snapshot_id, parent_watch_id)
    from swingset.history.acquisition import archive_watch_gate

    conn.execute("SAVEPOINT history_watch_admission")
    try:
        reason = archive_watch_gate(
            conn,
            source=spec.source,
            source_ref=spec.source_ref,
            page_kind=spec.parser,
            watch_kind=spec.kind,
            archive_url=spec.archive_url,
        )
        if reason:
            raise ValueError(f"phase 2 archive watch is gated: {reason}")
        inserted = _insert_watch(conn, spec, now, snapshot_id, parent_watch_id)
        conn.execute("RELEASE history_watch_admission")
        return inserted
    except BaseException:
        conn.execute("ROLLBACK TO history_watch_admission")
        conn.execute("RELEASE history_watch_admission")
        raise


def _insert_watch(
    conn: sqlite3.Connection,
    spec: WatchSpec,
    now: datetime,
    snapshot_id: str | None,
    parent_watch_id: str | None,
) -> bool:
    cursor = conn.execute(
        """INSERT OR IGNORE INTO watches(watch_id,source,kind,method,url,form,parser,
        source_ref,archive_url,notes,state,next_check_at,created_by_snapshot_id,parent_watch_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,'live',?,?,?)""",
        (
            spec.watch_id,
            spec.source,
            spec.kind,
            spec.method,
            spec.url,
            json.dumps(dict(spec.form), sort_keys=True, separators=(",", ":"))
            if spec.form
            else None,
            spec.parser,
            spec.source_ref,
            spec.archive_url,
            spec.notes,
            now.isoformat(),
            snapshot_id,
            parent_watch_id,
        ),
    )
    inserted = cursor.rowcount > 0
    if inserted and spec.archive_url:
        from swingset.history.acquisition import PHASE1_KINDS

        conn.execute(
            "UPDATE watches SET state='backfill',priority=? WHERE watch_id=?",
            (2 if spec.kind == "index" and spec.parser in PHASE1_KINDS else 6, spec.watch_id),
        )
    if (
        parent_watch_id
        and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='scheduler_parent_links'"
        ).fetchone()
    ):
        relationship = conn.execute(
            "INSERT OR IGNORE INTO scheduler_parent_links(parent_watch_id,child_watch_id,first_snapshot_id,first_seen_at) VALUES (?,?,?,?)",
            (parent_watch_id, spec.watch_id, snapshot_id, now.isoformat()),
        )
        if relationship.rowcount and not inserted:
            changed = conn.execute(
                "UPDATE watches SET state='live',next_check_at=?,first_404_at=NULL,consecutive_404s=0 "
                "WHERE watch_id=? AND state IN ('gone','metadata')",
                (now.isoformat(), spec.watch_id),
            )
            if changed.rowcount:
                conn.execute(
                    "UPDATE scheduler_watch_state SET metadata_attempts=0,last_metadata_attempt_at=NULL WHERE watch_id=?",
                    (spec.watch_id,),
                )
    return inserted


def refresh_policy(
    conn: sqlite3.Connection,
    config: Config,
    watch_id: str,
    now: datetime,
    *,
    outcome: Outcome | None = None,
    jitter: float | None = None,
) -> None:
    row = conn.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown watch {watch_id}")
    event = conn.execute(
        """SELECT e.start_date,e.end_date FROM source_event_map m JOIN events e USING(event_id)
        WHERE m.source=? AND m.source_ref=?""",
        (row["source"], row["source_ref"]),
    ).fetchone()
    current_state = str(row["state"])
    first_404 = row["first_404_at"]
    misses = int(row["consecutive_404s"])
    if outcome == Outcome.GONE:
        first_404 = first_404 or now.isoformat()
        misses += 1
        if misses >= 3 and now - datetime.fromisoformat(first_404) >= timedelta(days=2):
            current_state = "gone"
        conn.execute(
            "UPDATE watches SET first_404_at=?,consecutive_404s=? WHERE watch_id=?",
            (first_404, misses, watch_id),
        )
    elif outcome in (Outcome.OK, Outcome.NOT_MODIFIED):
        if current_state == "gone":
            current_state = "live"
        conn.execute(
            "UPDATE watches SET first_404_at=NULL,consecutive_404s=0 WHERE watch_id=?", (watch_id,)
        )
    result = policy(
        source=str(row["source"]),
        kind=str(row["kind"]),
        host=config.host(urlsplit(str(row["url"])).hostname or ""),
        now=now,
        start=date.fromisoformat(event[0]) if event and event[0] else None,
        end=date.fromisoformat(event[1]) if event and event[1] else None,
        state=current_state,
        unchanged_streak=int(row["unchanged_streak"]),
        jitter=random.uniform(0, 300) if jitter is None else jitter,
    )
    interval = result.interval
    state = result.state
    priority = result.priority
    if row["source"] == "wsdc_registry" and str(row["notes"] or "").startswith("confirmation:"):
        wsdc_id = int(str(row["source_ref"]).removeprefix("wsdc:"))
        if pending_confirmation_events(conn, wsdc_id, now):
            state, interval = "registry", 86400
        else:
            state, interval = "archived", 365 * 86400
    if outcome == Outcome.EXPECTED_UNAVAILABLE:
        count = conn.execute(
            "SELECT COUNT(DISTINCT substr(fetched_at,1,10)),MIN(fetched_at) FROM snapshots "
            "WHERE watch_id=? AND classification='ExpectedUnavailable'",
            (watch_id,),
        ).fetchone()
        if count[0] >= 30 and now - datetime.fromisoformat(count[1]) >= timedelta(days=29):
            state, interval = "gone", config.scheduler.unavailable_recheck_seconds
        else:
            state, interval = "upcoming", 86400
    elif outcome == Outcome.GONE and state != "gone":
        interval = 86400
    elif outcome in (Outcome.INVALID, Outcome.SERVER_ERROR):
        interval = 900
    dateless = (
        not row["archive_url"]
        and row["source"] not in {"wsdc_registry", "wsdc_calendar"}
        and row["kind"] != "index"
        and (event is None or not event[0] or not event[1])
        and current_state != "sealed"
    )
    if (
        dateless
        and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='scheduler_watch_state'"
        ).fetchone()
    ):
        conn.execute(
            "INSERT OR IGNORE INTO scheduler_watch_state(watch_id) VALUES (?)", (watch_id,)
        )
        if outcome is not None and outcome not in {
            Outcome.BLOCKED,
            Outcome.THROTTLED,
            Outcome.SERVER_ERROR,
        }:
            conn.execute(
                "UPDATE scheduler_watch_state SET metadata_attempts=metadata_attempts+1,last_metadata_attempt_at=? "
                "WHERE watch_id=? AND (last_metadata_attempt_at IS NULL OR last_metadata_attempt_at!=?)",
                (now.isoformat(), watch_id, now.isoformat()),
            )
        attempts = conn.execute(
            "SELECT metadata_attempts FROM scheduler_watch_state WHERE watch_id=?", (watch_id,)
        ).fetchone()[0]
        if state != "gone" and outcome != Outcome.EXPECTED_UNAVAILABLE:
            state = "metadata"
            interval = (
                86400
                if attempts < config.scheduler.metadata_recovery_attempts
                else config.scheduler.metadata_recheck_seconds
            )
            priority = 3 if attempts < config.scheduler.metadata_recovery_attempts else 4
    if state == "gone":
        interval, priority = config.scheduler.unavailable_recheck_seconds, 99
    if row["source"] == "wsdc_registry" and outcome is not None:
        check = usable_verification(conn, watch_id)
        if check is None or datetime.fromisoformat(check["checked_at"]) < now:
            # A transport success without an accepted interpretation is still retryable.
            # A pending parse may complete later; it must not strand this watch for a year.
            interval = min(interval or 900, 900)
    due = (now + timedelta(seconds=interval)).isoformat() if interval is not None else None
    if (
        outcome is None
        and state == current_state
        and state in {"metadata", "gone"}
        and row["next_check_at"] is not None
    ):
        due = row["next_check_at"]
    if state == "dormant" and event:
        from datetime import UTC

        due = (
            datetime.combine(date.fromisoformat(event[0]), datetime.min.time(), UTC)
            - timedelta(days=14)
        ).isoformat()
    conn.execute(
        "UPDATE watches SET state=?,next_check_at=?,priority=? WHERE watch_id=?",
        (state, due, priority, watch_id),
    )


def due_watches(conn: sqlite3.Connection, config: Config, now: datetime) -> list[str]:
    rows = conn.execute("""SELECT watch_id,source,url,state,kind,next_check_at,priority,
        source_ref,notes FROM watches
        WHERE state NOT IN ('sealed','retired') AND next_check_at IS NOT NULL ORDER BY priority,next_check_at,
        CASE kind WHEN 'round' THEN 1 ELSE 0 END,watch_id""").fetchall()
    due = [
        row
        for row in rows
        if config.enabled(str(row["source"]))
        and datetime.fromisoformat(row["next_check_at"]) <= now
    ]
    groups: dict[tuple[object, ...], list[int]] = {}
    for index, row in enumerate(due):
        if _registry_sequence(row) is None:
            continue
        key = (
            row["priority"],
            row["next_check_at"],
            1 if row["kind"] == "round" else 0,
        )
        groups.setdefault(key, []).append(index)
    for indexes in groups.values():
        ordered = sorted(
            (due[index] for index in indexes), key=lambda row: _registry_sequence(row) or 0
        )
        for index, row in zip(indexes, ordered, strict=True):
            due[index] = row
    return [str(row["watch_id"]) for row in due]


def _registry_sequence(row: sqlite3.Row) -> int | None:
    if row["source"] != "wsdc_registry" or row["notes"] not in {"sweep", "probe"}:
        return None
    match = re.fullmatch(r"wsdc:(\d+)", str(row["source_ref"]))
    return int(match.group(1)) if match else None
