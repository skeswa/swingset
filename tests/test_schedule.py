from datetime import UTC, date, datetime

from swingset.config import HostConfig
from swingset.schedule.policy import event_state, policy


def test_event_lifecycle_utc_padding():
    start, end = date(2026, 9, 11), date(2026, 9, 13)
    for moment, expected in [
        ("2026-08-01", "dormant"),
        ("2026-09-01", "upcoming"),
        ("2026-09-10", "live"),
        ("2026-09-16", "cooling"),
        ("2026-10-14", "archived"),
    ]:
        assert (
            event_state(start, end, datetime.fromisoformat(moment).replace(tzinfo=UTC)) == expected
        )


def test_slow_round_clock_and_live_doubling():
    args = dict(
        source="scoringdance",
        host=HostConfig(round_live_interval=14400),
        now=datetime(2026, 9, 11, tzinfo=UTC),
    )
    assert policy(kind="round", **args).interval == 14400
    assert policy(kind="event", unchanged_streak=8, **args).interval == 1800
    assert policy(kind="event", unchanged_streak=80, **args).interval == 3600


def test_weekend_index_includes_monday_morning():
    args = dict(source="eepro", kind="index", host=HostConfig())
    assert policy(now=datetime(2026, 9, 14, 11, tzinfo=UTC), **args).interval == 3600
    assert policy(now=datetime(2026, 9, 14, 12, tzinfo=UTC), **args).interval == 21600
