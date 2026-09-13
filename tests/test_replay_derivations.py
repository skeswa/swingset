"""Offline scratch cohorts use real atomic workers and resume immutable proof."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_h15_acceptance import source_fixture as fixture_source

from swingset.state.db import open_database


@pytest.fixture
def arranged(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    global replay
    from research import replay_derivations as replay

    generator = fixture_source.__wrapped__(tmp_path)
    f = next(generator)
    checkpoint = tmp_path / "checkpoints" / "retained"
    checkpoint.mkdir(parents=True)
    with closing(sqlite3.connect(checkpoint / "state.sqlite")) as saved:
        f.conn.backup(saved)
    original = checkpoint / "state.sqlite"
    (checkpoint / "checkpoint.json").write_text(
        json.dumps(
            {
                "schema_version": f.db.schema_version,
                "files": {
                    "state.sqlite": {
                        "size": original.stat().st_size,
                        "sha256": replay.digest(original),
                    }
                },
            }
        )
    )
    monkeypatch.setattr(replay, "verify_runtime", lambda *args: None)
    source = Path(__file__).parents[1]
    scratch = tmp_path / "scratch"
    replay.prepare_scratch(checkpoint, scratch, source, receipt_sha256="test-pin")
    yield f, checkpoint, scratch, source
    try:
        next(generator)
    except StopIteration:
        pass


def execute(arranged, output, **kwargs):
    f, _, scratch, source = arranged
    return replay.run(
        scratch,
        source,
        f.config,
        f.overrides,
        output,
        receipt_sha256="test-pin",
        clock=f.corpus.clock,
        **kwargs,
    )


def test_bounded_cohorts_resume_real_generations_without_parse_or_fairness(
    arranged, tmp_path, monkeypatch
):
    _, checkpoint, scratch, _ = arranged
    before = replay.digest(checkpoint / "state.sqlite")
    real = replay.derive_one
    calls = []

    def trace(database, archive, unit, *args):
        assert unit.stage in {"project", "link"}
        assert not (scratch / "blobs").exists()
        calls.append((unit.stage, unit.unit_kind, unit.unit_id))
        return real(database, archive, unit, *args)

    monkeypatch.setattr(replay, "derive_one", trace)
    first = execute(arranged, tmp_path / "first.json", max_units=2, progress_every=1)
    assert first["completed"] == 2 and first["status"] == "bounded_stop"
    with open_database(scratch, read_only=True) as db:
        retained = {
            row[0]
            for row in db.connection.execute("SELECT generation_id FROM derivation_generations")
        }
        parse_before = [
            tuple(row)
            for row in db.connection.execute(
                "SELECT * FROM pending_work WHERE stage='parse' ORDER BY unit_id"
            )
        ]
    second = execute(arranged, tmp_path / "second.json")
    assert second["status"] == "current"
    with open_database(scratch, read_only=True) as db:
        assert retained.issubset(
            {
                row[0]
                for row in db.connection.execute("SELECT generation_id FROM derivation_generations")
            }
        )
        assert [
            tuple(row)
            for row in db.connection.execute(
                "SELECT * FROM pending_work WHERE stage='parse' ORDER BY unit_id"
            )
        ] == parse_before
        assert (
            db.connection.execute(
                "SELECT count(*) FROM work_attempts WHERE stage='parse'"
            ).fetchone()[0]
            == 0
        )
    calls_before = list(calls)
    third = execute(arranged, tmp_path / "third.json")
    assert third["completed"] == third["attempted"] == 0 and third["status"] == "current"
    assert calls == calls_before
    assert replay.digest(checkpoint / "state.sqlite") == before
    assert not any(checkpoint.glob("*-shm"))
    assert first["network_requests"] == second["network_requests"] == 0
    assert not second["parse_executed"] and not second["fairness_claimed"]


def test_crash_receipt_and_resume_keep_previous_atomic_completion(arranged, tmp_path, monkeypatch):
    real = replay.derive_one
    calls = 0

    def stop(database, archive, unit, *args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return real(database, archive, unit, *args)

    monkeypatch.setattr(replay, "derive_one", stop)
    with pytest.raises(KeyboardInterrupt):
        execute(arranged, tmp_path / "interrupted.json", progress_every=1)
    receipt = json.loads((tmp_path / "interrupted.json").read_bytes())
    assert receipt["status"] == "interrupted" and receipt["completed"] == 1
    monkeypatch.setattr(replay, "derive_one", real)
    assert execute(arranged, tmp_path / "resumed.json")["status"] == "current"


@pytest.mark.parametrize(
    "problem",
    [
        "no_marker",
        "hardlink",
        "symlink",
        "wrong_marker",
        "output_symlink",
        "existing_output",
        "checkpoint_target",
        "production_target",
    ],
)
def test_refuses_unbound_or_linked_scratch_and_unsafe_receipts(arranged, tmp_path, problem):
    f, checkpoint, scratch, source = arranged
    output = tmp_path / "receipt.json"
    if problem == "no_marker":
        (scratch / replay.MARKER).unlink()
    elif problem in {"hardlink", "symlink"}:
        (scratch / "state.sqlite").unlink()
        if problem == "hardlink":
            os.link(checkpoint / "state.sqlite", scratch / "state.sqlite")
        else:
            (scratch / "state.sqlite").symlink_to(checkpoint / "state.sqlite")
    elif problem == "wrong_marker":
        value = json.loads((scratch / replay.MARKER).read_bytes())
        value["scratch"] = "elsewhere"
        (scratch / replay.MARKER).write_text(json.dumps(value))
    elif problem == "output_symlink":
        output.symlink_to(checkpoint / "state.sqlite")
    elif problem == "existing_output":
        output.write_text("protected")
    elif problem == "checkpoint_target":
        scratch = checkpoint
    else:
        scratch = Path("/var/lib/swingset")
    before = replay.digest(checkpoint / "state.sqlite")
    with pytest.raises((ValueError, FileNotFoundError, FileExistsError)):
        replay.run(scratch, source, f.config, f.overrides, output, receipt_sha256="test-pin")
    assert replay.digest(checkpoint / "state.sqlite") == before
    if problem == "existing_output":
        assert output.read_text() == "protected"


def test_worker_crash_rolls_back_then_uses_standard_interruption_cooldown(
    arranged, tmp_path, monkeypatch
):
    from swingset import project

    f, _, scratch, _ = arranged
    original = project.process_unit

    def crash(database, *args, **kwargs):
        database.connection.execute(
            "INSERT INTO meta VALUES ('partial_worker_output','must roll back')"
        )
        raise KeyboardInterrupt()

    monkeypatch.setattr(project, "process_unit", crash)
    with pytest.raises(KeyboardInterrupt):
        execute(arranged, tmp_path / "worker-crash.json")
    with open_database(scratch, read_only=True) as db:
        assert (
            db.connection.execute(
                "SELECT value FROM meta WHERE key='partial_worker_output'"
            ).fetchone()
            is None
        )
        assert (
            db.connection.execute(
                "SELECT count(*) FROM work_attempts WHERE outcome='running'"
            ).fetchone()[0]
            == 1
        )
    monkeypatch.setattr(project, "process_unit", original)
    recovered = execute(arranged, tmp_path / "recovery.json")
    assert recovered["recovered_admissions"] == recovered["interrupted_attempts"] == 1
    assert recovered["status"] != "current"
    f.corpus.clock.sleep(61)
    assert execute(arranged, tmp_path / "retry.json")["status"] == "current"


def test_wallclock_reserves_atomic_worker_bound_without_starting_next_unit(
    arranged, tmp_path, monkeypatch
):
    f, _, _, _ = arranged
    monkeypatch.setattr(replay, "monotonic", lambda: f.corpus.clock.now().timestamp())
    original = replay.derive_one
    calls = []

    def spend(*args, **kwargs):
        calls.append(args[2])
        result = original(*args, **kwargs)
        f.corpus.clock.sleep(11)
        return result

    monkeypatch.setattr(replay, "derive_one", spend)
    result = execute(arranged, tmp_path / "bounded.json", max_seconds=60)
    assert result["status"] == "bounded_stop" and len(calls) == 1
    assert result["elapsed_seconds"] == 11
    assert result["completed"] == 1


def test_new_scope_discovered_after_earlier_cohort_is_revisited(arranged, tmp_path, monkeypatch):
    from swingset.state.work import WorkUnit, enqueue

    original = replay.derive_one
    injected = False
    calls = []
    later = WorkUnit("project", "calendar", "new-parent-after-calendar-cohort")

    def discover(database, archive, unit, *args):
        nonlocal injected
        calls.append(unit)
        outcome = original(database, archive, unit, *args)
        if not injected:
            injected = True
            with database.transaction() as conn:
                enqueue(conn, (later,), enqueued_at="2026-09-13T08:00:00+00:00")
                replay.derivations.refresh(conn)
                conn.execute(
                    "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                    (later.stage, later.unit_kind, later.unit_id),
                )
        return outcome

    monkeypatch.setattr(replay, "derive_one", discover)
    result = execute(arranged, tmp_path / "discovered.json")
    assert result["status"] == "current"
    assert later in calls
    assert result["sweeps"] > 1
