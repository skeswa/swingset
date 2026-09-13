"""Deterministic identity samples and externally adjudicated cohort estimates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

METHOD = "sha256-ranked-stratified-without-replacement-v2"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _identity_keys(row: dict[str, Any]) -> list[str]:
    keys = ["subject:" + row["sample_id"], "event:" + row["event_id"]]
    name = " ".join(row["name_raw"].casefold().split())
    if name:
        keys.append("name:" + name)
    numbers = set(row["candidate_ids"])
    if row["accepted_wsdc_id"] is not None:
        numbers.add(row["accepted_wsdc_id"])
    keys.extend(f"person:{number}" for number in sorted(numbers))
    return keys


def _verify_packet(packet: dict[str, Any]) -> None:
    expected = {key: value for key, value in packet.items() if key != "sample_digest"}
    if packet.get("sample_digest") != _digest(expected):
        raise ValueError("sample artifact digest mismatch")


def _prior_exposure(
    packets: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], list[dict[str, Any]], set[str]]:
    """Carry full-population exposure, including ancestors and unsampled people."""
    keys: dict[str, set[str]] = defaultdict(set)
    provenance = []
    unknown = set()
    for packet in packets:
        _verify_packet(packet)
    unique = {packet["sample_digest"]: packet for packet in packets}
    for digest, packet in sorted(unique.items()):
        exposure = packet.get("tuning_exposure")
        complete = False
        if exposure is not None:
            if (
                not isinstance(exposure, dict)
                or exposure.get("version") != 1
                or type(exposure.get("complete")) is not bool
                or not isinstance(exposure.get("keys"), list)
                or not all(isinstance(key, str) and key for key in exposure["keys"])
            ):
                raise ValueError("invalid prior tuning exposure manifest")
            expected_count = sum(
                row["population_size"] for row in packet["strata"] if row["split"] == "tuning"
            )
            if exposure.get("population_subjects") != expected_count:
                raise ValueError("prior tuning exposure denominator mismatch")
            retained = set(exposure["keys"])
            sampled = {
                key
                for row in packet["samples"]
                if row["split"] == "tuning"
                for key in _identity_keys(row)
            }
            if not sampled <= retained:
                raise ValueError("prior tuning exposure omits sampled identities")
            complete = exposure["complete"]
            for key in retained:
                keys[key].add(digest)
        if not complete:
            unknown.add(digest)
        provenance.append(
            {
                "sample_digest": digest,
                "cohort": packet["cohort"],
                "cutoff": packet["cutoff"],
                "population_digest": packet["population_digest"],
                "exposure_complete": complete,
                "tuning_population_size": sum(
                    row["population_size"] for row in packet["strata"] if row["split"] == "tuning"
                ),
            }
        )
    return keys, provenance, unknown


def _components(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Conservatively keep shared events and possible people in one split."""
    parent: dict[str, str] = {}

    def root(key: str) -> str:
        parent.setdefault(key, key)
        current = key
        while parent[current] != current:
            current = parent[current]
        while parent[key] != key:
            prior = parent[key]
            parent[key] = current
            key = prior
        return current

    for row in rows:
        keys = _identity_keys(row)
        subject = keys[0]
        for key in keys[1:]:
            left, right = root(subject), root(key)
            parent[max(left, right)] = min(left, right)
    return {row["sample_id"]: root("subject:" + row["sample_id"]) for row in rows}


def draw_sample(
    population: list[dict[str, Any]],
    *,
    seed: str,
    cohort: str,
    cutoff: str,
    per_stratum: int = 10,
    evaluation_fraction: float = 0.5,
    context: dict[str, Any] | None = None,
    prior_tuning_packets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Freeze every stratum's denominator and sample membership; no labels inferred."""
    if per_stratum < 1 or not 0 < evaluation_fraction < 1:
        raise ValueError("positive per-stratum size and a split fraction between 0 and 1 required")
    if not seed.strip() or not cohort.strip() or datetime.fromisoformat(cutoff).tzinfo is None:
        raise ValueError("seed, cohort, and timezone-aware evidence cutoff required")
    cutoff_at = datetime.fromisoformat(cutoff)
    prior_keys, prior_provenance, unknown_prior = _prior_exposure(prior_tuning_packets or [])
    rows = []
    for item in population:
        inputs = [item.get("evidence", {})] + [
            reference.get("snapshot") or {} for reference in item.get("registry_evidence", [])
        ]
        if any(
            evidence.get("fetched_at")
            and datetime.fromisoformat(evidence["fetched_at"]) > cutoff_at
            for evidence in inputs
        ):
            raise ValueError("population contains evidence fetched after the cutoff")
        if item["stream"] not in {"accepted", "unresolved"}:
            raise ValueError("known failure cases belong in the separate regression corpus")
        row = json.loads(json.dumps(item))
        row["sample_id"] = _digest((row["source"], row["subject_kind"], row["subject_id"]))[:24]
        rows.append(row)
    if len({r["sample_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate population subject")
    rows.sort(key=lambda row: row["sample_id"])
    components = _components(rows)
    exposed: dict[str, set[str]] = defaultdict(set)
    component_sizes: dict[str, int] = defaultdict(int)
    for row in rows:
        component = components[row["sample_id"]]
        component_sizes[component] += 1
        exposed[component].update(unknown_prior)
        for key in _identity_keys(row):
            exposed[component].update(prior_keys.get(key, ()))
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    eligibility: dict[str, dict[str, Any]] = {}
    for row in rows:
        component = components[row["sample_id"]]
        row["split_group"] = _digest(component)[:24]
        row["heldout_eligible"] = not exposed[component]
        row["split"] = (
            "evaluation"
            if row["heldout_eligible"]
            and int(_digest((seed, component)), 16) / 2**256 < evaluation_fraction
            else "tuning"
        )
        row["stratum"] = json.dumps(
            [
                row["split"],
                row["stream"],
                row["source"],
                row["subject_kind"],
                row["era"],
                row["flags"],
            ],
            separators=(",", ":"),
        )
        row["sample_fingerprint"] = _digest(row)
        strata[row["stratum"]].append(row)
        scope = json.dumps(
            [row["stream"], row["source"], row["subject_kind"], row["era"], row["flags"]],
            separators=(",", ":"),
        )
        denominator = eligibility.setdefault(
            scope,
            {"stratum": scope, "population_size": 0, "eligible": 0, "excluded": 0},
        )
        denominator["population_size"] += 1
        denominator["eligible" if row["heldout_eligible"] else "excluded"] += 1
    selected, counts = [], []
    for name, members in sorted(strata.items()):
        chosen = sorted(members, key=lambda row: _digest((seed, name, row["sample_id"])))[
            :per_stratum
        ]
        selected.extend(chosen)
        counts.append(
            {
                "stratum": name,
                "split": members[0]["split"],
                "stream": members[0]["stream"],
                "population_size": len(members),
                "sample_size": len(chosen),
            }
        )
    artifact = {
        "schema_version": 2,
        "cohort": cohort,
        "cutoff": cutoff,
        "seed": seed,
        "sampling_method": METHOD,
        "per_stratum": per_stratum,
        "evaluation_fraction": evaluation_fraction,
        "population_digest": _digest(rows),
        "context": context or {},
        "strata": counts,
        "samples": selected,
        "split_groups": len(set(components.values())),
        "split_note": "Events, known or candidate IDs, and matching names share a split. Unknown aliases can still leak; review split membership before tuning. A connected population may occupy only one split.",
        "prior_tuning_packets": prior_provenance,
        "heldout_eligibility": {
            "population_size": len(rows),
            "eligible": sum(row["heldout_eligible"] for row in rows),
            "excluded": sum(not row["heldout_eligible"] for row in rows),
            "status": "prior_exposure_unavailable" if unknown_prior else "assessed",
            "strata": [eligibility[key] for key in sorted(eligibility)],
            "excluded_components": [
                {
                    "split_group": _digest(component)[:24],
                    "population_size": component_sizes[component],
                    "prior_packet_digests": sorted(digests),
                    "reason": "prior_tuning_population_unavailable"
                    if unknown_prior
                    else "connected_to_prior_tuning",
                }
                for component, digests in sorted(exposed.items())
                if digests
            ],
            "scope": "Eligibility is assessed against the explicitly supplied prior tuning packets; omitted tuning data and unknown aliases are not certified disjoint. Excluded components remain in tuning, not heldout.",
        },
        "tuning_exposure": {
            "version": 1,
            "complete": not unknown_prior,
            "population_subjects": sum(row["split"] == "tuning" for row in rows),
            "keys": sorted(
                set(prior_keys)
                | {key for row in rows if row["split"] == "tuning" for key in _identity_keys(row)}
            ),
        },
    }
    artifact["sample_digest"] = _digest(artifact)
    return artifact


def _wilson(correct: int, count: int) -> list[float] | None:
    if count == 0:
        return None
    z = 1.959963984540054
    estimate = correct / count
    scale = 1 + z * z / count
    center = (estimate + z * z / (2 * count)) / scale
    half = z * math.sqrt(estimate * (1 - estimate) / count + z * z / (4 * count * count)) / scale
    return [max(0.0, center - half), min(1.0, center + half)]


def evaluate(
    sample: dict[str, Any], reviews: list[dict[str, Any]], *, split: str = "evaluation"
) -> dict[str, Any]:
    """Report per-stratum estimates only after its entire sample is adjudicated."""
    if split not in {"evaluation", "tuning"}:
        raise ValueError("split must be evaluation or tuning")
    _verify_packet(sample)
    subjects = {row["sample_id"]: row for row in sample["samples"]}
    by_id = {}
    for review in reviews:
        identifier = review["sample_id"]
        if identifier not in subjects or identifier in by_id:
            raise ValueError("unknown or duplicate review subject")
        row = subjects[identifier]
        if review.get("sample_fingerprint") != row["sample_fingerprint"]:
            raise ValueError("review does not bind to frozen subject evidence")
        references = review.get("evidence")
        if (
            not isinstance(review.get("reviewer"), str)
            or not review["reviewer"].strip()
            or not isinstance(review.get("method"), str)
            or not review["method"].strip()
            or not isinstance(references, list)
            or not references
            or not all(isinstance(value, str) and value.strip() for value in references)
        ):
            raise ValueError("review requires author, independent evidence references, and method")
        if datetime.fromisoformat(review["reviewed_at"]).tzinfo is None:
            raise ValueError("review time requires a timezone")
        allowed = (
            {"correct", "incorrect", "insufficient_evidence"}
            if row["stream"] == "accepted"
            else {"matched", "no_registry_identity", "insufficient_evidence"}
        )
        if review["decision"] not in allowed:
            raise ValueError("review decision does not apply to this stream")
        reference = review.get("reference_wsdc_id")
        if review["decision"] in {"correct", "matched"} and (
            type(reference) is not int or reference < 1
        ):
            raise ValueError("a positive identity review requires its registry ID")
        if review["decision"] == "correct" and reference != row["accepted_wsdc_id"]:
            raise ValueError("correct review contradicts selected ID")
        if (
            row["stream"] == "unresolved"
            and review["decision"] != "insufficient_evidence"
            and review.get("candidate_search_complete") is not True
        ):
            raise ValueError("unresolved review must examine identities beyond the candidate list")
        by_id[identifier] = review
    reports = []
    for stratum in sample["strata"]:
        if stratum["split"] != split:
            continue
        members = [row for row in subjects.values() if row["stratum"] == stratum["stratum"]]
        judged = [(row, by_id[row["sample_id"]]) for row in members if row["sample_id"] in by_id]
        decisive = [
            (row, review) for row, review in judged if review["decision"] != "insufficient_evidence"
        ]
        complete = len(decisive) == len(members) and bool(members)
        correct = sum(review["decision"] == "correct" for _, review in decisive)
        accepted = stratum["stream"] == "accepted"
        reports.append(
            {
                **stratum,
                "reviewed": len(judged),
                "decisive_reviews": len(decisive),
                "missing_reviews": len(members) - len(judged),
                "insufficient_evidence": len(judged) - len(decisive),
                "status": "available" if complete else "unavailable",
                "precision_numerator": correct if accepted else None,
                "precision_denominator": len(decisive) if accepted else None,
                "precision": correct / len(decisive) if accepted and complete else None,
                "precision_interval_95": _wilson(correct, len(decisive))
                if accepted and complete
                else None,
                "uncertainty_method": "Wilson score interval, approximate binomial, no finite-population correction"
                if accepted
                else None,
                "false_accepted": sum(review["decision"] == "incorrect" for _, review in decisive),
                "missing_candidates": sum(
                    review["decision"] == "matched"
                    and review["reference_wsdc_id"] not in row["candidate_ids"]
                    for row, review in decisive
                ),
                "resolvable_abstentions": sum(
                    review["decision"] == "matched" for _, review in decisive
                ),
                "review_methods": sorted({review["method"] for _, review in judged}),
            }
        )
    accepted_reports = [r for r in reports if r["stream"] == "accepted"]
    return {
        "cohort": sample["cohort"],
        "cutoff": sample["cutoff"],
        "sample_digest": sample["sample_digest"],
        "review_digest": _digest(sorted(reviews, key=lambda review: review["sample_id"])),
        "split": split,
        "sampling_method": sample["sampling_method"],
        "heldout_eligibility": sample.get("heldout_eligibility"),
        "prior_tuning_packets": sample.get("prior_tuning_packets", []),
        "reviewed_precision_status": "available"
        if accepted_reports and all(r["status"] == "available" for r in accepted_reports)
        else "unavailable",
        "population_precision": None,
        "population_precision_note": "Stratum estimates describe this captured local cohort. Unequal sampling fractions must not be pooled into public or whole-population precision.",
        "strata": reports,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sample_parser = commands.add_parser("sample")
    sample_parser.add_argument("--state", required=True, type=Path)
    sample_parser.add_argument("--output", required=True, type=Path)
    sample_parser.add_argument("--seed", required=True)
    sample_parser.add_argument("--cohort", required=True)
    sample_parser.add_argument("--cutoff", required=True)
    sample_parser.add_argument("--per-stratum", type=int, default=10)
    sample_parser.add_argument("--evaluation-fraction", type=float, default=0.5)
    sample_parser.add_argument("--prior-tuning-packet", action="append", type=Path, default=[])
    report_parser = commands.add_parser("evaluate")
    report_parser.add_argument("--sample", required=True, type=Path)
    report_parser.add_argument("--reviews", required=True, type=Path)
    report_parser.add_argument("--output", required=True, type=Path)
    report_parser.add_argument("--split", choices=("evaluation", "tuning"), default="evaluation")
    args = parser.parse_args(argv)
    if args.command == "sample":
        from swingset.state.db import open_database

        from .evaluation_input import read_population

        with open_database(args.state, read_only=True, lock=False) as db:
            with db.transaction(immediate=False) as conn:
                rows, context = read_population(conn)
        result = draw_sample(
            rows,
            seed=args.seed,
            cohort=args.cohort,
            cutoff=args.cutoff,
            per_stratum=args.per_stratum,
            evaluation_fraction=args.evaluation_fraction,
            context=context,
            prior_tuning_packets=[
                json.loads(path.read_text()) for path in args.prior_tuning_packet
            ],
        )
    else:
        result = evaluate(
            json.loads(args.sample.read_text()),
            json.loads(args.reviews.read_text()),
            split=args.split,
        )
    # Never silently replace a review artifact; use a new explicit output path.
    with args.output.open("x") as stream:
        json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
