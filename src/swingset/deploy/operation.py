"""Run the reviewed, nonce-owned server network rehearsal and its relay checks.

This module uses only the standard library. A frozen minimal source package can
run on the host through PYTHONPATH without installing application dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PHASES = (
    "images",
    "setup",
    "baseline",
    "adversarial",
    "repair-targets",
    "repair-partial-targets",
    "repair-relay-ingress",
    "adversarial-resume",
    "recreate",
    "restart",
    "relay",
    "cleanup",
    "tunnel-before",
    "tunnel-ingress-before",
    "tunnel-after",
)
ROLES = {"acquire-plan", "derive", "release-maintain", "jobs", "checker", "health"}
SUPPORTS = ("proxy", "resolver", "owned-mock", "control", "relay", "operator")
INFO = '{"id":{{json .Id}},"pid":{{.State.Pid}},"running":{{json .State.Running}},"status":{{json .State.Status}},"role":{{json (index .Config.Labels "io.swingset.role")}},"deployment":{{json (index .Config.Labels "io.swingset.deployment")}},"oom":{{json .State.OOMKilled}},"restart":{{.RestartCount}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}'
IMAGE = '{"id":{{json .Id}},"arch":{{json .Architecture}}}'
PORTS = '{"host_config":{{json .HostConfig.PortBindings}},"network_settings":{{json .NetworkSettings.Ports}}}'
ATTACHMENT = '{"image":{{json .Image}},"networks":{{json .NetworkSettings.Networks}}}'
ADVERTISEMENT_OPTION = "com.docker.network.advertise_addr_nmsgs"
TARGET_SUPPORTS = ("proxy", "owned-mock", "control", "relay")
AUTHORIZATION = "from pathlib import Path;from swingset.deploy.security import require_authorization;print(require_authorization(Path('/run/swingset-config/network.json'),Path('/run/swingset-authorization'))['role'])"


def _command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=180, check=False)


class Operation:
    """One phase may affect only the deployment named by its frozen manifest."""

    def __init__(
        self,
        root: Path,
        *,
        runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _command,
    ) -> None:
        if root.is_symlink():
            raise ValueError("proof directory must not be a symlink")
        self.root = root.resolve(strict=True)
        self.manifest: dict[str, Any] = json.loads((self.root / "manifest.json").read_text())
        self.deployment = str(self.manifest["deployment"])
        if not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", self.deployment):
            raise ValueError("invalid deployment identity")
        self.roles: list[str] = self.manifest["roles"]
        self.policy: dict[str, Any] = json.loads((self.root / "config/network.json").read_text())
        if (
            self.policy["deployment"] != self.deployment
            or len(self.roles) != len(ROLES)
            or set(self.roles) != ROLES
            or set(self.roles) != set(self.policy["workers"])
        ):
            raise ValueError("proof roles or policy deployment differ")
        if self.policy["image_id"] != self.manifest["runtime_image"]:
            raise ValueError("policy runtime CONFIG identity differs")
        images = [self.manifest["runtime_image"], self.manifest["operator_image"]]
        images += [value["id"] for value in self.manifest["images"].values()]
        if not all(re.fullmatch(r"sha256:[0-9a-f]{64}", image) for image in images):
            raise ValueError("proof images require immutable CONFIG identities")
        self.runner = runner
        self.logs: list[dict[str, Any]] = []
        self.compose = ["docker", "compose", "-f", str(self.root / "proof.compose.json")]
        self.adversarial = self.compose + ["-f", str(self.root / "adversarial.override.json")]
        self.workers = str(self.policy["network"])
        self.targets = self.deployment + "-targets"

    def run(self, arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = self.runner(arguments)
        self.logs.append(
            {
                "command": arguments,
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        if check and result.returncode:
            raise RuntimeError("reviewed proof command failed")
        return result

    def cid(self, role: str) -> str:
        identifier = self.run(self.compose + ["ps", "-q", "--all", role]).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise RuntimeError("proof role did not resolve to one exact container")
        return identifier

    def info(self, identifier: str) -> dict[str, Any]:
        return json.loads(self.run(["docker", "inspect", "--format", INFO, identifier]).stdout)  # type: ignore[no-any-return]

    def inventory(self) -> list[dict[str, Any]]:
        identifiers = self.run(["docker", "ps", "-aq", "--no-trunc"]).stdout.splitlines()
        return [self.info(identifier) for identifier in identifiers]

    def owned(self, identifier: str) -> dict[str, Any]:
        result = self.info(identifier)
        if result["id"] != identifier or result["deployment"] != self.deployment:
            raise RuntimeError("container belongs to another deployment")
        return result

    def image_checks(self) -> None:
        images = [
            (self.manifest[name], self.manifest[name])
            for name in ("runtime_image", "operator_image")
        ]
        images += [(value["reference"], value["id"]) for value in self.manifest["images"].values()]
        for reference, expected in images:
            actual = json.loads(
                self.run(["docker", "image", "inspect", "--format", IMAGE, reference]).stdout
            )
            if actual != {"id": expected, "arch": "amd64"}:
                raise RuntimeError("immutable image CONFIG identity or platform differs")

    def marker_absent(self, roles: list[str] | None = None) -> None:
        for role in self.roles if roles is None else roles:
            identifier = self.cid(role)
            self.owned(identifier)
            if (
                self.run(
                    ["docker", "exec", identifier, "python", "-c", AUTHORIZATION], check=False
                ).returncode
                == 0
            ):
                raise RuntimeError("old authorization accepted a new incarnation")

    def ready(self) -> None:
        deadline = time.monotonic() + 60
        for role in self.roles:
            identifier = self.cid(role)
            self.owned(identifier)
            marker = self.root / "authorization" / (identifier + ".json")
            while True:
                if marker.exists():
                    result = self.run(
                        ["docker", "exec", identifier, "python", "-c", AUTHORIZATION], check=False
                    )
                    if result.returncode == 0:
                        if result.stdout.strip() != role:
                            raise RuntimeError("authorized role differs")
                        break
                if time.monotonic() >= deadline:
                    raise RuntimeError("fresh authorization timed out")
                time.sleep(0.2)

    def services_running(self, roles: list[str], *, timeout: float = 15) -> list[dict[str, Any]]:
        """Worker authorization alone does not establish gateway readiness."""
        deadline = time.monotonic() + timeout
        while True:
            values = [self.owned(self.cid(role)) for role in roles]
            for value in values:
                if value["oom"] or value["status"] not in {"running", "created", "restarting"}:
                    raise RuntimeError("proof service stopped, paused or OOM")
            if all(value["running"] and value["status"] == "running" for value in values):
                return values
            if time.monotonic() >= deadline:
                raise RuntimeError("proof services did not start before the deadline")
            time.sleep(0.2)

    def _counter(self, operator: str, pid: int, ipv6: bool) -> int:
        base = [
            "docker",
            "exec",
            operator,
            "nsenter",
            "--target",
            str(pid),
            "--net",
            "--",
            "ip6tables" if ipv6 else "iptables",
            "--wait",
            "5",
        ]
        text = self.run([*base, "-L", "SWINGSET_OUT", "-v", "-n", "-x"]).stdout
        rows = [line.split() for line in text.splitlines() if "REJECT" in line]
        if len(rows) != 1 or not rows[0][0].isdigit():
            raise RuntimeError("kernel reject counter is unavailable")
        self.run([*base, "-S", "OUTPUT"])
        return int(rows[0][0])

    def prove(self, stage: str, filename: str) -> None:
        operator, control = self.cid("operator"), self.cid("control")
        self.owned(operator)
        self.owned(control)
        addresses = self.manifest["proof_addresses"]
        nonce = self.manifest["nonce"]
        control_code = """import http.client,json,sys
for host in sys.argv[1:3]:
 c=http.client.HTTPConnection(host,8080,timeout=3);c.request('GET','/nonce')
 r=c.getresponse();assert r.status==200 and r.read().decode()==sys.argv[3];c.close()
print(json.dumps({'live_ipv4':True,'live_ipv6':True}))"""

        def positive() -> dict[str, Any]:
            value = json.loads(
                self.run(
                    ["docker", "exec", control, "python", "-c", control_code, *addresses, nonce]
                ).stdout
            )
            if not isinstance(value, dict):
                raise RuntimeError("owned control result must be an object")
            return value

        report: dict[str, Any] = {"stage": stage, "control_before": positive(), "workers": []}
        proxy = "http://" + self.policy["proxy"] + ":3128"
        redirects = """import httpx,json,socket,sys
assert {item[4][0] for item in socket.getaddrinfo('owned-mock.swingset.invalid',8080)}=={sys.argv[2]}
with httpx.Client(proxy=sys.argv[1],trust_env=False,follow_redirects=True,timeout=5) as c:
 assert c.get('http://owned-mock.swingset.invalid:8080/redirect').status_code==200
 assert c.get('http://owned-mock.swingset.invalid:8080/redirect-denied').status_code==403
print(json.dumps({'allowed_dns':True,'allowed_redirect':True,'forbidden_redirect':True}))"""
        for role in self.roles:
            identifier = self.cid(role)
            info = self.owned(identifier)
            if info["role"] != role or not info["running"]:
                raise RuntimeError("proof role or running state differs")
            before = [self._counter(operator, info["pid"], ipv6) for ipv6 in (False, True)]
            tcp = (
                self.routable_tcp(identifier, info["pid"], operator)
                if stage == "adversarial"
                else []
            )
            command = [
                "docker",
                "exec",
                identifier,
                "python",
                "-m",
                "swingset.deploy.probe",
                "--allowed",
                "http://owned-mock.swingset.invalid:8080/ok",
                "--denied-name",
                "forbidden.swingset.invalid",
                "--proxy",
                proxy,
            ]
            for address in addresses:
                command += ["--denied-address", address]
            checks = json.loads(self.run(command).stdout)
            checks.update(
                json.loads(
                    self.run(
                        [
                            "docker",
                            "exec",
                            identifier,
                            "python",
                            "-c",
                            redirects,
                            proxy,
                            addresses[0],
                        ]
                    ).stdout
                )
            )
            after = [self._counter(operator, info["pid"], ipv6) for ipv6 in (False, True)]
            if stage == "adversarial" and not all(
                end > start for start, end in zip(before, after, strict=True)
            ):
                raise RuntimeError("both routable address families must hit the kernel reject rule")
            report["workers"].append(
                {
                    **info,
                    "checks": checks,
                    "reject_counts_before": before,
                    "reject_counts_after": after,
                    "routable_tcp": tcp,
                }
            )
        report.update(control_after=positive(), complete=True)
        self._write(filename, report)

    def routable_tcp(self, identifier: str, pid: int, operator: str) -> list[dict[str, Any]]:
        """Separate routed TCP rejection from DNS, absent routes and IPv6 advertisements."""
        role = self.owned(identifier)["role"]
        results = []
        script = """import ipaddress,json,socket,sys
from pathlib import Path
address,source=sys.argv[1:3];v6=ipaddress.ip_address(address).version==6
family=socket.AF_INET6 if v6 else socket.AF_INET
flags=None
if v6:
 rows=[r.split() for r in Path('/proc/net/if_inet6').read_text().splitlines()]
 matches=[r for r in rows if int(r[0],16)==int(ipaddress.ip_address(source))]
 assert len(matches)==1
 flags=int(matches[0][4],16);assert not flags & (0x40|0x08)
with socket.socket(family,socket.SOCK_DGRAM) as route:
 route.connect((address,8080));routed_source=route.getsockname()[0]
assert routed_source==source
with socket.socket(family,socket.SOCK_STREAM) as connection:
 connection.settimeout(3)
 try:connection.connect((address,8080))
 except OSError as e:error=e.errno
 else:error=0
print(json.dumps({'address':address,'source':routed_source,'errno':error,'ipv6_flags':flags}))"""
        for index, address in enumerate(self.manifest["proof_addresses"]):
            before = self._counter(operator, pid, bool(index))
            result = json.loads(
                self.run(
                    [
                        "docker",
                        "exec",
                        identifier,
                        "python",
                        "-c",
                        script,
                        address,
                        self.manifest["adversarial_addresses"][role][index],
                    ]
                ).stdout
            )
            after = self._counter(operator, pid, bool(index))
            if (
                result.get("address") != address
                or result.get("source") != self.manifest["adversarial_addresses"][role][index]
                # The worker kernel is Linux even when this driver runs on macOS.
                or result.get("errno") != (13 if index else 113)
                or after <= before
            ):
                raise RuntimeError("routed TCP must receive the configured kernel rejection")
            results.append({**result, "reject_before": before, "reject_after": after})
        return results

    def relay_bindings(self) -> dict[str, Any]:
        identifier = self.cid("relay")
        self.owned(identifier)
        value = json.loads(self.run(["docker", "inspect", "--format", PORTS, identifier]).stdout)
        expected = {
            "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.manifest["relay_port"])}]
        }
        if value != {"host_config": expected, "network_settings": expected}:
            raise RuntimeError("actual relay bindings must be only the reviewed loopback port")
        return {"container_id": identifier, **value}

    def ingress_network(self) -> str:
        value = self.manifest["relay_ingress"]
        subnet = ipaddress.ip_network(value["subnet"])
        address = ipaddress.ip_address(value["address"])
        if (
            value["network"] != self.deployment + "-relay-ingress"
            or subnet.version != 4
            or not subnet.is_private
            or address not in subnet
            or address in (subnet.network_address, subnet.broadcast_address)
            or subnet.prefixlen != 29
        ):
            raise ValueError("relay ingress must be a nonce-owned private IPv4 fixture network")
        return str(value["network"])

    def repair_relay_ingress(self) -> dict[str, Any]:
        """Publish the owned credential-free relay through its own ingress bridge."""
        name = self.ingress_network()
        ingress = self.manifest["relay_ingress"]
        roles = [*self.roles, *SUPPORTS]
        if set(ingress["containers"]) != set(roles):
            raise ValueError("relay ingress must pin every proof service")
        before = self.services_running(roles)
        for role in roles:
            expected = ingress["containers"][role]
            identifier = self.cid(role)
            attachment = self.attachment(identifier)
            if (
                identifier != expected["id"]
                or attachment["image"] != expected["image"]
                or set(attachment["networks"]) != set(expected["networks"])
            ):
                raise RuntimeError("relay ingress starting container or attachment differs")
        if set(ingress["existing_networks"]) != {self.workers, self.targets}:
            raise ValueError("relay ingress must pin both existing fixture networks")
        for existing, expected_id in ingress["existing_networks"].items():
            value = json.loads(self.run(["docker", "network", "inspect", existing]).stdout)[0]
            members = {
                pin["id"] for pin in ingress["containers"].values() if existing in pin["networks"]
            }
            if (
                value["Id"] != expected_id
                or value["Labels"].get("io.swingset.deployment") != self.deployment
                or set(value["Containers"]) != members
            ):
                raise RuntimeError("existing fixture network or membership differs")
        networks = self.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"]
        ).stdout.splitlines()
        if name in networks:
            raise RuntimeError("relay ingress network already exists")
        subnet = ipaddress.ip_network(ingress["subnet"])
        for existing in networks:
            configs = (
                json.loads(
                    self.run(
                        [
                            "docker",
                            "network",
                            "inspect",
                            "--format",
                            "{{json .IPAM.Config}}",
                            existing,
                        ]
                    ).stdout
                )
                or []
            )
            if any(
                ipaddress.ip_network(value["Subnet"]).version == 4
                and ipaddress.ip_network(value["Subnet"]).overlaps(subnet)
                for value in configs
                if value.get("Subnet")
            ):
                raise RuntimeError("relay ingress overlaps an existing network")
        firewall = self.firewall_snapshot()
        self.run(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--subnet",
                ingress["subnet"],
                "--ipv6=false",
                "--opt",
                "com.docker.network.bridge.enable_ip_masquerade=false",
                "--label",
                "io.swingset.deployment=" + self.deployment,
                name,
            ]
        )
        relay = self.cid("relay")
        self.run(["docker", "network", "connect", "--ip", ingress["address"], name, relay])
        network = json.loads(self.run(["docker", "network", "inspect", name]).stdout)[0]
        if (
            network["Name"] != name
            or network["Driver"] != "bridge"
            or network["Labels"].get("io.swingset.deployment") != self.deployment
            or network["Internal"]
            or network["EnableIPv6"]
            or network["Options"].get("com.docker.network.bridge.enable_ip_masquerade") != "false"
            or {value["Subnet"] for value in network["IPAM"]["Config"]} != {ingress["subnet"]}
            or set(network["Containers"]) != {relay}
        ):
            raise RuntimeError("actual relay ingress network differs")
        for role in roles:
            expected = ingress["containers"][role]
            attachment = self.attachment(self.cid(role))
            wanted = set(expected["networks"]) | ({name} if role == "relay" else set())
            if (
                self.cid(role) != expected["id"]
                or attachment["image"] != expected["image"]
                or set(attachment["networks"]) != wanted
            ):
                raise RuntimeError("relay ingress changed another container or attachment")
            if role == "relay" and attachment["networks"][name]["IPAddress"] != ingress["address"]:
                raise RuntimeError("relay ingress address differs")
        if self.firewall_snapshot() != firewall:
            raise RuntimeError("relay ingress changed a worker firewall")
        bindings = self.relay_bindings()
        self._request("127.0.0.1")
        self._public_denied()
        return {
            "before": before,
            "created": self.services_running(roles),
            "ingress_network": network["Id"],
            "port_bindings": bindings,
            "loopback_nonce_success": True,
            "nonloopback_denied": True,
            "worker_firewalls_unchanged": True,
        }

    def images(self) -> dict[str, Any]:
        for image in self.manifest["images"].values():
            for reference in (image["index"], image["reference"]):
                self.run(["docker", "pull", "--platform", "linux/amd64", reference])
        self.image_checks()
        return {}

    def setup(self) -> dict[str, Any]:
        self.image_checks()
        before = self.inventory()
        if any(item["deployment"] == self.deployment for item in before):
            raise RuntimeError("proof containers already exist")
        networks = self.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"]
        ).stdout.splitlines()
        if self.workers in networks or self.targets in networks:
            raise RuntimeError("proof network name already exists")
        subnets = [self.policy["subnet"], *self.manifest["target_subnets"]]
        for network in networks:
            entries = (
                json.loads(
                    self.run(
                        [
                            "docker",
                            "network",
                            "inspect",
                            "--format",
                            "{{json .IPAM.Config}}",
                            network,
                        ]
                    ).stdout
                )
                or []
            )
            for entry in entries:
                if not entry.get("Subnet"):
                    continue
                existing = ipaddress.ip_network(entry["Subnet"])
                if any(
                    existing.version == proposed.version and existing.overlaps(proposed)
                    for proposed in map(ipaddress.ip_network, subnets)
                ):
                    raise RuntimeError("proof subnet overlaps an existing Docker network")
        directories = ("state", "spill", "runtime", "checker-ipc", "authorization", "holds")
        for name in directories:
            (self.root / name).mkdir(mode=0o755)
        shutil.copyfile(self.root / "hold.txt", self.root / "holds/network-proof")
        init = "import os;from pathlib import Path;root=Path('/init');[(os.chown(root/name,0,0),os.chmod(root/name,0o700),os.chown(root/name,10001,10001)) for name in ('state','spill','runtime','checker-ipc')];os.chown(root/'authorization',0,0);os.chmod(root/'authorization',0o755)"
        mounts = [
            value
            for name in directories[:-1]
            for value in (
                "--mount",
                "type=bind,src=" + str(self.root / name) + ",dst=/init/" + name,
            )
        ]
        self.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--security-opt",
                "no-new-privileges",
                "--label",
                "io.swingset.deployment=" + self.deployment,
                *mounts,
                self.manifest["operator_image"],
                "python",
                "-c",
                init,
            ]
        )
        label = "io.swingset.deployment=" + self.deployment
        self.run(
            [
                "docker",
                "network",
                "create",
                "--internal",
                "--driver",
                "bridge",
                "--subnet",
                self.policy["subnet"],
                "--ipv6=false",
                "--label",
                label,
                self.workers,
            ]
        )
        self.target_network()
        self.run(
            self.compose
            + [
                "up",
                "-d",
                "--no-deps",
                *self.roles,
                "proxy",
                "resolver",
                "owned-mock",
                "control",
                "relay",
            ]
        )
        self.marker_absent()
        self.run(self.compose + ["up", "-d", "--no-deps", "operator"])
        self.ready()
        return {
            "before": before,
            "created": self.services_running([*self.roles, *SUPPORTS]),
        }

    def baseline(self) -> dict[str, Any]:
        self.ready()
        self.services_running([*self.roles, *SUPPORTS])
        self.prove("baseline", "baseline-proof.json")
        return {}

    def target_network(self) -> None:
        command = [
            "docker",
            "network",
            "create",
            "--internal",
            "--driver",
            "bridge",
            "--ipv6",
            "--label",
            "io.swingset.deployment=" + self.deployment,
        ]
        command += ["--opt", ADVERTISEMENT_OPTION + "=0"]
        for subnet in self.manifest["target_subnets"]:
            command += ["--subnet", subnet]
        self.run([*command, self.targets])

    def attachment(self, identifier: str) -> dict[str, Any]:
        result = json.loads(
            self.run(["docker", "inspect", "--format", ATTACHMENT, identifier]).stdout
        )
        if not isinstance(result, dict):
            raise RuntimeError("container attachment must be an object")
        return result

    def firewall_snapshot(self) -> dict[str, list[str]]:
        operator = self.cid("operator")
        self.owned(operator)
        snapshots: dict[str, list[str]] = {}
        for role in self.roles:
            info = self.owned(self.cid(role))
            snapshots[role] = []
            for binary in ("iptables-save", "ip6tables-save"):
                saved = self.run(
                    [
                        "docker",
                        "exec",
                        operator,
                        "nsenter",
                        "--target",
                        str(info["pid"]),
                        "--net",
                        "--",
                        binary,
                    ]
                ).stdout
                snapshots[role].append(
                    "\n".join(
                        re.sub(r"^(:\S+\s+\S+) \[\d+:\d+\]$", r"\1 [0:0]", line)
                        for line in saved.splitlines()
                        if not line.startswith("#")
                    )
                )
        return snapshots

    def repair_partial_targets(self) -> dict[str, Any]:
        return self.repair_targets(replace_partial=True)

    def repair_targets(self, *, replace_partial: bool = False) -> dict[str, Any]:
        """Replace only the reviewed fixture bridge and its four support containers."""
        repair = self.manifest["target_repair"]
        roles = [*self.roles, *SUPPORTS]
        expected = {role: dict(value) for role, value in repair["containers"].items()}
        if set(expected) != set(roles):
            raise ValueError("target repair must pin every proof service")
        before = self.services_running(roles)
        attachments = {}
        for role in roles:
            identifier = self.cid(role)
            if identifier != expected[role]["id"]:
                raise RuntimeError("target repair starting container differs")
            value = self.attachment(identifier)
            if value["image"] != expected[role]["image"]:
                raise RuntimeError("target repair starting image differs")
            attachments[role] = value["networks"]
        network = json.loads(self.run(["docker", "network", "inspect", self.targets]).stdout)[0]
        if (
            network["Id"] != repair["network_id"]
            or network["Labels"].get("io.swingset.deployment") != self.deployment
            or network["Driver"] != "bridge"
            or not network["Internal"]
            or not network["EnableIPv6"]
            or {value["Subnet"] for value in network["IPAM"]["Config"]}
            != set(self.manifest["target_subnets"])
            or set(network["Containers"]) != {expected[role]["id"] for role in TARGET_SUPPORTS}
        ):
            raise RuntimeError("target repair starting network differs")
        partial = repair["partial_role"]
        if partial not in self.roles:
            raise ValueError("target repair partial endpoint is not a worker")
        for role in self.roles:
            wanted = {self.workers, self.targets} if role == partial else {self.workers}
            if set(attachments[role]) != wanted:
                raise RuntimeError("unexpected worker attachment before target repair")
        endpoint = attachments[partial][self.targets]
        if (
            endpoint["EndpointID"] != repair["partial_endpoint_id"]
            or endpoint["NetworkID"] != network["Id"]
        ):
            raise RuntimeError("partial endpoint identity differs")
        firewall = self.firewall_snapshot()
        partial_before = expected[partial]["id"]
        if replace_partial:
            self.run(self.compose + ["stop", "operator"])
            self.run(self.adversarial + ["up", "-d", "--no-deps", "--force-recreate", partial])
            identifier = self.cid(partial)
            self.owned(identifier)
            if identifier == partial_before:
                raise RuntimeError("partial worker recreation did not replace the container")
            if self.attachment(identifier)["image"] != expected[partial]["image"]:
                raise RuntimeError("partial worker recreation changed the image")
            self.marker_absent([partial])
            self.run(self.compose + ["start", "operator"])
            self.ready()
            for role in roles:
                if role != partial and self.cid(role) != expected[role]["id"]:
                    raise RuntimeError("partial worker recreation replaced another service")
            current = self.firewall_snapshot()
            for role in self.roles:
                if role == partial:
                    # A fresh Docker sandbox assigns different embedded-DNS NAT ports.
                    # Compare all filter rules; retain full-table equality for other workers.
                    previous_filter = [self.filter_rules(value) for value in firewall[role]]
                    current_filter = [self.filter_rules(value) for value in current[role]]
                    if previous_filter != current_filter:
                        raise RuntimeError("new partial worker filter rules differ")
                elif current[role] != firewall[role]:
                    raise RuntimeError("partial worker recreation changed another firewall")
            firewall = current
            expected[partial]["id"] = identifier
        else:
            self.run(
                [
                    "docker",
                    "network",
                    "disconnect",
                    "--force",
                    self.targets,
                    expected[partial]["id"],
                ]
            )
        if set(self.attachment(expected[partial]["id"])["networks"]) != {self.workers}:
            raise RuntimeError("partial target attachment was not removed")
        self.run(self.compose + ["rm", "--stop", "--force", *TARGET_SUPPORTS])
        empty = json.loads(self.run(["docker", "network", "inspect", self.targets]).stdout)[0]
        if empty["Id"] != network["Id"] or empty["Containers"]:
            raise RuntimeError("target bridge is not the reviewed empty network")
        self.run(["docker", "network", "rm", self.targets])
        self.target_network()
        created = json.loads(self.run(["docker", "network", "inspect", self.targets]).stdout)[0]
        if created["Id"] == network["Id"] or created["Options"].get(ADVERTISEMENT_OPTION) != "0":
            raise RuntimeError("new target bridge must disable unsolicited advertisements")
        self.run(self.compose + ["up", "-d", "--no-deps", *TARGET_SUPPORTS])
        after = self.services_running(roles)
        for role in roles:
            same = self.cid(role) == expected[role]["id"]
            if same != (role not in TARGET_SUPPORTS):
                raise RuntimeError("target repair replaced an unexpected set of containers")
        if self.firewall_snapshot() != firewall:
            raise RuntimeError("target repair changed a worker firewall")
        return {
            "before": before,
            "created": after,
            "network_before": network["Id"],
            "network_after": created["Id"],
            "worker_firewalls_unchanged": not replace_partial,
            "retained_worker_firewalls_unchanged": True,
            "worker_firewalls_unchanged_during_bridge_rebuild": True,
            "partial_worker_before": partial_before,
            "partial_worker_after": expected[partial]["id"],
            "partial_filter_rules_equivalent": replace_partial,
        }

    @staticmethod
    def filter_rules(saved: str) -> str:
        lines = saved.splitlines()
        if lines.count("*filter") != 1:
            raise RuntimeError("worker filter table is unavailable or duplicated")
        start = lines.index("*filter")
        end = lines.index("COMMIT", start)
        return "\n".join(lines[start : end + 1])

    def adversarial_phase(self) -> dict[str, Any]:
        self.run(self.compose + ["stop", "operator"])
        self.run(self.adversarial + ["up", "-d", "--no-deps", "--force-recreate", *self.roles])
        self.marker_absent()
        self.run(self.compose + ["start", "operator"])
        self.ready()
        return self.adversarial_resume()

    def adversarial_resume(self) -> dict[str, Any]:
        self.ready()
        self.services_running([*self.roles, *SUPPORTS])
        network = json.loads(self.run(["docker", "network", "inspect", self.targets]).stdout)[0]
        if (
            network["Labels"].get("io.swingset.deployment") != self.deployment
            or network["Options"].get(ADVERTISEMENT_OPTION) != "0"
            or set(network["Containers"]) != {self.cid(role) for role in TARGET_SUPPORTS}
        ):
            raise RuntimeError("fixture network must disable unsolicited advertisements")
        for role in self.roles:
            identifier = self.cid(role)
            self.owned(identifier)
            if set(self.attachment(identifier)["networks"]) != {self.workers}:
                raise RuntimeError("worker already has an unexpected attachment")
        firewall = self.firewall_snapshot()
        for role, addresses in self.manifest["adversarial_addresses"].items():
            if role not in self.roles:
                raise ValueError("adversarial role differs")
            self.run(
                [
                    "docker",
                    "network",
                    "connect",
                    "--ip",
                    addresses[0],
                    "--ip6",
                    addresses[1],
                    self.targets,
                    self.cid(role),
                ]
            )
        check = "import runpy,sys;api=runpy.run_path('/operator/security.py');policy=api['load'](__import__('pathlib').Path('/operator/network.json'));container=api['_inspect'](sys.argv[1]);\ntry:api['validate_container'](container,policy)\nexcept ValueError:print('extra attachment refused')\nelse:raise AssertionError('extra attachment accepted')"
        for role in self.roles:
            self.run(
                ["docker", "exec", self.cid("operator"), "python", "-c", check, self.cid(role)]
            )
        if self.firewall_snapshot() != firewall:
            raise RuntimeError("fixture attachment changed a worker firewall")
        self.prove("adversarial", "adversarial-proof.json")
        return {}

    def recreate(self) -> dict[str, Any]:
        for role in self.roles:
            self.run(["docker", "network", "disconnect", self.targets, self.cid(role)])
        before = [self.owned(self.cid(role)) for role in self.roles]
        self.run(self.compose + ["stop", "operator"])
        self.run(self.compose + ["up", "-d", "--no-deps", "--force-recreate", *self.roles])
        self.marker_absent()
        self.run(self.compose + ["start", "operator"])
        self.ready()
        after = [self.owned(self.cid(role)) for role in self.roles]
        if not {item["id"] for item in before}.isdisjoint(item["id"] for item in after):
            raise RuntimeError("proof recreation did not replace every container")
        self.prove("recreated", "recreated-proof.json")
        return {"before": before, "after": after}

    def restart(self) -> dict[str, Any]:
        before = [self.owned(self.cid(role)) for role in self.roles]
        self.run(self.compose + ["stop", "operator"])
        self.run(["docker", "restart", *[self.cid(role) for role in self.roles]])
        self.marker_absent()
        self.run(self.compose + ["start", "operator"])
        self.ready()
        after = [self.owned(self.cid(role)) for role in self.roles]
        if [item["id"] for item in before] != [item["id"] for item in after]:
            raise RuntimeError("same-container restart changed container identity")
        self.prove("recreated", "restart-proof.json")
        return {"before": before, "after": after}

    def _request(self, host: str) -> None:
        client = http.client.HTTPConnection(host, self.manifest["relay_port"], timeout=3)
        try:
            client.request("GET", "/nonce")
            response = client.getresponse()
            if response.status != 200 or response.read() != self.manifest["nonce"].encode():
                raise RuntimeError("owned relay nonce differs")
        finally:
            client.close()

    def _public_denied(self) -> None:
        try:
            self._request(self.manifest["public_ip"])
        except OSError:
            return
        raise RuntimeError("relay accepted a non-loopback connection")

    def relay(self) -> dict[str, Any]:
        before = self.relay_bindings()
        self._request("127.0.0.1")
        self._public_denied()
        self.run(self.compose + ["up", "-d", "--no-deps", "--force-recreate", "relay"])
        time.sleep(0.5)
        after = self.relay_bindings()
        self._request("127.0.0.1")
        self._public_denied()
        if before["container_id"] == after["container_id"]:
            raise RuntimeError("relay recreation retained the old container")
        return {"port_bindings_before": before, "port_bindings_after": after}

    def tunnel(self) -> dict[str, Any]:
        port = self.manifest["relay_port"]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-N",
            "-T",
            "-L",
            f"127.0.0.1:{port}:127.0.0.1:{port}",
            self.manifest["ssh_host"],
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError("owned SSH tunnel exited before opening")
                try:
                    self._request("127.0.0.1")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            self._public_denied()
            return {"tunnel_nonce_success": True, "nonloopback_denied": True}
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            self.logs.append(
                {"command": command, "exit": process.returncode, "stdout": stdout, "stderr": stderr}
            )

    def cleanup(self) -> dict[str, Any]:
        identifiers = self.run(
            [
                "docker",
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                "label=io.swingset.deployment=" + self.deployment,
            ]
        ).stdout.splitlines()
        if len(identifiers) > 12:
            raise RuntimeError("unexpected owned container count")
        for identifier in identifiers:
            self.owned(identifier)
        networks = [self.workers, self.targets]
        if "relay_ingress" in self.manifest:
            networks.append(self.ingress_network())
        for name in networks:
            value = json.loads(self.run(["docker", "network", "inspect", name]).stdout)[0]
            if value["Labels"].get("io.swingset.deployment") != self.deployment or not set(
                value["Containers"]
            ).issubset(identifiers):
                raise RuntimeError("proof network is foreign or contains unrelated containers")
        if identifiers:
            self.run(["docker", "stop", "--time", "10", *identifiers])
            self.run(["docker", "rm", *identifiers])
        for name in networks:
            value = json.loads(self.run(["docker", "network", "inspect", name]).stdout)[0]
            if (
                value["Labels"].get("io.swingset.deployment") != self.deployment
                or value["Containers"]
            ):
                raise RuntimeError("proof network is foreign or still occupied")
            self.run(["docker", "network", "rm", name])
        return {}

    def _write(self, filename: str, result: dict[str, Any]) -> None:
        with (self.root / "raw" / filename).open("x") as stream:
            json.dump(result, stream, sort_keys=True)

    def execute(self, phase: str) -> dict[str, Any]:
        if phase not in PHASES:
            raise ValueError("unknown proof phase")
        if self.manifest.get("execution_blocked"):
            raise RuntimeError("proof execution remains blocked")
        if not phase.startswith("tunnel-") and str(self.root) != self.manifest["server_root"]:
            raise ValueError("proof server directory differs")
        address = ipaddress.ip_address(self.manifest["public_ip"])
        if address.is_loopback or address.is_unspecified:
            raise ValueError("public probe address must be non-loopback")
        port = self.manifest["relay_port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid relay port")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,150}", self.manifest["ssh_host"]):
            raise ValueError("invalid SSH destination")
        subnets = [ipaddress.ip_network(value) for value in self.manifest["target_subnets"]]
        addresses = [ipaddress.ip_address(value) for value in self.manifest["proof_addresses"]]
        if (
            len(subnets) != 2
            or len(addresses) != 2
            or [value.version for value in addresses] != [4, 6]
            or not all(value.is_private for value in subnets)
        ):
            raise ValueError("proof targets require owned private IPv4 and IPv6 endpoints")
        if not all(
            any(address.version == subnet.version and address in subnet for subnet in subnets)
            for address in addresses
        ):
            raise ValueError("proof endpoint is outside its owned target network")
        for name, digest in self.manifest["files"].items():
            path = Path(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or (self.root / path).is_symlink()
                or not (self.root / path).resolve(strict=True).is_relative_to(self.root)
            ):
                raise ValueError("proof file path escapes its root")
            if hashlib.sha256((self.root / path).read_bytes()).hexdigest() != digest:
                raise ValueError("proof bundle file checksum differs")
        (self.root / "raw").mkdir(exist_ok=True)
        path = self.root / "raw" / (phase + ".json")
        if path.exists():
            raise ValueError("preserve the previous phase receipt before a reviewed retry")
        result: dict[str, Any] = {
            "phase": phase,
            "utc": time.time(),
            "deployment": self.deployment,
            "complete": False,
        }
        handlers = {
            "adversarial": self.adversarial_phase,
            "adversarial-resume": self.adversarial_resume,
            "repair-targets": self.repair_targets,
            "repair-partial-targets": self.repair_partial_targets,
            "repair-relay-ingress": self.repair_relay_ingress,
            "tunnel-before": self.tunnel,
            "tunnel-ingress-before": self.tunnel,
            "tunnel-after": self.tunnel,
        }
        handler = handlers.get(phase) or getattr(self, phase)
        try:
            result.update(handler())
            if not phase.startswith("tunnel-"):
                result["after_inventory"] = self.inventory()
            result["complete"] = True
            return result
        finally:
            result["logs"] = self.logs
            self._write(path.name, result)


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("phase", choices=PHASES)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    args = parser.parse_args(argv)
    try:
        Operation(args.root).execute(args.phase)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print("deployment proof failed: " + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
