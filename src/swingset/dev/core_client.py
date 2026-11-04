"""Supervise one private Linux test namespace and retain failed payloads."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_report(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def _owned_processes() -> list[int]:
    entries: list[tuple[int, int, int]] = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit() or int(path.name) == 1:
            continue
        try:
            values = dict(
                line.split(":", 1)
                for line in (path / "status").read_text().splitlines()
                if ":" in line
            )
            entries.append((int(path.name), int(values["PPid"]), int(values["Uid"].split()[0])))
        except (OSError, ValueError, KeyError):
            continue
    descendants = {1}
    while True:
        found = {
            pid for pid, parent, uid in entries if parent in descendants and uid == os.getuid()
        }
        if found <= descendants:
            return sorted(descendants - {1})
        descendants.update(found)


def _reap() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if not pid:
                return
        except ChildProcessError:
            return


def _stop_helpers() -> list[int]:
    # Called only by PID1 in this dedicated client namespace, never the DB namespace.
    _reap()
    for pid in _owned_processes():
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 0.3
    while _owned_processes() and time.monotonic() < deadline:
        _reap()
        time.sleep(0.02)
    for pid in _owned_processes():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for _ in range(20):
        _reap()
        remaining = _owned_processes()
        if not remaining:
            return []
        time.sleep(0.05)
    return _owned_processes()


def _copy_payload(source: Path, target: Path) -> dict[str, Any]:
    """Copy regular files and symlink entries without opening special files."""
    target.mkdir(mode=0o700)
    files = size = 0
    special: list[str] = []
    for entry in sorted(source.iterdir()):
        destination = target / entry.name
        mode = entry.lstat().st_mode
        if stat.S_ISLNK(mode):
            destination.symlink_to(os.readlink(entry))
        elif stat.S_ISDIR(mode):
            result = _copy_payload(entry, destination)
            files += result["files"]
            size += result["bytes"]
            special.extend(str(Path(entry.name) / name) for name in result["special"])
        elif stat.S_ISREG(mode):
            descriptor = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as incoming:
                if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                    raise RuntimeError("Failure payload changed while exporting")
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
            files += 1
            size += destination.stat().st_size
        else:
            special.append(entry.name)
    _sync_directory(target)
    return {"files": files, "bytes": size, "special": special}


def _sync_payload(path: Path) -> dict[str, Any]:
    files = size = 0
    special: list[str] = []
    for entry in sorted(path.iterdir()):
        mode = entry.lstat().st_mode
        if stat.S_ISDIR(mode):
            result = _sync_payload(entry)
            files += result["files"]
            size += result["bytes"]
            special.extend(str(Path(entry.name) / name) for name in result["special"])
        elif stat.S_ISREG(mode):
            descriptor = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                found = os.fstat(descriptor)
                if not stat.S_ISREG(found.st_mode):
                    raise RuntimeError("Retained capacity payload changed")
                os.fsync(descriptor)
                files += 1
                size += found.st_size
            finally:
                os.close(descriptor)
        else:
            special.append(entry.name)
    _sync_directory(path)
    return {"files": files, "bytes": size, "special": special}


def export_failures(root: Path = Path("/tmp")) -> dict[str, Any]:
    result: dict[str, Any] = {"required": True, "complete": True, "persistent_capacity": []}
    source = root / "pytest-of-swingset"
    if source.is_dir() and not source.is_symlink():
        result.update(_copy_payload(source, root / "failure-artifacts"))
    for directory in sorted(root.glob("swingset-capacity-*")):
        if directory.is_symlink() or not directory.is_dir():
            result["persistent_capacity"].append({"directory": directory.name, "special": True})
        else:
            result["persistent_capacity"].append(
                {"directory": directory.name, **_sync_payload(directory)}
            )
    _sync_directory(root)
    return result


def measure() -> dict[str, Any]:
    root = Path("/sys/fs/cgroup")
    stats = {
        k: int(v)
        for k, v in (line.split() for line in (root / "memory.stat").read_text().splitlines())
    }
    current = int((root / "memory.current").read_text())
    value: dict[str, Any] = {
        "current": current,
        "peak_total": int((root / "memory.peak").read_text()),
        "working": max(0, current - stats.get("inactive_file", 0)),
        "anon": stats.get("anon"),
        "file": stats.get("file"),
        "swap": int((root / "memory.swap.current").read_text()),
        "events": {
            k: int(v)
            for k, v in (line.split() for line in (root / "memory.events").read_text().splitlines())
        },
    }
    payload = Path("/tmp/pytest-of-swingset")
    if payload.exists():
        found = os.statvfs(payload)
        value["test_tmpfs"] = {
            "bytes_used": (found.f_blocks - found.f_bfree) * found.f_frsize,
            "bytes_total": found.f_blocks * found.f_frsize,
            "inodes_used": found.f_files - found.f_ffree,
            "inodes_total": found.f_files,
        }
    return value


def read_reports(root: Path) -> dict[str, Any]:
    names = ("core-client-summary.json", "core-phases.json", "core-results.json")
    result = {
        name: json.loads((root / name).read_text()) if (root / name).is_file() else None
        for name in names
    }
    result["partitions"] = {
        p.name: json.loads(p.read_text()) for p in (root / "partition-reports").glob("*.json")
    }
    return result


def pytest_command(partition: str) -> list[str]:
    """Record the effective command for each disjoint execution phase."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *(
            []
            if partition == "prologue"
            else [
                "-n",
                "4",
                "--dist=loadfile",
                "--no-loadscope-reorder",
                "--max-worker-restart=0",
            ]
        ),
        "--timings",
        "--durations=40",
        "-p",
        "swingset.dev.core_receipt",
        "--swingset-core-phase=" + ("prologue" if partition == "prologue" else "parallel"),
        *([] if partition == "prologue" else ["--swingset-partition=" + partition]),
    ]


def reap_copies(primary: Any, physical: Any = None, *, registry_root: Path | None = None) -> int:
    """Reap only exact dead owners, checking both configured server endpoints."""
    from swingset.state.postgres_copy import Copies

    from . import services

    endpoints = [primary, *([] if physical is None else [physical])]
    for service in endpoints:
        services._validate_service(service)
        with Copies.for_admin(service.dsn(), registry_root=registry_root):
            pass
    return len(endpoints)


def run(partition: str) -> int:
    if (
        sys.platform != "linux"
        or os.getpid() != 1
        or os.getuid() != 10001
        or not Path("/.dockerenv").is_file()
    ):
        raise RuntimeError("The test supervisor requires its dedicated UID10001 Linux container")
    requested: list[int] = []
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda number, frame: requested.append(number))
    started = time.monotonic()
    command = pytest_command(partition)
    child = subprocess.Popen(
        command, env={**os.environ, "SWINGSET_TEST_STARTED": str(started)}, start_new_session=True
    )
    forwarded = None
    while child.poll() is None:
        if requested and forwarded is None:
            forwarded = time.monotonic()
            try:
                os.killpg(child.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        if forwarded is not None and time.monotonic() - forwarded > 20:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.1)
    status = child.wait()
    remaining = _stop_helpers()
    try:
        from . import services, testing

        reaped = testing.clean_dead_runs()
        endpoints = reap_copies(
            services.ensure_service(), services.configured_ci_lifecycle_service()
        )
        records = list((services.home() / "test-runs").glob("*.json"))
        cleanup = {
            "test_runs_reaped": reaped,
            "copies_endpoints_reaped": endpoints,
            "remaining_run_records": len(records),
            "remaining_helpers": remaining,
            "complete": not records and not remaining,
        }
    except Exception as error:
        cleanup = {"complete": False, "error_type": type(error).__name__}
    exported = {"required": False, "complete": True}
    if status or not cleanup["complete"]:
        try:
            exported = export_failures()
        except Exception as error:
            exported = {"required": True, "complete": False, "error_type": type(error).__name__}
    summary = {
        "pytest_exit_code": status,
        "interrupted_signals": requested,
        "pytest_command": command,
        "phase": "prologue" if partition == "prologue" else "parallel",
        "seconds_including_cleanup": time.monotonic() - started,
        "cleanup": cleanup,
        "failure_artifact_export": exported,
        "pid_namespace": os.readlink("/proc/self/ns/pid"),
        "uid": os.getuid(),
    }
    _write_report(Path("/tmp/core-client-summary.json"), summary)
    print(
        "Core client: "
        + json.dumps({"partition": partition, "exit_code": status, "cleanup": cleanup["complete"]}),
        flush=True,
    )
    return status if status else (0 if cleanup["complete"] and exported["complete"] else 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "measure", "reports"))
    parser.add_argument("argument", nargs="?")
    arguments = parser.parse_args()
    if arguments.action == "run":
        if (
            not arguments.argument
            or arguments.argument != "prologue"
            and (
                not re.fullmatch(r"[0-3]/[234]", arguments.argument)
                or int(arguments.argument[0]) >= int(arguments.argument[2])
            )
        ):
            parser.error("run requires the prologue or a valid partition")
        return run(arguments.argument)
    print(
        json.dumps(
            measure()
            if arguments.action == "measure"
            else read_reports(Path(arguments.argument or "/probe")),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
