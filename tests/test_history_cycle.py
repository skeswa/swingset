"""Allocated archive dispatch, same-cycle interpretation, and durable stop/resume."""

import shutil
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_history_shared_gates import reviewed_index, spec
from test_platform_backfill import accept
from test_platform_backfill import fixture as fixture

from swingset.fetch.client import FetchClient
from swingset.history import backfill
from swingset.schedule import cycle, derive
from swingset.schedule.discover import discover
from swingset.schedule.watches import upsert_watch
from swingset.state.inputs import accept as accept_inputs
from swingset.state.inputs import capture


def prepare_cycle(fixture, tmp_path, monkeypatch):
    config_dir, overrides_dir = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config_dir)
    shutil.copytree("overrides", overrides_dir)
    (config_dir / "sources.toml").write_text("[sources.eepro]\nenabled = true\n")
    (overrides_dir / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides_dir / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    monkeypatch.setenv("SWINGSET_REVISION", "offline-history-cycle-control")
    reviewed_index(fixture)  # Real retained body and exact page-kind review.
    bundle = capture(config_dir, overrides_dir, fixture.db.state_dir, cycle.versions())
    accept_inputs(fixture.db, bundle, fixture.clock)
    discover(fixture.db, bundle, fixture.clock.now())
    # Arrange an otherwise settled pipeline and already scheduled ordinary sources.
    fixture.conn.execute("DELETE FROM pending_work")
    fixture.conn.execute("UPDATE watches SET state='paused',next_check_at=NULL")
    accept(fixture)
    target = spec()
    upsert_watch(fixture.conn, target, fixture.clock.now())
    assert (
        fixture.conn.execute(
            "SELECT priority FROM watches WHERE watch_id=?", (target.watch_id,)
        ).fetchone()[0]
        == 6
    )
    # Candidate construction is outside this scheduling regression; actual fetch,
    # admission and parse are exercised below, with publication disabled.
    monkeypatch.setattr(
        cycle,
        "build",
        lambda *args, **kwargs: SimpleNamespace(
            reused=True, candidate_id="offline-unpublished-build"
        ),
    )
    return config_dir, overrides_dir, target


@pytest.mark.parametrize("stop_after_fetch", [False, True])
def test_allocated_capture_parses_in_cycle_or_resumes_after_stop(
    fixture, tmp_path, monkeypatch, stop_after_fetch
):
    config_dir, overrides_dir, target = prepare_cycle(fixture, tmp_path, monkeypatch)
    real_dispatch, dispatches = backfill.dispatch_one, []

    def traced_dispatch(*args, **kwargs):
        assert kwargs["allocated"] is True
        assert kwargs["target_watch_id"] == target.watch_id
        result = real_dispatch(*args, **kwargs)
        dispatches.append(result.reason)
        return result

    monkeypatch.setattr(backfill, "dispatch_one", traced_dispatch)
    real_fetch, fetches, completed_fetches = FetchClient.fetch, [], []

    def traced_fetch(self, watch_id, page_kind, run_id, **kwargs):
        fetches.append((watch_id, kwargs["deadline"]))
        result = real_fetch(self, watch_id, page_kind, run_id, **kwargs)
        completed_fetches.append(result)
        return result

    monkeypatch.setattr(FetchClient, "fetch", traced_fetch)
    real_parse, parsed = derive.parse_snapshot, []

    def traced_parse(*args, **kwargs):
        attempt = real_parse(*args, **kwargs)
        parsed.append(args[2].unit_id)
        return attempt

    monkeypatch.setattr(derive, "parse_snapshot", traced_parse)
    body = Path("tests/fixtures/sources/eepro-archive/freedomswing2019.html").read_bytes()
    requests = []

    def handler(request):
        requests.append(str(request.url))
        assert request.url.host == "web.archive.org"
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=body)
        )

    transport = httpx.MockTransport(handler)
    started = fixture.clock.now()
    fetched = cycle.run_cycle(
        fixture.db,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=fixture.clock,
        transport=transport,
        budget=120,
        should_stop=lambda: stop_after_fetch and bool(completed_fetches),
    )
    # The 50-second acquisition allocation has its own offline reservation;
    # the old dispatcher's independent 120-second reserve does not apply.
    assert fetched["history_dispatch"]["reason"] == "dispatched"
    assert fetches == [(target.watch_id, started + timedelta(seconds=50))]
    assert fetched["checked"] == 1 and len(requests) == 2
    snapshot_id = fixture.conn.execute(
        "SELECT snapshot_id FROM snapshots WHERE watch_id=?", (target.watch_id,)
    ).fetchone()[0]
    if stop_after_fetch:
        assert fetched["stages"] == [] and parsed == []
        assert fixture.conn.execute(
            "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?", (snapshot_id,)
        ).fetchone()
        assert (
            fixture.conn.execute(
                "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
                (target.watch_id,),
            ).fetchone()[0]
            is None
        )
    else:
        assert "parse" in fetched["stages"] and parsed == [snapshot_id]
        assert (
            fixture.conn.execute(
                "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
                (target.watch_id,),
            ).fetchone()[0]
            == snapshot_id
        )
        assert (
            fixture.conn.execute(
                "SELECT COUNT(*) FROM observations WHERE snapshot_id=? AND kind='file_row'",
                (snapshot_id,),
            ).fetchone()[0]
            == 7
        )
        assert (
            fixture.conn.execute(
                "SELECT g.state FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id WHERE u.watch_id=?",
                (target.watch_id,),
            ).fetchone()[0]
            == "accepted"
        )
        assert dispatches == ["dispatched"]
        # The fixture has no child captures. Later cycles may legitimately try
        # another retained parent capture; this turn must not refetch this one.
        assert (
            fixture.conn.execute(
                "SELECT COUNT(*) FROM work_attempts WHERE stage='parse' AND unit_id=?",
                (snapshot_id,),
            ).fetchone()[0]
            == 1
        )
        return

    resumed = cycle.run_cycle(
        fixture.db,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=fixture.clock,
        transport=transport,
        budget=120,
        should_stop=lambda: stop_after_fetch and bool(parsed),
    )
    assert parsed == [snapshot_id]
    assert resumed["checked"] == 0 and len(fetches) == 1 and len(requests) == 2
    assert dispatches == ["dispatched"]
    assert (
        fixture.conn.execute(
            "SELECT parse_status FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()[0]
        == "ok"
    )
    assert (
        fixture.conn.execute(
            "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
            (target.watch_id,),
        ).fetchone()[0]
        == snapshot_id
    )
    assert (
        fixture.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE snapshot_id=? AND kind='file_row'",
            (snapshot_id,),
        ).fetchone()[0]
        == 7
    )
    assert (
        fixture.conn.execute(
            "SELECT g.state FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id WHERE u.watch_id=?",
            (target.watch_id,),
        ).fetchone()[0]
        == "accepted"
    )
    assert (
        fixture.conn.execute(
            "SELECT COUNT(*) FROM pending_work WHERE stage='parse' AND unit_id=?", (snapshot_id,)
        ).fetchone()[0]
        == 0
    )
    if stop_after_fetch:
        assert resumed["stages"] == ["parse"]
        assert (
            fixture.conn.execute(
                "SELECT COUNT(*) FROM pending_work WHERE stage='project'"
            ).fetchone()[0]
            > 0
        )


def test_blocked_capture_token_does_not_starve_older_complete_capture(
    fixture, tmp_path, monkeypatch
):
    from swingset.sources.eepro.adapter import AutoIndexPage
    from swingset.state.work import next_work

    config_dir, overrides_dir, target = prepare_cycle(fixture, tmp_path, monkeypatch)
    body = Path("tests/fixtures/sources/eepro-archive/freedomswing2019.html").read_bytes()
    marker = b"<!-- offline incomplete-extraction control -->"
    original_extract = AutoIndexPage.extract

    def incomplete_extract(self, raw):
        records = original_extract(self, raw)
        # Simulate a parser losing one advertised file: the independent raw-DOM
        # witness must block it, while the next body uses the full real adapter.
        return records[:-1] if marker in raw else records

    monkeypatch.setattr(AutoIndexPage, "extract", incomplete_extract)
    requests = []

    def handler(request):
        requests.append(str(request.url))
        assert request.url.host == "web.archive.org"
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, content=body + marker if "20190501" in request.url.path else body
        )

    transport = httpx.MockTransport(handler)
    first = cycle.run_cycle(
        fixture.db,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=fixture.clock,
        transport=transport,
        budget=300,
    )
    assert first["history_dispatch"]["reason"] == "dispatched"
    first_snapshot = fixture.conn.execute(
        "SELECT snapshot_id FROM snapshots WHERE watch_id=?", (target.watch_id,)
    ).fetchone()[0]
    assert (
        fixture.conn.execute(
            "SELECT outcome FROM work_attempts WHERE unit_id=? ORDER BY attempt_id",
            (first_snapshot,),
        ).fetchall()[0][0]
        == "blocked"
    )
    assert (
        fixture.conn.execute(
            "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
            (target.watch_id,),
        ).fetchone()[0]
        is None
    )
    first_generations = [
        tuple(row)
        for row in fixture.conn.execute(
            "SELECT generation_id,result_json,report_json FROM source_generations WHERE unit_key=? AND state='needs_review'",
            (target.watch_id,),
        )
    ]
    assert first_generations

    second = cycle.run_cycle(
        fixture.db,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=fixture.clock,
        transport=transport,
        budget=300,
    )
    assert second["history_dispatch"]["reason"] == "dispatched"
    assert "parse" in second["stages"] and "project" in second["stages"]
    assert len(requests) == 3 and "20190401" in requests[-1]
    promoted = fixture.conn.execute(
        "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?", (target.watch_id,)
    ).fetchone()[0]
    assert promoted is not None and promoted != first_snapshot
    assert (
        fixture.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE snapshot_id=? AND kind='file_row'", (promoted,)
        ).fetchone()[0]
        == 7
    )
    assert (
        fixture.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE snapshot_id=?", (first_snapshot,)
        ).fetchone()[0]
        == 0
    )
    outcomes = [
        row[0]
        for row in fixture.conn.execute(
            "SELECT outcome FROM work_attempts WHERE unit_id=? ORDER BY attempt_id",
            (first_snapshot,),
        )
    ]
    # The active capture changed, so the old attempt becomes superseded during
    # this same cycle. Its rejected source evidence remains available for review.
    assert outcomes == ["blocked", "superseded"]
    assert [
        tuple(row)
        for row in fixture.conn.execute(
            "SELECT generation_id,result_json,report_json FROM source_generations WHERE unit_key=? AND state='needs_review'",
            (target.watch_id,),
        )
    ] == first_generations
    attempts = fixture.conn.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0]
    for _ in range(3):
        resumed = cycle.run_cycle(
            fixture.db,
            config_dir=config_dir,
            overrides_dir=overrides_dir,
            clock=fixture.clock,
            transport=transport,
            budget=300,
        )
        assert resumed["checked"] == 0 and resumed["stages"] == []
        assert resumed["candidate_id"] == "offline-unpublished-build"
    assert len(requests) == 3
    assert fixture.conn.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0] == attempts
    assert next_work(fixture.conn, "parse", now=fixture.clock.now()) is None
    # The obsolete token stays visible and latched. It neither starves the
    # healthy derivation nor grants admission authority to rejected evidence.
    # H16 independently checks the selected release closure.
    assert fixture.conn.execute(
        "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?", (first_snapshot,)
    ).fetchone()
