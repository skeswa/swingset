"""Derive post-event registry work from retained individual final results."""

import sqlite3
from datetime import datetime, timedelta

_PENDING_FINALS = """
    FROM placements p
    JOIN contests c ON c.contest_id=p.contest_id
    JOIN events v ON v.event_id=p.event_id
    JOIN entries e ON
        (e.entry_id=p.leader_entry_id AND e.role='leader') OR
        (e.entry_id=p.follower_entry_id AND e.role='follower')
    WHERE c.wsdc_points_eligible=1 AND c.dance_style='wcs'
      AND v.end_date>=? AND v.end_date<=?
      AND e.name_raw IS NOT NULL AND trim(e.name_raw)!=''
      AND NOT EXISTS (
          SELECT 1 FROM registry_placements rp
          WHERE rp.wsdc_id=e.wsdc_id AND rp.event_id=p.event_id
            AND rp.role=e.role AND rp.division=c.division
            AND rp.dance_style=c.dance_style
            AND (rp.result=CAST(p.place AS TEXT) OR rp.result='F')
      )
"""


def _window(now: datetime) -> tuple[str, str]:
    return ((now.date() - timedelta(days=30)).isoformat(), now.date().isoformat())


def awaiting_first_number(conn: sqlite3.Connection, now: datetime) -> bool:
    """Recent unlinked first-point finalists warrant daily new-ID discovery."""
    return (
        conn.execute(
            "SELECT 1 "
            + _PENDING_FINALS
            + " AND e.wsdc_id IS NULL AND c.division IN ('newcomer','novice') LIMIT 1",
            _window(now),
        ).fetchone()
        is not None
    )


def pending_confirmation_events(conn: sqlite3.Connection, wsdc_id: int, now: datetime) -> list[str]:
    """All recent eligible events still missing this known dancer's registry result."""
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT p.event_id,v.end_date "
            + _PENDING_FINALS
            + " AND e.wsdc_id=? ORDER BY v.end_date DESC,p.event_id",
            (*_window(now), wsdc_id),
        )
    ]
