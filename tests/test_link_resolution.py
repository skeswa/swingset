"""Identity policy examples through the event resolver, without a database."""

from dataclasses import replace

import pytest

from swingset.link import DancerRecord, Subject, Weights
from swingset.link.model import (
    ConfirmedIdentity,
    EventEvidence,
    LinkingRules,
    SubjectEvidence,
    TentativeIdentity,
    UnmatchedIdentity,
    WithheldIdentity,
)
from swingset.link.policy import DecisionPolicy
from swingset.link.resolution import resolve_event
from swingset.state.identity_journal import Decision, JournalToken
from swingset.state.identity_references import ReferenceBinding, SourceReference

RULES = LinkingRules(Weights(), {})
DANCERS = (DancerRecord(100, "Alex Lee", "leader", 2026),)


def evidence(
    identifier="entry", *, name="Alex Lee", bib="42", contest="novice", printed=(), registry=()
):
    reference = SourceReference("test", "event", contest, "final", f"leader:{bib}")
    subject = Subject(
        "entry",
        identifier,
        name,
        "leader",
        "novice",
        2026,
        printed[0] if len(printed) == 1 else None,
        bib,
        contest,
    )
    binding = ReferenceBinding(reference, "entry", identifier, "event", "snapshot", "hash", {})
    return SubjectEvidence(
        subject,
        (binding,),
        frozenset(printed),
        frozenset(registry),
        "contradictory_printed_source_identities" if len(printed) > 1 else None,
    )


def reviewed(item, kind="same_person", number="100"):
    ref = item.bindings[0].reference
    return Decision(
        "review",
        ref.source,
        ref.source_event,
        ref.contest,
        ref.round,
        ref.participant,
        number,
        kind,
        "retained evidence",
        "reviewed",
        "reviewer",
        "2026-09-17",
        "",
    )


def resolve(*subjects, decisions=(), dancers=DANCERS):
    event = EventEvidence(
        "event",
        subjects,
        dancers,
        frozenset(),
        DecisionPolicy(JournalToken("reviewed-journal", 1), decisions, {}),
    )
    return resolve_event(event, RULES)


def test_perfect_name_score_is_a_hypothesis_without_a_default_join():
    result = resolve(evidence()).subjects[0]
    assert result.conclusion == TentativeIdentity(100, "name_unique", "probable", 1.0)
    assert result.default_wsdc_id is None
    assert result.conclusion.reasons == ("no_confirmation_evidence",)
    assert result.candidates[0].selected
    assert result.candidates[0].allowed


@pytest.mark.parametrize("route", ["manual", "source_id", "registry_placement"])
def test_each_confirmation_route_exposes_its_basis_and_default_join(route):
    item = evidence(
        printed=(100,) if route == "source_id" else (),
        registry=(100,) if route == "registry_placement" else (),
    )
    result = resolve(item, decisions=(reviewed(item),) if route == "manual" else ()).subjects[0]
    assert result.conclusion == ConfirmedIdentity(100, route)
    assert result.default_wsdc_id == 100
    assert result.conclusion.reasons == (route,)
    assert result.history_state == "accepted"


def test_manual_confirmation_precedes_consistent_printed_and_registry_evidence():
    item = evidence(printed=(100,), registry=(100,))
    result = resolve(item, decisions=(reviewed(item),)).subjects[0]
    assert result.conclusion == ConfirmedIdentity(100, "manual")


@pytest.mark.parametrize("manual", [False, True])
def test_confirmation_can_name_an_identity_outside_the_candidate_pool(manual):
    item = evidence(printed=() if manual else (999,))
    result = resolve(item, decisions=(reviewed(item, number="999"),) if manual else ()).subjects[0]
    assert result.default_wsdc_id == 999
    assert [c.candidate.dancer.wsdc_id for c in result.candidates] == [100]
    assert not result.candidates[0].selected


def test_registry_confirmation_still_requires_a_generated_candidate():
    item = evidence(registry=(999,))
    result = resolve(item).subjects[0]
    assert isinstance(result.conclusion, TentativeIdentity)
    assert result.default_wsdc_id is None


@pytest.mark.parametrize("kind,number", [("hold_unlinked", "NONE"), ("different_person", "100")])
def test_review_restrictions_withhold_printed_identity_but_preserve_signals(kind, number):
    item = replace(evidence(printed=(100,)), previous_default_id=100)
    result = resolve(item, decisions=(reviewed(item, kind, number),)).subjects[0]
    assert isinstance(result.conclusion, WithheldIdentity)
    assert result.default_wsdc_id is None
    assert result.history_state == "revoked"
    assert not result.candidates[0].allowed
    assert result.candidates[0].source_id
    assert result.candidates[0].score == 1.0
    assert result.findings[0].kind == "identity_decision"


def test_pair_restriction_allows_an_alternative_and_retains_ambiguity_evidence():
    item = evidence()
    dancers = (*DANCERS, replace(DANCERS[0], wsdc_id=101))
    result = resolve(
        item, decisions=(reviewed(item, "different_person"),), dancers=dancers
    ).subjects[0]
    assert result.conclusion == TentativeIdentity(101, "assignment", "possible", 1.0)
    assert [c.allowed for c in result.candidates] == [False, True]
    assert [c.selected for c in result.candidates] == [False, True]


def test_conflicting_printed_ids_remain_visible_in_withheld_result():
    item = evidence(printed=(100, 101))
    result = resolve(item, dancers=(*DANCERS, replace(DANCERS[0], wsdc_id=101))).subjects[0]
    assert isinstance(result.conclusion, WithheldIdentity)
    assert "contradictory_printed_source_identities" in result.conclusion.reasons
    assert all(c.source_id and c.score == 1.0 and not c.allowed for c in result.candidates)


def test_registry_and_printed_disagreement_withholds_both():
    result = resolve(evidence(printed=(100,), registry=(101,))).subjects[0]
    assert isinstance(result.conclusion, WithheldIdentity)
    assert "contradictory_source_and_registry_identities" in result.conclusion.reasons


def test_paired_subject_cannot_receive_even_a_reviewed_identity():
    item = evidence(name="Alex Lee and Sam Doe", printed=(100,))
    result = resolve(item, decisions=(reviewed(item),)).subjects[0]
    assert result.conclusion == WithheldIdentity(("paired_name_ownership_unresolved",))
    assert result.default_wsdc_id is None
    assert not result.candidates
    assert result.findings[0].kind == "paired_name"


def test_two_bibs_claiming_one_identity_are_withheld_together():
    result = resolve(
        evidence("first", printed=(100,)), evidence("second", bib="43", registry=(100,))
    )
    assert all(isinstance(item.conclusion, WithheldIdentity) for item in result.subjects)
    assert all(
        "identity_claimed_by_distinct_bibs" in item.conclusion.reasons for item in result.subjects
    )


def test_assignment_and_strong_claims_are_scoped_to_contest_and_role():
    first = evidence("first", printed=(100,))
    other_contest = evidence("second", contest="open", printed=(100,))
    other_role = evidence("third", printed=(100,))
    other_role = replace(other_role, subject=replace(other_role.subject, role="follower"))
    result = resolve(first, other_contest, other_role)
    assert [item.default_wsdc_id for item in result.subjects] == [100, 100, 100]
    tentative = resolve(evidence("first"), evidence("second", contest="open"))
    assert all(isinstance(item.conclusion, TentativeIdentity) for item in tentative.subjects)


def test_assignment_competes_within_a_scope_and_shares_a_bib():
    result = resolve(evidence("first"), evidence("second", bib="43"))
    assert isinstance(result.subjects[0].conclusion, TentativeIdentity)
    assert isinstance(result.subjects[1].conclusion, UnmatchedIdentity)
    shared = resolve(evidence("first"), evidence("second"))
    assert all(item.conclusion.method == "bib_reuse" for item in shared.subjects)
    assert all(item.candidates[0].bib_reuse for item in shared.subjects)


def test_loaded_evidence_is_reusable_and_resolution_is_deterministic():
    item = evidence()
    event = EventEvidence(
        "event", (item,), DANCERS, frozenset(), DecisionPolicy(JournalToken("journal", 1), (), {})
    )
    first = resolve_event(event, RULES)
    assert resolve_event(event, RULES) == first
    assert event.subjects == (item,)
    assert not event.policy.decisions


def test_unmatched_and_withheld_have_distinct_explanations():
    missing = resolve(evidence(name="Unregistered Person")).subjects[0]
    assert isinstance(missing.conclusion, UnmatchedIdentity)
    held = resolve(replace(evidence(), reference_problem="source_reference_unavailable")).subjects[
        0
    ]
    assert held.conclusion == WithheldIdentity(("source_reference_unavailable",))
    assert missing.conclusion.status == held.conclusion.status == "unmatched"
