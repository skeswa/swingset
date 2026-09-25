"""Cycle control fences preserve independent progress and pending evidence."""

import shutil
from pathlib import Path

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.publish.service import PublishResult
from swingset.schedule.cycle import run_cycle, versions
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.controls import Selector, change_control
from swingset.state.db import open_database
from swingset.state.findings import Finding, replace_findings
from swingset.state.inputs import accept, capture
from swingset.state.work import WorkUnit, enqueue

FIXTURE = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()


@pytest.fixture
def configuration(tmp_path):
    config, overrides = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config)
    shutil.copytree("overrides", overrides)
    (config / "sources.toml").write_text(
        '[sources.wsdc_calendar]\nenabled=true\nindex_urls=["https://worldsdc.com/events/"]\n'
    )
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    return config, overrides


def seed_held(db, clock):
    run = db.start_run(clock.now())
    watch = WatchSpec("", "eepro", "round", "GET", "https://eepro.com/held", "eepro.round")
    upsert_watch(db.connection, watch, clock.now())
    body = b"retained unsupported source layout"
    sha = Archive(db.state_dir).store_body(body)
    db.connection.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES ('held',?,'GET',?,?,200,?,?,1,?,'Ok')",
        (watch.watch_id, watch.url, clock.now().isoformat(), sha, len(body), run),
    )
    enqueue(
        db.connection, [WorkUnit("parse", "snapshot", "held")], enqueued_at=clock.now().isoformat()
    )
    replace_findings(
        db.connection,
        owner_kind="test",
        owner_id="held",
        findings=(Finding("manual_review", "snapshot", "held", "warning", "held layout", {}),),
        opened_at=clock.now().isoformat(),
        run_id=run,
    )


@pytest.mark.parametrize(
    "selector", [Selector("source", "eepro"), Selector("kind", "manual_review")]
)
def test_paused_backlog_does_not_starve_real_independent_fetch_and_projection(
    tmp_path, configuration, selector
):
    config, overrides = configuration
    clock = FakeClock()
    requests = []

    def transport(request):
        requests.append(str(request.url))
        assert request.url.host == "worldsdc.com"
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=FIXTURE)
        )

    with open_database(tmp_path / "state") as db:
        seed_held(db, clock)
        change_control(
            db.state_dir,
            selector=selector,
            paused=True,
            actor="operator",
            reason="source review",
            now=clock.now(),
        )
        result = run_cycle(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(transport),
        )
        assert not result["failed"]
        assert result["checked"] == 1
        assert db.connection.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert (
            db.connection.execute(
                "SELECT count(*) FROM work_attempts WHERE unit_id='held'"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.connection.execute(
                "SELECT count(*) FROM pending_work WHERE unit_id='held'"
            ).fetchone()[0]
            == 1
        )
        assert result["held_units"][0]["unit_id"] == "held"
        assert result["held_units"][0]["pauses"][0]["reason"] == "source review"
        assert "https://worldsdc.com/events/" in requests
        assert result["stages_disabled"] == []


def test_all_pause_blocks_build_and_derivation_but_preserves_bookkeeping(
    tmp_path, configuration, monkeypatch
):
    config, overrides = configuration
    clock = FakeClock()
    built = []
    monkeypatch.setattr(
        "swingset.build.service.build_release", lambda *_args, **_kwargs: built.append(True)
    )

    def forbidden(_request):
        raise AssertionError("paused request")

    with open_database(tmp_path / "state") as db:
        change_control(
            db.state_dir,
            selector=Selector("all", "all"),
            paused=True,
            actor="operator",
            reason="maintenance",
            now=clock.now(),
        )
        result = run_cycle(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(forbidden),
        )
        assert not result["failed"]
        assert built == [] and result["stages"] == []
        assert "build" in {item["action"] for item in result["held_operations"]}
        assert result["controls"]["state"] == "paused"
        assert result["accepted_inputs"]
        assert db.connection.execute("SELECT count(*) FROM watches").fetchone()[0] > 0
        assert db.connection.execute("SELECT count(*) FROM execution_admissions").fetchone()[0] == 0
        assert db.connection.execute(
            "SELECT finished_at FROM runs WHERE run_id=?", (result["run_id"],)
        ).fetchone()[0]


def test_startup_recovers_abandoned_publication_before_start_run(
    tmp_path, configuration, monkeypatch
):
    config, overrides = configuration
    clock = FakeClock()
    with open_database(tmp_path / "state") as db:
        db.connection.execute(
            "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state) VALUES ('abandoned','publication',?,0,1,1,'active')",
            (clock.now().isoformat(),),
        )
        original = db.start_run

        def checked_start(*args, **kwargs):
            assert (
                db.connection.execute("SELECT state FROM execution_admissions").fetchone()[0]
                == "uncertain"
            )
            return original(*args, **kwargs)

        monkeypatch.setattr(db, "start_run", checked_start)
        result = run_cycle(db, config_dir=config, overrides_dir=overrides, clock=clock, budget=0)
        assert result["recovered_admissions"] == 1
        assert not result["failed"]


def test_held_early_correction_does_not_abort_independent_work(
    tmp_path, configuration, monkeypatch
):
    config, overrides = configuration
    clock = FakeClock()
    monkeypatch.setattr("swingset.build.service.correction_needed", lambda *_args: True)
    monkeypatch.setattr(
        "swingset.schedule.cycle.reconcile",
        lambda *_args, **_kwargs: PublishResult("draining", reason="prior receipt pending"),
    )
    with open_database(tmp_path / "state") as db:
        seed_held(db, clock)
        change_control(
            db.state_dir,
            selector=Selector("source", "eepro"),
            paused=True,
            actor="operator",
            reason="correction review",
            now=clock.now(),
        )
        result = run_cycle(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            hub=object(),
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(200, content=FIXTURE)
                )
            ),
        )
        assert not result["failed"]
        assert result["held_operations"][0]["action"] == "correction_build"
        assert result["held_operations"][0]["pauses"][0]["reason"] == "correction review"
        assert result["reconciliation"]["state"] == "draining"
        assert result["reconciliation"]["reason"] == "prior receipt pending"
        assert db.connection.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_saved_crosscheck_is_held_by_its_shared_kind(tmp_path, configuration, monkeypatch):
    config, overrides = configuration
    clock = FakeClock()
    (config / "sources.toml").write_text("")
    calls = []
    monkeypatch.setattr(
        "swingset.schedule.cycle.run_saved_crosscheck_if_due", lambda *_args: calls.append(True)
    )
    with open_database(tmp_path / "state") as db:
        accept(db, capture(config, overrides, db.state_dir, versions()), clock)
        db.connection.execute("DELETE FROM pending_work")
        db.connection.execute("INSERT INTO meta VALUES ('registry_crosscheck_due','retained')")
        change_control(
            db.state_dir,
            selector=Selector("kind", "registry_event_association"),
            paused=True,
            actor="operator",
            reason="history review",
            now=clock.now(),
        )
        result = run_cycle(db, config_dir=config, overrides_dir=overrides, clock=clock)
        assert calls == []
        assert any(item["action"] == "registry_crosscheck" for item in result["held_operations"])
        assert (
            db.connection.execute(
                "SELECT value FROM meta WHERE key='registry_crosscheck_due'"
            ).fetchone()[0]
            == "retained"
        )


def test_historical_busy_check_ignores_only_paused_work_and_requests(tmp_path, configuration):
    from datetime import timedelta

    from swingset.config import load_config
    from swingset.history.backfill import _busy

    config, _ = configuration
    clock = FakeClock()
    with open_database(tmp_path / "state") as db:
        seed_held(db, clock)
        loaded = load_config(config)
        deadline = clock.now() + timedelta(seconds=720)
        assert _busy(db, loaded, clock, deadline) == "pipeline_work_pending"
        change_control(
            db.state_dir,
            selector=Selector("source", "eepro"),
            paused=True,
            actor="operator",
            reason="source hold",
            now=clock.now(),
        )
        assert _busy(db, loaded, clock, deadline) is None
        upsert_watch(
            db.connection,
            WatchSpec(
                "",
                "wsdc_calendar",
                "index",
                "GET",
                "https://worldsdc.com/events/",
                "wsdc_calendar.events",
            ),
            clock.now(),
        )
        assert _busy(db, loaded, clock, deadline) == "other_watch_due"
        change_control(
            db.state_dir,
            selector=Selector("kind", "source_event_mapping"),
            paused=True,
            actor="operator",
            reason="mapping hold",
            now=clock.now(),
        )
        assert _busy(db, loaded, clock, deadline) is None
        change_control(
            db.state_dir,
            selector=Selector("source", "eepro"),
            paused=False,
            actor="operator",
            reason="source ready",
            now=clock.now(),
        )
        assert _busy(db, loaded, clock, deadline) == "pipeline_work_pending"


def test_cycle_reports_publication_held_reason_without_claiming_failure(
    tmp_path, configuration, monkeypatch
):
    from swingset.build.builder import BuildResult

    config, overrides = configuration
    (config / "sources.toml").write_text("")
    clock = FakeClock()
    monkeypatch.setattr(
        "swingset.schedule.cycle.reconcile", lambda *_args, **_kwargs: PublishResult("idle")
    )
    monkeypatch.setattr(
        "swingset.build.service.build_release",
        lambda *_args, **_kwargs: BuildResult(
            "candidate", tmp_path / "candidate", "content", "manifest", True, False
        ),
    )
    monkeypatch.setattr(
        "swingset.schedule.cycle.publish",
        lambda *_args, **_kwargs: PublishResult(
            "held", candidate_id="candidate", reason="operator control changed before publication"
        ),
    )
    with open_database(tmp_path / "state") as db:
        result = run_cycle(
            db, config_dir=config, overrides_dir=overrides, clock=clock, dry_run=False, hub=object()
        )
        assert not result["failed"]
        assert result["publication"] == {
            "state": "held",
            "candidate_id": "candidate",
            "commit": None,
            "reason": "operator control changed before publication",
        }
        assert "publish_commit" not in result
        assert (
            db.connection.execute(
                "SELECT state FROM execution_admissions WHERE action_kind='build'"
            ).fetchone()[0]
            == "settled"
        )


@pytest.mark.parametrize("kind", ["build", "closure"])
def test_build_failure_is_reported_and_does_not_abort_the_cycle(
    tmp_path, configuration, monkeypatch, kind
):
    from swingset.build.builder import BuildError
    from swingset.build.closure_manifest import ClosureError

    config, overrides = configuration
    clock = FakeClock()

    def failing_build(*_args, **_kwargs):
        # A closure that fails validation is contained the same way (D-0199).
        if kind == "closure":
            raise ClosureError("callback_marks local_page_support_unverifiable")
        raise BuildError("callback_marks references missing entry x")

    monkeypatch.setattr("swingset.schedule.cycle.build", failing_build)
    with open_database(tmp_path / "state") as db:
        seed_held(db, clock)
        result = run_cycle(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(200, content=FIXTURE)
                )
            ),
        )
        # The build could not assemble a candidate, but the cycle went on to
        # fetch and derive, which is what repairs a stale generation (D-0187).
        assert result["build_error"].split(": ")[1].startswith("callback_marks")
        assert result["checked"] >= 1
        assert "parse" in result["stages"]
        # Non-fatal is not healthy: the watchdog alerts on failed=true, and 63
        # cycles once reported failed=false over one build error (D-0199).
        assert result["failed"]


def test_build_interval_defers_any_build_admitted_within_it(tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace

    from swingset.schedule.cycle import build_deferred_for_interval
    from swingset.schedule.fair_policy import SchedulerConfig

    clock = FakeClock()
    unset = SimpleNamespace(scheduler=SchedulerConfig())
    hourly = SimpleNamespace(scheduler=SchedulerConfig(build_interval_seconds=3600))
    with open_database(tmp_path / "state") as db:
        assert not build_deferred_for_interval(db.connection, hourly, now=clock.now())
        db.connection.execute(
            "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state,outcome) "
            "VALUES ('build_x','build',?,0,1,1,'settled','failed')",
            (clock.now().isoformat(),),
        )
        assert not build_deferred_for_interval(db.connection, unset, now=clock.now())
        later = clock.now() + timedelta(minutes=59)
        assert build_deferred_for_interval(db.connection, hourly, now=later)
        assert not build_deferred_for_interval(
            db.connection, hourly, now=later + timedelta(minutes=1)
        )


def test_repository_identity_ignores_uncommitted_store_markers(monkeypatch):
    from swingset.schedule.cycle import repository_identity

    monkeypatch.setenv("SWINGSET_REVISION", "uncommitted:abc123-source")
    digest = repository_identity()
    assert digest.startswith("source-sha256:")
    monkeypatch.setenv("SWINGSET_REVISION", "uncommitted:def456-source")
    assert repository_identity() == digest
    monkeypatch.setenv("SWINGSET_REVISION", "e136d95efdc7")
    assert repository_identity() == "e136d95efdc7"


def test_history_interval_defers_only_a_recently_materialized_history(tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace

    from swingset.schedule.cycle import history_deferred_for_interval
    from swingset.schedule.fair_policy import SchedulerConfig
    from swingset.state.work import WorkUnit

    clock = FakeClock()
    daily = SimpleNamespace(scheduler=SchedulerConfig(history_interval_seconds=86400))
    history = WorkUnit("project", "history", "all")
    with open_database(tmp_path / "state") as db:
        conn = db.connection
        run = db.start_run(clock.now())
        assert not history_deferred_for_interval(conn, daily, history, now=clock.now())
        conn.execute(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project','history','all',?)",
            (clock.now().isoformat(),),
        )
        conn.execute("INSERT INTO derivation_dependency_sets VALUES ('empty','[]')")
        conn.execute(
            "INSERT INTO derivation_generations VALUES ('dg_h','project','history','all','f','{}','empty',NULL,'d',0,?,?)",
            (clock.now().isoformat(), run),
        )
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id='dg_h' WHERE unit_kind='history'"
        )
        later = clock.now() + timedelta(hours=23)
        assert history_deferred_for_interval(conn, daily, history, now=later)
        assert not history_deferred_for_interval(
            conn, daily, WorkUnit("project", "event", "x"), now=later
        )
        assert not history_deferred_for_interval(
            conn, SimpleNamespace(scheduler=SchedulerConfig()), history, now=later
        )
        assert not history_deferred_for_interval(
            conn, daily, history, now=later + timedelta(hours=1)
        )


def test_a_bookkeeping_stage_overrun_is_recorded_and_the_cycle_still_fetches(
    tmp_path, configuration, monkeypatch
):
    # An overrun in a pre-acquisition stage aborted every cycle before it
    # fetched anything for eleven hours on 2026-09-25 (D-0201).
    from swingset.state import requirements
    from swingset.state.write_deadline import WriteDeadlineExceeded

    config, overrides = configuration
    clock = FakeClock()

    def overran(*_args, **_kwargs):
        raise WriteDeadlineExceeded("atomic write exceeded 45 seconds; output rolled back")

    monkeypatch.setattr(requirements, "scan", overran)
    with open_database(tmp_path / "state") as db:
        seed_held(db, clock)
        result = run_cycle(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(200, content=FIXTURE)
                )
            ),
        )
        assert result["stage_errors"] == [
            {
                "stage": "requirements_scan",
                "error": "atomic write exceeded 45 seconds; output rolled back",
            }
        ]
        assert result["failed"] and "error" not in result
        assert result["checked"] >= 1


def test_stages_switched_off_are_skipped_and_the_cycle_still_fetches(
    tmp_path, configuration, monkeypatch
):
    from swingset.history import event_sites
    from swingset.schedule import event_blocker_history, event_pressure, event_progress

    config, overrides = configuration
    with (config / "sources.toml").open("a") as file:
        file.write(
            "[stages]\nevent_site_review = false\nevent_pressure = false\n"
            "event_progress_refresh = false\nevent_blocker_observations = false\n"
        )
    clock = FakeClock()

    def skipped(*_args, **_kwargs):
        raise AssertionError("a stage switched off must not run")

    for module, name in (
        (event_sites, "reconcile"),
        (event_pressure, "bootstrap"),
        (event_pressure, "refresh"),
        (event_progress, "refresh"),
        (event_blocker_history, "refresh"),
    ):
        monkeypatch.setattr(module, name, skipped)
    requests = []

    def transport(request):
        requests.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=FIXTURE)
        )

    with open_database(tmp_path / "state") as db:
        # An accepted year would let the site review run if it were on.
        db.connection.execute(
            "INSERT INTO history_acceptance VALUES (2010,?,'test','digest')",
            (clock.now().isoformat(),),
        )
        result = run_cycle(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(transport),
        )
    stages = [
        "event_blocker_observations",
        "event_pressure",
        "event_progress_refresh",
        "event_site_review",
    ]
    assert result["stages_disabled"] == stages
    for key in (
        "event_sites_reviewed",
        "event_pressure_bootstrap",
        "event_pressure_refresh",
        "event_progress_refresh",
        "event_blocker_observations",
    ):
        assert key not in result
    assert result["stage_errors"] == [] and result["held_operations"] == []
    assert not result["failed"]
    assert "https://worldsdc.com/events/" in requests
    assert result["checked"] >= 1
