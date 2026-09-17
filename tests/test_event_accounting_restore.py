"""Actual activation preserves history while requiring fresh event accounting."""

import json
from copy import copy
from types import SimpleNamespace

import pytest
from test_event_accounting import event_view, settle
from test_event_completion_restore import file_hashes
from test_event_enumerations import admit_parent, child, finish_bootstrap, view
from test_event_enumerations import event as event
from test_event_gaps import response
from test_event_pressure import config, enroll
from test_event_progress import fetch, history, interpret, observe

from swingset.backup.checkpoint import create_checkpoint, restore_from_checkpoint
from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.state.db import SCHEMA_VERSION, open_database

HISTORY = (
    "event_stage_operations",
    "event_progress_receipts",
    "event_accounting_receipts",
    "event_retirement_receipts",
)
OBSERVATIONS = (
    "event_progress_policies",
    "event_progress_observations",
    "event_progress_scans",
    "event_progress_cursor",
    "event_accounting_support_observations",
    "event_retirement_observations",
    "event_gap_observations",
    "event_gap_revisions",
)


def rows(conn, tables):
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    }


def ready(f):
    admit_parent(f, ["old.htm", "seed.htm", "one.htm", "gone.htm"])
    child(f, "seed.htm")
    finish_bootstrap(f)
    enroll(f, config())
    observe(f)
    fetched = fetch(f)
    assert fetched.snapshot_id
    assert observe(f)["qualified_progress"] == 1
    interpret(f, fetched.snapshot_id)
    assert observe(f)["qualified_progress"] == 1
    result_generation = f.conn.execute(
        "SELECT generation_id FROM event_progress_receipts JOIN event_stage_operations USING(operation_id) "
        "WHERE event_progress_receipts.stage='interpreted'"
    ).fetchone()[0]
    response(f, "gone.htm", label="unavailable-before-checkpoint")
    parent_generation = admit_parent(f, ["seed.htm", "one.htm", "gone.htm"], authority=True)
    finish_bootstrap(f)
    enroll(f, config())
    settle(f)
    value = event_view(f)
    assert value["assessment"] == "locally_accounted"
    assert value["page_accounting"] == dict(positive=3, negative=0, unknown=0)
    assert value["unavailable"]["positive"] == 1
    assert value["page_retirement"]["assessment"] == "verified"
    assert value["page_retirement"]["verified_retirement_count"] == 1
    assert len(rows(f.conn, HISTORY)["event_progress_receipts"]) == 2
    gap = dict(
        f.conn.execute("SELECT * FROM event_gap_observations WHERE availability=1").fetchone()
    )
    return SimpleNamespace(
        parent_generation=parent_generation,
        result_generation=result_generation,
        gap=gap,
        historical=rows(f.conn, HISTORY),
        observations=rows(f.conn, OBSERVATIONS),
        epoch=f.conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0],
    )


def activate(f, tmp_path):
    saved = create_checkpoint(
        f.db.state_dir,
        f.conn,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    hashes = file_hashes(saved.path)
    heads = []
    target = tmp_path / "activated"
    restore_from_checkpoint(
        saved.path,
        target,
        SimpleNamespace(head=lambda: heads.append(None)),
        FakeClock(f.corpus.clock.now()),
        lock_timeout=1,
    )
    assert len(heads) == 2
    assert not (target / "RESTORE_PENDING").exists()
    assert file_hashes(saved.path) == hashes
    return target, saved.path, hashes


def branch(f, database):
    corpus = copy(f.corpus)
    corpus.db, corpus.conn = database, database.connection
    corpus.archive = Archive(database.state_dir)
    corpus.clock = FakeClock(f.corpus.clock.now())
    return SimpleNamespace(
        db=database,
        conn=database.connection,
        corpus=corpus,
        archive=corpus.archive,
        parent=f.parent,
    )


def assert_activated(f, before):
    assert (
        f.conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0] == before.epoch + 1
    )
    assert rows(f.conn, HISTORY) == before.historical
    assert rows(f.conn, OBSERVATIONS) == before.observations
    value = event_view(f)
    assert value["assessment"] == "unassessed"
    assert value["unavailable"]["unknown"] == 3
    assert value["page_retirement"]["assessment"] == "unassessed"
    assert value["page_retirement"]["latest_historical_receipt"] is not None
    progress = history(f)
    assert progress["progress"] and progress["observations"]
    assert not any(row["fresh"] for row in progress["observations"])
    assert rows(f.conn, HISTORY) == before.historical  # Reporting cannot synthesize transitions.


def semantic_accounting(f):
    value = event_view(f)
    return {
        key: value[key]
        for key in (
            "enumeration_id",
            "assessment",
            "listed_pages",
            "stages",
            "unavailable",
            "page_accounting",
            "parents",
            "pagination",
            "current_stage_authority",
        )
    }


def test_activated_observer_preserves_exact_history_then_matches_uninterrupted_resume(
    event, tmp_path
):
    f = event
    before = ready(f)
    target, checkpoint, hashes = activate(f, tmp_path)
    with open_database(target) as database:
        twin = branch(f, database)
        assert_activated(twin, before)
        assert event_view(f)["assessment"] == "locally_accounted"
        assert view(twin)["known_pages_accounted_for"] is True  # Independent live artifact check.
        settle(twin)
        settle(f)
        assert semantic_accounting(twin) == semantic_accounting(f)
        assert rows(twin.conn, HISTORY) == before.historical == rows(f.conn, HISTORY)
        assert event_view(twin)["page_retirement"]["assessment"] == "verified"
        # Rechecking after activation changes freshness tokens, not exact response support.
        restored_gap = dict(
            twin.conn.execute(
                "SELECT * FROM event_gap_observations WHERE availability=1"
            ).fetchone()
        )
        for key in ("request_id", "source_revision", "evidence_json", "evidence_digest"):
            assert restored_gap[key] == before.gap[key]
        assert restored_gap["token_json"] != before.gap["token_json"]

        def resume(branch):
            fetched = fetch(branch, "gone.htm")
            assert fetched.snapshot_id
            assert observe(branch)["qualified_progress"] == 1
            assert event_view(branch)["assessment"] == "unfinished"
            interpret(branch, fetched.snapshot_id)
            assert observe(branch)["qualified_progress"] == 1
            settle(branch)
            assert event_view(branch)["unavailable"]["positive"] == 0
            assert event_view(branch)["stages"]["interpreted"]["positive"] == 3
            assert (
                len(rows(branch.conn, ("event_retirement_receipts",))["event_retirement_receipts"])
                == 1
            )
            # Original historical receipts remain byte-for-byte prefixes after actual new success.
            current = rows(branch.conn, HISTORY)
            for table in HISTORY:
                assert current[table][: len(before.historical[table])] == before.historical[table]
            return semantic_accounting(branch), view(branch)

        assert resume(twin) == resume(f)
        assert rows(twin.conn, ("event_stage_operations",)) == rows(
            f.conn, ("event_stage_operations",)
        )
        assert not twin.conn.execute("PRAGMA foreign_key_check").fetchall()
    assert file_hashes(checkpoint) == hashes


@pytest.mark.parametrize(
    "damage", ["revoke_parent", "revoke_result", "change_gap", "lose_gap_body"]
)
def test_activated_restore_cannot_revive_accounting_from_rejected_or_changed_support(
    event, tmp_path, damage
):
    f = event
    before = ready(f)
    gap_support = json.loads(before.gap["evidence_json"])["unavailability_support"]
    original_body = f.archive.blob_path(gap_support["body_sha256"]).read_bytes()
    target, checkpoint, hashes = activate(f, tmp_path)
    with open_database(target) as database:
        twin = branch(f, database)
        assert_activated(twin, before)

        def damage_support(branch):
            if damage in {"revoke_parent", "revoke_result"}:
                generation = (
                    before.parent_generation
                    if damage == "revoke_parent"
                    else before.result_generation
                )
                branch.conn.execute(
                    "UPDATE source_generations SET state='revoked' WHERE generation_id=?",
                    (generation,),
                )
            elif damage == "change_gap":
                branch.conn.execute(
                    "UPDATE snapshots SET classification='Blocked',http_status=403 WHERE snapshot_id='unavailable-before-checkpoint'"
                )
            else:
                support = json.loads(before.gap["evidence_json"])["unavailability_support"]
                branch.archive.blob_path(support["body_sha256"]).unlink()

        damage_support(twin)
        damage_support(f)  # Equivalent evidence change in the uninterrupted branch.
        for branch_state in (twin, f):
            for _ in range(3):
                assert observe(branch_state)["qualified_progress"] == 0
                assert event_view(branch_state)["assessment"] != "locally_accounted"
            current = rows(branch_state.conn, HISTORY)
            for table in (
                "event_stage_operations",
                "event_progress_receipts",
                "event_retirement_receipts",
            ):
                assert current[table] == before.historical[table]
            assert (
                current["event_accounting_receipts"][
                    : len(before.historical["event_accounting_receipts"])
                ]
                == before.historical["event_accounting_receipts"]
            )
            expected = "unassessed" if damage == "lose_gap_body" else "unfinished"
            assert event_view(branch_state)["assessment"] == expected
            if damage == "revoke_parent":
                assert event_view(branch_state)["page_retirement"]["assessment"] == "unassessed"
        restored, uninterrupted = semantic_accounting(twin), semantic_accounting(f)
        if damage == "lose_gap_body":
            # Activation invalidated the restored parents. Until every page is
            # accounted, parent verification waits; the uninterrupted branch
            # still has its valid sampled parents. Neither branch is complete.
            assert restored.pop("parents") == dict(positive=0, negative=0, unknown=3)
            assert uninterrupted.pop("parents") == dict(positive=3, negative=0, unknown=0)
        assert restored == uninterrupted
        assert view(twin) == view(f)
        if damage == "lose_gap_body":
            for branch_state in (twin, f):
                branch_state.archive.blob_path(gap_support["body_sha256"]).write_bytes(
                    original_body
                )
                settle(branch_state)
                assert rows(
                    branch_state.conn,
                    (
                        "event_stage_operations",
                        "event_progress_receipts",
                        "event_retirement_receipts",
                    ),
                ) == {
                    table: before.historical[table]
                    for table in (
                        "event_stage_operations",
                        "event_progress_receipts",
                        "event_retirement_receipts",
                    )
                }
            assert semantic_accounting(twin) == semantic_accounting(f)
        assert not twin.conn.execute("PRAGMA foreign_key_check").fetchall()
    assert file_hashes(checkpoint) == hashes
