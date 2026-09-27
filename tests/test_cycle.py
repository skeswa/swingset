import json
import shutil
from pathlib import Path

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.schedule.cycle import repository_identity, run_cycle
from swingset.state.db import open_database

FIXTURE = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()


@pytest.fixture
def overrides(tmp_path):
    target = tmp_path / "overrides"
    shutil.copytree("overrides", target)
    (target / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (target / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    return target


@pytest.fixture
def calendar_config(tmp_path):
    target = tmp_path / "config"
    shutil.copytree("config", target)
    (target / "sources.toml").write_text(
        '[sources.wsdc_calendar]\nenabled = true\nindex_urls = ["https://worldsdc.com/events/"]\n'
    )
    return target


def test_repository_identity_hashes_local_python_source(tmp_path, monkeypatch):
    monkeypatch.delenv("SWINGSET_REVISION", raising=False)
    package = tmp_path / "swingset"
    package.mkdir()
    source = package / "module.py"
    source.write_text("VALUE = 1\n")

    first = repository_identity(package)
    assert first == repository_identity(package)
    assert first.startswith("source-sha256:")

    source.write_text("VALUE = 2\n")
    assert repository_identity(package) != first


def test_repository_revision_invalidates_one_build_then_is_quiet(
    tmp_path, overrides, calendar_config, monkeypatch
):
    clock = FakeClock()

    def handler(request):
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=FIXTURE)
        )

    monkeypatch.setenv("SWINGSET_REVISION", "revision-a")
    with open_database(tmp_path) as db:
        first = run_cycle(
            db,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(handler),
        )
        assert not first["failed"]

        monkeypatch.setenv("SWINGSET_REVISION", "revision-b")
        changed = run_cycle(
            db,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(handler),
        )
        assert changed["accepted_inputs"] == ["version/repository"]
        assert "build" in changed["stages"]
        manifest = json.loads(
            (
                tmp_path / "candidates" / changed["candidate_id"] / "_meta" / "manifest.json"
            ).read_bytes()
        )
        assert manifest["repository_commit"] == "revision-b"

        quiet = run_cycle(
            db,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(handler),
        )
        assert quiet["accepted_inputs"] == []
        assert quiet["stages"] == []


def test_calendar_cycle_then_fully_quiet_cycle(tmp_path, overrides, calendar_config):
    clock = FakeClock()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=FIXTURE)
        )

    with open_database(tmp_path) as db:
        first = run_cycle(
            db,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(handler),
        )
        assert not first["failed"]
        assert first["checked"] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
        first_row = dict(db.connection.execute("SELECT * FROM events").fetchone())
        clock.sleep(1)
        second = run_cycle(
            db,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(handler),
        )
        assert second["checked"] == 0
        assert second["stages"] == []
        assert len(calls) == 2
        assert dict(db.connection.execute("SELECT * FROM events").fetchone()) == first_row


def test_operator_pause_does_not_prevent_input_acceptance(tmp_path, overrides):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES ('all','all','test')"
        )

        def forbidden(request):
            raise AssertionError("paused cycle issued a request")

        result = run_cycle(
            db,
            config_dir=Path("config"),
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(forbidden),
        )
        assert not result["failed"]
        assert result["checked"] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM accepted_inputs").fetchone()[0] > 0


def test_zero_budget_accepts_inputs_but_starts_no_network_or_stage(tmp_path, overrides):
    clock = FakeClock()

    def forbidden(_request):
        raise AssertionError("budget-exhausted cycle issued a request")

    with open_database(tmp_path) as db:
        result = run_cycle(
            db,
            config_dir=Path("config"),
            overrides_dir=overrides,
            clock=clock,
            budget=0,
            transport=httpx.MockTransport(forbidden),
        )
        assert result["stopped"]
        assert result["checked"] == 0
        assert result["stages"] == []
        assert result["accepted_inputs"]
        saved = db.connection.execute(
            "SELECT summary_json FROM runs WHERE run_id=?", (result["run_id"],)
        ).fetchone()[0]
        assert '"stopped": true' in saved


def test_existing_downstream_work_does_not_starve_collection(tmp_path, overrides, calendar_config):
    clock = FakeClock()

    def handler(request):
        assert (
            db.connection.execute(
                "SELECT count(*) FROM pending_work WHERE stage='link' AND unit_id='missing'"
            ).fetchone()[0]
            == 1
        )
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=FIXTURE)
        )

    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO pending_work VALUES ('link','event','missing','2026-01-01T00:00:00Z')"
        )
        result = run_cycle(
            db,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(handler),
        )
        assert result["checked"] == 1
        assert "link" in result["stages"]
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0


def test_settled_build_receives_offline_time_before_acquisition_borrows(
    tmp_path, overrides, calendar_config, monkeypatch
):
    from swingset.schedule import cycle
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import WatchSpec

    clock = FakeClock()
    actions = []

    def handler(request):
        actions.append("http")
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=FIXTURE)
        )

    original_build = cycle.build

    def timed_build(*args, **kwargs):
        actions.append("build")
        clock.sleep(2)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(cycle, "build", timed_build)
    with open_database(tmp_path) as db:
        for n in range(20):
            upsert_watch(
                db.connection,
                WatchSpec(
                    "",
                    "wsdc_calendar",
                    "index",
                    "GET",
                    f"https://worldsdc.com/events/{n}",
                    "wsdc_calendar.events",
                ),
                clock.now(),
            )
        result = run_cycle(
            db,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            budget=60,
            transport=httpx.MockTransport(handler),
        )
        assert not result["failed"]
        assert result["candidate_id"]
        first_build = actions.index("build")
        assert "http" in actions[:first_build]
        assert "http" in actions[first_build + 1 :]
        assert result["scheduler"]["phase_seconds"]["offline"] >= 2
        repeated = db.connection.execute(
            "SELECT unit_kind,unit_id,count(*) AS attempts,count(DISTINCT input_fingerprint) AS inputs "
            "FROM work_attempts WHERE run_id=? AND stage='project' "
            "GROUP BY unit_kind,unit_id HAVING count(*)>1",
            (result["run_id"],),
        ).fetchall()
        assert repeated
        assert all(row["attempts"] == row["inputs"] for row in repeated)


def _calendar(tmp_path, overrides, calendar_config, clock, db, respond):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return respond(request)

    return run_cycle(
        db,
        config_dir=calendar_config,
        overrides_dir=overrides,
        clock=clock,
        transport=httpx.MockTransport(handler),
    )


def test_a_blocked_parse_waits_retries_on_its_clock_and_a_repeat_is_not_a_failure(
    tmp_path, overrides, calendar_config, monkeypatch
):
    """D-0216: a blocked parse waits a day for input, is retried, and waits two."""
    from datetime import timedelta

    from swingset.schedule import derive
    from swingset.schedule.parse import ParseAttempt
    from swingset.state import queue

    clock = FakeClock()
    parses = []

    def blocked(database, archive, unit, clock, run_id):
        parses.append(unit.unit_id)
        return ParseAttempt("wsdc_calendar", failed=True)

    monkeypatch.setattr(derive, "parse_snapshot", blocked)
    with open_database(tmp_path) as db:
        first = _calendar(
            tmp_path,
            overrides,
            calendar_config,
            clock,
            db,
            lambda _r: httpx.Response(200, content=FIXTURE),
        )
        assert len(parses) == 1
        # A first parse failure is a failure, as before.
        assert first["failed"] and first["waiting_repeats"] == 0
        (row,) = db.connection.execute("SELECT * FROM work_queue").fetchall()
        assert (row["state"], row["attempts"], row["error_class"]) == (
            "waiting_input",
            1,
            "parse_failed",
        )
        assert row["next_attempt_at"] == queue.at(clock.now() + timedelta(hours=24))
        # Not retried before its clock.
        clock.sleep(3600)
        _calendar(tmp_path, overrides, calendar_config, clock, db, lambda _r: httpx.Response(304))
        assert len(parses) == 1
        clock.sleep(23 * 3600)
        second = _calendar(
            tmp_path, overrides, calendar_config, clock, db, lambda _r: httpx.Response(304)
        )
        assert second["queue"]["woken"] == 1
        assert len(parses) == 2
        assert second["waiting_repeats"] == 1
        assert not second["failed"]
        row = db.connection.execute("SELECT * FROM work_queue").fetchone()
        assert (row["state"], row["attempts"]) == ("waiting_input", 2)
        assert row["next_attempt_at"] == queue.at(clock.now() + timedelta(hours=48))


def test_a_cycle_drains_the_neighbours_of_a_quarantined_poison_unit(
    tmp_path, overrides, monkeypatch
):
    """A projector that always raises is quarantined at its third failure; the
    units beside it keep draining, and it stops failing cycles (D-0216)."""
    from datetime import timedelta

    import swingset.project
    from swingset.state import derivations, queue
    from swingset.state.work import WorkUnit, enqueue

    clock = FakeClock()
    ran = []

    def project(database, unit, _bundle, _clock, run_id, selection=None):
        ran.append(unit.unit_id)
        if unit.unit_id == "poison":
            raise ValueError("projector defect")
        derivations.complete(
            database.connection, selection, rows=(), now=clock.now(), run_id=run_id
        )
        database.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

    def forbidden(_request):
        raise AssertionError("no source is enabled")

    # No source is enabled: the cycles only derive.
    config = tmp_path / "config"
    shutil.copytree("config", config)
    (config / "sources.toml").write_text("[sources.wsdc_calendar]\nenabled = false\n")
    monkeypatch.setattr(swingset.project, "process_unit", project)
    with open_database(tmp_path) as db:
        results = []
        for cycle in range(4):
            names = ["poison"] if cycle == 0 else []
            names += [f"n{cycle}_{i}" for i in range(3)]
            enqueue(
                db.connection,
                [WorkUnit("project", "dancer", name) for name in names],
                enqueued_at=clock.now().isoformat(),
            )
            results.append(
                run_cycle(
                    db,
                    config_dir=config,
                    overrides_dir=overrides,
                    clock=clock,
                    budget=30,
                    transport=httpx.MockTransport(forbidden),
                )
            )
            # Past the longest backoff so far, well short of seven days.
            clock.sleep(3600)
        assert ran.count("poison") == 3
        assert [bool(result["failed"]) for result in results] == [True, True, True, False]
        assert results[2]["queue"]["quarantined"] == ["project/dancer/poison"]
        for cycle in range(4):
            assert {f"n{cycle}_{i}" for i in range(3)} <= set(ran)
        row = queue.get(db.connection, WorkUnit("project", "dancer", "poison"))
        assert row is not None and row.state == "quarantined"
        assert row.next_attempt_at == queue.at(clock.now() - timedelta(hours=2) + timedelta(days=7))
