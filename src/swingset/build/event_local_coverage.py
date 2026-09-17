"""Bounded cutoff-local observations pinned independently of output selection.

Negatives describe an observation, not durable absence. Positive support must
still verify at each later boundary, even when the SQLite proof is reusable.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Any

from swingset.admission.evidence_budget import Budget, BudgetExceeded
from swingset.admission.page_evidence import FORMAT as EVIDENCE_FORMAT
from swingset.admission.page_evidence import Limits, Session, revalidate_positive
from swingset.admission.unavailable_evidence import FORMAT as UNAVAILABILITY_FORMAT
from swingset.admission.unavailable_evidence import revalidate as revalidate_unavailability
from swingset.admission.unsupported_evidence import FORMAT as UNSUPPORTED_FORMAT
from swingset.admission.unsupported_evidence import metadata_valid as unsupported_valid
from swingset.admission.unsupported_evidence import revalidate as revalidate_unsupported

from .closure_manifest import ClosureError, digest
from .event_artifacts import observation_time, source

FORMAT = "release-local-pages-v3"
UNAVAILABLE_FORMAT = "release-local-pages-v2"
LEGACY_FORMAT = "release-local-pages-v1"
MAX_REQUESTS = 32
MAX_BYTES = 4 * 1024 * 1024
CAPTURE_LIMITS = Limits()
VALIDATION_LIMITS = Limits(seconds=30)


def _capture_policy() -> dict[str, Any]:
    return {
        "format": "release-local-page-policy-v3",
        "unsupported_verifier_format": UNSUPPORTED_FORMAT,
        "verifier_format": EVIDENCE_FORMAT,
        "unavailability_verifier_format": UNAVAILABILITY_FORMAT,
        "max_requests": MAX_REQUESTS,
        "max_output_bytes": MAX_BYTES,
        "capture_limits": asdict(CAPTURE_LIMITS),
        "validation_limits": asdict(VALIDATION_LIMITS),
    }


def _policy(local: Mapping[str, Any]) -> tuple[int, int, Limits]:
    """Honor the pinned policy within hard bounds; never substitute defaults."""
    try:
        policy = local["policy"]
        legacy = local.get("format") == LEGACY_FORMAT
        expected_fields = {
            "format",
            "verifier_format",
            "max_requests",
            "max_output_bytes",
            "capture_limits",
            "validation_limits",
        }
        if not legacy:
            expected_fields.add("unavailability_verifier_format")
        if local.get("format") == FORMAT:
            expected_fields.add("unsupported_verifier_format")
        if (
            set(policy) != expected_fields
            or policy["format"]
            != (
                "release-local-page-policy-v1"
                if legacy
                else "release-local-page-policy-v3"
                if local.get("format") == FORMAT
                else "release-local-page-policy-v2"
            )
            or policy["verifier_format"] != EVIDENCE_FORMAT
            or (not legacy and policy["unavailability_verifier_format"] != UNAVAILABILITY_FORMAT)
            or (
                local.get("format") == FORMAT
                and policy["unsupported_verifier_format"] != UNSUPPORTED_FORMAT
            )
        ):
            raise ValueError("unsupported verification policy")
        requests, size = policy["max_requests"], policy["max_output_bytes"]
        if (
            type(requests) is not int
            or not 1 <= requests <= 32
            or type(size) is not int
            or not 1 <= size <= 4 * 1024 * 1024
        ):
            raise ValueError("invalid output bounds")
        expected = set(asdict(Limits()))
        if (
            set(policy["capture_limits"]) != expected
            or set(policy["validation_limits"]) != expected
        ):
            raise ValueError("incomplete verification limits")
        Limits(**policy["capture_limits"])
        validation = Limits(**policy["validation_limits"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ClosureError("local_page_policy_invalid") from exc
    return requests, size, validation


def capture(
    conn: sqlite3.Connection, entries: list[dict[str, Any]], *, cutoff: str
) -> dict[str, Any] | None:
    archive = source(conn)
    if archive is None:
        return None
    now = observation_time(conn)
    policy = _capture_policy()
    session = Session(
        conn, archive, cutoff=datetime.fromisoformat(cutoff), now=now, limits=CAPTURE_LIMITS
    )
    pages: dict[str, dict[str, Any]] = {}
    size = 0
    exhausted = False
    for entry in entries:
        if not entry["enumeration_id"] or entry["reasons"]:
            continue
        for member in entry["members"]:
            identifier = member["request_id"]
            if identifier in pages or exhausted:
                continue
            if len(pages) >= policy["max_requests"]:
                exhausted = True
                continue
            try:
                rows = session.read(
                    "SELECT request_json FROM source_event_enumeration_members WHERE enumeration_id=? AND request_id=?",
                    (entry["enumeration_id"], identifier),
                    ("request_json",),
                    cap=1,
                )
                if not rows:
                    exhausted = True
                    continue
                result = session.verify_request(
                    json.loads(rows[0]["request_json"]),
                    classify_unavailability=True,
                    classify_unsupported=True,
                )
                # Keep exact positive proofs and observation status, not scan
                # diagnostics/limits repeated for every request.
                page = {
                    key: result[key]
                    for key in (
                        "request_id",
                        "request",
                        "acquired",
                        "interpreted",
                        "acquisition_support",
                        "interpretation_support",
                        "unavailable",
                        "unavailability_support",
                        "unsupported",
                        "unsupported_support",
                        "reasons",
                    )
                }
                count = len(json.dumps(page).encode())
                if size + count > policy["max_output_bytes"]:
                    exhausted = True
                    continue
                size += count
                pages[identifier] = page
            except (BudgetExceeded, ValueError, TypeError, KeyError, RecursionError):
                exhausted = True
    return {
        "format": FORMAT,
        "policy": policy,
        "verified_at": now.isoformat(),
        "pages": pages,
        "scan_limited": exhausted,
    }


def summary(entry: Mapping[str, Any], local: Mapping[str, Any] | None) -> dict[str, Any]:
    if local is None or entry["reasons"] or not entry["enumeration_id"]:
        return {
            "acquired_pages": None,
            "interpreted_pages": None,
            "acquisition_unknown_pages": entry.get("listed_pages"),
            "interpretation_unknown_pages": entry.get("listed_pages"),
            "unavailable_pages": None,
            "unsupported_pages": None,
        }
    result: dict[str, Any] = {}
    for stage, field in (("acquired", "acquisition"), ("interpreted", "interpretation")):
        values = [local["pages"].get(m["request_id"], {}).get(stage) for m in entry["members"]]
        unknown = sum(value is None for value in values)
        result[stage + "_pages"] = None if unknown else sum(value is True for value in values)
        result[field + "_unknown_pages"] = unknown
    values = [local["pages"].get(m["request_id"], {}).get("unavailable") for m in entry["members"]]
    result["unavailable_pages"] = (
        sum(value is True for value in values)
        if local.get("format") in (FORMAT, UNAVAILABLE_FORMAT)
        and all(type(value) is bool for value in values)
        else None
    )
    values = [local["pages"].get(m["request_id"], {}).get("unsupported") for m in entry["members"]]
    result["unsupported_pages"] = (
        sum(value is True for value in values)
        if local.get("format") == FORMAT and all(type(value) is bool for value in values)
        else None
    )
    return result


def _pages(witness: Mapping[str, Any]) -> list[dict[str, Any]]:
    local = witness.get("local_pages")
    if local is None:
        return []
    requests, size, _ = _policy(local)
    if (
        local.get("format") not in (FORMAT, UNAVAILABLE_FORMAT, LEGACY_FORMAT)
        or len(local["pages"]) > requests
        or len(json.dumps(local).encode()) > size + 4096
    ):
        raise ClosureError("local_page_witness_invalid")
    verified = datetime.fromisoformat(local["verified_at"])
    cutoff = datetime.fromisoformat(witness["cutoff"])
    if verified.tzinfo is None or verified < cutoff:
        raise ClosureError("local_page_verification_time_invalid")
    membership = {
        m["request_id"]
        for e in witness["entries"]
        if e["enumeration_id"] and not e["reasons"]
        for m in e["members"]
    }
    result = []
    for identifier, page in local["pages"].items():
        if (
            identifier not in membership
            or identifier != page["request_id"]
            or any(
                page[stage] is not None and type(page[stage]) is not bool
                for stage in ("acquired", "interpreted")
            )
        ):
            raise ClosureError("local_page_membership_invalid")
        if local["format"] in (FORMAT, UNAVAILABLE_FORMAT) and (
            "unavailable" not in page
            or "unavailability_support" not in page
            or (page["unavailable"] is not None and type(page["unavailable"]) is not bool)
            or (
                page["unavailable"] is True
                and (page["acquired"] is not False or page["interpreted"] is not False)
            )
            or (
                (page["unavailable"] is True) != isinstance(page["unavailability_support"], Mapping)
            )
            or (page["unavailable"] is not True and page["unavailability_support"] is not None)
        ):
            raise ClosureError("local_page_unavailability_invalid")
        if local["format"] == FORMAT and not unsupported_valid(
            {**page, "cutoff": witness["cutoff"]}
        ):
            raise ClosureError("local_page_unsupported_invalid")
        result.append({**page, "cutoff": witness["cutoff"]})
    return result


def check_artifacts(conn: sqlite3.Connection, witness: Mapping[str, Any]) -> Budget | None:
    """Run before a SQLite-only cache lookup, once per validation invocation."""
    if witness.get("local_pages") is None:
        return None
    archive = source(conn)
    if archive is None:
        raise ClosureError("local_page_artifact_provider_missing")
    _, _, limits = _policy(witness["local_pages"])
    budget = Budget(conn, archive, limits.budget())
    try:
        for page in _pages(witness):
            refs = []
            if page["acquired"] is True:
                refs.append(("body", page["acquisition_support"]["body_sha256"]))
            if page["interpreted"] is True:
                for snapshot in page["interpretation_support"]["snapshots"]:
                    refs.extend(
                        (("body", snapshot["body_sha256"]), ("extract", snapshot["extract_sha256"]))
                    )
            if (
                witness["local_pages"]["format"] in (FORMAT, UNAVAILABLE_FORMAT)
                and page["unavailable"] is True
            ):
                refs.append(("body", page["unavailability_support"]["body_sha256"]))
            if witness["local_pages"]["format"] == FORMAT and page["unsupported"] is True:
                for snapshot in page["unsupported_support"]["snapshots"]:
                    refs.extend(
                        (("body", snapshot["body_sha256"]), ("extract", snapshot["extract_sha256"]))
                    )
            for kind, sha in refs:
                if not budget.artifact(kind, sha):
                    raise ClosureError("local_page_artifact_changed")
    except (BudgetExceeded, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ClosureError("local_page_artifact_unverifiable") from exc
    return budget


def validate(conn: sqlite3.Connection, witness: Mapping[str, Any], budget: Budget | None) -> None:
    if witness.get("local_pages") is None:
        return
    if budget is None:
        budget = check_artifacts(conn, witness)
    assert budget is not None
    try:
        for page in _pages(witness):
            if not revalidate_positive(conn, budget.archive, page, budget=budget):
                raise ClosureError("local_page_support_changed")
            if witness["local_pages"]["format"] in (
                FORMAT,
                UNAVAILABLE_FORMAT,
            ) and not revalidate_unavailability(page, budget=budget):
                raise ClosureError("local_page_unavailability_changed")
            if witness["local_pages"]["format"] == FORMAT and not revalidate_unsupported(
                page, budget=budget
            ):
                raise ClosureError("local_page_unsupported_changed")
            # Request identity must be the retained enumeration member, even if
            # every stage was unassessed/negative at the pinned observation.
            for entry in witness["entries"]:
                if any(m["request_id"] == page["request_id"] for m in entry["members"]):
                    rows = budget.read(
                        "SELECT request_json FROM source_event_enumeration_members WHERE enumeration_id=? AND request_id=?",
                        (entry["enumeration_id"], page["request_id"]),
                        ("request_json",),
                        cap=1,
                    )
                    if not rows or json.loads(rows[0]["request_json"]) != page["request"]:
                        raise ClosureError("local_page_request_changed")
                    break
    except (BudgetExceeded, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ClosureError("local_page_support_unverifiable") from exc


def read_set(witness: Mapping[str, Any]) -> dict[str, Any]:
    """Only exact identifiers; never keep decoded proof payloads in cache."""
    snapshots, generations, decisions, watches = set(), set(), set(), set()
    for page in _pages(witness):
        acquired = page.get("acquisition_support")
        interpreted = page.get("interpretation_support")
        unavailable = (
            page.get("unavailability_support")
            if witness["local_pages"]["format"] in (FORMAT, UNAVAILABLE_FORMAT)
            else None
        )
        unsupported = (
            page.get("unsupported_support") if witness["local_pages"]["format"] == FORMAT else None
        )
        for row in (
            ([acquired] if acquired else [])
            + (interpreted["snapshots"] if interpreted else [])
            + ([unavailable] if unavailable else [])
            + (unsupported["snapshots"] if unsupported else [])
        ):
            snapshots.add(row["snapshot_id"])
            watches.add(row["watch_id"])
        if unsupported:
            generations.add(unsupported["generation_id"])
        if interpreted:
            generations.add(interpreted["generation_id"])
            decisions.add(interpreted["accepted_decision"]["decision_id"])
    return {
        "snapshots": sorted(snapshots),
        "generations": sorted(generations),
        "decisions": sorted(decisions),
        "watches": sorted(watches),
    }


def fingerprint(reader: Any, evidence: Mapping[str, Any]) -> str:
    values = []
    for key, table, column in (
        ("snapshots", "snapshots", "snapshot_id"),
        ("generations", "source_generations", "generation_id"),
        ("decisions", "admission_decisions", "decision_id"),
        ("watches", "watches", "watch_id"),
    ):
        for identifier in evidence[key]:
            values.append(digest(reader.rows(table, f"{column}=?", (identifier,), limit=1)))
    return digest(values)
