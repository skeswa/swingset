"""H3–H5 regressions from the archived identity audit and negative controls."""

import json
from pathlib import Path

import pytest
from test_entry_quality import _sheet
from test_link_service import Bundle, entry, owned_source_sheet, run, seed
from test_project_event import EVENT, add
from test_project_event import seed as seed_projection

from swingset.link import DancerRecord, Subject, generate_candidates, score_candidate
from swingset.model.canonical import Entry
from swingset.normalize.divisions import classify_contest
from swingset.normalize.names import paired_names
from swingset.project.contests import project_event
from swingset.state.db import open_database

AUDIT = json.loads(
    (Path(__file__).parent / "fixtures/identity/audit-counterexamples.json").read_text()
)


@pytest.mark.parametrize("division", [None, "none", "open"])
@pytest.mark.parametrize(
    "level", ["none", "newcomer", "novice", "intermediate", "advanced", "allstar", "champion"]
)
def test_unrestricted_division_is_unavailable_at_every_dancer_level(division, level):
    dancer = DancerRecord(
        1, "Alex Lee", "follower", 2026, follower_required_level=level, follower_allowed_level=level
    )
    candidate = generate_candidates(
        Subject("entry", "e", "Alex Lee", "follower", division, 2026), [dancer], {}
    )[0]
    assert candidate.division_ok is None
    assert score_candidate(candidate) == pytest.approx(1.0)


@pytest.mark.parametrize("case", AUDIT["unrestricted"])
def test_audit_unrestricted_contests_keep_lynne_candidate(case):
    dancer = DancerRecord(**AUDIT["registry"])
    division = classify_contest(case["contest"]).division
    assert division == "none"
    subject = Subject("entry", case["event"], dancer.name_raw, "follower", division, 2026)
    candidate = generate_candidates(subject, [dancer], {})[0]
    assert candidate.division_ok is None
    assert score_candidate(candidate) == pytest.approx(1.0)


def test_unknown_registry_level_is_not_evidence_of_a_mismatch():
    dancer = DancerRecord(1, "Alex Lee", "leader", 2026)
    candidate = generate_candidates(
        Subject("entry", "e", "Alex Lee", "leader", "novice", 2026), [dancer], {}
    )[0]
    assert candidate.division_ok is None


def test_judge_ceiling_omits_unavailable_role_without_changing_entry_scores():
    dancer = DancerRecord(1, "Alex Lee", "leader", 2026, is_pro=True)
    judge = generate_candidates(Subject("judge", "j", "Alex Lee", event_year=2026), [dancer], {})[0]
    entry_candidate = generate_candidates(
        Subject("entry", "e", "Alex Lee", event_year=2026), [dancer], {}
    )[0]
    assert score_candidate(judge) == pytest.approx(1.0)
    assert score_candidate(entry_candidate) == pytest.approx(0.875)


@pytest.mark.parametrize("case", AUDIT["paired"])
def test_audit_pair_retains_ownership_and_abstains_in_projection(tmp_path, case):
    with open_database(tmp_path, lock=False) as db:
        seed_projection(db.connection)
        add(
            db.connection,
            "pair",
            "pair-snapshot",
            _sheet("Final", case["name_raw"], case["bib"], case["contest"]),
            "2026-09-01",
        )
        projection = project_event(db.connection, EVENT, "2026-09-10", "run_a")
    entries = [row for row in projection.rows if isinstance(row, Entry)]
    assert [(row.role, row.name_raw) for row in entries] == [("couple", case["name_raw"])]
    assert any(f.kind == "paired_name" for f in projection.findings)
    subject = Subject("entry", entries[0].entry_id, entries[0].name_raw, entries[0].role)
    assert generate_candidates(subject, [DancerRecord(**AUDIT["registry"])], {}) == []


@pytest.mark.parametrize("case", AUDIT["paired"])
def test_old_mixed_person_entry_cannot_bypass_abstention_with_printed_id(tmp_path, case):
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        db.connection.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run','t',0)")
        entry(db.connection, case["subject_id"], "c1", case["bib"], case["name_raw"])
        owned_source_sheet(db.connection, [(case["bib"], case["name_raw"], 26843)])
        run(db, Bundle())
        assert tuple(
            db.connection.execute("SELECT wsdc_id,status FROM identity_links").fetchone()
        ) == (None, "unmatched")
        assert db.connection.execute("SELECT wsdc_id FROM entries").fetchone()[0] is None
        assert db.connection.execute("SELECT count(*) FROM link_candidates").fetchone()[0] == 0
        assert (
            db.connection.execute("SELECT kind FROM findings WHERE closed_at IS NULL").fetchone()[0]
            == "paired_name"
        )
        run(db, Bundle())
        assert (
            db.connection.execute(
                "SELECT count(*) FROM findings WHERE closed_at IS NULL"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("name", ["Mary Jane Smith", "José de la Cruz", "Anne-Marie O'Neil"])
def test_compound_individual_names_and_explicit_ids_remain_available(name):
    assert not paired_names(name)
    subject = Subject("entry", "e", name, "leader", source_wsdc_id=1)
    candidate = generate_candidates(subject, [DancerRecord(1, name, "leader", 2026)], {})[0]
    assert score_candidate(candidate) == 1.0


def test_rescored_judge_and_unrestricted_entry_do_not_create_default_joins(tmp_path):
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        db.connection.execute("DELETE FROM dancers WHERE wsdc_id=2")
        db.connection.execute("UPDATE dancers SET is_pro=1")
        db.connection.execute("UPDATE contests SET division='none'")
        entry(db.connection, "event/c1/L-7", "c1", "7")
        db.connection.execute(
            "INSERT INTO judges(judge_id,event_id,name_raw,initials,anonymous,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('judge','event','Alex Lee','AL',0,'test','s','1','t','t','run')"
        )
        run(db, Bundle())
        assert db.connection.execute("SELECT wsdc_id FROM entries").fetchone()[0] is None
        assert db.connection.execute("SELECT wsdc_id FROM judges").fetchone()[0] is None
        assert [
            tuple(row)
            for row in db.connection.execute("SELECT wsdc_id,status,confidence FROM identity_links")
        ] == [(1, "probable", 1.0), (1, "probable", 1.0)]
        assert (
            db.connection.execute(
                "SELECT role_ok FROM link_candidates WHERE subject_kind='judge'"
            ).fetchone()[0]
            is None
        )


def test_linking_month_precision_event_preserves_unknown_dates(tmp_path):
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        db.connection.execute(
            "UPDATE events SET start_date=NULL,end_date=NULL,event_month='2026-01',date_precision='month'"
        )
        entry(db.connection, "event/c1/L-7", "c1", "7")
        run(db, Bundle())
        assert tuple(
            db.connection.execute(
                "SELECT start_date,end_date,date_precision FROM events"
            ).fetchone()
        ) == (None, None, "month")
