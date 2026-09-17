"""Pinned local observations never turn SQLite-only reuse into a file proof."""

import gzip
import json

import pytest
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event
from test_h16_acceptance import release_state as release_state

from swingset.build import closure, event_coverage, generations, service
from swingset.build.builder import BuildError
from swingset.build.closure_manifest import ClosureError
from swingset.build.closure_validation import validation_scope
from swingset.build.event_artifacts import artifact_source
from swingset.build.schema import SCHEMAS
from swingset.fetch.archive import Archive


def capture(f):
    with (
        f.db.transaction(immediate=False),
        artifact_source(f.conn, f.archive, clock=f.corpus.clock),
    ):
        return event_coverage.capture(
            f.conn, cutoff=f.corpus.clock.now().isoformat(), selected_support=()
        )


def prepare(f):
    admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    context, generation = child(f, "one.htm")
    return context, generation


def row(witness):
    return event_coverage.rows(witness, selected_mapping=(), schemas=SCHEMAS)[0]


def test_full_cutoff_totals_are_distinct_from_selected_and_emitted_support(event):
    f = event
    prepare(f)
    witness = capture(f)
    result = row(witness)
    assert result["listed_pages"] == 2
    assert result["acquired_pages"] == result["interpreted_pages"] == 1
    assert result["acquisition_unknown_pages"] == result["interpretation_unknown_pages"] == 0
    assert result["selected_interpreted_pages"] == result["represented_pages"] == 0
    assert result["unavailable_pages"] == 0 and result["unsupported_pages"] is None
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_shared_request_cap_exposes_null_total_and_explicit_unknown(event, monkeypatch):
    from swingset.build import event_local_coverage

    f = event
    prepare(f)
    monkeypatch.setattr(event_local_coverage, "MAX_REQUESTS", 1)
    witness = capture(f)
    result = row(witness)
    assert result["acquired_pages"] is result["interpreted_pages"] is None
    assert result["acquisition_unknown_pages"] == result["interpretation_unknown_pages"] == 1
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_later_new_evidence_never_retargets_pinned_observations(event, monkeypatch):
    f = event
    prepare(f)
    first = capture(f)
    f.corpus.clock.sleep(20)
    monkeypatch.setattr(f.corpus, "review", lambda _: None)
    child(f, "two.htm")
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, first, selected_support=())
    second = capture(f)
    assert row(first)["interpreted_pages"] == 1
    assert row(second)["interpreted_pages"] == 2
    assert event_coverage.semantic_token(first) != event_coverage.semantic_token(second)


def test_checked_time_alone_is_not_a_semantic_change(event):
    f = event
    prepare(f)
    first = capture(f)
    f.corpus.clock.sleep(60)
    second = capture(f)
    assert first["digest"] != second["digest"]
    assert event_coverage.semantic_token(first) == event_coverage.semantic_token(second)


@pytest.mark.parametrize("kind", ["body", "extract"])
def test_missing_artifact_fails_then_restore_validates_original_witness(event, kind):
    f = event
    prepare(f)
    witness = capture(f)
    positive = next(p for p in witness["local_pages"]["pages"].values() if p["interpreted"])
    receipt = positive["interpretation_support"]["snapshots"][0]
    path = (
        f.archive.blob_path(receipt["body_sha256"])
        if kind == "body"
        else f.archive.extract_path(receipt["extract_sha256"])
    )
    contents = path.read_bytes()
    path.unlink()
    with artifact_source(f.conn, f.archive), pytest.raises(ClosureError, match="artifact_changed"):
        event_coverage.validate(f.conn, witness, selected_support=())
    later = capture(f)
    assert row(later)["interpreted_pages"] == 0
    path.write_bytes(contents)
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


def bootstrap_release(f):
    from swingset.schedule.event_enumerations import bootstrap

    while bootstrap(f.db, now=f.clock.now())["may_have_more"]:
        pass


def artifact(pinned, state, kind):
    local = pinned["event_coverage"]["local_pages"]
    page = next(p for p in local["pages"].values() if p["interpreted"])
    receipt = page["interpretation_support"]["snapshots"][0]
    archive = Archive(state)
    return (
        archive.blob_path(receipt["body_sha256"])
        if kind == "body"
        else archive.extract_path(receipt["extract_sha256"])
    )


@pytest.mark.parametrize("boundary", [2, 3])
@pytest.mark.parametrize(
    "kind,damage",
    [("body", "missing"), ("body", "corrupt"), ("extract", "missing"), ("extract", "corrupt")],
)
def test_actual_completion_rechecks_files_after_cached_sql_proof(
    release_state, monkeypatch, boundary, kind, damage
):
    f = release_state
    bootstrap_release(f)
    original = closure.validate
    complete = generations.complete
    candidates = []
    calls = 0

    def finishing(database, selection, result, **kwargs):
        nonlocal calls
        candidates.append(result)
        before = list(
            f.conn.execute(
                "SELECT generation_id FROM derivation_generations ORDER BY generation_id"
            )
        )

        def checked(conn, value):
            nonlocal calls
            calls += 1
            if calls == boundary:
                path = artifact(selection.context["release_closure"], database.state_dir, kind)
                if damage == "missing":
                    path.unlink()
                else:
                    path.write_bytes(gzip.compress(b"corrupted") if kind == "body" else b"{}")
            return original(conn, value)

        monkeypatch.setattr(closure, "validate", checked)
        try:
            return complete(database, selection, result, **kwargs)
        finally:
            monkeypatch.setattr(closure, "validate", original)
            assert (
                list(
                    f.conn.execute(
                        "SELECT generation_id FROM derivation_generations ORDER BY generation_id"
                    )
                )
                == before
            )

    monkeypatch.setattr(generations, "complete", finishing)
    with pytest.raises(BuildError, match="local_page_artifact_changed"):
        service.build_release(f.db, f.bundle, f.clock, f.run)
    assert calls == boundary
    assert (candidates[0].path / "REJECTED").is_file()
    assert not generations.completed(
        f.conn, candidates[0].candidate_id, candidates[0].manifest_hash
    )


def test_proof_cache_rechecks_local_admission_outside_output_graph(event):
    f = event
    _, generation = prepare(f)
    witness = capture(f)
    readset = event_coverage.read_set(witness)
    before = event_coverage.fingerprint(f.conn, readset)
    f.conn.execute(
        "UPDATE admission_decisions SET state='needs_review' WHERE generation_id=?", (generation,)
    )
    assert event_coverage.fingerprint(f.conn, readset) != before
    with (
        artifact_source(f.conn, f.archive),
        pytest.raises(ClosureError, match="local_page_support_changed"),
    ):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_provider_is_connection_bound_and_required_for_local_proof(event):
    import sqlite3
    from contextlib import closing

    f = event
    prepare(f)
    witness = capture(f)
    with pytest.raises(ClosureError, match="provider_missing"):
        event_coverage.validate(f.conn, witness, selected_support=())
    with closing(sqlite3.connect(":memory:")) as other, artifact_source(other, f.archive):
        with pytest.raises(ClosureError, match="provider_missing"):
            event_coverage.validate(f.conn, witness, selected_support=())


def test_superseded_generation_keeps_exact_historical_support(event):
    f = event
    _, generation = prepare(f)
    witness = capture(f)
    f.conn.execute(
        "UPDATE source_generations SET state='superseded' WHERE generation_id=?", (generation,)
    )
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_normal_reuse_ignores_checked_time_but_pinned_reuse_rechecks_files(
    release_state, monkeypatch
):
    from swingset.build import reuse

    f = release_state
    bootstrap_release(f)
    first = service.build_release(f.db, f.bundle, f.clock, f.run)
    f.clock.sleep(60)
    assert service.build_release(f.db, f.bundle, f.clock, f.run).candidate_id == first.candidate_id
    policy = json.loads((first.path / "_meta/manifest.json").read_bytes())["release_policy"]
    pinned = closure.hydrate(f.conn, policy["closure"])
    artifact(pinned, f.db.state_dir, "body").unlink()
    # Even an already selected cached candidate must undergo file validation.
    monkeypatch.setattr(reuse, "reusable", lambda *args, **kwargs: first)
    with pytest.raises(ClosureError, match="local_page_artifact_changed"):
        service.build_release(f.db, f.bundle, f.clock, f.run)


def test_publication_boundary_rechecks_positive_files_without_database_change(release_state):
    from swingset.publish.safety import StaleCandidateError, _validate_candidate

    f = release_state
    bootstrap_release(f)
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    manifest = json.loads((result.path / "_meta/manifest.json").read_bytes())
    built = json.loads((result.path / "BUILT").read_bytes())
    pinned = closure.hydrate(f.conn, manifest["release_policy"]["closure"])
    before = f.conn.total_changes
    artifact(pinned, f.db.state_dir, "extract").unlink()
    with pytest.raises(StaleCandidateError, match="local_page_artifact_changed"):
        _validate_candidate(f.conn, f.db.state_dir, result.path, built, manifest)
    assert f.conn.total_changes == before


def test_unchanged_sql_proof_does_not_skip_second_file_check(release_state):
    f = release_state
    bootstrap_release(f)
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    manifest = json.loads((result.path / "_meta/manifest.json").read_bytes())["release_policy"][
        "closure"
    ]
    pinned = closure.hydrate(f.conn, manifest)
    with (
        f.db.transaction(),
        validation_scope(f.conn),
        artifact_source(f.conn, Archive(f.db.state_dir)),
    ):
        closure.validate(f.conn, manifest)
        before = f.conn.total_changes
        artifact(pinned, f.db.state_dir, "extract").write_bytes(b"{}")
        with pytest.raises(ClosureError, match="local_page_artifact_changed"):
            closure.validate(f.conn, manifest)
        assert f.conn.total_changes == before


def test_acquisition_only_body_is_also_a_positive_file_claim(event):
    f = event
    _, generation = prepare(f)
    f.conn.execute("DELETE FROM admission_decisions WHERE generation_id=?", (generation,))
    witness = capture(f)
    assert row(witness)["acquired_pages"] == 1 and row(witness)["interpreted_pages"] == 0
    page = next(p for p in witness["local_pages"]["pages"].values() if p["acquired"])
    f.archive.blob_path(page["acquisition_support"]["body_sha256"]).unlink()
    with artifact_source(f.conn, f.archive), pytest.raises(ClosureError, match="artifact_changed"):
        event_coverage.validate(f.conn, witness, selected_support=())


@pytest.mark.parametrize("mutation", ["classification", "source_owner", "decision_time"])
def test_local_support_readset_covers_mutable_semantics_not_only_body_hash(event, mutation):
    f = event
    context, generation = prepare(f)
    witness = capture(f)
    evidence = event_coverage.read_set(witness)
    before = event_coverage.fingerprint(f.conn, evidence)
    if mutation == "classification":
        f.conn.execute(
            "UPDATE snapshots SET classification='NotFound' WHERE snapshot_id=?",
            (context.snapshot_id,),
        )
    elif mutation == "source_owner":
        f.conn.execute("UPDATE watches SET source='wdr' WHERE watch_id=?", (context.watch_id,))
    else:
        f.conn.execute(
            "UPDATE admission_decisions SET decided_at='2099-01-01T00:00:00+00:00' WHERE generation_id=?",
            (generation,),
        )
    assert event_coverage.fingerprint(f.conn, evidence) != before
    with artifact_source(f.conn, f.archive), pytest.raises(ClosureError, match="support_changed"):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_later_accepted_decision_does_not_replace_the_pinned_decision(event):
    f = event
    _, generation = prepare(f)
    witness = capture(f)
    evidence = event_coverage.read_set(witness)
    before = event_coverage.fingerprint(f.conn, evidence)
    f.conn.execute(
        "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,policy_revision) SELECT generation_id,state,reason,'2099-01-01T00:00:00+00:00',policy_revision FROM admission_decisions WHERE generation_id=? AND state='accepted'",
        (generation,),
    )
    assert event_coverage.fingerprint(f.conn, evidence) == before
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_historical_cutoff_records_actual_injected_artifact_observation_time(event):
    f = event
    prepare(f)
    cutoff = f.corpus.clock.now()
    f.corpus.clock.sleep(3600)
    checked_at = f.corpus.clock.now()
    with (
        f.db.transaction(immediate=False),
        artifact_source(f.conn, f.archive, clock=f.corpus.clock),
    ):
        witness = event_coverage.capture(f.conn, cutoff=cutoff.isoformat(), selected_support=())
    assert witness["cutoff"] == cutoff.isoformat()
    assert witness["local_pages"]["verified_at"] == checked_at.isoformat()
    assert row(witness)["usable_verified_at"] == checked_at
    assert row(witness)["evidence_cutoff"] == cutoff
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_build_observes_artifacts_with_injected_clock_after_selecting_cutoff(
    release_state, monkeypatch
):
    from swingset.build import event_local_coverage

    f = release_state
    bootstrap_release(f)
    original = event_local_coverage.capture
    cutoff = f.clock.now()

    def delayed(*args, **kwargs):
        assert kwargs["cutoff"] == cutoff.isoformat()
        f.clock.sleep(7)
        return original(*args, **kwargs)

    monkeypatch.setattr(event_local_coverage, "capture", delayed)
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    public = json.loads((result.path / "_meta/manifest.json").read_bytes())["release_policy"][
        "closure"
    ]
    witness = closure.hydrate(f.conn, public)["event_coverage"]
    assert witness["cutoff"] == cutoff.isoformat()
    assert witness["local_pages"]["verified_at"] == f.clock.now().isoformat()
    assert witness["cutoff"] != witness["local_pages"]["verified_at"]


def test_capture_cannot_substitute_source_cutoff_for_missing_observation_clock(event):
    f = event
    prepare(f)
    with f.db.transaction(immediate=False), artifact_source(f.conn, f.archive):
        with pytest.raises(ValueError, match="injected observation clock"):
            event_coverage.capture(
                f.conn, cutoff=f.corpus.clock.now().isoformat(), selected_support=()
            )


def test_material_capture_and_validation_policy_is_pinned_once_and_changes_identity(
    event, monkeypatch
):
    from dataclasses import asdict, replace

    from swingset.build import event_local_coverage

    f = event
    prepare(f)
    first = capture(f)
    policy = first["local_pages"]["policy"]
    assert policy["max_requests"] == event_local_coverage.MAX_REQUESTS
    assert policy["max_output_bytes"] == event_local_coverage.MAX_BYTES
    assert policy["capture_limits"] == asdict(event_local_coverage.CAPTURE_LIMITS)
    assert policy["validation_limits"] == asdict(event_local_coverage.VALIDATION_LIMITS)
    assert all("limits" not in page for page in first["local_pages"]["pages"].values())
    monkeypatch.setattr(
        event_local_coverage,
        "CAPTURE_LIMITS",
        replace(event_local_coverage.CAPTURE_LIMITS, rows=1023),
    )
    monkeypatch.setattr(
        event_local_coverage,
        "VALIDATION_LIMITS",
        replace(event_local_coverage.VALIDATION_LIMITS, seconds=29),
    )
    second = capture(f)
    assert row(first)["interpreted_pages"] == row(second)["interpreted_pages"]
    assert event_coverage.semantic_token(first) != event_coverage.semantic_token(second)
    # A changed running default does not silently change the old proof's limits.
    assert first["local_pages"]["policy"] == policy
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, first, selected_support=())


@pytest.mark.parametrize("change", ["missing", "unbounded", "missing_limit"])
def test_missing_or_invalid_policy_is_not_replaced_with_runtime_defaults(event, change):
    from swingset.build.closure_manifest import digest

    f = event
    prepare(f)
    witness = capture(f)
    if change == "missing":
        del witness["local_pages"]["policy"]
    elif change == "unbounded":
        witness["local_pages"]["policy"]["max_requests"] = 10000000
    else:
        del witness["local_pages"]["policy"]["capture_limits"]["decoded_bytes"]
    witness["digest"] = digest({key: value for key, value in witness.items() if key != "digest"})
    with artifact_source(f.conn, f.archive), pytest.raises(ClosureError, match="policy_invalid"):
        event_coverage.validate(f.conn, witness, selected_support=())
