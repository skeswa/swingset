"""Supervise one reviewed import without reading database credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Runner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


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
            self.policy["format"] == "swingset-stage2-attended-import-v1"
            and type(self.policy["import_deadline_seconds"]) is int
            and self.policy["import_deadline_seconds"] in {7200, 21600}
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
            "operation": "held full-size import supervisor",
            "at": datetime.now(UTC).isoformat(),
            "complete": False,
            "start_attempted": False,
            "container_id": container,
            "platform_id": platform,
            "policy_sha256": policy_sha256,
            "samples": 0,
        }

    def docker(self, *arguments: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
        return self.runner(["docker", *arguments], timeout)

    def inspect(self, identifier: str) -> dict[str, Any]:
        response = self.docker("inspect", identifier)
        require(response.returncode == 0)
        value: dict[str, Any] = json.loads(response.stdout)[0]
        require(value["Id"] == identifier)
        return value

    def bound(self) -> dict[str, Any]:
        value = self.inspect(self.container)
        labels, host = value["Config"]["Labels"], value["HostConfig"]
        argv = self.policy["import_argv"]
        name = argv[argv.index("--name") + 1]
        networks = value["NetworkSettings"]["Networks"]
        address = argv[argv.index("--ip") + 1]
        network = self.policy["deployment"] + "-private"
        require(
            value["Name"] == "/" + name
            and value["Image"] == self.policy["runtime_image"]
            and labels.get("io.swingset.deployment") == self.policy["deployment"]
            and labels.get("io.swingset.role") == "operator"
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
            "/run/secrets/owner-dsn",
            "/run/secrets/worker-dsn",
            "/run/secrets/control-dsn",
        }
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
        temporary = self.root / "import-live.tmp"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as stream:
            json.dump(self.result, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.root / "import-live.json")

    def stop(self, reason: str) -> None:
        value = self.bound()  # Never stop a container with a changed identity or hardening.
        self.result.setdefault("stop_reason", reason)
        if value["State"]["Running"]:
            self.result["stop_attempted"] = True
            response = self.docker("stop", "--timeout", "120", self.container, timeout=150)
            self.result["stop_exit"] = response.returncode

    def run(self) -> dict[str, Any]:
        reserved = False
        try:
            value = self.bound()
            require(value["State"]["Status"] == "created" and not value["State"]["Running"])
            self.sample()
            descriptor = os.open(
                self.root / "import-watch.started",
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
                self.sample()
                self.result["state"] = {
                    key: value["State"][key] for key in ("Running", "ExitCode", "OOMKilled")
                }
                if not value["State"]["Running"]:
                    break
                if self.clock() - self.started >= self.policy["import_deadline_seconds"]:
                    self.stop(f"{self.policy['import_deadline_seconds']}-second deadline")
                    break
                if self.clock() - last_progress >= 60:
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
                    if response.returncode == 0:
                        progress = json.loads(response.stdout)
                        self.result["import_progress"] = progress
                        preflight = progress.get("preflight", {})
                        signature = (
                            progress["loaded_tables"],
                            preflight.get("phase"),
                            preflight.get("table"),
                        )
                        if signature != native_progress:
                            native_progress = signature
                            last_native_progress = self.clock()
                    else:
                        self.result["progress_probe_failed"] = True
                    last_progress = self.clock()
                self.result["no_native_progress_for_60_minutes"] = (
                    self.clock() - last_native_progress >= 3600
                )
                self.live()
                self.sleep(20)
            final = self.bound()
            self.sample()
            require(
                not final["State"]["Running"]
                and final["State"]["ExitCode"] == 0
                and not final["State"]["OOMKilled"]
                and not self.result.get("stop_reason")
            )
            self.result.update(complete=True, process_succeeded=True)
        except BaseException as error:
            self.result.update(error_type=type(error).__name__, no_automatic_retry=True)
            if self.result["start_attempted"]:
                try:
                    self.stop("supervisor, peer or disk failure")
                except BaseException as failure:
                    self.result["stop_error_type"] = type(failure).__name__
        finally:
            self.result.update(
                seconds=round(self.clock() - self.started, 3),
                finished_at=datetime.now(UTC).isoformat(),
                limits="Exit0 requires separate complete native audit and stored-identity postchecks.",
            )
            if reserved:
                self.live()
                descriptor = os.open(
                    self.root / "import-supervisor-result.json",
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
