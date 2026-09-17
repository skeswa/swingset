"""Body-backed critical unknowns are gaps, never successful interpretations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from swingset.admission.evidence_budget import Budget, BudgetExceeded
from swingset.admission.page_evidence import POLICY, SNAPSHOT, Limits, _Evidence, _time
from swingset.schedule.event_evidence import request as normalize_request
from swingset.schedule.event_evidence import request_id

FORMAT = "unsupported-page-observation-v1"


def explicit_unknown(value: Mapping[str, Any]) -> bool:
    """Require a typed contract finding, not a generic failed/empty parse."""
    report = value["report"]
    return bool(
        report["page_kind"] == value["page_kind"]
        and report["contract_version"] == value["contract_version"]
        and "critical_unknown" in report["failures"]
        and any(g["code"] == "critical_unknown" and g["passed"] is False for g in report["guards"])
        and any(
            f["critical"] is True
            and f["disposition"] == "unknown"
            and isinstance(f["path"], str)
            and f["path"]
            and isinstance(f["reason"], str)
            and f["reason"]
            for f in report["fields"]
        )
    )


def observe(
    checker: Any, *, interpreted: bool, exhausted: bool
) -> tuple[bool | None, dict[str, Any] | None]:
    """Successful retained interpretation wins; incomplete searches stay unknown."""
    if interpreted:
        return False, None
    if not exhausted or checker.unknown:
        return None, None
    if not checker.acquired:
        return False, None
    uncertain = checker.generation_unknown or any(
        not checker.reader.artifacts.get(key, False) for key in checker.used_artifacts
    )
    for identifier in sorted(checker.checked_generations):
        try:
            before = dict(checker.reader.reasons)
            proof = checker.interpreted(identifier, unsupported=True)
            uncertain = uncertain or before != dict(checker.reader.reasons)
            if proof is not None:
                return True, proof
        except (BudgetExceeded, ValueError, TypeError, KeyError, RecursionError) as exc:
            uncertain = True
            checker.reader.reasons[
                str(exc) if isinstance(exc, BudgetExceeded) else "unsupported_evidence_invalid"
            ] += 1
    return (None if uncertain else False), None


def metadata_valid(observation: Mapping[str, Any]) -> bool:
    """Check serialized shape only; exact support is rechecked at release boundaries."""
    try:
        value, proof = observation["unsupported"], observation["unsupported_support"]
        identity = observation["request"]
        if (
            (value is not None and type(value) is not bool)
            or normalize_request(
                identity["source"], identity["method"], identity["url"], identity.get("form")
            )
            != identity
            or request_id(identity) != observation["request_id"]
        ):
            return False
        _time(observation["cutoff"])
        if value is not True:
            return proof is None
        if (
            observation["acquired"] is not True
            or observation["interpreted"] is not False
            or observation.get("unavailable") is not False
            or not isinstance(proof, Mapping)
            or proof.keys()
            != {
                "generation_id",
                "content_digest",
                "input_fingerprint",
                "accepted_decision",
                "policy",
                "snapshots",
            }
            or proof["accepted_decision"] is not None
            or not isinstance(proof["generation_id"], str)
            or not proof["generation_id"].startswith("gen_")
            or not _sha(proof["generation_id"][4:])
            or not _sha(proof["content_digest"])
            or not _sha(proof["input_fingerprint"])
            or not isinstance(proof["snapshots"], list)
            or len(proof["snapshots"]) != 1
        ):
            return False
        policy, snapshot = proof["policy"], proof["snapshots"][0]
        return bool(
            isinstance(policy, Mapping)
            and policy.keys() == set(POLICY) | {"_bytes"}
            and type(policy["_bytes"]) is int
            and policy["_bytes"] >= 0
            and all(isinstance(policy[k], str) and policy[k] for k in POLICY)
            and policy["mode"] in {"shadow", "enforce", "paused"}
            and _time(policy["reviewed_at"]) <= _time(observation["cutoff"])
            and isinstance(snapshot, Mapping)
            and snapshot.keys() == set(SNAPSHOT) | {"source", "extract_sha256", "_bytes"}
            and type(snapshot["_bytes"]) is int
            and snapshot["_bytes"] >= 0
            and all(
                isinstance(snapshot[k], str) and snapshot[k] for k in ("snapshot_id", "watch_id")
            )
            and snapshot["source"] == identity["source"]
            and snapshot["classification"] in {"Ok", "NotModified"}
            and _sha(snapshot["body_sha256"])
            and _sha(snapshot["extract_sha256"])
            and _time(snapshot["fetched_at"]) <= _time(observation["cutoff"])
            and normalize_request(
                identity["source"], snapshot["method"], snapshot["url"], snapshot["form"]
            )
            == identity
        )
    except (BudgetExceeded, ValueError, TypeError, KeyError, RecursionError):
        return False


def _sha(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def revalidate(observation: Mapping[str, Any], *, budget: Budget) -> bool:
    """Recheck only the pinned generation and all manifest artifacts."""
    if not metadata_valid(observation):
        return False
    if observation["unsupported"] is not True:
        return True
    expected = observation["unsupported_support"]
    checker = _Evidence(
        budget.conn,
        budget.archive,
        Limits(),
        _time(observation["cutoff"]),
        observation["request"],
        budget,
    )
    return bool(checker.interpreted(expected["generation_id"], unsupported=True) == expected)
