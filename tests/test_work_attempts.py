"""Offline retry isolation, restart, input identity, and commit-boundary checks."""

from datetime import UTC, datetime, timedelta

import pytest

from swingset.schedule.watches import upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE
from swingset.state.attempts import (
    SupersededWorkError,
    begin_attempt,
    eligible,
    finish_attempt,
    latest_attempt,
    recover_interrupted,
    request_retry,
)
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, complete, enqueue, next_work, runnable_exists
from swingset.state.work_fingerprints import input_fingerprint

NOW = datetime(2026, 9, 13, tzinfo=UTC)
BAD = WorkUnit("parse", "snapshot", "broken")
GOOD = WorkUnit("project", "dancer", "healthy")


def seed(db, *units):
    run = db.start_run(NOW)
    with db.transaction() as conn:
        enqueue(conn, units, enqueued_at=NOW.isoformat())
    return run


def failed(db, run, *, outcome="blocked", retry_at=None):
    attempt = begin_attempt(db, BAD, now=NOW, run_id=run, fingerprint="same-input")
    finish_attempt(
        db,
        attempt,
        outcome=outcome,
        reason_code="fixture_failure",
        now=NOW,
        retry_at=retry_at,
        evidence={"required_digest": "a" * 64},
    )
    return attempt


def test_blocked_parse_does_not_starve_independent_work_or_spin_after_restart(tmp_path):
    with open_database(tmp_path) as db:
        run = seed(db, BAD, GOOD)
        attempt = failed(db, run)
        assert latest_attempt(db.connection, BAD)["outcome"] == "blocked"
        assert (
            next_work(db.connection, "parse", now=NOW, fingerprint=lambda _: "same-input") is None
        )
        assert runnable_exists(db.connection, now=NOW, fingerprint=lambda _: "same-input")
        assert next_work(db.connection, "project", now=NOW) == GOOD
        with db.transaction():
            good_attempt = begin_attempt(db, GOOD, now=NOW, run_id=run)
            complete(
                db,
                GOOD,
                lambda conn: conn.execute("INSERT INTO meta VALUES ('healthy-output','committed')"),
            )
            finish_attempt(
                db, good_attempt, outcome="succeeded", reason_code="output_committed", now=NOW
            )
        assert not runnable_exists(db.connection, now=NOW, fingerprint=lambda _: "same-input")
    with open_database(tmp_path) as db:
        assert not eligible(db.connection, BAD, "same-input", NOW + timedelta(days=5))
        assert (
            db.connection.execute("SELECT enqueued_at FROM pending_work").fetchone()[0]
            == attempt.work_token
        )
        assert (
            db.connection.execute("SELECT value FROM meta WHERE key='healthy-output'").fetchone()[0]
            == "committed"
        )


def test_identical_reenqueue_does_not_unlock_a_deterministic_failure(tmp_path):
    with open_database(tmp_path) as db:
        run = seed(db, BAD)
        failed(db, run)
        enqueue(db.connection, [BAD], enqueued_at=(NOW + timedelta(hours=1)).isoformat())
        assert not eligible(db.connection, BAD, "same-input", NOW + timedelta(hours=1))
        assert eligible(db.connection, BAD, "different-bytes-or-recipe", NOW)


def test_explicit_verified_recovery_can_unlock_same_digest(tmp_path):
    with open_database(tmp_path) as db:
        run = seed(db, BAD)
        failed(db, run, outcome="unavailable")
        assert not eligible(db.connection, BAD, "same-input", NOW)
        request_retry(db.connection, BAD, now=NOW, reason_code="verified_artifact_restored")
        assert eligible(db.connection, BAD, "same-input", NOW)


def test_transient_retry_is_deadline_bound_and_survives_restart(tmp_path):
    deadline = NOW + timedelta(minutes=5)
    with open_database(tmp_path) as db:
        failed(db, seed(db, BAD), outcome="transient", retry_at=deadline)
    with open_database(tmp_path) as db:
        assert not eligible(db.connection, BAD, "same-input", deadline - timedelta(microseconds=1))
        assert eligible(db.connection, BAD, "same-input", deadline)
        assert eligible(db.connection, BAD, "new-input", NOW)


def test_process_death_is_recovered_once_with_a_finite_retry_deadline(tmp_path):
    with open_database(tmp_path) as db:
        run = seed(db, BAD)
        begin_attempt(db, BAD, now=NOW, run_id=run, fingerprint="same-input")
    with open_database(tmp_path) as db:
        assert recover_interrupted(db, now=NOW) == 1
        assert recover_interrupted(db, now=NOW) == 0
        assert not eligible(db.connection, BAD, "same-input", NOW)
        assert eligible(db.connection, BAD, "same-input", NOW + timedelta(minutes=1))
        assert latest_attempt(db.connection, BAD)["outcome"] == "interrupted"


def test_handled_parser_failure_restores_deleted_pending_timestamp_token(tmp_path):
    with open_database(tmp_path) as db:
        run = seed(db, BAD)
        attempt = begin_attempt(db, BAD, now=NOW, run_id=run, fingerprint="same-input")
        with db.transaction():
            complete(db, BAD, lambda _: None)
            finish_attempt(
                db, attempt, outcome="blocked", reason_code="parse_guard_failed", now=NOW
            )
        assert (
            db.connection.execute("SELECT enqueued_at FROM pending_work").fetchone()[0]
            == attempt.work_token
        )
        assert not eligible(db.connection, BAD, "same-input", NOW)


def test_success_and_output_roll_back_together_and_retry_commits_once(tmp_path):
    with open_database(tmp_path) as db:
        run = seed(db, GOOD)
        attempt = begin_attempt(db, GOOD, now=NOW, run_id=run)
        with pytest.raises(RuntimeError, match="crash"):
            with db.transaction():
                complete(
                    db,
                    GOOD,
                    lambda conn: conn.execute("INSERT INTO meta VALUES ('atomic-output','yes')"),
                )
                finish_attempt(
                    db, attempt, outcome="succeeded", reason_code="output_committed", now=NOW
                )
                raise RuntimeError("crash before commit")
        assert not db.connection.execute("SELECT 1 FROM meta WHERE key='atomic-output'").fetchone()
        assert latest_attempt(db.connection, GOOD)["outcome"] == "running"
        assert db.connection.execute("SELECT 1 FROM pending_work").fetchone()
        with db.transaction():
            complete(
                db,
                GOOD,
                lambda conn: conn.execute("INSERT INTO meta VALUES ('atomic-output','yes')"),
            )
            finish_attempt(
                db, attempt, outcome="succeeded", reason_code="output_committed", now=NOW
            )
        assert latest_attempt(db.connection, GOOD)["outcome"] == "succeeded"
        assert not db.connection.execute("SELECT 1 FROM pending_work").fetchone()


def test_new_parse_invalidation_fences_old_completion_even_with_identical_timestamp(tmp_path):
    with open_database(tmp_path) as db:
        run = seed(db, BAD)
        attempt = begin_attempt(db, BAD, now=NOW, run_id=run)
        enqueue(db.connection, [BAD], enqueued_at=NOW.isoformat())
        with pytest.raises(SupersededWorkError):
            with db.transaction():
                complete(
                    db,
                    BAD,
                    lambda conn: conn.execute("INSERT INTO meta VALUES ('stale-output','bad')"),
                )
                finish_attempt(
                    db, attempt, outcome="succeeded", reason_code="output_committed", now=NOW
                )
        assert not db.connection.execute("SELECT 1 FROM meta WHERE key='stale-output'").fetchone()
        finish_attempt(db, attempt, outcome="superseded", reason_code="newer_work_pending", now=NOW)
        assert db.connection.execute("SELECT 1 FROM pending_work").fetchone()
        assert next_work(db.connection, "parse", now=NOW, exclude={BAD}) is None
        assert next_work(db.connection, "parse", now=NOW) is None
        request_retry(db.connection, BAD, now=NOW, reason_code="operator_retry")
        assert next_work(db.connection, "parse", now=NOW) == BAD


def test_superseded_deleted_work_does_not_retry_identical_inputs_each_cycle(tmp_path):
    with open_database(tmp_path) as db:
        attempt = begin_attempt(db, BAD, now=NOW, run_id=seed(db, BAD), fingerprint="old-snapshot")
        with db.transaction():
            complete(db, BAD, lambda _: None)
            finish_attempt(
                db, attempt, outcome="superseded", reason_code="snapshot_superseded", now=NOW
            )
    with open_database(tmp_path) as db:
        for offset in range(5):
            assert (
                next_work(
                    db.connection,
                    "parse",
                    now=NOW + timedelta(days=offset),
                    fingerprint=lambda _: "old-snapshot",
                )
                is None
            )
        assert (
            next_work(db.connection, "parse", now=NOW, fingerprint=lambda _: "changed-policy")
            == BAD
        )


def test_recovery_of_old_running_attempt_cannot_revive_newer_completed_work(tmp_path):
    with open_database(tmp_path) as db:
        run = seed(db, BAD)
        old = begin_attempt(db, BAD, now=NOW, run_id=run, fingerprint="old-input")
        newer = begin_attempt(db, BAD, now=NOW, run_id=run, fingerprint="new-input")
        with db.transaction():
            complete(db, BAD, lambda _: None)
            finish_attempt(db, newer, outcome="succeeded", reason_code="output_committed", now=NOW)
    with open_database(tmp_path) as db:
        assert recover_interrupted(db, now=NOW) == 1
        assert not db.connection.execute("SELECT 1 FROM pending_work").fetchone()
        assert latest_attempt(db.connection, BAD)["outcome"] == "succeeded"
        assert (
            db.connection.execute(
                "SELECT outcome FROM work_attempts WHERE attempt_id=?", (old.attempt_id,)
            ).fetchone()[0]
            == "superseded"
        )
        assert not db.connection.execute(
            "SELECT 1 FROM findings WHERE kind='work_attempt' AND closed_at IS NULL"
        ).fetchone()


@pytest.mark.parametrize(
    "outcome,retry",
    [("transient", None), ("transient", NOW), ("blocked", NOW + timedelta(seconds=1))],
)
def test_invalid_retry_contract_does_not_finish_attempt(tmp_path, outcome, retry):
    with open_database(tmp_path) as db:
        attempt = begin_attempt(db, BAD, now=NOW, run_id=seed(db, BAD), fingerprint="same-input")
        with pytest.raises(ValueError):
            finish_attempt(
                db, attempt, outcome=outcome, reason_code="failure", now=NOW, retry_at=retry
            )
        assert latest_attempt(db.connection, BAD)["outcome"] == "running"


def snapshot(db):
    run = seed(db, BAD)
    watch = SOURCE.watch(123)
    upsert_watch(db.connection, watch, NOW)
    db.connection.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES (?,?,?,?,?,200,?,12,1,?,'Found')",
        (BAD.unit_id, watch.watch_id, "POST", watch.url, NOW.isoformat(), "a" * 64, run),
    )
    return run, watch


def test_parse_fingerprint_ignores_own_failure_and_unrelated_progress(tmp_path):
    with open_database(tmp_path) as db:
        _, watch = snapshot(db)
        before = input_fingerprint(db.connection, BAD)
        db.connection.execute(
            "UPDATE snapshots SET parse_status='failed',extract_status='failed',parser_version='old',extract_version='old'"
        )
        db.connection.execute("UPDATE revisions SET value=value+1")
        enqueue(db.connection, [BAD], enqueued_at=(NOW + timedelta(days=1)).isoformat())
        assert input_fingerprint(db.connection, BAD) == before
        db.connection.execute(
            "UPDATE watches SET archive_url='https://archive.example/capture' WHERE watch_id=?",
            (watch.watch_id,),
        )
        assert input_fingerprint(db.connection, BAD) != before


def test_missing_parser_has_stable_fingerprint_and_does_not_block_healthy_selection(tmp_path):
    with open_database(tmp_path) as db:
        run, _ = snapshot(db)
        db.connection.execute("UPDATE watches SET parser='unimplemented.page'")
        original = input_fingerprint(db.connection, BAD)
        attempt = begin_attempt(db, BAD, now=NOW, run_id=run)
        finish_attempt(db, attempt, outcome="blocked", reason_code="unknown_parser", now=NOW)
        enqueue(db.connection, [GOOD], enqueued_at=NOW.isoformat())
        assert input_fingerprint(db.connection, BAD) == original
        assert next_work(db.connection, "parse", now=NOW) is None
        assert next_work(db.connection, "project", now=NOW) == GOOD
        db.connection.execute("UPDATE watches SET parser='wsdc_registry.dancer'")
        assert input_fingerprint(db.connection, BAD) != original
        assert next_work(db.connection, "parse", now=NOW) == BAD


@pytest.mark.parametrize("change", ["body", "recipe", "policy"])
def test_parse_changed_evidence_recipe_or_admission_policy_reopens_retry(tmp_path, change):
    with open_database(tmp_path) as db:
        run, _ = snapshot(db)
        original = input_fingerprint(db.connection, BAD)
        attempt = begin_attempt(db, BAD, now=NOW, run_id=run)
        finish_attempt(db, attempt, outcome="blocked", reason_code="guard_failed", now=NOW)
        if change == "body":
            db.connection.execute("UPDATE snapshots SET body_sha256=?", ("b" * 64,))
        elif change == "recipe":
            db.connection.execute(
                "INSERT INTO accepted_inputs VALUES ('pipeline','version/repository','new-code')"
            )
        else:
            db.connection.execute(
                "INSERT INTO admission_policies(page_kind,contract_version,policy_revision) VALUES ('wsdc_registry.dancer','4','new-policy')"
            )
        current = input_fingerprint(db.connection, BAD)
        assert current != original
        assert next_work(db.connection, "parse", now=NOW) == BAD


def test_project_fingerprint_is_scoped_to_its_own_observations(tmp_path):
    with open_database(tmp_path) as db:
        run, watch = snapshot(db)
        first = input_fingerprint(db.connection, GOOD)
        db.connection.execute(
            "INSERT INTO observations VALUES ('unrelated',?,?, 'x','dancer','other',0,'1','1','{}')",
            (watch.watch_id, BAD.unit_id),
        )
        assert input_fingerprint(db.connection, GOOD) == first
        db.connection.execute(
            "INSERT INTO observations VALUES ('related',?,?, 'x','dancer','healthy',1,'1','1','{}')",
            (watch.watch_id, BAD.unit_id),
        )
        assert input_fingerprint(db.connection, GOOD) != first
