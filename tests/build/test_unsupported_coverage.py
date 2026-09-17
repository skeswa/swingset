"""Only exact, retained critical-field evidence classifies unsupported pages."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_admission import BODY
from test_event_enumerations import child, view
from test_event_enumerations import event as event
from test_event_local_coverage import capture, row
from test_event_unsupported import prepare, unsupported
from test_h16_acceptance import release_state as release_state

from swingset.admission.page_evidence import Limits, Session
from swingset.build import closure, event_coverage, service
from swingset.build.closure_manifest import ClosureError, digest
from swingset.build.closure_validation import validation_scope
from swingset.build.event_artifacts import artifact_source
from swingset.fetch.archive import Archive
from swingset.model.canonical import Contest, Round
from swingset.project.contests import project_event
from swingset.schedule.event_evidence import request
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec


def check(f, *, cutoff=None, limits=None):
    with f.db.transaction(immediate=False):
        before = f.conn.total_changes
        value = Session(
            f.conn,
            f.archive,
            cutoff=cutoff or f.corpus.clock.now(),
            now=f.corpus.clock.now(),
            limits=limits or Limits(),
        ).verify_request(
            request("eepro", "GET", f.parent.url + "one.htm"),
            classify_unavailability=True,
            classify_unsupported=True,
        )
        assert f.conn.total_changes == before
        return value


def test_explicit_unknown_is_body_backed_gap_not_success(event):
    f = event
    prepare(f)
    context, generation = unsupported(f)
    proof = check(f)
    assert proof["unsupported"] is True and proof["unavailable"] is False
    assert proof["acquired"] is True and proof["interpreted"] is False
    assert proof["unsupported_support"]["generation_id"] == generation
    assert proof["unsupported_support"]["accepted_decision"] is None
    local = view(f)
    assert local["unsupported_pages"] == 1 and local["interpreted_pages"] == 1
    assert local["known_pages_accounted_for"] is True
    assert (
        next(p for p in local["members"] if p["unsupported"])["next_action"] == "unsupported_review"
    )
    witness = capture(f)
    assert row(witness)["unsupported_pages"] == 1
    assert row(witness)["acquired_pages"] == 2 and row(witness)["interpreted_pages"] == 1
    assert row(witness)["event_id"] is None
    reads = event_coverage.read_set(witness)["local_pages"]
    assert generation in reads["generations"] and context.snapshot_id in reads["snapshots"]
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


@pytest.mark.parametrize("generic,explicit", [(True, True), (False, False)])
def test_generic_failure_or_bare_guard_code_is_not_unsupported_evidence(event, generic, explicit):
    f = event
    prepare(f)
    unsupported(f, generic=generic, explicit=explicit)
    proof = check(f)
    assert proof["unsupported"] is False and proof["interpreted"] is False
    assert proof["unsupported_support"] is None
    assert view(f)["known_pages_accounted_for"] is False


def test_older_successful_interpretation_takes_precedence(event):
    f = event
    prepare(f)
    child(f, "one.htm")
    unsupported(f)
    proof = check(f)
    assert proof["interpreted"] is True and proof["unsupported"] is False


def test_numeric_projection_gap_keeps_source_interpretation_and_explicit_finding(event):
    f = event
    prepare(f)
    body = Path("src/swingset/admission/fixtures/eepro-numeric.body").read_bytes()
    spec = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        f.parent.url + "one.htm",
        "eepro.round",
        source_ref="eepro:test",
    )
    context = f.corpus.snapshot("numeric", body, spec=spec)
    generation, report = f.corpus.stage(context, body=body)
    assert not report.failures
    assert f.corpus.admit(generation) == "accepted"
    proof = check(f)
    assert proof["interpreted"] is True and proof["unsupported"] is False
    f.conn.execute(
        "INSERT INTO source_event_map VALUES ('eepro','eepro:test','mixed-event','alias',1)"
    )
    projection = project_event(
        f.conn, "mixed-event", f.corpus.clock.now().isoformat(), f.corpus.run
    )
    assert any(isinstance(r, Contest) and r.parse_status == "unsupported" for r in projection.rows)
    assert any(isinstance(r, Round) for r in projection.rows)
    assert any(f.kind == "parse_gap" for f in projection.findings)


def test_cutoff_and_bounded_candidates_keep_classification_honest(event):
    f = event
    prepare(f)
    cutoff = f.corpus.clock.now()
    unsupported(f)
    assert check(f, cutoff=cutoff)["unsupported"] is False
    assert check(f, limits=Limits(candidates=1))["unsupported"] is None


@pytest.mark.parametrize("damage", ["body", "extract", "generation", "policy", "review", "revoked"])
def test_positive_classification_rechecks_support_at_release_boundary(event, damage):
    f = event
    prepare(f)
    _, generation = unsupported(f)
    witness = capture(f)
    page = next(p for p in witness["local_pages"]["pages"].values() if p["unsupported"])
    support = page["unsupported_support"]
    if damage in {"body", "extract"}:
        snapshot = support["snapshots"][0]
        path = (
            f.archive.blob_path(snapshot["body_sha256"])
            if damage == "body"
            else f.archive.extract_path(snapshot["extract_sha256"])
        )
        path.unlink()
    elif damage == "generation":
        f.conn.execute("DROP TRIGGER source_generation_immutable")
        f.conn.execute(
            "UPDATE source_generations SET report_json='{}' WHERE generation_id=?", (generation,)
        )
    elif damage == "policy":
        f.conn.execute(
            "UPDATE admission_policies SET contract_version='changed' WHERE page_kind='eepro.round'"
        )
    elif damage == "review":
        f.conn.execute(
            "UPDATE admission_policies SET reviewed_report_digest=NULL WHERE page_kind='eepro.round'"
        )
    else:
        f.conn.execute(
            "UPDATE source_generations SET state='revoked' WHERE generation_id=?", (generation,)
        )
    with artifact_source(f.conn, f.archive), pytest.raises(ClosureError):
        event_coverage.validate(f.conn, witness, selected_support=())
    assert check(f)["unsupported"] is not True


def test_later_success_changes_new_observation_without_rewriting_cutoff(event):
    f = event
    prepare(f)
    unsupported(f)
    witness = capture(f)
    spec = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        f.parent.url + "one.htm",
        "eepro.round",
        source_ref="eepro:test",
    )
    ctx = f.corpus.snapshot("later-success", BODY, spec=spec)
    generation, _ = f.corpus.stage(ctx)
    assert f.corpus.admit(generation) == "accepted"
    assert check(f)["unsupported"] is False
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())
    assert row(witness)["unsupported_pages"] == 1


def test_version_two_witness_keeps_unsupported_unassessed(event):
    f = event
    prepare(f)
    unsupported(f)
    witness = copy.deepcopy(capture(f))
    local = witness["local_pages"]
    local["format"] = "release-local-pages-v2"
    local["policy"]["format"] = "release-local-page-policy-v2"
    del local["policy"]["unsupported_verifier_format"]
    for page in local["pages"].values():
        del page["unsupported"]
        del page["unsupported_support"]
    witness["digest"] = digest({k: v for k, v in witness.items() if k != "digest"})
    assert row(witness)["unsupported_pages"] is None
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


@pytest.mark.parametrize("boundary", ["cached_proof", "publication"])
def test_release_boundary_checks_unsupported_extract_without_sql_change(release_state, boundary):
    import json

    from swingset.publish.safety import StaleCandidateError, _validate_candidate

    f = release_state
    local = SimpleNamespace(
        db=f.db,
        conn=f.conn,
        corpus=f.corpus,
        archive=Archive(f.db.state_dir),
        parent=WatchSpec(
            "",
            "eepro",
            "index",
            "GET",
            "https://eepro.com/results/test/",
            "eepro.autoindex",
            source_ref="eepro:test",
        ),
    )
    upsert_watch(f.conn, local.parent, f.clock.now())
    prepare(local)
    unsupported(local)
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    manifest = json.loads((result.path / "_meta/manifest.json").read_bytes())
    public = manifest["release_policy"]["closure"]
    pinned = closure.hydrate(f.conn, public)
    page = next(
        p for p in pinned["event_coverage"]["local_pages"]["pages"].values() if p["unsupported"]
    )
    path = local.archive.extract_path(page["unsupported_support"]["snapshots"][0]["extract_sha256"])
    with f.db.transaction(), validation_scope(f.conn), artifact_source(f.conn, local.archive):
        closure.validate(f.conn, public)
        before = f.conn.total_changes
        path.unlink()
        if boundary == "cached_proof":
            with pytest.raises(ClosureError, match="local_page_artifact_changed"):
                closure.validate(f.conn, public)
        else:
            built = json.loads((result.path / "BUILT").read_bytes())
            with pytest.raises(StaleCandidateError, match="local_page_artifact_changed"):
                _validate_candidate(f.conn, f.db.state_dir, result.path, built, manifest)
        assert f.conn.total_changes == before


def test_aggregate_unknown_without_page_attribution_does_not_label_every_member(event):
    from test_event_page_evidence import aggregate

    f = event
    prepare(f)
    aggregate(f, unsupported=True)
    proof = check(f)
    assert proof["acquired"] is True and proof["interpreted"] is False
    assert proof["unsupported"] is None and proof["unsupported_support"] is None
