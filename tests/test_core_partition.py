"""Measured scheduling preserves every selected case and whole-file affinity."""

import hashlib
import json

import pytest

from swingset.dev.core_partition import PROLOGUE_NODE, assigned_nodes, load_weights, phase_nodes


def test_execution_phases_keep_exactly_one_copy_of_every_selected_case():
    selected = ["tests/a.py::test_a", PROLOGUE_NODE, "tests/b.py::test_b"]
    serial = phase_nodes(selected, "prologue")
    parallel = phase_nodes(selected, "parallel")
    assert serial == [PROLOGUE_NODE]
    assert set(serial).isdisjoint(parallel)
    assert set(serial) | set(parallel) == set(selected)
    assert selected[1] == PROLOGUE_NODE


@pytest.mark.parametrize("copies", [0, 2])
def test_a_missing_or_duplicate_prologue_cannot_silently_omit_work(copies):
    with pytest.raises(ValueError, match="exactly once"):
        phase_nodes(["tests/a.py::test_a", *([PROLOGUE_NODE] * copies)], "parallel")


def test_lpt_starts_long_files_first_and_preserves_cases_within_each_file():
    nodes = [
        "tests/a.py::test_first[b]",
        "tests/b.py::test_second",
        "tests/a.py::test_first[a]",
        "tests/c.py::test_third",
        "tests/d.py::test_fourth",
    ]
    weights = {"tests/a.py": 10, "tests/b.py": 8, "tests/c.py": 4, "tests/d.py": 2}
    assert assigned_nodes(nodes, 0, 2, weights) == [nodes[0], nodes[2], nodes[4]]
    assert assigned_nodes(nodes, 1, 2, weights) == [nodes[1], nodes[3]]
    parts = [assigned_nodes(nodes, index, 2, weights) for index in range(2)]
    assert set(parts[0]).isdisjoint(parts[1])
    assert set(parts[0]) | set(parts[1]) == set(nodes)
    assert nodes[0:2] == ["tests/a.py::test_first[b]", "tests/b.py::test_second"]


def test_three_measured_bins_balance_new_files_without_omitting_old_or_new_cases():
    nodes = [f"tests/{name}.py::test_{case}" for name in "abcdef" for case in range(2)]
    # Unknown files use their selected test count; stale historic keys are ignored.
    weights = {"tests/a.py": 10, "tests/b.py": 8, "tests/c.py": 6, "tests/retired.py": 100}
    parts = [assigned_nodes(nodes, index, 3, weights) for index in range(3)]
    assert len({node for part in parts for node in part}) == len(nodes)
    assert sum(map(len, parts)) == len(nodes)
    assert [part[0].split("::")[0] for part in parts] == ["tests/a.py", "tests/b.py", "tests/c.py"]
    assert [len(part) for part in parts] == [2, 4, 6]


def test_hash_fallback_keeps_original_order_and_whole_files():
    nodes = ["tests/a.py::test_b", "tests/b.py::test_a", "tests/a.py::test_a"]
    parts = [assigned_nodes(nodes, index, 2) for index in range(2)]
    assert sum(map(len, parts)) == len(nodes)
    for index, part in enumerate(parts):
        assert part == [
            node
            for node in nodes
            if int.from_bytes(hashlib.sha256(node.split("::")[0].encode()).digest(), "big") % 2
            == index
        ]


@pytest.mark.parametrize("weight", [0, -1, True, "2", None, float("inf"), float("nan")])
def test_invalid_private_scheduling_weights_fail_closed(tmp_path, weight):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps({"format": 1, "weights": {"tests/a.py": weight}}))
    with pytest.raises(ValueError, match="finite positive"):
        load_weights(path)


def test_weights_digest_identifies_exact_scheduling_bytes(tmp_path):
    path = tmp_path / "weights.json"
    body = b'{"format":1,"weights":{"tests/a.py":2.5}}\n'
    path.write_bytes(body)
    weights, checksum = load_weights(path)
    assert weights == {"tests/a.py": 2.5}
    assert checksum == hashlib.sha256(body).hexdigest()
