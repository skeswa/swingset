"""Mise entry points for the Postgres development loop."""

from __future__ import annotations

import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from . import services, testing

ROOT = Path(__file__).resolve().parents[3]
CORE_WORKERS = ("-n", "4", "--dist=loadfile", "--no-loadscope-reorder", "--max-worker-restart=0")


def pytest_command(arguments: Sequence[str] = ()) -> list[str]:
    """Keep focused runs quick and the unchanged default core bounded to four workers."""
    explicit_workers = any(
        value == "-n" or value.startswith(("-n", "--numprocesses")) for value in arguments
    )
    selected = any(
        not value.startswith("-")
        and ("::" in value or value.endswith(".py") or Path(value).exists())
        for value in arguments
    )
    core = (
        not selected
        and "--full-suite" not in arguments
        and "--pyargs" not in arguments
        and not explicit_workers
    )
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "swingset.dev.pytest_plugin",
        "--timings",
        *(CORE_WORKERS if core else ()),
        *arguments,
    ]


def _fast_core(arguments: Sequence[str]) -> int | None:
    if os.environ.get("SWINGSET_FAST_CORE", "1") == "1":
        from . import core_runner

        if core_runner.uses_fast_core(arguments):
            return core_runner.run(root=ROOT)
    return None


def _run(arguments: Sequence[str]) -> int:
    started = time.monotonic()
    result = subprocess.run(list(arguments), check=False)
    print(
        f"{arguments[2] if len(arguments) > 2 else arguments[0]}: {time.monotonic() - started:.2f}s",
        flush=True,
    )
    return result.returncode


def _stop(child: subprocess.Popen[bytes]) -> None:
    group = child.pid
    try:
        os.killpg(group, signal.SIGINT)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
    deadline = time.monotonic() + 1
    while services._group_alive(group) and time.monotonic() < deadline:
        time.sleep(0.02)
    # A crashed pytest parent must not leave a helper writing after cancellation.
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def watch(arguments: list[str]) -> int:
    if not any(
        Path(argument.split("::", 1)[0]).exists()
        for argument in arguments
        if not argument.startswith("-")
    ):
        raise ValueError("test-watch requires a pytest file, directory or node selector")
    from watchfiles import watch as changes

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("test-watch needs the pinned uv; run it through mise")
    pending: queue.Queue[object] = queue.Queue()
    stopped = threading.Event()

    def observe() -> None:
        paths = [
            ROOT / path
            for path in ("src", "tests", "config", "overrides", "pyproject.toml", "uv.lock")
        ]
        for _ in changes(*paths, stop_event=stopped, debounce=150, step=50):
            pending.put(True)

    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    child: subprocess.Popen[bytes] | None = None
    requested = True
    try:
        while True:
            if requested:
                if child is not None:
                    _stop(child)
                    testing.clean_dead_runs(owner_group=child.pid)
                    services.clean_dead_lifecycle(owner_group=child.pid)
                while not pending.empty():
                    pending.get_nowait()
                print("Running selected tests", flush=True)
                child = subprocess.Popen(
                    [uv, "run", "python", "-m", "swingset.dev.commands", "test", *arguments],
                    start_new_session=True,
                )
                requested = False
            try:
                pending.get(timeout=0.2)
                requested = True
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        return 130
    finally:
        stopped.set()
        if child is not None:
            _stop(child)
            testing.clean_dead_runs(owner_group=child.pid)
            services.clean_dead_lifecycle(owner_group=child.pid)
        observer.join(timeout=2)


def main(arguments: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not arguments:
        raise ValueError("expected dev, db, test, test-watch or check")
    action, *forwarded = arguments
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    if action in {"dev", "db"}:
        if forwarded:
            raise ValueError(f"{action} takes no arguments")
        started = time.monotonic()
        service = services.ensure_service("test" if action == "dev" else "dev")
        if action == "dev":
            from .seed import ensure_default

            services.ensure_service("physical")
            testing.clean_dead_runs()
            services.clean_dead_lifecycle()
            template = services.ensure_template(service)
            receipt = ensure_default()
            print(
                f"Ready: {service.name}, localhost:{service.port}, template {template}, seed {receipt['name']} ({time.monotonic() - started:.2f}s)"
            )
        else:
            print(
                f"Ready: {service.name}, localhost:{service.port} ({time.monotonic() - started:.2f}s)"
            )
        return 0
    if action == "test":
        print(
            "Pytest selectors: " + (" ".join(forwarded) if forwarded else "reviewed core"),
            flush=True,
        )
        os.environ["SWINGSET_TEST_STARTED"] = str(time.monotonic())
        if (status := _fast_core(forwarded)) is not None:
            return status
        os.execv(
            sys.executable,
            pytest_command(forwarded),
        )
    if action == "test-watch":
        return watch(forwarded)
    if action == "check":
        if forwarded:
            raise ValueError("check takes no arguments; use test for focused selection")
        status = _fast_core(())
        codes = [
            _run(pytest_command()) if status is None else status,
            _run([sys.executable, "-m", "ruff", "check", "."]),
            _run([sys.executable, "-m", "mypy", "src/swingset"]),
        ]
        return 1 if any(codes) else 0
    raise ValueError(f"unknown development command: {action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Development command failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
