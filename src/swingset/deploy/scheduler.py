"""Persistent calendar cursors, one bounded child at a time and no catch-up burst."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Task:
    name: str
    arguments: tuple[str, ...]
    period: int
    offset: int
    budget: float
    grace: float
    read_only: bool = False

    def slot(self, now: float) -> int:
        return int((now - self.offset) // self.period)


def tasks(group: str, state: Path) -> tuple[Task, ...]:
    def loop(name: str, period: int, budget: int, grace: int = 45, offset: int = 0) -> Task:
        arguments: tuple[str, ...] = (
            "-m",
            "swingset.cli",
            name,
            "--state",
            str(state),
            "--prod",
            "--timer",
            "--budget",
            str(budget),
        )
        if name == "release":
            arguments += ("--dry-run",)
        return Task(name, arguments, period, offset, budget, grace)

    if group == "acquire-plan":
        return loop("acquire", 300, 240), loop("plan", 1800, 600)
    if group == "derive":
        return (loop("derive", 300, 240, 120),)
    if group == "release-maintain":
        return loop("release", 3600, 1800), loop("maintain", 86400, 3600, offset=3 * 3600 + 30 * 60)
    if group == "jobs":
        return (
            Task(
                "backup-monday",
                ("-m", "swingset.cli", "backup", "--segmented", "--prod", "--state", str(state)),
                604800,
                4 * 86400 + 4 * 3600,
                7200,
                45,
            ),
            Task(
                "backup-thursday",
                ("-m", "swingset.cli", "backup", "--segmented", "--prod", "--state", str(state)),
                604800,
                4 * 3600,
                7200,
                45,
            ),
            Task(
                "summary",
                ("-m", "swingset.cli", "summary", "--json", "--state", str(state)),
                86400,
                8 * 3600,
                600,
                45,
            ),
        )
    raise ValueError("unknown scheduler group")


def consume(task: Task, now: float, cursors: dict[str, int]) -> bool:
    slot = task.slot(now)
    if slot <= cursors.get(task.name, slot - 1):
        return False
    cursors[task.name] = slot
    return True


def held(directory: Path) -> bool:
    value = os.environ.get("SWINGSET_OOB_HOLD", "1")
    if value not in {"0", "1"} or not directory.is_dir():
        raise RuntimeError("out-of-band hold configuration is missing or invalid")
    return value == "1" or any(directory.iterdir())


def _write(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def child(
    task: Task,
    stopped: Callable[[], bool],
    heartbeat: Callable[[], None],
    *,
    workspace: Path | None = None,
) -> int:
    arguments = task.arguments
    if workspace is not None:
        arguments = ("-m", "swingset.deploy.task", "--workspace", str(workspace), "--", *arguments)
    process = subprocess.Popen([sys.executable, *arguments], start_new_session=True)

    def reap() -> bool:
        # As container PID 1, the scheduler adopts grandchildren when a CLI
        # exits. Reap only this task's group, preserving every other owner.
        if process.poll() is not None:
            while True:
                try:
                    pid, _ = os.waitpid(-process.pid, os.WNOHANG)
                except ChildProcessError:
                    break
                if pid == 0:
                    break
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        return True

    started = time.monotonic()
    terminating: float | None = None
    peers = False
    try:
        while process.poll() is None:
            now = time.monotonic()
            if terminating is None and (stopped() or now - started >= task.budget):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                terminating = now
            if terminating is not None and now - terminating >= task.grace:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                break
            heartbeat()
            time.sleep(0.2)
        result = process.wait()
    finally:
        peers = reap()
        if peers:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            deadline = (terminating if terminating is not None else time.monotonic()) + task.grace
            try:
                while time.monotonic() < deadline:
                    if not reap():
                        break
                    heartbeat()
                    time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            finally:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        deadline = time.monotonic() + 2
        while reap():
            if time.monotonic() >= deadline:
                raise RuntimeError("task process group did not exit after SIGKILL")
            heartbeat()
            time.sleep(0.01)
    return result if terminating is None and not peers else result or 1


def _notify(
    state: Path, runtime: Path, task: str, *, result: int | None = None, blocked: bool = False
) -> None:
    from .health import notify_tick

    try:
        report = notify_tick(state, runtime, task, exit_code=result, held=blocked)
        print(json.dumps({"task": task, "monitor": report.safe_dict()}), flush=True)
    except Exception as exc:
        print(
            json.dumps(
                {"task": task, "failed": True, "monitor_delivery_failure": type(exc).__name__}
            ),
            flush=True,
        )


def run(
    group: str,
    state: Path,
    runtime: Path,
    holds: Path,
    *,
    before_task: Callable[[], bool] = lambda: True,
) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    cursor_path = runtime / "schedule.json"
    stopped = [False]
    previous = signal.signal(signal.SIGTERM, lambda _signum, _frame: stopped.__setitem__(0, True))
    heartbeat_at = [0.0]

    def heartbeat() -> None:
        if time.monotonic() - heartbeat_at[0] >= 10:
            _write(runtime / "heartbeat.json", {"utc": time.time(), "group": group})
            heartbeat_at[0] = time.monotonic()

    try:
        with (runtime / "scheduler.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            cursors = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
            if not isinstance(cursors, dict) or any(
                type(value) is not int for value in cursors.values()
            ):
                raise RuntimeError("scheduler cursor is malformed")
            while not stopped[0]:
                heartbeat()
                cleanup = Task("task-cleanup", (), 86400, 5 * 3600, 60, 45)
                if consume(cleanup, time.time(), cursors):
                    from .health import cleanup_task_directories

                    removed = cleanup_task_directories(runtime / "tasks", older_than=86400)
                    _write(cursor_path, cursors)
                    print(json.dumps({"task": cleanup.name, "removed": removed}), flush=True)
                for task in tasks(group, state):
                    if stopped[0]:
                        break
                    if consume(task, time.time(), cursors):
                        _write(cursor_path, cursors)  # A skipped or killed slot is still consumed.
                        blocked = held(holds)
                        if blocked and not task.read_only:
                            _notify(state, runtime, task.name, blocked=True)
                            print(json.dumps({"task": task.name, "held": True}), flush=True)
                            continue
                        if not before_task():
                            _notify(state, runtime, task.name, blocked=True)
                            print(json.dumps({"task": task.name, "held": True}), flush=True)
                            continue
                        result = child(
                            task, lambda: stopped[0], heartbeat, workspace=runtime / "tasks"
                        )
                        _notify(state, runtime, task.name, result=result)
                        print(
                            json.dumps({"task": task.name, "exit": result, "failed": result != 0}),
                            flush=True,
                        )
                time.sleep(0.5)
    finally:
        signal.signal(signal.SIGTERM, previous)
