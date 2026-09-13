import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest

from swingset.cli import main
from swingset.fetch.archive import Archive
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database
from swingset.state.findings import Finding, replace_findings
from swingset.state.requirement_report import human_report, inventory
from swingset.state.requirements import (
    Requirement,
    capture_cohort,
    reconcile_requirement,
    record_attempt,
    scan,
)

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def prepare(db):
    db.connection.execute(
        "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run',?,1)", (NOW.isoformat(),)
    )


def test_deleted_acquisition_row_is_recreated_from_retained_watch_without_repair(tmp_path):
    with open_database(tmp_path) as db:
        prepare(db)
        spec = WatchSpec(
            "", "eepro", "round", "GET", "https://eepro.com/results/old/round.html", "eepro.round"
        )
        upsert_watch(db.connection, spec, NOW - timedelta(days=3000))
        assert scan(db, NOW, "run", limit=1) == 1
        original = dict(
            db.connection.execute(
                "SELECT * FROM findings WHERE owner_kind='requirement'"
            ).fetchone()
        )
        assert original["state"] == "ready"
        db.connection.execute("DELETE FROM findings WHERE finding_id=?", (original["finding_id"],))
        scan(db, NOW, "run", limit=1)
        scan(db, NOW, "run", limit=1)
        recovered = db.connection.execute("SELECT finding_id,state FROM findings").fetchone()
        assert tuple(recovered) == (original["finding_id"], "ready")
        assert (
            db.connection.execute("SELECT count(*) FROM requirement_transitions").fetchone()[0] == 1
        )
        assert db.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0


def test_lost_review_finding_rebuilds_from_accepted_owner_evidence(tmp_path):
    with open_database(tmp_path) as db:
        prepare(db)
        finding = Finding(
            "unrecognized",
            "event",
            "historical",
            "warning",
            "Review evidence",
            {"snapshot": "saved"},
        )
        replace_findings(
            db.connection,
            owner_kind="event",
            owner_id="historical",
            findings=(finding,),
            opened_at=NOW.isoformat(),
            run_id="run",
        )
        key = db.connection.execute("SELECT finding_id FROM findings").fetchone()[0]
        db.connection.execute("DELETE FROM findings")
        scan(db, NOW, "run")
        assert db.connection.execute("SELECT finding_id FROM findings").fetchone()[0] == key
        replace_findings(
            db.connection,
            owner_kind="event",
            owner_id="historical",
            findings=(),
            opened_at=(NOW + timedelta(hours=1)).isoformat(),
            run_id="run",
        )
        scan(db, NOW + timedelta(hours=1), "run")
        assert db.connection.execute("SELECT state FROM findings").fetchone()[0] == "satisfied"


def test_cohort_retries_reopening_retirement_discovery_and_restart_balance(tmp_path):
    with open_database(tmp_path) as db:
        prepare(db)
        requirement = Requirement(
            "round_observations", "old", "eepro", "ready", "fetch listed round", {"watch": "old"}
        )
        key = reconcile_requirement(db.connection, requirement, NOW, "run")
        capture_cohort(db.connection, "history", NOW)
        record_attempt(db.connection, key, "attempt", NOW + timedelta(seconds=1), "failed")
        record_attempt(db.connection, key, "attempt", NOW + timedelta(seconds=1), "failed")
        ready = inventory(db.connection, NOW + timedelta(seconds=2), since=NOW)
        assert ready["changes"]["attempts"] == 1
        assert ready["cohorts"][0]["completion_percent"] == 0
        reconcile_requirement(
            db.connection,
            replace(requirement, state="satisfied"),
            NOW + timedelta(seconds=3),
            "run",
        )
        assert (
            inventory(db.connection, NOW + timedelta(seconds=4))["cohorts"][0]["completion_percent"]
            == 100
        )
        reconcile_requirement(db.connection, requirement, NOW + timedelta(seconds=5), "run")
        assert (
            inventory(db.connection, NOW + timedelta(seconds=6))["cohorts"][0]["completion_percent"]
            == 0
        )
        reconcile_requirement(
            db.connection,
            replace(requirement, subject_key="new"),
            NOW + timedelta(seconds=7),
            "run",
        )
        capture_cohort(db.connection, "history", NOW + timedelta(seconds=8))
        report = inventory(db.connection, NOW + timedelta(seconds=9), since=NOW)
        assert report["cohorts"][0]["total"] == 1
        assert report["cohorts"][0]["outside_cohort"] == 1
        reconcile_requirement(
            db.connection,
            replace(requirement, state="out_of_scope"),
            NOW + timedelta(seconds=10),
            "run",
        )
    with open_database(tmp_path) as db:
        report = inventory(db.connection, NOW + timedelta(seconds=11), since=NOW)
        changes = report["changes"]
        assert (
            changes["opening_unmet"]
            + changes["opened"]
            + changes["reopened"]
            - changes["satisfied"]
            - changes["retired"]
            == changes["closing_unmet"]
            == 1
        )
        assert changes["attempts"] == 1
        assert report["cohorts"][0]["retired"] == 1
        assert report["cohorts"][0]["completion_percent"] is None


def test_cohort_new_work_excludes_preexisting_closed_and_other_scopes_at_same_time(tmp_path):
    requirement = Requirement("round_observations", "member", "eepro", "ready", "fetch", {})
    with open_database(tmp_path) as db:
        prepare(db)
        reconcile_requirement(db.connection, requirement, NOW, "run")
        old_closed = replace(requirement, subject_key="previously-satisfied", state="satisfied")
        old_key = reconcile_requirement(db.connection, old_closed, NOW, "run")
        reconcile_requirement(
            db.connection,
            replace(requirement, subject_key="retired", state="out_of_scope"),
            NOW,
            "run",
        )
        capture_cohort(db.connection, "scoped", NOW, source="eepro", kind="round_observations")
        initial = inventory(db.connection, NOW)["cohorts"][0]
        assert initial["total"] == 1 and initial["outside_cohort"] == 0
        for item in (
            replace(requirement, subject_key="later-same-clock"),
            replace(requirement, subject_key="other-source", source="scoringdance"),
            replace(requirement, subject_key="other-kind", kind="artifact"),
            replace(requirement, subject_key="other-policy", policy_version="future-policy"),
        ):
            reconcile_requirement(db.connection, item, NOW, "run")
        # Reopening and recreating a preexisting row must not become discovery.
        db.connection.execute("DELETE FROM findings WHERE finding_id=?", (old_key,))
        reconcile_requirement(
            db.connection, replace(old_closed, state="ready"), NOW + timedelta(seconds=1), "run"
        )
        capture_cohort(
            db.connection,
            "scoped",
            NOW + timedelta(seconds=2),
            source="eepro",
            kind="round_observations",
        )
        result = inventory(db.connection, NOW + timedelta(seconds=3))
        cohort = result["cohorts"][0]
        assert cohort["total"] == 1
        assert cohort["first_open_transition_cutoff"] == initial["first_open_transition_cutoff"]
        assert cohort["outside_cohort"] == cohort["outside_cohort_known_new"] == 1
        assert cohort["outside_cohort_uncertain"] == 0
        assert "outside_cohort_known_new" in human_report(result)
    with open_database(tmp_path) as db:
        assert (
            inventory(db.connection, NOW + timedelta(seconds=4))["cohorts"][0]["outside_cohort"]
            == 1
        )


def test_legacy_cohort_capture_order_stays_unknown_after_migration(tmp_path, monkeypatch):
    from swingset.state import db as database_module

    requirement = Requirement("round_observations", "member", "eepro", "ready", "fetch", {})
    with monkeypatch.context() as patcher:
        patcher.setattr(database_module, "SCHEMA_VERSION", 9)
        with open_database(tmp_path) as db:
            prepare(db)
            member = reconcile_requirement(db.connection, requirement, NOW, "run")
            reconcile_requirement(
                db.connection,
                replace(requirement, subject_key="same-time-closed", state="satisfied"),
                NOW,
                "run",
            )
            db.connection.execute(
                "INSERT INTO requirement_cohorts VALUES ('legacy',?,?,NULL,NULL,1)",
                (NOW.isoformat(), requirement.policy_version),
            )
            db.connection.execute(
                "INSERT INTO requirement_cohort_members VALUES ('legacy',?)", (member,)
            )
            reconcile_requirement(
                db.connection,
                replace(requirement, subject_key="known-later"),
                NOW + timedelta(seconds=1),
                "run",
            )
            before = inventory(db.connection, NOW + timedelta(seconds=2))["cohorts"][0]
            assert before["outside_cohort"] is None
            assert before["outside_cohort_known_new"] == before["outside_cohort_uncertain"] == 1
    with open_database(tmp_path) as db:
        assert (
            db.connection.execute(
                "SELECT first_open_transition_cutoff FROM requirement_cohorts"
            ).fetchone()[0]
            is None
        )
        capture_cohort(db.connection, "legacy", NOW + timedelta(seconds=3))
        after = inventory(db.connection, NOW + timedelta(seconds=4))["cohorts"][0]
        assert after == before


def test_deleted_satisfied_requirement_reopening_preserves_progress_ledger(tmp_path):
    with open_database(tmp_path) as db:
        prepare(db)
        requirement = Requirement("round_observations", "old", "eepro", "ready", "fetch", {})
        key = reconcile_requirement(db.connection, requirement, NOW, "run")
        reconcile_requirement(
            db.connection,
            replace(requirement, state="satisfied"),
            NOW + timedelta(seconds=1),
            "run",
        )
        db.connection.execute("DELETE FROM findings WHERE finding_id=?", (key,))
        reconcile_requirement(db.connection, requirement, NOW + timedelta(seconds=2), "run")
        report = inventory(db.connection, NOW + timedelta(seconds=3), since=NOW)
        assert report["changes"]["reopened"] == 1
        assert report["changes"]["opening_unmet"] == 0


def test_paused_unknown_cohorts_have_no_percentage_and_doctor_stays_local(tmp_path, capsys):
    with open_database(tmp_path) as db:
        prepare(db)
        requirement = Requirement("round_observations", "old", "eepro", "ready", "fetch", {})
        key = reconcile_requirement(db.connection, requirement, NOW, "run")
        capture_cohort(db.connection, "unknown", NOW, bounded=False)
        capture_cohort(db.connection, "paused", NOW)
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason) VALUES ('source','eepro',NULL,'review')"
        )
        report = inventory(db.connection, NOW + timedelta(days=1))
        assert report["stale"] and report["paused"] == 1
        assert all(
            row["completion_percent"] is None and row["eta"] is None for row in report["cohorts"]
        )
        assert key in human_report(report)
        assert main(["doctor", "--state", str(tmp_path), "--json", "--requirement", key]) == 0
        assert key in capsys.readouterr().out


def test_doctor_watch_refreshes_without_writer_lock(tmp_path, capsys):
    with open_database(tmp_path):
        with patch("swingset.cli.time.sleep", side_effect=KeyboardInterrupt) as sleep:
            assert main(["doctor", "--state", str(tmp_path), "--watch", "--interval", "0.25"]) == 0
        sleep.assert_called_once_with(0.25)
    assert "Requirements at" in capsys.readouterr().out


def test_doctor_watch_reads_updated_snapshot_after_each_configured_interval(tmp_path, capsys):
    from swingset.clock import FakeClock

    clock = FakeClock(NOW)
    with open_database(tmp_path) as db:
        prepare(db)
        requirement = Requirement("round_observations", "old", "eepro", "ready", "first action", {})
        reconcile_requirement(db.connection, requirement, NOW, "run")
        calls = []

        def tick(seconds):
            calls.append(seconds)
            if len(calls) > 1:
                raise KeyboardInterrupt
            clock.sleep(seconds)
            reconcile_requirement(
                db.connection,
                replace(requirement, next_action="updated action"),
                clock.now(),
                "run",
            )

        with (
            patch("swingset.cli.time.sleep", side_effect=tick),
            patch("swingset.cli.SystemClock.now", side_effect=clock.now),
        ):
            assert (
                main(
                    ["doctor", "--state", str(tmp_path), "--watch", "--json", "--interval", "0.25"]
                )
                == 0
            )
    output = capsys.readouterr().out
    decoder = json.JSONDecoder()
    frames = []
    while output.strip():
        output = output.lstrip()
        frame, end = decoder.raw_decode(output)
        frames.append(frame["requirements"])
        output = output[end:]
    assert calls == [0.25, 0.25]
    assert len(frames) == 2
    assert frames[0]["requirements"][0]["next_action"] == "first action"
    assert frames[1]["requirements"][0]["next_action"] == "updated action"
    assert frames[1]["snapshot_at"] == (NOW + timedelta(seconds=0.25)).isoformat()


def test_scan_cursor_survives_restart_and_bounds_each_page(tmp_path):
    with open_database(tmp_path) as db:
        prepare(db)
        for number in range(4):
            upsert_watch(
                db.connection,
                WatchSpec(
                    "",
                    "eepro",
                    "round",
                    "GET",
                    f"https://eepro.com/results/{number}.html",
                    "eepro.round",
                ),
                NOW,
            )
        assert scan(db, NOW, "run", limit=2) == 2
        cursor = db.connection.execute("SELECT cursor FROM requirement_scan").fetchone()[0]
        assert cursor
    with open_database(tmp_path) as db:
        assert scan(db, NOW, "run", limit=2) == 2
        assert db.connection.execute("SELECT count(*) FROM findings").fetchone()[0] == 4
        for _ in range(3):
            scan(db, NOW, "run", limit=2)
        assert not db.connection.execute("SELECT cursor FROM requirement_scan").fetchone()[0]


def test_retiring_source_scope_retains_history_and_never_counts_as_repair(tmp_path):
    with open_database(tmp_path) as db:
        prepare(db)
        spec = WatchSpec(
            "", "eepro", "round", "GET", "https://eepro.com/results/old.html", "eepro.round"
        )
        upsert_watch(db.connection, spec, NOW)
        scan(db, NOW, "run")
        db.connection.execute("DELETE FROM watches")
        scan(db, NOW + timedelta(hours=1), "run")
        report = inventory(db.connection, NOW + timedelta(hours=2), since=NOW)
        assert report["states"] == {"out_of_scope": 1}
        assert report["changes"]["retired"] == 1
        assert report["changes"]["satisfied"] == 0


def test_no_progress_alert_uses_clock_and_pause_only_suppresses_eligible_alarm(tmp_path):
    with open_database(tmp_path) as db:
        prepare(db)
        reconcile_requirement(
            db.connection,
            Requirement(
                "round_observations",
                "old",
                "eepro",
                "ready",
                "fetch known URL",
                {"url": "archived"},
            ),
            NOW,
            "run",
        )
        later = NOW + timedelta(hours=2)
        report = inventory(db.connection, later)
        assert report["alerts"][0]["next_action"] == "fetch known URL"
        assert report["oldest_unresolved_age_seconds"] == 7200
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason) VALUES ('all','all',NULL,'operator')"
        )
        paused = inventory(db.connection, later)
        assert paused["alerts"] == []
        assert paused["stale"]
        assert paused["oldest_unresolved_age_seconds"] == 7200


def test_pipeline_lag_uses_fake_clock_and_keeps_ages_visible_while_paused(tmp_path):
    from swingset.clock import FakeClock

    clock = FakeClock(NOW)
    with open_database(tmp_path) as db:
        prepare(db)
        spec = WatchSpec("", "eepro", "round", "GET", "https://eepro.com/result", "eepro.round")
        upsert_watch(db.connection, spec, NOW)
        db.connection.execute(
            "INSERT INTO pending_work VALUES ('project','event','old-event',?)", (NOW.isoformat(),)
        )
        initial = inventory(db.connection, clock.now())["pipeline_lag"]
        assert initial["acquisition"]["oldest_lag_seconds"] == 0
        assert initial["derivation"]["oldest_lag_seconds"] == 0
        assert initial["alerts"] == []
        clock.sleep(7200)
        delayed = inventory(db.connection, clock.now())["pipeline_lag"]
        assert delayed["acquisition"]["oldest_lag_seconds"] == 7200
        assert delayed["derivation"]["oldest_lag_seconds"] == 7200
        assert {row["reason"] for row in delayed["alerts"]} == {
            "acquisition_schedule_lag",
            "queued_derivation_lag",
        }
        assert all(row["next_action"] and row["oldest"] for row in delayed["alerts"])
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason) VALUES ('all','all',NULL,'review')"
        )
        paused = inventory(db.connection, clock.now())["pipeline_lag"]
        assert paused["alerts"] == []
        assert paused["acquisition"]["oldest_lag_seconds"] == 7200
        assert paused["derivation"]["oldest_lag_seconds"] == 7200
        assert paused["acquisition"]["paused_overdue_watches"] == 1
        assert db.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert db.connection.execute("SELECT count(*) FROM pending_work").fetchone()[0] == 1


def test_h11_worker_states_and_human_json_use_the_same_local_snapshot(tmp_path, capsys):
    with open_database(tmp_path) as db:
        prepare(db)
        requirement = Requirement(
            "round_observations", "old", "eepro", "ready", "fetch retained URL", {}
        )
        key = reconcile_requirement(db.connection, requirement, NOW, "run")
        record_attempt(db.connection, key, "failed-attempt", NOW, "failed")
        clock_time = NOW + timedelta(seconds=1)
        assert inventory(db.connection, clock_time)["worker_state"] == "recent_unfinished_run"
        db.connection.execute(
            "UPDATE runs SET finished_at=? WHERE run_id='run'", (clock_time.isoformat(),)
        )
        assert inventory(db.connection, clock_time)["worker_state"] == "idle"
        clock_time = NOW + timedelta(hours=2)
        expected = inventory(db.connection, clock_time)
        assert expected["worker_state"] == "stopped_or_stale"
        assert expected["alerts"][0]["last_attempt"]["attempt_id"] == "failed-attempt"
        assert expected["requirements"][0]["status_age_seconds"] == 7200
        assert expected["requirements"][0]["age_seconds"] == 7200
        with patch("swingset.cli.SystemClock.now", return_value=clock_time):
            assert main(["doctor", "--state", str(tmp_path), "--json"]) == 0
            reported = json.loads(capsys.readouterr().out)["requirements"]
            assert reported == expected
            assert main(["doctor", "--state", str(tmp_path)]) == 0
            assert human_report(reported) in capsys.readouterr().out


def test_requirement_progress_and_status_age_follow_verified_transitions(tmp_path):
    from swingset.clock import FakeClock

    clock = FakeClock(NOW)
    with open_database(tmp_path) as db:
        prepare(db)
        requirement = Requirement("round_observations", "old", "eepro", "ready", "fetch", {})
        key = reconcile_requirement(db.connection, requirement, clock.now(), "run")
        clock.sleep(3600)
        record_attempt(db.connection, key, "retry", clock.now(), "failed")
        unverified = inventory(db.connection, clock.now())
        assert unverified["seconds_since_progress"] is None
        assert unverified["requirements"][0]["status_age_seconds"] == 3600
        reconcile_requirement(
            db.connection, replace(requirement, state="satisfied"), clock.now(), "run"
        )
        clock.sleep(900)
        verified = inventory(db.connection, clock.now())
        assert verified["seconds_since_progress"] == 900
        assert verified["requirements"][0]["status_age_seconds"] == 900
        assert verified["requirements"][0]["age_seconds"] == 4500
        assert verified["alerts"] == []


def test_artifact_gap_is_one_digest_and_corruption_is_visible(tmp_path):
    with open_database(tmp_path) as db:
        prepare(db)
        archive = Archive(tmp_path)
        digest = archive.store_body(b"retained source evidence")
        spec = WatchSpec(
            "", "eepro", "round", "GET", "https://eepro.com/results/old.html", "eepro.round"
        )
        upsert_watch(db.connection, spec, NOW)
        for key in ("s1", "s2"):
            db.connection.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET',?,?,200,?,23,1,'run','Ok')",
                (key, spec.watch_id, spec.url, NOW.isoformat(), digest),
            )
        path = archive.blob_path(digest)
        compressed = path.read_bytes()
        path.write_bytes(compressed[:10] + b"\x06" + compressed[11:])
        scan(db, NOW, "run")
        rows = db.connection.execute(
            "SELECT state,subject_id,evidence_json FROM findings WHERE kind='archive_artifact'"
        ).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0][:2]) == ("unavailable", digest)
        assert '"s1"' in rows[0][2] and '"s2"' in rows[0][2]
        path.write_bytes(compressed)
        scan(db, NOW + timedelta(hours=1), "run")
        assert (
            db.connection.execute(
                "SELECT state FROM findings WHERE kind='archive_artifact'"
            ).fetchone()[0]
            == "satisfied"
        )


def test_registry_occurrences_before_configured_history_start_are_out_of_scope(tmp_path):
    from test_link_service import seed

    with open_database(tmp_path) as db:
        seed(db.connection)
        prepare(db)
        db.connection.execute(
            "INSERT INTO registry_placements(wsdc_id,role,dance_style,division,series_id,series_name_raw,event_month,result,points,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (1,'leader','wcs','novice','series','Old event','2012-01','1',3,'registry','snapshot','1','t','t','run')"
        )
        scan(db, NOW, "run", history_start=date(2015, 1, 1))
        assert (
            db.connection.execute(
                "SELECT state FROM findings WHERE kind='registry_event_association'"
            ).fetchone()[0]
            == "out_of_scope"
        )


def test_cohort_rejects_scope_changes(tmp_path):
    with open_database(tmp_path) as db:
        capture_cohort(db.connection, "fixed", NOW)
        with pytest.raises(ValueError, match="different scope"):
            capture_cohort(db.connection, "fixed", NOW, source="eepro")


def test_summary_checkpoint_does_not_repeat_same_timestamp_transitions(tmp_path, capsys):
    with open_database(tmp_path) as db:
        prepare(db)
        reconcile_requirement(
            db.connection,
            Requirement("round_observations", "old", "eepro", "ready", "fetch", {}),
            NOW,
            "run",
        )
        with patch("swingset.cli.SystemClock.now", return_value=NOW):
            assert main(["summary", "--state", str(tmp_path), "--json"]) == 0
            first = json.loads(capsys.readouterr().out)
            assert first["requirements"]["changes"]["opened"] == 1
            assert main(["summary", "--state", str(tmp_path), "--json"]) == 0
            second = json.loads(capsys.readouterr().out)
            assert second["requirements"]["changes"]["opened"] == 0
            assert second["requirements"]["changes"]["opening_unmet"] == 1


def test_finalist_requirement_retires_when_contest_is_no_longer_eligible(tmp_path):
    from test_link_service import entry, seed

    with open_database(tmp_path) as db:
        prepare(db)
        seed(db.connection)
        db.connection.execute(
            "INSERT INTO rounds(round_id,contest_id,round_type,round_index,name_raw,scoring_method,callback_legend,judge_count,entry_count,source_round_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('final','c1','final',1,'Final','relative_placement','{}',5,6,'final','test','snap','1','t','t','run')"
        )
        entry(db.connection, "finalist", "c1", "7", "New Person")
        db.connection.execute(
            "INSERT INTO placements(placement_id,round_id,contest_id,event_id,place,leader_entry_id,tally,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('place','final','c1','event',6,'finalist','','test','snap','1','t','t','run')"
        )
        scan(db, NOW, "run")
        row = db.connection.execute(
            "SELECT finding_id,state FROM findings WHERE kind='first_point_reconsideration'"
        ).fetchone()
        assert row["state"] == "waiting_for_source"
        capture_cohort(db.connection, "finalists", NOW)
        db.connection.execute("UPDATE contests SET division='advanced' WHERE contest_id='c1'")
        scan(db, NOW + timedelta(seconds=1), "run")
        assert (
            db.connection.execute(
                "SELECT state FROM findings WHERE finding_id=?", (row["finding_id"],)
            ).fetchone()[0]
            == "out_of_scope"
        )
        cohort = inventory(db.connection, NOW + timedelta(seconds=2))["cohorts"][0]
        assert cohort["total"] == 1 and cohort["retired"] == 1 and cohort["satisfied"] == 0
