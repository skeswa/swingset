"""Compare bounded captured name-number pairs without running resolution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any

MAX_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 1024 * 1024
SUBJECTS = {
    "2025-07-midwest-westie-fest/novice-jj/F-916": 16984,
    "2026-02-rose-city-swing/novice-jj/F-312": 16984,
    "2026-05-soswing/newcomer-strictly/F-name-anna-koch": 13703,
    "2026-05-soswing/novice-jj/L-220": 10681,
    "2026-07-midwest-westie-fest/novice-jj/F-342": 16984,
}


def require(condition: Any) -> None:
    if not condition:
        raise ValueError("Numerical probe input identity or bounds failed")


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def invalid_constant(value: str) -> None:
    raise ValueError("Invalid metadata JSON constant")


def metadata(path: Path, expected: str) -> dict[str, Any]:
    require(re.fullmatch(r"[0-9a-f]{64}", expected))
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == expected)
    decoder = json.JSONDecoder(object_pairs_hook=unique_object, parse_constant=invalid_constant)
    text, position = raw.decode("utf-8"), 0
    documents: list[dict[str, Any]] = []
    while position < len(text):
        if text[position] in " \t\r\n":
            position += 1
            continue
        require(len(documents) < 4)
        value, position = decoder.raw_decode(text, position)
        require(isinstance(value, dict))
        documents.append(value)
    sections = [doc["section"] for doc in documents]
    require(all(doc["complete"] is True for doc in documents))
    if sections == ["identity", "dependencies", "input_metadata", "identity_final"]:
        value = dict(documents[2])
        require(
            value["scope"] == "three_dancers_five_exact_entry_ids_persisted_locator_subset_only"
        )
        require(len(value["subjects"]) == 5 and len(value["dancers"]) == 3)
        require(all(row.get("subject_kind", "entry") == "entry" for row in value["subjects"]))
        value["subjects"] = [dict(row, subject_kind="entry") for row in value["subjects"]]
        expected_pairs = {("entry", subject, dancer) for subject, dancer in SUBJECTS.items()}
    else:
        require(sections == ["identity", "input_metadata", "identity_final"])
        value = documents[1]["payload"]
        require(isinstance(value, dict))
        require(value["pair_set_exact"] is True and 0 < len(value["pairs"]) <= 256)
        require(
            all(
                type(value[name]) is int
                for name in ("requested_pairs", "requested_unique_subjects", "requested_dancers")
            )
        )
        require(
            isinstance(value["scope"], str) and re.fullmatch(r"[a-z0-9_]{1,256}", value["scope"])
        )
        expected_pairs = {
            (row["subject_kind"], row["subject_id"], row["candidate_wsdc_id"])
            for row in value["pairs"]
        }
        require(len(expected_pairs) == len(value["pairs"]) == value["requested_pairs"])
        require(
            value["requested_unique_subjects"]
            == len({(kind, subject) for kind, subject, _ in expected_pairs})
        )
        require(value["requested_dancers"] == len(value["dancers"]))
    require(0 < len(value["subjects"]) <= 256 and 0 < len(value["dancers"]) <= 256)
    actual = [
        (row["subject_kind"], row["requested_subject_id"], row["candidate_wsdc_id"])
        for row in value["subjects"]
    ]
    require(
        len(actual) == len(set(actual)) == len(expected_pairs) and set(actual) == expected_pairs
    )
    require(
        all(
            kind in {"entry", "judge"}
            and isinstance(subject, str)
            and type(dancer) is int
            and dancer > 0
            for kind, subject, dancer in actual
        )
    )
    dancer_ids = [row["requested_wsdc_id"] for row in value["dancers"]]
    require(all(type(identifier) is int and identifier > 0 for identifier in dancer_ids))
    require(
        len(dancer_ids) == len(set(dancer_ids)) and set(dancer_ids) == {pair[2] for pair in actual}
    )
    return dict(value)


def number(value: float) -> dict[str, str]:
    return {"repr": repr(value), "hex": value.hex()}


def backend_identity(function: Any) -> dict[str, str]:
    module = sys.modules.get(function.__module__)
    if module is None:
        raise ValueError("Numerical backend is not loaded")
    filename = module.__file__
    if filename is None:
        raise ValueError("Numerical backend has no source witness")
    path = Path(filename)
    return {
        "module": function.__module__,
        "name": function.__name__,
        "file": str(path),
        "file_sha256": file_hash(path),
    }


def backends() -> tuple[dict[str, tuple[Any, Any]], dict[str, str] | None]:
    from rapidfuzz.distance import JaroWinkler, metrics_py
    from rapidfuzz.fuzz import token_set_ratio
    from rapidfuzz.fuzz_py import token_set_ratio as python_token

    result = {
        "default": (JaroWinkler.normalized_similarity, token_set_ratio),
        "python": (metrics_py.jaro_winkler_normalized_similarity, python_token),
    }
    unavailable = None
    try:
        from rapidfuzz import fuzz_cpp  # type: ignore[attr-defined]
        from rapidfuzz.distance import metrics_cpp  # type: ignore[attr-defined]
    except ImportError:
        unavailable = {"backend": "cpp_generic", "error_type": "ImportError"}
    else:
        result["cpp_generic"] = (
            metrics_cpp.jaro_winkler_normalized_similarity,
            fuzz_cpp.token_set_ratio,
        )
    return result, unavailable


def run(path: Path, expected: str) -> dict[str, Any]:
    from rapidfuzz import __version__ as rapidfuzz_version

    from swingset.normalize import names

    captured = metadata(path, expected)
    implementations, unavailable = backends()
    dancers = {row["requested_wsdc_id"]: row for row in captured["dancers"]}
    pairs = []
    for subject in captured["subjects"]:
        dancer = dancers[subject["candidate_wsdc_id"]]
        require(
            subject["present"] is True
            and dancer["present"] is True
            and dancer["merged_into_wsdc_id"] is None
        )
        raw_dancer = dancer["first_name"] + " " + dancer["last_name"]
        require(
            len(subject["name_raw"].encode("utf-8")) <= 1024
            and len(raw_dancer.encode("utf-8")) <= 1024
        )
        left = names.normalize_name(subject["name_raw"]).value
        right = names.normalize_name(raw_dancer).value
        require(right == dancer["name_norm"])
        if subject.get("stored_name_norm_available") is True:
            require(left == subject["name_norm"])
        require(len(left.encode("utf-8")) <= 1024 and len(right.encode("utf-8")) <= 1024)
        scores = {}
        for label, (jaro, token) in implementations.items():
            j, t = jaro(left, right), token(left, right) / 100.0
            scores[label] = {
                "jaro_winkler": number(j),
                "token_set_similarity": number(t),
                "jaro_admitted": j >= 0.92,
                "token_admitted": t >= 0.90,
                "fuzzy_gate_admitted": j >= 0.92 or t >= 0.90,
            }
        pairs.append(
            {
                "subject_kind": subject["subject_kind"],
                "subject_id": subject["requested_subject_id"],
                "dancer_id": subject["candidate_wsdc_id"],
                "raw_subject": subject["name_raw"],
                "raw_dancer": raw_dancer,
                "normalized_subject": left,
                "normalized_dancer": right,
                "subject_role": subject["role"],
                "dancer_role": dancer["primary_role"],
                "merged_into": dancer["merged_into_wsdc_id"],
                "scores": scores,
            }
        )
    override = os.environ.get("RAPIDFUZZ_IMPLEMENTATION")
    return {
        "format": "swingset-pure-link-numerics-v1",
        "input_sha256": expected,
        "input_scope": captured["scope"],
        "command_sha256": file_hash(Path(__file__)),
        "normalization_sha256": file_hash(Path(names.__file__)),
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "rapidfuzz": rapidfuzz_version,
            "implementation_override": override if override in {None, "cpp", "python"} else "other",
        },
        "backends": {
            label: {"jaro": backend_identity(jaro), "token": backend_identity(token)}
            for label, (jaro, token) in implementations.items()
        },
        "unavailable": unavailable,
        "thresholds": {"jaro": number(0.92), "token_set": number(0.90)},
        "pairs": pairs,
        "limits": [
            "Pure normalized-name gates only; no candidate loading, nickname/source-ID gates, scoring, resolver, database or network.",
            "This runtime's numbers do not prove another runtime's backend or historical output.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.input, args.input_sha256)
        encoded = json.dumps(result, sort_keys=True)
        require(len(encoded.encode("utf-8")) + 1 <= MAX_REPORT_BYTES)
    except Exception as error:
        print(json.dumps({"complete": False, "error_type": type(error).__name__}, sort_keys=True))
        return 1
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
