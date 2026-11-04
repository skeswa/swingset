"""Phase timing without requiring a database or a repository-local conftest."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict, deque
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

_TIMINGS = {"collection": 0.0, "setup": 0.0, "call": 0.0, "teardown": 0.0}
_COLLECTION_STARTED = 0.0


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--swingset-core-phase",
        choices=("prologue", "parallel"),
        help="Run the reviewed prologue or its disjoint concurrent remainder",
    )
    parser.addoption(
        "--swingset-partition",
        metavar="INDEX/COUNT",
        help="Run a zero-based deterministic file partition after normal test selection",
    )
    parser.addoption(
        "--timings",
        action="store_true",
        help="Report collection, setup, test-body and teardown timings",
    )


def _partition(config: pytest.Config) -> tuple[int, int] | None:
    value = config.getoption("swingset_partition")
    if value is None:
        return None
    try:
        index, count = (int(part) for part in value.split("/"))
    except (TypeError, ValueError):
        raise pytest.UsageError("--swingset-partition requires INDEX/COUNT") from None
    if count < 1 or not 0 <= index < count:
        raise pytest.UsageError("--swingset-partition requires 0 <= INDEX < COUNT")
    return index, count


def pytest_configure(config: pytest.Config) -> None:
    _partition(config)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> Generator[None, None, None]:
    # Wrap every selector, including the suite's stale/unclassified checks and
    # pytest's -k/-m filters. Partitioning must never hide those failures.
    yield
    partition = _partition(config)
    phase = config.getoption("swingset_core_phase")
    if partition is None and phase is None:
        return
    if (phase == "prologue" and partition is not None) or (
        phase == "parallel" and partition is None
    ):
        raise pytest.UsageError("The prologue is serial; parallel core requires a partition")
    index, count = partition or (0, 1)
    selected = [item.nodeid for item in items]
    from .core_partition import assigned_nodes, load_weights, phase_nodes

    try:
        candidates = phase_nodes(selected, phase) if phase else selected
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error

    weights = None
    weights_digest = None
    if weights_path := os.environ.get("SWINGSET_CORE_PARTITION_WEIGHTS"):
        try:
            weights, weights_digest = load_weights(Path(weights_path))
        except (OSError, ValueError) as error:
            raise pytest.UsageError(f"Invalid core partition weights: {error}") from error
    nodeids = (
        candidates if phase == "prologue" else assigned_nodes(candidates, index, count, weights)
    )
    available: dict[str, deque[pytest.Item]] = defaultdict(deque)
    for item in items:
        available[item.nodeid].append(item)
    assigned = [available[nodeid].popleft() for nodeid in nodeids]
    retained = {id(item) for item in assigned}
    excluded = [item for item in items if id(item) not in retained]
    if target := os.environ.get("SWINGSET_TEST_PARTITION_REPORT_DIR"):
        directory = Path(target)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        nodeids = [item.nodeid for item in assigned]
        worker = getattr(config, "workerinput", {}).get("workerid", "controller")
        report = {
            "format": 1,
            "index": index,
            "count": count,
            "worker": worker,
            "phase": phase,
            "selected": selected,
            "assigned": nodeids,
            "selected_count": len(selected),
            "assigned_count": len(nodeids),
            "selected_sha256": hashlib.sha256("\n".join(selected).encode()).hexdigest(),
            "assigned_sha256": hashlib.sha256("\n".join(nodeids).encode()).hexdigest(),
            "partition_policy": (
                "one-case-prologue-v1"
                if phase == "prologue"
                else ("measured-lpt-v1" if weights is not None else "sha256-files-v1")
            ),
            "weights_sha256": weights_digest,
        }
        path = directory / f"partition-{worker}.json"
        temporary = path.with_suffix(".tmp")
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(report, stream, sort_keys=True)
            stream.write("\n")
        temporary.replace(path)
    if excluded:
        config.hook.pytest_deselected(items=excluded)
    items[:] = assigned


def pytest_sessionstart(session: pytest.Session) -> None:
    global _COLLECTION_STARTED
    _COLLECTION_STARTED = time.monotonic()
    for phase in _TIMINGS:
        _TIMINGS[phase] = 0.0


def pytest_collection_finish(session: pytest.Session) -> None:
    _TIMINGS["collection"] = time.monotonic() - _COLLECTION_STARTED


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: Any, ids: list[str]) -> None:
    _TIMINGS["collection"] = max(_TIMINGS["collection"], time.monotonic() - _COLLECTION_STARTED)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # Xdist's failed worker-crash report uses "???" rather than a normal phase.
    # Preserve that report and its duration without masking the original crash.
    _TIMINGS[report.when] = _TIMINGS.get(report.when, 0.0) + report.duration


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    if not config.getoption("timings"):
        return
    if hasattr(config, "workerinput"):
        return
    timing = {phase: round(seconds, 4) for phase, seconds in _TIMINGS.items()}
    workers = getattr(config.option, "numprocesses", 0) or 0
    if workers:
        timing["workers"] = workers
        timing["parallel_phase_totals"] = True
    if started := os.environ.get("SWINGSET_TEST_STARTED"):
        timing["wall"] = round(time.monotonic() - float(started), 4)
    terminalreporter.write_line("Phase timings (seconds): " + json.dumps(timing, sort_keys=True))
    if target := os.environ.get("SWINGSET_TEST_TIMINGS_PATH"):
        Path(target).write_text(json.dumps(timing, sort_keys=True) + "\n")
