"""Only an ordered admitted withdrawal can retire an enumerated request."""

import json
import sqlite3
from dataclasses import replace

import pytest
from test_cycle import overrides as overrides
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event

from swingset.admission.enumeration_evidence import members
from swingset.admission.event_retirement import verify_edge
from swingset.admission.page_evidence import Limits, Session
from swingset.fetch.archive import canonical, digest
from swingset.schedule.watches import upsert_watch


def edge(f, *, limits=None):
    with f.db.transaction(immediate=False):
        identifier = f.conn.execute(
            "SELECT enumeration_id FROM source_event_inventory WHERE source='eepro' AND source_ref='eepro:test'"
        ).fetchone()[0]
        session = Session(
            f.conn,
            f.archive,
            cutoff=f.corpus.clock.now(),
            now=f.corpus.clock.now(),
            limits=limits or Limits(),
        )
        current = members(
            session, enumeration_id=identifier, source="eepro", source_ref="eepro:test"
        )
        return verify_edge(
            session,
            source="eepro",
            source_ref="eepro:test",
            successor_id=identifier,
            successor_members=current,
        )


def replacement(f, *, authority=True):
    before = admit_parent(f, ["old.htm", "keep.htm"])
    finish_bootstrap(f)
    after = admit_parent(f, ["keep.htm"], authority=authority)
    finish_bootstrap(f)
    return before, after


def test_admitted_withdrawal_has_stable_exact_proof(event):
    before, after = replacement(event)
    value = edge(event)
    assert value["assessment"] == "verified" and len(value["verified_retirement_ids"]) == 1
    assert value["proof"]["replacement"]["generation_id"] == after
    assert value["proof"]["claims"][0]["generation_id"] == before
    assert (
        value["proof"]["replacement"]["decision_id"] > value["proof"]["predecessor"]["decision_id"]
    )
    event.corpus.clock.sleep(100)
    assert edge(event, limits=Limits(seconds=1))["proof_digest"] == value["proof_digest"]


def test_nonauthoritative_omission_does_not_retire(event):
    replacement(event, authority=False)
    assert edge(event)["assessment"] == "not_applicable"
    assert edge(event)["verified_retirement_ids"] == []


@pytest.mark.parametrize("support", ["parent", "child"])
def test_independent_support_survives_authoritative_index_withdrawal(event, support):
    f = event
    admit_parent(f, ["old.htm", "keep.htm"])
    if support == "parent":
        other = replace(f.parent, url="https://eepro.com/results/other/")
        upsert_watch(f.conn, other, f.corpus.clock.now())
        admit_parent(f, [f.parent.url + "old.htm"], parent=other)
    else:
        child(f, "old.htm")
    finish_bootstrap(f)
    admit_parent(f, ["keep.htm"], authority=True)
    finish_bootstrap(f)
    assert edge(f)["assessment"] == "not_applicable"


def test_event_predecessor_may_be_created_by_another_unit(event):
    f = event
    old = admit_parent(f, ["old.htm", "keep.htm"])
    other = replace(f.parent, url="https://eepro.com/results/other/")
    upsert_watch(f.conn, other, f.corpus.clock.now())
    intervening = admit_parent(f, ["other.htm"], parent=other)
    finish_bootstrap(f)
    admit_parent(f, ["keep.htm"], authority=True)
    finish_bootstrap(f)
    value = edge(f)
    assert value["assessment"] == "verified"
    assert value["proof"]["predecessor"]["generation_id"] == intervening
    assert value["proof"]["claims"][0]["generation_id"] == old


def forge_successor(f, *, generation=None, decision=None, remove=None):
    """Corrupt metadata consistently; accepted output must still defeat it."""
    identifier = f.conn.execute(
        "SELECT enumeration_id FROM source_event_inventory WHERE source_ref='eepro:test'"
    ).fetchone()[0]
    header = dict(
        f.conn.execute(
            "SELECT * FROM source_event_enumerations WHERE enumeration_id=?", (identifier,)
        ).fetchone()
    )
    rows = [
        dict(r)
        for r in f.conn.execute(
            "SELECT * FROM source_event_enumeration_members WHERE enumeration_id=? ORDER BY request_id",
            (identifier,),
        )
    ]
    decoded = [
        dict(
            request_id=r["request_id"],
            request=json.loads(r["request_json"]),
            support=json.loads(r["support_json"]),
            first_known_at=r["first_known_at"],
        )
        for r in rows
        if remove is None or not json.loads(r["request_json"])["url"].endswith(remove)
    ]
    content = dict(
        source=header["source"],
        source_ref=header["source_ref"],
        predecessor=header["predecessor_id"],
        generation_id=generation or header["generation_id"],
        parents=json.loads(header["parent_support_json"]),
        members=decoded,
        pagination=header["pagination"],
    )
    fake = "enumeration_" + digest(canonical(content))
    if fake == identifier:
        f.conn.execute("DROP TRIGGER event_enumeration_no_update")
        f.conn.execute(
            "UPDATE source_event_enumerations SET decision_id=? WHERE enumeration_id=?",
            (decision, identifier),
        )
        return
    header.update(
        enumeration_id=fake,
        generation_id=content["generation_id"],
        decision_id=decision or header["decision_id"],
        membership_digest=digest(canonical([r["request_id"] for r in decoded])),
    )
    f.conn.execute(
        "INSERT INTO source_event_enumerations VALUES(" + ",".join("?" for _ in header) + ")",
        tuple(header.values()),
    )
    for row in decoded:
        f.conn.execute(
            "INSERT INTO source_event_enumeration_members VALUES(?,?,?,?,?)",
            (
                fake,
                row["request_id"],
                json.dumps(row["request"]),
                json.dumps(row["support"]),
                row["first_known_at"],
            ),
        )
    f.conn.execute(
        "UPDATE source_event_inventory SET enumeration_id=? WHERE source_ref='eepro:test'", (fake,)
    )


def test_old_accepted_replacement_cannot_authorize_later_claims(event):
    f = event
    old = admit_parent(f, ["keep.htm"], authority=True)
    old_decision = f.conn.execute(
        "SELECT decision_id FROM admission_decisions WHERE generation_id=? AND state='accepted'",
        (old,),
    ).fetchone()[0]
    replacement(f)
    forge_successor(f, generation=old, decision=old_decision)
    value = edge(f)
    assert value["assessment"] == "unassessed"
    assert value["reason"] == "retirement_replacement_precedes_event"


def test_unpinned_decision_and_auxiliary_removed_json_cannot_invent_retirement(event):
    f = event
    before, _ = replacement(f)
    f.conn.execute("DROP TRIGGER event_enumeration_no_update")
    f.conn.execute("UPDATE source_event_enumerations SET removed_json='[\"fake\"]'")
    assert edge(f)["assessment"] == "verified"
    old = f.conn.execute(
        "SELECT decision_id FROM admission_decisions WHERE generation_id=? AND state='accepted'",
        (before,),
    ).fetchone()[0]
    f.conn.execute(
        "UPDATE source_event_enumerations SET decision_id=? WHERE enumeration_id=(SELECT enumeration_id FROM source_event_inventory WHERE source_ref='eepro:test')",
        (old,),
    )
    assert edge(f)["assessment"] == "unassessed"


def test_hash_consistent_omission_of_still_declared_request_fails(event):
    f = event
    replacement(f)
    forge_successor(f, remove="keep.htm")
    assert edge(f)["reason"] == "retirement_request_still_declared"


@pytest.mark.parametrize("target", ["old", "replacement"])
@pytest.mark.parametrize("damage", ["body", "revocation", "policy"])
def test_lost_incompatible_or_revoked_parent_is_not_retirement(event, target, damage):
    f = event
    old, after = replacement(f)
    generation = old if target == "old" else after
    if damage == "revocation":
        f.conn.execute(
            "UPDATE source_generations SET state='revoked' WHERE generation_id=?", (generation,)
        )
    elif damage == "policy":
        f.conn.execute("UPDATE admission_policies SET contract_version='unknown'")
    else:
        manifest = json.loads(
            f.conn.execute(
                "SELECT manifest_json FROM source_generations WHERE generation_id=?", (generation,)
            ).fetchone()[0]
        )
        f.archive.blob_path(manifest[0]["body_sha256"]).unlink()
    assert edge(f)["assessment"] == "unassessed"


def test_shared_budget_exhaustion_is_unknown(event):
    replacement(event)
    value = edge(event, limits=Limits(decoded_bytes=32))
    assert value["assessment"] == "unassessed" and value["verified_retirement_ids"] is None


def test_unrelated_predecessor_body_is_not_needed_for_admission_order(event):
    f = event
    admit_parent(f, ["old.htm", "keep.htm"])
    other = replace(f.parent, url="https://eepro.com/results/other/")
    upsert_watch(f.conn, other, f.corpus.clock.now())
    unrelated = admit_parent(f, ["other.htm"], parent=other)
    finish_bootstrap(f)
    admit_parent(f, ["keep.htm"], authority=True)
    finish_bootstrap(f)
    manifest = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?", (unrelated,)
        ).fetchone()[0]
    )
    f.archive.blob_path(manifest[0]["body_sha256"]).unlink()
    assert edge(f)["assessment"] == "verified"


def runtime_ready(f):
    from test_event_pressure import config, enroll

    replacement(f)
    enroll(f, config())


def observe(f, **kwargs):
    from test_event_progress import observe as refresh

    return refresh(f, **kwargs)


def retirement_view(f):
    from test_event_accounting import event_view

    return event_view(f)["page_retirement"]


def receipt_count(f):
    return f.conn.execute("SELECT count(*) FROM event_retirement_receipts").fetchone()[0]


def test_runtime_records_one_withdrawal_across_policy_change_and_restart(event):
    from test_event_pressure import config

    from swingset.schedule.event_accounting_report import report

    f = event
    runtime_ready(f)
    assert observe(f)["retirement_receipts"] == 1
    current = retirement_view(f)
    assert current["assessment"] == "verified" and current["verified_retirement_count"] == 1
    assert current["whole_event_retired"] is None and not current["complete_retirement_history"]
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()
    f.corpus.clock.sleep(10)
    observe(f, limits=Limits(seconds=1))
    assert receipt_count(f) == 1
    assert retirement_view(f)["assessment"] == "unassessed"  # Different captured execution policy.
    observe(f)
    assert receipt_count(f) == 1 and retirement_view(f)["assessment"] == "verified"
    with sqlite3.connect((f.db.state_dir / "state.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        result = report(
            conn,
            f.archive,
            config(),
            now=f.corpus.clock.now(),
            source="eepro",
            source_ref="eepro:test",
        )
        assert result["events"][0]["page_retirement"]["assessment"] == "verified"
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        f.conn.execute("DELETE FROM event_retirement_receipts")


@pytest.mark.parametrize(
    "damage", ["whole_event", "all_predecessor", "wrong_predecessor", "unknown_ids"]
)
def test_report_cannot_promote_corrupt_mutable_observation(event, damage):
    f = event
    runtime_ready(f)
    observe(f)
    row = f.conn.execute("SELECT result_json FROM event_retirement_observations").fetchone()
    value = json.loads(row[0])
    if damage == "whole_event":
        value.update(whole_event_retired=True, complete_retirement_history=True)
    elif damage == "all_predecessor":
        value["all_predecessor_obligations_retired"] = True
    elif damage == "wrong_predecessor":
        value["predecessor_id"] = "fabricated"
    else:
        value["assessment"] = "unassessed"
    f.conn.execute("UPDATE event_retirement_observations SET result_json=?", (json.dumps(value),))
    result = retirement_view(f)
    assert result["whole_event_retired"] is None and not result["complete_retirement_history"]
    if damage in {"wrong_predecessor", "unknown_ids"}:
        assert result["assessment"] == "unassessed" and result["verified_retirement_ids"] is None
    else:
        assert result["all_predecessor_obligations_retired"] is False


def test_revocation_invalidates_current_proof_without_deleting_history(event):
    f = event
    runtime_ready(f)
    observe(f)
    generation = f.conn.execute("SELECT generation_id FROM event_retirement_receipts").fetchone()[0]
    f.conn.execute(
        "UPDATE source_generations SET state='revoked' WHERE generation_id=?", (generation,)
    )
    assert retirement_view(f)["assessment"] == "unassessed"
    observe(f)
    assert retirement_view(f)["assessment"] == "unassessed" and receipt_count(f) == 1
    assert retirement_view(f)["latest_historical_receipt"]


def test_lost_and_restored_proof_files_do_not_duplicate_withdrawal(event):
    f = event
    runtime_ready(f)
    observe(f)
    generation = f.conn.execute("SELECT generation_id FROM event_retirement_receipts").fetchone()[0]
    manifest = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?", (generation,)
        ).fetchone()[0]
    )
    path = f.archive.blob_path(manifest[0]["body_sha256"])
    body = path.read_bytes()
    path.unlink()
    f.corpus.clock.sleep(86401)
    observe(f)
    assert retirement_view(f)["assessment"] == "unassessed"
    path.write_bytes(body)
    f.corpus.clock.sleep(86401)
    observe(f)
    assert retirement_view(f)["assessment"] == "verified" and receipt_count(f) == 1


def test_priority_visit_preserves_accounting_and_member_cursor(event):
    from test_event_accounting import complete, receipts, settle

    f = event
    complete(f)
    settle(f)
    before = receipts(f)
    f.conn.execute("UPDATE event_retirement_observations SET retry_fresh=1")
    cursor = tuple(f.conn.execute("SELECT * FROM event_progress_scans").fetchone())
    assert observe(f)["checked_pages"] == 0
    assert receipts(f) == before
    after = tuple(f.conn.execute("SELECT * FROM event_progress_scans").fetchone())
    assert cursor[:4] == after[:4]


def test_partial_allowance_edge_retries_once_fresh_then_yields_to_pages(event, monkeypatch):
    from swingset.schedule import event_retirement

    f = event
    runtime_ready(f)
    calls = []

    def expensive(session, **kwargs):
        calls.append(session.reader.rows)
        session.reader.rows = session.limits.rows
        return dict(
            format=event_retirement.FORMAT,
            assessment="unassessed",
            predecessor_id=None,
            successor_id=kwargs["successor_id"],
            verified_retirement_ids=None,
            all_predecessor_obligations_retired=None,
            reason="row_budget",
            proof=None,
        )

    monkeypatch.setattr(event_retirement, "verify_edge", expensive)
    first = observe(f)
    assert first["checked_pages"] == 1
    assert (
        f.conn.execute("SELECT retry_fresh FROM event_retirement_observations").fetchone()[0] == 1
    )
    second = observe(f)
    assert second["checked_pages"] == 0
    assert (
        f.conn.execute("SELECT retry_fresh FROM event_retirement_observations").fetchone()[0] == 0
    )
    third = observe(f)
    assert third["checked_pages"] == 1 and len(calls) == 2
    assert retirement_view(f)["assessment"] == "unassessed"


def test_parent_priority_path_still_processes_retirement(event):
    from test_event_accounting import complete, settle

    f = event
    complete(f)
    settle(f)
    f.conn.execute("DELETE FROM event_accounting_support_observations")
    f.conn.execute("DELETE FROM event_retirement_observations")
    assert observe(f)["checked_pages"] == 0
    assert f.conn.execute("SELECT 1 FROM event_retirement_observations").fetchone()


def test_concurrent_fence_and_failure_roll_back_retirement(event, monkeypatch):
    from swingset.schedule import event_retirement

    f = event
    runtime_ready(f)
    original = event_retirement.verify_edge

    def change(session, **kwargs):
        value = original(session, **kwargs)
        with sqlite3.connect(f.db.state_dir / "state.sqlite") as other:
            other.execute("UPDATE event_pressure_state SET epoch=epoch+1")
        return value

    monkeypatch.setattr(event_retirement, "verify_edge", change)
    assert observe(f)["discarded_events"] == 1 and receipt_count(f) == 0
    assert not f.conn.execute("SELECT 1 FROM event_retirement_observations").fetchone()
    monkeypatch.setattr(event_retirement, "verify_edge", original)
    f.conn.execute(
        "CREATE TRIGGER failed_retirement BEFORE INSERT ON event_retirement_receipts BEGIN SELECT RAISE(ABORT,'retirement write failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="retirement write failure"):
        observe(f)
    assert not f.conn.execute("SELECT 1 FROM event_retirement_observations").fetchone()
    assert not f.conn.execute("SELECT 1 FROM event_progress_observations").fetchone()


def test_empty_successor_only_retires_its_known_predecessor_obligations(event):
    f = event
    admit_parent(f, ["old.htm"])
    finish_bootstrap(f)
    shifted = replace(f.parent, source_ref="eepro:other")
    f.conn.execute(
        "UPDATE watches SET source_ref=? WHERE watch_id=?", (shifted.source_ref, shifted.watch_id)
    )
    admit_parent(f, ["new.htm"], parent=shifted, authority=True)
    finish_bootstrap(f)
    value = edge(f)
    assert value["assessment"] == "verified"
    assert value["all_predecessor_obligations_retired"] is True
    assert len(value["verified_retirement_ids"]) == 1


def test_skipped_edge_history_is_not_fabricated(event):
    f = event
    admit_parent(f, ["old1.htm", "old2.htm", "keep.htm"])
    finish_bootstrap(f)
    admit_parent(f, ["old2.htm", "keep.htm"], authority=True)
    finish_bootstrap(f)
    admit_parent(f, ["keep.htm"], authority=True)
    finish_bootstrap(f)
    value = edge(f)
    from swingset.schedule.event_evidence import request, request_id

    assert value["verified_retirement_ids"] == [
        request_id(request("eepro", "GET", f.parent.url + "old2.htm"))
    ]


def test_checkpoint_keeps_receipt_and_epoch_invalidation_keeps_it_historical(event, tmp_path):
    from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint
    from swingset.state.db import SCHEMA_VERSION

    f = event
    runtime_ready(f)
    observe(f)
    tables = ("event_retirement_receipts", "event_retirement_observations")
    expected = {t: list(map(tuple, f.conn.execute("SELECT * FROM " + t))) for t in tables}
    saved = create_checkpoint(
        f.db.state_dir,
        f.conn,
        tmp_path / "saved",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    target = tmp_path / "restored"
    restore_checkpoint(saved.path, target, maximum_schema_version=SCHEMA_VERSION)
    with sqlite3.connect((target / "state.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        assert {t: list(conn.execute("SELECT * FROM " + t)) for t in tables} == expected
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    f.conn.execute("UPDATE event_pressure_state SET epoch=epoch+1")
    assert retirement_view(f)["assessment"] == "unassessed" and receipt_count(f) == 1


def test_paused_real_cycle_cannot_verify_retirement(event, tmp_path, overrides):
    from test_event_progress import (
        test_paused_cycle_does_not_verify_artifacts_or_record_observed_progress,
    )

    test_paused_cycle_does_not_verify_artifacts_or_record_observed_progress(
        event, overrides, tmp_path
    )
    assert not event.conn.execute("SELECT 1 FROM event_retirement_observations").fetchone()
    assert receipt_count(event) == 0
