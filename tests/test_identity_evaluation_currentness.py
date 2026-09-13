"""New samples require actual derivation currentness; frozen packets never change."""

import pytest
from test_h15_acceptance import drain_project
from test_h15_acceptance import source_fixture as source_fixture

from swingset.link.evaluation import main
from swingset.link.evaluation_input import read_population
from swingset.link.service import link_event
from swingset.schedule.parse import parse_snapshot
from swingset.state.work import next_work, unfinished_units


def settle(fixture):
    for unit in list(unfinished_units(fixture.conn, stages=("parse",))):
        parse_snapshot(
            fixture.db, fixture.corpus.archive, unit, fixture.corpus.clock, fixture.corpus.run
        )
    drain_project(fixture)
    for _ in range(10):
        unit = next_work(fixture.conn, "link", now=fixture.corpus.clock.now())
        if unit is None:
            assert list(unfinished_units(fixture.conn)) == []
            return
        link_event(
            fixture.db, unit.unit_id, fixture.bundle, fixture.corpus.clock, fixture.corpus.run
        )
    pytest.fail("fixture link generations did not settle")


@pytest.mark.parametrize("stage", ["project", "link"])
def test_lost_queue_cannot_hide_missing_materialization(source_fixture, stage):
    f = source_fixture
    settle(f)
    before = list(f.conn.iterdump())
    rows, _ = read_population(f.conn)
    assert rows
    assert list(f.conn.iterdump()) == before
    f.conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id=NULL "
        "WHERE stage=? AND unit_kind='event' AND unit_id='event-a'",
        (stage,),
    )
    assert f.conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
    before = list(f.conn.iterdump())
    with pytest.raises(ValueError, match="settled"):
        read_population(f.conn)
    assert list(f.conn.iterdump()) == before


def test_changed_source_without_enqueue_refuses_new_sample(source_fixture, tmp_path):
    f = source_fixture
    settle(f)
    changed = f.body.replace(b"Alice Example", b"Changed Evidence")
    context = f.corpus.snapshot("new-evidence", changed)
    generation, report = f.corpus.stage(context, body=changed)
    assert not report.failures and f.corpus.admit(generation) == "accepted"
    f.conn.execute("DELETE FROM pending_work")
    assert list(unfinished_units(f.conn))
    before = list(f.conn.iterdump())
    output = tmp_path / "refused-sample.json"
    with pytest.raises(ValueError, match="settled"):
        main(
            [
                "sample",
                "--state",
                str(f.db.state_dir),
                "--output",
                str(output),
                "--seed",
                "fixed-seed",
                "--cohort",
                "stale-fixture",
                "--cutoff",
                f.corpus.clock.now().isoformat(),
            ]
        )
    assert not output.exists()
    assert list(f.conn.iterdump()) == before
