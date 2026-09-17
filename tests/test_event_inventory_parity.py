"""Doctor uses normalized request evidence, with honest finite-budget results."""

import json
import sqlite3
from unittest.mock import Mock

import pytest
from build.test_event_page_evidence import aggregate
from test_admission import BODY
from test_event_enumerations import admit_parent, child, finish_bootstrap, view
from test_event_enumerations import event as event

from swingset.admission.page_evidence import Limits
from swingset.fetch.archive import Archive
from swingset.schedule.event_inventory import inventory
from swingset.schedule.event_report import report
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec


def read(f, *, limits=None):
    with f.db.transaction(immediate=False):
        before = f.conn.total_changes
        result = inventory(
            f.conn,
            f.archive,
            source="eepro",
            source_ref="eepro:test",
            now=f.corpus.clock.now(),
            limits=limits or Limits(),
        )
        assert f.conn.total_changes == before
        assert f.conn.in_transaction and f.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        json.dumps(result)
        return result


def test_alias_watch_outside_parent_declarations_supplies_same_request(event):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    declared = view(f)["members"][0]["watch_ids"]
    alias = WatchSpec(
        "",
        "eepro",
        "event",
        "GET",
        "https://EEPRO.COM:443/results/test/one.htm#alias",
        "eepro.round",
        source_ref="eepro:different-alias",
    )
    upsert_watch(f.conn, alias, f.corpus.clock.now())
    ctx = f.corpus.snapshot("alias-evidence", BODY, spec=alias)
    generation, _ = f.corpus.stage(ctx)
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    assert ctx.watch_id not in declared
    result = read(f)
    assert result["acquired_pages"] == result["interpreted_pages"] == 1
    assert result["known_pages_accounted_for"] is True
    member = result["members"][0]
    assert member["declared_watch_ids"] == declared
    assert member["evidence_watch_ids"] == [ctx.watch_id]
    assert member["generation_ids"] == [generation]
    assert member["next_action"] == "await_selected_release"


def test_aggregate_generation_on_other_anchor_interprets_each_member_and_report_agrees(event):
    f = event
    contexts, generation = aggregate(f)
    finish_bootstrap(f)
    assert not f.conn.execute(
        "SELECT 1 FROM source_units WHERE watch_id=?", (contexts[1].watch_id,)
    ).fetchone()
    result = read(f)
    assert result["listed_pages"] == result["acquired_pages"] == result["interpreted_pages"] == 2
    assert result["known_pages_accounted_for"] is True
    assert all(member["generation_ids"] == [generation] for member in result["members"])
    for member in result["members"]:
        expected = next(context for context in contexts if context.url == member["request"]["url"])
        assert member["snapshot_ids"] == [expected.snapshot_id]
        assert member["evidence_watch_ids"] == [expected.watch_id]
    with f.db.transaction(immediate=False):
        detailed = report(
            f.conn, f.archive, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now()
        )
    assert detailed["detail"]["interpreted_pages"] == 2
    assert detailed["detail"]["known_pages_accounted_for"] is True


def test_corrupt_aggregate_member_invalidates_interpretation_without_losing_acquisition(event):
    f = event
    _, generation = aggregate(f)
    finish_bootstrap(f)
    assert read(f)["interpreted_pages"] == 2
    manifest = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?", (generation,)
        ).fetchone()[0]
    )
    f.archive.extract_path(manifest[1]["extract_sha256"]).write_bytes(b"{}")
    result = read(f)
    assert result["acquired_pages"] == 2 and result["interpreted_pages"] == 0
    assert result["known_pages_accounted_for"] is False
    assert "extract_artifact_unavailable" in result["blockers"]


def test_parent_failure_cannot_be_hidden_by_valid_alias_page(event):
    f = event
    parent = admit_parent(f, ["one.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)
    assert read(f)["known_pages_accounted_for"] is True
    manifest = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?", (parent,)
        ).fetchone()[0]
    )
    f.archive.blob_path(manifest[0]["body_sha256"]).unlink()
    result = read(f)
    assert result["interpreted_pages"] == 1
    assert result["known_pages_accounted_for"] is False
    assert any(parent["usable"] is False for parent in result["parent_support"])


def test_artifact_budget_exhaustion_is_shared_and_never_becomes_missing_or_complete(event):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    child(f, "one.htm")
    child(f, "two.htm")
    finish_bootstrap(f)
    result = read(f, limits=Limits(decoded_bytes=4))
    assert result["listed_pages"] == 2
    assert result["acquired_pages"] is result["interpreted_pages"] is None
    assert result["acquisition_unknown_pages"] == result["interpretation_unknown_pages"] == 2
    assert all(member["acquired"] is member["interpreted"] is None for member in result["members"])
    assert result["known_pages_accounted_for"] is None
    assert all(member["next_action"] == "verify_retained_evidence" for member in result["members"])


def test_oversized_enumeration_cannot_publish_a_successful_prefix(event):
    f = event
    admit_parent(f, [f"round-{i}.htm" for i in range(129)])
    finish_bootstrap(f)
    result = read(f)
    assert result["listed_pages"] is result["acquired_pages"] is result["interpreted_pages"] is None
    assert result["members"] == [] and result["known_pages_accounted_for"] is None
    assert result["blockers"] == ["candidate_budget"]


def test_unsearched_alias_universe_is_unknown_not_a_negative(event):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    for index in range(3):
        spec = WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            f.parent.url + f"unrelated-{index}.htm",
            "eepro.round",
            source_ref="eepro:other",
        )
        upsert_watch(f.conn, spec, f.corpus.clock.now())
        f.corpus.snapshot(f"unrelated-{index}", BODY, spec=spec)
    result = read(f, limits=Limits(candidates=1))
    assert result["acquired_pages"] is result["interpreted_pages"] is None
    assert result["members"][0]["acquired"] is None
    assert "snapshot_candidate_budget" in result["blockers"]


def test_rehashed_request_identity_still_cannot_change_pinned_enumeration(event):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    f.conn.execute("DROP TRIGGER event_enumeration_member_no_update")
    f.conn.execute("UPDATE source_event_enumeration_members SET request_json='{}'")
    result = read(f)
    assert result["listed_pages"] is result["acquired_pages"] is result["interpreted_pages"] is None
    assert "event_enumeration_evidence_invalid" in result["blockers"]


def test_query_only_snapshot_required_without_silently_changing_caller(event):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    assert not f.conn.in_transaction
    assert f.conn.execute("PRAGMA query_only").fetchone()[0] == 0
    with pytest.raises(ValueError, match="query-only"):
        inventory(
            f.conn, f.archive, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now()
        )
    assert not f.conn.in_transaction
    assert f.conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_inventory_never_reads_whole_artifacts_or_recovers_or_writes(event, monkeypatch):
    f = event
    admit_parent(f, ["one.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)
    before = list(f.conn.iterdump())
    monkeypatch.setattr(Archive, "read_body", Mock(side_effect=AssertionError("whole body")))
    monkeypatch.setattr(Archive, "read_extract", Mock(side_effect=AssertionError("whole extract")))
    with f.db.transaction(immediate=False):
        f.conn.set_authorizer(
            lambda action, *args: (
                sqlite3.SQLITE_OK
                if action
                in (
                    sqlite3.SQLITE_SELECT,
                    sqlite3.SQLITE_READ,
                    sqlite3.SQLITE_FUNCTION,
                    sqlite3.SQLITE_PRAGMA,
                )
                else sqlite3.SQLITE_DENY
            )
        )
        try:
            result = inventory(
                f.conn, f.archive, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now()
            )
        finally:
            f.conn.set_authorizer(None)
    assert result["interpreted_pages"] == 1
    assert list(f.conn.iterdump()) == before


def test_parent_after_cutoff_retains_fallback_reason_in_event_blockers(event):
    from datetime import timedelta

    f = event
    parent = admit_parent(f, ["one.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)
    f.conn.execute("DROP TRIGGER source_generation_immutable")
    f.conn.execute(
        "UPDATE source_generations SET created_at=? WHERE generation_id=?",
        ((f.corpus.clock.now() + timedelta(days=1)).isoformat(), parent),
    )
    result = read(f)
    unsupported = next(row for row in result["parent_support"] if row["generation_id"] == parent)
    assert unsupported["usable"] is False and unsupported["reasons"] == []
    assert unsupported["reason"] == "parent_interpretation_unavailable"
    assert unsupported["reason"] in result["blockers"]
    assert result["interpreted_pages"] == 1
    assert result["known_pages_accounted_for"] is False


@pytest.mark.parametrize(
    "observed_at", ["future", "not-a-timestamp", "2099-01-01", "2026-02-30T00:00:00+00:00"]
)
def test_latest_response_uses_aware_cutoff_and_unknown_times_are_unassessed(event, observed_at):
    from datetime import timedelta

    f = event
    admit_parent(f, ["one.htm"])
    context, _ = child(f, "one.htm")
    finish_bootstrap(f)
    spec = WatchSpec(
        "", "eepro", "round", "GET", context.url, "eepro.round", source_ref="eepro:test"
    )
    response = f.corpus.snapshot("unusable-time-error", BODY, spec=spec)
    value = (
        (f.corpus.clock.now() + timedelta(days=1)).isoformat()
        if observed_at == "future"
        else observed_at
    )
    f.conn.execute(
        "UPDATE snapshots SET fetched_at=?,classification='ServerError',http_status=503 WHERE snapshot_id=?",
        (value, response.snapshot_id),
    )
    result = read(f)
    member = result["members"][0]
    assert "latest_response_ServerError" not in member["blockers"]
    assert "latest_response_ServerError" not in result["blockers"]
    if observed_at == "future":
        assert member["latest_response_assessed"] is True
        assert member["latest_response_reason"] is None
        assert result["interpreted_pages"] == 1
    else:
        assert member["latest_response_assessed"] is False
        assert member["latest_response_reason"] == "response_time_unassessed"


def test_late_member_metadata_failure_retains_denominator_but_not_partial_totals(
    event, monkeypatch
):
    import swingset.schedule.event_inventory as inventory_module

    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)
    original = inventory_module._Evidence.page
    calls = 0

    def damaged(self, member):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("retained member metadata malformed")
        return original(self, member)

    monkeypatch.setattr(inventory_module._Evidence, "page", damaged)
    result = read(f)
    assert calls == 2 and result["listed_pages"] == 2
    assert result["members"] == [] and result["member_records_complete"] is False
    assert result["acquired_pages"] is result["interpreted_pages"] is None
    assert result["acquisition_unknown_pages"] == result["interpretation_unknown_pages"] == 2
    assert result["stage_assessment_complete"] is False
    assert result["known_pages_accounted_for"] is None
