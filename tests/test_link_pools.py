import pytest
from test_link_service import Bundle, entry, owned_source_sheet, run, seed

from swingset.link import DancerRecord, Subject, generate_candidates
from swingset.link.model import EventEvidence, LinkingRules, SubjectEvidence
from swingset.link.policy import DecisionPolicy
from swingset.link.resolution import resolve_event
from swingset.link.score import Weights
from swingset.state.db import open_database
from swingset.state.findings import Finding, replace_findings
from swingset.state.identity_journal import JournalToken


@pytest.mark.parametrize("kind", ["entry", "judge"])
@pytest.mark.parametrize(
    "name", ["Álex Léé Jr", "Alex Lee", "Alex la Rue", "李 小龙", "", "***", "Alex Lee and Sam Doe"]
)
@pytest.mark.parametrize("source_id", [None, 2, 3])
def test_resolution_preserves_full_pool_candidate_signals(kind, name, source_id):
    dancers = [
        DancerRecord(4, "Alexander Lee", "leader", 2026),
        DancerRecord(2, "Álex Léé Jr", "leader", 2023),
        DancerRecord(3, "Alex Doe", "follower", 2020, True),
        DancerRecord(1, "Alex Lee", "leader", 2026),
        DancerRecord(5, "Alex la Rue", "leader", 2026),
        DancerRecord(6, "李 小龙", "leader", 2026),
        DancerRecord(7, "***", "unknown", 2026),
    ]
    judge_ids = {2, 3, 4, 6, 7}
    subject = Subject(
        kind, "subject", name, "leader" if kind == "entry" else "unknown", "novice", 2026, source_id
    )
    full_pool = (
        dancers
        if kind == "entry"
        else [dancer for dancer in dancers if dancer.wsdc_id in judge_ids]
    )
    result = resolve_event(
        EventEvidence(
            "event",
            (SubjectEvidence(subject),),
            tuple(dancers),
            frozenset(judge_ids),
            DecisionPolicy(JournalToken("test", 1), (), {}),
        ),
        LinkingRules(Weights(), {"alex": "alexander"}),
    )
    actual = [assessment.candidate for assessment in result.subjects[0].candidates]
    expected = generate_candidates(subject, full_pool, {"alex": "alexander"})
    assert sorted(actual, key=lambda item: item.dancer.wsdc_id) == sorted(
        expected, key=lambda item: item.dancer.wsdc_id
    )


def test_service_keeps_judge_eligibility_and_original_source_id_behavior(tmp_path):
    with open_database(tmp_path) as db:
        seed(db.connection)
        entry(db.connection, "entry", "c1", "032", "Alex Lee")
        db.connection.execute("UPDATE dancers SET follower_highest_level='CHMP' WHERE wsdc_id=2")
        db.connection.execute(
            "INSERT INTO judges(judge_id,event_id,name_raw,initials,anonymous,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('judge','event','Alex Lee','AL',0,'test','snap','1','t','t','run')"
        )
        owned_source_sheet(db.connection, [("032", "Alex Lee", 999)])
        run(db, Bundle())
        candidates = db.connection.execute(
            "SELECT subject_kind,wsdc_id FROM link_candidates ORDER BY subject_kind,wsdc_id"
        ).fetchall()
        assert [tuple(row) for row in candidates] == [("entry", 1), ("entry", 2), ("judge", 2)]
        assert db.connection.execute("SELECT wsdc_id,link_status FROM entries").fetchone()[:] == (
            999,
            "confirmed",
        )
        assert db.connection.execute("SELECT wsdc_id FROM judges").fetchone()[0] is None


def test_empty_event_skips_dancer_pool_but_closes_findings_and_completes_work(tmp_path):
    with open_database(tmp_path) as db:
        seed(db.connection)
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run','2026-01-04T00:00:00Z',0)"
        )
        replace_findings(
            db.connection,
            owner_kind="link",
            owner_id="event",
            findings=(
                Finding("paired_name", "entry", "removed", "warning", "Old paired name", {}),
            ),
            opened_at="2026-01-04T00:00:00Z",
            run_id="run",
        )
        queries = []
        db.connection.set_trace_callback(queries.append)
        run(db, Bundle())
        db.connection.set_trace_callback(None)
        assert not any("FROM dancers WHERE merged_into_wsdc_id" in query for query in queries)
        assert (
            db.connection.execute(
                "SELECT closed_at FROM findings WHERE owner_kind='link'"
            ).fetchone()[0]
            is not None
        )
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM pending_work WHERE stage='link'"
            ).fetchone()[0]
            == 0
        )
        assert db.connection.execute("SELECT COUNT(*) FROM identity_links").fetchone()[0] == 0
