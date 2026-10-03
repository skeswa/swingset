"""Whole-pipeline rounds of loop ticks (`helpers/loop_ticks.py`); the cycle went in phase 5b."""

import json
import shutil
from pathlib import Path

import httpx
import pytest
from helpers.loop_ticks import run_round

from swingset.clock import FakeClock
from swingset.state.db import open_database
from swingset.versions import repository_identity

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

    def ticks():
        return run_round(
            tmp_path,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            handler=handler,
        )

    monkeypatch.setenv("SWINGSET_REVISION", "revision-a")
    first = ticks()
    assert not first.failed

    monkeypatch.setenv("SWINGSET_REVISION", "revision-b")
    changed = ticks()
    assert changed.accepted_inputs == ["version/repository"]
    assert "build" in changed.stages
    manifest = json.loads(
        (
            tmp_path / "candidates" / changed["release"]["candidate_id"] / "_meta" / "manifest.json"
        ).read_bytes()
    )
    assert manifest["repository_commit"] == "revision-b"

    quiet = ticks()
    assert quiet.accepted_inputs == []
    assert quiet.stages == []


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

    def ticks():
        return run_round(
            tmp_path,
            config_dir=calendar_config,
            overrides_dir=overrides,
            clock=clock,
            handler=handler,
        )

    first = ticks()
    assert not first.failed
    assert first.checked == 1
    with open_database(tmp_path) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
        first_row = dict(db.connection.execute("SELECT * FROM events").fetchone())
    clock.sleep(1)
    second = ticks()
    assert second.checked == 0
    assert second.stages == []
    assert len(calls) == 2
    with open_database(tmp_path) as db:
        assert dict(db.connection.execute("SELECT * FROM events").fetchone()) == first_row


def test_operator_pause_does_not_prevent_input_acceptance(tmp_path, overrides):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES ('all','all','test')"
        )

    def forbidden(request):
        raise AssertionError("paused cycle issued a request")

    result = run_round(
        tmp_path,
        config_dir=Path("config"),
        overrides_dir=overrides,
        clock=clock,
        handler=forbidden,
    )
    assert not result.failed
    assert result.checked == 0
    with open_database(tmp_path) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM accepted_inputs").fetchone()[0] > 0


def test_zero_budget_accepts_inputs_but_starts_no_network_or_stage(tmp_path, overrides):
    clock = FakeClock()

    def forbidden(_request):
        raise AssertionError("budget-exhausted cycle issued a request")

    result = run_round(
        tmp_path,
        config_dir=Path("config"),
        overrides_dir=overrides,
        clock=clock,
        budget=0,
        handler=forbidden,
    )
    assert all(record["stopped"] for record in result.values())
    assert result.checked == 0
    assert result.stages == []
    assert result.accepted_inputs
    with open_database(tmp_path) as db:
        saved = db.connection.execute(
            "SELECT summary_json FROM runs WHERE run_id=?", (result["derive"]["run_id"],)
        ).fetchone()[0]
    assert '"stopped": true' in saved


def test_existing_downstream_work_does_not_starve_collection(tmp_path, overrides, calendar_config):
    import sqlite3

    clock = FakeClock()

    def handler(request):
        with sqlite3.connect(f"file:{tmp_path / 'state.sqlite'}?mode=ro", uri=True) as conn:
            assert (
                conn.execute(
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
    result = run_round(
        tmp_path,
        config_dir=calendar_config,
        overrides_dir=overrides,
        clock=clock,
        handler=handler,
    )
    assert result.checked == 1
    assert "link" in result.stages
    with open_database(tmp_path) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0


def _calendar(tmp_path, overrides, calendar_config, clock, respond):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return respond(request)

    return run_round(
        tmp_path,
        config_dir=calendar_config,
        overrides_dir=overrides,
        clock=clock,
        handler=handler,
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
    first = _calendar(
        tmp_path,
        overrides,
        calendar_config,
        clock,
        lambda _r: httpx.Response(200, content=FIXTURE),
    )["derive"]
    assert len(parses) == 1
    # A first parse failure is a failure, as before.
    assert first["failed"] and first["waiting_repeats"] == 0
    with open_database(tmp_path) as db:
        (row,) = db.connection.execute("SELECT * FROM work_queue").fetchall()
    assert (row["state"], row["attempts"], row["error_class"]) == (
        "waiting_input",
        1,
        "parse_failed",
    )
    assert row["next_attempt_at"] == queue.at(clock.now() + timedelta(hours=24))
    # Not retried before its clock.
    clock.sleep(3600)
    _calendar(tmp_path, overrides, calendar_config, clock, lambda _r: httpx.Response(304))
    assert len(parses) == 1
    clock.sleep(23 * 3600)
    second = _calendar(tmp_path, overrides, calendar_config, clock, lambda _r: httpx.Response(304))[
        "derive"
    ]
    assert second["queue"]["woken"] == 1
    assert len(parses) == 2
    assert second["waiting_repeats"] == 1
    assert not second["failed"]
    with open_database(tmp_path) as db:
        row = db.connection.execute("SELECT * FROM work_queue").fetchone()
    assert (row["state"], row["attempts"]) == ("waiting_input", 2)
    assert row["next_attempt_at"] == queue.at(clock.now() + timedelta(hours=48))


def test_a_cycle_drains_the_neighbours_of_a_quarantined_poison_unit(
    tmp_path, overrides, monkeypatch
):
    """A projector that always raises is quarantined at its third failure; the
    units beside it keep draining, and it stops failing derive ticks (D-0216)."""
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

    # No source is enabled: the ticks only derive.
    config = tmp_path / "config"
    shutil.copytree("config", config)
    (config / "sources.toml").write_text("[sources.wsdc_calendar]\nenabled = false\n")
    monkeypatch.setattr(swingset.project, "process_unit", project)
    results = []
    for cycle in range(4):
        names = ["poison"] if cycle == 0 else []
        names += [f"n{cycle}_{i}" for i in range(3)]
        with open_database(tmp_path) as db:
            enqueue(
                db.connection,
                [WorkUnit("project", "dancer", name) for name in names],
                enqueued_at=clock.now().isoformat(),
            )
        results.append(
            run_round(
                tmp_path,
                config_dir=config,
                overrides_dir=overrides,
                clock=clock,
                budget=30,
                handler=forbidden,
            )["derive"]
        )
        # Past the longest backoff so far, well short of seven days.
        clock.sleep(3600)
    assert ran.count("poison") == 3
    assert [bool(result["failed"]) for result in results] == [True, True, True, False]
    assert results[2]["queue"]["quarantined"] == ["project/dancer/poison"]
    for cycle in range(4):
        assert {f"n{cycle}_{i}" for i in range(3)} <= set(ran)
    with open_database(tmp_path) as db:
        row = queue.get(db.connection, WorkUnit("project", "dancer", "poison"))
    assert row is not None and row.state == "quarantined"
    assert row.next_attempt_at == queue.at(clock.now() - timedelta(hours=2) + timedelta(days=7))
