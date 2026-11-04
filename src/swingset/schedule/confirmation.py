"""Derive post-event registry work from retained individual final results."""

from datetime import datetime, timedelta

from swingset.state import engine

_PENDING_FINALS = (
    "\n    FROM placements p\n    JOIN contests c ON c.contest_id=p.contest_id\n    JOIN events v "
    "ON v.event_id=p.event_id\n    JOIN entries e ON\n        (e.entry_id=p.leader_entry_id AND "
    "e.role='leader') OR\n        (e.entry_id=p.follower_entry_id AND e.role='follower')\n    "
    "WHERE c.wsdc_points_eligible=1 AND c.dance_style='wcs'\n      AND v.end_date>=%s AND "
    "v.end_date<=%s\n      AND e.name_raw IS NOT NULL AND trim(e.name_raw)!=''\n      AND NOT "
    "EXISTS (\n          SELECT 1 FROM registry_placements rp\n          WHERE "
    "rp.wsdc_id=e.wsdc_id AND rp.event_id=p.event_id\n            AND rp.role=e.role AND "
    "rp.division=c.division\n            AND rp.dance_style=c.dance_style\n            AND "
    "(rp.result=CAST(p.place AS TEXT) OR rp.result='F')\n      )\n"
)


def _window(now: datetime) -> tuple[str, str]:
    return ((now.date() - timedelta(days=30)).isoformat(), now.date().isoformat())


def awaiting_first_number(conn: engine.Connection, now: datetime) -> bool:
    """Recent unlinked first-point finalists warrant daily new-ID discovery."""
    return (
        conn.execute(
            "SELECT 1 AS _value_0 "
            + _PENDING_FINALS
            + " AND e.wsdc_id IS NULL AND c.division IN ('newcomer','novice') LIMIT 1",
            _window(now),
        ).fetchone()
        is not None
    )


def pending_confirmation_events(conn: engine.Connection, wsdc_id: int, now: datetime) -> list[str]:
    """All recent eligible events still missing this known dancer's registry result."""
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT p.event_id,v.end_date "
            + _PENDING_FINALS
            + " AND e.wsdc_id=%s ORDER BY v.end_date DESC,p.event_id",
            (*_window(now), wsdc_id),
        )
    ]
