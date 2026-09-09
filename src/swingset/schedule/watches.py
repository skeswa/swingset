"""Idempotent watch discovery and one-check scheduling after every response."""

import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from swingset.config import Config
from swingset.fetch.classify import Outcome
from swingset.schedule.policy import policy
from swingset.sources.base import WatchSpec


def upsert_watch(
    conn: sqlite3.Connection,
    spec: WatchSpec,
    now: datetime,
    *,
    snapshot_id: str | None = None,
    parent_watch_id: str | None = None,
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
    return cursor.rowcount > 0


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
        conn.execute(
            "UPDATE watches SET first_404_at=NULL,consecutive_404s=0 WHERE watch_id=?", (watch_id,)
        )
    result = policy(
        source=str(row["source"]),
        kind=str(row["kind"]),
        host=config.host(urlsplit(str(row["url"])).hostname or ""),
        now=now,
        start=date.fromisoformat(event[0]) if event else None,
        end=date.fromisoformat(event[1]) if event else None,
        state=current_state,
        unchanged_streak=int(row["unchanged_streak"]),
        jitter=random.uniform(0, 300) if jitter is None else jitter,
    )
    interval = result.interval
    state = result.state
    if row["source"] == "wsdc_registry" and str(row["notes"] or "").startswith("confirmation:"):
        event_id = str(row["notes"]).split(":", 1)[1]
        confirmation_event = conn.execute(
            "SELECT end_date FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if confirmation_event and now.date() <= date.fromisoformat(
            str(confirmation_event[0])
        ) + timedelta(days=30):
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
            state, interval = "gone", None
        else:
            state, interval = "upcoming", 86400
    elif outcome == Outcome.GONE and state != "gone":
        interval = 86400
    elif outcome in (Outcome.INVALID, Outcome.SERVER_ERROR):
        interval = 900
    due = (now + timedelta(seconds=interval)).isoformat() if interval is not None else None
    if state == "dormant" and event:
        from datetime import UTC

        due = (
            datetime.combine(date.fromisoformat(event[0]), datetime.min.time(), UTC)
            - timedelta(days=14)
        ).isoformat()
    conn.execute(
        "UPDATE watches SET state=?,next_check_at=?,priority=? WHERE watch_id=?",
        (state, due, result.priority, watch_id),
    )


def due_watches(conn: sqlite3.Connection, config: Config, now: datetime) -> list[str]:
    rows = conn.execute("""SELECT watch_id,source,url,state,kind,next_check_at FROM watches
        WHERE state!='gone' AND next_check_at IS NOT NULL ORDER BY priority,next_check_at,
        CASE kind WHEN 'round' THEN 1 ELSE 0 END,watch_id""").fetchall()
    return [
        str(row["watch_id"])
        for row in rows
        if config.enabled(str(row["source"]))
        and datetime.fromisoformat(row["next_check_at"]) <= now
    ]
