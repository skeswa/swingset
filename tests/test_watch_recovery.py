from datetime import UTC, datetime, timedelta

from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.classify import Outcome
from swingset.schedule.watches import refresh_policy, upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database


def test_wdr_requires_thirty_distinct_daily_unavailable_checks_before_gone(tmp_path) -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    config = Config({"example.test": HostConfig()}, {"wdr": SourceConfig(True)})
    spec = WatchSpec("", "wdr", "event", "GET", "https://example.test/routeInfo.json", "wdr.rounds")
    with open_database(tmp_path) as database:
        run = database.start_run(started)
        upsert_watch(database.connection, spec, started)
        for index in range(30):
            database.connection.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
                "body_bytes,content_changed,run_id,classification) VALUES (?,?,?,?,?,403,0,0,?,?)",
                (
                    f"same-day-{index}",
                    spec.watch_id,
                    "GET",
                    spec.url,
                    (started + timedelta(seconds=index)).isoformat(),
                    run,
                    "ExpectedUnavailable",
                ),
            )
        refresh_policy(
            database.connection,
            config,
            spec.watch_id,
            started + timedelta(days=29),
            outcome=Outcome.EXPECTED_UNAVAILABLE,
            jitter=0,
        )
        assert (
            database.connection.execute(
                "SELECT state FROM watches WHERE watch_id=?", (spec.watch_id,)
            ).fetchone()[0]
            != "gone"
        )
        database.connection.execute("DELETE FROM snapshots")
        for day in range(30):
            database.connection.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
                "body_bytes,content_changed,run_id,classification) VALUES (?,?,?,?,?,403,0,0,?,?)",
                (
                    f"day-{day}",
                    spec.watch_id,
                    "GET",
                    spec.url,
                    (started + timedelta(days=day)).isoformat(),
                    run,
                    "ExpectedUnavailable",
                ),
            )
        refresh_policy(
            database.connection,
            config,
            spec.watch_id,
            started + timedelta(days=29),
            outcome=Outcome.EXPECTED_UNAVAILABLE,
            jitter=0,
        )
        assert tuple(
            database.connection.execute(
                "SELECT state,next_check_at FROM watches WHERE watch_id=?", (spec.watch_id,)
            ).fetchone()
        ) == ("gone", None)
