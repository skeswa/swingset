"""Record controller outcomes for a complete partition, without import side effects."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from . import fixture_observer, restore_observer

_PHASES: dict[str, dict[str, float]] = {}
_MONOTONIC_NS = time.monotonic_ns
_THREAD_CPU_NS = time.thread_time_ns
_THREAD_ID = threading.get_ident
_PROPERTY = "swingset_phase_observation"
_LIMIT = 65536
_PENDING = pytest.StashKey[dict[str, dict[str, Any]]]()
_STATE = pytest.StashKey[Any]()
_RESTORE = pytest.StashKey[dict[str, Any]]()
_FIXTURE = pytest.StashKey[dict[str, Any]]()
_FIELDS = {
    "phase",
    "worker",
    "start_ns",
    "end_ns",
    "owner_thread_cpu_ns",
    "owner_thread_id",
    "owner_thread_unchanged",
}


def _worker(config: Any) -> str:
    return getattr(config, "workerinput", {}).get("workerid", "controller")  # type: ignore[no-any-return]


@contextmanager
def _observe(item: pytest.Item, phase: str) -> Iterator[None]:
    record: dict[str, Any] = {"phase": phase, "worker": _worker(item.config)}
    try:
        start, cpu, owner = _MONOTONIC_NS(), _THREAD_CPU_NS(), _THREAD_ID()
    except Exception:
        record["clock_error"] = True
    try:
        yield
    finally:
        if "clock_error" not in record:
            try:
                # CPU's interval lies inside the wall interval. Neither clock
                # resolves through time.* after tests begin patching clocks.
                used, end = _THREAD_CPU_NS() - cpu, _MONOTONIC_NS()
                record.update(
                    start_ns=start,
                    end_ns=end,
                    owner_thread_cpu_ns=used,
                    owner_thread_id=owner,
                    owner_thread_unchanged=_THREAD_ID() == owner,
                )
            except Exception:
                record["clock_error"] = True
        item.stash.setdefault(_PENDING, {})[phase] = record


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, Any, Any]:
    with _observe(item, "setup"):
        if fixture_observer.matches(item.nodeid):
            with fixture_observer.observe(_worker(item.config)) as value:
                try:
                    return (yield)
                finally:
                    item.stash[_FIXTURE] = value
        return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, Any]:
    with _observe(item, "call"):
        if restore_observer.matches(item.nodeid):
            with restore_observer.observe(
                _worker(item.config), item.nodeid, getattr(item, "module", None)
            ) as value:
                try:
                    return (yield)
                finally:
                    item.stash[_RESTORE] = value
        return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, Any, Any]:
    with _observe(item, "teardown"):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[Any]
) -> Generator[None, Any, Any]:
    report = yield
    if record := item.stash.get(_PENDING, {}).pop(call.when, None):
        # Public report properties survive pytest's actual xdist serializer.
        # Do not serialize an Item, Config, callable, SQL or test arguments.
        report.user_properties.append((_PROPERTY, record))
    if call.when == "setup" and _FIXTURE in item.stash:
        report.user_properties.append((fixture_observer.PROPERTY, item.stash[_FIXTURE]))
        del item.stash[_FIXTURE]
    if call.when == "call" and _RESTORE in item.stash:
        report.user_properties.append((restore_observer.PROPERTY, item.stash[_RESTORE]))
        del item.stash[_RESTORE]
    return report


class _Recorder:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.records: list[dict[str, Any]] = []
        self.unobserved: list[dict[str, str]] = []
        self.errors: set[str] = set()
        self.dropped = 0
        self.restore = restore_observer.Collector()
        self.fixture = fixture_observer.Collector()

    def _unobserved(self, report: Any, reason: str) -> None:
        self.errors.add(reason)
        if len(self.unobserved) < _LIMIT:
            self.unobserved.append(
                {
                    "nodeid": report.nodeid,
                    "phase": report.when,
                    "worker": getattr(report, "worker_id", _worker(self.config)),
                }
            )
        else:
            self.dropped += 1
            self.errors.add("observation_limit")

    def pytest_runtest_logreport(self, report: Any) -> None:
        self.restore.accept(report, getattr(report, "worker_id", _worker(self.config)))
        self.fixture.accept(report, getattr(report, "worker_id", _worker(self.config)))
        values = [value for name, value in report.user_properties if name == _PROPERTY]
        if len(values) != 1:
            self._unobserved(report, "missing_or_duplicate_observation")
            return
        value = values[0]
        if not isinstance(value, dict) or set(value) != _FIELDS:
            self._unobserved(report, "invalid_observation_schema")
            return
        integers = ("start_ns", "end_ns", "owner_thread_cpu_ns", "owner_thread_id")
        if (
            value["phase"] not in {"setup", "call", "teardown"}
            or value["phase"] != report.when
            or not isinstance(value["worker"], str)
            or not re.fullmatch(r"controller|gw[0-9]{1,4}", value["worker"])
            or any(type(value[key]) is not int for key in integers)
            or type(value["owner_thread_unchanged"]) is not bool
        ):
            self._unobserved(report, "invalid_observation_values")
            return
        expected = getattr(report, "worker_id", _worker(self.config))
        if value["worker"] != expected:
            self.errors.add("executing_worker_mismatch")
        wall = value["end_ns"] - value["start_ns"]
        if wall < 0 or value["owner_thread_cpu_ns"] < 0:
            self.errors.add("negative_duration")
        if value["owner_thread_cpu_ns"] > wall:
            self.errors.add("thread_cpu_exceeds_wall")
        if not value["owner_thread_unchanged"]:
            self.errors.add("owner_thread_changed")
        if len(self.records) >= _LIMIT:
            self.dropped += 1
            self.errors.add("observation_limit")
        else:
            self.records.append({"nodeid": report.nodeid, **value})

    def report(self) -> dict[str, Any]:
        previous: dict[str, int] = {}
        for value in sorted(
            self.records, key=lambda row: (row["worker"], row["start_ns"], row["end_ns"])
        ):
            if value["start_ns"] < previous.get(value["worker"], value["start_ns"]):
                self.errors.add("overlapping_worker_phases")
            previous[value["worker"]] = max(previous.get(value["worker"], 0), value["end_ns"])
        return {
            "format": 1,
            "quality": not self.errors,
            "errors": sorted(self.errors),
            "dropped": self.dropped,
            "limit": _LIMIT,
            "records": self.records,
            "unobserved": self.unobserved,
            "clocks": "native monotonic_ns/thread_time_ns captured at plugin import",
            "scope": "Executing hook thread only; worker IDs are local to this client.",
            "limitations": [
                "Includes observer overhead; not a normal timing proof.",
                "Wall minus owner-thread CPU does not identify a wait cause.",
                "Does not measure other threads, other workers or server CPU.",
            ],
        }


def pytest_configure(config: pytest.Config) -> None:
    recorder = _Recorder(config)
    config.stash[_STATE] = recorder
    config.pluginmanager.register(recorder, "swingset-core-phase-observations")


def pytest_sessionstart(session: Any) -> None:
    _PHASES.clear()


def pytest_runtest_logreport(report: Any) -> None:
    phases = _PHASES.setdefault(report.nodeid, {})
    phases[report.when] = phases.get(report.when, 0.0) + report.duration


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if hasattr(session.config, "workerinput"):
        return
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    keys = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")
    counts = {key: len(reporter.stats.get(key, [])) for key in keys}
    executed = sorted({report.nodeid for key in keys for report in reporter.stats.get(key, [])})
    path = Path("/tmp/core-results.json")
    with path.open("w") as stream:
        json.dump(
            {
                "exit_code": int(exitstatus),
                "counts": counts,
                "executed": executed,
                "node_phases": _PHASES,
                "phase_observations": session.config.stash[_STATE].report(),
                "restore_call_observations": session.config.stash[_STATE].restore.report(),
                "publication_fixture_observation": session.config.stash[_STATE].fixture.report(),
            },
            stream,
            sort_keys=True,
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
