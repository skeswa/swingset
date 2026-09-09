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
                tmp_path
                / "candidates"
                / changed["candidate_id"]
                / "_meta"
                / "manifest.json"
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


def test_existing_downstream_work_skips_fetch_batch(tmp_path, overrides):
    clock = FakeClock()

    def forbidden(_request):
        raise AssertionError("cycle fetched before draining existing downstream work")

    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO pending_work VALUES ('link','event','missing','2026-01-01T00:00:00Z')"
        )
        result = run_cycle(
            db,
            config_dir=Path("config"),
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(forbidden),
        )
        assert result["checked"] == 0
        assert "link" in result["stages"]
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
