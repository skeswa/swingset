"""Enumeration evidence is pinned independently of canonical identity and publication."""

import json

import pytest
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event
from test_h16_acceptance import candidate_tables
from test_h16_acceptance import release_state as release_state

from swingset.build import closure, event_coverage, service
from swingset.build.closure_manifest import ClosureError, digest
from swingset.build.closure_support import _observations
from swingset.build.closure_validation import validation_scope
from swingset.build.schema import SCHEMAS


def support(f, identifier):
    row = f.conn.execute(
        "SELECT * FROM source_generations WHERE generation_id=?", (identifier,)
    ).fetchone()
    recipe = json.loads(row["recipe_json"])
    return [
        {
            **item,
            "state": "accepted",
            "source_generations": [
                {
                    "generation_id": identifier,
                    "recipe": recipe,
                }
            ],
        }
        for item in _observations(row)
    ]


def captured(f, selected=()):
    return event_coverage.capture(
        f.conn, cutoff=f.corpus.clock.now().isoformat(), selected_support=selected
    )


def test_exact_selected_support_is_not_a_full_local_or_published_total(event):
    f = event
    admit_parent(f, ["one.htm", "two.htm", "unsupported.pdf"])
    context, generation = child(f, "one.htm")
    finish_bootstrap(f)
    selected = support(f, generation)
    witness = captured(f, selected)
    event_coverage.validate(f.conn, witness, selected_support=selected)
    rows = event_coverage.rows(witness, selected_mapping=(), schemas=SCHEMAS)
    row = rows[0]
    assert row["scope_kind"] == "source_event" and row["event_id"] is None
    assert row["listed_pages"] == 3 and row["selected_interpreted_pages"] == 1
    assert (
        row["acquired_pages"]
        is row["interpreted_pages"]
        is row["unavailable_pages"]
        is row["unsupported_pages"]
        is None
    )
    assert not row["enumeration_complete"] and row["represented_pages"] == 0
    facts = {"coverage": rows, "entries": [{"snapshot_id": context.snapshot_id}]}
    event_coverage.finalize(facts, witness)
    assert row["represented_pages"] == 1
    # Removing the last emitted result fact reduces representation even though
    # its admitted interpretation remains part of the pinned source support.
    facts["entries"].clear()
    event_coverage.finalize(facts, witness)
    assert row["represented_pages"] == 0


def test_late_same_size_replacement_does_not_retarget_old_witness(event):
    f = event
    admit_parent(f, ["one.htm"], authority=True)
    finish_bootstrap(f)
    first = captured(f)
    f.corpus.clock.sleep(10)
    admit_parent(f, ["replacement.htm"], authority=True)
    finish_bootstrap(f)
    second = captured(f)
    assert first["entries"][0]["listed_pages"] == second["entries"][0]["listed_pages"] == 1
    assert first["digest"] != second["digest"]
    event_coverage.validate(f.conn, first, selected_support=())
    historical = event_coverage.capture(f.conn, cutoff=first["cutoff"], selected_support=())
    assert historical == first


@pytest.mark.parametrize("mutation", ["revocation", "policy", "snapshot", "member"])
def test_changed_pinned_support_rejected(event, mutation):
    f = event
    parent = admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    witness = captured(f)
    if mutation == "revocation":
        f.conn.execute(
            "UPDATE source_generations SET state='revoked' WHERE generation_id=?", (parent,)
        )
    elif mutation == "policy":
        f.conn.execute("UPDATE admission_policies SET contract_version='other'")
    elif mutation == "snapshot":
        f.conn.execute("UPDATE snapshots SET body_sha256=?", ("0" * 64,))
    else:
        f.conn.execute("DROP TRIGGER event_enumeration_member_no_update")
        f.conn.execute("UPDATE source_event_enumeration_members SET request_json='{}'")
    with pytest.raises(ClosureError, match="event_coverage_support_changed"):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_no_inventory_schema_preserves_legacy_manifest_format(tmp_path):
    import sqlite3

    with sqlite3.connect(":memory:") as conn:
        assert (
            event_coverage.capture(conn, cutoff="2026-01-01T00:00:00+00:00", selected_support=())
            is None
        )
    old = closure.ReleaseClosure("2026-01-01T00:00:00+00:00", (), (), (), (), ())
    assert "event_coverage" not in old.manifest()
    assert "event_coverage" not in closure.public_manifest(old)


def test_bounded_capture_exposes_unassessed_instead_of_partial_total(event, monkeypatch):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    monkeypatch.setattr(event_coverage, "MAX_MEMBERS", 1)
    witness = captured(f)
    row = event_coverage.rows(witness, selected_mapping=(), schemas=SCHEMAS)[0]
    assert row["listed_pages"] is None and row["selected_interpreted_pages"] is None
    assert "event_evidence_row_budget" in row["scope_reasons"]
    event_coverage.validate(f.conn, witness, selected_support=())


def test_forged_member_support_is_rejected_even_with_rehashed_witness(event):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    witness = captured(f)
    witness["entries"][0]["members"][0]["snapshot_ids"] = ["invented"]
    witness["digest"] = digest({k: v for k, v in witness.items() if k != "digest"})
    with pytest.raises(ClosureError):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_actual_build_binds_witness_and_reuses_only_matching_enumeration(release_state):
    f = release_state
    from swingset.schedule.event_enumerations import bootstrap

    while bootstrap(f.db, now=f.clock.now())["may_have_more"]:
        pass
    first = service.build_release(f.db, f.bundle, f.clock, f.run)
    public = json.loads((first.path / "_meta/manifest.json").read_bytes())["release_policy"][
        "closure"
    ]
    assert public["event_coverage"]["digest"]
    private = closure.hydrate(f.conn, public)
    assert private["event_coverage"]["entries"]
    coverage = [
        r for r in candidate_tables(first.path)["coverage"] if r["scope_kind"] == "source_event"
    ]
    assert coverage and any(r["acquired_pages"] is not None for r in coverage)
    assert any(r["unavailable_pages"] == 0 for r in coverage)
    assert all(r["unavailable_pages"] in (None, 0) for r in coverage)
    assert any(r["unsupported_pages"] == 0 for r in coverage)
    assert all(r["unsupported_pages"] in (None, 0) for r in coverage)
    assert service.build_release(f.db, f.bundle, f.clock, f.run).candidate_id == first.candidate_id
    from swingset.build.event_artifacts import artifact_source
    from swingset.fetch.archive import Archive

    with (
        f.db.transaction(),
        validation_scope(f.conn),
        artifact_source(f.conn, Archive(f.db.state_dir)),
    ):
        closure.validate(f.conn, public)
        row = next(entry for entry in private["event_coverage"]["entries"] if entry["members"])
        f.conn.execute("DROP TRIGGER event_enumeration_member_no_update")
        f.conn.execute(
            "UPDATE source_event_enumeration_members SET support_json='[]' WHERE enumeration_id=?",
            (row["enumeration_id"],),
        )
        with pytest.raises(ClosureError):
            closure.validate(f.conn, public)


def test_unchanged_cutoff_reuses_semantic_support_and_declared_membership_changes_it(event):
    from swingset.build.closure_support import support_token

    f = event
    admit_parent(f, ["one.htm"], authority=True)
    finish_bootstrap(f)
    first = captured(f)

    def token(value):
        return support_token({"policies": [], "source_support": [], "event_coverage": value})

    f.corpus.clock.sleep(60)
    again = captured(f)
    assert again["digest"] != first["digest"] and token(again) == token(first)
    admit_parent(f, ["two.htm"], authority=True)
    finish_bootstrap(f)
    assert token(captured(f)) != token(first)


@pytest.mark.parametrize(
    "field,value", [("listed_pages", 900), ("pagination", "complete"), ("parents", [])]
)
def test_positive_summary_cannot_be_forged_by_rehashing(event, field, value):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    witness = captured(f)
    witness["entries"][0][field] = value
    witness["digest"] = digest({k: v for k, v in witness.items() if k != "digest"})
    with pytest.raises(ClosureError):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_finalizer_retains_structural_page_when_identity_is_suppressed(event):
    from swingset.build.suppression import apply_suppressions

    f = event
    admit_parent(f, ["one.htm"])
    context, generation = child(f, "one.htm")
    finish_bootstrap(f)
    witness = captured(f, support(f, generation))
    facts = {
        "coverage": event_coverage.rows(witness, selected_mapping=(), schemas=SCHEMAS),
        "entries": [
            {
                "entry_id": "private-alice",
                "event_id": "event",
                "contest_id": "contest",
                "source": "eepro",
                "snapshot_id": context.snapshot_id,
                "name_raw": "Alice",
                "name_norm": "alice",
                "wsdc_id": 123,
            }
        ],
    }
    apply_suppressions(facts, [{"wsdc_id": 123}])
    event_coverage.finalize(facts, witness)
    assert facts["entries"][0]["wsdc_id"] is None
    assert facts["coverage"][0]["represented_pages"] == 1


def test_parent_budget_exhaustion_discards_partial_proof_and_build_can_validate(event, monkeypatch):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    identifier = captured(f)["entries"][0]["enumeration_id"]
    reader = event_coverage._Reader(f.conn)
    reader.enumeration(identifier)
    monkeypatch.setattr(event_coverage, "MAX_TOTAL_BYTES", reader.bytes + 1)
    witness = captured(f)
    entry = witness["entries"][0]
    assert entry["enumeration_id"] is None and not entry["parents"]
    assert entry["reasons"] == ["event_evidence_byte_budget"]
    event_coverage.validate(f.conn, witness, selected_support=())
    row = event_coverage.rows(witness, selected_mapping=(), schemas=SCHEMAS)[0]
    assert row["listed_pages"] is row["selected_interpreted_pages"] is None


def test_shared_parent_is_verified_once_within_bounded_capture_only(event):
    f = event
    parent = admit_parent(f, ["one.htm"])
    cutoff = f.corpus.clock.now().isoformat()
    reader = event_coverage._Reader(f.conn)
    first = reader.parent(parent, cutoff)
    consumed = reader.bytes
    for _ in range(50):
        assert reader.parent(parent, cutoff) == first
    assert reader.bytes == consumed
    f.conn.execute("UPDATE source_generations SET state='revoked' WHERE generation_id=?", (parent,))
    with pytest.raises(ValueError):
        event_coverage._Reader(f.conn).parent(parent, cutoff)


def test_new_subject_after_cutoff_is_not_added_to_historical_population(event):
    from dataclasses import replace

    from swingset.schedule.watches import upsert_watch

    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    first = captured(f)
    f.corpus.clock.sleep(10)
    parent = replace(f.parent, url="https://eepro.com/results/later/", source_ref="eepro:later")
    upsert_watch(f.conn, parent, f.corpus.clock.now())
    admit_parent(f, ["later.htm"], parent=parent)
    finish_bootstrap(f)
    assert len(captured(f)["entries"]) == 2
    assert event_coverage.capture(f.conn, cutoff=first["cutoff"], selected_support=()) == first


def test_valid_later_enumeration_cannot_be_backdated_by_rehashing_witness(event):
    f = event
    cutoff = f.corpus.clock.now().isoformat()
    f.corpus.clock.sleep(10)
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    witness = captured(f)
    witness["cutoff"] = cutoff
    witness["digest"] = digest({k: v for k, v in witness.items() if k != "digest"})
    with pytest.raises(ClosureError):
        event_coverage.validate(f.conn, witness, selected_support=())


@pytest.mark.parametrize(
    "field,value",
    [
        ("listed_pages", 1),
        ("members", [{"request_id": "invented"}]),
        ("parents", [{"generation_id": "invented"}]),
        ("pagination", "complete"),
    ],
)
def test_unassessed_witness_cannot_carry_positive_proof(event, monkeypatch, field, value):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    monkeypatch.setattr(event_coverage, "MAX_MEMBERS", 1)
    witness = captured(f)
    assert witness["entries"][0]["enumeration_id"] is None
    witness["entries"][0][field] = value
    witness["digest"] = digest({k: v for k, v in witness.items() if k != "digest"})
    with pytest.raises(ClosureError):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_unmatched_selected_snapshot_cannot_gain_membership_behind_cached_proof(event):
    from swingset.build.closure_validation import _ReadSet
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import WatchSpec

    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    upsert_watch(
        f.conn,
        WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            f.parent.url + "unlisted.htm",
            "eepro.round",
            source_ref="eepro:test",
        ),
        f.corpus.clock.now(),
    )
    context, generation = child(f, "unlisted.htm")
    selected = support(f, generation)
    witness = captured(f, selected)
    assert not any(m["snapshot_ids"] for e in witness["entries"] for m in e["members"])
    readset = _ReadSet.capture(
        {
            "selected": [],
            "dependency_sets": [],
            "source_support": selected,
            "event_coverage": witness,
        },
        [],
    )
    before = readset.fingerprint(f.conn)
    assert before is not None
    f.conn.execute(
        "UPDATE snapshots SET url=? WHERE snapshot_id=?",
        (f.parent.url + "one.htm", context.snapshot_id),
    )
    assert readset.fingerprint(f.conn) != before
    with pytest.raises(ClosureError):
        event_coverage.validate(f.conn, witness, selected_support=selected)


def test_serialized_witness_budget_cannot_create_partial_positive_entry(event, monkeypatch):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    monkeypatch.setattr(event_coverage, "MAX_WITNESS_BYTES", 512)
    witness = captured(f)
    entry = witness["entries"][0]
    assert entry["enumeration_id"] is None
    assert entry["reasons"] == ["event_witness_byte_budget"]
    event_coverage.validate(f.conn, witness, selected_support=())
    monkeypatch.setattr(event_coverage, "MAX_WITNESS_BYTES", 1)
    omitted = captured(f)
    assert omitted["entries"] == [] and omitted["omitted_subjects"] == 1
    event_coverage.validate(f.conn, omitted, selected_support=())
