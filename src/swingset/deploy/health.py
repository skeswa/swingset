"""Read-only health checks, bounded webhook delivery and independent liveness.

Only the dedicated watchdog pings the dead-man endpoint. A successful ping
means checks ran and their alerts were delivered; it does not erase an alarm.
Scheduler callbacks cannot keep a dead watchdog looking alive.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import platform
import re
import shutil
import signal
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

HOUR = 3600


@dataclass(frozen=True)
class Report:
    utc: float
    alert_keys: tuple[str, ...]
    delivery_failures: tuple[str, ...] = ()

    def safe_dict(self) -> dict[str, Any]:
        return {
            "utc": self.utc,
            "group": "health",
            "failed": bool(self.alert_keys or self.delivery_failures),
            "alert_keys": list(self.alert_keys),
            "delivery_failures": list(self.delivery_failures),
        }


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("invalid monitoring record")
    return value


def _at(value: str | None) -> float | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("monitoring requires an aware timestamp")
    return result.timestamp()


def _database_signals(state: Path, now: float) -> set[str]:
    from swingset.ops import watchdog as legacy

    keys: set[str] = set()
    connection = legacy._connect(str(state))
    try:
        with connection.read_snapshot():
            requests = [
                _at(connection.execute(legacy.QUERIES[name]).fetchone()[0])
                for name in ("checked", "issued")
            ]
            latest_request = max((stamp for stamp in requests if stamp is not None), default=0)
            if now - latest_request > HOUR:
                keys.add("fetch-idle")
            progress = []
            for failed, error, stamp in connection.execute(
                "SELECT failed,error,last_progress_at FROM loop_state"
            ):
                if failed or error:
                    keys.add("run-failed")
                parsed = _at(stamp)
                if parsed is not None:
                    progress.append(parsed)
            if now - max(progress, default=0) > HOUR:
                keys.add("progress-idle")
            for until, created in connection.execute(
                "SELECT until_at,created_at FROM operator_pauses"
            ):
                if until is None or (_at(until) or 0) > now:
                    keys.add("paused")
                    if created is None or now - (_at(created) or 0) > HOUR:
                        keys.add("hold-overdue")
    finally:
        connection.close()
    return keys


def _signals(state: Path, now: float, *, full: bool) -> set[str]:
    from swingset.ops import watchdog as legacy

    keys: set[str] = set()
    try:
        keys.update(_database_signals(state, now))
        # Durable run files also cover a crash before loop_state was committed.
        records = legacy._loop_records(str(state), now)
        if any(record.get("failed") or record.get("error") for record in records.latest.values()):
            keys.add("run-failed")
    except Exception:
        keys.add("health-blind")
    if full:
        lines = legacy.run(
            [
                "--state",
                str(state),
                "--stale-fetch",
                str(HOUR),
                "--stale-progress",
                str(HOUR),
                "--repeat",
                "1800",
                "--disk-min-free",
                "100000000000",
                "--backup-upload",
                "1",
                "--backup-segmented",
                "1",
                "--backup-max-age",
                str(5 * 86400),
                "--now",
                datetime.fromtimestamp(now, UTC).isoformat(),
            ]
        )
        for line in lines:
            match = re.match(r"\[([a-z][a-z0-9-]*)\]", line)
            keys.add(match[1] if match else "watchdog-error")
    return keys


def _platform_signals(runtime: Path, now: float | None) -> set[str]:
    keys: set[str] = set()
    observed_at = time.time() if now is None else now
    path = Path(
        os.environ.get("SWINGSET_CONTAINER_STATUS", "/run/swingset-authorization/containers.json")
    )
    try:
        value = _read(path)
        observed_at = time.time() if now is None else now
        if not value or not 0 <= observed_at - float(value["utc"]) <= 120:
            keys.add("platform-status-stale")
        if value.get("complete_inventory") is not True or not value.get("containers"):
            keys.add("platform-incomplete")
        running_roles = {item["role"] for item in value.get("containers", []) if item["running"]}
        for container in value.get("containers", []):
            if container.get("oom_killed"):
                keys.add("container-failed")
            if not container["running"] and container["role"] in running_roles:
                continue
            if (
                not container["running"]
                or container.get("oom_killed")
                or container.get("health") == "unhealthy"
            ):
                keys.add("container-failed")
        if value.get("resource_exit", 0) != 0:
            keys.add("platform-observer-blind")
        if any(
            item.get("running") and item.get("health") == "unhealthy"
            for item in value.get("other_containers", [])
        ):
            keys.add("platform-other-unhealthy")
        for tick_path in runtime.parent.glob("*/tick.json"):
            tick = _read(tick_path)
            if tick.get("exit_code") not in (None, 0):
                keys.add("task-failed")
            if tick.get("held") and observed_at - float(tick["hold_since"]) > HOUR:
                keys.add("hold-overdue")
    except (OSError, ValueError, TypeError, KeyError):
        keys.add("platform-status-invalid")
    holds = Path(os.environ.get("SWINGSET_HOLDS_DIR", "/run/swingset-holds"))
    try:
        if holds.exists() and any(
            observed_at - path.stat().st_mtime > HOUR for path in holds.iterdir()
        ):
            keys.add("hold-overdue")
    except OSError:
        keys.add("health-blind")
    return keys


def _endpoint(variable: str) -> str:
    path = Path(os.environ[variable])
    if path.stat().st_mode & 0o077:
        raise ValueError("notification secrets must be private")
    target = path.read_text().strip()
    parsed = urlsplit(target)
    local = False
    try:
        local = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        pass
    if (
        parsed.scheme != "https"
        and not (parsed.scheme == "http" and local)
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("invalid notification endpoint")
    return target


def _post(target: str, body: bytes) -> None:
    headers = {
        "User-Agent": f"swingset/{version('swingset')} (+https://github.com/skeswa/swingset)",
        "Content-Type": "application/json",
    }
    # Never follow a webhook redirect or read its potentially private body.
    with httpx.Client(timeout=5, follow_redirects=False) as client:
        with client.stream("POST", target, content=body, headers=headers) as response:
            if not 200 <= response.status_code < 300:
                raise RuntimeError("notification delivery failed")


def _publish(
    state: Path,
    runtime: Path,
    keys: set[str],
    now: float | None,
    *,
    deadman: bool,
    observe_state: bool = True,
    deliver: bool = True,
) -> Report:
    live_clock = now is None
    now = time.time() if now is None else now
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        cursor = _read(runtime / "health.json")
        notified = cursor.get("notified_at", 0)
        if (
            type(notified) not in (int, float)
            or not math.isfinite(notified)
            or not 0 <= notified <= now + 300
        ):
            raise ValueError("invalid monitoring clock")
    except (OSError, ValueError):
        cursor = {}
        keys.add("health-state-invalid")
    log = state / "ALERTS.log"
    if observe_state and log.exists():
        info = log.stat()
        if info.st_size and (
            cursor.get("log_inode") != info.st_ino or cursor.get("log_size") != info.st_size
        ):
            keys.add("alerts-appended")
        cursor.update(log_inode=info.st_ino, log_size=info.st_size)
    failures: list[str] = []
    alert = ping = ""
    if deliver:
        try:
            alert = _endpoint("SWINGSET_ALERT_URL_FILE")
            ping = _endpoint("SWINGSET_DEADMAN_URL_FILE")
        except (OSError, ValueError, KeyError):
            failures.append("notification-unconfigured")
    signature = hashlib.sha256(json.dumps(sorted(keys)).encode()).hexdigest()
    notify = bool(keys) and (
        signature != cursor.get("signature") or now - cursor.get("notified_at", 0) >= 1800
    )
    if notify and alert:
        text = "Swingset health: " + ", ".join(sorted(keys))
        body = json.dumps(
            {"text": text, "content": text, "utc": now, "alert_keys": sorted(keys)}
        ).encode()
        try:
            _post(alert, body)
            cursor["notified_at"] = now
        except Exception:
            failures.append("alert-delivery-failed")
    if (
        deadman
        and ping
        and not failures
        and not keys.intersection({"health-blind", "watchdog-blind", "watchdog-error"})
    ):
        try:
            _post(ping, b"{}")
        except Exception:
            failures.append("deadman-delivery-failed")
    if notify or failures:
        with (runtime / "ALERTS.log").open("a") as handle:
            for key in sorted(keys | set(failures)):
                handle.write(
                    f"{datetime.fromtimestamp(now, UTC).isoformat()} [{key}] deployment health\n"
                )
    completed_at = time.time() if live_clock else now
    cursor.update(signature=signature, utc=completed_at)
    _write(runtime / "health.json", cursor)
    report = Report(completed_at, tuple(sorted(keys)), tuple(failures))
    _write(runtime / ("heartbeat.json" if deadman else "tick-health.json"), report.safe_dict())
    return report


def watchdog(state: Path, *, runtime: Path | None = None, now: float | None = None) -> Report:
    stamp = time.time() if now is None else now
    runtime = runtime or Path("/var/lib/swingset-runtime/health")
    keys = _signals(state, stamp, full=True)
    keys.update(_platform_signals(runtime, now))
    return _publish(state, runtime, keys, now, deadman=True)


def notify_tick(
    state: Path,
    runtime: Path,
    task: str,
    *,
    exit_code: int | None = None,
    held: bool = False,
    now: float | None = None,
) -> Report:
    stamp = time.time() if now is None else now
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,50}", task):
        raise ValueError("invalid task name")
    invalid = False
    try:
        previous = _read(runtime / "tick.json")
        hold_since = previous["hold_since"] if previous.get("held") and held else stamp
        if (
            type(hold_since) not in (int, float)
            or not math.isfinite(hold_since)
            or not 0 <= hold_since <= stamp
        ):
            raise ValueError("invalid hold clock")
    except (OSError, ValueError, KeyError):
        invalid = True
        hold_since = stamp - HOUR - 1
    _write(
        runtime / "tick.json",
        {
            "utc": stamp,
            "task": task,
            "exit_code": exit_code,
            "held": held,
            "hold_since": hold_since,
        },
    )
    keys = set() if held else _signals(state, stamp, full=False)
    if invalid:
        keys.add("health-state-invalid")
    if exit_code not in (None, 0):
        keys.add("task-failed")
    if held and stamp - hold_since > HOUR:
        keys.add("hold-overdue")
    return _publish(
        state, runtime, keys, stamp, deadman=False, observe_state=not held, deliver=not held
    )


def _pid_namespace() -> str:
    try:
        return str(Path("/proc/self/ns/pid").stat().st_ino)
    except FileNotFoundError:
        return "host:" + platform.node()


def create_task_directory(root: Path, *, now: float | None = None) -> Path:
    """Register a disposable task before work; retained data never belongs here."""
    if root.name != "tasks" or root.is_symlink():
        raise ValueError("disposable tasks require an explicit tasks directory")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = root / ("task-" + uuid4().hex)
    directory.mkdir(mode=0o700)
    info = directory.stat()
    _write(
        directory / "owner.json",
        {
            "format": 1,
            "kind": "swingset-disposable-task",
            "root": str(root.resolve()),
            "uid": info.st_uid,
            "device": info.st_dev,
            "inode": info.st_ino,
            "pid": os.getpid(),
            "process_group": os.getpgrp(),
            "pid_namespace": _pid_namespace(),
            "created_at": time.time() if now is None else now,
        },
    )
    return directory


def cleanup_task_directories(
    root: Path, *, older_than: float, now: float | None = None
) -> list[str]:
    """Remove only registered, expired tasks whose exact same-namespace owner died.

    Unknown directories and old container namespaces remain for platform-owner
    inspection. This never searches global temporary files or Docker resources.
    The caller, rather than this helper, chooses the minimum age.
    """
    stamp = time.time() if now is None else now
    if not math.isfinite(older_than) or older_than < 0 or root.name != "tasks" or root.is_symlink():
        raise ValueError("invalid disposable-task cleanup scope")
    if not root.exists():
        return []
    parent = root.stat()
    if parent.st_uid != os.getuid():
        raise ValueError("disposable-task root belongs to another owner")
    removed: list[str] = []
    for directory in root.iterdir():
        if not re.fullmatch(r"task-[a-f0-9]{32}", directory.name) or directory.is_symlink():
            continue
        try:
            info = directory.stat()
            record_path = directory / "owner.json"
            if not directory.is_dir() or record_path.is_symlink():
                continue
            owner = _read(record_path)
            identity = (info.st_uid, info.st_dev, info.st_ino)
            if (
                owner.get("format") != 1
                or owner.get("kind") != "swingset-disposable-task"
                or owner.get("root") != str(root.resolve())
                or identity != (owner.get("uid"), owner.get("device"), owner.get("inode"))
                or info.st_uid != os.getuid()
                or info.st_dev != parent.st_dev
                or owner.get("pid_namespace") != _pid_namespace()
                or type(owner.get("pid")) is not int
                or owner["pid"] <= 0
                or type(owner.get("process_group")) is not int
                or owner["process_group"] <= 0
                or not 0 <= older_than <= stamp - float(owner["created_at"])
            ):
                continue
            try:
                os.kill(owner["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                continue
            try:
                os.killpg(owner["process_group"], 0)
            except ProcessLookupError:
                pass
            else:
                continue
            again = directory.lstat()
            if directory.is_symlink() or (again.st_uid, again.st_dev, again.st_ino) != identity:
                continue
            shutil.rmtree(directory)
            removed.append(directory.name)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return removed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["watchdog"])
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--runtime", type=Path, default=Path("/var/lib/swingset-runtime/health"))
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= HOUR:
        parser.error("watchdog reports must be between one second and one hour apart")
    stopped = threading.Event()
    previous = signal.signal(signal.SIGTERM, lambda _signum, _frame: stopped.set())
    try:
        while not stopped.is_set():
            started = time.monotonic()
            report = watchdog(args.state, runtime=args.runtime)
            print(json.dumps(report.safe_dict()), flush=True)
            if not args.watch or stopped.wait(max(0, args.interval - (time.monotonic() - started))):
                break
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
