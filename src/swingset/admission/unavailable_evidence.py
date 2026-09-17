"""Pinned unavailable-origin observations; absence is never current availability."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from swingset.admission.evidence_budget import Budget, BudgetExceeded
from swingset.admission.page_evidence import SNAPSHOT, _time
from swingset.schedule.event_evidence import request as normalize_request
from swingset.schedule.event_evidence import request_id

FORMAT = "unavailable-origin-observation-v1"
COLUMNS = (*SNAPSHOT, "http_status")
TERMINAL = frozenset({"Gone", "ExpectedUnavailable"})


def terminal_response(row: Mapping[str, Any]) -> bool:
    status = row["http_status"]
    return (
        row["via"] == "origin"
        and row["classification"] in TERMINAL
        and type(status) is int
        and 400 <= status <= 599
        and (row["classification"] != "Gone" or status in (404, 410))
    )


def observe(
    checker: Any, *, snapshots_exhausted: bool
) -> tuple[bool | None, dict[str, Any] | None]:
    """Reuse the request verifier's complete candidate domain and one budget."""
    if checker.acquired:
        return False, None
    if not snapshots_exhausted or checker.unknown:
        return None, None
    try:
        origins = [row for row in checker.matching.values() if row["via"] == "origin"]
        if not origins:
            return False, None
        latest = max(_time(row["fetched_at"]) for row in origins)
        tied = [row for row in origins if _time(row["fetched_at"]) == latest]
        outcomes = {row["classification"] in TERMINAL for row in tied}
        if len(outcomes) != 1:
            checker.reader.reasons["conflicting_latest_origin_outcomes"] += 1
            return None, None
        if outcomes == {False}:
            return False, None
        checked = []
        for candidate in tied:
            rows = checker.reader.read(
                "SELECT s.* FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=? AND w.source=?",
                (candidate["snapshot_id"], checker.request["source"]),
                COLUMNS,
                cap=1,
            )
            if (
                not rows
                or not terminal_response(rows[0])
                or any(rows[0][key] != candidate[key] for key in SNAPSHOT)
            ):
                checker.reader.reasons["unavailable_response_metadata_invalid"] += 1
                return None, None
            checked.append(rows[0])
        chosen = max(checked, key=lambda row: row["snapshot_id"])
        if not checker.artifact("body", chosen["body_sha256"]):
            return None, None
        return True, chosen
    except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError) as exc:
        checker.reader.reasons[
            str(exc) if isinstance(exc, BudgetExceeded) else "unavailable_evidence_invalid"
        ] += 1
        return None, None


def metadata_valid(observation: Mapping[str, Any]) -> bool:
    """Validate observation shape, not absence, current metadata, or artifact bytes."""
    try:
        return _metadata_valid(observation)
    except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError):
        return False


def _metadata_valid(observation: Mapping[str, Any]) -> bool:
    value, expected = observation.get("unavailable"), observation.get("unavailability_support")
    if any(
        observation[key] is not None and type(observation[key]) is not bool
        for key in ("acquired", "interpreted", "unavailable")
    ):
        return False
    identity = observation["request"]
    if (
        normalize_request(
            identity["source"], identity["method"], identity["url"], identity.get("form")
        )
        != identity
        or request_id(identity) != observation["request_id"]
    ):
        return False
    _time(observation["cutoff"])
    if value is not True:
        return expected is None
    if (
        observation["acquired"] is not False
        or observation["interpreted"] is not False
        or not isinstance(expected, Mapping)
    ):
        return False
    return (
        expected.keys() == set(COLUMNS) | {"_bytes"}
        and type(expected["_bytes"]) is int
        and expected["_bytes"] >= 0
        and all(
            isinstance(expected[key], str) and expected[key]
            for key in ("snapshot_id", "watch_id", "body_sha256")
        )
        and len(expected["body_sha256"]) == 64
        and all(c in "0123456789abcdef" for c in expected["body_sha256"])
        and terminal_response(expected)
        and _time(expected["fetched_at"]) <= _time(observation["cutoff"])
        and normalize_request(
            identity["source"], expected["method"], expected["url"], expected["form"]
        )
        == identity
    )


def revalidate(observation: Mapping[str, Any], *, budget: Budget) -> bool:
    """Recheck pinned positive response evidence without retargeting old observations."""
    if not metadata_valid(observation):
        return False
    if observation.get("unavailable") is not True:
        return True
    expected, identity = observation["unavailability_support"], observation["request"]
    rows = budget.read(
        "SELECT s.* FROM snapshots s JOIN watches w USING(watch_id) WHERE s.snapshot_id=? AND w.source=?",
        (expected["snapshot_id"], identity["source"]),
        COLUMNS,
        cap=1,
    )
    if not rows or dict(expected) != rows[0]:
        return False
    return budget.artifact("body", rows[0]["body_sha256"])
