from unittest.mock import patch

import pytest
from test_link_service import Bundle, entry, owned_source_sheet, run, seed

from swingset.link import DancerRecord, Subject, generate_candidates
from swingset.link.service import _candidate_pools
from swingset.normalize.names import normalize_name
from swingset.state.db import open_database
from swingset.state.findings import Finding, replace_findings


@pytest.mark.parametrize("kind", ["entry", "judge"])
@pytest.mark.parametrize(
    "name", ["Álex Léé Jr", "Alex Lee", "Alex la Rue", "李 小龙", "", "***", "Alex Lee and Sam Doe"]
)
@pytest.mark.parametrize("source_id", [None, 2, 3])
def test_indexed_pool_preserves_every_candidate_signal_and_order(kind, name, source_id):
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
    entry_pools, judge_pools = _candidate_pools(dancers, judge_ids)
    subject = Subject(
        kind, "subject", name, "leader" if kind == "entry" else "unknown", "novice", 2026, source_id
    )
    full_pool = (
        dancers
        if kind == "entry"
        else [dancer for dancer in dancers if dancer.wsdc_id in judge_ids]
    )
    pools = entry_pools if kind == "entry" else judge_pools
    indexed = pools.get(normalize_name(name).last_token[:1], [])
    assert generate_candidates(subject, indexed, {"alex": "alexander"}) == generate_candidates(
        subject, full_pool, {"alex": "alexander"}
    )
    assert indexed == [
        dancer
        for dancer in full_pool
        if normalize_name(dancer.name_raw).last_token[:1] == normalize_name(name).last_token[:1]
        and normalize_name(name).last_token
    ]


def test_service_keeps_judge_eligibility_and_original_source_id_behavior(tmp_path):
    with open_database(tmp_path) as db:
        seed(db.connection)
        entry(db.connection, "entry", "c1", "032", "Alex Lee")
        db.connection.execute("UPDATE dancers SET follower_highest_level='CHMP' WHERE wsdc_id=2")
        db.connection.execute(
            "INSERT INTO judges(judge_id,event_id,name_raw,initials,anonymous,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('judge','event','Alex Lee','AL',0,'test','snap','1','t','t','run')"
        )
        with patch(
            "swingset.link.service.generate_candidates", wraps=generate_candidates
        ) as generate:
            owned_source_sheet(db.connection, [("032", "Alex Lee", 999)])
            run(db, Bundle())
        pools = {
            call.args[0].subject_kind: [dancer.wsdc_id for dancer in call.args[1]]
            for call in generate.call_args_list
        }
        assert pools == {"entry": [1, 2], "judge": [2]}
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
