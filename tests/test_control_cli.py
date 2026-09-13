"""H13 controls persist independently of the cycle lock and report drain state."""

import json
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from swingset.cli import main
from swingset.state.db import open_database


def test_pause_commits_while_cycle_owns_process_lock_without_accepting_inputs(tmp_path):
    with open_database(tmp_path) as db:
        before = list(db.connection.execute("SELECT * FROM accepted_inputs"))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "swingset.cli",
                "pause",
                "--all",
                "--actor",
                "test-operator",
                "--reason",
                "bounded control test",
                "--state",
                str(tmp_path),
                "--lock-timeout",
                "0.2",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["persisted"]
        row = db.connection.execute("SELECT * FROM operator_pauses").fetchone()
        assert row["scope_kind"] == "all" and row["actor"] == "test-operator"
        assert row["pause_id"]
        assert list(db.connection.execute("SELECT * FROM accepted_inputs")) == before
        assert db.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["--source", "not-a-registered-source"],
        ["--host", "typo.invalid"],
        ["--kind", "not-a-kind"],
        ["--all", "--actor", " "],
        ["--all", "--reason", " "],
        ["--all", "--lock-timeout", "nan"],
        ["--all", "--lock-timeout", "-1"],
        ["--all", "--wait", "inf"],
        ["--all", "--wait", "-1"],
        ["--all", "--until", "2026-01-01T00:00:00"],
    ],
)
def test_invalid_control_request_never_changes_control_revision(tmp_path, capsys, arguments):
    with open_database(tmp_path) as db:
        before = db.connection.execute("SELECT revision FROM control_state").fetchone()[0]
    assert main(["pause", "--state", str(tmp_path), *arguments]) == 1
    capsys.readouterr()
    with open_database(tmp_path) as db:
        assert db.connection.execute("SELECT revision FROM control_state").fetchone()[0] == before
        assert db.connection.execute("SELECT COUNT(*) FROM operator_pauses").fetchone()[0] == 0


def test_wait_timeout_reports_persisted_pause_and_remaining_admission(tmp_path, capsys):
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state) VALUES ('active-unit','derive',?,0,0,0,'active')",
            (datetime.now(UTC).isoformat(),),
        )
        db.connection.execute(
            "INSERT INTO execution_dependencies VALUES ('active-unit','source','eepro')"
        )
    assert (
        main(
            [
                "pause",
                "--source",
                "eepro",
                "--state",
                str(tmp_path),
                "--wait",
                "0",
                "--actor",
                "operator",
                "--reason",
                "drain requested",
            ]
        )
        == 1
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["persisted"]
    assert receipt["wait_timed_out"] and not receipt["wait_completed"]
    assert receipt["status"]["state"] == "pausing"
    assert receipt["status"]["draining_attempts"][0]["action_id"] == "active-unit"
    with open_database(tmp_path) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM operator_pauses").fetchone()[0] == 1


def test_kind_pause_waits_without_running_work_then_selective_resume_keeps_all(tmp_path, capsys):
    with open_database(tmp_path):
        pass
    assert main(["pause", "--all", "--state", str(tmp_path), "--reason", "maintenance"]) == 0
    capsys.readouterr()
    assert (
        main(["pause", "--kind", "round_observations", "--state", str(tmp_path), "--wait", "0"])
        == 0
    )
    paused = json.loads(capsys.readouterr().out)
    assert paused["wait_completed"]
    assert (
        main(
            [
                "resume",
                "--kind",
                "round_observations",
                "--state",
                str(tmp_path),
                "--reason",
                "kind ready",
            ]
        )
        == 0
    )
    capsys.readouterr()
    with open_database(tmp_path) as db:
        assert {
            row[0] for row in db.connection.execute("SELECT scope_kind FROM operator_pauses")
        } == {"all"}


def test_control_path_never_migrates_old_database(tmp_path, monkeypatch, capsys):
    import swingset.state.db as database

    with monkeypatch.context() as old:
        old.setattr(database, "SCHEMA_VERSION", 11)
        with open_database(tmp_path):
            pass
    assert main(["pause", "--all", "--state", str(tmp_path)]) == 1
    capsys.readouterr()
    with open_database(tmp_path, lock=False, read_only=True) as db:
        assert db.schema_version == 11
        assert db.connection.execute("SELECT COUNT(*) FROM operator_pauses").fetchone()[0] == 0


def test_report_pauses_follow_action_dependencies_and_expiry(tmp_path):
    from datetime import timedelta

    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import WatchSpec
    from swingset.state.controls import Selector, change_control
    from swingset.state.requirement_report import human_report, inventory
    from swingset.state.requirements import Requirement, reconcile_requirement

    now = datetime(2026, 9, 13, tzinfo=UTC)
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,1)", (now.isoformat(),)
        )
        upsert_watch(
            db.connection,
            WatchSpec("", "eepro", "round", "GET", "https://eepro.com/result", "eepro.round"),
            now,
        )
        db.connection.execute(
            "INSERT INTO pending_work VALUES ('project','dancer','42',?)", (now.isoformat(),)
        )
        key = reconcile_requirement(
            db.connection,
            Requirement(
                "source_id_checked", "42", "wsdc_registry", "ready", "check retained facts", {}
            ),
            now,
            "test",
        )
        db.connection.execute(
            "INSERT INTO hosts(host,paused_until) VALUES ('eepro.com',?)",
            ((now + timedelta(days=1)).isoformat(),),
        )
        later = now + timedelta(hours=2)
        host_only = inventory(db.connection, later)
        assert host_only["pipeline_lag"]["acquisition"]["paused_overdue_watches"] == 1
        assert host_only["pipeline_lag"]["derivation"]["paused_units"] == 0
        assert host_only["eligible"] == 1
        assert host_only["alerts"][0]["requirement_id"] == key
        change_control(
            tmp_path,
            selector=Selector("kind", "source_id_checked"),
            paused=True,
            actor="operator",
            reason="identity review",
            now=later,
            until=later + timedelta(minutes=5),
        )
        held = inventory(db.connection, later)
        assert held["eligible"] == 0 and held["paused"] == 1
        assert held["pipeline_lag"]["derivation"]["paused_units"] == 1
        assert held["pipeline_lag"]["alerts"] == []
        pause = held["requirements"][0]["pauses"][0]
        assert (
            pause["reason"] == "identity review"
            and pause["resume_selector"] == "--kind source_id_checked"
        )
        assert pause["pause_id"] in human_report(held)
        expired = inventory(db.connection, later + timedelta(minutes=5))
        assert expired["eligible"] == 1 and expired["paused"] == 0
        assert expired["controls"]["state"] == "running"
        assert len(expired["controls"]["expired_pauses"]) == 1
        assert expired["pipeline_lag"]["derivation"]["oldest_lag_seconds"] == 7500
        assert db.connection.execute("SELECT count(*) FROM operator_pauses").fetchone()[0] == 1


def test_doctor_and_summary_share_effective_control_snapshot(tmp_path, capsys, monkeypatch):
    from datetime import timedelta

    import swingset.cli as cli
    from swingset.clock import FakeClock
    from swingset.state.controls import Selector, change_control

    now = datetime(2026, 9, 13, tzinfo=UTC)
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state) VALUES ('running','derive',?,0,1,1,'active')",
            (now.isoformat(),),
        )
    change_control(
        tmp_path,
        selector=Selector("all", "all"),
        paused=True,
        actor="operator",
        reason="drain",
        now=now,
    )
    clock = FakeClock(now + timedelta(seconds=61))
    monkeypatch.setattr(cli, "SystemClock", lambda: clock)
    assert main(["doctor", "--json", "--state", str(tmp_path)]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["controls"] == doctor["requirements"]["controls"]
    assert doctor["controls"]["stuck_drain"]
    assert doctor["controls"]["state"] == "pausing"
    logged = []
    monkeypatch.setattr(cli, "log", lambda event, **fields: logged.append((event, fields)))
    assert main(["summary", "--state", str(tmp_path)]) == 0
    summary = next(fields for event, fields in logged if event == "summary")
    assert summary["controls"] == doctor["controls"]
