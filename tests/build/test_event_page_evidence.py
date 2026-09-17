"""Cutoff-local checks are bounded evidence, not release or scheduling authority."""

import gzip
import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_event_enumerations import admit_parent, child
from test_event_enumerations import event as event

from swingset.build.event_page_evidence import Limits, verify_request
from swingset.fetch.archive import Archive
from swingset.schedule.event_evidence import request
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec


def check(f, *, cutoff=None, now=None, limits=None, identity=None):
    f.conn.commit()
    f.conn.execute("PRAGMA query_only=1")
    f.conn.execute("BEGIN")
    before = f.conn.total_changes
    try:
        result = verify_request(
            f.conn,
            f.archive,
            request=identity or request("eepro", "GET", f.parent.url + "one.htm"),
            cutoff=cutoff or f.corpus.clock.now(),
            now=now or f.corpus.clock.now(),
            limits=limits or Limits(),
        )
        assert f.conn.total_changes == before
        assert f.conn.in_transaction and f.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        json.dumps(result)
        return result
    finally:
        f.conn.rollback()
        f.conn.execute("PRAGMA query_only=0")


def ready(f):
    admit_parent(f, ["one.htm"])
    return child(f, "one.htm")


def test_actual_admitted_support_has_exact_receipts_and_distinct_verification_time(
    event, monkeypatch
):
    f = event
    context, generation = ready(f)
    cutoff = f.corpus.clock.now()
    later = cutoff + timedelta(hours=1)
    monkeypatch.setattr(Archive, "read_body", Mock(side_effect=AssertionError("whole body")))
    monkeypatch.setattr(Archive, "read_extract", Mock(side_effect=AssertionError("whole extract")))
    result = check(f, cutoff=cutoff, now=later)
    assert result["acquired"] is result["interpreted"] is True
    assert result["acquisition_support"]["snapshot_id"] == context.snapshot_id
    proof = result["interpretation_support"]
    assert proof["generation_id"] == generation and proof["content_digest"]
    assert proof["accepted_decision"]["generation_id"] == generation
    assert proof["policy"]["contract_version"]
    assert {a["kind"] for a in result["artifacts"] if a["valid"]} == {"body", "extract"}
    assert result["cutoff"] != result["artifacts_verified_at"]
    assert all(a["verified_at"] == later.isoformat() for a in result["artifacts"])


def test_snapshot_and_generation_after_cutoff_are_not_imported(event):
    f = event
    cutoff = f.corpus.clock.now()
    f.corpus.clock.sleep(1)
    ready(f)
    result = check(f, cutoff=cutoff)
    assert result["acquired"] is result["interpreted"] is False
    assert result["acquisition_support"] is result["interpretation_support"] is None


def test_late_acceptance_does_not_make_preexisting_generation_interpreted_at_cutoff(event):
    f = event
    _, generation = ready(f)
    cutoff = f.corpus.clock.now()
    f.conn.execute(
        "UPDATE admission_decisions SET decided_at=? WHERE generation_id=?",
        ((cutoff + timedelta(seconds=1)).isoformat(), generation),
    )
    result = check(f, cutoff=cutoff, now=cutoff + timedelta(seconds=2))
    assert result["acquired"] is True and result["interpreted"] is False
    assert result["reasons"]["not_accepted_at_cutoff"]


def test_older_accepted_generation_survives_later_blocked_reinterpretation(event):
    f = event
    context, old = ready(f)
    f.corpus.clock.sleep(1)
    newer, _ = f.corpus.stage(
        replace(context, fetched_at=f.corpus.clock.now().isoformat()),
        report_change=lambda report: replace(report, proposed_removal="watch"),
    )
    assert newer != old
    f.conn.execute(
        "UPDATE source_generations SET state='needs_review' WHERE generation_id=?", (newer,)
    )
    result = check(f)
    assert result["interpreted"] is True
    assert result["interpretation_support"]["generation_id"] == old


def test_related_revocation_rejects_previous_accepted_support(event):
    f = event
    context, old = ready(f)
    other, _ = f.corpus.stage(
        context, report_change=lambda report: replace(report, proposed_removal="watch")
    )
    assert other != old
    f.conn.execute("UPDATE source_generations SET state='revoked' WHERE generation_id=?", (other,))
    result = check(f)
    assert result["acquired"] is True and result["interpreted"] is False
    assert result["reasons"]["source_evidence_revoked"]


@pytest.mark.parametrize(
    "damage",
    [
        "body_missing",
        "body_digest",
        "body_truncated",
        "extract_missing",
        "extract_digest",
        "generation",
        "policy",
    ],
)
def test_bad_evidence_never_supplies_positive_interpretation(event, damage):
    f = event
    context, generation = ready(f)
    snapshot = f.conn.execute(
        "SELECT * FROM snapshots WHERE snapshot_id=?", (context.snapshot_id,)
    ).fetchone()
    raw = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?", (generation,)
        ).fetchone()[0]
    )[0]
    if damage.startswith("body"):
        path = f.archive.blob_path(snapshot["body_sha256"])
        if damage == "body_missing":
            path.unlink()
        else:
            path.write_bytes(
                gzip.compress(b"bad") if damage == "body_digest" else path.read_bytes()[:-6]
            )
    elif damage.startswith("extract"):
        path = f.archive.extract_path(raw["extract_sha256"])
        if damage == "extract_missing":
            path.unlink()
        else:
            path.write_bytes(b"{}")
    elif damage == "generation":
        f.conn.execute("DROP TRIGGER source_generation_immutable")
        f.conn.execute(
            "UPDATE source_generations SET result_json='{}' WHERE generation_id=?", (generation,)
        )
    else:
        f.conn.execute(
            "UPDATE admission_policies SET contract_version='different' WHERE page_kind='eepro.round'"
        )
    result = check(f)
    assert result["interpreted"] is False
    assert result["acquired"] is (not damage.startswith("body"))


def test_failure_response_body_is_not_acquisition(event):
    f = event
    context, _ = ready(f)
    f.conn.execute(
        "UPDATE snapshots SET classification='ServerError',http_status=503 WHERE snapshot_id=?",
        (context.snapshot_id,),
    )
    result = check(f)
    assert result["acquired"] is result["interpreted"] is False


def test_shared_request_normalizes_url_and_forms_independently_of_aliases_and_watch_kind(event):
    f = event
    context, generation = ready(f)
    f.conn.execute(
        "UPDATE snapshots SET url='https://EEPRO.COM:443/results/test/one.htm#ignored',form='{}' WHERE snapshot_id=?",
        (context.snapshot_id,),
    )
    # The generation's exact snapshot URL no longer agrees, but acquisition is
    # still the same normalized request. It must not require a canonical alias.
    result = check(f)
    assert result["acquired"] is True and result["interpreted"] is False
    f.conn.execute(
        "INSERT INTO source_event_map VALUES ('eepro','eepro:test','different-event','alias',1)"
    )
    assert check(f) == result
    spec = WatchSpec(
        "",
        "eepro",
        "other-kind",
        "GET",
        f.parent.url + "one.htm",
        "eepro.round",
        source_ref="another-ref",
    )
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    from test_admission import BODY

    ctx = f.corpus.snapshot("shared-other-kind", BODY, spec=spec)
    fresh, _ = f.corpus.stage(ctx)
    f.corpus.review(fresh)
    assert f.corpus.admit(fresh) == "accepted"
    assert check(f)["interpretation_support"]["generation_id"] == fresh
    assert (
        check(f, identity=request("eepro", "GET", f.parent.url + "one.htm", {"different": "form"}))[
            "acquired"
        ]
        is False
    )


@pytest.mark.parametrize(
    "limit",
    [
        Limits(json_bytes=1),
        Limits(rows=1),
        Limits(total_json_bytes=1),
        Limits(decoded_bytes=1),
        Limits(compressed_bytes=1),
    ],
)
def test_exhaustion_is_unknown_and_never_false(event, limit):
    f = event
    ready(f)
    result = check(f, limits=limit)
    assert result["acquired"] is result["interpreted"] is None
    assert result["reasons"]


def test_candidate_overflow_preserves_positive_evidence_but_not_negative_claim(event):
    f = event
    ready(f)
    result = check(f, limits=Limits(candidates=1))
    assert result["acquired"] is result["interpreted"] is True
    assert not result["snapshots_exhausted"]
    absent = check(
        f,
        identity=request("eepro", "GET", f.parent.url + "absent.htm"),
        limits=Limits(candidates=1),
    )
    assert absent["acquired"] is absent["interpreted"] is None


def test_oversized_generation_is_not_decoded(event, monkeypatch):
    f = event
    _, generation = ready(f)
    f.conn.execute("DROP TRIGGER source_generation_immutable")
    f.conn.execute(
        "UPDATE source_generations SET result_json=? WHERE generation_id=?",
        ("x" * 10000, generation),
    )
    import swingset.admission.page_evidence as module

    decoder = Mock(side_effect=AssertionError("oversized decode"))
    monkeypatch.setattr(module, "decode_generation", decoder)
    result = check(f, limits=Limits(json_bytes=4000))
    assert result["acquired"] is True and result["interpreted"] is None
    decoder.assert_not_called()


def test_requires_caller_readonly_transaction_and_disallows_recovery(event):
    f = event
    args = dict(
        request=request("eepro", "GET", f.parent.url),
        cutoff=f.corpus.clock.now(),
        now=f.corpus.clock.now(),
    )
    with pytest.raises(ValueError, match="query-only"):
        verify_request(f.conn, f.archive, **args)
    with pytest.raises(ValueError, match="recovery"):
        verify_request(f.conn, Archive(f.archive.state_dir, recovery=Mock()), **args)


@pytest.mark.parametrize(
    "kwargs", [{"rows": 0}, {"seconds": float("nan")}, {"candidates": 257}, {"decoded_bytes": -1}]
)
def test_limits_are_positive_bounded(kwargs):
    with pytest.raises(ValueError):
        Limits(**kwargs)


def test_generation_created_after_cutoff_cannot_supply_interpretation(event):
    f = event
    _, generation = ready(f)
    cutoff = f.corpus.clock.now()
    f.conn.execute("DROP TRIGGER source_generation_immutable")
    f.conn.execute(
        "UPDATE source_generations SET created_at=? WHERE generation_id=?",
        ((cutoff + timedelta(seconds=1)).isoformat(), generation),
    )
    result = check(f, cutoff=cutoff, now=cutoff + timedelta(seconds=2))
    assert result["acquired"] is True and result["interpreted"] is False


def test_common_exact_request_finishes_before_large_unrelated_alias_universe(event):
    from test_admission import BODY

    f = event
    ready(f)
    for index in range(20):
        spec = WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            f.parent.url + f"noise-{index}.htm",
            "eepro.round",
            source_ref="noise",
        )
        upsert_watch(f.conn, spec, f.corpus.clock.now())
        f.corpus.snapshot(f"noise-{index}", BODY, spec=spec)
    result = check(f, limits=Limits(candidates=2, rows=16))
    assert result["acquired"] is result["interpreted"] is True
    assert not result["snapshots_exhausted"]


def test_cooperative_time_budget_returns_unknown_without_changing_connection(event, monkeypatch):
    import swingset.schedule.event_pressure_probe as primitives

    f = event
    ready(f)
    times = iter([0.0, 5.0])
    monkeypatch.setattr(primitives.time, "monotonic", lambda: next(times))
    result = check(f, limits=Limits(seconds=1))
    assert result["acquired"] is result["interpreted"] is None
    assert result["reasons"]["time_budget"]


def test_naive_retained_time_is_unknown_not_a_false_negative(event):
    f = event
    context, _ = ready(f)
    f.conn.execute(
        "UPDATE snapshots SET fetched_at='2026-01-01' WHERE snapshot_id=?", (context.snapshot_id,)
    )
    result = check(f)
    assert result["acquired"] is result["interpreted"] is None
    assert result["reasons"]["evidence_time_unknown"]


def test_budget_between_exact_and_alias_generation_pass_never_becomes_false(event):
    from test_admission import BODY

    f = event
    _, old = ready(f)
    f.conn.execute(
        "UPDATE admission_decisions SET state='needs_review' WHERE generation_id=?", (old,)
    )
    spec = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        "https://EEPRO.COM:443/results/test/one.htm#alias",
        "eepro.round",
        source_ref="other-alias",
    )
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    context = f.corpus.snapshot("usable-alias", BODY, spec=spec)
    generation, _ = f.corpus.stage(context)
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    verdicts = []
    for row_budget in range(2, 24):
        result = check(f, limits=Limits(rows=row_budget))
        verdicts.append(result["interpreted"])
        assert result["interpreted"] is not False
    assert True in verdicts and None in verdicts


def aggregate(f):
    """Stage, review and admit two actual pages under a different anchor watch."""
    from test_admission import BODY

    from swingset.admission.contracts import inspect
    from swingset.admission.generations import begin_attempt, stage_generation
    from swingset.admission.report import evaluate
    from swingset.sources.base import ParseResult

    admit_parent(f, ["anchor.htm", "one.htm"])
    page = f.corpus.page
    contexts, manifests, reports, observations = [], [], [], []
    for name, body in [("anchor", BODY), ("one", BODY.replace(b"Alice", b"Carol"))]:
        spec = WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            f.parent.url + name + ".htm",
            "eepro.round",
            source_ref="eepro:test",
        )
        ctx = f.corpus.snapshot("aggregate-" + name, body, spec=spec)
        extract = page.extract(body)
        parsed = page.parse(extract, ctx)
        reports.append(inspect(ctx, body, extract, parsed))
        observations.extend(parsed.observations)
        contexts.append(ctx)
        manifests.append(
            {
                "slot": ctx.snapshot_id,
                "snapshot_id": ctx.snapshot_id,
                "watch_id": ctx.watch_id,
                "url": ctx.url,
                "body_sha256": f.archive.store_body(body),
                "extract_sha256": f.archive.store_extract(extract),
                "observed_at": ctx.fetched_at,
                "captured_at": None,
                "archive_url": None,
                "via": "origin",
                "extract_version": str(page.EXTRACT_VERSION),
                "parser_version": str(page.PARSER_VERSION),
            }
        )
    slots = tuple(ctx.snapshot_id for ctx in contexts)
    coverage = replace(
        reports[0].coverage,
        expected_pages=slots,
        observed_pages=slots,
        source_count=sum(r.coverage.source_count for r in reports),
        interpreted_count=sum(r.coverage.interpreted_count for r in reports),
    )
    report = evaluate(
        page.kind,
        reports[0].contract_version,
        tuple(field for r in reports for field in r.fields),
        coverage,
    )
    assert not report.failures
    inputs = begin_attempt(f.conn, contexts[0], page.EXTRACT_VERSION, page.PARSER_VERSION)
    with f.db.transaction():
        generation = stage_generation(
            f.conn,
            f.archive,
            inputs,
            manifests[0]["extract_sha256"],
            ParseResult(observations=tuple(observations)),
            report,
            now=f.corpus.clock.now().isoformat(),
            run_id=f.corpus.run,
            manifest=tuple(manifests),
        )
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    return contexts, generation


@pytest.mark.parametrize("candidate_limit", [1, 64])
def test_accepted_aggregate_on_other_watch_supplies_requested_page(event, candidate_limit):
    f = event
    contexts, generation = aggregate(f)
    assert not f.conn.execute(
        "SELECT 1 FROM source_units WHERE watch_id=?", (contexts[1].watch_id,)
    ).fetchone()
    result = check(f, limits=Limits(candidates=candidate_limit))
    assert result["acquired"] is result["interpreted"] is True
    proof = result["interpretation_support"]
    assert proof["generation_id"] == generation
    assert {row["snapshot_id"] for row in proof["snapshots"]} == {
        ctx.snapshot_id for ctx in contexts
    }
    assert proof["accepted_decision"]["state"] == "accepted"


def test_other_anchor_candidate_overflow_cannot_claim_no_interpretation(event):
    from test_admission import BODY

    f = event
    aggregate(f)
    # More recent unrelated generations fill the fallback. There are fewer
    # snapshots than the cap, so only the generation domain remains unproven.
    spec = WatchSpec(
        "", "eepro", "round", "GET", f.parent.url + "noise.htm", "eepro.round", source_ref="noise"
    )
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    context = f.corpus.snapshot("noise", BODY, spec=spec)
    for index in range(8):
        f.corpus.stage(
            context,
            result_change=lambda value, index=index: replace(
                value, legitimate_empty=bool(index % 2)
            ),
            report_change=lambda value, index=index: replace(
                value, coverage=replace(value.coverage, snapshot_token=str(index))
            ),
        )
    result = check(f, limits=Limits(candidates=6))
    assert result["snapshots_exhausted"] and result["acquired"] is True
    assert result["interpreted"] is None and not result["generations_exhausted"]
    assert result["reasons"]["source_generation_candidate_budget"]
    assert check(f)["interpreted"] is True


def test_aggregate_member_source_change_invalidates_whole_support(event):
    f = event
    contexts, _ = aggregate(f)
    f.conn.execute(
        "UPDATE watches SET source='scoringdance' WHERE watch_id=?", (contexts[1].watch_id,)
    )
    result = check(f, identity=request("eepro", "GET", contexts[0].url))
    assert result["acquired"] is True and result["interpreted"] is False
    assert result["reasons"]["manifest_source_mismatch"]


def test_shared_session_metadata_budget_is_not_reset_per_request(event):
    from swingset.admission.evidence_budget import BudgetExceeded
    from swingset.admission.page_evidence import Session

    f = event
    ready(f)
    f.conn.commit()
    f.conn.execute("PRAGMA query_only=1")
    f.conn.execute("BEGIN")
    try:
        session = Session(
            f.conn,
            f.archive,
            cutoff=f.corpus.clock.now(),
            now=f.corpus.clock.now(),
            limits=Limits(rows=3),
        )
        assert session.read("SELECT 'one' AS value", (), ("value",), cap=1)[0]["value"] == "one"
        assert session.read("SELECT 'two' AS value", (), ("value",), cap=1)[0]["value"] == "two"
        with pytest.raises(BudgetExceeded, match="row_budget"):
            session.read("SELECT 'three' AS value", (), ("value",), cap=1)
        result = session.verify_request(request("eepro", "GET", f.parent.url + "one.htm"))
        assert result["acquired"] is result["interpreted"] is None
        assert result["reasons"]["row_budget"]
        assert session.exhausted()
        assert f.conn.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        f.conn.rollback()
        f.conn.execute("PRAGMA query_only=0")


def test_exact_operation_cannot_substitute_an_older_accepted_decision(event):
    from swingset.admission.page_evidence import Session

    f = event
    context, generation = ready(f)
    decision = f.conn.execute(
        "SELECT decision_id FROM admission_decisions WHERE generation_id=? AND state='accepted'",
        (generation,),
    ).fetchone()[0]
    f.conn.commit()
    f.conn.execute("PRAGMA query_only=1")
    f.conn.execute("BEGIN")
    try:
        session = Session(f.conn, f.archive, cutoff=f.corpus.clock.now(), now=f.corpus.clock.now())
        identity = request("eepro", "GET", f.parent.url + "one.htm")
        actual = session.verify_operation(identity, generation_id=generation, decision_id=decision)
        assert actual["interpreted"] is True
        assert actual["interpretation_support"]["accepted_decision"]["decision_id"] == decision
        wrong = session.verify_operation(
            identity, generation_id=generation, decision_id=decision + 1000
        )
        assert wrong["interpreted"] is False
        assert wrong["interpretation_support"] is None
        snapshot = session.verify_operation(identity, snapshot_id=context.snapshot_id)
        assert snapshot["acquired"] is True
        assert snapshot["interpreted"] is None
        absent = session.verify_operation(identity, snapshot_id="absent")
        assert absent["acquired"] is False
        assert absent["acquisition_support"] is None
    finally:
        f.conn.rollback()
        f.conn.execute("PRAGMA query_only=0")


def test_exact_aggregate_operation_can_support_non_anchor_request(event):
    from swingset.admission.page_evidence import Session

    f = event
    contexts, generation = aggregate(f)
    decision = f.conn.execute(
        "SELECT decision_id FROM admission_decisions WHERE generation_id=? AND state='accepted'",
        (generation,),
    ).fetchone()[0]
    f.conn.commit()
    f.conn.execute("PRAGMA query_only=1")
    f.conn.execute("BEGIN")
    try:
        session = Session(f.conn, f.archive, cutoff=f.corpus.clock.now(), now=f.corpus.clock.now())
        result = session.verify_operation(
            request("eepro", "GET", contexts[1].url), generation_id=generation, decision_id=decision
        )
        assert result["acquired"] is result["interpreted"] is True
        assert result["acquisition_support"]["snapshot_id"] == contexts[1].snapshot_id
        assert result["interpretation_support"]["generation_id"] == generation
    finally:
        f.conn.rollback()
        f.conn.execute("PRAGMA query_only=0")
