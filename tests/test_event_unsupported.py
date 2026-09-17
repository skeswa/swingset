"""Sampled unsupported accounting preserves proof fences and progress semantics."""

import copy
import json
from dataclasses import replace

import pytest
from test_admission import BODY
from test_event_accounting import event_view, settle
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event
from test_event_pressure import config, enroll
from test_event_progress import observe

from swingset.admission.report import Field, Guard
from swingset.fetch.archive import canonical, digest
from swingset.schedule import event_gaps
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec


def unsupported(f, *, generic=False, explicit=True, name="one.htm"):
    spec = WatchSpec(
        "", "eepro", "round", "GET", f.parent.url + name, "eepro.round", source_ref="eepro:test"
    )
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    body = (
        BODY.replace(b"<th>Place</th>", b"<th>Quantum</th>") if explicit and not generic else BODY
    )
    context = f.corpus.snapshot("unknown-" + name, body, spec=spec)

    def changed(report):
        code = "coverage_count_mismatch" if generic else "critical_unknown"
        return replace(
            report,
            fields=(*report.fields, Field("new-column", "unknown", "Unrecognized result column"))
            if explicit
            else report.fields,
            guards=tuple(replace(g, passed=False) if g.code == code else g for g in report.guards),
        )

    generation, report = f.corpus.stage(context, body=body, report_change=changed)
    assert report.failures
    assert f.corpus.admit(generation) == "needs_review"
    return context, generation


def prepare(f):
    admit_parent(f, ["one.htm"])
    upsert_watch(
        f.conn,
        WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            f.parent.url + "seed.htm",
            "eepro.round",
            source_ref="eepro:test",
        ),
        f.corpus.clock.now(),
    )
    child(f, "seed.htm")  # Independently activate the existing round contract.
    finish_bootstrap(f)


def ready(f):
    prepare(f)
    context, generation = unsupported(f)
    enroll(f, config())
    settle(f)
    return context, generation


def test_accounting_does_not_create_acquisition_or_success_receipts(event):
    f = event
    prepare(f)
    unsupported(f)
    enroll(f, config())
    before = tuple(f.conn.execute("SELECT * FROM event_stage_operations"))
    settle(f)
    value = event_view(f)
    assert value["assessment"] == "locally_accounted"
    assert value["unsupported"] == dict(positive=1, negative=1, unknown=0)
    assert value["unavailable"] == dict(positive=0, negative=2, unknown=0)
    assert value["stages"]["interpreted"] == dict(positive=1, negative=1, unknown=0)
    assert value["stages"]["acquired"] == dict(positive=2, negative=0, unknown=0)
    assert tuple(f.conn.execute("SELECT * FROM event_stage_operations")) == before
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()


def test_body_loss_reopens_sampled_accounting_after_bounded_refresh(event):
    f = event
    context, _ = ready(f)
    sha = f.conn.execute(
        "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (context.snapshot_id,)
    ).fetchone()[0]
    path = f.archive.blob_path(sha)
    saved = path.read_bytes()
    path.unlink()
    observe(f)
    assert event_view(f)["assessment"] != "locally_accounted"
    path.write_bytes(saved)
    settle(f)
    assert event_view(f)["unsupported"]["positive"] == 1
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()


def test_new_generation_without_admission_invalidates_sampled_gap(event):
    f = event
    context, _ = ready(f)
    old = f.conn.execute(
        "SELECT revision FROM event_gap_revisions WHERE source='eepro'"
    ).fetchone()[0]
    f.corpus.stage(
        context,
        report_change=lambda r: replace(
            r, guards=(*r.guards, Guard("new-failure", False, "new evidence"))
        ),
    )
    assert (
        f.conn.execute("SELECT revision FROM event_gap_revisions WHERE source='eepro'").fetchone()[
            0
        ]
        > old
    )
    assert event_view(f)["unsupported"]["positive"] == 0
    settle(f)
    assert event_view(f)["unsupported"]["positive"] == 1


def test_revocation_invalidates_classification_and_does_not_retire_page(event):
    f = event
    _, generation = ready(f)
    f.conn.execute(
        "UPDATE source_generations SET state='revoked' WHERE generation_id=?", (generation,)
    )
    observe(f)
    value = event_view(f)
    assert value["assessment"] != "locally_accounted"
    assert value["unsupported"]["positive"] == 0
    assert value["retirement"] == "unassessed" and value["listed_pages"] == 2


def test_legacy_gap_record_does_not_invent_unsupported_history(event):
    f = event
    ready(f)
    row = dict(f.conn.execute("SELECT * FROM event_gap_observations LIMIT 1").fetchone())
    evidence = json.loads(row["evidence_json"])
    evidence["format"] = "unavailable-origin-observation-v1"
    del evidence["unsupported"]
    del evidence["unsupported_support"]
    row["evidence_json"] = json.dumps(evidence)
    assert (
        event_gaps.value(
            row, evidence["request"], row["source_revision"], classification="unsupported"
        )
        is None
    )


@pytest.mark.parametrize(
    "damage", ["generation", "digest", "snapshot", "request", "policy", "time"]
)
def test_malformed_positive_metadata_stays_unknown_even_with_matching_digest(event, damage):
    f = event
    ready(f)
    rows = [dict(r) for r in f.conn.execute("SELECT * FROM event_gap_observations")]
    row = next(r for r in rows if json.loads(r["evidence_json"])["unsupported"])
    evidence = copy.deepcopy(json.loads(row["evidence_json"]))
    support = evidence["unsupported_support"]
    if damage == "generation":
        support["generation_id"] = ""
    elif damage == "digest":
        support["content_digest"] = "missing"
    elif damage == "snapshot":
        support["snapshots"] = [{}]
    elif damage == "request":
        support["snapshots"][0]["url"] += "other.htm"
    elif damage == "policy":
        support["policy"] = {}
    else:
        support["snapshots"][0]["fetched_at"] = "2099-01-01T00:00:00+00:00"
    row["evidence_json"] = canonical(evidence).decode()
    row["evidence_digest"] = digest(canonical(evidence))
    assert (
        event_gaps.value(
            row, evidence["request"], row["source_revision"], classification="unsupported"
        )
        is None
    )


@pytest.mark.parametrize("other", [None, False, True])
def test_explicit_unsupported_union_is_distinct_from_success(other):
    assert event_gaps.accounted(False, other, True) is True
    assert event_gaps.accounted(False, False, None) is None
    with pytest.raises(ValueError):
        event_gaps.accounted(False, False, 1)


def test_actual_new_column_contract_accounts_for_unknown_layout(event):
    f = event
    prepare(f)
    body = BODY.replace(b"<th>Place</th>", b"<th>Quantum</th>")
    spec = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        f.parent.url + "one.htm",
        "eepro.round",
        source_ref="eepro:test",
    )
    context = f.corpus.snapshot("changed-layout", body, spec=spec)
    generation, report = f.corpus.stage(context, body=body)
    assert "critical_unknown" in report.failures
    assert f.corpus.admit(generation) == "needs_review"
    enroll(f, config())
    settle(f)
    assert event_view(f)["unsupported"]["positive"] == 1
