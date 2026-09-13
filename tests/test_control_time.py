"""Pause intervals change diagnostic clocks without changing evidence age."""

from datetime import UTC, datetime, timedelta

import pytest

from swingset.state.control_time import ControlTime
from swingset.state.controls import ActionScope, Selector, change_control
from swingset.state.db import open_database
from swingset.state.requirement_report import inventory
from swingset.state.requirements import Requirement, reconcile_requirement

NOW = datetime(2026, 9, 13, tzinfo=UTC)


def control(state, kind, identifier, at, *, paused=True, until=None):
    change_control(
        state,
        selector=Selector(kind, identifier),
        paused=paused,
        actor="operator",
        reason="test interval",
        now=NOW + timedelta(seconds=at),
        until=NOW + timedelta(seconds=until) if until is not None else None,
    )


def test_overlapping_scopes_union_once_after_selective_resume_and_expiry(tmp_path):
    with open_database(tmp_path) as db:
        control(tmp_path, "all", "all", 10)
        control(tmp_path, "source", "eepro", 20, until=80)
        control(tmp_path, "all", "all", 30, paused=False)
        control(tmp_path, "kind", "round_observations", 60, until=90)
        view = ControlTime(db.connection, NOW + timedelta(seconds=100))
        scope = ActionScope(sources=frozenset({"eepro"}), kinds=frozenset({"round_observations"}))
        full = view.measure(scope, NOW.isoformat())
        assert full["unpaused_seconds"] == 20
        assert full["operator_paused_seconds"] == 80
        reset = view.measure(scope, (NOW + timedelta(seconds=50)).isoformat())
        assert reset["operator_paused_seconds"] == 40 and reset["unpaused_seconds"] == 10
        # A later unrelated source only inherits the shared all pause.
        other = view.measure(ActionScope(sources=frozenset({"wdr"})), NOW.isoformat())
        assert other["unpaused_seconds"] == 80
        queries = []
        db.connection.set_trace_callback(queries.append)
        for offset in range(1000):
            view.measure(scope, (NOW - timedelta(seconds=offset)).isoformat())
        assert queries == [] and len(view.cache) == 2


def test_resume_alert_excludes_pause_but_retains_requirement_and_queue_wall_ages(tmp_path):
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,1)", (NOW.isoformat(),)
        )
        key = reconcile_requirement(
            db.connection,
            Requirement(
                "source_id_checked", "42", "wsdc_registry", "ready", "check retained source", {}
            ),
            NOW,
            "test",
        )
        db.connection.execute(
            "INSERT INTO pending_work VALUES ('project','dancer','42',?)", (NOW.isoformat(),)
        )
        control(tmp_path, "source", "wsdc_registry", 10)
        control(tmp_path, "source", "wsdc_registry", 3610, paused=False)
        resumed = inventory(db.connection, NOW + timedelta(seconds=3610), stale_after=60)
        row = resumed["requirements"][0]
        assert row["eligible"]
        assert row["age_seconds"] == 3610
        assert row["no_progress_clock"]["unpaused_seconds"] == 10
        assert row["no_progress_clock"]["operator_paused_seconds"] == 3600
        assert resumed["alerts"] == []
        queue = resumed["pipeline_lag"]
        assert queue["derivation"]["oldest_lag_seconds"] == 3610
        assert queue["derivation"]["oldest"]["lag_clock"]["unpaused_seconds"] == 10
        assert queue["alerts"] == []
        stalled = inventory(db.connection, NOW + timedelta(seconds=3671), stale_after=60)
        assert stalled["alerts"][0]["requirement_id"] == key
        assert stalled["pipeline_lag"]["alerts"][0]["reason"] == "queued_derivation_lag"


def test_legacy_unknown_start_is_unavailable_until_measurement_begins_after_import(tmp_path):
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,1)", (NOW.isoformat(),)
        )
        reconcile_requirement(
            db.connection,
            Requirement(
                "source_id_checked", "42", "wsdc_registry", "ready", "check retained evidence", {}
            ),
            NOW,
            "test",
        )
        expiry = (NOW + timedelta(seconds=200)).isoformat()
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason,pause_id,actor,until_at) VALUES ('all','all','legacy hold','legacy','legacy:unknown',?)",
            (expiry,),
        )
        db.connection.execute(
            "INSERT INTO control_events(control_revision,pause_id,scope_kind,scope_id,action,actor,reason,until_at,occurred_at) VALUES (0,'legacy','all','all','legacy_import','legacy:unknown','legacy hold',?,?)",
            (expiry, (NOW + timedelta(seconds=100)).isoformat()),
        )
        view = ControlTime(db.connection, NOW + timedelta(seconds=300))
        scope = ActionScope(sources=frozenset({"wsdc_registry"}))
        unknown = view.measure(scope, NOW.isoformat())
        assert unknown["unpaused_seconds"] is None
        assert unknown["operator_paused_seconds"] is None
        assert unknown["time_basis"] == "legacy_pause_start_unknown"
        known = view.measure(scope, (NOW + timedelta(seconds=150)).isoformat())
        assert known["unpaused_seconds"] == 100 and known["operator_paused_seconds"] == 50
        report = inventory(db.connection, NOW + timedelta(seconds=300), stale_after=60)
        assert report["unknown_no_progress_clocks"] == 1
        assert report["oldest_unresolved_age_seconds"] == 300
        assert report["requirements"][0]["eligible"]
        assert report["alerts"] == []


@pytest.mark.parametrize("since", ["2026-09-13", "2026-09-13T00:00:00", "invalid", None])
def test_timezone_missing_or_invalid_measurement_start_is_unknown(tmp_path, since):
    with open_database(tmp_path) as db:
        measured = ControlTime(db.connection, NOW).measure(None, since)
        assert measured == {
            "unpaused_seconds": None,
            "operator_paused_seconds": None,
            "time_basis": "unknown_start",
        }


@pytest.mark.parametrize(
    "field,value",
    [
        ("occurred_at", "2026-09-13"),
        ("occurred_at", "invalid"),
        ("until_at", "2026-09-13T00:00:00"),
    ],
)
def test_invalid_event_timing_only_makes_matching_scope_unavailable(tmp_path, field, value):
    with open_database(tmp_path) as db:
        control(tmp_path, "source", "eepro", 10, until=90)
        db.connection.execute(f"UPDATE control_events SET {field}=?", (value,))
        measured = ControlTime(db.connection, NOW + timedelta(seconds=100))
        assert measured.has_history
        held = measured.measure(ActionScope(sources=frozenset({"eepro"})), NOW.isoformat())
        assert (
            held["unpaused_seconds"] is None and held["time_basis"] == "control_timestamp_unknown"
        )
        unrelated = measured.measure(ActionScope(sources=frozenset({"wdr"})), NOW.isoformat())
        assert unrelated["unpaused_seconds"] == 100


def test_legacy_current_pause_with_naive_creation_time_does_not_crash(tmp_path):
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason,created_at) VALUES ('all','all','legacy','2026-09-13')"
        )
        result = ControlTime(db.connection, NOW).measure(
            ActionScope(), (NOW - timedelta(seconds=1)).isoformat()
        )
        assert result["unpaused_seconds"] is None
        assert result["time_basis"] == "control_timestamp_unknown"
