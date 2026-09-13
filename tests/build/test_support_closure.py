import copy
import sqlite3
from collections import Counter

import pytest

from swingset.build.support_closure import close_revoked_support


def graph():
    def supported(**row):
        return {"snapshot_id": "sheet", "parser_version": "1", **row}

    return {
        "events": [supported(event_id="event", year=2024), supported(event_id="other", year=2025)],
        "contests": [supported(contest_id="contest", event_id="event")],
        "rounds": [supported(round_id="round", contest_id="contest", chief_judge_id="judge")],
        "entries": [
            supported(
                entry_id="entry",
                contest_id="contest",
                event_id="event",
                wsdc_id=7,
                role="leader",
                link_status="confirmed",
                link_confidence=1.0,
            )
        ],
        "judges": [supported(judge_id="judge", event_id="event", wsdc_id=7)],
        "placements": [
            supported(
                placement_id="place",
                round_id="round",
                contest_id="contest",
                event_id="event",
                leader_entry_id="entry",
                leader_wsdc_id=7,
                registry_points_leader=10,
                registry_confirmed=True,
                points_matches_expected=True,
                place=1,
            )
        ],
        "final_marks": [
            supported(round_id="round", placement_id="place", judge_id="judge", rank=1)
        ],
        "callback_marks": [
            supported(round_id="round", entry_id="entry", judge_id="judge", mark="yes")
        ],
        "callbacks": [supported(round_id="round", entry_id="entry", yes_count=1)],
        "heats": [supported(heat_id="heat", round_id="round", entry_id="entry")],
        "dancers": [
            supported(wsdc_id=7, snapshot_id="registry"),
            supported(wsdc_id=8, snapshot_id="other-registry", merged_into_wsdc_id=7),
        ],
        "registry_placements": [
            supported(wsdc_id=7, event_id="event", snapshot_id="registry"),
            supported(wsdc_id=8, event_id="event", snapshot_id="other-registry"),
        ],
        "identity_links": [
            {
                "link_id": "link",
                "subject_kind": "entry",
                "subject_id": "entry",
                "wsdc_id": 7,
                "method": "registry_placement",
                "status": "confirmed",
            }
        ],
        "link_candidates": [
            {"subject_kind": "entry", "subject_id": "entry", "wsdc_id": 7, "chosen": True}
        ],
        "review_queue": [{"subject_id": "entry"}, {"subject_id": "unrelated-history-finding"}],
        "coverage": [{"year": 2024, "events": 1}, {"year": 2025, "events": 1}],
        "snapshots": [{"snapshot_id": "sheet", "parser_version": "1"}],
    }


def support(monkeypatch, revoked):
    calls = Counter()

    def check(conn, snapshot_id, *, parser_version):
        calls[snapshot_id, parser_version] += 1
        return {
            "state": "revoked" if snapshot_id in revoked else "accepted",
            "private_note": "Never include this in a public omission reason",
        }

    monkeypatch.setattr("swingset.build.support_closure.interpretation_support", check)
    return calls


@pytest.mark.parametrize(
    "table",
    [
        "events",
        "contests",
        "rounds",
        "entries",
        "judges",
        "placements",
        "final_marks",
        "callback_marks",
        "callbacks",
        "heats",
    ],
)
def test_revoked_scoring_support_omits_entire_event_and_preserves_registry_mirror(
    monkeypatch, table
):
    original = graph()
    original[table][0]["snapshot_id"] = "revoked"
    before = copy.deepcopy(original)
    calls = support(monkeypatch, {"revoked"})
    result = close_revoked_support(sqlite3.connect(":memory:"), original)
    assert original == before
    assert [r["event_id"] for r in result.tables["events"]] == ["other"]
    for name in (
        "contests",
        "rounds",
        "entries",
        "judges",
        "placements",
        "heats",
        "callback_marks",
        "callbacks",
        "final_marks",
        "identity_links",
        "link_candidates",
    ):
        assert result.tables[name] == []
    assert [r["event_id"] for r in result.tables["registry_placements"]] == [None, None]
    assert result.tables["coverage"] == [{"year": 2025, "events": 1}]
    assert result.tables["review_queue"] == [{"subject_id": "unrelated-history-finding"}]
    assert result.tables["snapshots"] == before["snapshots"]
    assert max(calls.values()) == 1
    assert "private" not in str(result.reasons)


def test_dancer_revocation_withdraws_ids_points_and_merge_claims_but_keeps_marks(monkeypatch):
    original = graph()
    support(monkeypatch, {"registry"})
    result = close_revoked_support(sqlite3.connect(":memory:"), original)
    assert [r["wsdc_id"] for r in result.tables["dancers"]] == [8]
    assert result.tables["dancers"][0]["merged_into_wsdc_id"] is None
    assert [r["wsdc_id"] for r in result.tables["registry_placements"]] == [8]
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["judges"][0]["wsdc_id"] is None
    placement = result.tables["placements"][0]
    assert placement["leader_wsdc_id"] is None
    assert placement["registry_points_leader"] is None
    assert placement["points_matches_expected"] is None
    assert placement["registry_confirmed"] is False
    assert result.tables["identity_links"][0]["wsdc_id"] is None
    assert result.tables["link_candidates"] == []
    for name in ("events", "rounds", "final_marks", "callback_marks", "callbacks", "heats"):
        assert result.tables[name] == original[name]


def test_registry_fact_revocation_does_not_destroy_independent_dancer_or_structure(monkeypatch):
    original = graph()
    original["registry_placements"][0]["snapshot_id"] = "revoked-placement"
    original["link_candidates"][0]["registry_confirms"] = True
    support(monkeypatch, {"revoked-placement"})
    result = close_revoked_support(sqlite3.connect(":memory:"), original)
    assert result.tables["dancers"] == original["dancers"]
    assert result.tables["final_marks"] == original["final_marks"]
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["placements"][0]["registry_points_leader"] is None
    assert result.tables["link_candidates"] == []


def test_no_revocation_preserves_baseline_and_never_reads_new_public_generations(monkeypatch):
    original = graph()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE events(event_id)")
    conn.execute("INSERT INTO events VALUES ('new-accepted-event')")
    support(monkeypatch, set())
    result = close_revoked_support(conn, original)
    assert result.tables == original
    assert result.removed_rows == {}
    assert result.withdrawn_claims == {}


@pytest.mark.parametrize("revoked", [set(), {"registry"}, {"other-registry"}])
def test_closure_shares_unchanged_marks_without_mutating_nested_input(monkeypatch, revoked):
    original = graph()
    original["identity_links"][0]["source_ref_ids"] = ["retained-reference"]
    original["link_candidates"][0]["evidence"] = {"cells": ["printed-name"]}
    before = copy.deepcopy(original)
    support(monkeypatch, revoked)
    result = close_revoked_support(sqlite3.connect(":memory:"), original)
    assert original == before
    assert result.tables["final_marks"] is not original["final_marks"]
    assert result.tables["final_marks"][0] is original["final_marks"][0]
    if "registry" in revoked:
        assert result.tables["entries"][0] is not original["entries"][0]
        assert result.tables["entries"][0]["wsdc_id"] is None
        assert original["entries"][0]["wsdc_id"] == 7
