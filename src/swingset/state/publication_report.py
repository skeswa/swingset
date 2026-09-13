"""Report local readiness separately from acknowledged publication progress."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swingset.build.files import sha256_file
from swingset.build.identity_policy import correction_token
from swingset.publish.safety import StaleCandidateError, verify_candidate_files


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("receipt or manifest must be an object")
    return value


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def _age(value: Any, now: datetime) -> float | None:
    parsed = _instant(value)
    return max(0.0, (now - parsed).total_seconds()) if parsed else None


def _current(conn: sqlite3.Connection, policy: dict[str, Any]) -> bool:
    closure = policy.get("closure") if policy.get("mode") == "closure" else None
    if closure is not None:
        from swingset.build.closure import ClosureError, validate

        try:
            validate(conn, closure)
        except (ClosureError, KeyError, ValueError):
            return False
    if policy.get("token") != correction_token(conn, closure=closure):
        return False
    for name in ("overrides/identity_overrides.csv", "overrides/suppressions.csv"):
        expected = policy.get("input_file_hashes", {}).get(name)
        if expected is not None:
            row = conn.execute(
                "SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name=?",
                (name,),
            ).fetchone()
            if row is None or row[0] != expected:
                return False
    return True


def _inputs_match(conn: sqlite3.Connection, built: dict[str, Any], policy: dict[str, Any]) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    if row is None or row[0] != policy.get("input_bundle_hash"):
        return False
    paths = built.get("input_paths")
    if not isinstance(paths, dict) or not paths.get("overrides"):
        return False
    for name in ("identity_overrides.csv", "suppressions.csv"):
        expected = policy.get("input_file_hashes", {}).get("overrides/" + name)
        if expected is not None and sha256_file(Path(paths["overrides"]) / name) != expected:
            return False
    return True


def publication_report(conn: sqlite3.Connection, state_dir: Path, now: datetime) -> dict[str, Any]:
    """Files and receipts supply evidence; no writes, source calls, or local proof adoption."""
    if now.tzinfo is None:
        raise ValueError("publication reporting requires a timezone-aware clock")
    published: dict[str, Any] = {
        name: None
        for name in ("commit", "verified_at", "candidate_id", "closure_digest", "evidence_cutoff")
    }
    baseline_policy: dict[str, Any] = {}
    baseline_receipt: dict[str, Any] = {}
    baseline = state_dir / "baseline"
    errors: list[dict[str, str]] = []
    if baseline.is_symlink():
        try:
            baseline = baseline.resolve(strict=True)
            verify_candidate_files(baseline)
            receipt = _object(baseline / "PUBLISHED")
            manifest = _object(baseline / "_meta/manifest.json")
            if not isinstance(receipt.get("commit"), str) or not receipt["commit"]:
                raise ValueError("missing acknowledged commit")
            baseline_receipt = receipt
            baseline_policy = manifest.get("release_policy") or {}
            closure = baseline_policy.get("closure") or {}
            published.update(
                commit=receipt["commit"],
                verified_at=receipt.get("verified_at"),
                candidate_id=baseline.name,
                closure_digest=receipt.get("closure_digest") or closure.get("digest"),
                evidence_cutoff=receipt.get("evidence_cutoff") or closure.get("cutoff"),
            )
        except (OSError, ValueError, KeyError, StaleCandidateError):
            errors.append(
                {"scope": "baseline", "reason": "publication_receipt_or_files_unverified"}
            )
    waiting = []
    candidates = state_dir / "candidates"
    for path in sorted(candidates.iterdir()) if candidates.is_dir() else ():
        if not path.is_dir() or (path / "REJECTED").exists() or (path / "PUBLISHED").exists():
            continue
        if not (path / "BUILT").is_file():
            continue
        try:
            built = _object(path / "BUILT")
            manifest = _object(path / "_meta/manifest.json")
            policy = manifest.get("release_policy") or {}
            if not built.get("changed") or built.get("baseline_commit") != published["commit"]:
                continue
            if not _current(conn, policy) or not _inputs_match(conn, built, policy):
                continue
            if policy.get("mode") == "closure":
                from swingset.build.generations import completed

                if not completed(conn, path.name, sha256_file(path / "_meta/manifest.json")):
                    continue
            verify_candidate_files(path)
            waiting.append((path.name, manifest.get("built_at")))
        except (OSError, ValueError, KeyError, StaleCandidateError):
            errors.append({"scope": path.name, "reason": "local_candidate_unverified"})
    times = [value for _, value in waiting if _instant(value) is not None]
    oldest = min(times, key=lambda value: _instant(value) or now) if times else None
    from swingset.state.correction_age import correction_age

    correction = correction_age(
        conn, baseline_receipt=baseline_receipt, baseline_policy=baseline_policy, now=now
    )
    detected = correction["detected_at"]
    correction_pending = (
        not _current(conn, baseline_policy) if baseline_policy else detected is not None
    )
    return {
        "published": published,
        "awaiting_publication": {
            "candidate_ids": [name for name, _ in waiting],
            "oldest_built_at": oldest,
            "age_seconds": _age(oldest, now),
            "basis": "verified local candidates; no remote receipt",
        },
        "correction": {
            **correction,
            "awaiting_publication": correction_pending,
            "age_seconds": correction["age_seconds"] if correction_pending else None,
        },
        "verification_issues": errors,
    }
