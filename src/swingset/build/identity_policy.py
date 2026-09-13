"""Recheck every public default join against current decisions and retained support."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from swingset.admission.support import interpretation_support, selection_digest
from swingset.link.decisions import POLICY_VERSION, DecisionResolver
from swingset.normalize.names import normalize_name, paired_names
from swingset.state.identity_references import ReferenceReader

from .builder import PUBLISHED_TABLES, BuildError, BuildInput
from .files import sha256_file

RELEASE_POLICY_VERSION = "identity-release-v1"
PUBLIC_RESOLUTION_FIELDS = (
    "source_ref_ids",
    "decision_ids",
    "acceptance_policy",
    "acceptance_state",
    "journal_digest",
    "journal_generation",
)


def baseline_tables(
    baseline: Path, *, selected: tuple[str, ...] = PUBLISHED_TABLES
) -> dict[str, list[dict[str, Any]]]:
    """Read the acknowledged immutable dataset, checking every recorded file hash."""
    if not (baseline / "PUBLISHED").is_file():
        raise BuildError("correction requires an acknowledged published baseline")
    built = json.loads((baseline / "BUILT").read_bytes())
    if sha256_file(baseline / "_meta/manifest.json") != built["manifest_hash"]:
        raise BuildError("published baseline manifest failed verification")
    manifest = json.loads((baseline / "_meta/manifest.json").read_bytes())
    for name, expected in manifest["files"].items():
        if sha256_file(baseline / name) != expected:
            raise BuildError(f"published baseline file failed verification: {name}")
    return {
        table: [
            row
            for path in sorted((baseline / "data" / table).glob("*.parquet"))
            for batch in pq.ParquetFile(path).iter_batches()
            for row in batch.to_pylist()
        ]
        if table != "changelog"
        else []
        for table in selected
    }


def correction_token(
    conn: sqlite3.Connection, *, closure: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    from swingset.state.identity_journal import token

    journal = token(conn)
    from .closure_support import support_token

    revision = conn.execute(
        "SELECT value FROM revisions WHERE name='identity_decisions'"
    ).fetchone()
    return {
        "release_version": RELEASE_POLICY_VERSION,
        "decision_policy": POLICY_VERSION,
        "journal_digest": journal.digest,
        "journal_generation": journal.generation,
        "admission_digest": support_token(closure)
        if closure is not None
        else selection_digest(conn),
        "identity_decisions_revision": int(revision[0]) if revision else 0,
    }


def repair_placement_identities(tables: dict[str, list[dict[str, Any]]]) -> None:
    """Remove dependent identity and point claims whose entry support disappeared."""
    entries = {row["entry_id"]: row for row in tables["entries"]}
    for row in tables["placements"]:
        changed = False
        for role in ("leader", "follower"):
            entry = entries.get(row.get(f"{role}_entry_id"))
            supported = (
                entry is not None and entry.get("role") == role and entry.get("wsdc_id") is not None
            )
            identifier = entry["wsdc_id"] if entry is not None and supported else None
            if row.get(f"{role}_wsdc_id") != identifier:
                # A correction can withdraw a join, never introduce a replacement.
                row[f"{role}_wsdc_id"] = None
                changed = True
            if row.get(f"{role}_wsdc_id") is None:
                changed |= row.get(f"registry_points_{role}") is not None
                row[f"registry_points_{role}"] = None
        if changed:
            row["points_matches_expected"] = None
        if any(row.get(f"registry_points_{role}") is None for role in ("leader", "follower")):
            row["registry_confirmed"] = False
        if all(row.get(f"registry_points_{role}") is None for role in ("leader", "follower")):
            row["points_matches_expected"] = None


def _registry_support(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, frozenset[int]]:
    """Match only registry facts contained in this release's selected tables."""
    names = {
        row["wsdc_id"]: normalize_name(
            str(
                row.get("name_norm")
                or " ".join(str(row.get(k) or "") for k in ("first_name", "last_name"))
            )
        ).value
        for row in tables["dancers"]
    }
    registry: dict[tuple[Any, ...], set[int]] = {}
    for row in tables["registry_placements"]:
        if row.get("event_id") is not None:
            key = tuple(
                row.get(k) for k in ("event_id", "role", "division", "dance_style", "result")
            )
            registry.setdefault(key, set()).add(int(row["wsdc_id"]))
    contests = {row["contest_id"]: row for row in tables["contests"]}
    entries = {row["entry_id"]: row for row in tables["entries"]}
    support: dict[str, set[int]] = {}
    for placement in tables["placements"]:
        contest = contests.get(placement.get("contest_id"), {})
        if not contest.get("wsdc_points_eligible"):
            continue
        for role in ("leader", "follower"):
            entry = entries.get(placement.get(f"{role}_entry_id"))
            if entry is None or entry.get("role") != role:
                continue
            key = (
                placement.get("event_id"),
                role,
                contest.get("division"),
                contest.get("dance_style"),
            )
            ids = registry.get((*key, str(placement.get("place"))), set()) | registry.get(
                (*key, "F"), set()
            )
            support.setdefault(str(entry["entry_id"]), set()).update(
                identifier
                for identifier in ids
                if names.get(identifier) == normalize_name(str(entry.get("name_raw") or "")).value
            )
    return {key: frozenset(value) for key, value in support.items()}


def apply_identity_policy(
    conn: sqlite3.Connection,
    data: BuildInput,
    *,
    baseline: Path | None,
    correction_only: bool,
    closure: Mapping[str, Any] | None = None,
) -> BuildInput:
    """No new default joins before H17; old joins still need current support.

    Published v1 interpretations may retain their disclosed legacy status while
    their source locators are available and no revocation exists. This grants
    no admission or removal authority to that evidence.
    """
    tables = {name: data.tables.get(name, ()) for name in PUBLISHED_TABLES}
    mutable = {
        name: [dict(row) for row in tables[name]]
        for name in ("entries", "judges", "placements", "identity_links", "link_candidates")
    }
    tables.update(mutable)
    previous = (
        baseline_tables(baseline, selected=("entries", "judges"))
        if baseline
        else {name: [] for name in ("entries", "judges")}
    )
    previous_subjects = {
        (kind, str(row[key])): row
        for kind, name, key in (("entry", "entries", "entry_id"), ("judge", "judges", "judge_id"))
        for row in previous[name]
    }
    assertions = {
        (row["subject_kind"], row["subject_id"]): row for row in mutable["identity_links"]
    }
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in mutable["link_candidates"]:
        candidates.setdefault((candidate["subject_kind"], candidate["subject_id"]), []).append(
            candidate
        )
    contests = {row["contest_id"]: row for row in tables["contests"]}
    from .closure_support import observation_payload_loader

    reader = ReferenceReader(
        conn,
        observation_payloads=observation_payload_loader(conn, closure)
        if closure is not None
        else None,
    )
    resolver = DecisionResolver(conn)
    registry_support = _registry_support(tables)
    from .closure_support import interpretation_lookup

    support_cache: dict[tuple[str, str], dict[str, Any]] = (
        interpretation_lookup(closure) if closure is not None else {}
    )
    counts: Counter[str] = Counter()
    for kind, name, key in (("entry", "entries", "entry_id"), ("judge", "judges", "judge_id")):
        for row in mutable[name]:
            subject = (kind, str(row[key]))
            assertion = assertions.get(subject)
            proposed = row.get("wsdc_id")
            contest = contests.get(row.get("contest_id"), {})
            bindings = reader.for_record(kind, row, contest_name=contest.get("name_raw"))
            printed_ids = {
                int(binding.locator["source_wsdc_id"])
                for binding in bindings
                if binding.locator.get("source_wsdc_id") is not None
            }
            registry_ids = (
                registry_support.get(str(row[key]), frozenset()) if kind == "entry" else frozenset()
            )
            problem = resolver.binding_problem(
                bindings, subject_kind=kind, subject_id=str(row[key])
            )
            if len(printed_ids) > 1:
                problem = "contradictory_printed_source_identities"
            resolution = resolver.resolve(
                tuple(binding.reference for binding in bindings),
                source_wsdc_id=next(iter(printed_ids)) if len(printed_ids) == 1 else None,
                registry_wsdc_ids=registry_ids,
                legacy_subject_id=str(row[key]),
                reference_problem=problem,
            )
            reason = "unresolved"
            if proposed is not None:
                prior = previous_subjects.get(subject, {})
                evidence_key = (
                    str(row.get("snapshot_id") or ""),
                    str(row.get("parser_version") or ""),
                )
                if evidence_key not in support_cache:
                    support_cache[evidence_key] = (
                        {"state": "unavailable", "usable": False}
                        if closure is not None
                        else interpretation_support(
                            conn, evidence_key[0], parser_version=evidence_key[1]
                        )
                    )
                support = support_cache[evidence_key]
                legacy = (
                    prior.get("snapshot_id") == row.get("snapshot_id")
                    and prior.get("parser_version") == row.get("parser_version")
                    and support["state"] in {"legacy_unassessed", "unassessed", "superseded"}
                    and support.get("selected_observation", False)
                )
                if (
                    not assertion
                    or assertion.get("status") != "confirmed"
                    or assertion.get("wsdc_id") != proposed
                    or (kind == "entry" and row.get("link_status") != "confirmed")
                ):
                    reason = "assertion_not_confirmed"
                elif row.get("role") == "couple" or paired_names(str(row.get("name_raw") or "")):
                    reason = "individual_ownership_unavailable"
                elif not bindings:
                    reason = "source_reference_unavailable"
                elif not resolution.allows(int(proposed)):
                    reason = "current_decision_withholds"
                elif assertion.get("method") not in {"source_id", "registry_placement", "manual"}:
                    reason = "confirmation_method_unsupported"
                elif (
                    assertion.get("method") == "manual" and resolution.positive_wsdc_id != proposed
                ):
                    reason = "current_positive_decision_unavailable"
                elif assertion.get("method") == "source_id" and printed_ids != {int(proposed)}:
                    reason = "printed_source_identity_unavailable"
                elif assertion.get("method") == "registry_placement" and registry_ids != frozenset(
                    {int(proposed)}
                ):
                    reason = "registry_identity_support_unavailable"
                elif not support["usable"] and not legacy:
                    reason = "source_interpretation_unavailable"
                elif baseline is None or prior.get("wsdc_id") != proposed:
                    reason = "accuracy_gate_pending"
                else:
                    reason = "accepted_legacy" if legacy else "accepted"
                if reason not in {"accepted", "accepted_legacy"}:
                    row["wsdc_id"] = None
                    if kind == "entry":
                        row["link_status"] = "unmatched"
                        row["link_confidence"] = 0.0
                counts[reason] += 1
            if assertion is not None:
                assertion.update(
                    source_ref_ids=list(resolution.ref_ids),
                    decision_ids=list(resolution.decision_ids),
                    acceptance_policy=POLICY_VERSION,
                    acceptance_state="accepted"
                    if row.get("wsdc_id") is not None
                    else "revoked"
                    if proposed is not None
                    else "unresolved",
                    journal_digest=resolution.token.digest,
                    journal_generation=resolution.token.generation,
                )
                withdrawn = proposed is not None and row.get("wsdc_id") is None
                rejected_assertion = assertion.get("wsdc_id") is not None and not resolution.allows(
                    int(assertion["wsdc_id"])
                )
                if withdrawn or rejected_assertion:
                    assertion.update(
                        wsdc_id=None, status="unmatched", method="none", confidence=0.0
                    )
            for candidate in candidates.get(subject, []):
                if (proposed is not None and row.get("wsdc_id") is None) or not resolution.allows(
                    int(candidate["wsdc_id"])
                ):
                    candidate["chosen"] = False
    repair_placement_identities(mutable)
    from swingset.state.correction_age import correction_age

    acknowledged = json.loads((baseline / "PUBLISHED").read_bytes()) if baseline else None
    acknowledged_policy = (
        (json.loads((baseline / "_meta/manifest.json").read_bytes()).get("release_policy") or {})
        if baseline
        else None
    )
    age = correction_age(conn, baseline_receipt=acknowledged, baseline_policy=acknowledged_policy)
    policy = {
        "token": correction_token(conn, closure=closure),
        "mode": "closure"
        if closure is not None
        else "correction_only"
        if correction_only
        else "settled",
        "input_bundle_hash": data.input_bundle_hash,
        "input_file_hashes": dict(data.captured_file_hashes),
        "revisions": dict(data.revisions),
        "baseline_commit": json.loads((baseline / "PUBLISHED").read_bytes())["commit"]
        if baseline
        else None,
        "detected_at": age["detected_at"],
        "correction_age_basis": age["basis"],
        "default_join_counts": dict(sorted(counts.items())),
        "legacy_policy": "Published baseline support may remain unassessed; no revocation, new join, or removal authority is permitted.",
        "accuracy_expansions": "withheld_pending_H17",
    }
    return replace(data, tables=tables, release_policy=policy)
