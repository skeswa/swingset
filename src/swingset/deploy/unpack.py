"""Supervise one offline SQLite checkpoint unpack without application credentials.

This module also runs as a standalone stdlib-only server program. The reviewed
JSON policy pins the transport, image and original services; it cannot supply
Docker arguments or shell commands. Every result and partial output is retained.
"""

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
from pathlib import Path
from typing import Any

LABEL = "io.swingset.unpack-job"
MEMORY = 2 * 1024**3
MINIMUM_FREE = 200_000_000_000
DEADLINE = 7200
ALLOWED_ENVIRONMENT = {
    "PATH",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHON_VERSION",
    "PYTHON_SHA256",
    "GPG_KEY",
    "UV_PYTHON_DOWNLOADS",
    "UV_LINK_MODE",
    "SWINGSET_DEPENDENCY_IMAGE",
    "SWINGSET_REVISION",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "UV_PROJECT_ENVIRONMENT",
    "UV_NO_SYNC",
    "HOME",
    "TMPDIR",
    "SQLITE_TMPDIR",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "ARROW_NUM_THREADS",
    "MALLOC_ARENA_MAX",
}


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _keys(value: Any, names: set[str]) -> None:
    _require(isinstance(value, dict) and set(value) == names, "Unexpected unpack policy fields")


def _sha(value: Any, prefix: str = "") -> None:
    _require(
        isinstance(value, str) and re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", value),
        "Invalid immutable unpack identity",
    )


def _identity(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    return {
        "path": str(path),
        "uid": metadata.st_uid,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _directory(expected: dict[str, Any]) -> Path:
    path = Path(expected["path"])
    _require(
        path.is_absolute() and path.resolve() == path and not path.is_symlink(),
        "Unpack directory path changed",
    )
    _require(
        stat.S_ISDIR(path.lstat().st_mode) and _identity(path) == expected,
        "Unpack directory ownership changed",
    )
    return path


def load_policy(path: Path) -> dict[str, Any]:
    policy: dict[str, Any] = json.loads(path.read_bytes())
    _keys(
        policy,
        {
            "format",
            "nonce",
            "root",
            "transport",
            "image",
            "checkpoint",
            "expected",
            "baseline",
            "services",
        }
        | ({"supplemental_inputs"} if "supplemental_inputs" in policy else set()),
    )
    _require(policy["format"] == "swingset-unpack-job-v1", "Unknown unpack policy format")
    _require(
        isinstance(policy["nonce"], str) and re.fullmatch(r"[0-9a-f]{12}", policy["nonce"]),
        "Invalid unpack job nonce",
    )
    _keys(policy["image"], {"id", "source", "dependency"})
    _sha(policy["image"]["id"], "sha256:")
    _sha(policy["image"]["dependency"], "sha256:")
    _sha(policy["image"]["source"], "source-sha256:")
    _keys(policy["checkpoint"], {"set_id", "manifest_sha256"})
    _require(
        re.fullmatch(r"set_[0-9]{8}T[0-9]{6}Z", policy["checkpoint"]["set_id"]),
        "Invalid checkpoint set identity",
    )
    _sha(policy["checkpoint"]["manifest_sha256"])
    root = Path(policy["root"]["path"])
    _require(root.is_absolute() and len(root.parts) >= 3, "Unsafe unpack root")
    expected_paths = [
        root / "transport",
        root / "transport/sets",
        root / "transport/sets" / policy["checkpoint"]["set_id"],
    ]
    _require(
        isinstance(policy["transport"], list) and len(policy["transport"]) == 3,
        "Unpack transport directory pins are incomplete",
    )
    for expected, required_path in zip(
        [policy["root"], *policy["transport"]], [root, *expected_paths], strict=True
    ):
        _keys(expected, {"path", "uid", "device", "inode", "mode"})
        _require(
            expected["path"] == str(required_path)
            and expected["mode"] == 0o700
            and expected["uid"] == os.getuid()
            and expected["uid"] != 0
            and all(
                type(expected[name]) is int and expected[name] >= 0
                for name in ("uid", "device", "inode", "mode")
            ),
            "Unsafe unpack directory policy",
        )
    if "supplemental_inputs" in policy:
        supplements = policy["supplemental_inputs"]
        _keys(supplements, {"directory", "bundles"})
        directory = supplements["directory"]
        _keys(directory, {"path", "uid", "device", "inode", "mode"})
        _require(
            isinstance(directory["path"], str)
            and Path(directory["path"]).is_absolute()
            and not any(character in directory["path"] for character in (",", "\n", "\0"))
            and len(Path(directory["path"]).parts) >= 3
            and directory["mode"] == 0o700
            and directory["uid"] == os.getuid()
            and directory["uid"] != 0
            and all(
                type(directory[name]) is int and directory[name] >= 0
                for name in ("uid", "device", "inode", "mode")
            ),
            "Unsafe supplemental inputs directory policy",
        )
        bundles = supplements["bundles"]
        _require(isinstance(bundles, list) and bool(bundles), "Missing supplemental bundle pins")
        for bundle in bundles:
            _keys(bundle, {"bundle", "manifest_sha256", "files", "bytes", "added"})
            _sha(bundle["bundle"])
            _sha(bundle["manifest_sha256"])
            _require(
                all(type(bundle[name]) is int and bundle[name] > 0 for name in ("files", "bytes"))
                and type(bundle["added"]) is bool,
                "Invalid supplemental bundle counts",
            )
        identifiers = [bundle["bundle"] for bundle in bundles]
        _require(
            identifiers == sorted(set(identifiers)),
            "Supplemental bundle pins must be unique and sorted",
        )
    _keys(
        policy["expected"],
        {
            "complete",
            "format",
            "set_id",
            "source_bytes",
            "source_sha256",
            "schema_version",
            "indexed_files",
            "indexed_bytes",
            "segments",
            "baseline_candidate",
            "state_sqlite",
        },
    )
    expected = policy["expected"]
    _sha(expected["source_sha256"])
    _require(
        expected["complete"] is True
        and expected["format"] == "swingset-sqlite37-unpack-v1"
        and expected["set_id"] == policy["checkpoint"]["set_id"]
        and expected["schema_version"] == 37
        and expected["state_sqlite"] == "/work/sqlite37/state.sqlite"
        and all(
            type(expected[name]) is int and expected[name] > 0
            for name in ("source_bytes", "indexed_files", "indexed_bytes", "segments")
        ),
        "Invalid unpack expected result",
    )
    _keys(policy["baseline"], {"candidate", "commit", "manifest_digest"})
    _require(
        isinstance(policy["baseline"]["candidate"], str)
        and re.fullmatch(r"cand_[0-9a-f]+", policy["baseline"]["candidate"])
        and policy["baseline"]["candidate"] == expected["baseline_candidate"],
        "Invalid unpack baseline",
    )
    _require(re.fullmatch(r"[0-9a-f]{40}", policy["baseline"]["commit"]), "Invalid baseline commit")
    _sha(policy["baseline"]["manifest_digest"])
    _require(
        isinstance(policy["services"], dict) and bool(policy["services"]),
        "Missing service baseline",
    )
    ids = []
    for key, members in policy["services"].items():
        _require(
            isinstance(key, str) and isinstance(members, list) and bool(members),
            "Invalid service baseline",
        )
        for member in members:
            _keys(member, {"id", "running", "health"})
            _sha(member["id"])
            _require(
                member["running"] is True and member["health"] in (None, "healthy"),
                "Unhealthy service policy",
            )
            ids.append(member["id"])
    _require(len(ids) == len(set(ids)), "Duplicate baseline service identity")
    return policy


def _write(path: Path, value: dict[str, Any]) -> None:
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w"
    ) as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _call(*arguments: str, timeout: float = 25) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments), capture_output=True, text=True, check=True, timeout=timeout
    )


def _health() -> dict[str, Any]:
    identifiers = _call("docker", "ps", "-q").stdout.split()
    result: dict[str, list[dict[str, Any]]] = {}
    if not identifiers:
        return result
    for item in json.loads(_call("docker", "inspect", *identifiers).stdout):
        labels = item["Config"].get("Labels") or {}
        state = item["State"]
        if labels.get("com.docker.swarm.service.name"):
            key = "swarm:" + labels["com.docker.swarm.service.name"]
        elif labels.get("com.docker.compose.project") and labels.get("com.docker.compose.service"):
            key = (
                "compose:"
                + labels["com.docker.compose.project"]
                + "/"
                + labels["com.docker.compose.service"]
            )
        else:
            key = "container:" + item["Name"].lstrip("/")
        result.setdefault(key, []).append(
            {
                "id": item["Id"],
                "running": state["Running"]
                and state.get("Status") == "running"
                and not state.get("Paused")
                and not state.get("Restarting"),
                "health": (state.get("Health") or {}).get("Status"),
                "restart_count": item.get("RestartCount", 0),
                "oom_killed": bool(state.get("OOMKilled", False)),
            }
        )
    return {
        key: sorted(value, key=lambda member: member["id"]) for key, value in sorted(result.items())
    }


def _check_health(current: dict[str, Any], baseline: dict[str, Any]) -> None:
    for key, prior in baseline.items():
        found = {item["id"]: item for item in current.get(key, [])}
        _require(
            set(found) == {item["id"] for item in prior},
            key + ": original server container identity changed",
        )
        for old in prior:
            new = found[old["id"]]
            _require(
                new["running"] and not new["oom_killed"] and new["health"] == old["health"],
                key + ": original server service became unhealthy",
            )
            _require(
                "restart_count" not in old or new["restart_count"] == old["restart_count"],
                key + ": original server service restarted",
            )


class UnpackJob:
    def __init__(self, policy: dict[str, Any]):
        self.policy = policy
        self.root = Path(policy["root"]["path"])
        self.job = self.root / ("unpack-" + policy["nonce"])
        self.name = "swingset-unpack-" + policy["nonce"]
        self.image = policy["image"]["id"]
        self.directories: list[dict[str, Any]] = []
        self.container_id: str | None = None
        self.create_attempted = False
        self.baseline: dict[str, Any] = {}

    def check_paths(self) -> None:
        for expected in [self.policy["root"], *self.policy["transport"], *self.directories]:
            _directory(expected)
        if "supplemental_inputs" in self.policy:
            _directory(self.policy["supplemental_inputs"]["directory"])

    def check_supplements(self) -> None:
        supplements = self.policy.get("supplemental_inputs")
        if supplements is None:
            return
        directory = _directory(supplements["directory"])
        _require(
            sorted(path.name for path in directory.iterdir())
            == [bundle["bundle"] for bundle in supplements["bundles"]],
            "Supplemental bundle inventory changed",
        )
        for bundle in supplements["bundles"]:
            path = directory / bundle["bundle"]
            manifest = path / "manifest.json"
            _require(
                not path.is_symlink()
                and path.is_dir()
                and not manifest.is_symlink()
                and stat.S_ISREG(manifest.lstat().st_mode)
                and hashlib.sha256(manifest.read_bytes()).hexdigest() == bundle["manifest_sha256"],
                "Supplemental bundle manifest changed",
            )

    def inspect(self, key: str) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(_call("docker", "inspect", key).stdout)[0]
        return value

    def owned(self, container: dict[str, Any]) -> dict[str, Any]:
        _require(
            container["Image"] == self.image
            and container["Name"] == "/" + self.name
            and (container["Config"].get("Labels") or {}).get(LABEL) == self.policy["nonce"]
            and self.container_id in (None, container["Id"]),
            "Unpack container ownership changed",
        )
        return container

    def argv(self) -> list[str]:
        command = [
            "docker",
            "create",
            "--pull=never",
            "--name",
            self.name,
            "--cidfile",
            str(self.job / "container.cid"),
            "--label",
            LABEL + "=" + self.policy["nonce"],
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--workdir",
            "/app",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            "128",
            "--cpus",
            "2",
            "--memory",
            str(MEMORY),
            "--memory-swap",
            str(MEMORY),
            "--shm-size",
            "16777216",
            "--ulimit",
            "nofile=4096:4096",
            "--stop-timeout",
            "30",
            "--init",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=1",
            "--log-opt",
            "compress=false",
        ]
        for source, target, readonly in [
            (self.root / "transport", "/transport", True),
            (self.job / "work", "/work", False),
            (self.job / "spill", "/spill", False),
        ]:
            command += [
                "--mount",
                f"type=bind,src={source},dst={target},bind-propagation=rprivate"
                + (",readonly" if readonly else ""),
            ]
        if "supplemental_inputs" in self.policy:
            command += [
                "--mount",
                "type=bind,src="
                + self.policy["supplemental_inputs"]["directory"]["path"]
                + ",dst=/supplemental-inputs,bind-propagation=rprivate,readonly",
            ]
        for setting in [
            "HOME=/spill/home",
            "TMPDIR=/spill/tmp",
            "SQLITE_TMPDIR=/spill/tmp",
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONUNBUFFERED=1",
            "OMP_NUM_THREADS=2",
            "OPENBLAS_NUM_THREADS=2",
            "ARROW_NUM_THREADS=2",
            "MALLOC_ARENA_MAX=2",
        ]:
            command += ["--env", setting]
        command += [
            "--entrypoint",
            "/app/.venv/bin/swingset",
            self.image,
            "unpack-sqlite37",
            "--from",
            "/transport",
            "--set-id",
            self.policy["checkpoint"]["set_id"],
            "--manifest-sha256",
            self.policy["checkpoint"]["manifest_sha256"],
            "--keep-failed",
        ]
        if "supplemental_inputs" in self.policy:
            command += ["--supplemental-inputs", "/supplemental-inputs"]
        return command + ["/work/sqlite37"]

    def prepare(self) -> dict[str, Any]:
        self.check_paths()
        self.check_supplements()
        _require(
            shutil.disk_usage(self.root).free >= MINIMUM_FREE, "Server free-disk floor reached"
        )
        manifest = (
            self.root / "transport/sets" / self.policy["checkpoint"]["set_id"] / "backup.json"
        )
        _require(
            hashlib.sha256(manifest.read_bytes()).hexdigest()
            == self.policy["checkpoint"]["manifest_sha256"],
            "Frozen checkpoint manifest changed",
        )
        current = _health()
        _check_health(current, self.policy["services"])
        self.baseline = {key: current[key] for key in self.policy["services"]}
        image = json.loads(_call("docker", "image", "inspect", self.image).stdout)[0]
        labels = image["Config"].get("Labels") or {}
        _require(
            image["Id"] == self.image
            and image["Architecture"] == "amd64"
            and image["Os"] == "linux"
            and labels.get("org.opencontainers.image.revision") == self.policy["image"]["source"]
            and labels.get("io.swingset.dependency-image") == self.policy["image"]["dependency"],
            "Unpack image provenance changed",
        )
        _require(
            {entry.split("=", 1)[0] for entry in image["Config"].get("Env", [])}
            <= ALLOWED_ENVIRONMENT,
            "Unpack image exposes unexpected environment names",
        )
        absent = subprocess.run(
            ["docker", "inspect", self.name], capture_output=True, text=True, timeout=20
        )
        _require(
            absent.returncode != 0
            and ("No such object" in absent.stderr or "No such container" in absent.stderr),
            "Unpack container name already exists or cannot be inspected",
        )
        for path in [
            self.job,
            self.job / "work",
            self.job / "spill",
            self.job / "spill/tmp",
            self.job / "spill/home",
        ]:
            path.mkdir(mode=0o700)
            self.directories.append(_identity(path))
        owner = {
            "nonce": self.policy["nonce"],
            "image": self.image,
            "name": self.name,
            "directories": self.directories,
        }
        _write(self.job / "owner.json", owner)
        _write(self.job / "health-baseline.json", self.baseline)
        self.create_attempted = True
        try:
            result = _call(*self.argv(), timeout=45)
        finally:
            cidfile = self.job / "container.cid"
            if cidfile.is_file() and not cidfile.is_symlink():
                self.container_id = cidfile.read_text().strip()
        _require(
            self.container_id == result.stdout.strip(),
            "Docker create identity differs from its owner record",
        )
        _sha(self.container_id)
        if self.container_id is None:
            raise RuntimeError("Docker did not record an unpack container identity")
        self.owned(self.inspect(self.container_id))
        owner["container_id"] = self.container_id
        _write(self.job / "container.json", owner)
        return owner

    def validate(self) -> dict[str, Any]:
        self.check_paths()
        container = self.owned(self.inspect(self.container_id or self.name))
        limits, config = container["HostConfig"], container["Config"]
        _require(
            not container["State"]["Running"] and container["State"]["Status"] == "created",
            "Unpack container already started",
        )
        expected = self.argv()[self.argv().index(self.image) + 1 :]
        _require(
            config["User"] == f"{os.getuid()}:{os.getgid()}"
            and config["WorkingDir"] == "/app"
            and config["Entrypoint"] == ["/app/.venv/bin/swingset"]
            and config["Cmd"] == expected,
            "Unpack executable changed",
        )
        _require(
            limits["NetworkMode"] == "none"
            and limits["ReadonlyRootfs"]
            and not limits["Privileged"]
            and limits["CapDrop"] == ["ALL"]
            and "no-new-privileges=true" in limits["SecurityOpt"]
            and limits.get("Init") is True
            and limits.get("RestartPolicy", {}).get("Name", "") in ("", "no")
            and limits["Memory"] == MEMORY
            and limits["MemorySwap"] == MEMORY
            and limits["NanoCpus"] == 2_000_000_000
            and limits["PidsLimit"] == 128,
            "Unpack isolation or resource bounds changed",
        )
        _require(
            not any(
                limits.get(key)
                for key in ("PidMode", "UTSMode", "Devices", "DeviceRequests", "PortBindings")
            )
            and limits["IpcMode"] == "private",
            "Unpack host namespace or devices exposed",
        )
        _require(
            limits["LogConfig"]
            == {
                "Type": "local",
                "Config": {"max-size": "10m", "max-file": "1", "compress": "false"},
            },
            "Unpack log bounds changed",
        )
        mounts = {
            "/transport": (str(self.root / "transport"), False),
            "/work": (str(self.job / "work"), True),
            "/spill": (str(self.job / "spill"), True),
        }
        if "supplemental_inputs" in self.policy:
            mounts["/supplemental-inputs"] = (
                self.policy["supplemental_inputs"]["directory"]["path"],
                False,
            )
        _require(
            len(container["Mounts"]) == len(mounts)
            and {mount["Destination"] for mount in container["Mounts"]} == set(mounts),
            "Unexpected unpack mounts",
        )
        for mount in container["Mounts"]:
            _require(mount["Destination"] in mounts, "Unexpected unpack mount")
            source, writable = mounts[mount["Destination"]]
            _require(
                mount["Type"] == "bind"
                and mount["Source"] == source
                and mount["RW"] is writable
                and mount["Propagation"] == "rprivate",
                "Unpack mount changed",
            )
        _require(
            {entry.split("=", 1)[0] for entry in config.get("Env", [])} <= ALLOWED_ENVIRONMENT,
            "Unpack image exposes unexpected environment names",
        )
        return {
            "container_id": container["Id"],
            "image": container["Image"],
            "network": limits["NetworkMode"],
            "memory": limits["Memory"],
            "memory_swap": limits["MemorySwap"],
            "log_config": limits["LogConfig"],
        }

    def snapshot(self) -> dict[str, Any]:
        self.check_paths()
        container = self.owned(self.inspect(self.container_id or self.name))
        current = _health()
        _check_health(current, self.baseline)
        free = shutil.disk_usage(self.root).free
        _require(free >= MINIMUM_FREE, "Server free-disk floor reached")
        paths = [
            self.job / "work/sqlite37/state.sqlite",
            *(self.job / "work").glob(".sqlite37-*/state/state.sqlite"),
        ]
        metrics: dict[str, Any] = {"io": {}, "cpu": {}, "memory": {}}
        state = container["State"]
        if state.get("Running") and state.get("Pid"):
            try:
                rows = (Path("/proc") / str(state["Pid"]) / "cgroup").read_text().splitlines()
                relative = next(row.split(":", 2)[2] for row in rows if row.startswith("0::"))
                _require(
                    ".." not in Path(relative).parts and container["Id"] in relative,
                    "Unpack process cgroup identity differs",
                )
                group = Path("/sys/fs/cgroup") / relative.lstrip("/")
                for line in (group / "io.stat").read_text().splitlines():
                    for field in line.split()[1:]:
                        key, value = field.split("=")
                        metrics["io"][key] = metrics["io"].get(key, 0) + int(value)
                metrics["cpu"] = {
                    key: int(value)
                    for key, value in (
                        line.split() for line in (group / "cpu.stat").read_text().splitlines()
                    )
                }
                metrics["memory"] = {
                    name: int((group / name).read_text())
                    for name in ("memory.current", "memory.peak")
                    if (group / name).is_file()
                }
            except (FileNotFoundError, PermissionError, StopIteration):
                pass
        return {
            "container_id": container["Id"],
            "state": {
                key: container["State"].get(key)
                for key in (
                    "Status",
                    "Running",
                    "ExitCode",
                    "OOMKilled",
                    "Error",
                    "StartedAt",
                    "FinishedAt",
                    "Pid",
                )
            },
            "free_bytes": free,
            "health": current,
            **metrics,
            "raw_sqlite_bytes": max(
                (path.stat().st_size for path in paths if path.is_file()), default=0
            ),
        }

    def stop(self) -> dict[str, Any]:
        self.check_paths()
        if not self.create_attempted:
            return {"not_attempted": True, "not_running": True}
        if self.container_id is None:
            raise RuntimeError(
                "Unpack container identity was not recorded; refusing a name-based stop"
            )
        result = subprocess.run(
            ["docker", "inspect", self.container_id], capture_output=True, text=True, timeout=20
        )
        if result.returncode:
            _require(
                "No such object" in result.stderr or "No such container" in result.stderr,
                "Cannot establish unpack container absence",
            )
            return {"known_absent": True, "not_running": True}
        container = self.owned(json.loads(result.stdout)[0])
        if container["State"]["Running"]:
            subprocess.run(
                ["docker", "stop", "--time", "30", container["Id"]],
                capture_output=True,
                text=True,
                timeout=45,
            )
            container = self.owned(self.inspect(container["Id"]))
            if container["State"]["Running"]:
                _call("docker", "kill", "--signal", "KILL", container["Id"], timeout=20)
                container = self.owned(self.inspect(container["Id"]))
        return {
            "container_id": container["Id"],
            "not_running": not container["State"]["Running"],
            "exit_code": container["State"]["ExitCode"],
            "oom_killed": container["State"]["OOMKilled"],
            "removed": False,
            "state": {
                key: container["State"].get(key) for key in ("Error", "Status", "StartedAt", "Pid")
            },
        }

    def postcheck(self) -> dict[str, Any]:
        final = self.snapshot()
        self.check_supplements()
        _require(
            not final["state"]["Running"]
            and final["state"]["ExitCode"] == 0
            and not final["state"]["OOMKilled"],
            "Unpack container did not succeed",
        )
        logs = _call("docker", "logs", self.container_id or self.name, timeout=45).stdout
        result = json.loads(logs)
        _require(
            all(result.get(key) == value for key, value in self.policy["expected"].items()),
            "Unpack output differs from the frozen checkpoint",
        )
        _require(
            result.get("supplemental_input_bundles")
            == self.policy.get("supplemental_inputs", {}).get("bundles", []),
            "Unpack supplemental bundle provenance differs from policy",
        )
        closure = result.get("closure", {})
        _require(
            closure.get("complete") is True
            and closure.get("candidates", 0) >= 1
            and closure.get("input_bundles", 0) >= 1,
            "Unpack closure is incomplete",
        )
        root = self.job / "work/sqlite37"
        _require(
            root.is_dir()
            and not root.is_symlink()
            and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700,
            "Unpacked directory ownership changed",
        )
        _require(
            (root / "state.sqlite").stat().st_size == self.policy["expected"]["source_bytes"]
            and (root / "baseline").is_symlink()
            and os.readlink(root / "baseline")
            == "candidates/" + self.policy["baseline"]["candidate"],
            "Unpacked SQLite or baseline differs",
        )
        published = json.loads(
            (root / "candidates" / self.policy["baseline"]["candidate"] / "PUBLISHED").read_bytes()
        )
        _require(
            all(
                published.get(key) == self.policy["baseline"][value]
                for key, value in [
                    ("candidate_id", "candidate"),
                    ("commit", "commit"),
                    ("manifest_digest", "manifest_digest"),
                ]
            ),
            "Unpacked published baseline differs",
        )
        _write(self.job / "unpack-result.json", result)
        return {
            "complete": True,
            "result": result,
            "final_server_snapshot": final,
            "log_bytes": len(logs.encode()),
            "log_sha256": hashlib.sha256(logs.encode()).hexdigest(),
        }


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True), flush=True)


def _process_owner() -> dict[str, Any]:
    process = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": os.getpid(),
        "process_start": process[19],
        "process_group": os.getpgrp(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }


def run(policy_path: Path) -> int:
    policy = load_policy(policy_path)
    job = UnpackJob(policy)
    job.check_paths()
    owner = {
        **_process_owner(),
        "nonce": policy["nonce"],
        "image": job.image,
        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
    }
    _write(job.root / ("unpack-" + policy["nonce"] + "-supervisor-owner.json"), owner)
    result: dict[str, Any] = {"complete": False, "supervisor_owner": owner}
    started = time.monotonic()
    previous = {
        number: signal.getsignal(number)
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    }

    def interrupted(number: int, frame: Any) -> None:
        raise InterruptedError("Unpack supervisor interrupted")

    for number in previous:
        signal.signal(number, interrupted)
    attempted = False
    try:
        attempted = True
        result["owner"] = job.prepare()
        result["validation"] = job.validate()
        _call("docker", "start", job.container_id or job.name, timeout=30)
        last_metric, last_progress, alerted = None, time.monotonic(), False
        with os.fdopen(
            os.open(
                job.job / "progress.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            ),
            "w",
        ) as progress:
            while True:
                _require(
                    time.monotonic() - started < DEADLINE, "Server-local unpack deadline reached"
                )
                snapshot = job.snapshot()
                snapshot["elapsed_seconds"] = round(time.monotonic() - started, 3)
                progress.write(json.dumps(snapshot, sort_keys=True) + "\n")
                progress.flush()
                _emit({"phase": "unpack", **snapshot})
                metric = (snapshot["raw_sqlite_bytes"], tuple(sorted(snapshot["io"].items())))
                if metric != last_metric:
                    last_metric, last_progress, alerted = metric, time.monotonic(), False
                if time.monotonic() - last_progress >= 3600 and not alerted:
                    _emit({"phase": "alert", "kind": "no_observable_unpack_progress_60m"})
                    alerted = True
                if not snapshot["state"]["Running"]:
                    break
                time.sleep(min(30, max(0, DEADLINE - (time.monotonic() - started))))
        result.update(job.postcheck())
    except BaseException as error:
        result.update(error_class=type(error).__name__, error=str(error)[:1024])
    finally:
        for number in previous:
            signal.signal(number, signal.SIG_IGN)
        if attempted:
            try:
                result["container_cleanup"] = job.stop()
            except Exception as error:
                result["container_cleanup"] = {"not_running": False, "error": type(error).__name__}
            if not result["container_cleanup"]["not_running"]:
                result["complete"] = False
        result["seconds"] = round(time.monotonic() - started, 3)
        result["supervisor_exit"] = 0 if result["complete"] else 1
        try:
            job.check_paths()
            _write(job.root / ("unpack-" + policy["nonce"] + "-supervisor-result.json"), result)
            _emit({"phase": "result", **result})
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    return int(result["supervisor_exit"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    return run(parser.parse_args(argv).policy)


if __name__ == "__main__":
    raise SystemExit(main())
