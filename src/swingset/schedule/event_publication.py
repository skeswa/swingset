"""Source-event coverage from one acknowledged, verified baseline only."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from swingset.publish.safety import StaleCandidateError, verify_candidate_files

FIELDS = (
    "scope_kind",
    "scope_id",
    "source",
    "via",
    "event_id",
    "year",
    "enumeration_id",
    "enumeration_snapshot_ids",
    "enumeration_complete",
    "listed_pages",
    "acquired_pages",
    "interpreted_pages",
    "selected_interpreted_pages",
    "represented_pages",
    "unavailable_pages",
    "unsupported_pages",
    "scope_status",
    "scope_reasons",
    "evidence_cutoff",
    "method",
    "uncertainty",
)


def report(state_dir: Path, *, source: str, source_ref: str) -> dict[str, Any]:
    """Never consult a newer candidate or adopt local progress as publication.

    Resolve the baseline once, so concurrent promotion cannot mix its receipt
    with another candidate's coverage. File verification streams hashes;
    Parquet decoding is batched, not a total byte or execution-time bound.
    """
    result: dict[str, Any] = {
        "supported": False,
        "source": source,
        "source_ref": source_ref,
        "commit": None,
        "candidate_id": None,
        "verified_at": None,
        "evidence_cutoff": None,
        "rows": [],
        "published_pages": None,
        "basis": "acknowledged baseline coverage; local candidates do not advance publication",
    }
    baseline = state_dir / "baseline"
    if not baseline.is_symlink():
        return {**result, "reason": "acknowledged_baseline_unavailable"}
    try:
        candidate = baseline.resolve(strict=True)
        verify_candidate_files(candidate)
        receipt = json.loads((candidate / "PUBLISHED").read_bytes())
        manifest = json.loads((candidate / "_meta/manifest.json").read_bytes())
        if (
            not isinstance(receipt, dict)
            or not isinstance(receipt.get("commit"), str)
            or not receipt["commit"]
        ):
            raise ValueError("acknowledged commit is missing")
        policy = manifest.get("release_policy") or {}
        if not isinstance(policy, dict):
            raise ValueError("release policy is not an object")
        closure = policy.get("closure") or {}
        if not isinstance(closure, dict):
            raise ValueError("release closure is not an object")
        if receipt.get("candidate_id", candidate.name) != candidate.name:
            raise ValueError("publication receipt names another candidate")
        for field, expected in (
            ("closure_digest", closure.get("digest")),
            ("evidence_cutoff", closure.get("cutoff")),
        ):
            if receipt.get(field) is not None and receipt[field] != expected:
                raise ValueError("publication receipt differs from retained closure")
        result.update(
            commit=receipt["commit"],
            candidate_id=candidate.name,
            verified_at=receipt.get("verified_at"),
            evidence_cutoff=receipt.get("evidence_cutoff") or closure.get("cutoff"),
        )
        paths = sorted(
            name
            for name in manifest["files"]
            if name.startswith("data/coverage/") and name.endswith(".parquet")
        )
        rows = []
        extension = False
        for name in paths:
            parquet = pq.ParquetFile(candidate / name)
            names = set(parquet.schema_arrow.names)
            if (
                not {"scope_kind", "scope_id", "source", "enumeration_id", "represented_pages"}
                <= names
            ):
                continue
            extension = True
            for batch in parquet.iter_batches(
                batch_size=64,
                columns=[field for field in FIELDS if field in names],
                use_threads=False,
            ):
                for row in batch.to_pylist():
                    if (row["scope_kind"], row["source"], row["scope_id"]) == (
                        "source_event",
                        source,
                        source_ref,
                    ):
                        row = {
                            key: value.isoformat() if isinstance(value, (date, datetime)) else value
                            for key, value in row.items()
                        }
                        rows.append(row)
                        if len(rows) > 100:
                            return {**result, "reason": "published_event_row_limit"}
        if not extension:
            return {**result, "reason": "event_coverage_unavailable"}
        if not rows:
            return {**result, "reason": "source_event_not_reported"}
        # Older receipts may identify a public baseline, but cannot bind new
        # extension claims to this candidate and its selected evidence.
        if (
            receipt.get("candidate_id") != candidate.name
            or not closure.get("digest")
            or not closure.get("cutoff")
            or receipt.get("closure_digest") != closure["digest"]
            or receipt.get("evidence_cutoff") != closure["cutoff"]
        ):
            return {**result, "reason": "event_publication_receipt_unbound"}
        # Transport-specific rows can overlap. Only the explicitly deduplicated
        # format's single aggregate row supplies a scalar page count.
        pages = (
            rows[0].get("represented_pages")
            if len(rows) == 1
            and rows[0].get("method") == "source-event-release-v1"
            and rows[0].get("via") == "unknown"
            else None
        )
        return {**result, "supported": True, "rows": rows, "published_pages": pages}
    except (OSError, ValueError, KeyError, TypeError, StaleCandidateError):
        return {
            **result,
            "supported": False,
            "rows": [],
            "published_pages": None,
            "reason": "publication_receipt_or_files_unverified",
        }
