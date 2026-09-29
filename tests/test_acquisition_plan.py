"""Acquisition plan rows (migration 34): year and page rows with a reason."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_history_projection_gate import state as state

from swingset.state import acquisition_plan as plan
from swingset.state.db import open_database

AT = "2026-09-25T00:00:00Z"


def test_plan_rows_round_trip_and_count_by_kind_and_reason(tmp_path: Path) -> None:
    year = plan.PlanRow(
        plan.year_key(2024),
        "year",
        plan.ELIGIBLE,
        AT,
        "r1",
        year=2024,
        detail_json='{"projection_pending": false}',
    )
    page = plan.PlanRow(
        plan.page_key("w1"),
        "page",
        "history_year_unaccepted",
        AT,
        "r1",
        year=2023,
        source="wsdc",
        url="https://example.test/",
        rank=1,
    )
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            plan.upsert(conn, year)
            plan.upsert(conn, page)
            assert plan.get(conn, "year:2024") == year
            assert plan.get(conn, "page:w1") == page
            moved = plan.PlanRow(**{**page.__dict__, "reason": plan.ELIGIBLE, "rank": 7})
            plan.upsert(conn, moved)
            assert plan.get(conn, "page:w1") == moved
            assert plan.counts_by_reason(conn) == {("page", "eligible"): 1, ("year", "eligible"): 1}
            assert plan.delete(conn, "page:w1")
            assert not plan.delete(conn, "page:w1")
            assert plan.get(conn, "page:w1") is None


def _page(key: str, run_id: str, *, rank: int, reason: str = plan.ELIGIBLE) -> plan.PlanRow:
    return plan.PlanRow(
        plan.page_key(key),
        "page",
        reason,
        AT,
        run_id,
        source="eepro",
        watch_kind="autoindex",
        page_kind="eepro.autoindex",
        url=f"https://eepro.com/{key}/",
        rank=rank,
    )


def test_write_chunks_prunes_only_a_whole_pass_and_reads_eligible_rows_in_rank_order(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        first = [
            plan.PlanRow(plan.year_key(2019), "year", plan.ELIGIBLE, AT, "r1", year=2019),
            _page("a", "r1", rank=2),
            _page("b", "r1", rank=0, reason="watch_cooldown"),
            _page("c", "r1", rank=1),
        ]
        written = plan.write(database, first, run_id="r1", prune_pages=True, chunk=2)
        assert written == {"written": 4, "deleted": 0, "transactions": 2}
        assert [row.plan_key for row in plan.eligible_pages(conn)] == ["page:c", "page:a"]
        # A cut pass rewrites what it reached and keeps the rest.
        cut = [plan.PlanRow(plan.year_key(2020), "year", "year_unaccepted", AT, "r2", year=2020)]
        cut.append(_page("a", "r2", rank=0))
        assert plan.write(database, cut, run_id="r2", prune_pages=False)["deleted"] == 1
        assert plan.get(conn, "year:2019") is None
        assert {row.plan_key for row in plan.eligible_pages(conn)} == {"page:a", "page:c"}
        # A whole pass deletes the pages it did not write.
        assert (
            plan.write(database, [_page("a", "r3", rank=0)], run_id="r3", prune_pages=True)[
                "deleted"
            ]
            == 3
        )
        assert [row.plan_key for row in plan.eligible_pages(conn)] == ["page:a"]
        # An empty whole pass still clears the table in one transaction.
        assert plan.write(database, [], run_id="r4", prune_pages=True) == {
            "written": 0,
            "deleted": 1,
            "transactions": 1,
        }


def test_the_facts_only_gate_skips_the_pending_question_and_names_the_closed_fact(state):
    """D-0218: in the loops a pending mapping no longer closes phase two; accept-year still asks."""
    from swingset.project.history import (
        accept_year,
        facts_only_year_gate,
        pending_inventory_projection,
        phase_two_allowed,
        projection_pending,
        year_gate_reason,
    )
    from swingset.state.work import WorkUnit, enqueue

    conn = state.conn
    enqueue(
        conn,
        (WorkUnit("project", "mapping", "eepro"),),
        enqueued_at=state.clock.now().isoformat(),
    )
    conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id=NULL WHERE stage='project' AND unit_kind='mapping'"
    )
    assert pending_inventory_projection(conn) and not phase_two_allowed(conn, 2019)
    with facts_only_year_gate():
        assert not pending_inventory_projection(conn)
        assert projection_pending(conn)
        assert phase_two_allowed(conn, 2019) and phase_two_allowed(conn, 2019, pending=True)
        with pytest.raises(ValueError, match="projection is pending"):
            accept_year(conn, 2019, accepted_by="owner", accepted_at=state.clock.now().isoformat())
        assert year_gate_reason(conn, 2019) is None
        conn.execute("DELETE FROM meta WHERE key='h10_deployed'")
        assert year_gate_reason(conn, 2019) == "h7_h10_not_deployed"
        conn.execute("UPDATE events SET start_date='2019-01-03' WHERE year=2019")
        assert year_gate_reason(conn, 2019) == "acceptance_digest_changed"
        assert not phase_two_allowed(conn, 2019)
        assert year_gate_reason(conn, 2018) == "year_unaccepted"
    assert pending_inventory_projection(conn)
