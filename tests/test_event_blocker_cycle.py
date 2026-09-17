"""Paused cycles can retain diagnostic facts without starting paused work."""

import shutil

import httpx
import pytest
from test_cycle import overrides as overrides
from test_event_enumerations import admit_parent
from test_event_enumerations import event as event

from swingset.schedule.cycle import run_cycle
from swingset.schedule.event_report import report
from swingset.state.controls import Selector, change_control


def test_paused_cycle_observes_blockers_and_doctor_only_reads(event, overrides, tmp_path):
    f = event
    admit_parent(f, ["one.htm"])
    config = tmp_path / "config"
    shutil.copytree("config", config)
    (config / "sources.toml").write_text("[sources.wsdc_calendar]\nenabled=false\n")
    change_control(
        f.db.state_dir,
        selector=Selector("all", "all"),
        paused=True,
        actor="test",
        reason="maintenance",
        now=f.corpus.clock.now(),
    )
    snapshots = f.conn.execute("SELECT count(*) FROM snapshots").fetchone()[0]
    result = run_cycle(
        f.db,
        config_dir=config,
        overrides_dir=overrides,
        clock=f.corpus.clock,
        budget=1,
        transport=httpx.MockTransport(lambda _: pytest.fail("paused observation fetched")),
    )
    assert not result["failed"]
    observed = result["event_blocker_observations"]
    assert observed["scanned_events"] >= 1
    assert observed["reason_counts"]["operator_pause"] >= 1
    assert any(row["action"] == "event_pressure_refresh" for row in result["held_operations"])
    assert f.conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == snapshots
    assert not f.conn.execute("SELECT 1 FROM host_budget WHERE requests>0").fetchone()
    changes = f.conn.total_changes
    with f.db.transaction(immediate=False):
        value = report(
            f.conn, f.archive, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now()
        )
        history = value["detail"]["blocker_history"]
        assert "operator_pause" in history["latest"]["facts"]["reasons"]
        assert history["eligible_service_age_seconds"] is None
        assert f.conn.total_changes == changes
