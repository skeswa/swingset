from datetime import UTC, date, datetime

from swingset.model.ids import (
    contest_id,
    entry_id,
    event_id,
    judge_id,
    round_id,
    run_id,
    series_id,
    slug,
    snapshot_id,
    unique_slugs,
)


def test_public_id_examples_and_collision_suffixes() -> None:
    event = event_id(date(2026, 8, 31), "Summer Hummer")
    contest = contest_id(event, "Novice J&J")
    assert series_id("Summer Hummer", 53) == "wsdc-53"
    assert event == "2026-08-summer-hummer"
    assert contest == "2026-08-summer-hummer/novice-j-j"
    assert round_id(contest, "prelim") == f"{contest}/prelim"
    assert entry_id(contest, "leader", "255") == f"{contest}/L-255"
    assert entry_id(contest, "follower", None, "Zoë O'Neil") == f"{contest}/F-name-zoe-o-neil"
    assert judge_id(event, "Jane Doe") == f"{event}/judge/jane-doe"
    assert unique_slugs(["Open", "Open", "Open"]) == ["open", "open-2", "open-3"]
    assert slug("  Åll-Star / J&J ") == "all-star-j-j"


def test_archive_and_run_ids_are_utc_and_stable() -> None:
    moment = datetime(2026, 9, 6, 3, 15, tzinfo=UTC)
    assert snapshot_id(moment, "9f2c1a7b3e4d" * 5) == "snap_20260906T031500Z_9f2c1a7b3e4d"
    assert run_id(moment) == "run_20260906T031500Z"
