"""Host-owned network-namespace firewalls authorize each exact worker incarnation.

The host has Docker and namespace privileges. Worker containers have neither.
Workers wait for the matching read-only authorization before opening Postgres.
The OUTPUT policy covers every interface, including a later network attachment.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

CHAIN = "SWINGSET_OUT"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", value["deployment"]):
        raise ValueError("invalid deployment identity")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]):
        raise ValueError("network authorization requires the exact runtime image id")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{3,100}", value["network"]):
        raise ValueError("invalid private network name")
    network = ipaddress.ip_network(value["subnet"], strict=True)
    if network.version != 4 or not network.is_private:
        raise ValueError("worker network must be a private IPv4 subnet")
    addresses = []
    for role, member in value["workers"].items():
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", role):
            raise ValueError("invalid worker role")
        if type(member.get("required", True)) is not bool:
            raise ValueError("worker inventory requirement must be boolean")
        for name in ("address", "database"):
            address = ipaddress.ip_address(member[name])
            if address not in network or str(address) in {
                str(network.network_address),
                str(network.broadcast_address),
            }:
                raise ValueError("worker or database address is outside its private network")
        addresses.append(member["address"])
        if (
            type(member.get("database_port", 5432)) is not int
            or not 1 <= member.get("database_port", 5432) <= 65535
        ):
            raise ValueError("invalid database port")
    if not addresses or len(set(addresses)) != len(addresses):
        raise ValueError("worker addresses must be unique")
    for name in ("proxy", "dns"):
        if ipaddress.ip_address(value[name]) not in network or value[name] in addresses:
            raise ValueError("gateway address is invalid")
    return value


def digest(policy: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def rules(policy: dict[str, Any], role: str, *, ipv6: bool = False) -> list[list[str]]:
    result = [
        ["-o", "lo", "-j", "ACCEPT"],
        ["-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
    ]
    if not ipv6:
        member = policy["workers"][role]
        for destination, protocol, port in (
            (member["database"], "tcp", str(member.get("database_port", 5432))),
            (policy["proxy"], "tcp", "3128"),
            (policy["dns"], "udp", "53"),
            (policy["dns"], "tcp", "53"),
        ):
            result.append(
                [
                    "-d",
                    destination + "/32",
                    "-p",
                    protocol,
                    "-m",
                    protocol,
                    "--dport",
                    port,
                    "-j",
                    "ACCEPT",
                ]
            )
    result.append(
        [
            "-j",
            "REJECT",
            "--reject-with",
            "icmp6-adm-prohibited" if ipv6 else "icmp-admin-prohibited",
        ]
    )
    return result


def _run(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, check=False)
    if check and result.returncode:
        raise RuntimeError("network command failed: " + shlex.join(arguments[:4]))
    return result


def _inspect(identifier: str) -> dict[str, Any]:
    return json.loads(_run(["docker", "inspect", identifier]).stdout)[0]  # type: ignore[no-any-return]


def _start(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def validate_container(container: dict[str, Any], policy: dict[str, Any]) -> str:
    labels = container["Config"].get("Labels", {})
    role = labels.get("io.swingset.role", "")
    if (
        labels.get("io.swingset.deployment") != policy["deployment"]
        or role not in policy["workers"]
    ):
        raise ValueError("container does not belong to this deployment")
    if not container["State"]["Running"] or container["Image"] != policy["image_id"]:
        raise ValueError("container is not running the proved image")
    networks = container["NetworkSettings"]["Networks"]
    if set(networks) != {policy["network"]}:
        raise ValueError("worker has an unexpected network attachment")
    network = networks[policy["network"]]
    if network["IPAddress"] != policy["workers"][role]["address"] or network.get(
        "GlobalIPv6Address"
    ):
        raise ValueError("worker private address differs")
    host = container["HostConfig"]
    if (
        host.get("PidMode") not in {None, ""}
        or host.get("IpcMode") not in {None, "", "private"}
        or host.get("CgroupnsMode") not in {None, "", "private"}
    ):
        raise ValueError("worker process, IPC or cgroup namespace is shared")
    if host.get("Privileged") or host.get("CapAdd") or "ALL" not in (host.get("CapDrop") or []):
        raise ValueError("worker capabilities differ")
    if not host.get("ReadonlyRootfs") or not any(
        value.startswith("no-new-privileges") for value in host.get("SecurityOpt") or []
    ):
        raise ValueError("worker filesystem or privilege settings differ")
    if container["Config"].get("User") not in {"10001", "10001:10001", "swingset"}:
        raise ValueError("worker must run as the pinned non-root user")
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
    allowed_secrets = {"worker-dsn", "alert-url", "deadman-url"}
    if role == "checker":
        allowed_secrets = {"scratch-admin-dsn"}
    elif role == "verifier":
        allowed_secrets = {"worker-dsn", "control-dsn", "scratch-admin-dsn"}
    elif role == "operator":
        allowed_secrets |= {
            "control-dsn",
            "owner-dsn",
            "hf-token",
            "r2-access-key",
            "r2-secret-key",
        }
    elif role == "release-maintain":
        allowed_secrets |= {"hf-token", "r2-access-key", "r2-secret-key"}
    elif role == "jobs":
        allowed_secrets |= {"r2-access-key", "r2-secret-key"}
    for mount in container.get("Mounts", []):
        destination = mount.get("Destination", "")
        if "docker.sock" in mount.get("Source", "") or not (
            destination in writable | readonly or destination.startswith("/run/secrets/")
        ):
            raise ValueError("worker has an unexpected host mount")
        if (destination in readonly or destination.startswith("/run/secrets/")) and mount.get("RW"):
            raise ValueError("worker configuration or credentials are writable")
        if (
            destination.startswith("/run/secrets/")
            and destination.removeprefix("/run/secrets/") not in allowed_secrets
        ):
            raise ValueError("service received an unexpected credential")
        if destination == "/run/secrets/scratch-admin-dsn" and role not in {"checker", "verifier"}:
            raise ValueError("ordinary workers cannot receive scratch administration credentials")
        if destination == "/run/secrets/owner-dsn" and role != "operator":
            raise ValueError("ordinary workers cannot receive migration credentials")
    return str(role)


def _normalize(rule: str) -> list[str]:
    values = shlex.split(rule)
    if "--ctstate" in values:
        offset = values.index("--ctstate") + 1
        values[offset] = ",".join(sorted(values[offset].split(",")))
    return values


def _install(pid: int, expected: list[list[str]], *, ipv6: bool) -> None:
    base = [
        "nsenter",
        "--target",
        str(pid),
        "--net",
        "--",
        "ip6tables" if ipv6 else "iptables",
        "--wait",
        "5",
    ]
    found = _run([*base, "-S", CHAIN], check=False)
    if found.returncode:
        _run([*base, "-N", CHAIN])
        current = []
    else:
        current = [
            _normalize(line)[2:] for line in found.stdout.splitlines() if line.startswith("-A ")
        ]
    normalized = [_normalize(shlex.join(rule)) for rule in expected]
    if current != normalized[: len(current)]:
        raise RuntimeError("existing namespace firewall differs; authorization refused")
    for rule in expected[len(current) :]:
        _run([*base, "-A", CHAIN, *rule])
    output = _run([*base, "-S", "OUTPUT"]).stdout.splitlines()
    jumps = [line for line in output if line.startswith("-A ")]
    jump = ["-A", "OUTPUT", "-j", CHAIN]
    if not jumps or _normalize(jumps[0]) != jump:
        if any(CHAIN in line for line in jumps):
            raise RuntimeError("namespace firewall jump is not first")
        _run([*base, "-I", "OUTPUT", "1", "-j", CHAIN])
    for rule in expected:
        _run([*base, "-C", CHAIN, *rule])
    final = [
        _normalize(line)[2:]
        for line in _run([*base, "-S", CHAIN]).stdout.splitlines()
        if line.startswith("-A ")
    ]
    if final != normalized:
        raise RuntimeError("namespace firewall verification failed")


def authorize(identifier: str, policy: dict[str, Any], directory: Path) -> dict[str, Any]:
    container = _inspect(identifier)
    role = validate_container(container, policy)
    pid = int(container["State"]["Pid"])
    network_inode = Path(f"/proc/{pid}/ns/net").stat().st_ino
    started = _start(pid)
    network = _inspect(policy["network"])
    if network.get("Labels", {}).get("io.swingset.deployment") != policy["deployment"]:
        raise ValueError("worker bridge belongs to another deployment")
    if network.get("Internal") is not True or network.get("EnableIPv6") is not False:
        raise ValueError("worker network must be internal with IPv6 disabled")
    if network["Driver"] != "bridge" or [
        item.get("Subnet") for item in network["IPAM"]["Config"]
    ] != [policy["subnet"]]:
        raise ValueError("worker bridge subnet differs")
    _install(pid, rules(policy, role), ipv6=False)
    _install(pid, rules(policy, role, ipv6=True), ipv6=True)
    after = _inspect(identifier)
    if (
        validate_container(after, policy) != role
        or after["State"]["Pid"] != pid
        or _start(pid) != started
        or Path(f"/proc/{pid}/ns/net").stat().st_ino != network_inode
    ):
        raise RuntimeError("worker incarnation changed during authorization")
    value = {
        "container_id": container["Id"],
        "role": role,
        "policy_sha256": digest(policy),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "network_inode": network_inode,
        "process_start": started,
    }
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = directory / (container["Id"] + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o444)
    temporary.replace(directory / (container["Id"] + ".json"))
    return value


def require_authorization(policy_path: Path, directory: Path) -> dict[str, Any]:
    policy = load(policy_path)
    hostname = os.environ.get("HOSTNAME", "")
    if not re.fullmatch(r"[0-9a-f]{12,64}", hostname):
        raise RuntimeError("worker hostname is not its container identity")
    found = list(directory.glob(hostname + "*.json"))
    if len(found) != 1:
        raise RuntimeError("worker is waiting for host network authorization")
    value = json.loads(found[0].read_text())
    if (
        not value["container_id"].startswith(hostname)
        or value["policy_sha256"] != digest(policy)
        or value["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        or value["network_inode"] != Path("/proc/self/ns/net").stat().st_ino
        or value["process_start"] != _start(1)
        or value["role"] not in policy["workers"]
    ):
        raise RuntimeError("worker network authorization is stale or belongs elsewhere")
    return value  # type: ignore[no-any-return]


def export_status(directory: Path, policy: dict[str, Any]) -> None:
    """Only the platform operator reads peer container state and memory."""
    identifiers = _run(["docker", "ps", "-aq", "--no-trunc"]).stdout.splitlines()
    containers = []
    template = '{"id":{{json .Id}},"name":{{json .Name}},"running":{{json .State.Running}},"status":{{json .State.Status}},"oom_killed":{{json .State.OOMKilled}},"exit_code":{{json .State.ExitCode}},"restart_count":{{json .RestartCount}},"image":{{json .Image}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}},"deployment":{{json (index .Config.Labels "io.swingset.deployment")}},"role":{{json (index .Config.Labels "io.swingset.role")}}}'
    for identifier in identifiers:
        result = _run(["docker", "inspect", "--format", template, identifier], check=False)
        if result.returncode == 0:
            containers.append(json.loads(result.stdout))
    samples = _run(
        ["docker", "stats", "--no-stream", "--no-trunc", "--format", "{{json .}}"], check=False
    )
    owned = [
        item
        for item in containers
        if item["deployment"] == policy["deployment"]
        and not (
            policy["workers"].get(item["role"], {}).get("required", True) is False
            and not item["running"]
            and item.get("exit_code") == 0
            and not item.get("oom_killed")
        )
    ]
    counts = {
        role: sum(item["running"] and item["role"] == role for item in owned)
        for role, member in policy["workers"].items()
        if member.get("required", True)
    }
    value = {
        "utc": time.time(),
        "deployment": policy["deployment"],
        "policy_sha256": digest(policy),
        "complete_inventory": all(count == 1 for count in counts.values()),
        "expected_roles": sorted(counts),
        "containers": owned,
        "other_containers": [
            item for item in containers if item["deployment"] != policy["deployment"]
        ],
        "resources": [json.loads(line) for line in samples.stdout.splitlines() if line.strip()],
        "resource_exit": samples.returncode,
    }
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = directory / "containers.tmp"
    temporary.write_text(json.dumps(value, sort_keys=True))
    temporary.chmod(0o444)
    temporary.replace(directory / "containers.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("authorization_directory", type=Path)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    policy = load(args.policy)
    seen: dict[str, tuple[int, str]] = {}
    last_status = 0.0
    while True:
        identifiers = _run(
            [
                "docker",
                "ps",
                "-q",
                "--no-trunc",
                "--filter",
                "label=io.swingset.deployment=" + policy["deployment"],
                "--filter",
                "label=io.swingset.guarded=true",
            ]
        ).stdout.splitlines()
        for identifier in identifiers:
            current = _inspect(identifier)
            incarnation = int(current["State"]["Pid"]), str(current["State"]["StartedAt"])
            if seen.get(identifier) != incarnation:
                value = authorize(identifier, policy, args.authorization_directory)
                seen[identifier] = incarnation
                print(json.dumps(value), flush=True)
        if time.monotonic() - last_status >= 30:
            export_status(args.authorization_directory, policy)
            last_status = time.monotonic()
        if not args.watch:
            return
        time.sleep(2)


if __name__ == "__main__":
    main()
