from datetime import UTC, datetime, timedelta

import pytest

from swingset.config import Config, HostConfig, SourceConfig
from swingset.schedule.confirmation import awaiting_first_number, pending_confirmation_events
from swingset.schedule.registry import discover_registry
from swingset.schedule.watches import refresh_policy, upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE
from swingset.state.db import open_database

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def insert(conn, table, **values):
    provenance = dict(
        source="test",
        snapshot_id="snap",
        parser_version="1",
        first_seen_at=NOW.isoformat(),
        last_seen_at=NOW.isoformat(),
        run_id="run",
    )
    values = {**provenance, **values}
    conn.execute(
        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def finalist(
    conn, event="event", *, age=7, division="novice", eligible=1, role="follower", wsdc_id=None
):
    insert(
        conn,
        "events",
        event_id=event,
        series_id=event,
        name=event,
        year=2026,
        start_date=(NOW - timedelta(days=age + 2)).date().isoformat(),
        end_date=(NOW - timedelta(days=age)).date().isoformat(),
        wsdc_status="registry",
        sources="[]",
    )
    insert(
        conn,
        "contests",
        contest_id=event,
        event_id=event,
        name_raw="Novice J&J",
        division=division,
        age_division="none",
        contest_type="jack_and_jill",
        partner_mode="random_partner",
        dance_style="wcs",
        wsdc_points_eligible=eligible,
        combined_from="[]",
        parse_status="parsed",
        source_contest_ref=event,
    )
    insert(
        conn,
        "rounds",
        round_id=event,
        contest_id=event,
        round_type="final",
        round_index=1,
        name_raw="Final",
        scoring_method="relative_placement",
        callback_legend="{}",
        judge_count=5,
        entry_count=10,
        source_round_ref=event,
    )
    insert(
        conn,
        "entries",
        entry_id=event,
        contest_id=event,
        event_id=event,
        role=role,
        bib="1",
        name_raw="New Person",
        name_norm="new person",
        wsdc_id=wsdc_id,
        link_status="unmatched",
        link_confidence=0,
        rounds_danced='["final"]',
    )
    insert(
        conn,
        "placements",
        placement_id=event,
        round_id=event,
        contest_id=event,
        event_id=event,
        place=6,
        tally="",
        **{f"{role}_entry_id": event},
    )


def known_dancer(conn):
    insert(
        conn,
        "dancers",
        wsdc_id=1,
        first_name="New",
        last_name="Person",
        name_norm="new person",
        is_pro=0,
        primary_role="follower",
        leader_required_level="novice",
        leader_allowed_level="novice",
        follower_required_level="novice",
        follower_allowed_level="novice",
        leader_highest_level="novice",
        leader_highest_points=0,
        follower_highest_level="novice",
        follower_highest_points=1,
        recent_year=2026,
        registry_internal_id=1,
        registry_fetched_at=NOW.isoformat(),
    )


def posted(conn, event, result="F"):
    insert(
        conn,
        "registry_placements",
        wsdc_id=1,
        role="follower",
        dance_style="wcs",
        division="novice",
        series_id=event,
        series_name_raw=event,
        event_month="2026-09",
        event_id=event,
        result=result,
        points=1,
    )


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, True),
        ({"division": "newcomer"}, True),
        ({"division": "advanced"}, False),
        ({"eligible": 0}, False),
        ({"role": "couple"}, False),
        ({"age": 31}, False),
        ({"age": 30}, True),
        ({"age": -1}, False),
        ({"wsdc_id": 1}, False),
    ],
)
def test_only_recent_unlinked_first_point_individual_finalists_accelerate_discovery(
    tmp_path, kwargs, expected
):
    with open_database(tmp_path) as db:
        known_dancer(db.connection)
        finalist(db.connection, **kwargs)
        assert awaiting_first_number(db.connection, NOW) is expected


def test_recent_unlinked_final_shortens_weekly_probe_wait_but_never_below_daily(tmp_path):
    with open_database(tmp_path) as db:
        finalist(db.connection)
        db.connection.executemany(
            "INSERT INTO cursors(name,value) VALUES (?,?)",
            (
                ("registry_probe_next_at", (NOW + timedelta(days=6)).isoformat()),
                ("registry_probe_last_completed_at", NOW.isoformat()),
            ),
        )
        discover_registry(db, NOW + timedelta(hours=23))
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
            ).fetchone()
            is None
        )
        discover_registry(db, NOW + timedelta(days=1))
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
            ).fetchone()[0]
            == "1"
        )
        assert (
            db.connection.execute("SELECT COUNT(*) FROM watches WHERE notes='probe'").fetchone()[0]
            == 20
        )


def test_old_unlinked_final_keeps_weekly_discovery_and_retains_result(tmp_path):
    with open_database(tmp_path) as db:
        finalist(db.connection, age=31)
        due = NOW + timedelta(days=6)
        db.connection.executemany(
            "INSERT INTO cursors(name,value) VALUES (?,?)",
            (
                ("registry_probe_next_at", due.isoformat()),
                ("registry_probe_last_completed_at", (NOW - timedelta(days=1)).isoformat()),
            ),
        )
        discover_registry(db, NOW)
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
            ).fetchone()
            is None
        )
        discover_registry(db, due)
        assert db.connection.execute(
            "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
        ).fetchone()
        assert db.connection.execute("SELECT COUNT(*) FROM placements").fetchone()[0] == 1


def test_confirmation_refresh_tracks_all_pending_events_and_stops_after_posting(tmp_path):
    config = Config({"points.worldsdc.com": HostConfig()}, {"wsdc_registry": SourceConfig(True)})
    with open_database(tmp_path) as db:
        conn = db.connection
        known_dancer(conn)
        finalist(conn, "older", age=14, wsdc_id=1)
        finalist(conn, "newer", age=7, wsdc_id=1)
        assert pending_confirmation_events(conn, 1, NOW) == ["newer", "older"]
        spec = SOURCE.watch(1)
        upsert_watch(conn, spec, NOW)
        conn.execute(
            "UPDATE watches SET notes='confirmation:newer' WHERE watch_id=?", (spec.watch_id,)
        )
        posted(conn, "newer")
        assert pending_confirmation_events(conn, 1, NOW) == ["older"]
        refresh_policy(conn, config, spec.watch_id, NOW, jitter=0)
        assert (
            conn.execute(
                "SELECT next_check_at FROM watches WHERE watch_id=?", (spec.watch_id,)
            ).fetchone()[0]
            == (NOW + timedelta(days=1)).isoformat()
        )
        posted(conn, "older", result="5")
        assert pending_confirmation_events(conn, 1, NOW) == ["older"]
        conn.execute("UPDATE registry_placements SET result='6' WHERE event_id='older'")
        assert pending_confirmation_events(conn, 1, NOW) == []
        refresh_policy(conn, config, spec.watch_id, NOW, jitter=0)
        assert (
            conn.execute(
                "SELECT next_check_at FROM watches WHERE watch_id=?", (spec.watch_id,)
            ).fetchone()[0]
            == (NOW + timedelta(days=365)).isoformat()
        )
