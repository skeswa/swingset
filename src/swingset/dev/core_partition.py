"""Deterministic whole-file assignment and ordering from measured test phases."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

PROLOGUE_NODE = (
    "tests/test_write_deadline.py::"
    "test_timed_out_derivation_backs_off_while_independent_output_commits"
)


def phase_nodes(nodeids: Sequence[str], phase: str) -> list[str]:
    """Schedule one unchanged latency case before concurrent database work."""
    if nodeids.count(PROLOGUE_NODE) != 1:
        raise ValueError("The complete core must contain its prologue case exactly once")
    if phase == "prologue":
        return [PROLOGUE_NODE]
    if phase == "parallel":
        return [node for node in nodeids if node != PROLOGUE_NODE]
    raise ValueError("Unknown core execution phase")


def _duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Core file weights must be finite positive durations")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError("Core file weights must be finite positive durations") from error
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Core file weights must be finite positive durations")
    return result


def load_weights(path: Path) -> tuple[dict[str, float], str]:
    """Read one scheduling input; reject non-finite or nonpositive durations."""
    body = path.read_bytes()
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid core file weights JSON") from error
    if (
        not isinstance(value, dict)
        or value.get("format") != 1
        or not isinstance(value.get("weights"), dict)
    ):
        raise ValueError("Core file weights require format 1 and a weights mapping")
    result = {}
    for name, weight in value["weights"].items():
        if not isinstance(name, str) or not name:
            raise ValueError("Core file weight names must be nonempty paths")
        result[name] = _duration(weight)
    return result, hashlib.sha256(body).hexdigest()


def assigned_nodes(
    nodeids: Sequence[str],
    index: int,
    count: int,
    weights: Mapping[str, float] | None = None,
) -> list[str]:
    """Assign each file once and preserve the order of its individual cases."""
    if count < 1 or not 0 <= index < count:
        raise ValueError("Core partition requires 0 <= index < count")
    if weights is None:
        return [
            node
            for node in nodeids
            if int.from_bytes(hashlib.sha256(node.split("::", 1)[0].encode()).digest(), "big")
            % count
            == index
        ]
    groups: dict[str, list[str]] = {}
    for node in nodeids:
        groups.setdefault(node.split("::", 1)[0], []).append(node)
    measured = {
        name: _duration(weights.get(name, float(len(nodes)))) for name, nodes in groups.items()
    }
    loads = [0.0] * count
    result = []
    for name in sorted(groups, key=lambda name: (-measured[name], name)):
        owner = min(range(count), key=lambda number: (loads[number], number))
        loads[owner] += measured[name]
        if owner == index:
            result.extend(groups[name])
    return result
