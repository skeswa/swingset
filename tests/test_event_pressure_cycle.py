"""Cycle observation respects local-operation controls and cannot fetch artifacts."""

import shutil

import httpx
import pytest
from test_cycle import overrides as overrides
from test_event_enumerations import event as event
from test_parse_hint_recovery import forget, pending, ready

from swingset.clock import FakeClock
from swingset.schedule import event_pressure
from swingset.schedule.cycle import run_cycle, versions
from swingset.state.controls import Selector, change_control
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture


def test_cycle_recovers_lost_parse_hint_during_pause_without_executing_it(
    event, tmp_path, overrides
):
    f = event
    ready(f)
    _, unit = pending(f)
    config = tmp_path / "config"
    shutil.copytree("config", config)
    (config / "sources.toml").write_text("[sources.wsdc_calendar]\nenabled=false\n")
    accept(f.db, capture(config, overrides, f.db.state_dir, versions()), f.corpus.clock)
    original_token = forget(f, unit)
    change_control(
        f.db.state_dir,
        selector=Selector("source", "eepro"),
        paused=True,
        actor="test",
        reason="retained pause",
        now=f.corpus.clock.now(),
    )

    def forbidden(request):
        raise AssertionError("paused queue reconstruction must not fetch")

    result = run_cycle(
        f.db,
        config_dir=config,
        overrides_dir=overrides,
        clock=f.corpus.clock,
        budget=1,
        transport=httpx.MockTransport(forbidden),
    )
    assert not result["failed"]
    assert result["parse_hint_recovery"]["enqueued"] == 1
    assert (
        f.conn.execute(
            "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=?",
            (unit.unit_id,),
        ).fetchone()[0]
        == original_token
    )
    assert not f.conn.execute(
        "SELECT 1 FROM work_attempts WHERE stage='parse' AND unit_id=?", (unit.unit_id,)
    ).fetchone()
    assert not f.conn.execute("SELECT 1 FROM host_budget WHERE requests>0").fetchone()


@pytest.mark.parametrize("paused", [False, True])
def test_cycle_pressure_refresh_uses_control_boundary_and_read_only_archive(
    tmp_path, overrides, monkeypatch, paused
):
    config = tmp_path / "config"
    shutil.copytree("config", config)
    (config / "sources.toml").write_text("[sources.wsdc_calendar]\nenabled=false\n")
    clock = FakeClock()
    seen = []

    def refresh(database, archive, policy, *, now, wall_seconds, **kwargs):
        assert archive.recovery is None
        assert 0 < wall_seconds <= 1
        active = database.connection.execute(
            "SELECT action_kind FROM execution_admissions "
            "WHERE state='active' AND action_id LIKE 'event_pressure_%'"
        ).fetchall()
        assert [row[0] for row in active] == ["project"]
        seen.append(now)
        return {"test_observation": True}

    def forbidden(request):
        raise AssertionError("pressure observation must not issue an HTTP request")

    monkeypatch.setattr(event_pressure, "refresh", refresh)
    with open_database(tmp_path / "state") as database:
        if paused:
            change_control(
                database.state_dir,
                selector=Selector("source", "wsdc_calendar"),
                paused=True,
                actor="test",
                reason="retained pause",
                now=clock.now(),
            )
        result = run_cycle(
            database,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            budget=1,
            transport=httpx.MockTransport(forbidden),
        )
        assert not result["failed"]
        assert bool(seen) is not paused
        if paused:
            assert any(
                row["action"] == "event_pressure_refresh" and row["reason"] == "operator_pause"
                for row in result["held_operations"]
            )
            assert "event_pressure_refresh" not in result
        else:
            assert result["event_pressure_refresh"] == {"test_observation": True}
        assert (
            database.connection.execute(
                "SELECT count(*) FROM execution_admissions WHERE state='active'"
            ).fetchone()[0]
            == 0
        )
        assert (
            database.connection.execute(
                "SELECT coalesce(sum(requests),0) FROM host_budget"
            ).fetchone()[0]
            == 0
        )
