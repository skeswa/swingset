"""Disposable input rehearsal uses real parsers and retains operating interlocks."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_admission import Corpus

from journal.tools.runtime import rehearse_extension_inputs as helper
from journal.tools.runtime import rehearse_extension_migration as verifier
from swingset.backup.checkpoint import create_checkpoint
from swingset.state import db as db_module
from swingset.state.controls import Selector, change_control
from swingset.state.db import open_database


def test_external_overrides_must_match_the_complete_frozen_csv_inventory(tmp_path, monkeypatch):
    monkeypatch.delenv("SWINGSET_REVISION", raising=False)
    source = Path(__file__).parents[1]
    overrides = tmp_path / "overrides"
    shutil.copytree(source / "overrides", overrides)
    assert helper.external_inputs(source, source / "config", overrides)
    (overrides / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    with pytest.raises(ValueError, match="inventory differs"):
        helper.external_inputs(source, source / "config", overrides)


@pytest.mark.parametrize(
    "path",
    ["/var/lib/swingset/scratch", "checkpoint/child", "source/child", "other/checkpoints/scratch"],
)
def test_live_or_retained_destination_is_rejected(tmp_path, path):
    with pytest.raises(ValueError):
        helper.scratch_path(
            tmp_path / path if not path.startswith("/") else Path(path),
            tmp_path / "checkpoint",
            tmp_path / "source",
        )


def test_drain_runs_real_parse_once_without_touching_hold_budget_or_policy(tmp_path):
    with open_database(tmp_path) as db:
        corpus = Corpus(db)
        corpus.snapshot("one")
        (tmp_path / "operator-hold").write_text("retained\n")
        db.connection.execute("INSERT INTO hosts(host) VALUES ('eepro.com')")
        db.connection.execute(
            "INSERT INTO host_budget VALUES ('eepro.com','2026-09-17',10,2934701)"
        )
        before = helper.protected(db.connection, 0)
        report = helper.drain(
            db,
            SimpleNamespace(),
            corpus.clock,
            max_seconds=60,
            max_units=1,
            selection_window=helper.selection_window,
        )
        assert report["attempted"] == 1
        assert next(iter(report["outcomes"])).startswith("parse/")
        assert db.connection.execute("SELECT 1 FROM source_generations").fetchone()
        assert helper.protected(db.connection, 0) == before
        assert (tmp_path / "operator-hold").read_text() == "retained\n"
        assert report["all_work_current"] is False
        assert report["time_limit_basis"] == "selection_budget_not_hard_wall_deadline"
        assert report["worker_overrun_possible"] is True
        assert db.connection.execute(
            "SELECT finished_at FROM runs WHERE run_id=?", (report["run_id"],)
        ).fetchone()[0]


def test_source_pause_remains_effective_in_offline_replay(tmp_path):
    with open_database(tmp_path) as db:
        corpus = Corpus(db)
        corpus.snapshot("held")
        change_control(
            tmp_path,
            selector=Selector("source", "eepro"),
            paused=True,
            actor="offline-test",
            reason="preserve hold",
            now=corpus.clock.now(),
        )
        before = helper.protected(db.connection, 0)
        report = helper.drain(
            db,
            SimpleNamespace(),
            corpus.clock,
            max_seconds=60,
            max_units=1,
            selection_window=helper.selection_window,
        )
        assert report["attempted"] == 0
        assert helper.protected(db.connection, 0) == before
        assert (
            db.connection.execute(
                "SELECT count(*) FROM pending_work WHERE stage='parse'"
            ).fetchone()[0]
            == 1
        )


def test_changed_named_null_id_judge_is_reported_without_inventing_an_id(tmp_path):
    with open_database(tmp_path) as db:
        original = {"judge": ["No Registry Number", None]}
        report = helper.judge_continuity(db.connection, original)
        assert report == dict(
            original_named=1,
            original_null_ids=1,
            preserved=0,
            changed_or_missing=1,
            examples=["judge"],
            requires_review=True,
        )


def test_retained_artifact_link_or_changed_bytes_are_rejected(tmp_path):
    checkpoint, scratch = tmp_path / "saved", tmp_path / "scratch"
    checkpoint.mkdir()
    scratch.mkdir()
    (checkpoint / "checkpoint.json").write_text("{}")
    (checkpoint / "operator-hold").write_text("held")
    marker = dict(
        checkpoint=str(checkpoint),
        checkpoint_sha256=helper.sha(checkpoint / "checkpoint.json"),
        retained_files={"operator-hold": {"sha256": helper.sha(checkpoint / "operator-hold")}},
    )
    (scratch / "operator-hold").symlink_to(checkpoint / "operator-hold")
    with pytest.raises(ValueError, match="changed or linked"):
        helper.verify_files(scratch, marker)
    (scratch / "operator-hold").unlink()
    (scratch / "operator-hold").write_text("not held")
    with pytest.raises(ValueError, match="changed or linked"):
        helper.verify_files(scratch, marker)


def test_original_admission_mutation_is_detected_while_new_offline_attempts_are_allowed(tmp_path):
    with open_database(tmp_path) as db:
        corpus = Corpus(db)
        corpus.snapshot("one")
        helper.drain(
            db,
            SimpleNamespace(),
            corpus.clock,
            max_seconds=60,
            max_units=1,
            selection_window=helper.selection_window,
        )
        highwater = db.connection.execute("SELECT max(rowid) FROM execution_admissions").fetchone()[
            0
        ]
        before = helper.protected(db.connection, highwater)
        db.connection.execute("UPDATE execution_admissions SET outcome='changed'")
        assert helper.protected(db.connection, highwater) != before
        assert json.dumps(before)


def test_prepare_copies_real_schema14_checkpoint_without_shared_artifacts(tmp_path, monkeypatch):
    state, checkpoint, scratch = (tmp_path / name for name in ("state", "checkpoint", "scratch"))
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 14)
    with open_database(state) as db:
        corpus = Corpus(db)
        corpus.snapshot("retained")
        (state / "operator-hold").write_text("held")
        candidate = state / "candidates" / helper.CANDIDATE
        candidate.mkdir(parents=True)
        (candidate / "BUILT").write_text("retained")
        (candidate / "PUBLISHED").write_text(
            json.dumps(dict(commit=helper.BASELINE, candidate_id=helper.CANDIDATE))
        )
        (state / "baseline").symlink_to(candidate)
        saved = create_checkpoint(
            state, db.connection, checkpoint, schema_version=14, versions={}, input_bundle_hash=None
        )
    monkeypatch.setattr(helper, "CHECKPOINT_SHA", saved.manifest_hash)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 28)
    original = helper.sha(checkpoint / "state.sqlite")
    marker = helper.prepare(checkpoint, scratch, Path(__file__).parents[1], verifier)
    assert marker["schema"] == 28
    assert helper.sha(checkpoint / "state.sqlite") == original
    assert (scratch / helper.MARKER).exists()
    helper.verify_files(scratch, marker, check_original=True)
    with open_database(scratch, read_only=True) as db:
        assert db.schema_version == 28
        assert (
            db.connection.execute(
                "SELECT count(*) FROM pending_work WHERE stage='parse'"
            ).fetchone()[0]
            == 1
        )
    blob = next((scratch / "blobs").rglob("*.gz"), None)
    if blob is None:
        blob = next(path for path in (scratch / "blobs").rglob("*") if path.is_file())
    relative = blob.relative_to(scratch)
    assert blob.stat().st_ino != (checkpoint / relative).stat().st_ino


def test_ordinary_parse_failure_remains_a_finding_without_retry_under_unchanged_policy(tmp_path):
    with open_database(tmp_path) as db:
        corpus = Corpus(db)
        corpus.snapshot("broken", b"not a result sheet")
        first = helper.drain(
            db,
            SimpleNamespace(),
            corpus.clock,
            max_seconds=60,
            max_units=1,
            selection_window=helper.selection_window,
        )
        assert first["attempted"] == 1
        assert db.connection.execute("SELECT 1 FROM findings WHERE closed_at IS NULL").fetchone()
        # First staging creates the ordinary shadow policy, a relevant input
        # change. Its next attempt must then settle under that unchanged policy.
        helper.drain(
            db,
            SimpleNamespace(),
            corpus.clock,
            max_seconds=60,
            max_units=1,
            selection_window=helper.selection_window,
        )
        attempts = db.connection.execute(
            "SELECT count(*) FROM work_attempts WHERE stage='parse'"
        ).fetchone()[0]
        helper.drain(
            db,
            SimpleNamespace(),
            corpus.clock,
            max_seconds=60,
            max_units=1,
            selection_window=helper.selection_window,
        )
        assert (
            db.connection.execute(
                "SELECT count(*) FROM work_attempts WHERE stage='parse'"
            ).fetchone()[0]
            == attempts
        )


def test_hold_change_at_turn_boundary_prevents_any_worker(tmp_path):
    with open_database(tmp_path) as db:
        corpus = Corpus(db)
        corpus.snapshot("held")

        def changed_hold():
            raise ValueError("hold changed")

        with pytest.raises(ValueError, match="hold changed"):
            helper.drain(
                db,
                SimpleNamespace(),
                corpus.clock,
                max_seconds=60,
                max_units=1,
                selection_window=helper.selection_window,
                verify_turn=changed_hold,
            )
        assert db.connection.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 0
        assert (
            db.connection.execute(
                "SELECT count(*) FROM pending_work WHERE stage='parse'"
            ).fetchone()[0]
            == 1
        )
