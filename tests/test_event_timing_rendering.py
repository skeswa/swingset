"""Real doctor CLI modes preserve recorded acquisition timing and uncertainty."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_event_enumerations import admit_parent, finish_bootstrap
from test_event_enumerations import event as event_fixture
from test_event_pressure import config, enroll

from swingset import cli
from swingset.config import load_config
from swingset.fetch.eligibility import Assessment
from swingset.fetch.politeness import Gate, Paused
from swingset.schedule import event_pressure
from swingset.schedule.event_timing import Recorder, encoded, sample

event = event_fixture


@pytest.mark.parametrize("state", ["lower_bound_alert", "held", "blocked", "legacy_unknown"])
def test_real_doctor_json_human_and_summary_preserve_timing_state(
    event, monkeypatch, capsys, state
):
    f, clock = event, event.corpus.clock
    source, ref = "eepro", "eepro:test"
    if state == "legacy_unknown":
        f.conn.execute(
            "INSERT INTO source_event_inventory VALUES (?,?,NULL,NULL,'legacy_watch')",
            (source, ref),
        )
    else:
        admit_parent(f, ["one.htm"])
        finish_bootstrap(f)
        policy = config()
        enroll(f, policy)
        binding = encoded(
            event_pressure.token(f.conn, source, ref, event_pressure.policy(policy)["digest"])
        )
        if state == "held":
            (f.db.state_dir / "operator-hold").touch()

        def at():
            return sample(f.db, clock, binding)

        recorder = Recorder(
            f.db,
            source=source,
            source_ref=ref,
            run_id="rendering-check",
            token=binding,
            policy={"service_gap": 10, "no_successful_progress": 10},
            at=at(),
        )
        assessment = Assessment(
            "eligible", "offline_recorded_eligible_phase", clock.now() + timedelta(seconds=100)
        )
        if state == "blocked":
            effective = load_config(Path("config"))
            f.conn.execute("INSERT OR IGNORE INTO hosts(host) VALUES ('eepro.com')")
            f.conn.execute(
                "INSERT INTO host_budget VALUES ('eepro.com',?,?,0)",
                (clock.now().date().isoformat(), effective.host("eepro.com").daily_request_budget),
            )
            grant, until = Gate(f.conn, effective, clock).assess("eepro.com", source=source)
            assert grant == Paused("request budget")
            assessment = Assessment("blocked", grant.reason, until)
        recorder.open(at(), assessment)
        clock.sleep(12)
        recorder.close(at())
        if state == "lower_bound_alert":
            clock.sleep(1)  # A real unobserved bookkeeping interval remains unknown.
            recorder.open(at(), assessment)
            clock.sleep(1)
            recorder.close(at())

    # The actual doctor still loads configuration and opens its own read snapshot.
    # Only its injectable wall-clock boundary is fixed; neither report is mocked.
    monkeypatch.setattr(cli, "SystemClock", lambda: clock)
    args = [
        "--state",
        str(f.db.state_dir),
        "--config",
        "config",
        "--source",
        source,
        "--source-event",
        ref,
    ]
    before = list(f.conn.iterdump())
    assert cli.main(["doctor", *args, "--json"]) == 0
    machine = json.loads(capsys.readouterr().out)
    assert cli.main(["doctor", *args]) == 0
    human = capsys.readouterr().out
    human_object, end = json.JSONDecoder().raw_decode(human)
    assert human[end:].strip()  # Also exercise the actual human requirement renderer.
    assert cli.main(["summary", *args]) == 0
    summary = capsys.readouterr()
    assert not summary.out
    line = next(value for value in summary.err.splitlines() if value.startswith('event="summary" '))
    logged_inventory, _ = json.JSONDecoder().raw_decode(line.split("event_inventory=", 1)[1])
    inventory = machine["event_inventory"]
    assert human_object["event_inventory"] == inventory == logged_inventory
    assert list(f.conn.iterdump()) == before

    timing = inventory["detail"]["acquisition_timing"]
    assert timing["eligible_service_age_seconds"] is None
    assert timing["legacy_eligible_age"] == "unknown"
    assert timing["whole_event_eligible_age"] == "unknown"
    if state == "legacy_unknown":
        assert timing["observation"] is None and "alarms" not in timing
        assert inventory["legacy_unassessed_events"] == 1
        return
    for alarm in timing["alarms"].values():
        assert alarm["objective_seconds"] == 10
        if state == "lower_bound_alert":
            assert alarm["state"] == "alert"
            assert alarm["assessment"] == "lower_bound_exceeded"
            assert alarm["eligible_seconds"] == 13
            assert timing["exact_observed_active_eligible_seconds"] is None
            assert timing["wall_age_since_observation_seconds"] == 14
        elif state == "held":
            assert alarm["state"] == "suppressed_by_hold"
            assert timing["observation"]["blocked_seconds"] == 12
            assert alarm["eligible_seconds"] == 0
        else:
            assert alarm["state"] == "blocked"
            assert alarm["guard_or_reason"] == "request budget"
            assert timing["observation"]["blocked_seconds"] == 12
