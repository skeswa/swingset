"""Supervise one reviewed import without reading database credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Runner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


@contextmanager
def interruptions() -> Generator[None, None, None]:
    def interrupted(signum: int, frame: Any) -> None:
        raise InterruptedError("utility supervisor interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def command(arguments: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)


def require(condition: object) -> None:
    if not condition:
        raise ValueError("reviewed import job refused")


def private_json(path: Path, expected_sha256: str) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1
            and info.st_size <= 128 * 1024
        )
        raw = stream.read(128 * 1024 + 1)
    require(len(raw) <= 128 * 1024)
    require(hashlib.sha256(raw).hexdigest() == expected_sha256)
    value: dict[str, Any] = json.loads(raw)
    require(isinstance(value, dict))
    return value


def app_state(container: dict[str, Any]) -> dict[str, Any]:
    state = container["State"]
    return {
        "id": container["Id"],
        "name": container["Name"],
        "image": container["Image"],
        "running": state["Running"],
        "oom": state["OOMKilled"],
        "restart": container["RestartCount"],
        "health": state.get("Health", {}).get("Status"),
    }


class ImportJob:
    """Own one already-created utility; retain its partial state on every failure."""

    FORMAT = "swingset-stage2-attended-import-v1"
    DEADLINE = "import_deadline_seconds"
    DEADLINES = frozenset({7200, 21600})
    ARGV = "import_argv"
    ROLE = "operator"
    CREDENTIALS = frozenset({"owner-dsn", "worker-dsn", "control-dsn"})
    PREFIX = "import"
    OPERATION = "held full-size import supervisor"
    ALARM = "no_native_progress_for_60_minutes"
    LIMITS = "Exit0 requires separate complete native audit and stored-identity postchecks."
    STATE_READ_ONLY = False

    def __init__(
        self,
        policy: Path,
        policy_sha256: str,
        container: str,
        platform: str,
        *,
        runner: Runner = command,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        free: Callable[[str], int] = lambda path: shutil.disk_usage(path).free,
    ) -> None:
        require(re.fullmatch(r"[0-9a-f]{64}", policy_sha256))
        self.policy = private_json(policy, policy_sha256)
        require(
            self.policy["format"] == self.FORMAT
            and type(self.policy[self.DEADLINE]) is int
            and self.policy[self.DEADLINE] in self.DEADLINES
            and self.policy["minimum_free_bytes"] == 200 * 10**9
            and re.fullmatch(r"swingset-stage2-app-[0-9a-f]{12}", self.policy["deployment"])
            and re.fullmatch(r"sha256:[0-9a-f]{64}", self.policy["runtime_image"])
            and re.fullmatch(r"[0-9a-f]{64}", self.policy["source_sha256"])
            and len(self.policy["seven_original_apps"]) == 7
            and len({item["id"] for item in self.policy["seven_original_apps"]}) == 7
        )
        for identifier in (container, platform):
            require(re.fullmatch(r"[0-9a-f]{64}", identifier))
        self.root = Path(self.policy["root"])
        info = self.root.lstat()
        require(
            self.root.is_absolute()
            and self.root.resolve() == self.root
            and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700
        )
        self.container, self.platform = container, platform
        self.runner, self.clock, self.sleep, self.free = runner, clock, sleep, free
        self.started = clock()
        self.result: dict[str, Any] = {
            "operation": self.OPERATION,
            "at": datetime.now(UTC).isoformat(),
            "complete": False,
            "start_attempted": False,
            "container_id": container,
            "platform_id": platform,
            "policy_sha256": policy_sha256,
            "samples": 0,
        }

    def docker(self, *arguments: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(["docker", *arguments], timeout)
        except (OSError, subprocess.SubprocessError) as error:
            pinned = {self.container, self.platform}
            for name in ("seven_original_apps", "support5"):
                pinned.update(item["id"] for item in self.policy.get(name, []))
            pinned.update(self.policy.get("postgres_ids", {}).values())
            if self.policy.get("native_container"):
                pinned.add(self.policy["native_container"])
            target = next((argument for argument in arguments[1:] if argument in pinned), None)
            self.result.setdefault(
                "docker_command_failure",
                {
                    "action": arguments[0],
                    "target": target,
                    "timeout_seconds": timeout,
                    "error_type": type(error).__name__,
                },
            )
            raise

    def inspect(self, identifier: str) -> dict[str, Any]:
        response = self.docker("inspect", identifier)
        require(response.returncode == 0)
        value: dict[str, Any] = json.loads(response.stdout)[0]
        require(value["Id"] == identifier)
        return value

    def bound(self) -> dict[str, Any]:
        value = self.inspect(self.container)
        labels, host = value["Config"]["Labels"], value["HostConfig"]
        argv = self.policy[self.ARGV]
        name = argv[argv.index("--name") + 1]
        networks = value["NetworkSettings"]["Networks"]
        address = argv[argv.index("--ip") + 1]
        network = self.policy["deployment"] + "-private"
        require(
            value["Name"] == "/" + name
            and value["Image"] == self.policy["runtime_image"]
            and labels.get("io.swingset.deployment") == self.policy["deployment"]
            and labels.get("io.swingset.role") == self.ROLE
            and labels.get("io.swingset.guarded") == "true"
            and value["Config"]["User"] == "10001:10001"
            and host["ReadonlyRootfs"]
            and not host["Privileged"]
            and not host.get("CapAdd")
            and "ALL" in (host.get("CapDrop") or [])
            and any(s.startswith("no-new-privileges") for s in host.get("SecurityOpt") or [])
            and host.get("PidMode") in {None, ""}
            and host["Memory"] == 8 * 1024**3
            and host["MemorySwap"] == 8 * 1024**3
            and host["NanoCpus"] == 6 * 10**9
            and host["PidsLimit"] == 256
            and value["Config"]["Entrypoint"] == ["python"]
            and value["Config"]["Cmd"] == argv[argv.index(self.policy["runtime_image"]) + 1 :]
            and set(networks) == {network}
            and networks[network].get("IPAMConfig", {}).get("IPv4Address") == address
            and (not value["State"]["Running"] or networks[network]["IPAddress"] == address)
            and host["Dns"] == [argv[argv.index("--dns") + 1]]
        )
        writable = {
            "/var/lib/swingset",
            "/var/lib/swingset-spill",
            "/var/lib/swingset-runtime",
            "/run/swingset-checker",
        }
        readonly = {
            "/run/swingset-config",
            "/run/swingset-authorization",
            "/run/swingset-holds",
            "/var/lib/swingset-source",
        }
        readonly.update("/run/secrets/" + name for name in self.CREDENTIALS)
        if self.STATE_READ_ONLY:
            writable.remove("/var/lib/swingset")
            readonly.add("/var/lib/swingset")
        require(
            {item["Destination"] for item in value["Mounts"] if item["RW"]} == writable
            and {item["Destination"] for item in value["Mounts"] if not item["RW"]} == readonly
            and all("docker.sock" not in item["Source"] for item in value["Mounts"])
        )
        return value

    def sample(self) -> None:
        peers = [app_state(self.inspect(item["id"])) for item in self.policy["seven_original_apps"]]
        require(peers == self.policy["seven_original_apps"])
        platform = self.inspect(self.platform)
        require(
            platform["Image"]
            == "sha256:824e5ac0bc1ec5e13193141522a3550eac38830847aa4a6ae14eafb2144aab68"
            and platform["Config"]["Labels"].get("io.swingset.deployment")
            == self.policy["deployment"]
            and platform["State"]["Running"]
            and not platform["State"]["OOMKilled"]
        )
        available = min(self.free("/var/tmp"), self.free("/var/lib/docker"))
        require(available >= self.policy["minimum_free_bytes"])
        self.result.update(
            samples=self.result["samples"] + 1,
            original7_apps_unchanged=True,
            last_sample_at=datetime.now(UTC).isoformat(),
            minimum_free_observed=min(
                self.result.get("minimum_free_observed", available), available
            ),
            elapsed_seconds=round(self.clock() - self.started, 1),
        )

    def live(self) -> None:
        temporary = self.root / (self.PREFIX + "-live.tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as stream:
            json.dump(self.result, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.root / (self.PREFIX + "-live.json"))

    def progress(self) -> tuple[Any, ...] | None:
        script = (
            "import json;from pathlib import Path;"
            "p=Path('/var/lib/swingset-runtime/operator/import.json');"
            "r=json.loads(p.read_bytes()) if p.exists() and p.stat().st_size<2*1024**2 else {};"
            "p=p.with_name('import-preflight.json');"
            "v=json.loads(p.read_bytes()) if p.exists() and p.stat().st_size<4096 else {};"
            "print(json.dumps({'receipt_exists':bool(r),'complete':r.get('complete',False),"
            "'loaded_tables':len(r.get('loaded',{})),"
            "'preflight':v,"
            "'loaded_rows':sum(v.get('rows',0) for v in r.get('loaded',{}).values())}))"
        )
        response = self.docker("exec", self.container, "python", "-c", script)
        if response.returncode:
            self.result["progress_probe_failed"] = True
            return None
        progress = json.loads(response.stdout)
        self.result["import_progress"] = progress
        preflight = progress.get("preflight", {})
        return progress["loaded_tables"], preflight.get("phase"), preflight.get("table")

    def final_check(self) -> None:
        """Specialized utilities may require an observed startup identity too."""

    def stop(self, reason: str) -> None:
        value = self.bound()  # Never stop a container with a changed identity or hardening.
        self.result.setdefault("stop_reason", reason)
        if value["State"]["Running"]:
            if self.result.get("stop_attempted"):
                self.result["requires_attended_escalation"] = True
                raise ValueError("utility stop outcome remains unknown")
            self.result["stop_attempted"] = True
            response = self.docker("stop", "--timeout", "120", self.container, timeout=150)
            self.result["stop_exit"] = response.returncode
            final = self.bound()
            terminal = not final["State"]["Running"] and final["State"]["Pid"] == 0
            if response.returncode != 0 or not terminal:
                self.result["requires_attended_escalation"] = True
                raise ValueError("utility stop is not confirmed")
            self.result["stop_confirmed"] = True
            value = final
            self.result["stopped_state"] = {
                key: final["State"][key]
                for key in ("Running", "Pid", "ExitCode", "OOMKilled", "StartedAt", "FinishedAt")
                if key in final["State"]
            }
        elif value["State"]["Status"] == "created" and self.result["start_attempted"]:
            self.result.update(start_outcome="unknown", requires_attended_escalation=True)
        if not value["State"]["Running"] and value["State"]["Status"] != "created":
            self.result["state"] = {
                key: value["State"][key]
                for key in ("Running", "Pid", "ExitCode", "OOMKilled", "StartedAt", "FinishedAt")
                if key in value["State"]
            }
            self.result["state_observed_at"] = datetime.now(UTC).isoformat()
            self.result["terminal_state"] = "confirmed"

    def run(self) -> dict[str, Any]:
        reserved = False
        try:
            value = self.bound()
            require(value["State"]["Status"] == "created" and not value["State"]["Running"])
            self.sample()
            descriptor = os.open(
                self.root / (self.PREFIX + "-watch.started"),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            os.close(descriptor)
            reserved = True
            self.result["start_attempted"] = True
            self.live()
            require(self.docker("start", self.container, timeout=30).returncode == 0)
            last_progress = -60.0
            last_native_progress = self.started
            native_progress = None
            while True:
                value = self.bound()
                observed_at = datetime.now(UTC).isoformat()
                self.sample()
                self.result["state"] = {
                    key: value["State"][key]
                    for key in ("Running", "ExitCode", "OOMKilled", "Pid", "StartedAt")
                    if key in value["State"]
                }
                self.result["state_observed_at"] = observed_at
                if not value["State"]["Running"]:
                    break
                if self.clock() - self.started >= self.policy[self.DEADLINE]:
                    self.stop(f"{self.policy[self.DEADLINE]}-second deadline")
                    break
                if self.clock() - last_progress >= 60:
                    signature = self.progress()
                    if signature is not None and signature != native_progress:
                        native_progress = signature
                        last_native_progress = self.clock()
                    last_progress = self.clock()
                self.result[self.ALARM] = self.clock() - last_native_progress >= 3600
                self.live()
                self.sleep(20)
            final = self.bound()
            self.sample()
            self.result["final_state"] = {
                key: final["State"][key]
                for key in ("Running", "Pid", "ExitCode", "OOMKilled", "StartedAt", "FinishedAt")
                if key in final["State"]
            }
            require(
                not final["State"]["Running"]
                and final["State"]["ExitCode"] == 0
                and not final["State"]["OOMKilled"]
                and not self.result.get("stop_reason")
            )
            self.final_check()
            self.result.update(complete=True, process_succeeded=True)
        except BaseException as error:
            self.result.update(error_type=type(error).__name__, no_automatic_retry=True)
            if self.result["start_attempted"]:
                self.result["terminal_state"] = "unknown"
                if "state" in self.result:
                    self.result["last_observed_state"] = self.result.pop("state")
                    self.result["last_observed_at"] = self.result.pop("state_observed_at", None)
                try:
                    self.stop("supervisor, peer or disk failure")
                except BaseException as failure:
                    self.result["stop_error_type"] = type(failure).__name__
                    self.result["requires_attended_escalation"] = True
        finally:
            self.result.update(
                seconds=round(self.clock() - self.started, 3),
                finished_at=datetime.now(UTC).isoformat(),
                limits=self.LIMITS,
            )
            if reserved:
                self.live()
                descriptor = os.open(
                    self.root / (self.PREFIX + "-supervisor-result.json"),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(self.result, stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
        return self.result


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--platform", required=True)


def execute(args: argparse.Namespace) -> int:
    try:
        with interruptions():
            result = ImportJob(args.plan, args.plan_sha256, args.container, args.platform).run()
    except BaseException as error:
        result = {
            "complete": False,
            "mutation_attempted": None,
            "mutation_outcome": "unknown",
            "no_automatic_retry": True,
            "error_type": type(error).__name__,
        }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    raise SystemExit(execute(parser.parse_args()))
