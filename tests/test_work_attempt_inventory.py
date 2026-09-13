"""H12 durable failures remain visible and reconstructible through H11 scans."""

import json
from datetime import timedelta

from test_admission import BODY, Corpus

from swingset.fetch.archive import digest
from swingset.state.attempts import (
    begin_attempt,
    eligible,
    finish_attempt,
    recover_interrupted,
    request_retry,
)
from swingset.state.db import open_database
from swingset.state.requirement_report import inventory
from swingset.state.requirements import Requirement, scan
from swingset.state.work import WorkUnit
from swingset.state.work_fingerprints import input_fingerprint


def failed_work(db):
    c = Corpus(db)
    context = c.snapshot("missing-body")
    c.archive.blob_path(digest(BODY)).unlink()
    unit = WorkUnit("parse", "snapshot", context.snapshot_id)
    attempt = begin_attempt(db, unit, now=c.clock.now(), run_id=c.run)
    finish_attempt(
        db,
        attempt,
        outcome="unavailable",
        reason_code="artifact_unavailable",
        now=c.clock.now(),
        evidence={"kind": "body", "sha256": digest(BODY)},
    )
    return c, unit, attempt


def full_scan(db, c):
    db.connection.execute("UPDATE requirement_scan SET cursor='' WHERE singleton=1")
    while scan(db, c.clock.now(), c.run, limit=2) == 2:
        pass


def finding(conn):
    return conn.execute("SELECT * FROM findings WHERE kind='work_attempt'").fetchone()


def test_unavailable_work_requirement_survives_inventory_scan_and_lost_row(tmp_path):
    with open_database(tmp_path) as db:
        c, unit, _ = failed_work(db)
        before = dict(finding(db.connection))
        assert inventory(db.connection, c.clock.now())["changes"]["failures"] == 1
        full_scan(db, c)
        assert dict(finding(db.connection)) == before
        db.connection.execute("DELETE FROM findings WHERE finding_id=?", (before["finding_id"],))
        full_scan(db, c)
        restored = dict(finding(db.connection))
        assert restored == before
        assert restored["state"] == "unavailable"
        assert json.loads(restored["evidence_json"])["sha256"] == digest(BODY)
        assert not eligible(
            db.connection, unit, input_fingerprint(db.connection, unit), c.clock.now()
        )


def test_restored_artifact_does_not_satisfy_work_until_output_transaction_commits(tmp_path):
    with open_database(tmp_path) as db:
        c, unit, _ = failed_work(db)
        c.archive.store_body(BODY)
        full_scan(db, c)
        assert finding(db.connection)["state"] == "unavailable"
        assert not eligible(
            db.connection, unit, input_fingerprint(db.connection, unit), c.clock.now()
        )
        request_retry(
            db.connection, unit, now=c.clock.now(), reason_code="verified_artifact_restored"
        )
        attempt = begin_attempt(db, unit, now=c.clock.now(), run_id=c.run)
        assert inventory(db.connection, c.clock.now())["active_attempts"] == 1
        try:
            with db.transaction():
                finish_attempt(
                    db, attempt, outcome="succeeded", reason_code="parsed", now=c.clock.now()
                )
                raise RuntimeError("simulate rolled-back output")
        except RuntimeError:
            pass
        assert finding(db.connection)["state"] == "unavailable"
        assert inventory(db.connection, c.clock.now())["active_attempts"] == 1
        with db.transaction():
            finish_attempt(
                db, attempt, outcome="succeeded", reason_code="parsed", now=c.clock.now()
            )
        assert finding(db.connection)["state"] == "satisfied"
        assert inventory(db.connection, c.clock.now())["active_attempts"] == 0


def test_running_work_without_finding_is_visible_filtered_and_after_restart(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("first-attempt")
        unit = WorkUnit("parse", "snapshot", ctx.snapshot_id)
        attempt = begin_attempt(db, unit, now=c.clock.now(), run_id=c.run)
        identifier = Requirement(
            "work_attempt",
            json.dumps((unit.stage, unit.unit_kind, unit.unit_id), separators=(",", ":")),
            "eepro",
            "ready",
            "",
            {},
        ).identifier
        assert finding(db.connection) is None
        assert inventory(db.connection, c.clock.now())["active_attempts"] == 1
        assert (
            inventory(
                db.connection,
                c.clock.now(),
                source="eepro",
                kind="work_attempt",
                requirement_id=identifier,
            )["active_attempts"]
            == 1
        )
        assert inventory(db.connection, c.clock.now(), source="wdr")["active_attempts"] == 0
        assert (
            inventory(db.connection, c.clock.now(), kind="archive_artifact")["active_attempts"] == 0
        )
        # A legacy mirror must not make a running work attempt count twice.
        db.connection.execute(
            "INSERT INTO requirement_attempts VALUES (?,?,?,?)",
            (f"work:{attempt.attempt_id}", identifier, c.clock.now().isoformat(), "running"),
        )
    with open_database(tmp_path) as db:
        assert inventory(db.connection, c.clock.now())["active_attempts"] == 1
        assert recover_interrupted(db, now=c.clock.now()) == 1
        assert inventory(db.connection, c.clock.now())["active_attempts"] == 0
        assert finding(db.connection)["state"] == "retry_wait"
        assert not eligible(
            db.connection, unit, input_fingerprint(db.connection, unit), c.clock.now()
        )
        assert eligible(
            db.connection,
            unit,
            input_fingerprint(db.connection, unit),
            c.clock.now() + timedelta(seconds=60),
        )


def test_explicit_cli_reparse_releases_only_selected_failed_attempt_latch(tmp_path):
    from test_work_isolation import configuration

    from swingset.cli import main

    state = tmp_path / "state"
    config, overrides = configuration(tmp_path)
    with open_database(state) as db:
        c, older, _ = failed_work(db)
        context = c.snapshot("selected-retry")
        selected = WorkUnit("parse", "snapshot", context.snapshot_id)
        attempt = begin_attempt(db, selected, now=c.clock.now(), run_id=c.run)
        finish_attempt(
            db, attempt, outcome="blocked", reason_code="parse_failed", now=c.clock.now()
        )
        since = c.clock.now().isoformat()
    assert (
        main(
            [
                "reparse",
                "--state",
                str(state),
                "--config",
                str(config),
                "--overrides",
                str(overrides),
                "--kind",
                "eepro.round",
                "--since",
                since,
            ]
        )
        == 0
    )
    with open_database(state) as db:
        rows = {
            row["unit_id"]: dict(row)
            for row in db.connection.execute("SELECT * FROM work_generations WHERE stage='parse'")
        }
        assert rows[older.unit_id]["retry_generation"] == 0
        assert rows[selected.unit_id]["retry_generation"] == 1
        assert rows[selected.unit_id]["retry_reason"] == "operator_reparse"
        assert eligible(
            db.connection, selected, input_fingerprint(db.connection, selected), c.clock.now()
        )
        assert db.connection.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0] == 2
