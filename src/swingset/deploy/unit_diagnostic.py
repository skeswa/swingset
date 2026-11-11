"""Diagnose one inventory transform on a new owned copy of a reviewed checkpoint.

Stage this separately from /app only after promotion, testing and review. Imports
use the installed, verified producer package; this operator is not that package.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import inspect
import json
import os
import signal
import sys
import threading
import time
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

UNIT = "project/inventory/all"
FORMAT = "swingset-one-unit-rollback-diagnostic-v1"
RUN_ID = "graph_verify"
WRITE_SECONDS = 75.0
IMAGE = "sha256:346f30a492e7263c77e7c4346595e5a28e01bb1957ce6ee7e16be4f5c0f74f89"
RUNTIME_ARTIFACT = (
    "swingset-runtime@sha256:5f9b83c510d8af3870cf3b20d3319fb9dff83c91e1c4eadb4fe03134ce0b4cf9"
)
SOURCE = "source-sha256:8aae03bf38566229a30475acadaf0ea659cff843166d6c40b31883cb6e8e3c2a"
INSTALLED_PROOF = "d13c0e3f4688c6bbcc51a8339266487cda96a86d20cc9f441c3d5ca92180cd74"
BUSINESS_CLOCK = "2026-11-11T13:03:02.488396+00:00"
SOURCE_CHECKPOINT = "00c831a474ecfa1de29ca0e8402366e33ed62cd5d409a0f294d9db9c4a0e2f37"
ACCEPTED_CHECKPOINT = "5039effbf0b79dbb5cef98fb2001e7142be18ec8a80eaf75a8ea9dc233351a61"
EXPECTED_UNITS = 108390
UNIT_SET_SHA256 = "d58f3f45406d1ebdb9e4c5fcd9dcc4cd525118971c97089039206bad47665228"
INPUTS_SHA256 = "63c46b8dd4890849edb6cd1f549f80518a067f8ebd7c102e792ee0d47b988fe8"
BINDING_SHA256 = "1ffa96ed76243926c62d2e98b8c34d7042fc080b46d0dc82c03973a21847010e"


_ACCEPT_CALL_LIMIT = 8192
_ACCEPT_SOURCE = {
    "inputs": "58ef4df6b9f9af6f019df1fe35435f533e938dc75227f78cbb2deda3b4fd6d68",
    "recipes": "902b5165ab67e48a9da61ba7b335a7b229016e01cdf902f2990d8fe0e00f4ed0",
    "work": "28ba39b53527d43201cd3e2a46090dfbdc66d3e996b8098c5b134381a193f077",
}


def _acceptance_bindings() -> tuple[list[tuple[Any, str, Any]], dict[str, dict[Any, str]]]:
    """Bind already-loaded eager held8 functions, without importing or calling them."""
    modules = []
    for name, expected in _ACCEPT_SOURCE.items():
        module = sys.modules.get("swingset.state." + name)
        if not inspect.ismodule(module) or module.__file__ is None:
            raise ValueError("acceptance observer requires the loaded held producer")
        if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != expected:
            raise ValueError("acceptance observer business source differs")
        modules.append(module)
    inputs, recipes, work = modules
    for module, name in (
        (inputs, "accept"),
        (recipes, "captured_audit_inputs"),
        (recipes, "captured_recipe_inputs"),
        (work, "affected_work"),
        (work, "enqueue"),
        (work, "accept_inputs"),
    ):
        function = getattr(module, name)
        if (
            not inspect.isfunction(function)
            or function.__module__ != module.__name__
            or function.__globals__ is not vars(module)
            or function.__name__ != name
            or function.__qualname__ != name
            or function.__code__.co_name != name
            or function.__code__.co_filename != module.__file__
            or function.__code__.co_flags
            & (inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR)
        ):
            raise ValueError("acceptance observer requires original eager functions")
    if (
        inputs.captured_recipe_inputs is not recipes.captured_recipe_inputs
        or inputs.accept_inputs is not work.accept_inputs
    ):
        raise ValueError("acceptance observer original aliases differ")
    journal = sys.modules.get("swingset.state.identity_journal")
    if not inspect.ismodule(journal) or getattr(journal, "enqueue", None) is not work.enqueue:
        raise ValueError("acceptance observer requires the original prelude journal alias")
    affected = [
        code
        for code in inputs.accept.__code__.co_consts
        if isinstance(code, types.CodeType) and code.co_name == "affected"
    ]
    if len(affected) != 1:
        raise ValueError("acceptance observer original affected caller differs")
    hooks = [
        (inputs, "captured_recipe_inputs", recipes.captured_recipe_inputs),
        (recipes, "captured_recipe_inputs", recipes.captured_recipe_inputs),
        (work, "affected_work", work.affected_work),
        (work, "enqueue", work.enqueue),
    ]
    callers = {
        "captured_recipe_inputs": {
            inputs.accept.__code__: "recipe_pipeline",
            recipes.captured_audit_inputs.__code__: "recipe_audit",
        },
        "affected_work": {affected[0]: "affected_work"},
        "enqueue": {work.accept_inputs.__code__: "enqueue"},
    }
    return hooks, callers


class Observations:
    """Bounded owner-thread timings; never serialize statements or their arguments."""

    PHASES = frozenset(
        {
            "capture",
            "prefreeze_source_identity",
            "freeze",
            "destination_prelude",
            "accept",
            "post_acceptance_binding",
            "inventory_trial",
            "post_source_identity",
        }
    )

    def __init__(self) -> None:
        self.wall, self.cpu, self.identity = time.monotonic, time.thread_time, threading.get_ident
        self.owner = self.identity()
        self.depth = 0
        self._acceptance_active = False
        self.value: dict[str, Any] = {
            "quality": True,
            "clock_errors": 0,
            "overflow": False,
            "foreign_thread_calls": 0,
            "nested_execute_calls": 0,
            "phases": [],
            "families": {},
            "last_phase": None,
            "last_family": None,
            "coverage": "Connection.execute outermost owner-thread calls only; raw transaction/commit commands may bypass this hook. Phase accept includes ordinary commit. No server or child-thread CPU; spans overlap and must not be summed. Failure frames cover only the primary traceback, not cause/context chains. Coarse families and last_family do not identify a precise timed SQL statement.",
        }

    def owner_call(self) -> bool:
        try:
            if self.identity() == self.owner:
                return True
        except Exception:
            self.value["quality"] = False
            return False
        self.count(self.value, "foreign_thread_calls")
        return False

    def count(self, row: dict[str, Any], key: str) -> None:
        if row.get(key, 0) < 2**63 - 1:
            row[key] = row.get(key, 0) + 1
        else:
            self.value.update(quality=False, overflow=True)

    def stamp(self) -> tuple[float, float] | None:
        try:
            return self.wall(), self.cpu()
        except Exception:
            self.value["quality"] = False
            self.count(self.value, "clock_errors")
            return None

    def finish(self, row: dict[str, Any], start: tuple[float, float] | None) -> None:
        end = self.stamp()
        if start is None or end is None:
            return
        wall, cpu = end[0] - start[0], end[1] - start[1]
        if not (0 <= cpu <= wall + 0.001 and wall < float("inf")):
            self.value["quality"] = False
            return
        row["wall_seconds"] = row.get("wall_seconds", 0.0) + wall
        row["owner_thread_cpu_seconds"] = row.get("owner_thread_cpu_seconds", 0.0) + cpu

    @contextlib.contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if not self.owner_call():
            yield
            return
        if name not in self.PHASES:
            self.value["quality"] = False
            name = "other"
        self.value["last_phase"] = name
        row: dict[str, Any] = {"name": name}
        start = self.stamp()
        error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                self.finish(row, start)
                if len(self.value["phases"]) < 16:
                    self.value["phases"].append(row)
                else:
                    self.value.update(quality=False, overflow=True)
            except Exception:
                self.value["quality"] = False
            except BaseException:
                self.value["quality"] = False
                if error is None:
                    raise

    @staticmethod
    def family(query: Any) -> str:
        if not isinstance(query, str):
            return "other"
        text = query[:512].upper().split()
        for table in ("ACCEPTED_INPUTS", "PENDING_WORK", "WATCHES", "DERIVATION_SCOPES", "META"):
            if table in text or "OPS." + table in text:
                return table.lower()
        if any(word.startswith("IDENTITY_") for word in text):
            return "identity"
        return "read" if text[:1] == ["SELECT"] else "other"

    @contextlib.contextmanager
    def connection(self, connection: Any) -> Iterator[None]:
        original = connection.execute

        def execute(*args: Any, **kwargs: Any) -> Any:
            if not self.owner_call():
                return original(*args, **kwargs)
            if self.depth:
                self.count(self.value, "nested_execute_calls")
                return original(*args, **kwargs)
            try:
                family = self.family(args[0] if args else kwargs.get("query"))
            except Exception:
                self.value["quality"] = False
                family = "other"
            self.value["last_family"] = family
            row = self.value["families"].setdefault(family, {})
            self.count(row, "calls")
            start = self.stamp()
            error: BaseException | None = None
            self.depth += 1
            try:
                return original(*args, **kwargs)
            except BaseException as exc:
                error = exc
                self.count(row, "errors")
                raise
            finally:
                self.depth -= 1
                try:
                    self.finish(row, start)
                except Exception:
                    self.value["quality"] = False
                except BaseException:
                    self.value["quality"] = False
                    if error is None:
                        raise

        with patch.object(connection, "execute", execute):
            yield

    @contextlib.contextmanager
    def acceptance(self) -> Iterator[None]:
        """Observe four fixed eager caller labels; coarse SQL families stay unchanged."""
        if self._acceptance_active:
            raise ValueError("acceptance observation is already active")
        bindings, callers = _acceptance_bindings()  # Before the accept-phase clock.
        value: dict[str, Any] = {
            "format": "swingset-acceptance-callees-v1",
            "quality": True,
            "closed": False,
            "hooks_restored": False,
            "errors": [],
            "other_caller_calls": 0,
            "rows": {
                label: {
                    "calls": 0,
                    "normal_returns": 0,
                    "errors": 0,
                    "wall_seconds": 0.0,
                    "owner_thread_cpu_seconds": 0.0,
                }
                for label in ("recipe_pipeline", "recipe_audit", "affected_work", "enqueue")
            },
            "scope": "Original held8 eager functions, exact owner/caller only. Callee and coarse execute spans are inclusive and overlap; do not sum. No argument, result, frame, error value, SQL or per-yield observation. Identity-journal enqueue aliases are outside the enqueue label. Partial failure remains a prefix, not completed acceptance.",
        }
        self.value["acceptance_callees"] = value
        self._acceptance_active = True
        foreign_before = self.value["foreign_thread_calls"]
        busy: set[str] = set()

        def invalid(reason: str) -> None:
            self.value["quality"] = value["quality"] = False
            if reason not in value["errors"]:
                value["errors"].append(reason)

        def measured(name: str, original: Any) -> Any:
            def call(*args: Any, **kwargs: Any) -> Any:
                try:
                    owned = self.owner_call()
                    label = callers[name].get(sys._getframe(1).f_code) if owned else None
                except Exception:
                    invalid("caller_error")
                    return original(*args, **kwargs)
                except BaseException:
                    invalid("caller_error")
                    raise
                if not owned:
                    return original(*args, **kwargs)
                if label is None:
                    self.count(value, "other_caller_calls")
                    invalid("unknown_caller")
                    return original(*args, **kwargs)
                row = value["rows"][label]
                if label in busy or row["calls"] >= _ACCEPT_CALL_LIMIT:
                    self.value["overflow"] |= row["calls"] >= _ACCEPT_CALL_LIMIT
                    invalid("reentry_or_limit")
                    return original(*args, **kwargs)
                self.count(row, "calls")
                try:
                    start = self.stamp()
                except BaseException:
                    invalid("clock_error")
                    raise
                primary: BaseException | None = None
                busy.add(label)
                try:
                    result = original(*args, **kwargs)
                except BaseException as error:
                    primary = error
                    self.count(row, "errors")
                    raise
                else:
                    self.count(row, "normal_returns")
                    return result
                finally:
                    busy.discard(label)
                    try:
                        self.finish(row, start)
                        if not all(
                            0 <= row[key] < float("inf")
                            for key in ("wall_seconds", "owner_thread_cpu_seconds")
                        ):
                            invalid("clock_error")
                    except Exception:
                        invalid("clock_error")
                    except BaseException:
                        invalid("clock_error")
                        if primary is None:
                            raise

            return call

        hooks = [
            (module, name, original, measured(name, original))
            for module, name, original in bindings
        ]
        installed = []
        primary: BaseException | None = None
        try:
            for module, name, original, hook in hooks:
                installed.append((module, name, original, hook))
                setattr(module, name, hook)
            yield
        except BaseException as error:
            primary = error
            raise
        finally:
            fatal: BaseException | None = None
            for module, name, original, hook in reversed(installed):
                try:
                    if getattr(module, name) is hook:
                        setattr(module, name, original)
                    else:
                        invalid("hook_changed")
                except BaseException as error:
                    invalid("restore_error")
                    if not isinstance(error, Exception) and fatal is None:
                        fatal = error
            try:
                value["hooks_restored"] = all(
                    getattr(module, name) is original for module, name, original in bindings
                )
                if (
                    not value["hooks_restored"]
                    or self.identity() != self.owner
                    or self.value["foreign_thread_calls"] != foreign_before
                ):
                    invalid("restore_or_owner")
                value["quality"] &= self.value["quality"]
                value["closed"] = True
            except Exception:
                invalid("finalization_error")
            except BaseException as error:
                invalid("finalization_error")
                if fatal is None:
                    fatal = error
            finally:
                self._acceptance_active = False
            if primary is None and fatal is not None:
                raise fatal

    def failure(self, error: BaseException) -> None:
        """Only trusted code locations, never messages, source lines or locals."""
        import swingset

        roots = [(Path(__file__).resolve(), "operator/unit_diagnostic.py")]
        installed = Path(swingset.__file__).resolve().parent if swingset.__file__ else None
        frames: list[dict[str, str | int]] = []
        trace = error.__traceback__
        for _ in range(64):
            if trace is None:
                break
            code = trace.tb_frame.f_code
            path = Path(code.co_filename).resolve()
            name = next((label for source, label in roots if path == source), None)
            if name is None and installed is not None and path.is_relative_to(installed):
                name = "installed/swingset/" + path.relative_to(installed).as_posix()
            if name is not None and code.co_name.isidentifier() and len(frames) < 16:
                frames.append({"file": name, "function": code.co_name, "line": trace.tb_lineno})
            trace = trace.tb_next
        if trace is not None:
            self.value.update(quality=False, overflow=True)
        self.value["failure_frames"] = frames


def destination_binding(database: Any, bundle: Any, clock: Any) -> dict[str, Any]:
    """Reproduce G's semantic prelude, before any acceptance write."""
    from swingset.fetch.archive import canonical, digest
    from swingset.state import verify_graph
    from swingset.state.checkpoint_binding import input_acceptance_binding

    expected = verify_graph.enumerate_units(database.connection)
    if (len(expected), digest(canonical(expected))) != (
        EXPECTED_UNITS,
        UNIT_SET_SHA256,
    ) or UNIT not in expected:
        raise ValueError("frozen unit inventory differs from the failed graph")
    with database.connection.read_snapshot():
        binding = input_acceptance_binding(database.connection, bundle, clock)
    if (
        binding.get("format") != "swingset-input-checkpoint-binding-v1"
        or binding.get("source_checkpoint") != SOURCE_CHECKPOINT
        or binding.get("checkpoint") != ACCEPTED_CHECKPOINT
        or digest(canonical(binding.get("inputs"))) != INPUTS_SHA256
        or digest(canonical(binding)) != BINDING_SHA256
    ):
        raise ValueError("input acceptance projection differs from the failed graph")
    return binding


class _PhaseExpired(KeyboardInterrupt):
    pass


@contextlib.contextmanager
def phase_deadline(seconds: float) -> Iterator[None]:
    """Do not replace an earlier owner deadline or recurring timer."""
    started = time.monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)
    delay, interval = signal.getitimer(signal.ITIMER_REAL)
    if interval or 0 < delay <= seconds:
        raise RuntimeError("diagnostic conflicts with an earlier deadline")

    def expired(number: int, frame: Any) -> None:
        raise _PhaseExpired("diagnostic phase exceeded its finite bound")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
        if time.monotonic() - started >= seconds:
            raise _PhaseExpired("diagnostic phase crossed its deadline")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if delay:
            signal.setitimer(
                signal.ITIMER_REAL, max(0.000001, delay - (time.monotonic() - started)), interval
            )


def profile_derivation(
    database: Any, bundle: Any, clock: Any, report: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Use real installed verify_unit; never call its old error-overwriting wrapper."""
    from swingset.project import history, process
    from swingset.state import derivations, verify, write_deadline
    from swingset.state.work import WorkUnit

    unit = WorkUnit(*UNIT.split("/", 2))
    identity, wall_clock, cpu_clock = threading.get_ident, time.monotonic, time.thread_time
    owner = identity()
    inventory_active = False
    phases: list[dict[str, Any]] = []
    completions: list[str] = []
    labels: list[tuple[str, int]] = []
    observer: dict[str, Any] = {
        "quality": True,
        "clock_errors": 0,
        "overflow": False,
        "foreign_thread_calls": 0,
        "other_completions": 0,
        "other_labels": 0,
        "inventory_span_calls": {
            "capture": 0,
            "desired": 0,
            "prepare_inventory": 0,
            "post_link_hints": 0,
        },
    }
    original_live = verify._live_label

    def run_label() -> Any:
        row = database.connection.execute(
            "SELECT md5(row_to_json(r)::text) FROM runs AS r WHERE run_id='graph_verify'"
        ).fetchone()
        return None if row is None else row[0]

    def measured(name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                owned = identity() == owner
            except Exception:
                observer["quality"] = False
                return function(*args, **kwargs)
            if not owned:
                observer["foreign_thread_calls"] += 1
                return function(*args, **kwargs)
            if name in observer["inventory_span_calls"]:
                count = observer["inventory_span_calls"][name]
                if count < 2**63 - 1:
                    observer["inventory_span_calls"][name] = count + 1
                else:
                    observer.update(quality=False, overflow=True)
            try:
                start = wall_clock(), cpu_clock()
            except Exception:
                observer["quality"] = False
                observer["clock_errors"] += 1
                start = None
            error = None
            try:
                return function(*args, **kwargs)
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                try:
                    end = wall_clock(), cpu_clock()
                    if start is not None:
                        wall, cpu = end[0] - start[0], end[1] - start[1]
                        if not 0 <= cpu <= wall + 0.001 or not wall < float("inf"):
                            observer["quality"] = False
                        elif len(phases) < 64:
                            phases.append(
                                {
                                    "name": name,
                                    "wall_seconds": wall,
                                    "thread_cpu_seconds": cpu,
                                    "error_type": error,
                                }
                            )
                        else:
                            observer.update(quality=False, overflow=True)
                except Exception:
                    observer["quality"] = False
                    observer["clock_errors"] += 1
                except BaseException:
                    observer["quality"] = False
                    if error is None:
                        raise

        return call

    original_process = process.process_unit
    measured_process = measured("process", original_process)

    def processing(*args: Any, **kwargs: Any) -> Any:
        nonlocal inventory_active
        selected = args[1] if len(args) > 1 else kwargs.get("unit")
        try:
            owned = identity() == owner
        except Exception:
            observer["quality"] = False
            owned = False
        if selected != unit or not owned:
            return measured_process(*args, **kwargs)
        previous, inventory_active = inventory_active, True
        try:
            return measured_process(*args, **kwargs)
        finally:
            inventory_active = previous

    def inventory_span(name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        timed = measured(name, function)

        def call(*args: Any, **kwargs: Any) -> Any:
            if inventory_active:
                return timed(*args, **kwargs)
            return function(*args, **kwargs)

        return call

    def live(conn: Any, selected: Any) -> Any:
        if selected != unit:
            observer["other_labels"] += 1
            return measured("helper_label", original_live)(conn, selected)
        name = "initial_live_label" if not labels else "output_label"
        label = measured(name, original_live)(conn, selected)
        if len(labels) < 64:
            labels.append(label)
        else:
            observer["overflow"] = True
        return label

    original_complete = derivations.complete

    def complete(conn: Any, selected: Any, **kwargs: Any) -> Any:
        if selected.unit != unit:
            observer["other_completions"] += 1
            return measured("helper_complete", original_complete)(conn, selected, **kwargs)
        if len(completions) < 64:
            completions.append(derivations.digest(selected.dependencies))
        else:
            observer["overflow"] = True
        return measured("complete", original_complete)(conn, selected, **kwargs)

    report = {} if report is None else report
    report.update(
        {
            "unit": UNIT,
            "unit_result": None,
            "phases": phases,
            "completion_dependencies": completions,
            "observer": observer,
            "rollback_fingerprint_equal": False,
            "write_budget_seconds": write_deadline.WRITE_SECONDS,
            "report_stage_budget_seconds": 90,
            "limitations": [
                "Nested completed-function timings overlap; do not sum them.",
                "Only the calling thread is timed; foreign calls are passed through.",
                "Inventory subspans run only inside its process call, excluding all live labels. Capture and desired overlap. Post-link-hints covers only _hint_links; year hints, enqueue and pending deletion remain unassigned process work.",
                "This unit-scoped rollback witness is not a full history audit or graph proof.",
            ],
        }
    )
    with phase_deadline(90):
        pointer = verify._pointer_label(database.connection, unit)
        before_runs = run_label()
        with (
            patch.object(verify, "_live_label", live),
            patch.object(process, "process_unit", processing),
            patch.object(derivations, "capture", inventory_span("capture", derivations.capture)),
            patch.object(derivations, "desired", inventory_span("desired", derivations.desired)),
            patch.object(
                history,
                "prepare_inventory",
                inventory_span("prepare_inventory", history.prepare_inventory),
            ),
            patch.object(
                process, "_hint_links", inventory_span("post_link_hints", process._hint_links)
            ),
            patch.object(derivations, "complete", complete),
        ):
            result = measured("verify_unit", verify.verify_unit)(
                database, unit, bundle, clock, RUN_ID
            )
        if not completions:
            result["state"] = "failed"
            result.setdefault("error", "transform returned without completing its unit")
        report["unit_result"] = result
        try:
            with database.connection.read_snapshot():
                after = measured("rollback_live_label", original_live)(database.connection, unit)
                after_pointer = verify._pointer_label(database.connection, unit)
                after_runs = run_label()
            report.update(
                rollback_live_label=list(after),
                rollback_pointer=after_pointer,
                rollback_run_rows_equal=before_runs == after_runs,
                rollback_fingerprint_equal=bool(labels)
                and after == labels[0]
                and after_pointer == pointer
                and before_runs == after_runs,
            )
        except Exception as error:
            # Preserve the transform's original error even if labeling itself
            # failed. An incomplete witness never becomes an accepted diagnosis.
            report["rollback_error_type"] = type(error).__name__
    report["observer_complete"] = (
        observer["quality"] and not observer["overflow"] and not observer["foreign_thread_calls"]
    )
    return report


def producer_identity() -> dict[str, Any]:
    """Verify installed Python bytes independently of the image revision label."""
    import swingset
    from swingset.link.service import LINKER_VERSION

    if swingset.__file__ is None:
        raise ValueError("diagnostic requires a regular installed producer")
    root = Path(swingset.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        name, body = path.relative_to(root).as_posix().encode(), path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    source = "source-sha256:" + digest.hexdigest()
    if (
        source != SOURCE
        or str(LINKER_VERSION) != "9"
        or os.environ.get("SWINGSET_RUNTIME_IMAGE") != RUNTIME_ARTIFACT
    ):
        raise ValueError("installed diagnostic producer differs")
    return {
        "source_revision": source,
        "linker_version": "9",
        "image": IMAGE,
        "runtime_artifact": RUNTIME_ARTIFACT,
        "installed_proof_sha256": INSTALLED_PROOF,
        "installed_file_count": 560,
    }


def run(
    checkpoint: Path,
    capture_state: Path,
    receipt: Path,
    config: Path,
    overrides: Path,
    at: str,
    expected_source: str,
    expected_checkpoint: str,
) -> dict[str, Any]:
    from swingset.clock import FakeClock
    from swingset.fetch.archive import canonical, digest, durable_write
    from swingset.state import input_profile, inputs, verify_graph, write_deadline
    from swingset.state.db import open_database
    from swingset.state.postgres_copy import Copies
    from swingset.versions import versions

    input_profile.validate_paths(checkpoint, receipt, capture_state=capture_state)
    if (at, expected_source, expected_checkpoint) != (
        BUSINESS_CLOCK,
        SOURCE_CHECKPOINT,
        ACCEPTED_CHECKPOINT,
    ):
        raise ValueError("diagnostic context differs from the reviewed failed graph")
    producer = producer_identity()
    installed_write_seconds = write_deadline.WRITE_SECONDS
    if type(installed_write_seconds) not in (int, float) or installed_write_seconds not in (
        60.0,
        WRITE_SECONDS,
    ):
        raise ValueError("installed worker write budget differs from the known producer policy")
    write_policy: dict[str, Any] = {
        "installed_seconds": installed_write_seconds,
        "authorized_seconds": WRITE_SECONDS,
        "applied_seconds": None,
        "restored": None,
        "scope": "Postfreeze owned-copy acceptance and ordinary inventory trial only; installed producer bytes unchanged.",
    }
    observations = Observations()
    clock = FakeClock(dt.datetime.fromisoformat(at))
    result: dict[str, Any] = {
        "format": FORMAT,
        "graph_proof": False,
        "diagnostic_complete": False,
        "unit": UNIT,
        "business_clock": at,
        "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "producer": producer,
        "worker_write_policy": write_policy,
        "observations": observations.value,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(result))
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(receipt.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)

    def record(phase: str) -> None:
        result["phase"] = phase
        durable_write(receipt, canonical(result))

    try:
        with contextlib.ExitStack() as contexts:
            with phase_deadline(2700):
                record("capture_old_installed_inputs")
                with observations.phase("capture"):
                    bundle = inputs.capture(config, overrides, capture_state, versions())
                source = contexts.enter_context(
                    open_database(
                        checkpoint, read_only=True, lock=False, allow_checkpoint_schema=True
                    )
                )
                if source.environment != "dev":
                    raise ValueError("unit diagnostic requires a private dev source")
                copies = contexts.enter_context(Copies(checkpoint))
                frozen = receipt.parent / "owned-copy"
                if frozen.exists() or frozen.is_symlink():
                    raise ValueError("unit diagnostic refuses an existing copy state")
                with source.connection.read_snapshot():
                    with observations.phase("prefreeze_source_identity"):
                        source_identity = verify_graph.checkpoint_identity(source.connection)
                    if source_identity != expected_source:
                        raise ValueError("source checkpoint identity differs")
                    record("freeze_one_new_owned_database")
                    with observations.phase("freeze"):
                        copies.freeze(source.connection, frozen)
                if len(copies.record["databases"]) != 1 or not copies.record["databases"][
                    0
                ].endswith("_0"):
                    raise ValueError("unit diagnostic requires exactly its new owned_0 database")
                result.update(owner_token=copies.identifier, source_checkpoint=source_identity)
            # The old installed bytes keep their identity. Apply the authorized
            # runtime policy only to the owned copy, independently of its 90s
            # trial ceiling; the original module constant is always restored.
            try:
                with patch.object(write_deadline, "WRITE_SECONDS", WRITE_SECONDS):
                    write_policy["applied_seconds"] = write_deadline.WRITE_SECONDS
                    with open_database(frozen, lock=False) as database:
                        record("destination_graph_prelude")
                        with observations.phase("destination_prelude"):
                            binding = destination_binding(database, bundle, clock)
                        result["input_acceptance_binding"] = {
                            "sha256": digest(canonical(binding)),
                            "inputs_sha256": digest(canonical(binding["inputs"])),
                            "source_checkpoint": binding["source_checkpoint"],
                            "checkpoint": binding["checkpoint"],
                            "expected_unit_count": EXPECTED_UNITS,
                            "unit_set_sha256": UNIT_SET_SHA256,
                        }
                        record("accept_old_installed_inputs_on_copy_only")
                        # This is the ordinary top-level accept and commit, with no
                        # profiling transaction, extra SQL or receipt I/O in the write.
                        with (
                            observations.acceptance(),
                            observations.phase("accept"),
                            observations.connection(database.connection),
                        ):
                            inputs.accept(database, bundle, clock)
                        with observations.phase("post_acceptance_binding"):
                            identity = verify_graph.checkpoint_identity(database.connection)
                            accepted = verify_graph.context(database.connection)
                            if (
                                identity != binding["checkpoint"]
                                or accepted["inputs"] != binding["inputs"]
                            ):
                                raise ValueError(
                                    "candidate context differs from failed graph context"
                                )
                        expected = verify_graph.enumerate_units(
                            database.connection, ["project/inventory"]
                        )
                        if expected != [UNIT]:
                            raise ValueError("unit diagnostic selection differs")
                        record("one_real_rollback_trial")
                        result["trial"] = {}
                        with observations.phase("inventory_trial"):
                            profile_derivation(database, bundle, clock, result["trial"])
                        record("close_owned_copy")
            finally:
                write_policy["restored"] = write_deadline.WRITE_SECONDS == installed_write_seconds
            # Source is still readonly. No checkpoint, database or actor is adopted.
            with source.connection.read_snapshot():
                with observations.phase("post_source_identity"):
                    if verify_graph.checkpoint_identity(source.connection) != expected_source:
                        raise ValueError("source checkpoint changed")
            record("owned_cleanup")
        result["owned_database_cleanup_complete"] = True
        result["diagnostic_complete"] = (
            result["trial"]["rollback_fingerprint_equal"]
            and result["trial"]["observer_complete"]
            and observations.value["quality"]
        )
        result["unit_passed"] = result["trial"]["unit_result"]["state"] == "match"
        record("complete")
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
        result["last_active_phase"] = observations.value["last_phase"]
        try:
            observations.failure(exc)
        except BaseException:
            observations.value["quality"] = False
        try:
            record("failed")
        except BaseException:
            # A receipt/observer fault must not replace the application's fatal
            # error, deadline or uncertain-commit exception.
            pass
        raise
    return result


def authorize_installed_verifier() -> None:
    from swingset.deploy import runtime, security
    from swingset.deploy.configuration import runtime_artifact
    from swingset.deploy.utility import configure

    policy_path = Path("/run/swingset-config/network.json")
    proof = runtime.authorized(policy_path, Path("/run/swingset-authorization"), wait=True)
    if proof["role"] != "verifier" or os.environ.get("SWINGSET_ENV") != "dryrun":
        raise RuntimeError("unit diagnostic role or explicit rehearsal environment differs")
    runtime_artifact(os.environ.get("SWINGSET_RUNTIME_IMAGE", ""))
    policy = security.load(policy_path)
    configure("verifier", Path("/run/secrets"), policy["workers"]["verifier"])


def arguments(parser: argparse.ArgumentParser) -> None:
    for name in ("checkpoint", "capture-state", "receipt", "config", "overrides"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--at", required=True)
    parser.add_argument("--source-checkpoint", required=True)
    parser.add_argument("--expected-checkpoint", required=True)


def execute(args: argparse.Namespace) -> int:
    previous_umask = os.umask(0o077)
    try:
        authorize_installed_verifier()
        result = run(
            args.checkpoint,
            args.capture_state,
            args.receipt,
            args.config,
            args.overrides,
            args.at,
            args.source_checkpoint,
            args.expected_checkpoint,
        )
    except BaseException as error:
        print(json.dumps({"diagnostic_complete": False, "error_type": type(error).__name__}))
        return 1
    finally:
        os.umask(previous_umask)
    print(
        json.dumps(
            {
                "diagnostic_complete": result["diagnostic_complete"],
                "unit": UNIT,
                "graph_proof": False,
                "unit_state": result["trial"]["unit_result"]["state"],
                "unit_passed": result["unit_passed"],
            }
        )
    )
    return 0 if result["diagnostic_complete"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    return execute(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
