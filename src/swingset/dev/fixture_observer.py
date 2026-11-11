"""One passive aggregate for the publication fixture's direct durable writes."""

from __future__ import annotations

import inspect
import re
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import CodeType
from typing import Any

NODE = "tests/build/test_release_paths.py::test_a_changed_artifact_file_refuses_publication"
PROPERTY = "swingset_publication_fixture_observation"
FORMAT = "swingset-publication-fixture-observation-v1"
_SCOPE = "Setup owner only; direct original _published_fixture caller only. Aggregate elapsed includes native work and scheduling, not a filesystem/fsync cause or worker CPU. Observer overhead is included; no normal timing or savings claim."
_WALL = time.monotonic_ns
_CPU = time.thread_time_ns
_THREAD = threading.get_ident
_FRAME = sys._getframe
_ERRORS = {
    "binding_error",
    "clock_error",
    "thread_error",
    "thread_changed",
    "overlap",
    "hook_changed",
    "restore_error",
    "report_error",
}
_COUNTS = ("calls", "normal_returns", "exceptions", "other_callers", "foreign_calls")
_TIMES = ("wall_ns", "owner_thread_cpu_ns", "setup_wall_ns", "setup_owner_thread_cpu_ns")


def matches(nodeid: str) -> bool:
    return nodeid == NODE


def _binding() -> tuple[Any, CodeType]:
    from swingset.fetch import archive

    # Collection already imported the fixture through test_release_paths.
    # Never import test modules or inspect an executing frame's locals here.
    module = sys.modules.get("test_correction_only")
    fixture = getattr(module, "_published_fixture", None)
    if not inspect.isfunction(fixture) or fixture.__code__.co_flags & (
        inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
    ):
        raise ValueError("Publication fixture binding is not a regular function")
    return archive, fixture.__code__


class _Observation:
    def __init__(self, worker: str) -> None:
        self.worker = worker
        self.errors: set[str] = set()
        self.owner = self.thread() or 0
        self.counts = dict.fromkeys(_COUNTS, 0)
        self.wall: int | None = 0
        self.cpu: int | None = 0
        self.setup_wall: int | None = None
        self.setup_cpu: int | None = None
        self.completed = self.active = self.busy = False
        self.restored = True
        self.last_end: int | None = None
        self.hook: tuple[Any, Any, Any] | None = None
        self.lock = threading.Lock()

    def thread(self) -> int | None:
        try:
            value = _THREAD()
            if type(value) is int and value > 0:
                return value
        except Exception:
            pass
        self.errors.add("thread_error")
        return None

    def start(self) -> tuple[int, int] | None:
        try:
            wall, cpu = _WALL(), _CPU()
            if type(wall) is int and type(cpu) is int:
                return wall, cpu
        except Exception:
            pass
        self.errors.add("clock_error")
        return None

    def finish(self, before: tuple[int, int] | None) -> tuple[int | None, int | None, int | None]:
        # CPU interval lies inside wall interval.
        cpu, wall = _CPU(), _WALL()
        if self.thread() != self.owner:
            self.errors.add("thread_changed")
        if before is None or type(wall) is not int or type(cpu) is not int:
            self.errors.add("clock_error")
            return None, None, None
        return wall - before[0], cpu - before[1], wall

    def invoke(self, original: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self.counts["calls"] += 1
        if self.busy:
            self.errors.add("overlap")
            try:
                result = original(*args, **kwargs)
            except BaseException:
                self.counts["exceptions"] += 1
                raise
            else:
                self.counts["normal_returns"] += 1
                return result
        before, failure = None, None
        self.busy = True
        try:
            before = self.start()
            if before is not None and self.last_end is not None and before[0] < self.last_end:
                self.errors.add("overlap")
            result = original(*args, **kwargs)
        except BaseException as error:
            failure = error
            self.counts["exceptions"] += 1
            raise
        else:
            self.counts["normal_returns"] += 1
            return result
        finally:
            try:
                try:
                    wall, cpu, self.last_end = self.finish(before)
                    self.wall = None if self.wall is None or wall is None else self.wall + wall
                    self.cpu = None if self.cpu is None or cpu is None else self.cpu + cpu
                except BaseException as fault:
                    self.wall = self.cpu = None
                    self.errors.add("clock_error")
                    if failure is None and not isinstance(fault, Exception):
                        raise
            finally:
                self.busy = False

    def install(self) -> None:
        owner, caller_code = _binding()
        original = owner.durable_write
        if (
            not isinstance(caller_code, CodeType)
            or not inspect.isfunction(original)
            or original.__code__.co_flags
            & (inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR)
        ):
            raise ValueError("Publication write binding is not a regular function")

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not self.active:
                return original(*args, **kwargs)
            thread = self.thread()
            if thread is None or not self.owner:
                return original(*args, **kwargs)
            if thread != self.owner:
                with self.lock:
                    self.counts["foreign_calls"] += 1
                return original(*args, **kwargs)
            try:
                caller = _FRAME(1).f_code is caller_code
            except Exception:
                self.errors.add("binding_error")
                return original(*args, **kwargs)
            if not caller:
                self.counts["other_callers"] += 1
                return original(*args, **kwargs)
            return self.invoke(original, args, kwargs)

        self.hook = owner, original, wrapped
        owner.durable_write = wrapped

    def close(self) -> None:
        if self.hook is None:
            return
        owner, original, wrapped = self.hook
        if owner.durable_write is not wrapped:
            self.restored = False
            self.errors.add("hook_changed")
            return
        owner.durable_write = original
        if owner.durable_write is not original:
            self.restored = False
            self.errors.add("restore_error")

    def report(self, exception: str | None) -> dict[str, Any]:
        value = {
            "format": FORMAT,
            "phase": "setup",
            "worker": self.worker,
            "owner_thread_id": self.owner,
            "setup_completed": self.completed,
            "hooks_restored": self.restored,
            "exception_type": exception,
            "wall_ns": self.wall,
            "owner_thread_cpu_ns": self.cpu,
            "setup_wall_ns": self.setup_wall,
            "setup_owner_thread_cpu_ns": self.setup_cpu,
            **self.counts,
            "errors": sorted(self.errors),
            "scope": _SCOPE,
        }
        value["quality"] = quality(value)
        return value


def safe(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "format",
        "phase",
        "worker",
        "owner_thread_id",
        "setup_completed",
        "hooks_restored",
        "exception_type",
        "errors",
        "scope",
        "quality",
        *_COUNTS,
        *_TIMES,
    }:
        return False
    return bool(
        value["format"] == FORMAT
        and value["phase"] == "setup"
        and value["scope"] == _SCOPE
        and isinstance(value["worker"], str)
        and re.fullmatch(r"controller|gw[0-9]{1,4}", value["worker"])
        and all(
            type(value[key]) is int and 0 <= value[key] < 2**63
            for key in (*_COUNTS, "owner_thread_id")
        )
        and all(value[key] is None or type(value[key]) is int for key in _TIMES)
        and all(
            type(value[key]) is bool for key in ("setup_completed", "hooks_restored", "quality")
        )
        and (
            value["exception_type"] is None
            or isinstance(value["exception_type"], str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value["exception_type"])
        )
        and isinstance(value["errors"], list)
        and len(value["errors"]) <= len(_ERRORS)
        and all(isinstance(error, str) and error in _ERRORS for error in value["errors"])
    )


def quality(value: dict[str, Any]) -> bool:
    if (
        not safe({**value, "quality": False})
        or value["errors"]
        or not value["setup_completed"]
        or not value["hooks_restored"]
        or not value["owner_thread_id"]
        or value["exception_type"] is not None
    ):
        return False
    if not value["calls"] or value["normal_returns"] != value["calls"] or value["exceptions"]:
        return False
    if any(type(value[key]) is not int or value[key] < 0 for key in _TIMES):
        return False
    return bool(
        0 < value["wall_ns"] <= value["setup_wall_ns"]
        and 0 <= value["owner_thread_cpu_ns"] <= value["wall_ns"]
        and value["owner_thread_cpu_ns"]
        <= value["setup_owner_thread_cpu_ns"]
        <= value["setup_wall_ns"]
    )


@contextmanager
def observe(worker: str) -> Iterator[dict[str, Any]]:
    value: dict[str, Any] = {}
    state = _Observation(worker)
    before, failure, exception = None, None, None
    try:
        before = state.start()
        try:
            state.install()
        except Exception:
            state.errors.add("binding_error")
        state.active = True
        yield value
        state.completed = True
    except BaseException as error:
        failure, exception = error, type(error).__name__
        raise
    finally:
        state.active = False
        pending = None
        try:
            state.setup_wall, state.setup_cpu, _ = state.finish(before)
        except BaseException as fault:
            state.errors.add("clock_error")
            if failure is None and not isinstance(fault, Exception):
                pending = fault
        finally:
            try:
                state.close()
            except BaseException as fault:
                state.restored = False
                state.errors.add("restore_error")
                if failure is None and pending is None and not isinstance(fault, Exception):
                    pending = fault
        try:
            value.update(state.report(exception))
        except BaseException as fault:
            value.update(format=FORMAT, quality=False, errors=["report_error"])
            if failure is None and pending is None and not isinstance(fault, Exception):
                pending = fault
        if pending is not None:
            raise pending


class Collector:
    def __init__(self) -> None:
        self.expected = False
        self.value: dict[str, Any] | None = None
        self.errors: set[str] = set()

    def accept(self, report: Any, worker: str) -> None:
        values = [value for name, value in report.user_properties if name == PROPERTY]
        if not matches(report.nodeid):
            if values:
                self.errors.add("unexpected_case_payload")
            return
        self.expected = True
        if report.when != "setup":
            if values:
                self.errors.add("non_setup_payload")
            return
        if self.value is not None or len(values) != 1:
            self.errors.add("missing_or_duplicate_payload")
            return
        value = values[0]
        if not safe(value) or value["worker"] != worker or value["quality"] != quality(value):
            self.errors.add("invalid_payload")
            return
        self.value = value

    def report(self) -> dict[str, Any]:
        if self.expected and self.value is None:
            self.errors.add("missing_setup_payload")
        return {
            "format": FORMAT,
            "applicable": self.expected,
            "quality": not self.errors and (self.value is None or self.value["quality"]),
            "errors": sorted(self.errors),
            "case": None if self.value is None else {"nodeid": NODE, **self.value},
        }
