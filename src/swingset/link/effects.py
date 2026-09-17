"""Update placement points and confirmation watches within the link transaction."""

import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta

from swingset.schedule.confirmation import pending_confirmation_events
from swingset.schedule.watches import upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE as REGISTRY_SOURCE

from .points import expected_points


def update_registry_points(db: sqlite3.Connection, event_id: str) -> None:
    rows = db.execute(
        "SELECT p.placement_id,p.place,p.leader_entry_id,p.follower_entry_id,p.couple_entry_id,c.division,c.dance_style,c.wsdc_points_eligible,p.contest_id FROM placements p JOIN contests c USING(contest_id) WHERE p.event_id=?",
        (event_id,),
    ).fetchall()
    event = db.execute(
        "SELECT series_id,end_date FROM events WHERE event_id=?", (event_id,)
    ).fetchone()
    if event is None:
        return
    for row in rows:
        incomplete_prelim = db.execute(
            "SELECT 1 FROM findings f JOIN rounds r ON r.round_id=f.subject_id WHERE f.kind='missing_identity' AND f.subject_kind='round' AND f.closed_at IS NULL AND r.contest_id=? AND r.round_type='prelim' LIMIT 1",
            (row[8],),
        ).fetchone()
        points: list[int | None] = []
        field_sizes: list[int | None] = []
        for role, entry_id in (("leader", row[2]), ("follower", row[3])):
            linked = (
                db.execute("SELECT wsdc_id FROM entries WHERE entry_id=?", (entry_id,)).fetchone()
                if entry_id
                else None
            )
            registry = (
                db.execute(
                    "SELECT points FROM registry_placements WHERE wsdc_id=? AND role=? AND event_id=? AND division=? AND dance_style=? AND result=?",
                    (linked[0], role, event_id, row[5], row[6], str(row[1])),
                ).fetchone()
                if bool(row[7]) and linked and linked[0] is not None
                else None
            )
            points.append(int(registry[0]) if registry else None)
            field = (
                None
                if incomplete_prelim
                else db.execute(
                    "SELECT max(field_size) FROM (SELECT count(*) AS field_size FROM rounds r JOIN entries e ON e.contest_id=r.contest_id AND e.role=? WHERE r.contest_id=? AND r.round_type='prelim' AND EXISTS (SELECT 1 FROM json_each(e.rounds_danced) WHERE value=r.round_type) GROUP BY r.round_id)",
                    (role, row[8]),
                ).fetchone()
            )
            field_sizes.append(int(field[0]) if field and field[0] is not None else None)
        confirmed = all(value is not None for value in points)
        available = [index for index, value in enumerate(points) if value is not None]
        matches = (
            None
            if not available
            or not bool(row[7])
            or any(field_sizes[index] is None for index in available)
            else all(
                points[index] == expected_points(field_sizes[index] or 0, int(row[1]))
                for index in available
            )
        )
        db.execute(
            "UPDATE placements SET registry_points_leader=?,registry_points_follower=?,registry_confirmed=?,points_matches_expected=? WHERE placement_id=?",
            (
                points[0],
                points[1],
                int(confirmed),
                None if matches is None else int(matches),
                row[0],
            ),
        )


def seed_confirmation_watches(db: sqlite3.Connection, event_id: str, now: datetime) -> None:
    event = db.execute(
        "SELECT end_date,event_month FROM events WHERE event_id=?", (event_id,)
    ).fetchone()
    if event is None:
        return
    if event[0] is None:
        if not event[1]:
            return
        year, month = map(int, str(event[1]).split("-"))
        end = date(year, month, monthrange(year, month)[1])
    else:
        end = date.fromisoformat(str(event[0]))
    if end + timedelta(days=30) < now.date():
        return
    ids = db.execute(
        """SELECT DISTINCT e.wsdc_id FROM placements p
        JOIN entries e ON e.entry_id IN
          (p.leader_entry_id,p.follower_entry_id,p.couple_entry_id)
        WHERE p.event_id=? AND e.wsdc_id IS NOT NULL""",
        (event_id,),
    )
    for row in ids:
        wsdc_id = int(row[0])
        pending_events = pending_confirmation_events(db, wsdc_id, now)
        if not pending_events:
            continue
        spec = REGISTRY_SOURCE.watch(wsdc_id)
        upsert_watch(db, spec, now)
        watch = db.execute(
            "SELECT last_checked_at,next_check_at FROM watches WHERE watch_id=?",
            (spec.watch_id,),
        ).fetchone()
        due = str(watch[1] or now.isoformat())
        if watch[0] is not None:
            last_checked = datetime.fromisoformat(str(watch[0]).replace("Z", "+00:00"))
            due = (last_checked + timedelta(days=1)).isoformat()
        db.execute(
            "UPDATE watches SET notes=?,priority=5,next_check_at=? WHERE watch_id=?",
            (f"confirmation:{pending_events[0]}", due, spec.watch_id),
        )
