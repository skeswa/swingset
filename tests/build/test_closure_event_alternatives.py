"""Unsupported structural alternatives do not erase independently supported events."""

import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from materialized_fixture import materialize_seeded_outputs
from test_h16_acceptance import release_state as release_state
from test_project_event import EVENT

from swingset.build.closure import select
from swingset.build.closure_manifest import canonical
from swingset.build.closure_rows import ReconstructedRows, _close, _put, reconstruct
from swingset.state import derivations
from swingset.state.work import WorkUnit


@pytest.mark.parametrize("alternative_kind", ["calendar", "map"])
def test_reconstruction_keeps_supported_event_and_judges_in_either_overlay_order(
    release_state, tmp_path, alternative_kind
):
    f = release_state
    baseline = f.baseline
    event = dict(f.conn.execute("SELECT * FROM events WHERE event_id=?", (EVENT,)).fetchone())
    original_judges = [dict(row) for row in f.conn.execute("SELECT * FROM judges")]
    assert original_judges and all(row["wsdc_id"] is None for row in original_judges)
    # An earlier calendar sorts before the retained calendar base; map sorts after it.
    # Complete through the real ledger, then make history select the extra scope.
    captured_at = (
        f.clock.now() - timedelta(seconds=1) if alternative_kind == "calendar" else f.clock.now()
    )
    f.clock.sleep(1)
    unit = WorkUnit("project", alternative_kind, "unsupported-alternative")
    changed = {**event, "series_id": "slug-unreviewed", "held": "listed"}
    with f.db.transaction():
        selection = derivations.capture(f.conn, unit, now=captured_at)
        derivations.complete(
            f.conn,
            selection,
            rows=(derivations.OutputRow("events", canonical([event["event_id"]]), changed),),
            now=captured_at,
            run_id=f.run,
        )
    materialize_seeded_outputs(f.db, now=f.clock.now(), run_id=f.run, stages=("project",))
    closure = select(f.conn, cutoff=f.clock.now(), baseline=baseline)
    assert any(row["unit_id"] == unit.unit_id for row in closure.selected)
    with reconstruct(f.conn, closure, directory=tmp_path / "output", baseline=baseline) as rows:
        selected_event = next(row for row in rows.iter_table("events") if row["event_id"] == EVENT)
        assert selected_event["series_id"] == event["series_id"]
        assert selected_event["held"] == event["held"]
        assert {
            row["judge_id"]: (row["name_raw"], row["wsdc_id"]) for row in rows.iter_table("judges")
        } == {row["judge_id"]: (row["name_raw"], None) for row in original_judges}
        assert rows.counts["legacy_owned_values_not_published"] == 1
        assert not any(item["unit_id"] == EVENT for item in rows.omissions)


def spool():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE rows(table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(table_name,record_key));"
        "CREATE TABLE rejected(table_name TEXT,record_key TEXT,payload TEXT,reason TEXT);"
    )
    return conn


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ("events", "source_revoked"),
        ("judges", "legacy_owned_values_not_published"),
        ("judges", "source_revoked"),
        ("final_marks", "legacy_owned_values_not_published"),
        ("final_marks", "source_revoked"),
    ],
)
def test_valid_alternative_event_never_masks_revocation_or_unsupported_result(table, reason):
    with closing(spool()) as conn:
        _put(conn, "events", '["event"]', {"event_id": "event", "series_id": "wsdc-291"})
        _put(
            conn,
            "judges",
            '["judge"]',
            {"judge_id": "judge", "event_id": "event", "name_raw": "Named Judge"},
        )
        _put(conn, "rounds", '["round"]', {"round_id": "round", "event_id": "event"})
        _put(conn, "registry_placements", '["registry"]', {"event_id": "event", "wsdc_id": 123})
        # Marks resolve their event through structural parents; judges name it directly.
        rejected = {"round_id": "round"} if table == "final_marks" else {"event_id": "event"}
        key = '["event"]' if table == "events" else '["rejected"]'
        conn.execute(
            "INSERT INTO rejected VALUES (?,?,?,?)", (table, key, json.dumps(rejected), reason)
        )
        result = ReconstructedRows(conn)
        _close(conn, result)
        assert list(result.iter_table("events")) == []
        assert list(result.iter_table("judges")) == []
        assert list(result.iter_table("registry_placements"))[0]["event_id"] is None
        assert result.counts[reason] == 1
        assert result.omissions == [
            {
                "stage": "project",
                "unit_kind": "event",
                "unit_id": "event",
                "status": "withheld",
                "reason": "source_support_unavailable",
            }
        ]


def test_partial_history_patch_cannot_replace_a_rejected_structural_event():
    with closing(spool()) as conn:
        event = {"event_id": "event", "series_id": "slug-unsupported"}
        conn.execute(
            "INSERT INTO rejected VALUES (?,?,?,?)",
            ("events", '["event"]', json.dumps(event), "legacy_owned_values_not_published"),
        )
        assert not _put(
            conn,
            "events",
            '["event"]',
            {"event_id": "event", "coverage_tier": "sheets_complete"},
            patch=True,
        )
        _put(
            conn,
            "judges",
            '["judge"]',
            {"judge_id": "judge", "event_id": "event", "name_raw": "Named Judge"},
        )
        result = ReconstructedRows(conn)
        _close(conn, result)
        assert list(result.iter_table("events")) == []
        assert list(result.iter_table("judges")) == []
        assert result.counts["legacy_owned_values_not_published"] == 1
        assert result.omissions[0]["unit_id"] == "event"
