"""Prepare one allowlisted, bounded local CPU or driver-wait diagnostic."""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import fcntl
import hashlib
import inspect
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import types
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from . import core_client, core_images, core_runner, physical, restore_observer, services

NODE = "tests/test_scoring_pipeline.py::test_archived_scoringdance_event_runs_end_to_end"
CASES = {
    "scoring": NODE,
    "uncertain-publication": "tests/publish/test_publication_controls.py::test_uncertain_landed_request_recovers_despite_pause_and_new_journal",
    "unlanded-publication": "tests/publish/test_safety.py::test_unlanded_old_intent_is_rejected_then_new_build_can_publish",
    "publication-fixture": "tests/build/test_release_paths.py::test_a_changed_artifact_file_refuses_publication",
    "failed-build": "tests/build/test_completion_validation.py::test_failed_build_attempt_retains_no_closure_proof",
    "checkpoint": "tests/backup/test_checkpoint.py::test_restore_public_verification_keeps_activation_explicit",
    "sql-guard": "tests/test_output_packs.py::test_only_allowlisted_modules_name_the_row_tables_in_sql",
}
CASE_PHASES = {case: "setup" if case == "publication-fixture" else "call" for case in CASES}
MODES = ("cpu", "waits")
CASE_ENV = "SWINGSET_DIAGNOSTIC_CASE"
MODE_ENV = "SWINGSET_DIAGNOSTIC_MODE"
PHASE_ENV = "SWINGSET_DIAGNOSTIC_PHASE"
PROFILE_ENV = "SWINGSET_DIAGNOSTIC_CPU"
PROFILE_FLAGS = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
MEASUREMENT_SECONDS = 30
CLEANUP_SECONDS = 120
MAX_PROFILE_ENTRIES = 20000
LIMITS = {
    "measurement_seconds": MEASUREMENT_SECONDS,
    "cleanup_seconds": CLEANUP_SECONDS,
    "client_memory_bytes": core_runner.MEMORY,
    "client_memory_plus_swap_bytes": core_runner.MEMORY,
    "client_cpus": core_runner.CLIENT_CPUS,
}
_CALLBACKS = {
    "_pystart_callback": ("PY_START", "PY_RESUME", "PY_THROW"),
    "_pyreturn_callback": ("PY_RETURN", "PY_YIELD", "PY_UNWIND"),
    "_ccall_callback": ("CALL",),
    "_creturn_callback": ("C_RETURN", "C_RAISE"),
}


class _OwnerProfile(cProfile.Profile):
    """CPython 3.12 monitoring is global; its thread CPU clock must not be.

    Measured reproduction: scratch thread-profile analysis 551b7239. The four
    private callbacks match CPython's versioned Modules/_lsprof.c interface.
    """

    def __init__(self) -> None:
        super().__init__(timer=time.thread_time)
        self.owner = threading.get_ident()
        base = super()
        self.callbacks = {name: getattr(base, name) for name in _CALLBACKS}
        if any(
            not isinstance(callback, types.BuiltinMethodType)
            or getattr(callback, "__self__", None) is not self
            or callback.__name__ != name
            for name, callback in self.callbacks.items()
        ):
            raise RuntimeError("CPU profiler callback identity is unsupported")

    def _dispatch(self, name: str, args: tuple[Any, ...]) -> Any:
        if threading.get_ident() == self.owner:
            return self.callbacks[name](*args)
        return None

    def _pystart_callback(self, *args: Any) -> Any:
        return self._dispatch("_pystart_callback", args)

    def _pyreturn_callback(self, *args: Any) -> Any:
        return self._dispatch("_pyreturn_callback", args)

    def _ccall_callback(self, *args: Any) -> Any:
        return self._dispatch("_ccall_callback", args)

    def _creturn_callback(self, *args: Any) -> Any:
        return self._dispatch("_creturn_callback", args)


@contextlib.contextmanager
def _owner_profile() -> Iterator[_OwnerProfile]:
    monitoring = getattr(sys, "monitoring", None)
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 12)
        or monitoring is None
        or not all(hasattr(cProfile.Profile, name) for name in _CALLBACKS)
    ):
        raise RuntimeError("CPU profiling requires the supported CPython 3.12 callbacks")
    tool = monitoring.PROFILER_ID
    if monitoring.get_tool(tool) is not None or monitoring.get_events(tool):
        raise RuntimeError("Another profiler owns the monitoring state")
    events = {getattr(monitoring.events, event) for group in _CALLBACKS.values() for event in group}
    previous = {}
    # A free tool can retain callbacks. Snapshot them under an exclusive tool
    # reservation, and restore them even when cProfile.enable partially fails.
    monitoring.use_tool_id(tool, "swingset-cpu-admission")
    try:
        for event in events:
            previous[event] = monitoring.register_callback(tool, event, None)
    finally:
        try:
            for event, callback in previous.items():
                monitoring.register_callback(tool, event, callback)
        finally:
            monitoring.free_tool_id(tool)
    profile = _OwnerProfile()
    enabled = False
    try:
        profile.enable(builtins=False)
        enabled = True
        python_events = 0
        for name in ("_pystart_callback", "_pyreturn_callback"):
            for event in _CALLBACKS[name]:
                python_events |= getattr(monitoring.events, event)
        # builtins=False alone still dispatches the C-event Python wrappers.
        # Disable their events too, before any measured body work begins.
        monitoring.set_events(tool, python_events)
        yield profile
    finally:
        try:
            if enabled:
                profile.disable()
            elif monitoring.get_tool(tool) == "cProfile":
                # A failed enable may already have registered some callbacks. Only
                # remove callbacks bound to this exact instance, never a peer's.
                for event in events:
                    old = monitoring.register_callback(tool, event, None)
                    if (
                        old is not None
                        and old is not previous[event]
                        and getattr(old, "__self__", None) is not profile
                    ):
                        monitoring.register_callback(tool, event, old)
                        raise RuntimeError("CPU monitoring ownership changed")
                profile.disable()
                if monitoring.get_tool(tool) == "cProfile":
                    monitoring.set_events(tool, 0)
                    monitoring.free_tool_id(tool)
                profile.clear()
        finally:
            if monitoring.get_tool(tool) is not None or monitoring.get_events(tool):
                raise RuntimeError("CPU monitoring ownership changed")
            monitoring.use_tool_id(tool, "swingset-cpu-restore")
            try:
                for event, callback in previous.items():
                    monitoring.register_callback(tool, event, callback)
            finally:
                monitoring.free_tool_id(tool)


def cpu_report(profile: Any, *, wall: float, cpu: float) -> dict[str, Any]:
    """Rank only non-generator self CPU; never sum overlapping cumulative costs."""
    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    invalid = 0
    valid_clock = math.isfinite(cpu) and math.isfinite(wall) and min(wall, cpu) >= 0
    # Bound conversion/enable-disable rounding to at most one millisecond;
    # this does not excuse negative self times or cross-thread clock offsets.
    tolerance = min(0.001, max(0.00001, cpu * 0.0001)) if valid_clock else 0.0
    entries = profile.getstats()
    for entry in entries[:MAX_PROFILE_ENTRIES]:
        code = entry.code
        suspended = isinstance(code, types.CodeType) and bool(code.co_flags & PROFILE_FLAGS)
        location = (
            f"{code.co_filename}:{code.co_firstlineno}:{code.co_name}"
            if isinstance(code, types.CodeType)
            else str(code)
        )
        value = float(entry.inlinetime)
        if not math.isfinite(value) or value < 0:
            invalid += 1
            excluded.append({"function": location, "reason": "invalid self time"})
        elif suspended:
            excluded.append({"function": location, "reason": "generator/coroutine suspension"})
        elif valid_clock and value > cpu + tolerance:
            invalid += 1
            excluded.append({"function": location, "reason": "self time exceeds owner CPU"})
        else:
            rows.append({"function": location, "calls": entry.callcount, "self_cpu_seconds": value})
    return {
        "format": "swingset-cpu-self-v1",
        "clock": "time.thread_time",
        "profile_scope": "python_frames_only",
        "body_wall_seconds": wall,
        "body_thread_cpu_seconds": cpu,
        "python_version": sys.version,
        "thread_scope": "enabling thread only",
        "accounting_tolerance_seconds": tolerance,
        "functions": sorted(rows, key=lambda row: (-row["self_cpu_seconds"], row["function"])),
        "excluded": excluded,
        "entries_seen": len(entries),
        "entries_omitted": max(0, len(entries) - MAX_PROFILE_ENTRIES),
        "quality_pass": len(entries) <= MAX_PROFILE_ENTRIES
        and not invalid
        and valid_clock
        and sum(row["self_cpu_seconds"] for row in rows) <= cpu + tolerance,
        "normal_timing_claimed": False,
        "accounting": "Owner-thread Python-frame self/inlinetime only; native work on that thread is charged to its containing Python frame, without direct C-function ranking. Worker-thread CPU and generator/coroutine suspension entries are excluded. Cumulative times overlap and are not exported or summed. Profiler overhead is included in body clocks; no normal timing or attributed savings claim.",
    }


def _profile_body() -> Iterator[dict[str, Any]]:
    result: dict[str, Any] = {}
    wall, cpu = time.monotonic(), time.thread_time()
    profile = None
    try:
        with _owner_profile() as admitted:
            profile = admitted
            yield result
    finally:
        if profile is not None:
            result.update(
                cpu_report(profile, wall=time.monotonic() - wall, cpu=time.thread_time() - cpu)
            )
            profile.dump_stats("/tmp/diagnostic-cpu.pstats")


def selection(case: str, mode: str) -> str:
    """Only named cases are admitted; a plan cannot smuggle a pytest selector."""
    if case not in CASES or mode not in MODES or (case == "sql-guard" and mode != "cpu"):
        raise ValueError("Unsupported diagnostic case or mode")
    return CASES[case]


def _phase(case: str, supplied: Any) -> str:
    # Older call-only packets have no phase field. Setup is always explicit.
    expected = CASE_PHASES[case]
    if ("call" if supplied is None else supplied) != expected:
        raise ValueError("Diagnostic phase does not match its named case")
    return expected


def wait_quality(report: Mapping[str, Any]) -> bool:
    clocks: list[float] = []
    for key in (
        "body_wall_seconds",
        "body_thread_cpu_seconds",
        "owner_wait_wall_seconds",
        "owner_wait_thread_cpu_seconds",
    ):
        value = report.get(key)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
        ):
            return False
        clocks.append(float(value))
    wall, cpu, wait_wall, wait_cpu = clocks
    counts: list[int] = []
    for key in (
        "owner_wait_calls",
        "owner_wait_exceptions",
        "nested_owner_wait_calls",
        "foreign_thread_wait_calls",
    ):
        value = report.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
        counts.append(value)
    return bool(
        report.get("format") == "swingset-owner-driver-waits-v1"
        and report.get("thread_scope")
        == "body owner thread; outermost psycopg.Connection.wait only"
        and report.get("driver_wrapper_restored") is True
        and type(report.get("invalid_clock_samples")) is int
        and report.get("invalid_clock_samples") == 0
        and report.get("normal_timing_claimed") is False
        and counts[1] <= counts[0]
        and wait_wall <= wall + 0.001
        and wait_cpu <= cpu + 0.001
        and wait_cpu <= wait_wall + 0.001
        and cpu <= wall + 0.001
    )


class _OwnerWaits:
    """Count driver waits without retaining any generator, SQL or arguments."""

    def __init__(self) -> None:
        self.owner = threading.get_ident()
        self.depth = 0
        self.calls = 0
        self.exceptions = 0
        self.nested = 0
        self.foreign = 0
        self.foreign_lock = threading.Lock()
        self.wall = 0.0
        self.cpu = 0.0
        self.invalid_clock_samples = 0

    def invoke(
        self, original: Any, connection: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        if threading.get_ident() != self.owner:
            with self.foreign_lock:
                self.foreign += 1
            return original(connection, *args, **kwargs)
        if self.depth:
            self.nested += 1
            return original(connection, *args, **kwargs)
        self.calls += 1
        wall, cpu = time.monotonic(), time.thread_time()
        self.depth = 1
        try:
            return original(connection, *args, **kwargs)
        except BaseException:
            self.exceptions += 1
            raise
        finally:
            try:
                elapsed_wall = time.monotonic() - wall
                elapsed_cpu = time.thread_time() - cpu
                if not all(
                    math.isfinite(value) and value >= 0 for value in (elapsed_wall, elapsed_cpu)
                ):
                    self.invalid_clock_samples += 1
                self.wall += elapsed_wall
                self.cpu += elapsed_cpu
            finally:
                self.depth = 0

    def report(self, *, wall: float, cpu: float, restored: bool) -> dict[str, Any]:
        result = {
            "format": "swingset-owner-driver-waits-v1",
            "thread_scope": "body owner thread; outermost psycopg.Connection.wait only",
            "clock": "time.monotonic and time.thread_time",
            "body_wall_seconds": wall,
            "body_thread_cpu_seconds": cpu,
            "owner_wait_wall_seconds": self.wall,
            "owner_wait_thread_cpu_seconds": self.cpu,
            "owner_wait_calls": self.calls,
            "owner_wait_exceptions": self.exceptions,
            "nested_owner_wait_calls": self.nested,
            "foreign_thread_wait_calls": self.foreign,
            "driver_wrapper_restored": restored,
            "invalid_clock_samples": self.invalid_clock_samples,
            "accounting_tolerance_seconds": 0.001,
            "normal_timing_claimed": False,
            "accounting": "Only outermost owner-thread Connection.wait elapsed and thread CPU. Foreign-thread calls are counted without reading clocks. No SQL, generator or arguments retained. Driver wait elapsed includes client CPU and scheduling/socket waits; it is not database server CPU or all worker waiting. No optimization or normal timing claim.",
        }
        result["quality_pass"] = wait_quality(result)
        return result


@contextlib.contextmanager
def _owner_waits(connection_type: Any) -> Iterator[_OwnerWaits]:
    original = connection_type.wait
    tracking = _OwnerWaits()

    def wrapped(connection: Any, *args: Any, **kwargs: Any) -> Any:
        return tracking.invoke(original, connection, args, kwargs)

    connection_type.wait = wrapped
    try:
        yield tracking
    finally:
        if connection_type.wait is not wrapped:
            raise RuntimeError("Driver wait wrapper ownership changed")
        connection_type.wait = original


@contextlib.contextmanager
def _wait_body() -> Iterator[dict[str, Any]]:
    from psycopg import Connection

    result: dict[str, Any] = {}
    wall, cpu = time.monotonic(), time.thread_time()
    original = Connection.wait
    tracking = None
    try:
        with _owner_waits(Connection) as admitted:
            tracking = admitted
            yield result
    finally:
        if tracking is not None:
            result.update(
                tracking.report(
                    wall=time.monotonic() - wall,
                    cpu=time.thread_time() - cpu,
                    restored=Connection.wait is original,
                )
            )


# This exact case uses aggregate owner clocks, not a whole-body CPU profiler.
_GUARD_WALL = time.monotonic_ns
_GUARD_CPU = time.thread_time_ns
_GUARD_THREAD = threading.get_ident
_GUARD_LIMIT = 65536
_GUARD_BUCKETS = ("global_parse", "partition_parse", "global_naming", "function_naming")


def _guard_bindings() -> tuple[Any, Any, Any, types.CodeType]:
    module = sys.modules.get("test_output_packs")
    test = getattr(module, "test_only_allowlisted_modules_name_the_row_tables_in_sql", None)
    naming = getattr(module, "_naming", None)
    docs = getattr(module, "_docstrings", None)
    if (
        not inspect.ismodule(module)
        or not inspect.isfunction(test)
        or not inspect.isfunction(naming)
        or not inspect.isfunction(docs)
    ):
        raise RuntimeError("SQL guard binding is not the original regular module function")
    if any(
        not inspect.isfunction(function)
        or function.__globals__ is not vars(module)
        or function.__code__.co_flags & PROFILE_FLAGS
        for function in (test, naming, docs)
    ):
        raise RuntimeError("SQL guard binding is not the original regular module function")
    code = test.__code__
    parser = getattr(module, "ast", None)
    if (
        not code.co_filename.endswith("/tests/test_output_packs.py")
        or any(function.__code__.co_filename != code.co_filename for function in (naming, docs))
        or sys.version_info[:2] != (3, 12)
        or not inspect.ismodule(parser)
        or parser.__name__ != "ast"
        or not inspect.isfunction(parser.parse)
    ):
        raise RuntimeError("SQL guard original Python 3.12 caller binding changed")
    return module, parser, naming, code


def guard_quality(value: Mapping[str, Any]) -> bool:
    """Recheck clocks, counts and completion instead of trusting a quality flag."""
    if (
        value.get("format") != "swingset-sql-guard-owner-v1"
        or value.get("errors") != []
        or any(
            value.get(key) is not True for key in ("completed", "hooks_restored", "owner_unchanged")
        )
        or any(
            type(value.get(key)) is not int or value[key] != 0
            for key in ("foreign_calls", "other_calls", "nested_calls")
        )
    ):
        return False
    buckets = value.get("buckets")
    if not isinstance(buckets, Mapping) or set(buckets) != set(_GUARD_BUCKETS):
        return False
    spans: list[Mapping[str, Any]] = []
    for key in ("body", "initial_scan_to_partition_parse", "function_selection_and_assertion"):
        span = value.get(key)
        if not isinstance(span, Mapping):
            return False
        spans.append(span)
    timing = {"wall_ns", "owner_thread_cpu_ns"}
    for row in [*buckets.values(), *spans]:
        if not isinstance(row, Mapping) or not timing <= row.keys():
            return False
        if any(
            type(row[key]) is not int or not 0 <= row[key] <= MEASUREMENT_SECONDS * 1_000_000_000
            for key in timing
        ):
            return False
        if row["owner_thread_cpu_ns"] > row["wall_ns"]:
            return False
    for row in buckets.values():
        if set(row) != timing | {"calls", "normal_returns", "exceptions"}:
            return False
        if any(
            type(row.get(key)) is not int or not 0 <= row[key] <= _GUARD_LIMIT
            for key in ("calls", "normal_returns", "exceptions")
        ):
            return False
        if row["calls"] != row["normal_returns"] or row["exceptions"]:
            return False
    if (
        not 0 < buckets["global_parse"]["calls"] == buckets["global_naming"]["calls"]
        or buckets["partition_parse"]["calls"] != 1
        or buckets["function_naming"]["calls"] < 1
        or sum(row["calls"] for row in buckets.values()) > _GUARD_LIMIT
    ):
        return False
    body, initial, selection = spans
    for key in timing:
        if (
            buckets["global_parse"][key] + buckets["global_naming"][key] > initial[key]
            or buckets["function_naming"][key] > selection[key]
            or initial[key] + buckets["partition_parse"][key] + selection[key] > body[key]
        ):
            return False
    return True


@contextlib.contextmanager
def _guard_body() -> Iterator[dict[str, Any]]:
    module, parser, naming, caller = _guard_bindings()
    original_parse = parser.parse
    owner = _GUARD_THREAD()
    if type(owner) is not int or owner <= 0:
        raise RuntimeError("SQL guard owner thread is unknown")
    result: dict[str, Any] = {
        "format": "swingset-sql-guard-owner-v1",
        "completed": False,
        "hooks_restored": False,
        "owner_unchanged": False,
        "foreign_calls": 0,
        "other_calls": 0,
        "nested_calls": 0,
        "buckets": {
            label: dict.fromkeys(
                ("calls", "normal_returns", "exceptions", "wall_ns", "owner_thread_cpu_ns"), 0
            )
            for label in _GUARD_BUCKETS
        },
        "scope": "Exact original call phase, owner only. Initial span includes the partition source read before its parse; final span includes the unchanged final assertion. Naming/parse children are inclusive and overlap parents. No pure membership, child/server CPU, normal timing or savings claim. No AST/value/path is retained; no per-node hooks or extra traversals.",
    }
    errors: set[str] = set()
    active = busy = False
    calls = 0
    split_start: tuple[int, int] | None = None
    split_end: tuple[int, int] | None = None

    def stamp(*, finish: bool = False) -> tuple[int, int] | None:
        try:
            if finish:
                cpu, wall = _GUARD_CPU(), _GUARD_WALL()
            else:
                wall, cpu = _GUARD_WALL(), _GUARD_CPU()
            if type(wall) is not int or type(cpu) is not int or min(wall, cpu) < 0:
                raise ValueError("invalid clock")
            return wall, cpu
        except Exception:
            errors.add("clock_error")
            return None

    def elapsed(start: tuple[int, int] | None, end: tuple[int, int] | None) -> dict[str, int]:
        if start is None or end is None:
            errors.add("clock_error")
            return {"wall_ns": 0, "owner_thread_cpu_ns": 0}
        wall, cpu = end[0] - start[0], end[1] - start[1]
        if min(wall, cpu) < 0 or cpu > wall:
            errors.add("invalid_clock")
        return {"wall_ns": wall, "owner_thread_cpu_ns": cpu}

    def invoke(label: str, original: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        nonlocal busy, calls, split_start, split_end
        calls += 1
        if calls > _GUARD_LIMIT:
            errors.add("count_limit")
            return original(*args, **kwargs)
        if busy:
            result["nested_calls"] += 1
            errors.add("nested_call")
            return original(*args, **kwargs)
        if label == "partition_parse":
            if split_start is not None:
                errors.add("duplicate_partition_parse")
            # Finish the prior phase before starting the nested parse bucket.
            # Reusing a START pair here leaves CPU outside the wall interval.
            split_start = stamp(finish=True)
        before = stamp()
        row = result["buckets"][label]
        row["calls"] += 1
        busy = True
        primary: BaseException | None = None
        try:
            value = original(*args, **kwargs)
        except BaseException as error:
            primary = error
            row["exceptions"] += 1
            raise
        else:
            row["normal_returns"] += 1
            return value
        finally:
            after = None
            try:
                after = stamp(finish=True)
                if label == "partition_parse":
                    # The next phase needs its own START pair after this FINISH
                    # pair. The observer-only gap belongs to neither phase.
                    split_end = stamp()
            except BaseException:
                errors.add("clock_error")
                # The original helper failure wins; otherwise unwind to the
                # outer context, whose finally restores both hooks first.
                if primary is None:
                    raise
            finally:
                busy = False
            for key, used in elapsed(before, after).items():
                row[key] += used

    def admitted() -> bool:
        if not active:
            return False
        try:
            thread = _GUARD_THREAD()
            if type(thread) is not int or thread <= 0:
                raise ValueError("invalid thread identity")
        except Exception:
            errors.add("thread_error")
            return False
        if thread != owner:
            result["foreign_calls"] += 1
            return False
        return True

    def parse(*args: Any, **kwargs: Any) -> Any:
        if admitted():
            identity = sys._getframe(1).f_code
            if identity is caller and len(args) == 1 and set(kwargs) in ({"filename"}, set()):
                label = "global_parse" if kwargs else "partition_parse"
                return invoke(label, original_parse, args, kwargs)
            result["other_calls"] += 1
        return original_parse(*args, **kwargs)

    def named(*args: Any, **kwargs: Any) -> Any:
        if admitted():
            identity = sys._getframe(1).f_code
            if (
                identity is caller
                and len(args) == 1
                and not kwargs
                and isinstance(args[0], (parser.Module, parser.FunctionDef))
            ):
                return invoke(
                    "global_naming" if isinstance(args[0], parser.Module) else "function_naming",
                    naming,
                    args,
                    kwargs,
                )
            result["other_calls"] += 1
        return naming(*args, **kwargs)

    before = after = None
    primary_error: BaseException | None = None
    observer_fatal: BaseException | None = None
    try:
        parser.parse = parse
        module._naming = named
        before = stamp()
        active = True
        try:
            yield result
        except BaseException:
            errors.add("case_exception")
            raise
        else:
            result["completed"] = True
        finally:
            active = False
            try:
                after = stamp(finish=True)
            except BaseException as error:
                errors.add("clock_error")
                observer_fatal = error
    except BaseException as error:
        primary_error = error
        raise
    finally:
        active = False
        # Restoration also covers interrupted clock reads and partial installation.
        owned = parser.parse is parse and module._naming is named
        if parser.parse is parse:
            parser.parse = original_parse
        if module._naming is named:
            module._naming = naming
        result["hooks_restored"] = (
            owned and parser.parse is original_parse and module._naming is naming
        )
        if not result["hooks_restored"]:
            errors.add("hook_changed")
        try:
            result["owner_unchanged"] = _GUARD_THREAD() == owner
        except BaseException as error:
            errors.add("thread_error")
            if not isinstance(error, Exception) and observer_fatal is None:
                observer_fatal = error
        result["body"] = elapsed(before, after)
        result["initial_scan_to_partition_parse"] = elapsed(before, split_start)
        result["function_selection_and_assertion"] = elapsed(split_end, after)
        result["errors"] = sorted(errors)
        result["quality_pass"] = guard_quality(result)
        if primary_error is None and observer_fatal is not None:
            raise observer_fatal


@contextlib.contextmanager
def _measure_phase(
    node: str,
    phase: str,
    *,
    case: str,
    mode: str,
    measurement: dict[str, Any],
) -> Iterator[None]:
    if node != selection(case, mode):
        raise RuntimeError("Diagnostic must run its exact reviewed case")
    if phase != CASE_PHASES[case]:
        yield
        return
    result: dict[str, Any] = {}
    context = (
        _guard_body()
        if case == "sql-guard"
        else contextlib.contextmanager(_profile_body)()
        if mode == "cpu"
        else _wait_body()
    )
    try:
        with context as result:
            yield
    finally:
        measurement.update(result)
        measurement["phase"] = phase


# Importing the CLI must not require pytest. Only the dedicated child loads hooks.
if os.environ.get(PROFILE_ENV) == "1":
    import pytest

    _CASE = os.environ.get(CASE_ENV, "")
    _MODE = os.environ.get(MODE_ENV, "")
    _NODE = selection(_CASE, _MODE)
    _PHASE = _phase(_CASE, os.environ.get(PHASE_ENV))
    _MEASUREMENT: dict[str, Any] = {}

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_setup(item: Any) -> Iterator[None]:
        with _measure_phase(item.nodeid, "setup", case=_CASE, mode=_MODE, measurement=_MEASUREMENT):
            yield

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_call(item: Any) -> Iterator[None]:
        with _measure_phase(item.nodeid, "call", case=_CASE, mode=_MODE, measurement=_MEASUREMENT):
            yield

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(session: Any, exitstatus: int) -> Iterator[None]:
        yield
        path = Path("/tmp/core-results.json")
        result = json.loads(path.read_text())
        result["diagnostic_measurement"] = {
            "case": _CASE,
            "mode": _MODE,
            "node": _NODE,
            "phase": _PHASE,
        }
        result["cpu_profile" if _MODE == "cpu" else "wait_profile"] = _MEASUREMENT
        core_client._write_report(path, result)


def _binding(root: Path) -> tuple[str, str]:
    return core_runner.source_revision(root), hashlib.sha256(
        json.dumps(core_runner.source_binding(root), sort_keys=True).encode()
    ).hexdigest()


def prepare(
    directory: Path, *, image: str, case: str = "scoring", mode: str = "cpu"
) -> dict[str, Any]:
    """Read existing identities and write a private plan, without starting actors."""
    node = selection(case, mode)
    if directory.exists() or directory.is_symlink():
        raise ValueError("Diagnostic directory already exists; never replace a packet")
    info = core_runner._container("swingset-test-postgres")
    if not info["State"]["Running"] or info["Config"]["Image"] != services.POSTGRES_IMAGE:
        raise RuntimeError("The pinned warm test service must already be running")
    _image(image)
    revision, binding = _binding(core_runner.ROOT)
    job = uuid.uuid4().hex[:24]
    plan = {
        "format": "swingset-diagnostic-v1",
        "mode": mode,
        "case": case,
        "node": node,
        "phase": CASE_PHASES[case],
        "source": revision,
        "binding": binding,
        "image": image,
        "primary_id": info["Id"],
        "job": job,
        "name": f"swingset-core-{job}-prologue",
        "volume": f"swingset-core-{job}-prologue-tmp",
        "measurement_seconds": MEASUREMENT_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "retained_volume": True,
        "normal_timing_claimed": False,
    }
    directory.mkdir(parents=True, mode=0o700)
    directory = directory.resolve()
    plan["directory"] = str(directory)
    core_runner._write_private(directory / "plan.json", plan)
    return plan


def _image(image: str) -> None:
    image_info = json.loads(core_runner._docker("image", "inspect", image).stdout)[0]
    if (
        image_info["Id"] != image
        or image_info["Os"] + "/" + image_info["Architecture"] != core_images.native_platform()
    ):
        raise RuntimeError("Diagnostic requires an existing pinned native image")


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[None]:
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        found = os.fstat(fd)
        if not stat.S_ISREG(found.st_mode) or found.st_uid != os.getuid():
            raise RuntimeError("Diagnostic lock ownership changed")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _consume(directory: Path, plan: Mapping[str, Any]) -> None:
    fd = os.open(directory / "started.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"job": plan["job"]}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _absent(kind: str, name: str) -> None:
    found = core_runner._docker(kind, "inspect", name, check=False)
    markers = (
        ("no such object", "no such container") if kind == "container" else ("no such volume",)
    )
    if found.returncode == 0 or not any(marker in found.stderr.lower() for marker in markers):
        raise RuntimeError("Diagnostic resource is existing or its absence is unconfirmed")


def _copy_roots() -> tuple[Path, ...]:
    configured = os.environ.get("SWINGSET_COPY_ROOT")
    return (
        (Path(configured),) if configured else (Path("/tmp") / f"swingset-pg-copies-{os.getuid()}",)
    )


def _endpoint(service: services.Service) -> str:
    from psycopg.conninfo import conninfo_to_dict

    values = conninfo_to_dict(service.dsn())
    return hashlib.sha256(
        json.dumps(
            {key: values.get(key) for key in ("host", "port", "user")}, sort_keys=True
        ).encode()
    ).hexdigest()


def _idle(primary: services.Service, peer: services.Service) -> None:
    """Read only: no live clients, case databases, or retained endpoint ledgers."""
    if any((services.home() / "test-runs").glob("*.json")):
        raise RuntimeError("Retained test-run ledgers must be reviewed first")
    endpoints = {_endpoint(primary), _endpoint(peer)}
    for root in _copy_roots():
        if not root.is_absolute() or root.is_symlink():
            raise RuntimeError("Scratch ledger directory identity is unconfirmed")
        if not root.exists():
            continue
        found = root.stat()
        if found.st_uid != os.getuid() or found.st_mode & 0o077:
            raise RuntimeError("Scratch ledgers are not privately owned")
        for path in root.glob("*.json"):
            if services._json(path).get("endpoint") in endpoints:
                raise RuntimeError("Retained endpoint ledger must be reviewed first")
    for service in (primary, peer):
        with service.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid()"
            ).fetchone()
            if row is None or row[0] != 0:
                raise RuntimeError("Other test clients are still connected")
            if service is primary:
                for name, template, allowed in conn.execute(
                    "SELECT datname,datistemplate,datallowconn FROM pg_database ORDER BY datname"
                ):
                    if name in {"postgres", "template0", "template1", services.ROLE}:
                        continue
                    if not (
                        re.fullmatch(r"sst_template_[0-9a-f]{20}", name)
                        and template
                        and not allowed
                    ):
                        raise RuntimeError("Retained test case databases must be reviewed first")


def accepted(result: Mapping[str, Any]) -> bool:
    reports = result.get("reports") or {}
    outcome = reports.get("core-results.json") or {}
    summary = reports.get("core-client-summary.json") or {}
    counts = outcome.get("counts") or {}
    case, mode = result.get("case"), result.get("mode")
    if (
        not isinstance(case, str)
        or not isinstance(mode, str)
        or case not in CASES
        or mode not in MODES
        or (case == "sql-guard" and mode != "cpu")
    ):
        return False
    node = CASES[case]
    phase = CASE_PHASES[case]
    quality = outcome.get("cpu_profile" if mode == "cpu" else "wait_profile") or {}
    measured = outcome.get("diagnostic_measurement") or {}
    expected = {"case": case, "mode": mode, "node": node}
    if phase != "call" or "phase" in measured:
        expected["phase"] = phase
    if case == "sql-guard" and not guard_quality(quality):
        return False
    if case == "checkpoint":
        restored = outcome.get("restore_call_observations") or {}
        if not isinstance(restored, Mapping):
            return False
        cases = restored.get("cases") or []
        if (
            restored.get("format") != restore_observer.FORMAT
            or restored.get("applicable") is not True
            or restored.get("quality") is not True
            or restored.get("errors") != []
            or not isinstance(cases, list)
            or len(cases) != 1
            or not isinstance(cases[0], Mapping)
            or cases[0].get("nodeid") != node
        ):
            return False
        payload = {key: value for key, value in cases[0].items() if key != "nodeid"}
        if (
            payload.get("quality") is not True
            or not restore_observer.safe(payload)
            or not restore_observer.quality(payload)
        ):
            return False
    return bool(
        result.get("exit_code") == 0
        and result.get("limits") == LIMITS
        and result.get("source_stable")
        and result.get("abort") is None
        and not result.get("host_cleanup_ceiling_reached")
        and not result.get("interrupted_signals")
        and result.get("actor_container_absent")
        and not result.get("stop_error")
        and counts.get("passed") == 1
        and not any(
            counts.get(key, 0) for key in ("failed", "error", "skipped", "xfailed", "xpassed")
        )
        and result.get("node") == node
        and result.get("phase", "call") == phase
        and outcome.get("executed") == [node]
        and measured == expected
        and quality.get("phase", "call") == phase
        and outcome.get("exit_code") == 0
        and quality.get("quality_pass") is True
        and (mode != "waits" or wait_quality(quality))
        and not summary.get("interrupted_signals")
        and summary.get("pytest_exit_code") == 0
        and not (summary.get("diagnostic") or {}).get("measurement_ceiling_reached", True)
        and (summary.get("cleanup") or {}).get("complete")
        and (summary.get("failure_artifact_export") or {}).get("complete")
    )


def run(directory: Path) -> dict[str, Any]:
    with _lock(services.home() / "core-executor.lock"), _lock(services.home() / "services.lock"):
        return _run_locked(directory)


def _run_locked(directory: Path) -> dict[str, Any]:
    found = directory.lstat()
    if not stat.S_ISDIR(found.st_mode) or found.st_uid != os.getuid() or found.st_mode & 0o077:
        raise RuntimeError("Diagnostic packet must be a private owned directory")
    directory = directory.resolve()
    plan = services._json(directory / "plan.json")
    revision, binding = _binding(core_runner.ROOT)
    if (
        plan.get("format") != "swingset-diagnostic-v1"
        or not isinstance(plan.get("case"), str)
        or plan.get("case") not in CASES
        or plan.get("mode") not in MODES
        or (plan.get("case") == "sql-guard" and plan.get("mode") != "cpu")
        or plan.get("node") != CASES.get(str(plan.get("case")))
        or plan.get("phase", "call") != CASE_PHASES.get(str(plan.get("case")))
        or plan.get("measurement_seconds") != MEASUREMENT_SECONDS
        or plan.get("cleanup_seconds") != CLEANUP_SECONDS
        or plan.get("directory") != str(directory)
        or (plan.get("source"), plan.get("binding")) != (revision, binding)
    ):
        raise RuntimeError("Diagnostic plan or source changed; prepare a new reviewed packet")
    job = plan["job"]
    if (
        len(job) != 24
        or any(char not in "0123456789abcdef" for char in job)
        or plan["name"] != f"swingset-core-{job}-prologue"
        or plan["volume"] != f"swingset-core-{job}-prologue-tmp"
    ):
        raise RuntimeError("Diagnostic resource ownership is not exact")
    _image(plan["image"])
    info = core_runner._container(plan["primary_id"])
    if (
        info["Id"] != plan["primary_id"]
        or info["Name"] != "/swingset-test-postgres"
        or not info["State"]["Running"]
        or info["Config"]["Image"] != services.POSTGRES_IMAGE
        or info["Config"]["Labels"].get(services.LABEL) != "test"
        or info["HostConfig"]["Memory"] != core_runner.MEMORY
        or info["HostConfig"]["NanoCpus"] != 6_000_000_000
    ):
        raise RuntimeError("Diagnostic warm test identity changed")
    record = services._json(services.home() / "test-service.json")
    if record["configuration"] != services._configuration("test"):
        raise RuntimeError("Diagnostic test profile changed")
    primary = services.Service(
        "swingset-test-postgres",
        "test",
        15432,
        record["password"],
        worker_password=record["worker_password"],
        control_password=record["control_password"],
    )
    services._validate_service(primary)
    with primary.connect() as conn:
        if services._roles_signature(conn) != record["roles_signature"] or conn.execute(
            "SELECT datistemplate,datallowconn FROM pg_database WHERE datname=%s",
            (services.template_name(),),
        ).fetchone() != (True, False):
            raise RuntimeError("Existing exact test roles and template are required")
    peer = physical._service(physical._read())
    physical.assert_clean(peer)
    if services.active_runs():
        raise RuntimeError("Active test owners must finish before the diagnostic")
    _idle(primary, peer)
    _absent("volume", plan["volume"])
    _absent("container", plan["name"])
    resources = core_runner._database_resources(plan["primary_id"])
    baseline = {
        name: item["events"].get("oom_kill", 0)
        for name, item in resources["services"].items()
        if item
    }
    reason = core_runner.resource_failure(
        {}, resources, baseline, shutil.disk_usage(core_runner.ROOT).free
    )
    if reason:
        raise RuntimeError(reason)
    values = core_runner._private_environment(primary, peer, revision)
    values.pop("SWINGSET_TEST_PARTITION_REPORT_DIR", None)
    values.pop("SWINGSET_CORE_PARTITION_WEIGHTS", None)
    values[CASE_ENV] = plan["case"]
    values[MODE_ENV] = plan["mode"]
    values[PHASE_ENV] = CASE_PHASES[plan["case"]]
    _consume(directory, plan)
    client: dict[str, Any] = {
        "job": job,
        "index": "prologue",
        "name": plan["name"],
        "volume": plan["volume"],
    }
    core_runner._write_private(directory / "owner.json", client)
    environment = directory / "private.env"
    requested: list[int] = []
    old = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    for number in old:
        signal.signal(number, lambda number, frame: requested.append(number))
    timer: threading.Timer | None = None
    process: subprocess.Popen[Any] | None = None
    ceiling: list[bool] = []
    samples: list[dict[str, Any]] = []
    try:
        fd = os.open(environment, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
        core_runner._docker(
            "volume",
            "create",
            "--label",
            core_runner.LABEL + "=" + job,
            "--label",
            core_runner.LABEL + ".partition=prologue",
            client["volume"],
        )
        volume = json.loads(core_runner._docker("volume", "inspect", client["volume"]).stdout)[0]
        if volume["Labels"] != {
            core_runner.LABEL: job,
            core_runner.LABEL + ".partition": "prologue",
        }:
            raise RuntimeError("Diagnostic volume ownership changed")
        client["volume_created"] = volume["CreatedAt"]
        core_runner._write_private(directory / "owner.json", client)
        command = core_runner._client_command(
            core_runner.ROOT, plan["image"], environment, plan["primary_id"], client, job
        )
        pivot = command.index(plan["image"])
        command = command[:pivot] + [plan["image"], "-m", "swingset.dev.diagnostics", "actor"]
        started = time.monotonic()
        with (directory / "client.log").open("w") as stream:
            process = subprocess.Popen(
                command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
            )
            client["process"] = process

            def stop() -> None:
                ceiling.append(True)
                requested.append(signal.SIGTERM)
                try:
                    core_runner._signal_client(client, job, "SIGKILL")
                except Exception:
                    client["stop_error"] = "Host cleanup ceiling could not confirm actor stop"
                if process is not None and process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

            timer = threading.Timer(CLEANUP_SECONDS, stop)
            timer.daemon = True
            timer.start()
            abort = core_runner._wait_clients(
                [client],
                root=core_runner.ROOT,
                primary_id=plan["primary_id"],
                job=job,
                baseline=baseline,
                samples=samples,
                requested=requested,
                started=started,
            )
            core_runner._stop_clients([client], job)
            timer.cancel()
            timer.join(timeout=16)
            if timer.is_alive():
                client["stop_error"] = "Cleanup watchdog still active"
        reports = core_runner._read_reports(core_runner.ROOT, plan["image"], client, job)
        cidfile = directory / "client-prologue.cid"
        cid = cidfile.read_text().strip() if cidfile.is_file() else None
        inspection = core_runner._docker("container", "inspect", cid, check=False) if cid else None
        absent = bool(
            inspection
            and inspection.returncode
            and any(
                text in inspection.stderr.lower()
                for text in ("no such object", "no such container")
            )
        )
        result = {
            "mode": plan["mode"],
            "case": plan["case"],
            "node": plan["node"],
            "phase": CASE_PHASES[plan["case"]],
            "limits": dict(LIMITS),
            "normal_timing_claimed": False,
            "seconds_including_cleanup_and_read": time.monotonic() - started,
            "exit_code": process.returncode,
            "abort": abort,
            "host_cleanup_ceiling_reached": bool(ceiling),
            "interrupted_signals": requested,
            "actor_container_absent": absent,
            "stop_error": client.get("stop_error"),
            "source_stable": _binding(core_runner.ROOT) == (revision, binding),
            "reports": reports,
            "samples": samples,
            "volume_retained_for_review": True,
        }
        result["diagnostic_accepted"] = accepted(result)
        core_runner._write_private(directory / "result.json", result)
        return result
    except BaseException as error:
        core_runner._write_private(
            directory / "failed.json",
            {
                "error_type": type(error).__name__,
                "actor_started": process is not None,
                "volume_retained_for_review": True,
            },
        )
        raise
    finally:
        if timer is not None:
            timer.cancel()
        if process is not None:
            core_runner._stop_clients([client], job)
        environment.unlink(missing_ok=True)
        for number, handler in old.items():
            signal.signal(number, handler)


def actor() -> int:
    case, mode = os.environ.get(CASE_ENV, ""), os.environ.get(MODE_ENV, "")
    node = selection(case, mode)
    phase = _phase(case, os.environ.get(PHASE_ENV))
    previous = {key: os.environ.get(key) for key in (PROFILE_ENV, CASE_ENV, MODE_ENV, PHASE_ENV)}
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        node,
        "--timings",
        "--tb=no",
        "-p",
        "swingset.dev.core_receipt",
        "-p",
        "swingset.dev.diagnostics",
    ]
    try:
        os.environ.update({PROFILE_ENV: "1", CASE_ENV: case, MODE_ENV: mode, PHASE_ENV: phase})
        return core_client.run_command(
            command, partition="diagnostic", measurement_seconds=MEASUREMENT_SECONDS
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "result", "actor"))
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--case", choices=tuple(CASES), default="scoring")
    parser.add_argument("--mode", choices=MODES, default="cpu")
    args = parser.parse_args(arguments)
    if args.action == "actor":
        return actor()
    if args.directory is None:
        parser.error("a private diagnostic directory is required")
    if args.action == "prepare":
        if args.image is None:
            parser.error("prepare requires an existing --image digest")
        result = prepare(args.directory, image=args.image, case=args.case, mode=args.mode)
    elif args.action == "run":
        result = run(args.directory)
    else:
        result = services._json(args.directory / "result.json")
    print(json.dumps(result, sort_keys=True))
    return 1 if args.action == "run" and not result["diagnostic_accepted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
