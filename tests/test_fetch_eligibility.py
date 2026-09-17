"""Eligibility assessment shares gates without spending allowance or recovering files."""

from datetime import timedelta

from test_event_enumerations import event as event
from test_event_timing import prepared

from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.eligibility import ordinary
from swingset.fetch.politeness import Gate, Grant, Paused, Wait
from swingset.fetch.robots import Robots
from swingset.state.controls import Selector, change_control


def setup(f):
    policy = prepared(f)
    clock = f.corpus.clock
    return (
        Gate(f.conn, policy, clock),
        Robots(f.conn, f.archive, clock),
        f.conn.execute("SELECT watch_id FROM watches WHERE url LIKE '%one.htm'").fetchone()[0],
    )


def test_host_assessment_is_read_only_and_matches_issuance_through_daily_reset(event):
    f = event
    gate, _, _ = setup(f)
    clock = f.corpus.clock
    before = f.conn.total_changes
    assert isinstance(gate.assess("new.test")[0], Grant)
    assert f.conn.total_changes == before
    assert isinstance(gate.acquire("eepro.com"), Grant)
    assert gate.assess("eepro.com")[0] == Wait(1)
    gate.release("eepro.com", Classification(Outcome.OK))
    assert gate.assess("eepro.com")[0] == Wait(5)
    clock.sleep(5)
    f.conn.execute("UPDATE host_budget SET requests=10000 WHERE host='eepro.com'")
    assert gate.assess("eepro.com")[0] == Paused("request budget")
    _, boundary = gate.assess("eepro.com")
    clock.sleep((boundary - clock.now()).total_seconds())
    assert isinstance(gate.assess("eepro.com")[0], Grant)
    assert isinstance(gate.acquire("eepro.com"), Grant)


def test_midnight_response_bytes_stay_charged_to_request_day(event):
    f = event
    gate, _, _ = setup(f)
    clock = f.corpus.clock
    day = clock.now().date().isoformat()
    gate.acquire("eepro.com")
    clock.sleep(86401)
    gate.release("eepro.com", Classification(Outcome.OK), body_bytes=99, request_day=day)
    assert tuple(f.conn.execute("SELECT day,requests,bytes FROM host_budget").fetchone()) == (
        day,
        1,
        99,
    )


def test_ordinary_gates_cannot_be_inferred_from_due_watch(event):
    f = event
    gate, robots, watch = setup(f)
    clock = f.corpus.clock

    def check():
        return ordinary(gate, robots, watch, deadline=clock.now() + timedelta(seconds=100))

    assert check().state == "eligible"
    f.conn.execute(
        "UPDATE watches SET paused_until=? WHERE watch_id=?",
        ((clock.now() + timedelta(seconds=30)).isoformat(), watch),
    )
    assert check().reason == "watch_retry"
    f.conn.execute("UPDATE watches SET paused_until=NULL WHERE watch_id=?", (watch,))
    change_control(
        f.db.state_dir,
        selector=Selector("source", "eepro"),
        paused=True,
        actor="test",
        now=clock.now(),
        reason="test",
        sources=("eepro",),
    )
    assert check().reason == "operator_pause"
    change_control(
        f.db.state_dir,
        selector=Selector("source", "eepro"),
        paused=False,
        actor="test",
        now=clock.now(),
        reason="test",
        sources=("eepro",),
    )
    sha = f.conn.execute("SELECT robots_sha256 FROM hosts WHERE host='eepro.com'").fetchone()[0]
    f.archive.blob_path(sha).unlink()
    assert check().state == "unknown"
    assert check().reason == "robots_proof_unavailable"


def test_cached_robots_bounds_decoded_bytes_and_expiry_without_writes(event):
    f = event
    gate, robots, _ = setup(f)
    sha = f.archive.store_body(b"#" * 2048)
    f.conn.execute("UPDATE hosts SET robots_sha256=? WHERE host='eepro.com'", (sha,))
    before = f.conn.total_changes
    assert robots.cached("https://eepro.com/results", maximum_bytes=64)[0] is None
    assert f.conn.total_changes == before
    gate.clock.sleep(86400)
    assert robots.cached("https://eepro.com/results")[0] is None


def test_source_and_parser_admission_pressure_and_history_do_not_grant_time(event):
    from dataclasses import replace

    from swingset.config import SourceConfig

    f = event
    gate, robots, watch = setup(f)
    clock = f.corpus.clock

    def check():
        return ordinary(gate, robots, watch, deadline=clock.now() + timedelta(seconds=100))

    original = gate.config
    gate.config = replace(original, sources={"eepro": SourceConfig(False)})
    assert check().reason == "source_disabled"
    gate.config = replace(original, scheduler=replace(original.scheduler, pending_parse_items=1))
    f.conn.execute(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES('parse','snapshot','pending',?)",
        (clock.now().isoformat(),),
    )
    assert check().reason == "pending work backpressure"
    f.conn.execute("DELETE FROM pending_work")
    f.conn.execute(
        "UPDATE watches SET archive_url='https://web.archive.org/web/20200101000000/https://eepro.com/one.htm' WHERE watch_id=?",
        (watch,),
    )
    assert check().state == "unknown"
    assert check().reason == "historical_dispatch_proof_required"
    f.conn.execute("UPDATE watches SET parser='eepro.unknown' WHERE watch_id=?", (watch,))
    assert check().reason == "source_page_kind_unavailable"


def test_legacy_invalid_timestamps_and_future_robots_stay_unknown(event):
    f = event
    gate, robots, watch = setup(f)
    clock = f.corpus.clock
    for raw in ("not-a-time", "2026-01-01T00:00:00"):
        f.conn.execute("UPDATE watches SET next_check_at=? WHERE watch_id=?", (raw, watch))
        proof = ordinary(gate, robots, watch, deadline=clock.now() + timedelta(seconds=100))
        assert proof.state == "unknown"
        assert proof.reason == "request_gate_metadata_invalid"
    f.conn.execute(
        "UPDATE watches SET next_check_at=? WHERE watch_id=?", (clock.now().isoformat(), watch)
    )
    f.conn.execute(
        "UPDATE hosts SET robots_fetched_at=? WHERE host='eepro.com'",
        ((clock.now() + timedelta(days=1)).isoformat(),),
    )
    assert robots.cached("https://eepro.com/")[0] is None
    assert (
        ordinary(gate, robots, watch, deadline=clock.now() + timedelta(seconds=100)).state
        == "unknown"
    )
