"""Event rotation consumes actual request debits without changing host gates."""

from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_event_enumerations import admit_parent, finish_bootstrap, view
from test_event_enumerations import event as event

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.client import FetchClient
from swingset.fetch.controls import issue, release
from swingset.fetch.politeness import Gate, Grant, Paused
from swingset.schedule import event_enumerations, event_turns
from swingset.schedule.fair_policy import SchedulerConfig
from swingset.schedule.fairness import next_watch, prepare_event_turns, servicing
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.sources.wsdc_calendar import EventsPage
from swingset.state.controls import Selector, change_control
from swingset.state.db import open_database


@pytest.fixture
def f(tmp_path, monkeypatch):
    clock = FakeClock()
    members = {}
    monkeypatch.setattr(
        event_enumerations,
        "memberships",
        lambda conn, keys: {key: members.get(key, []) for key in keys},
    )
    db = open_database(tmp_path)
    config = Config(
        {host: HostConfig(daily_request_budget=10000) for host in ("example.test", "other.test")},
        {"wsdc_calendar": SourceConfig(True)},
    )
    fixture = SimpleNamespace(
        db=db, clock=clock, members=members, config=config, run=db.start_run(clock.now())
    )
    try:
        yield fixture
    finally:
        fixture.db.close()


def add(f, event, page=0, *, host="example.test"):
    spec = WatchSpec(
        "",
        "wsdc_calendar",
        "round",
        "GET",
        f"https://{host}/{event}/{page}",
        EventsPage.kind,
        source_ref=event,
    )
    with f.db.transaction():
        upsert_watch(f.db.connection, spec, f.clock.now())
    f.members[spec.watch_id] = [
        {"source": "wsdc_calendar", "source_ref": event, "enumeration_id": None}
    ]
    return spec


def select(f, **kwargs):
    with f.db.transaction() as conn:
        prepare_event_turns(conn, f.config, now=f.clock.now(), run_id=f.run, **kwargs)
    return next_watch(f.db.connection, f.config, now=f.clock.now(), run_id=f.run, **kwargs)


def debit(f, choice, *, finish=True):
    gate = Gate(f.db.connection, f.config, f.clock)
    with servicing(choice, run_id=f.run):
        grant, action = issue(
            f.db,
            gate,
            f.clock,
            host=choice.host,
            source="wsdc_calendar",
            watch=SimpleNamespace(watch_id=choice.key, kind="round"),
            page_kind=EventsPage.kind,
            crawl_delay=0,
            sweep=False,
        )
    assert isinstance(grant, Grant)
    if finish:
        release(
            f.db,
            gate,
            f.clock,
            action,
            choice.host,
            Classification(Outcome.OK),
            body_bytes=0,
            request_day=f.clock.now().date().isoformat(),
        )
    f.clock.sleep(5)
    return action


def test_existing_large_and_older_events_finish_under_continuous_new_arrivals(f):
    for event_name, count in (("monterey", 33), ("large", 65), ("older", 9)):
        for page in range(count):
            add(f, event_name, page)
    completed = Counter()
    history = []
    for issued in range(300):
        choice = select(f)
        assert choice is not None and choice.turn is not None
        # One retrievable result page per admitted request in this finite model.
        debit(f, choice)
        event_name = choice.turn.source_ref
        completed[event_name] += 1
        history.append(event_name)
        with f.db.transaction() as conn:
            conn.execute(
                "UPDATE watches SET state='sealed',next_check_at=NULL WHERE watch_id=?",
                (choice.key,),
            )
        if issued % 4 == 0:
            add(f, f"arrival-{issued}")
        if completed["monterey"] == 33 and completed["large"] == 65 and completed["older"] == 9:
            break
    assert completed["monterey"] == 33
    assert completed["large"] == 65
    assert completed["older"] == 9
    assert len(history) < 300
    # Arrivals enter behind all owners already waiting, including the large event.
    assert set(history[:12]) == {"monterey", "large", "older"}
    assert all(
        history.count(event) == count
        for event, count in (("monterey", 33), ("large", 65), ("older", 9))
    )
    assert f.db.connection.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[
        0
    ] == len(history)


def test_restart_and_policy_reload_preserve_partial_turn_and_debits(f):
    add(f, "a")
    add(f, "b")
    choice = select(f)
    assert choice.turn
    original_policy = choice.turn.policy_digest
    debit(f, choice, finish=False)
    state = f.db.state_dir
    f.db.close()
    f.db = open_database(state)
    f.config = replace(f.config, scheduler=replace(f.config.scheduler, event_turn_requests=1))
    resumed = select(f)
    assert resumed.turn.owner_key == choice.turn.owner_key
    assert resumed.turn.policy_digest == original_policy
    row = f.db.connection.execute(
        "SELECT used,target_requests FROM scheduler_event_turns WHERE owner_key=?",
        (choice.turn.owner_key,),
    ).fetchone()
    assert tuple(row) == (1, 4)
    assert f.db.connection.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 1


def test_blocked_owner_keeps_place_while_independent_event_proceeds(f):
    a, b = add(f, "a"), add(f, "b")
    first = select(f)
    assert first.key in {a.watch_id, b.watch_id}
    with f.db.transaction() as conn:
        conn.execute(
            "UPDATE watches SET paused_until=? WHERE watch_id=?",
            ("2027-01-01T00:00:00+00:00", first.key),
        )
    other = select(f)
    assert other.key != first.key
    debit(f, other)
    with f.db.transaction() as conn:
        conn.execute("UPDATE watches SET paused_until=NULL WHERE watch_id=?", (first.key,))
    assert select(f).turn.position == first.turn.position
    assert (
        f.db.connection.execute(
            "SELECT used FROM scheduler_event_turns WHERE owner_key=?", (first.turn.owner_key,)
        ).fetchone()[0]
        == 0
    )


def test_shared_watch_redirect_robots_and_retry_have_one_owner_and_actual_hosts(f):
    f.config = replace(f.config, scheduler=replace(f.config.scheduler, event_turn_requests=1))
    spec = add(f, "a")
    f.members[spec.watch_id].append(
        {"source": "wsdc_calendar", "source_ref": "b", "enumeration_id": None}
    )
    choice = select(f)
    seen = Counter()
    body = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()

    def handler(request):
        key = str(request.url)
        seen[key] += 1
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"location": "https://other.test/final"})
        return httpx.Response(500 if seen[key] == 1 else 200, content=body)

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
            result = fetcher.fetch(spec.watch_id, EventsPage(), f.run)
    finally:
        fetcher.close()
    assert result.snapshot_id is not None
    assert sum(seen.values()) == 5
    assert dict(f.db.connection.execute("SELECT host,requests FROM host_budget")) == {
        "example.test": 2,
        "other.test": 3,
    }
    assert f.db.connection.execute(
        "SELECT count(*),count(DISTINCT owner_key) FROM scheduler_event_requests"
    ).fetchone()[:] == (5, 1)
    assert (
        f.db.connection.execute(
            "SELECT used FROM scheduler_event_turns WHERE owner_key=?", (choice.turn.owner_key,)
        ).fetchone()[0]
        == 5
    )
    f.clock.sleep(5)
    add(f, "b", 1)
    with f.db.transaction() as conn:
        conn.execute(
            "UPDATE watches SET next_check_at='2027-01-01T00:00:00+00:00' WHERE watch_id=?",
            (spec.watch_id,),
        )
    # The completed chain cannot receive a fresh turn by remaining selected.
    assert select(f).turn.owner_key != choice.turn.owner_key


def test_full_fetch_chain_bound_includes_all_robots_redirects_and_retries(f):
    spec = add(f, "chain", host="page0.test")
    choice = select(f)
    for _ in range(f.config.scheduler.event_turn_requests - 1):
        debit(f, choice)
    choice = select(f)
    seen = Counter()
    body = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()

    def handler(request):
        key = str(request.url)
        seen[key] += 1
        if seen[key] <= 3:
            return httpx.Response(500)
        if request.url.path == "/robots.txt":
            page = request.url.host.removeprefix("page").removesuffix(".test")
            return httpx.Response(
                302, headers={"location": f"https://robots-{page}-1.test/robots-hop/{page}/1"}
            )
        if request.url.path.startswith("/robots-hop/"):
            page, hop = request.url.path.split("/")[-2:]
            if int(hop) < 3:
                return httpx.Response(
                    302,
                    headers={
                        "location": f"https://robots-{page}-{int(hop) + 1}.test/robots-hop/{page}/{int(hop) + 1}"
                    },
                )
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        page = int(request.url.host.removeprefix("page").removesuffix(".test"))
        if page < 3:
            return httpx.Response(302, headers={"location": f"https://page{page + 1}.test/result"})
        return httpx.Response(200, content=body)

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
            result = fetcher.fetch(spec.watch_id, EventsPage(), f.run)
    finally:
        fetcher.close()
    assert result.snapshot_id is not None
    assert sum(seen.values()) == event_turns.FETCH_CHAIN_REQUEST_LIMIT == 80
    bound = f.config.scheduler.event_turn_requests + event_turns.FETCH_CHAIN_REQUEST_LIMIT - 1
    assert bound == 83
    assert f.db.connection.execute("SELECT used FROM scheduler_event_turns").fetchone()[0] == bound
    assert (
        f.db.connection.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[0]
        == bound
    )
    assert f.db.connection.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == bound


def test_gate_pause_and_failed_admission_do_not_consume_turn(f, monkeypatch):
    spec = add(f, "a")
    choice = select(f)
    change_control(
        f.db.state_dir,
        selector=Selector("all", "all"),
        paused=True,
        actor="test",
        reason="hold",
        now=f.clock.now(),
        timeout=1,
    )
    gate = Gate(f.db.connection, f.config, f.clock)
    kwargs = dict(
        host=choice.host,
        source=spec.source,
        watch=SimpleNamespace(watch_id=spec.watch_id, kind=spec.kind),
        page_kind=spec.parser,
        crawl_delay=0,
        sweep=False,
    )
    with servicing(choice, run_id=f.run):
        grant, action = issue(f.db, gate, f.clock, **kwargs)
    assert isinstance(grant, Paused) and action is None
    change_control(
        f.db.state_dir,
        selector=Selector("all", "all"),
        paused=False,
        actor="test",
        reason="resume",
        now=f.clock.now(),
        timeout=1,
    )
    original = event_turns.record

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after event debit")

    monkeypatch.setattr(event_turns, "record", failed)
    with servicing(choice, run_id=f.run), pytest.raises(RuntimeError, match="after event debit"):
        issue(f.db, gate, f.clock, **kwargs)
    assert f.db.connection.execute("SELECT count(*) FROM scheduler_requests").fetchone()[0] == 0
    assert (
        f.db.connection.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[0] == 0
    )
    assert f.db.connection.execute("SELECT used FROM scheduler_event_turns").fetchone()[0] == 0
    assert (
        f.db.connection.execute("SELECT coalesce(sum(requests),0) FROM host_budget").fetchone()[0]
        == 0
    )


def test_admitted_enumeration_drives_real_membership_and_request_receipt(event):
    admit_parent(event, ["one.htm", "two.htm"])
    finish_bootstrap(event)
    inventory = view(event)
    children = {watch for member in inventory["members"] for watch in member["watch_ids"]}
    exclude = {row[0] for row in event.conn.execute("SELECT watch_id FROM watches")} - children
    config = Config({"eepro.com": HostConfig()}, {"eepro": SourceConfig(True)})
    clock, run = event.corpus.clock, event.corpus.run
    with event.db.transaction() as conn:
        prepare_event_turns(conn, config, now=clock.now(), exclude=exclude, run_id=run)
    choice = next_watch(event.conn, config, now=clock.now(), exclude=exclude, run_id=run)
    assert choice is not None and choice.key in children and choice.turn is not None
    assert (choice.turn.source, choice.turn.source_ref, choice.turn.enumeration_id) == (
        "eepro",
        "eepro:test",
        inventory["enumeration_id"],
    )
    assert inventory["canonical_event_id"] is None
    gate = Gate(event.conn, config, clock)
    with servicing(choice, run_id=run):
        grant, action = issue(
            event.db,
            gate,
            clock,
            host=choice.host,
            source="eepro",
            watch=SimpleNamespace(watch_id=choice.key, kind="round"),
            page_kind="eepro.round",
            crawl_delay=0,
            sweep=False,
        )
    assert isinstance(grant, Grant)
    release(
        event.db,
        gate,
        clock,
        action,
        choice.host,
        Classification(Outcome.OK),
        body_bytes=0,
        request_day=clock.now().date().isoformat(),
    )
    assert tuple(
        event.conn.execute(
            "SELECT source,source_ref,enumeration_id FROM scheduler_event_requests WHERE action_id=?",
            (action,),
        ).fetchone()
    ) == ("eepro", "eepro:test", inventory["enumeration_id"])
    assert view(event)["acquired_pages"] == 0


@pytest.mark.parametrize("value", [0, -1, 65, True, 1.5])
def test_turn_target_is_positive_bounded_integer(value):
    with pytest.raises(ValueError):
        SchedulerConfig(event_turn_requests=value)
