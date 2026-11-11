"""Passive owner-thread observations for fixed restore and checkpoint calls."""

from __future__ import annotations

import inspect
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, cast

BASE = "tests/test_postgres_rebuild.py::test_empty_restore_rebuild_preserves_public_bytes_and_unpublished_generation_receipt"
PAIR_NODES = frozenset({BASE + "[False]", BASE + "[True]"})
CHECKPOINT = (
    "tests/backup/test_checkpoint.py::test_restore_public_verification_keeps_activation_explicit"
)
NODES = PAIR_NODES | {CHECKPOINT}
PROPERTY = "swingset_restore_call_observation"
FORMAT = "swingset-restore-call-observations-v5"
LIMIT = 64
LABELS = (
    "schema.migrate",
    "inputs.capture",
    "build_release",
    "publish",
    "snapshot.capture",
    "restore_check",
    "restore_checkpoint",
    "verify_restored_public",
    "activate_restored_state",
    "history.audit",
)
CHECKPOINT_LABELS = (
    "checkpoint.state",
    "create_checkpoint",
    "restore_checkpoint",
    "verify_restored_public",
    "activate_restored_state",
)
CHECKPOINT_CHILD_LABELS = ("snapshot.capture", "backup.check")
_WALL = time.monotonic_ns
_CPU = time.thread_time_ns
_THREAD = threading.get_ident
_GETFRAME = sys._getframe
_COUNTERS = ("wait_calls", "wait_exceptions", "wait_wall_ns", "wait_cpu_ns")
_TIMING = {"start_ns", "end_ns", "owner_thread_cpu_ns", *_COUNTERS}
_RECORD = {
    "index",
    "label",
    "ordinal",
    "parent",
    "depth",
    "exception_type",
    "digest_scans",
    "input_capture",
    *_TIMING,
}
_DIGEST_TIMING = {"wall_ns", "owner_thread_cpu_ns", *_COUNTERS}
_ROLE_COUNTS = {"calls", "normal_returns", "exceptions"}
_DIGEST_COUNTS = {
    *_ROLE_COUNTS,
    "pairs",
    "export_calls",
    "export_normal_returns",
    "export_exceptions",
    "unexpected_callers",
    "nested_calls",
    "foreign_calls",
}
_DIGEST_KEYS = {*_DIGEST_COUNTS, "snapshot_completed", "physical", "immutable"}
_INPUT_BUCKETS = ("runtime", "future_result", "path_exists")
_INPUT_EXCLUDED = {"foreign_calls", "other_callers"}


def _new_inputs() -> dict[str, Any]:
    return {
        "capture_completed": False,
        **{
            name: dict.fromkeys(_ROLE_COUNTS | _DIGEST_TIMING | _INPUT_EXCLUDED, 0)
            for name in _INPUT_BUCKETS
        },
    }


def _new_digests() -> dict[str, Any]:
    return {
        **dict.fromkeys(_DIGEST_COUNTS, 0),
        "snapshot_completed": False,
        **{
            role: dict.fromkeys(_ROLE_COUNTS | _DIGEST_TIMING, 0)
            for role in ("physical", "immutable")
        },
    }


_ERRORS = {
    "clock_error",
    "invalid_clock",
    "thread_changed",
    "binding_error",
    "unsupported_binding",
    "hook_changed",
    "restore_error",
    "record_limit",
    "boundary_during_wait",
    "report_error",
    "thread_error",
    "observer_error",
    "digest_scope",
    "digest_incomplete",
    "input_scope",
    "input_incomplete",
    "checkpoint_scope",
}
_SCOPE = "Call owner thread only; nested spans and their wait deltas are inclusive and overlap. Checkpoint snapshot.capture and backup.check cover only immediate children of create_checkpoint; other owner callers are excluded before child-span clocks."
_DIGEST_SCOPE = "Legacy-pair snapshots alone have two fixed digest aggregates as inclusive children, not additional independent spans. Physical/immutable roles require the original immediate export code caller and ordered pairs; no table, row, arguments or result values are observed."
_INPUT_SCOPE = "Three fixed owner-thread aggregates per completed input capture: imported runtime capture, direct Future.result and direct Path.exists. Only the original immediate inputs.capture code caller is timed. Other callers and foreign threads are excluded without clocks. Elapsed buckets exclude worker CPU and establish no filesystem, fsync or scheduling cause."
_ACCOUNTING = "Outermost psycopg.Connection.wait only; includes client CPU, socket and scheduling. Not server CPU or pure blocking; helper threads and child processes are excluded. Body excludes hook installation/restoration and includes observer overhead."


def matches(nodeid: str) -> bool:
    return nodeid in NODES


def _bindings() -> tuple[list[tuple[Any, str, str]], Any]:
    from psycopg import Connection

    from swingset.backup import check, checkpoint, snapshot
    from swingset.build import service as build
    from swingset.publish import service as publication
    from swingset.state import inputs, postgres_backup, postgres_schema

    return [
        (postgres_schema, "migrate", "schema.migrate"),
        (inputs, "capture", "inputs.capture"),
        (build, "build_release", "build_release"),
        (publication, "publish", "publish"),
        (snapshot, "capture", "snapshot.capture"),
        (check, "restore_check", "restore_check"),
        (checkpoint, "restore_checkpoint", "restore_checkpoint"),
        (checkpoint, "verify_restored_public", "verify_restored_public"),
        (checkpoint, "activate_restored_state", "activate_restored_state"),
        (postgres_backup, "audit", "history.audit"),
    ], Connection


def _checkpoint_bindings(module: Any) -> tuple[list[tuple[Any, str, str]], Any]:
    from psycopg import Connection

    from swingset.backup import check, checkpoint, snapshot

    # These aliases were imported when pytest collected the fixed case. Module
    # hooks alone would miss its calls; observe the actual original aliases.
    if not inspect.ismodule(module) or not inspect.isfunction(module.state):
        raise ValueError("checkpoint test module is unavailable")
    if any(
        getattr(module, name) is not getattr(checkpoint, name) for name in CHECKPOINT_LABELS[1:]
    ):
        raise ValueError("checkpoint test alias differs")
    return [(module, "state", "checkpoint.state")] + [
        (module, name, name) for name in CHECKPOINT_LABELS[1:]
    ] + [(snapshot, "capture", "snapshot.capture"), (check, "check", "backup.check")], Connection


def _labels(variant: str) -> tuple[str, ...]:
    return CHECKPOINT_LABELS + CHECKPOINT_CHILD_LABELS if variant == "checkpoint" else LABELS


class _Observation:
    def __init__(self, worker: str, variant: str) -> None:
        self.worker, self.variant = worker, variant
        self.errors: set[str] = set()
        self.owner = self.thread() or 0
        self.active = False
        self.labels = _labels(variant)
        self.coverage = dict.fromkeys(self.labels, 0)
        self.foreign = dict.fromkeys(self.labels, 0)
        self.wait = dict.fromkeys(_COUNTERS, 0)
        self.wait_depth = self.nested_waits = self.foreign_waits = self.dropped = 0
        self.records: list[dict[str, Any]] = []
        self.parents: list[int] = []
        self.hooks: list[tuple[Any, str, Any, Any]] = []
        self.restored = True
        self.digest_depth = self.export_depth = 0
        self.lock = threading.Lock()

    def thread(self) -> int | None:
        try:
            return _THREAD()
        except Exception:
            self.errors.add("thread_error")
            return None

    def clock(self) -> tuple[int, int] | None:
        try:
            wall, cpu = _WALL(), _CPU()
            if type(wall) is int and type(cpu) is int:
                return wall, cpu
            self.errors.add("invalid_clock")
        except Exception:
            self.errors.add("clock_error")
        return None

    def start(self) -> tuple[tuple[int, int] | None, dict[str, int]]:
        if self.wait_depth:
            self.errors.add("boundary_during_wait")
        return self.clock(), dict(self.wait)

    def finish(self, before: tuple[tuple[int, int] | None, dict[str, int]]) -> dict[str, Any]:
        # Read CPU before wall, keeping the CPU interval inside wall's interval.
        after = None
        try:
            cpu, wall = _CPU(), _WALL()
            if type(wall) is int and type(cpu) is int:
                after = wall, cpu
            else:
                self.errors.add("invalid_clock")
            if _THREAD() != self.owner:
                self.errors.add("thread_changed")
        except Exception:
            self.errors.add("clock_error")
        if self.wait_depth:
            self.errors.add("boundary_during_wait")
        clock, waits = before
        return {
            "start_ns": None if clock is None else clock[0],
            "end_ns": None if after is None else after[0],
            "owner_thread_cpu_ns": None if clock is None or after is None else after[1] - clock[1],
            **{key: self.wait[key] - waits[key] for key in _COUNTERS},
        }

    def invoke(
        self,
        label: str,
        original: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        if not self.active:
            return original(*args, **kwargs)
        thread = self.thread()
        if thread is None or not self.owner:
            return original(*args, **kwargs)
        if thread != self.owner:
            with self.lock:
                self.foreign[label] += 1
            return original(*args, **kwargs)
        if self.variant == "checkpoint" and label in CHECKPOINT_CHILD_LABELS:
            if not self.parents or self.records[self.parents[-1]]["label"] != "create_checkpoint":
                if any(
                    self.records[index]["label"] == "create_checkpoint" for index in self.parents
                ):
                    self.errors.add("checkpoint_scope")
                return original(*args, **kwargs)
        self.coverage[label] += 1
        if len(self.records) >= LIMIT:
            self.dropped += 1
            self.errors.add("record_limit")
            return original(*args, **kwargs)
        record: dict[str, Any] = {
            "index": len(self.records),
            "label": label,
            "ordinal": self.coverage[label],
            "parent": self.parents[-1] if self.parents else None,
            "depth": len(self.parents),
            "exception_type": None,
            "digest_scans": _new_digests()
            if label == "snapshot.capture" and self.variant != "checkpoint"
            else None,
            "input_capture": _new_inputs() if label == "inputs.capture" else None,
        }
        if label == "snapshot.capture" and self.snapshot() is not None:
            self.errors.add("digest_scope")
        if label == "inputs.capture" and self.input_capture() is not None:
            self.errors.add("input_scope")
        self.records.append(record)
        self.parents.append(record["index"])
        before = None
        failure = None
        try:
            try:
                before = self.start()
            except Exception:
                self.errors.add("observer_error")
            result = original(*args, **kwargs)
            if record["digest_scans"] is not None:
                record["digest_scans"]["snapshot_completed"] = True
            if label == "inputs.capture":
                record["input_capture"]["capture_completed"] = True
            return result
        except BaseException as error:
            failure = error
            record["exception_type"] = type(error).__name__
            raise
        finally:
            try:
                try:
                    record.update(
                        self.finish(before) if before is not None else dict.fromkeys(_TIMING)
                    )
                except BaseException as fault:
                    record.update(dict.fromkeys(_TIMING))
                    self.errors.add("observer_error")
                    if failure is None and not isinstance(fault, Exception):
                        raise
            finally:
                self.parents.pop()
                if record["digest_scans"] is not None and not _digest_quality(
                    record["digest_scans"], record
                ):
                    self.errors.add("digest_incomplete")
                if label == "inputs.capture" and not _input_quality(
                    record["input_capture"], record
                ):
                    self.errors.add("input_incomplete")

    def snapshot(self) -> dict[str, Any] | None:
        for index in reversed(self.parents):
            record = self.records[index]
            if record["label"] == "snapshot.capture":
                return cast(dict[str, Any], record["digest_scans"])
        return None

    def input_capture(self) -> dict[str, Any] | None:
        for index in reversed(self.parents):
            record = self.records[index]
            if record["label"] == "inputs.capture":
                return cast(dict[str, Any], record["input_capture"])
        return None

    def finish_aggregate(
        self, aggregate: dict[str, Any], before: Any, failure: BaseException | None
    ) -> None:
        try:
            span = self.finish(before) if before is not None else dict.fromkeys(_TIMING)
            elapsed = (
                None
                if span["start_ns"] is None or span["end_ns"] is None
                else span["end_ns"] - span["start_ns"]
            )
            for key in _DIGEST_TIMING:
                number = elapsed if key == "wall_ns" else span[key]
                aggregate[key] = (
                    None if number is None or aggregate[key] is None else aggregate[key] + number
                )
        except BaseException as fault:
            aggregate.update(dict.fromkeys(_DIGEST_TIMING))
            self.errors.add("observer_error")
            if failure is None and not isinstance(fault, Exception):
                raise

    def input_call(
        self,
        aggregate: dict[str, Any],
        original: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        aggregate["calls"] += 1
        before = None
        failure = None
        try:
            try:
                before = self.start()
            except Exception:
                self.errors.add("observer_error")
            result = original(*args, **kwargs)
        except BaseException as error:
            failure = error
            aggregate["exceptions"] += 1
            raise
        else:
            aggregate["normal_returns"] += 1
            return result
        finally:
            self.finish_aggregate(aggregate, before, failure)

    def install_inputs(self, owner: Any, name: str) -> None:
        capture = getattr(owner, name)
        bindings = (
            (owner, "capture_runtime", "runtime"),
            (Future, "result", "future_result"),
            (Path, "exists", "path_exists"),
        )
        originals = [getattr(target, attribute) for target, attribute, _ in bindings]
        if any(
            not inspect.isfunction(function)
            or function.__code__.co_flags
            & (inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR)
            for function in (capture, *originals)
        ):
            self.errors.add("unsupported_binding")
            return
        capture_code = capture.__code__

        def wrap(bucket: str, original: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(original)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                if not self.active:
                    return original(*args, **kwargs)
                data = self.input_capture()
                if data is None:
                    return original(*args, **kwargs)
                aggregate = data[bucket]
                thread = self.thread()
                if thread is None or not self.owner:
                    self.errors.add("input_scope")
                    return original(*args, **kwargs)
                if thread != self.owner:
                    with self.lock:
                        aggregate["foreign_calls"] += 1
                    return original(*args, **kwargs)
                try:
                    caller = _GETFRAME(1).f_code is capture_code
                except Exception:
                    self.errors.add("observer_error")
                    return original(*args, **kwargs)
                if not caller:
                    aggregate["other_callers"] += 1
                    return original(*args, **kwargs)
                return self.input_call(aggregate, original, args, kwargs)

            return wrapped

        for (target, attribute, bucket), original in zip(bindings, originals, strict=True):
            wrapped = wrap(bucket, original)
            self.hooks.append((target, attribute, original, wrapped))
            setattr(target, attribute, wrapped)

    def digest_owner(self) -> dict[str, Any] | None:
        if not self.active:
            return None
        data = self.snapshot()
        if data is None:
            return None
        thread = self.thread()
        if thread is None or not self.owner:
            self.errors.add("digest_scope")
            return None
        if thread != self.owner:
            with self.lock:
                data["foreign_calls"] += 1
                self.errors.add("digest_scope")
            return None
        return data

    def export_call(
        self, original: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        data = self.digest_owner()
        if data is None:
            return original(*args, **kwargs)
        data["export_calls"] += 1
        if self.export_depth or data["export_calls"] != 1:
            self.errors.add("digest_scope")
        self.export_depth += 1
        try:
            result = original(*args, **kwargs)
        except BaseException:
            data["export_exceptions"] += 1
            raise
        else:
            data["export_normal_returns"] += 1
            return result
        finally:
            self.export_depth -= 1

    def digest_call(
        self,
        caller: bool,
        original: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        data = self.digest_owner()
        if data is None:
            return original(*args, **kwargs)
        data["calls"] += 1
        nested = bool(self.digest_depth)
        unexpected = not caller or self.export_depth != 1
        if nested or unexpected:
            data["nested_calls"] += int(nested)
            data["unexpected_callers"] += int(unexpected)
            self.errors.add("digest_scope")
            try:
                result = original(*args, **kwargs)
            except BaseException:
                data["exceptions"] += 1
                raise
            else:
                data["normal_returns"] += 1
                return result
        role = data["physical" if data["calls"] % 2 else "immutable"]
        role["calls"] += 1
        self.digest_depth = 1
        before = None
        failure = None
        try:
            try:
                before = self.start()
            except Exception:
                self.errors.add("observer_error")
            result = original(*args, **kwargs)
        except BaseException as error:
            failure = error
            data["exceptions"] += 1
            role["exceptions"] += 1
            raise
        else:
            data["normal_returns"] += 1
            role["normal_returns"] += 1
            if data["calls"] % 2 == 0 and data["normal_returns"] == data["calls"]:
                data["pairs"] += 1
            return result
        finally:
            try:
                self.finish_aggregate(role, before, failure)
            finally:
                self.digest_depth = 0

    def install_digests(self, owner: Any) -> None:
        original, export = owner._row_digest, owner.export
        if any(
            not inspect.isfunction(function)
            or function.__code__.co_flags
            & (inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR)
            for function in (original, export)
        ):
            self.errors.add("unsupported_binding")
            return
        export_code = export.__code__

        def digest(*args: Any, **kwargs: Any) -> Any:
            if not self.active or self.snapshot() is None:
                return original(*args, **kwargs)
            # Read only code identity; never retain a caller frame or its locals.
            try:
                caller = _GETFRAME(1).f_code is export_code
            except Exception:
                self.errors.add("observer_error")
                caller = False
            return self.digest_call(caller, original, args, kwargs)

        def exporter(*args: Any, **kwargs: Any) -> Any:
            return self.export_call(export, args, kwargs)

        for name, original_function, wrapped in (
            ("_row_digest", original, digest),
            ("export", export, exporter),
        ):
            self.hooks.append((owner, name, original_function, wrapped))
            setattr(owner, name, wrapped)

    def driver_wait(
        self, original: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        if not self.active:
            return original(*args, **kwargs)
        thread = self.thread()
        if thread is None or not self.owner:
            return original(*args, **kwargs)
        if thread != self.owner:
            with self.lock:
                self.foreign_waits += 1
            return original(*args, **kwargs)
        if self.wait_depth:
            self.nested_waits += 1
            return original(*args, **kwargs)
        self.wait["wait_calls"] += 1
        before = self.clock()
        self.wait_depth = 1
        failure = None
        try:
            return original(*args, **kwargs)
        except BaseException as error:
            failure = error
            self.wait["wait_exceptions"] += 1
            raise
        finally:
            try:
                try:
                    cpu, wall = _CPU(), _WALL()
                    if type(wall) is int and type(cpu) is int:
                        if before is not None:
                            self.wait["wait_wall_ns"] += wall - before[0]
                            self.wait["wait_cpu_ns"] += cpu - before[1]
                    else:
                        self.errors.add("invalid_clock")
                except BaseException as fault:
                    self.errors.add("clock_error")
                    if failure is None and not isinstance(fault, Exception):
                        raise
            finally:
                self.wait_depth = 0

    def install(self, bindings: list[tuple[Any, str, str]], driver: Any) -> None:
        if len(bindings) != len(self.labels) or {label for _, _, label in bindings} != set(
            self.labels
        ):
            self.errors.add("binding_error")
            return
        if "history.audit" in self.labels:
            self.install_digests(
                next(owner for owner, _, label in bindings if label == "history.audit")
            )
        if "inputs.capture" in self.labels:
            self.install_inputs(
                *next((owner, name) for owner, name, label in bindings if label == "inputs.capture")
            )
        for owner, name, label in bindings:
            original = getattr(owner, name)
            if not inspect.isfunction(original) or original.__code__.co_flags & (
                inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
            ):
                self.errors.add("unsupported_binding")
                continue
            wrapped = self.wrap(label, original)
            self.hooks.append((owner, name, original, wrapped))
            setattr(owner, name, wrapped)
        original_wait = driver.wait

        def wait(*args: Any, **kwargs: Any) -> Any:
            return self.driver_wait(original_wait, args, kwargs)

        self.hooks.append((driver, "wait", original_wait, wait))
        driver.wait = wait

    def wrap(self, label: str, original: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(original)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            return self.invoke(label, original, args, kwargs)

        return wrapped

    def close(self, active_error: BaseException | None) -> None:
        self.active = False
        interrupt = None
        for owner, name, original, wrapped in reversed(self.hooks):
            try:
                if getattr(owner, name) is not wrapped:
                    self.errors.add("hook_changed")
                    self.restored = False
                    continue
                setattr(owner, name, original)
                if getattr(owner, name) is not original:
                    self.errors.add("restore_error")
                    self.restored = False
            except BaseException as error:
                self.errors.add("restore_error")
                self.restored = False
                if active_error is None and not isinstance(error, Exception) and interrupt is None:
                    interrupt = error
        if interrupt is not None:
            raise interrupt

    def report(self, body: dict[str, Any], exception: str | None) -> dict[str, Any]:
        result = {
            "format": FORMAT,
            "worker": self.worker,
            "variant": self.variant,
            "owner_thread_id": self.owner,
            "body": body,
            "records": self.records,
            "coverage": self.coverage,
            "foreign_function_calls": self.foreign,
            "nested_owner_wait_calls": self.nested_waits,
            "foreign_wait_calls": self.foreign_waits,
            "hooks_restored": self.restored,
            "dropped": self.dropped,
            "errors": sorted(self.errors),
            "exception_type": exception,
            "limit": LIMIT,
            "scope": _SCOPE,
            "accounting": _ACCOUNTING,
            "digest_scope": _DIGEST_SCOPE,
            "input_scope": _INPUT_SCOPE,
        }
        result["quality"] = quality(result)
        return result


def _exception(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, str)
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value) is not None
    )


def _timing(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _TIMING
        and all(number is None or type(number) is int for number in value.values())
    )


def _digest_safe(data: Any) -> bool:
    if (
        not isinstance(data, dict)
        or set(data) != _DIGEST_KEYS
        or type(data["snapshot_completed"]) is not bool
    ):
        return False
    if any(type(data[key]) is not int or not 0 <= data[key] < 2**63 for key in _DIGEST_COUNTS):
        return False
    for role in ("physical", "immutable"):
        value = data[role]
        if not isinstance(value, dict) or set(value) != _ROLE_COUNTS | _DIGEST_TIMING:
            return False
        if any(type(value[key]) is not int or not 0 <= value[key] < 2**63 for key in _ROLE_COUNTS):
            return False
        if any(value[key] is not None and type(value[key]) is not int for key in _DIGEST_TIMING):
            return False
    return True


def _digest_quality(data: Any, span: dict[str, Any]) -> bool:
    if not _digest_safe(data) or not data["snapshot_completed"]:
        return False
    expected = {
        "calls": 282,
        "normal_returns": 282,
        "pairs": 141,
        "export_calls": 1,
        "export_normal_returns": 1,
    }
    if any(data[key] != expected.get(key, 0) for key in _DIGEST_COUNTS):
        return False
    for name in ("physical", "immutable"):
        role = data[name]
        if (
            role["calls"] != 141
            or role["normal_returns"] != 141
            or role["exceptions"]
            or any(type(role[key]) is not int or role[key] < 0 for key in _DIGEST_TIMING)
        ):
            return False
        if not (
            role["wait_cpu_ns"] <= role["wait_wall_ns"] <= role["wall_ns"]
            and role["wait_cpu_ns"] <= role["owner_thread_cpu_ns"] <= role["wall_ns"]
            and role["wait_exceptions"] <= role["wait_calls"]
        ):
            return False
    if any(type(span.get(key)) is not int for key in _TIMING):
        return False
    return bool(
        sum(data[name]["wall_ns"] for name in ("physical", "immutable"))
        <= span["end_ns"] - span["start_ns"]
        and sum(data[name]["owner_thread_cpu_ns"] for name in ("physical", "immutable"))
        <= span["owner_thread_cpu_ns"]
        and all(
            sum(data[name][key] for name in ("physical", "immutable")) <= span[key]
            for key in _COUNTERS
        )
    )


def _input_safe(data: Any) -> bool:
    if (
        not isinstance(data, dict)
        or set(data) != {"capture_completed", *_INPUT_BUCKETS}
        or type(data["capture_completed"]) is not bool
    ):
        return False
    for name in _INPUT_BUCKETS:
        aggregate = data[name]
        if (
            not isinstance(aggregate, dict)
            or set(aggregate) != _ROLE_COUNTS | _DIGEST_TIMING | _INPUT_EXCLUDED
        ):
            return False
        if any(
            type(aggregate[key]) is not int or not 0 <= aggregate[key] < 2**63
            for key in _ROLE_COUNTS | _INPUT_EXCLUDED
        ):
            return False
        if any(
            aggregate[key] is not None and type(aggregate[key]) is not int for key in _DIGEST_TIMING
        ):
            return False
    return True


def _input_quality(data: Any, span: dict[str, Any]) -> bool:
    if (
        not _input_safe(data)
        or not data["capture_completed"]
        or any(type(span.get(key)) is not int for key in _TIMING)
    ):
        return False
    if data["runtime"]["calls"] != 1 or not data["path_exists"]["calls"]:
        return False
    if span.get("ordinal") == 1 and not data["future_result"]["calls"]:
        return False
    for name in _INPUT_BUCKETS:
        aggregate = data[name]
        if (
            aggregate["normal_returns"] != aggregate["calls"]
            or aggregate["exceptions"]
            or any(type(aggregate[key]) is not int or aggregate[key] < 0 for key in _DIGEST_TIMING)
        ):
            return False
        if not (
            aggregate["wait_cpu_ns"] <= aggregate["wait_wall_ns"] <= aggregate["wall_ns"]
            and aggregate["wait_cpu_ns"] <= aggregate["owner_thread_cpu_ns"] <= aggregate["wall_ns"]
            and aggregate["wait_exceptions"] <= aggregate["wait_calls"]
        ):
            return False
    return bool(
        sum(data[name]["wall_ns"] for name in _INPUT_BUCKETS) <= span["end_ns"] - span["start_ns"]
        and sum(data[name]["owner_thread_cpu_ns"] for name in _INPUT_BUCKETS)
        <= span["owner_thread_cpu_ns"]
        and all(sum(data[name][key] for name in _INPUT_BUCKETS) <= span[key] for key in _COUNTERS)
    )


def safe(value: Any) -> bool:
    """Reject unreviewed transported fields before retaining any child payload."""
    keys = {
        "format",
        "worker",
        "variant",
        "owner_thread_id",
        "body",
        "records",
        "coverage",
        "foreign_function_calls",
        "nested_owner_wait_calls",
        "foreign_wait_calls",
        "hooks_restored",
        "dropped",
        "errors",
        "exception_type",
        "limit",
        "scope",
        "accounting",
        "digest_scope",
        "input_scope",
        "quality",
    }
    if not isinstance(value, dict) or set(value) != keys:
        return False
    if (
        value["format"] != FORMAT
        or value["scope"] != _SCOPE
        or value["accounting"] != _ACCOUNTING
        or value["digest_scope"] != _DIGEST_SCOPE
        or value["input_scope"] != _INPUT_SCOPE
        or value["limit"] != LIMIT
        or not isinstance(value["worker"], str)
        or not re.fullmatch(r"controller|gw[0-9]{1,4}", value["worker"])
        or value["variant"] not in {"False", "True", "checkpoint"}
        or not _exception(value["exception_type"])
        or type(value["quality"]) is not bool
        or type(value["hooks_restored"]) is not bool
        or not _timing(value["body"])
        or not isinstance(value["records"], list)
        or len(value["records"]) > LIMIT
        or not isinstance(value["errors"], list)
        or any(error not in _ERRORS for error in value["errors"])
    ):
        return False
    for key in ("owner_thread_id", "nested_owner_wait_calls", "foreign_wait_calls", "dropped"):
        if type(value[key]) is not int or not 0 <= value[key] < 2**63:
            return False
    for key in ("coverage", "foreign_function_calls"):
        if (
            not isinstance(value[key], dict)
            or set(value[key]) != set(_labels(value["variant"]))
            or any(
                type(number) is not int or not 0 <= number < 2**63 for number in value[key].values()
            )
        ):
            return False
    for record in value["records"]:
        if (
            not isinstance(record, dict)
            or set(record) != _RECORD
            or record["label"] not in _labels(value["variant"])
            or not _exception(record["exception_type"])
            or (
                not _digest_safe(record["digest_scans"])
                if record["label"] == "snapshot.capture" and value["variant"] != "checkpoint"
                else record["digest_scans"] is not None
            )
            or (
                not _input_safe(record["input_capture"])
                if record["label"] == "inputs.capture"
                else record["input_capture"] is not None
            )
            or not _timing({key: record[key] for key in _TIMING})
            or any(type(record[key]) is not int for key in ("index", "ordinal", "depth"))
            or record["parent"] is not None
            and type(record["parent"]) is not int
        ):
            return False
    return True


def quality(value: dict[str, Any]) -> bool:
    """Inclusive waits/spans must fit the owner body, never an additive CPU claim."""
    if (
        not safe({**value, "quality": False})
        or value["errors"]
        or value["dropped"]
        or not value["hooks_restored"]
        or not value["owner_thread_id"]
    ):
        return False
    minimum = dict.fromkeys(_labels(value["variant"]), 1)
    if value["variant"] == "checkpoint":
        majors = [record for record in value["records"] if record["parent"] is None]
        children = [record for record in value["records"] if record["parent"] is not None]
        created = next(
            (record for record in majors if record["label"] == "create_checkpoint"), None
        )
        if (
            value["coverage"] != minimum
            or any(value["foreign_function_calls"].values())
            or value["foreign_wait_calls"]
            or [record["label"] for record in majors] != list(CHECKPOINT_LABELS)
            or [record["label"] for record in children] != list(CHECKPOINT_CHILD_LABELS)
            or created is None
            or any(record["parent"] != created["index"] for record in children)
        ):
            return False
    else:
        minimum.update(
            build_release=3 if value["variant"] == "True" else 2,
            publish=2 if value["variant"] == "True" else 1,
        )
    if any(value["coverage"][label] < count for label, count in minimum.items()):
        return False
    if sum(value["coverage"].values()) != len(value["records"]):
        return False
    body = value["body"]

    def valid_span(span: dict[str, Any]) -> bool:
        if any(type(number) is not int or number < 0 for number in span.values()):
            return False
        wall = span["end_ns"] - span["start_ns"]
        return bool(
            wall > 0
            and 0 <= span["wait_cpu_ns"] <= span["wait_wall_ns"] <= wall
            and span["wait_cpu_ns"] <= span["owner_thread_cpu_ns"] <= wall
            and span["wait_exceptions"] <= span["wait_calls"]
        )

    if not valid_span(body):
        return False
    previous: dict[int | None, int] = {}
    ordinals = dict.fromkeys(_labels(value["variant"]), 0)
    records = value["records"]
    for index, record in enumerate(records):
        if record["index"] != index or not valid_span({key: record[key] for key in _TIMING}):
            return False
        if record["digest_scans"] is not None and not _digest_quality(
            record["digest_scans"], record
        ):
            return False
        if record["label"] == "inputs.capture" and not _input_quality(
            record["input_capture"], record
        ):
            return False
        parent = record["parent"]
        if parent is not None and not 0 <= parent < index:
            return False
        enclosing = body if parent is None else records[parent]
        if (
            record["depth"] != (0 if parent is None else enclosing["depth"] + 1)
            or not enclosing["start_ns"]
            <= record["start_ns"]
            <= record["end_ns"]
            <= enclosing["end_ns"]
            or any(record[key] > body[key] for key in _COUNTERS)
            or record["start_ns"] < previous.get(parent, record["start_ns"])
        ):
            return False
        previous[parent] = record["end_ns"]
        ordinals[record["label"]] += 1
        if record["ordinal"] != ordinals[record["label"]]:
            return False
    return True


@contextmanager
def observe(worker: str, nodeid: str, module: Any = None) -> Iterator[dict[str, Any]]:
    result: dict[str, Any] = {}
    variant = (
        "checkpoint" if nodeid == CHECKPOINT else "True" if nodeid.endswith("[True]") else "False"
    )
    state = _Observation(worker, variant)
    exception = None
    failure: BaseException | None = None
    before = None
    try:
        try:
            state.install(
                *(_checkpoint_bindings(module) if variant == "checkpoint" else _bindings())
            )
        except Exception:
            state.errors.add("binding_error")
        state.active = True
        try:
            before = state.start()
        except Exception:
            state.errors.add("observer_error")
        yield result
    except BaseException as error:
        failure = error
        exception = type(error).__name__
        raise
    finally:
        state.active = False
        pending = None
        body = dict.fromkeys(_TIMING)
        try:
            if before is not None:
                body = state.finish(before)
        except BaseException as fault:
            state.errors.add("observer_error")
            if failure is None and not isinstance(fault, Exception):
                pending = fault
        finally:
            try:
                state.close(failure if failure is not None else pending)
            except BaseException as fault:
                state.errors.add("restore_error")
                state.restored = False
                if failure is None and pending is None and not isinstance(fault, Exception):
                    pending = fault
        try:
            result.update(state.report(body, exception))
        except BaseException as fault:
            # The controller marks an incomplete/unsafe payload unusable. It
            # must not replace an expected publish error, assertion or SIGINT.
            result.update(format=FORMAT, quality=False, errors=["report_error"])
            if failure is None and pending is None and not isinstance(fault, Exception):
                pending = fault
        if pending is not None:
            raise pending


class Collector:
    def __init__(self) -> None:
        self.expected: set[str] = set()
        self.cases: dict[str, dict[str, Any]] = {}
        self.errors: set[str] = set()

    def accept(self, report: Any, worker: str) -> None:
        values = [value for name, value in report.user_properties if name == PROPERTY]
        if not matches(report.nodeid):
            if values:
                self.errors.add("unexpected_case_payload")
            return
        self.expected.add(report.nodeid)
        if report.when != "call":
            if values:
                self.errors.add("non_call_payload")
            return
        if len(values) != 1 or report.nodeid in self.cases:
            self.errors.add("missing_or_duplicate_case_payload")
            return
        value = values[0]
        try:
            valid = (
                safe(value)
                and value["worker"] == worker
                and report.nodeid
                == (
                    CHECKPOINT
                    if value["variant"] == "checkpoint"
                    else BASE + "[" + value["variant"] + "]"
                )
                and value["quality"] == quality(value)
            )
        except Exception:
            valid = False
        if valid:
            self.cases[report.nodeid] = value
        else:
            self.errors.add("invalid_case_payload")

    def report(self) -> dict[str, Any]:
        pair = self.expected & PAIR_NODES
        if pair and pair != PAIR_NODES:
            self.errors.add("incomplete_variant_pair")
        if set(self.cases) != self.expected:
            self.errors.add("incomplete_case_payload")
        if PAIR_NODES <= set(self.cases):
            first, second = (
                self.cases[BASE + "[" + variant + "]"] for variant in ("False", "True")
            )
            if first["worker"] != second["worker"]:
                self.errors.add("variant_worker_mismatch")
            if first["owner_thread_id"] != second["owner_thread_id"]:
                self.errors.add("variant_thread_mismatch")
            if (
                first["body"]["end_ns"] is None
                or second["body"]["start_ns"] is None
                or first["body"]["end_ns"] > second["body"]["start_ns"]
            ):
                self.errors.add("variant_order_unknown_or_overlapping")
        return {
            "format": FORMAT,
            "applicable": bool(self.expected),
            "quality": not self.errors and all(value["quality"] for value in self.cases.values()),
            "errors": sorted(self.errors),
            "cases": [{"nodeid": node, **value} for node, value in self.cases.items()],
        }
