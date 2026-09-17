"""Unavailable responses are pinned observations, not missing-file guesses."""

import copy
import gzip
import json
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from test_event_enumerations import admit_parent, finish_bootstrap
from test_event_enumerations import event as event
from test_event_local_coverage import capture, row
from test_h16_acceptance import release_state as release_state

from swingset.admission.page_evidence import Limits, Session
from swingset.build import closure, event_coverage, service
from swingset.build.closure_manifest import ClosureError, digest
from swingset.build.closure_validation import validation_scope
from swingset.build.event_artifacts import artifact_source
from swingset.fetch.archive import Archive
from swingset.schedule.event_evidence import request
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec


def prepare(f):
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)


def response(f, name="unavailable", *, classification="Gone", status=404, via="origin"):
    spec = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        f.parent.url + "one.htm",
        "eepro.round",
        source_ref="eepro:test",
    )
    context = f.corpus.snapshot(name, ("response " + name).encode(), spec=spec, via=via)
    f.conn.execute(
        "UPDATE snapshots SET classification=?,http_status=? WHERE snapshot_id=?",
        (classification, status, context.snapshot_id),
    )
    return context


def check(f, *, cutoff=None, limits=None):
    with f.db.transaction(immediate=False):
        session = Session(
            f.conn,
            f.archive,
            cutoff=cutoff or f.corpus.clock.now(),
            now=f.corpus.clock.now(),
            limits=limits or Limits(),
        )
        before = f.conn.total_changes
        value = session.verify_request(
            request("eepro", "GET", f.parent.url + "one.htm"), classify_unavailability=True
        )
        assert f.conn.total_changes == before
        return value


@pytest.mark.parametrize(
    "classification,status", [("Gone", 404), ("Gone", 410), ("ExpectedUnavailable", 403)]
)
def test_explicit_origin_response_supplies_exact_body_backed_observation(
    event, classification, status
):
    f = event
    prepare(f)
    context = response(f, classification=classification, status=status)
    proof = check(f)
    assert proof["unavailable"] is True and proof["acquired"] is proof["interpreted"] is False
    support = proof["unavailability_support"]
    assert support["snapshot_id"] == context.snapshot_id and support["http_status"] == status
    assert any(a["kind"] == "body" and a["valid"] for a in proof["artifacts"])
    witness = capture(f)
    assert row(witness)["unavailable_pages"] == 1 and row(witness)["unsupported_pages"] is None
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())
    assert context.snapshot_id in event_coverage.read_set(witness)["local_pages"]["snapshots"]


@pytest.mark.parametrize(
    "classification,status,via",
    [
        ("Gone", 404, "wayback"),
        ("Blocked", 403, "origin"),
        ("ServerError", 500, "origin"),
        ("Throttled", 429, "origin"),
    ],
)
def test_archive_and_transient_failures_do_not_establish_origin_unavailability(
    event, classification, status, via
):
    f = event
    prepare(f)
    response(f, classification=classification, status=status, via=via)
    proof = check(f)
    assert proof["unavailable"] is False and proof["unavailability_support"] is None
    assert proof["acquired"] is False


@pytest.mark.parametrize("via", ["origin", "wayback"])
def test_any_older_usable_acquisition_wins_over_newer_gone(event, via):
    f = event
    prepare(f)
    response(f, "old-usable", classification="Ok", status=200, via=via)
    response(f, "new-gone")
    value = check(f)
    assert value["acquired"] is True and value["unavailable"] is False
    assert row(capture(f))["unavailable_pages"] == 0


def test_latest_origin_order_is_chronological_and_conflicting_ties_are_unknown(event):
    f = event
    prepare(f)
    response(f, "gone")
    response(f, "transient", classification="Blocked", status=403)
    assert check(f)["unavailable"] is False
    at = f.corpus.clock.now()
    f.conn.execute(
        "UPDATE snapshots SET fetched_at=? WHERE snapshot_id='gone'",
        ((at + timedelta(hours=1)).isoformat(),),
    )
    assert check(f)["unavailable"] is False  # after cutoff
    f.conn.execute("UPDATE snapshots SET fetched_at=? WHERE snapshot_id='gone'", (at.isoformat(),))
    assert check(f)["unavailable"] is None
    earlier = (at - timedelta(seconds=1)).astimezone(timezone(timedelta(hours=-4)))
    f.conn.execute(
        "UPDATE snapshots SET fetched_at=? WHERE snapshot_id='transient'", (earlier.isoformat(),)
    )
    assert check(f)["unavailable"] is True


@pytest.mark.parametrize("damage", ["missing", "corrupt", "timestamp", "status", "budget"])
def test_incomplete_or_invalid_evidence_is_unknown(event, damage):
    f = event
    prepare(f)
    response(f)
    sha = f.conn.execute(
        "SELECT body_sha256 FROM snapshots WHERE snapshot_id='unavailable'"
    ).fetchone()[0]
    if damage == "missing":
        f.archive.blob_path(sha).unlink()
    elif damage == "corrupt":
        f.archive.blob_path(sha).write_bytes(gzip.compress(b"different"))
    elif damage == "timestamp":
        f.conn.execute("UPDATE snapshots SET fetched_at='invalid' WHERE snapshot_id='unavailable'")
    elif damage == "status":
        f.conn.execute("UPDATE snapshots SET http_status=200 WHERE snapshot_id='unavailable'")
    value = check(f, limits=Limits(candidates=1) if damage == "budget" else None)
    assert value["unavailable"] is None and value["unavailability_support"] is None


def test_cutoff_and_missing_source_evidence_do_not_become_available(event):
    f = event
    prepare(f)
    cutoff = f.corpus.clock.now()
    response(f)
    value = check(f, cutoff=cutoff)
    assert value["unavailable"] is False and value["acquired"] is False


@pytest.mark.parametrize("status", [200, 304])
def test_expected_unavailable_requires_error_status(event, status):
    f = event
    prepare(f)
    response(f, classification="ExpectedUnavailable", status=status)
    assert check(f)["unavailable"] is None


@pytest.mark.parametrize("damage", ["body", "classification", "status", "via"])
def test_pinned_response_rechecks_artifacts_and_metadata_at_validation(event, damage):
    f = event
    prepare(f)
    response(f)
    witness = capture(f)
    before = event_coverage.fingerprint(f.conn, event_coverage.read_set(witness))
    if damage == "body":
        support = next(
            p["unavailability_support"]
            for p in witness["local_pages"]["pages"].values()
            if p["unavailable"]
        )
        f.archive.blob_path(support["body_sha256"]).unlink()
        assert event_coverage.fingerprint(f.conn, event_coverage.read_set(witness)) == before
    else:
        field, value = {
            "classification": ("classification", "Blocked"),
            "status": ("http_status", 403),
            "via": ("via", "wayback"),
        }[damage]
        f.conn.execute(f"UPDATE snapshots SET {field}=? WHERE snapshot_id='unavailable'", (value,))
        assert event_coverage.fingerprint(f.conn, event_coverage.read_set(witness)) != before
    with artifact_source(f.conn, f.archive), pytest.raises(ClosureError):
        event_coverage.validate(f.conn, witness, selected_support=())


def test_restored_older_body_changes_new_observation_not_the_pinned_one(event):
    f = event
    prepare(f)
    response(f, "old", classification="Ok", status=200)
    sha = f.conn.execute("SELECT body_sha256 FROM snapshots WHERE snapshot_id='old'").fetchone()[0]
    path = f.archive.blob_path(sha)
    saved = path.read_bytes()
    path.unlink()
    response(f)
    witness = capture(f)
    assert row(witness)["unavailable_pages"] == 1
    path.write_bytes(saved)
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())
    assert row(witness)["unavailable_pages"] == 1 and row(capture(f))["unavailable_pages"] == 0


def test_legacy_v1_witness_keeps_unknown_count_and_remains_valid(event):
    f = event
    prepare(f)
    response(f)
    witness = copy.deepcopy(capture(f))
    local = witness["local_pages"]
    local["format"] = "release-local-pages-v1"
    local["policy"]["format"] = "release-local-page-policy-v1"
    del local["policy"]["unavailability_verifier_format"]
    for page in local["pages"].values():
        del page["unavailable"]
        del page["unavailability_support"]
    witness["digest"] = digest({k: v for k, v in witness.items() if k != "digest"})
    assert row(witness)["unavailable_pages"] is None
    with artifact_source(f.conn, f.archive):
        event_coverage.validate(f.conn, witness, selected_support=())


@pytest.mark.parametrize("boundary", ["cached_proof", "publication"])
def test_release_boundary_rechecks_unavailable_body_without_sql_changes(release_state, boundary):
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
    response(local)
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    manifest = json.loads((result.path / "_meta/manifest.json").read_bytes())
    public = manifest["release_policy"]["closure"]
    pinned = closure.hydrate(f.conn, public)
    page = next(
        p for p in pinned["event_coverage"]["local_pages"]["pages"].values() if p["unavailable"]
    )
    path = local.archive.blob_path(page["unavailability_support"]["body_sha256"])
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
