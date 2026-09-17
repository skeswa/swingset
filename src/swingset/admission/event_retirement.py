"""Verify one admitted enumeration withdrawal inside a shared evidence session."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from swingset.fetch.archive import canonical, digest
from swingset.schedule.event_evidence import decode_generation, request, timestamp
from swingset.schedule.event_request_kind import purpose

from .enumeration_evidence import members
from .event_declarations import declarations
from .evidence_budget import BudgetExceeded
from .page_evidence import GENERATION, Session

FORMAT = "immediate-event-page-retirement-v1"
MEMBERS = 128
WATCH = (
    "watch_id",
    "source",
    "kind",
    "method",
    "url",
    "form",
    "parser",
    "source_ref",
    "archive_url",
)


def _header(session: Session, identifier: str) -> dict[str, Any]:
    rows = session.read(
        "SELECT generation_id,decision_id,predecessor_id FROM source_event_enumerations WHERE enumeration_id=?",
        (identifier,),
        ("generation_id", "decision_id", "predecessor_id"),
        cap=1,
    )
    if not rows:
        raise ValueError("retirement_enumeration_missing")
    return rows[0]


def _accepted_order(
    session: Session, identifier: str, decision: int, source: str
) -> dict[str, Any]:
    """Historical ordering needs an accepted decision, not unrelated artifacts."""
    raw = session.read(
        "SELECT * FROM source_generations WHERE generation_id=?", (identifier,), GENERATION, cap=1
    )
    if not raw:
        raise ValueError("retirement_predecessor_generation_missing")
    value = decode_generation(raw[0], identifier)
    rows = session.read(
        "SELECT decision_id,decided_at FROM admission_decisions WHERE decision_id=? AND generation_id=? AND state='accepted'",
        (decision, identifier),
        ("decision_id", "decided_at"),
        cap=1,
    )
    if type(decision) is not int or not rows or value["recipe"]["context"]["source"] != source:
        raise ValueError("retirement_predecessor_admission_invalid")
    created = timestamp(value["created_at"])
    accepted = timestamp(rows[0]["decided_at"])
    if (
        created is None
        or accepted is None
        or datetime.fromisoformat(created) > session.cutoff
        or datetime.fromisoformat(accepted) > session.cutoff
    ):
        raise ValueError("retirement_predecessor_time_unassessed")
    return dict(
        generation_id=identifier,
        decision_id=decision,
        input_fingerprint=value["input_fingerprint"],
        content_digest=digest(
            canonical({k: v for k, v in raw[0].items() if k not in {"state", "_bytes"}})
        ),
    )


class _Parents:
    def __init__(self, session: Session, source: str):
        self.session, self.source = session, source
        self.cache: dict[tuple[str, int], dict[str, Any]] = {}

    def get(self, identifier: str, decision: int | None = None) -> dict[str, Any]:
        session = self.session
        if decision is None:
            rows = session.read(
                "SELECT decision_id FROM admission_decisions WHERE generation_id=? AND state='accepted' ORDER BY decision_id LIMIT 1",
                (identifier,),
                ("decision_id",),
                cap=1,
            )
            if not rows:
                raise ValueError("retirement_claim_unadmitted")
            decision = rows[0]["decision_id"]
        if type(decision) is not int or decision <= 0:
            raise ValueError("retirement_decision_invalid")
        key = identifier, decision
        if key in self.cache:
            return self.cache[key]
        if len(self.cache) >= session.limits.candidates:
            raise BudgetExceeded("retirement_parent_candidate_budget")
        raw = session.read(
            "SELECT * FROM source_generations WHERE generation_id=?",
            (identifier,),
            GENERATION,
            cap=1,
        )
        if not raw:
            raise ValueError("retirement_generation_missing")
        value = decode_generation(raw[0], identifier)
        context = value["recipe"]["context"]
        if context["source"] != self.source:
            raise ValueError("retirement_source_mismatch")
        snapshot = session.read(
            "SELECT method,url,form FROM snapshots WHERE snapshot_id=?",
            (context["snapshot_id"],),
            ("method", "url", "form"),
            cap=1,
        )
        if not snapshot:
            raise ValueError("retirement_anchor_missing")
        identity = request(
            self.source, snapshot[0]["method"], snapshot[0]["url"], snapshot[0]["form"]
        )
        checked = session.verify_operation(identity, generation_id=identifier, decision_id=decision)
        if checked["interpreted"] is not True:
            raise ValueError("retirement_parent_unassessed:" + ",".join(sorted(checked["reasons"])))
        watch = None
        if purpose(context["kind"]) == "result":
            watches = session.read(
                "SELECT * FROM watches WHERE watch_id=?", (context["watch_id"],), WATCH, cap=1
            )
            watch = watches[0] if watches else None
        projected = declarations(value, anchor_watch=watch, anchor_snapshot=snapshot[0])
        proof = checked["interpretation_support"]
        result = dict(
            value=value,
            declarations=projected,
            decision=decision,
            binding=dict(
                generation_id=identifier,
                decision_id=decision,
                content_digest=proof["content_digest"],
                input_fingerprint=value["input_fingerprint"],
            ),
        )
        self.cache[key] = result
        return result


def verify_edge(
    session: Session,
    *,
    source: str,
    source_ref: str,
    successor_id: str,
    successor_members: list[dict[str, Any]],
) -> dict[str, Any]:
    """Caller has verified successor membership in this exact read session."""
    result: dict[str, Any] = dict(
        format=FORMAT,
        assessment="unassessed",
        predecessor_id=None,
        successor_id=successor_id,
        verified_retirement_ids=None,
        all_predecessor_obligations_retired=None,
        reason=None,
        proof=None,
    )
    try:
        header = _header(session, successor_id)
        predecessor = header["predecessor_id"]
        result["predecessor_id"] = predecessor
        if predecessor is None:
            return {
                **result,
                "assessment": "not_applicable",
                "reason": "initial_enumeration",
                "verified_retirement_ids": [],
            }
        before = members(
            session, enumeration_id=predecessor, source=source, source_ref=source_ref, limit=MEMBERS
        )
        old = {m["request_id"]: m for m in before}
        current = {m["request_id"] for m in successor_members}
        removed = sorted(set(old) - current)
        if not removed:
            return {
                **result,
                "assessment": "not_applicable",
                "reason": "no_removed_requests",
                "verified_retirement_ids": [],
            }
        parents = _Parents(session, source)
        replacement = parents.get(header["generation_id"], header["decision_id"])
        prior_header = _header(session, predecessor)
        prior = _accepted_order(
            session, prior_header["generation_id"], prior_header["decision_id"], source
        )
        if replacement["decision"] <= prior["decision_id"]:
            raise ValueError("retirement_replacement_precedes_event")
        value = replacement["value"]
        if value["removal_authority"] != "watch" or value["report"]["proposed_removal"] != "watch":
            raise ValueError("retirement_authority_missing")
        declared = replacement["declarations"].get(source_ref, {})
        claims = []
        for key in removed:
            if key in declared:
                raise ValueError("retirement_request_still_declared")
            support = old[key]["support"]
            if not support:
                raise ValueError("retirement_claim_missing")
            for claim in support:
                if claim["unit_key"] != value["unit_key"]:
                    raise ValueError("retirement_independent_claim_survives")
                previous = parents.get(claim["generation_id"])
                if previous["value"]["unit_key"] != value["unit_key"]:
                    raise ValueError("retirement_claim_owner_mismatch")
                if replacement["decision"] <= previous["decision"]:
                    raise ValueError("retirement_replacement_precedes_claim")
                if key not in previous["declarations"].get(source_ref, {}):
                    raise ValueError("retirement_claim_request_unsupported")
                claims.append({"request_id": key, **previous["binding"]})
        proof = dict(
            format=FORMAT,
            source=source,
            source_ref=source_ref,
            predecessor_id=predecessor,
            successor_id=successor_id,
            replacement=replacement["binding"],
            predecessor=prior,
            retired_requests=removed,
            predecessor_request_count=len(old),
            successor_request_count=len(current),
            claims=sorted(
                claims, key=lambda c: (c["request_id"], c["generation_id"], c["decision_id"])
            ),
        )
        if len(canonical(proof)) > session.limits.json_bytes:
            raise BudgetExceeded("retirement_proof_json_budget")
        return {
            **result,
            "assessment": "verified",
            "verified_retirement_ids": removed,
            "all_predecessor_obligations_retired": bool(old)
            and not current
            and len(removed) == len(old),
            "proof": proof,
            "proof_digest": digest(canonical(proof)),
        }
    except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError) as exc:
        return {**result, "reason": str(exc)}


EVENT_FORMAT = "whole-source-event-retirement-v1"


def verify_event(
    session: Session, *, source: str, source_ref: str, page_edge: dict[str, Any]
) -> dict[str, Any]:
    """Disprove all retained independent declarations within a bounded full domain.

    A page withdrawal and an empty declared group do not withdraw an event.
    If the complete admitted source domain exceeds this session, stay unknown.
    """
    result: dict[str, Any] = dict(format=EVENT_FORMAT, retired=None, reason=None, proof=None)
    if (
        page_edge["assessment"] != "verified"
        or not page_edge["all_predecessor_obligations_retired"]
    ):
        return {**result, "reason": "predecessor_page_withdrawal_unproven"}
    try:
        edge_proof = page_edge["proof"]
        parents = _Parents(session, source)
        replacement = parents.get(
            edge_proof["replacement"]["generation_id"], edge_proof["replacement"]["decision_id"]
        )
        if source_ref in replacement["declarations"]:
            return {**result, "retired": False, "reason": "replacement_still_declares_event"}
        cap = min(MEMBERS, session.limits.candidates)
        source_expression = (
            "CASE WHEN json_valid(g.recipe_json) THEN "
            "CASE WHEN json_type(g.recipe_json,'$.context.source')='text' "
            "THEN json_extract(g.recipe_json,'$.context.source') END END"
        )
        rows = session.read(
            "SELECT g.generation_id,min(d.decision_id) AS decision_id FROM source_generations g "
            "JOIN admission_decisions d USING(generation_id) "
            f"WHERE ({source_expression}=? OR {source_expression} IS NULL) AND d.state='accepted' "
            "AND (julianday(d.decided_at)<=julianday(?) OR julianday(d.decided_at) IS NULL) "
            "GROUP BY g.generation_id ORDER BY decision_id LIMIT ?",
            (source, session.cutoff.isoformat(), cap),
            ("generation_id", "decision_id"),
            cap=cap,
        )
        if len(rows) >= cap:
            raise BudgetExceeded("event_retirement_source_domain_budget")
        claims: dict[str, dict[str, Any]] = {}
        domain = []
        own_prior_declaration = False
        replacement_seen = False
        for row in rows:
            item = parents.get(row["generation_id"], row["decision_id"])
            inputs = session.read(
                "SELECT outcome FROM event_enumeration_inputs WHERE generation_id=?",
                (row["generation_id"],),
                ("outcome",),
                cap=1,
            )
            if not inputs or inputs[0]["outcome"] not in {"processed", "ignored_non_event_parent"}:
                raise ValueError("event_retirement_bootstrap_unassessed")
            value = item["value"]
            domain.append(item["binding"])
            unit = value["unit_key"]
            if source_ref in item["declarations"]:
                claims[unit] = item["binding"]
                own_prior_declaration = own_prior_declaration or (
                    unit == replacement["value"]["unit_key"]
                    and item["decision"] < replacement["decision"]
                )
            elif (
                value["removal_authority"] == "watch"
                and value["report"]["proposed_removal"] == "watch"
            ):
                claims.pop(unit, None)
            if row["generation_id"] == replacement["value"]["generation_id"]:
                replacement_seen = True
        if not own_prior_declaration or not replacement_seen:
            raise ValueError("event_retirement_prior_declaration_unproven")
        if claims:
            return {**result, "retired": False, "reason": "independent_event_declaration_survives"}
        revisions = session.read(
            "SELECT revision FROM event_gap_revisions WHERE source=?",
            (source,),
            ("revision",),
            cap=1,
        )
        admissions = session.read(
            "SELECT coalesce(max(decision_id),0) AS high_water FROM admission_decisions",
            (),
            ("high_water",),
            cap=1,
        )
        proof = dict(
            format=EVENT_FORMAT,
            source=source,
            source_ref=source_ref,
            source_revision=revisions[0]["revision"] if revisions else 0,
            admission_high_water=admissions[0]["high_water"],
            predecessor_id=page_edge["predecessor_id"],
            successor_id=page_edge["successor_id"],
            replacement=replacement["binding"],
            page_proof_digest=page_edge["proof_digest"],
            admitted_source_domain=domain,
            scope="complete_retained_admitted_source_declarations_at_verification",
        )
        if len(canonical(proof)) > session.limits.json_bytes:
            raise BudgetExceeded("event_retirement_proof_json_budget")
        return {**result, "retired": True, "proof": proof, "proof_digest": digest(canonical(proof))}
    except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError) as exc:
        return {**result, "reason": str(exc)}
