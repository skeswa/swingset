"""Protected new-work capacity uses request receipts, not watch selections."""

from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_event_enumerations import admit_parent, finish_bootstrap, view
from test_event_enumerations import event as event
from test_event_turns import add, debit, select
from test_event_turns import f as f

from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.fetch.controls import issue
from swingset.fetch.politeness import Gate
from swingset.schedule import event_capacity, event_enumerations
from swingset.schedule.fair_policy import SchedulerConfig
from swingset.schedule.fairness import next_watch, prepare_event_turns, servicing
from swingset.sources.scoringdance import RoundPage
from swingset.state.db import open_database


@pytest.fixture
def capacity(f, monkeypatch):
    monkeypatch.setattr(event_capacity, "memberships", event_enumerations.memberships)
    return f


def page(f, name, *, listed):
    spec = add(f, name)
    with f.db.transaction() as conn:
        conn.execute(
            "UPDATE watches SET parser=? WHERE watch_id=?",
            ("scoringdance.round" if listed else "scoringdance.event", spec.watch_id),
        )
    return spec


@pytest.mark.parametrize("percent", [1, 25, 50, 75, 99])
def test_weighted_share_under_continuous_discovery_with_event_rotation(capacity, percent):
    f = capacity
    f.config = replace(f.config, scheduler=replace(f.config.scheduler, listed_page_percent=percent))
    page(f, "listed-a", listed=True)
    page(f, "listed-b", listed=True)
    page(f, "index", listed=False)
    lanes, owners = Counter(), Counter()
    for number in range(200):
        if number % 10 == 0:
            page(f, f"new-index-{number}", listed=False)
        choice = select(f)
        lanes[choice.capacity.lane] += 1
        if choice.capacity.lane == "listed_result":
            owners[choice.turn.source_ref] += 1
        debit(f, choice)
    assert lanes["listed_result"] == 2 * percent
    assert lanes["discovery"] == 200 - 2 * percent
    assert abs(owners["listed-a"] - owners["listed-b"]) <= f.config.scheduler.event_turn_requests
    assert (
        f.db.connection.execute("SELECT count(*) FROM scheduler_capacity_requests").fetchone()[0]
        == 200
    )
    assert f.db.connection.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 200


@pytest.mark.parametrize("listed", [False, True])
def test_borrowing_preserves_credit_and_does_not_create_catchup_debt(capacity, listed):
    f = capacity
    page(f, "only", listed=listed)
    for _ in range(30):
        choice = select(f)
        assert not choice.capacity.competing
        debit(f, choice)
    assert (
        f.db.connection.execute("SELECT credit FROM scheduler_capacity_service").fetchone()[0] == 0
    )
    page(f, "arriving", listed=not listed)
    lanes = []
    for _ in range(4):
        choice = select(f)
        lanes.append(choice.capacity.lane)
        debit(f, choice)
    assert lanes == ["listed_result", "discovery", "listed_result", "discovery"]
    assert (
        f.db.connection.execute(
            "SELECT count(*) FROM scheduler_capacity_requests WHERE reason='borrowed'"
        ).fetchone()[0]
        == 30
    )


def test_pause_filters_competitor_and_restart_preserves_actual_credit(capacity):
    f = capacity
    page(f, "result", listed=True)
    index = page(f, "index", listed=False)
    first = select(f)
    debit(f, first)
    assert first.capacity.lane == "listed_result"
    with f.db.transaction() as conn:
        conn.execute(
            "UPDATE watches SET paused_until='2027-01-01T00:00:00+00:00' WHERE watch_id=?",
            (index.watch_id,),
        )
    borrowed = select(f)
    assert not borrowed.capacity.competing
    debit(f, borrowed)
    state = f.db.state_dir
    f.db.close()
    f.db = open_database(state)
    assert (
        f.db.connection.execute("SELECT credit FROM scheduler_capacity_service").fetchone()[0]
        == -50
    )
    with f.db.transaction() as conn:
        conn.execute("UPDATE watches SET paused_until=NULL WHERE watch_id=?", (index.watch_id,))
    assert select(f).capacity.lane == "discovery"


def test_failed_capacity_receipt_rolls_back_host_event_and_share_usage(capacity, monkeypatch):
    f = capacity
    spec = page(f, "result", listed=True)
    choice = select(f)
    original = event_capacity.record

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("capacity receipt failed")

    monkeypatch.setattr(event_capacity, "record", fail)
    with (
        servicing(choice, run_id=f.run),
        pytest.raises(RuntimeError, match="capacity receipt failed"),
    ):
        issue(
            f.db,
            Gate(f.db.connection, f.config, f.clock),
            f.clock,
            host=choice.host,
            source=spec.source,
            watch=SimpleNamespace(watch_id=spec.watch_id, kind=spec.kind),
            page_kind=spec.parser,
            crawl_delay=0,
            sweep=False,
        )
    for table in (
        "scheduler_requests",
        "scheduler_event_requests",
        "scheduler_capacity_requests",
        "scheduler_capacity_service",
    ):
        assert f.db.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert (
        f.db.connection.execute("SELECT coalesce(sum(requests),0) FROM host_budget").fetchone()[0]
        == 0
    )
    assert f.db.connection.execute("SELECT used FROM scheduler_event_turns").fetchone()[0] == 0


def test_shared_redirect_chain_charges_one_lane_and_each_actual_host(capacity):
    f = capacity
    result = page(f, "result", listed=True)
    f.members[result.watch_id].append(
        {"source": "wsdc_calendar", "source_ref": "shared", "enumeration_id": None}
    )
    page(f, "index", listed=False)
    choice = select(f)
    calls = Counter()
    body = Path("tests/fixtures/sources/synthetic_scoringdance_round.html").read_bytes()

    def handler(request):
        calls[str(request.url)] += 1
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"location": "https://other.test/final"})
        return httpx.Response(500 if calls[str(request.url)] == 1 else 200, content=body)

    fetcher = FetchClient(
        f.db.connection,
        f.config,
        f.clock,
        Archive(f.db.state_dir),
        transport=httpx.MockTransport(handler),
        random_value=lambda: 0,
    )
    try:
        with servicing(choice, run_id=f.run):
            fetched = fetcher.fetch(result.watch_id, RoundPage(), f.run)
    finally:
        fetcher.close()
    assert fetched.snapshot_id is not None
    assert sum(calls.values()) == 5
    assert f.db.connection.execute(
        "SELECT count(*),count(DISTINCT lane) FROM scheduler_capacity_requests"
    ).fetchone()[:] == (5, 1)
    assert dict(f.db.connection.execute("SELECT host,requests FROM host_budget")) == {
        "example.test": 2,
        "other.test": 3,
    }
    assert (
        f.db.connection.execute(
            "SELECT credit FROM scheduler_capacity_service WHERE selected_host='example.test'"
        ).fetchone()[0]
        == -250
    )
    assert (
        f.db.connection.execute("SELECT count(*) FROM scheduler_capacity_service").fetchone()[0]
        == 1
    )
    # The entire chain consumes five requests of listed capacity. Other-host
    # redirects neither duplicate the receipt nor reset its selected-host debt.
    page(f, "next-result", listed=True)
    with f.db.transaction() as conn:
        conn.execute(
            "UPDATE watches SET next_check_at='2027-01-01T00:00:00+00:00' WHERE watch_id=?",
            (result.watch_id,),
        )
    f.clock.sleep(5)
    for _ in range(5):
        following = select(f)
        assert following.capacity.lane == "discovery"
        debit(f, following)
    assert select(f).capacity.lane == "listed_result"


@pytest.mark.parametrize(
    ("parser", "listed", "expected"),
    [
        ("scoringdance.event", True, "event_index"),
        ("eepro.autoindex", True, "event_index"),
        ("wdr.rounds", True, "listed_result"),
        ("wdr.rounds", False, "unlisted_result"),
        ("eepro.round", True, "listed_result"),
        ("scoringdance.sitemap", True, "essential_discovery"),
        ("wsdc_registry.dancer", False, "essential_discovery"),
        ("unknown", True, "other"),
    ],
)
def test_purpose_uses_parser_and_declared_membership(parser, listed, expected):
    assert event_capacity.purpose(parser, listed=listed) == expected


def test_real_admitted_listing_selects_result_capacity_without_canonical_mapping(event):
    admit_parent(event, ["one.htm"])
    finish_bootstrap(event)
    inventory = view(event)
    children = {watch for member in inventory["members"] for watch in member["watch_ids"]}
    exclude = {row[0] for row in event.conn.execute("SELECT watch_id FROM watches")} - children
    config = Config({"eepro.com": HostConfig()}, {"eepro": SourceConfig(True)})
    with event.db.transaction() as conn:
        prepare_event_turns(conn, config, now=event.corpus.clock.now(), exclude=exclude)
    choice = next_watch(event.conn, config, now=event.corpus.clock.now(), exclude=exclude)
    assert choice.capacity.lane == "listed_result" and choice.capacity.purpose == "listed_result"
    assert choice.turn.enumeration_id == inventory["enumeration_id"]
    assert inventory["canonical_event_id"] is None
    assert not event.conn.execute("SELECT 1 FROM scheduler_capacity_requests").fetchone()


@pytest.mark.parametrize("category", ["current", "old", "identity"])
def test_other_work_classes_keep_existing_event_selection(capacity, category):
    f = capacity
    page(f, "result", listed=True)
    choice = replace(select(f), category=category, turn=None, capacity=None)
    selected = event_capacity.choose(f.db.connection, f.config, [choice])
    assert selected == choice
    assert selected.capacity is None
    assert not f.db.connection.execute("SELECT 1 FROM scheduler_capacity_service").fetchone()


@pytest.mark.parametrize("value", [0, -1, 100, True, 1.5])
def test_share_is_a_positive_integer_below_one_hundred(value):
    with pytest.raises(ValueError):
        SchedulerConfig(listed_page_percent=value)
