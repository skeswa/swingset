"""Shared insertion and transport cannot bypass the historical acquisition gates."""

import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from test_admission import Corpus
from test_platform_backfill import EVENT, accept
from test_platform_backfill import fixture as fixture

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.schedule.event_pressure import bootstrap as bootstrap_pressure
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.sources.eepro.adapter import AutoIndexPage
from swingset.sources.wsdc_calendar import EventsPage


def reviewed_index(fixture):
    corpus = Corpus(fixture.db)
    corpus.page = AutoIndexPage()
    corpus.spec = WatchSpec(
        "",
        "eepro",
        "autoindex",
        "GET",
        "https://eepro.com/results/review-control/",
        corpus.page.kind,
        source_ref="eepro:review-control",
    )
    upsert_watch(fixture.conn, corpus.spec, fixture.clock.now())
    body = Path("tests/fixtures/sources/eepro-archive/freedomswing2019.html").read_bytes()
    generation, report = corpus.stage(corpus.snapshot("gate-control", body), body=body)
    assert not report.failures
    corpus.review(generation)
    # These tests exercise invalidation after request selection. Settle the
    # independent expansion metadata gate as an ordinary cycle does first.
    while fixture.conn.execute("SELECT 1 FROM event_pressure_dirty LIMIT 1").fetchone():
        bootstrap_pressure(fixture.db, fixture.config, now=fixture.clock.now())
    return corpus.page


def spec(*, kind="autoindex", parser="eepro.autoindex"):
    url = "https://eepro.com/results/example2019/"
    return WatchSpec(
        "",
        "eepro",
        kind,
        "GET",
        url,
        parser,
        source_ref=EVENT.source_ref,
        archive_url=f"https://web.archive.org/web/20190501000000id_/{url}",
    )


def assert_no_acquisition(fixture, prior_watches):
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == prior_watches
    assert fixture.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["autoindex", "index"])
def test_real_review_does_not_replace_year_acceptance_or_index_purpose(fixture, kind):
    reviewed_index(fixture)
    before = fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0]
    with pytest.raises(ValueError, match="history_year_unaccepted"):
        upsert_watch(fixture.conn, spec(kind=kind), fixture.clock.now())
    assert_no_acquisition(fixture, before)


def test_parent_contract_cannot_authorize_a_round_kind(fixture):
    reviewed_index(fixture)
    accept(fixture)
    with pytest.raises(ValueError, match="history_contract_not_enforced"):
        upsert_watch(fixture.conn, spec(kind="round", parser="eepro.round"), fixture.clock.now())


def test_phase_two_index_label_still_gets_priority_six(fixture):
    reviewed_index(fixture)
    accept(fixture)
    target = spec(kind="index")
    upsert_watch(fixture.conn, target, fixture.clock.now())
    assert tuple(
        fixture.conn.execute(
            "SELECT state,priority FROM watches WHERE watch_id=?", (target.watch_id,)
        ).fetchone()
    ) == ("backfill", 6)


def test_insert_and_priority_change_roll_back_without_touching_outer_transaction(fixture):
    reviewed_index(fixture)
    accept(fixture)
    target = spec()
    fixture.conn.execute(
        f"CREATE TEMP TRIGGER fail_priority BEFORE UPDATE OF priority ON watches WHEN NEW.watch_id='{target.watch_id}' BEGIN SELECT RAISE(ABORT,'offline priority crash'); END"
    )
    with fixture.db.transaction():
        fixture.conn.execute("INSERT INTO meta VALUES ('outer_sentinel','retained')")
        with pytest.raises(sqlite3.IntegrityError, match="offline priority crash"):
            upsert_watch(fixture.conn, target, fixture.clock.now())
        assert (
            fixture.conn.execute(
                "SELECT 1 FROM watches WHERE watch_id=?", (target.watch_id,)
            ).fetchone()
            is None
        )
        assert (
            fixture.conn.execute("SELECT value FROM meta WHERE key='outer_sentinel'").fetchone()[0]
            == "retained"
        )


@pytest.mark.parametrize(
    "invalidation,reason",
    [
        ("DELETE FROM history_acceptance", "history_year_unaccepted"),
        (
            "UPDATE admission_policies SET mode='paused' WHERE page_kind='eepro.autoindex'",
            "history_contract_not_enforced",
        ),
        (
            "UPDATE admission_policies SET contract_version='stale' WHERE page_kind='eepro.autoindex'",
            "history_contract_review_stale",
        ),
    ],
)
def test_policy_or_year_changed_after_insertion_stops_before_robots_and_budget(
    fixture, invalidation, reason
):
    page = reviewed_index(fixture)
    accept(fixture)
    target = spec()
    upsert_watch(fixture.conn, target, fixture.clock.now())
    fixture.conn.execute(invalidation)
    client = FetchClient(
        fixture.conn,
        fixture.config,
        fixture.clock,
        Archive(fixture.db.state_dir),
        transport=httpx.MockTransport(lambda _: pytest.fail("request must be gated")),
    )
    try:
        result = client.fetch(target.watch_id, page, fixture.run)
        assert result.skipped == reason
        assert fixture.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0
    finally:
        client.close()


@pytest.mark.parametrize(
    "invalidation,reason",
    [
        ("DELETE FROM history_acceptance", "history_year_unaccepted"),
        (
            "UPDATE admission_policies SET mode='paused' WHERE page_kind='eepro.autoindex'",
            "history_contract_not_enforced",
        ),
    ],
)
def test_invalidation_during_host_wait_prevents_http_and_request_charge(
    fixture, invalidation, reason
):
    page = reviewed_index(fixture)
    accept(fixture)
    target = spec()
    upsert_watch(fixture.conn, target, fixture.clock.now())
    archive = Archive(fixture.db.state_dir)
    sha = archive.store_body(b"User-agent: *\nAllow: /\n")
    fixture.conn.execute(
        "INSERT INTO hosts(host,next_allowed_at,robots_sha256,robots_status,robots_fetched_at) VALUES ('web.archive.org',?,?,200,?)",
        (
            (fixture.clock.now() + timedelta(seconds=10)).isoformat(),
            sha,
            fixture.clock.now().isoformat(),
        ),
    )

    class InvalidatingClock(FakeClock):
        def sleep(self, seconds):
            super().sleep(seconds)
            fixture.conn.execute(invalidation)

    clock = InvalidatingClock(fixture.clock.now())
    client = FetchClient(
        fixture.conn,
        fixture.config,
        clock,
        archive,
        transport=httpx.MockTransport(lambda _: pytest.fail("request must stop after wait")),
    )
    try:
        result = client.fetch(target.watch_id, page, fixture.run)
        assert result.skipped == reason
        assert clock.sleeps
        assert fixture.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0
    finally:
        client.close()


def test_policy_invalidation_on_redirect_stops_before_next_hop(fixture):
    page = reviewed_index(fixture)
    accept(fixture)
    target = spec()
    upsert_watch(fixture.conn, target, fixture.clock.now())
    archive = Archive(fixture.db.state_dir)
    sha = archive.store_body(b"User-agent: *\nAllow: /\n")
    fixture.conn.execute(
        "INSERT INTO hosts(host,robots_sha256,robots_status,robots_fetched_at) VALUES ('web.archive.org',?,200,?)",
        (sha, fixture.clock.now().isoformat()),
    )
    calls = []

    def handler(request):
        calls.append(str(request.url))
        fixture.conn.execute(
            "UPDATE admission_policies SET mode='paused' WHERE page_kind='eepro.autoindex'"
        )
        return httpx.Response(
            302, headers={"location": target.archive_url.replace("201905", "201906")}
        )

    client = FetchClient(
        fixture.conn, fixture.config, fixture.clock, archive, transport=httpx.MockTransport(handler)
    )
    try:
        assert (
            client.fetch(target.watch_id, page, fixture.run).skipped
            == "history_contract_not_enforced"
        )
        assert len(calls) == 1
        assert fixture.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 1
    finally:
        client.close()


def test_true_phase_one_still_fetches_without_year_or_scoring_policy(fixture):
    url = "https://worldsdc.com/events/"
    target = WatchSpec(
        "",
        "wsdc_calendar",
        "index",
        "GET",
        url,
        "wsdc_calendar.events",
        archive_url=f"https://web.archive.org/web/20190501000000id_/{url}",
    )
    upsert_watch(fixture.conn, target, fixture.clock.now())
    assert (
        fixture.conn.execute(
            "SELECT priority FROM watches WHERE watch_id=?", (target.watch_id,)
        ).fetchone()[0]
        == 2
    )
    config = replace(fixture.config, sources={"wsdc_calendar": fixture.config.sources["eepro"]})
    calls = []
    body = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()

    def handler(request):
        calls.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=body)
        )

    client = FetchClient(
        fixture.conn,
        config,
        fixture.clock,
        Archive(fixture.db.state_dir),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.fetch(target.watch_id, EventsPage(), fixture.run).snapshot_id
        assert len(calls) == 2
    finally:
        client.close()


def test_capture_scheduler_keeps_phase_two_at_priority_six_even_with_index_label(fixture):
    from swingset.fetch.wayback import schedule_capture

    reviewed_index(fixture)
    accept(fixture)
    target = spec(kind="index")
    with fixture.db.transaction():
        assert schedule_capture(fixture.conn, target, now=fixture.clock.now())
    assert tuple(
        fixture.conn.execute(
            "SELECT state,priority FROM watches WHERE watch_id=?", (target.watch_id,)
        ).fetchone()
    ) == ("backfill", 6)


def test_phase_two_index_label_can_retry_next_distinct_capture_without_lowering_priority(fixture):
    from test_platform_backfill import CAPTURES, failed_snapshot

    from swingset.fetch.wayback import retry_capture, schedule_capture

    reviewed_index(fixture)
    accept(fixture)
    target = spec(kind="index")
    with fixture.db.transaction():
        assert schedule_capture(fixture.conn, target, now=fixture.clock.now())
        failed_snapshot(fixture, target.watch_id, CAPTURES[0], 1)
        assert retry_capture(fixture.conn, target.watch_id, now=fixture.clock.now())
    selected = fixture.conn.execute(
        "SELECT archive_url,state,priority FROM watches WHERE watch_id=?", (target.watch_id,)
    ).fetchone()
    assert tuple(selected) == (CAPTURES[1].archive_url, "backfill", 6)
    fixture.conn.execute(
        "UPDATE admission_policies SET mode='paused' WHERE page_kind='eepro.autoindex'"
    )
    with fixture.db.transaction():
        assert not retry_capture(fixture.conn, target.watch_id, now=fixture.clock.now())
    assert tuple(
        fixture.conn.execute(
            "SELECT archive_url,state,priority FROM watches WHERE watch_id=?", (target.watch_id,)
        ).fetchone()
    ) == tuple(selected)


def test_phase_one_capture_keeps_priority_two_and_does_not_use_sheet_retry(fixture):
    from swingset.fetch.wayback import retry_capture, schedule_capture

    url = "https://worldsdc.com/events/"
    target = WatchSpec(
        "",
        "wsdc_calendar",
        "index",
        "GET",
        url,
        "wsdc_calendar.events",
        archive_url=f"https://web.archive.org/web/20190501000000id_/{url}",
    )
    with fixture.db.transaction():
        assert schedule_capture(fixture.conn, target, now=fixture.clock.now())
        assert not retry_capture(fixture.conn, target.watch_id, now=fixture.clock.now())
    assert (
        fixture.conn.execute(
            "SELECT priority FROM watches WHERE watch_id=?", (target.watch_id,)
        ).fetchone()[0]
        == 2
    )
