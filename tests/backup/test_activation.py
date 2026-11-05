"""Bounded restore turns retain their barrier and database-bound progress."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from test_postgres_schema import fresh_database  # noqa: F401

from swingset.backup import activation, snapshot
from swingset.backup.checkpoint import CheckpointError
from swingset.clock import FakeClock
from swingset.state import inputs, postgres_backup, postgres_rebuild, postgres_schema, verify
from swingset.state.db import open_database
from swingset.state.engine import write_settings


@pytest.fixture
def staged(fresh_database, tmp_path, monkeypatch):  # noqa: F811
    service, name, connection = fresh_database
    postgres_schema.migrate(connection)
    connection.execute(
        "INSERT INTO ops.meta(key,value) VALUES('input_bundle_hash','activation-fixture')"
    )
    connection.execute(
        "INSERT INTO derive.derivation_scopes(stage,unit_kind,unit_id,registered_at) "
        "VALUES('project','year','2026','2026-11-05T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO ops.runs(run_id,started_at,finished_at,dry_run,summary_json) "
        "VALUES('original-source-run','2026-11-04','2026-11-04',1,'{\"retained\":true}')"
    )
    state = tmp_path / "state"
    write_settings(
        state, service.dsn(name, role="worker"), control_dsn=service.dsn(name, role="control")
    )
    (state / "RESTORE_PENDING").write_text("pending\n")
    monkeypatch.setattr(snapshot, "current_runtime_identity", lambda: "activation-runtime")
    monkeypatch.setattr(inputs, "load", lambda *_: object())
    calls = []
    bootstrap = postgres_rebuild.bootstrap

    def tracked_bootstrap(*args, **kwargs):
        calls.append("bootstrap")
        return bootstrap(*args, **kwargs)

    monkeypatch.setattr(postgres_rebuild, "bootstrap", tracked_bootstrap)
    monkeypatch.setattr(
        verify,
        "run",
        lambda *args, **kwargs: {"complete": True, "mismatches": [], "failures": []},
    )
    return state, connection, calls, FakeClock(datetime(2026, 11, 5, tzinfo=UTC))


def _rebuild(*, complete):
    return {
        "complete": complete,
        "reason": None if complete else "rebuild_budget_exhausted",
        "run_id": "activation-turn",
        "processed_units": 1,
        "remaining_units": 0 if complete else 1,
        "seconds": 0.01,
    }


def test_partial_turn_reopens_without_invalidating_completed_replay(staged, monkeypatch):
    state, connection, calls, clock = staged
    turns = iter((False, True))
    monkeypatch.setattr(
        postgres_rebuild, "rebuild", lambda *args, **kwargs: _rebuild(complete=next(turns))
    )
    first = activation.activate(state, clock=clock, allow_partial=True, max_units=1)
    assert first["complete"] is False and first["turn"] == 1
    assert (state / "RESTORE_PENDING").is_file()
    with pytest.raises(RuntimeError, match="restore verification is pending"):
        open_database(state)
    saved = json.loads(
        connection.execute(
            "SELECT summary_json FROM ops.runs WHERE run_id=%s", (first["job"],)
        ).fetchone()[0]
    )
    assert saved["phase"] == "bootstrapped" and saved["turns"] == 1
    result = activation.activate(state, clock=clock, allow_partial=True, max_units=1)
    assert result["complete"] is True and result["turn"] == 2
    assert calls == ["bootstrap"]
    assert not (state / "RESTORE_PENDING").exists()
    assert (
        connection.execute(
            "SELECT summary_json FROM ops.runs WHERE run_id='original-source-run'"
        ).fetchone()[0]
        == '{"retained":true}'
    )
    with open_database(state):
        pass


def test_changed_runtime_cannot_adopt_a_partial_restore(staged, monkeypatch):
    state, _, calls, clock = staged
    monkeypatch.setattr(postgres_rebuild, "rebuild", lambda *a, **kw: _rebuild(complete=False))
    activation.activate(state, clock=clock, allow_partial=True)
    monkeypatch.setattr(snapshot, "current_runtime_identity", lambda: "different-runtime")
    with pytest.raises(CheckpointError, match="different recovery inputs"):
        activation.activate(state, clock=clock, allow_partial=True)
    assert calls == ["bootstrap"] and (state / "RESTORE_PENDING").exists()


def test_stop_before_a_turn_preserves_marker_without_bootstrap(staged):
    state, _, calls, clock = staged
    result = activation.activate(state, clock=clock, allow_partial=True, stop=lambda: True)
    assert result["complete"] is False and result["reason"] == "rebuild_stopped"
    assert not calls and (state / "RESTORE_PENDING").exists()


def test_single_turn_failure_retains_resumable_state(staged, monkeypatch):
    state, _, calls, clock = staged
    monkeypatch.setattr(postgres_rebuild, "rebuild", lambda *a, **kw: _rebuild(complete=False))
    with pytest.raises(CheckpointError, match="restore rebuild is incomplete"):
        activation.activate(state, clock=clock)
    monkeypatch.setattr(postgres_rebuild, "rebuild", lambda *a, **kw: _rebuild(complete=True))
    assert activation.activate(state, clock=clock, allow_partial=True)["complete"]
    assert calls == ["bootstrap"]


def test_worker_crash_after_bootstrap_does_not_reset_the_cache_on_resume(staged, monkeypatch):
    state, _, calls, clock = staged

    def crash(*args, **kwargs):
        raise KeyboardInterrupt("injected worker death")

    monkeypatch.setattr(postgres_rebuild, "rebuild", crash)
    with pytest.raises(KeyboardInterrupt):
        activation.activate(state, clock=clock, allow_partial=True)
    assert (state / "RESTORE_PENDING").exists()
    monkeypatch.setattr(postgres_rebuild, "rebuild", lambda *a, **kw: _rebuild(complete=True))
    assert activation.activate(state, clock=clock, allow_partial=True)["complete"]
    assert calls == ["bootstrap"]


def test_failed_final_history_audit_keeps_the_restore_barrier(staged, monkeypatch):
    state, _, _, clock = staged
    history = state / "tmp/restore-history"
    history.mkdir(parents=True)
    (history / postgres_backup.MANIFEST).write_text('{"fixture":"pinned"}')
    monkeypatch.setattr(postgres_backup, "verify", lambda *_: {"rebuild_units": []})
    monkeypatch.setattr(postgres_rebuild, "rebuild", lambda *a, **kw: _rebuild(complete=True))

    def corrupt(*args, **kwargs):
        raise RuntimeError("restored history differs: runs")

    monkeypatch.setattr(postgres_backup, "audit", corrupt)
    with pytest.raises(RuntimeError, match="restored history differs"):
        activation.activate(state, clock=clock, allow_partial=True)
    assert (state / "RESTORE_PENDING").exists() and history.exists()


@pytest.mark.parametrize("old_completed_progress", [False, True])
def test_completed_activation_retries_only_finalization_after_marker_unlink_fails(
    staged, monkeypatch, old_completed_progress
):
    state, connection, calls, clock = staged
    history = state / "tmp/restore-history"
    history.mkdir(parents=True)
    (history / postgres_backup.MANIFEST).write_text('{"fixture":"pinned"}')
    monkeypatch.setattr(postgres_backup, "verify", lambda *_: {"rebuild_units": []})
    work = []

    def rebuild(*args, **kwargs):
        if "rebuild" in work:
            raise RuntimeError("completed activation must not replay derivations")
        work.append("rebuild")
        return _rebuild(complete=True)

    def verified(*args, **kwargs):
        work.append("verify")
        return {"complete": True, "mismatches": [], "failures": []}

    audits = []

    def audit(*args, **kwargs):
        assert kwargs == {"after_rebuild": True}
        audits.append("audit")

    monkeypatch.setattr(postgres_rebuild, "rebuild", rebuild)
    monkeypatch.setattr(verify, "run", verified)
    monkeypatch.setattr(postgres_backup, "audit", audit)
    marker = state / "RESTORE_PENDING"
    unlink = type(marker).unlink

    def failed_unlink(path, *args, **kwargs):
        if path == marker:
            raise OSError("interrupted final marker removal")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(type(marker), "unlink", failed_unlink)
        with pytest.raises(OSError, match="interrupted final marker removal"):
            activation.activate(state, clock=clock)
    assert marker.is_file() and history.exists()
    with pytest.raises(RuntimeError, match="restore verification is pending"):
        open_database(state)
    key, serialized = connection.execute(
        "SELECT run_id,summary_json FROM ops.runs WHERE run_id LIKE 'restore_activation:%'"
    ).fetchone()
    completed = json.loads(serialized)
    assert completed["phase"] == "complete" and completed["needs_rebuild"] is False
    if old_completed_progress:
        # A completed ledger written before this fix retained the old flag.
        completed["needs_rebuild"] = True
        connection.execute(
            "UPDATE ops.runs SET summary_json=%s WHERE run_id=%s", (json.dumps(completed), key)
        )
    result = activation.activate(state, clock=clock)
    assert result["complete"] and result["turn"] == 2
    assert calls == ["bootstrap"] and work == ["rebuild", "verify"]
    assert audits == ["audit", "audit"]
    assert not marker.exists() and not history.exists()
    saved = json.loads(
        connection.execute(
            "SELECT summary_json FROM ops.runs WHERE run_id=%s", (result["job"],)
        ).fetchone()[0]
    )
    assert saved["phase"] == "complete" and saved["needs_rebuild"] is False
    assert (
        connection.execute(
            "SELECT summary_json FROM ops.runs WHERE run_id='original-source-run'"
        ).fetchone()[0]
        == '{"retained":true}'
    )


@pytest.mark.parametrize(
    "seconds,max_units", [(0, 1), (-1, 1), (float("inf"), 1), (float("nan"), 1), (1, 0), (1, True)]
)
def test_invalid_turn_bounds_are_refused_before_opening_state(tmp_path, seconds, max_units):
    with pytest.raises(ValueError, match="positive finite"):
        activation.activate(tmp_path, seconds=seconds, max_units=max_units, allow_partial=True)
