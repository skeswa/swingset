"""The host proof driver refuses foreign resources before a Docker mutation."""

import hashlib
import json
import subprocess
from copy import deepcopy

import pytest

from swingset.deploy.operation import IMAGE, INFO, PORTS, ROLES, SUPPORTS, Operation


class Docker:
    def __init__(self, manifest):
        self.manifest = manifest
        self.identifiers = {
            role: f"{index:064x}" for index, role in enumerate([*sorted(ROLES), *SUPPORTS], 1)
        }
        self.containers = {
            identifier: {
                "id": identifier,
                "pid": index,
                "running": True,
                "status": "running",
                "role": role,
                "deployment": manifest["deployment"],
                "oom": False,
                "restart": 0,
                "Env": ["PRIVATE=fixture-do-not-log"],
                "Cmd": ["fixture-do-not-log"],
            }
            for index, (role, identifier) in enumerate(self.identifiers.items(), 1)
        }
        self.images = {
            value: {"id": value, "arch": "amd64"}
            for value in [
                manifest["runtime_image"],
                manifest["operator_image"],
                *[image["id"] for image in manifest["images"].values()],
            ]
        }
        self.references = {image["reference"]: image["id"] for image in manifest["images"].values()}
        expected = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15435"}]}
        self.bindings = {"host_config": deepcopy(expected), "network_settings": deepcopy(expected)}
        self.network_label = manifest["deployment"]
        self.network_members = set(self.containers)
        self.mutations = []

    def __call__(self, command):
        if command[:2] == ["docker", "ps"]:
            value = "\n".join(self.containers)
        elif command[:2] == ["docker", "inspect"]:
            fields, identifier = command[-2:]
            if fields == INFO:
                # Emulate Docker's field-limited formatting against records
                # containing private fields the caller must never request.
                value = json.dumps(
                    {
                        key: self.containers[identifier].get(key)
                        for key in (
                            "id",
                            "pid",
                            "running",
                            "status",
                            "role",
                            "deployment",
                            "oom",
                            "restart",
                            "health",
                        )
                    }
                )
            elif fields == PORTS:
                value = json.dumps(self.bindings)
            else:
                raise AssertionError("unexpected broad container inspection")
        elif command[:3] == ["docker", "image", "inspect"]:
            assert command[-2] == IMAGE
            reference = self.references.get(command[-1], command[-1])
            value = json.dumps(self.images[reference])
        elif command[:3] == ["docker", "network", "inspect"]:
            value = json.dumps(
                [
                    {
                        "Labels": {"io.swingset.deployment": self.network_label},
                        "Containers": {identifier: {} for identifier in self.network_members},
                    }
                ]
            )
        elif command[1] == "compose" and command[-4:-1] == ["ps", "-q", "--all"]:
            value = self.identifiers[command[-1]]
        else:
            self.mutations.append(command)
            value = ""
        return subprocess.CompletedProcess(command, 0, value, "")


@pytest.fixture
def proof(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "hold.txt").write_text("owner: fixture\nreason: offline test\n")
    runtime, operator, gateway = ["sha256:" + value * 64 for value in "abc"]
    manifest = {
        "deployment": "fixture-proof-1234",
        "server_root": str(tmp_path),
        "roles": sorted(ROLES),
        "runtime_image": runtime,
        "operator_image": operator,
        "images": {
            "gateway": {
                "reference": "fixture/gateway@" + gateway,
                "index": "fixture/gateway@" + gateway,
                "id": gateway,
            }
        },
        "public_ip": "203.0.113.10",
        "relay_port": 15435,
        "ssh_host": "owned-fixture",
        "proof_addresses": ["172.30.241.5", "fd42:7377:696e:241::5"],
        "target_subnets": ["172.30.241.0/24", "fd42:7377:696e:241::/64"],
        "nonce": "fixture-nonce",
        "files": {},
    }
    policy = {
        "deployment": manifest["deployment"],
        "image_id": runtime,
        "network": "fixture-workers",
        "subnet": "172.30.240.0/24",
        "workers": dict.fromkeys(ROLES, {}),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "config/network.json").write_text(json.dumps(policy))
    docker = Docker(manifest)
    return Operation(tmp_path, runner=docker), docker


def test_inventory_retains_healthcheck_less_records_without_requesting_or_logging_env(proof):
    operation, docker = proof
    healthy = docker.identifiers["relay"]
    docker.containers[healthy]["health"] = "healthy"
    rows = operation.inventory()
    assert len(rows) == len(docker.containers)
    assert next(row for row in rows if row["id"] == healthy)["health"] == "healthy"
    assert sum(row["health"] is None for row in rows) == len(rows) - 1
    serialized = json.dumps({"rows": rows, "logs": operation.logs})
    assert (
        "fixture-do-not-log" not in serialized
        and '"Env"' not in serialized
        and '"Cmd"' not in serialized
    )
    assert not docker.mutations


@pytest.mark.parametrize("change", ["config", "platform"])
def test_setup_refuses_wrong_image_identity_before_creating_any_resource(proof, change):
    operation, docker = proof
    image = docker.images[operation.manifest["runtime_image"]]
    image["id" if change == "config" else "arch"] = (
        "sha256:" + "d" * 64 if change == "config" else "arm64"
    )
    with pytest.raises(RuntimeError, match="CONFIG identity or platform"):
        operation.execute("setup")
    assert not docker.mutations
    receipt = json.loads((operation.root / "raw/setup.json").read_text())
    assert receipt["complete"] is False


def test_cleanup_checks_every_owned_label_before_stopping_or_removing_anything(proof):
    operation, docker = proof
    docker.containers[next(iter(docker.containers))]["deployment"] = "another-deployment"
    with pytest.raises(RuntimeError, match="another deployment"):
        operation.execute("cleanup")
    assert not docker.mutations


@pytest.mark.parametrize("foreign", ["label", "member"])
def test_cleanup_refuses_foreign_network_or_member_before_removing_owned_containers(proof, foreign):
    operation, docker = proof
    if foreign == "label":
        docker.network_label = "another-deployment"
    else:
        docker.network_members.add("f" * 64)
    with pytest.raises(RuntimeError, match="foreign or contains unrelated"):
        operation.execute("cleanup")
    assert not docker.mutations


@pytest.mark.parametrize("side", ["host_config", "network_settings"])
@pytest.mark.parametrize("change", ["public", "port", "extra"])
def test_actual_relay_mapping_refuses_public_wrong_port_or_extra_binding(proof, side, change):
    operation, docker = proof
    mappings = docker.bindings[side]["5432/tcp"]
    if change == "public":
        mappings[0]["HostIp"] = "0.0.0.0"
    elif change == "port":
        mappings[0]["HostPort"] = "15436"
    else:
        mappings.append({"HostIp": "::", "HostPort": "15435"})
    with pytest.raises(RuntimeError, match="actual relay bindings"):
        operation.relay_bindings()
    assert not docker.mutations


def test_actual_relay_mapping_accepts_only_matching_host_and_running_bindings(proof):
    operation, _ = proof
    result = operation.relay_bindings()
    assert (
        result["host_config"]
        == result["network_settings"]
        == {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15435"}]}
    )


def test_drifted_bundle_is_refused_before_any_docker_command(proof):
    operation, docker = proof
    payload = operation.root / "payload"
    payload.write_bytes(b"reviewed")
    operation.manifest["files"] = {"payload": hashlib.sha256(payload.read_bytes()).hexdigest()}
    payload.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        operation.execute("images")
    assert not operation.logs and not docker.mutations


def test_symlink_parent_cannot_redirect_a_reviewed_file_outside_the_proof_root(proof, tmp_path):
    operation, docker = proof
    outside = tmp_path.parent / "outside-proof-payload"
    outside.write_bytes(b"same bytes")
    (operation.root / "redirect").symlink_to(outside.parent, target_is_directory=True)
    operation.manifest["files"] = {
        "redirect/" + outside.name: hashlib.sha256(outside.read_bytes()).hexdigest()
    }
    with pytest.raises(ValueError, match="escapes"):
        operation.execute("images")
    assert not operation.logs and not docker.mutations


def test_top_level_cli_forwards_only_the_explicit_proof_root_and_phase(tmp_path, monkeypatch):
    from swingset import cli
    from swingset.deploy import operation

    calls = []
    monkeypatch.setattr(operation, "main", lambda argv: (calls.append(argv), 17)[1])
    assert cli.main(["deploy-proof", "--root", str(tmp_path), "baseline"]) == 17
    assert calls == [["--root", str(tmp_path), "baseline"]]


@pytest.mark.parametrize("role", [*sorted(ROLES), *SUPPORTS])
def test_every_one_of_the_twelve_services_must_be_running_before_setup_is_ready(
    proof, role, monkeypatch
):
    from swingset.deploy import operation as module

    operation, docker = proof
    docker.containers[docker.identifiers[role]].update(running=False, status="exited")
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: pytest.fail("exited service must fail immediately")
    )
    with pytest.raises(RuntimeError, match="stopped, paused or OOM"):
        operation.services_running([*operation.roles, *SUPPORTS])


@pytest.mark.parametrize("phase", ["setup", "baseline"])
def test_worker_authorization_does_not_mark_a_phase_complete_when_the_resolver_exited(
    proof, monkeypatch, phase
):
    operation, docker = proof
    docker.containers[docker.identifiers["resolver"]].update(running=False, status="exited")
    monkeypatch.setattr(operation, "image_checks", lambda: None)
    monkeypatch.setattr(operation, "inventory", lambda: [])
    monkeypatch.setattr(operation, "marker_absent", lambda: None)
    authorized = []
    monkeypatch.setattr(operation, "ready", lambda: authorized.append(True))
    with pytest.raises(RuntimeError, match="stopped, paused or OOM"):
        operation.execute(phase)
    assert authorized == [True]
    assert json.loads((operation.root / "raw" / (phase + ".json")).read_text())["complete"] is False


def test_running_service_with_an_oom_flag_cannot_pass_setup_readiness(proof):
    operation, docker = proof
    docker.containers[docker.identifiers["control"]]["oom"] = True
    with pytest.raises(RuntimeError, match="stopped, paused or OOM"):
        operation.services_running([*operation.roles, *SUPPORTS])


def test_pending_service_is_polled_until_running_and_all_twelve_are_returned(proof, monkeypatch):
    from swingset.deploy import operation as module

    operation, docker = proof
    resolver = docker.containers[docker.identifiers["resolver"]]
    resolver.update(running=False, status="created")
    polls = []

    def advance(seconds):
        polls.append(seconds)
        resolver.update(running=True, status="running")

    monkeypatch.setattr(module.time, "sleep", advance)
    values = operation.services_running([*operation.roles, *SUPPORTS])
    assert len(values) == 12 and polls == [0.2]


def test_pending_service_cannot_wait_past_its_readiness_budget(proof, monkeypatch):
    from swingset.deploy import operation as module

    operation, docker = proof
    docker.containers[docker.identifiers["resolver"]].update(running=False, status="created")
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: pytest.fail("zero budget must not sleep")
    )
    with pytest.raises(RuntimeError, match="before the deadline"):
        operation.services_running([*operation.roles, *SUPPORTS], timeout=0)


@pytest.fixture
def target_repair(proof):
    from swingset.deploy.operation import ATTACHMENT, TARGET_SUPPORTS

    operation, docker = proof
    pins = {
        role: {"id": identifier, "image": operation.manifest["runtime_image"]}
        for role, identifier in docker.identifiers.items()
    }
    operation.manifest["target_repair"] = {
        "containers": pins,
        "network_id": "d" * 64,
        "partial_role": "acquire-plan",
        "partial_endpoint_id": "e" * 64,
    }
    attached = {role: {operation.workers: {}} for role in ROLES}
    attached["acquire-plan"][operation.targets] = {
        "EndpointID": "e" * 64,
        "NetworkID": "d" * 64,
    }
    network = {
        "Id": "d" * 64,
        "Labels": {"io.swingset.deployment": operation.deployment},
        "Driver": "bridge",
        "Internal": True,
        "EnableIPv6": True,
        "IPAM": {"Config": [{"Subnet": value} for value in operation.manifest["target_subnets"]]},
        "Containers": {pins[role]["id"]: {} for role in TARGET_SUPPORTS},
        "Options": {},
    }

    def run(command):
        if command[:2] == ["docker", "inspect"] and command[-2] == ATTACHMENT:
            role = next(
                role for role, identifier in docker.identifiers.items() if identifier == command[-1]
            )
            value = json.dumps({"image": pins[role]["image"], "networks": attached.get(role, {})})
        elif command[:3] == ["docker", "network", "inspect"]:
            value = json.dumps([network])
        elif command[:3] == ["docker", "network", "disconnect"]:
            docker.mutations.append(command)
            attached["acquire-plan"].pop(operation.targets)
            value = ""
        elif command[:3] == ["docker", "network", "create"]:
            docker.mutations.append(command)
            network.update(Id="f" * 64, Options={"com.docker.network.advertise_addr_nmsgs": "0"})
            value = network["Id"]
        elif "rm" in command and command[:2] == ["docker", "compose"]:
            docker.mutations.append(command)
            network["Containers"] = {}
            value = ""
        elif "up" in command and command[:2] == ["docker", "compose"]:
            docker.mutations.append(command)
            changed = ["acquire-plan"] if command[-1] == "acquire-plan" else TARGET_SUPPORTS
            for index, role in enumerate(changed, 100):
                old = docker.identifiers[role]
                new = f"{(200 if role == 'acquire-plan' else index):064x}"
                docker.identifiers[role] = new
                docker.containers[new] = {**docker.containers.pop(old), "id": new}
                if role == "acquire-plan":
                    attached[role] = {operation.workers: {}}
            value = ""
        elif command[-1] in {"iptables-save", "ip6tables-save"}:
            value = "*filter\n:OUTPUT ACCEPT [0:0]\n-A OUTPUT -j SWINGSET_OUT\nCOMMIT\n"
        else:
            return docker(command)
        return subprocess.CompletedProcess(command, 0, value, "")

    operation.runner = run
    return operation, docker, attached, network


def test_target_repair_replaces_only_fixture_bridge_and_four_supports_without_firewall_changes(
    target_repair,
):
    from swingset.deploy.operation import TARGET_SUPPORTS

    operation, docker, _, network = target_repair
    result = operation.repair_targets()
    assert result["worker_firewalls_unchanged"] is True
    assert result["network_before"] != result["network_after"]
    creation = next(
        command for command in docker.mutations if command[:3] == ["docker", "network", "create"]
    )
    assert "com.docker.network.advertise_addr_nmsgs=0" in creation
    removal = next(command for command in docker.mutations if "--stop" in command)
    assert removal[-4:] == list(TARGET_SUPPORTS)
    assert network["Options"] == {"com.docker.network.advertise_addr_nmsgs": "0"}
    assert not any("iptables" in part for command in docker.mutations for part in command)


@pytest.mark.parametrize(
    "drift", ["container", "image", "label", "network", "member", "attachment", "endpoint"]
)
def test_target_repair_refuses_drift_before_any_mutation(target_repair, drift):
    operation, docker, attached, network = target_repair
    if drift == "container":
        operation.manifest["target_repair"]["containers"]["derive"]["id"] = "0" * 64
    elif drift == "image":
        # Keep expected pins separate from the simulated daemon response.
        original = operation.runner

        def wrong_image(command):
            value = original(command)
            from swingset.deploy.operation import ATTACHMENT

            if command[:2] == ["docker", "inspect"] and command[-2] == ATTACHMENT:
                data = json.loads(value.stdout)
                data["image"] = "sha256:" + "0" * 64
                return subprocess.CompletedProcess(command, 0, json.dumps(data), "")
            return value

        operation.runner = wrong_image
    elif drift == "label":
        network["Labels"]["io.swingset.deployment"] = "foreign"
    elif drift == "network":
        network["Id"] = "0" * 64
    elif drift == "member":
        network["Containers"]["0" * 64] = {}
    elif drift == "attachment":
        attached["derive"]["foreign-network"] = {}
    else:
        attached["acquire-plan"][operation.targets]["EndpointID"] = "0" * 64
    with pytest.raises(RuntimeError):
        operation.repair_targets()
    assert not docker.mutations


@pytest.mark.parametrize(
    "family,bad_errno,source,counters",
    [
        (0, 101, "172.30.241.30", [3, 4]),
        (0, 113, "172.30.240.10", [3, 4]),
        (0, 113, "172.30.241.30", [3, 3]),
        (1, 101, "fd42:7377:696e:241::30", [3, 4, 5, 6]),
        (1, 113, "fd42:7377:696e:241::30", [3, 4, 5, 6]),
    ],
)
def test_routed_tcp_refuses_absent_route_wrong_source_and_unexercised_reject(
    proof, monkeypatch, family, bad_errno, source, counters
):
    operation, docker = proof
    identifier = docker.identifiers["acquire-plan"]
    operation.manifest["adversarial_addresses"] = {
        "acquire-plan": ["172.30.241.30", "fd42:7377:696e:241::30"]
    }
    counts = iter(counters)
    monkeypatch.setattr(operation, "_counter", lambda *args: next(counts))
    original = operation.runner

    def run(command):
        if command[:2] != ["docker", "exec"]:
            return original(command)
        index = operation.manifest["proof_addresses"].index(command[-2])
        value = {
            "address": command[-2],
            "source": source if index == family else command[-1],
            "errno": bad_errno if index == family else 113,
            "ipv6_flags": None if not index else 128,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    operation.runner = run
    with pytest.raises(RuntimeError, match="routed TCP"):
        operation.routable_tcp(identifier, 123, docker.identifiers["operator"])


def test_routed_tcp_records_separate_ipv4_and_ipv6_administrative_rejection(proof, monkeypatch):
    operation, docker = proof
    identifier = docker.identifiers["acquire-plan"]
    operation.manifest["adversarial_addresses"] = {
        "acquire-plan": ["172.30.241.30", "fd42:7377:696e:241::30"]
    }
    counts = iter([3, 4, 12, 13])
    monkeypatch.setattr(operation, "_counter", lambda *args: next(counts))
    original = operation.runner

    def run(command):
        if command[:2] != ["docker", "exec"]:
            return original(command)
        value = {
            "address": command[-2],
            "source": command[-1],
            "errno": 13 if ":" in command[-2] else 113,
            "ipv6_flags": 128 if ":" in command[-2] else None,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    operation.runner = run
    results = operation.routable_tcp(identifier, 123, docker.identifiers["operator"])
    assert [value["errno"] for value in results] == [113, 13]
    assert all(value["reject_after"] > value["reject_before"] for value in results)


def test_firewall_snapshot_excludes_save_timestamps_and_counters_but_preserves_all_rules(proof):
    operation, docker = proof
    original = operation.runner
    calls = 0

    def run(command):
        nonlocal calls
        if command[-1] not in {"iptables-save", "ip6tables-save"}:
            return original(command)
        calls += 1
        return subprocess.CompletedProcess(
            command,
            0,
            f"# Generated at {calls}\n*filter\n:OUTPUT ACCEPT [{calls}:{calls * 60}]\n-A OUTPUT -j SWINGSET_OUT\nCOMMIT\n# Completed at {calls}\n",
            "",
        )

    operation.runner = run
    first = operation.firewall_snapshot()
    assert first == operation.firewall_snapshot()
    assert all("-A OUTPUT -j SWINGSET_OUT" in saved for pair in first.values() for saved in pair)
    assert not docker.mutations


def test_partial_repair_replaces_only_partial_worker_before_fixture_rebuild(
    target_repair, monkeypatch
):
    from swingset.deploy.operation import TARGET_SUPPORTS

    operation, docker, _, _ = target_repair
    original = dict(docker.identifiers)
    transitions = []
    monkeypatch.setattr(
        operation, "marker_absent", lambda roles=None: transitions.append(("absent", roles))
    )
    monkeypatch.setattr(operation, "ready", lambda: transitions.append(("ready",)))
    result = operation.repair_partial_targets()
    changed = {role for role in original if original[role] != docker.identifiers[role]}
    assert changed == {"acquire-plan", *TARGET_SUPPORTS}
    assert transitions == [("absent", ["acquire-plan"]), ("ready",)]
    assert result["partial_worker_before"] == original["acquire-plan"]
    assert result["partial_worker_after"] != original["acquire-plan"]
    assert result["partial_filter_rules_equivalent"] is True
    assert result["retained_worker_firewalls_unchanged"] is True
    assert not any(
        command[:3] == ["docker", "network", "disconnect"] for command in docker.mutations
    )
    compose_mutations = [
        command for command in docker.mutations if command[:2] == ["docker", "compose"]
    ]
    assert compose_mutations[0][-2:] == ["stop", "operator"]
    assert compose_mutations[1][-2:] == ["--force-recreate", "acquire-plan"]
    assert compose_mutations[2][-2:] == ["start", "operator"]
    assert compose_mutations[3][-4:] == list(TARGET_SUPPORTS)


@pytest.mark.parametrize(
    "failure", ["old_authorization", "fresh_authorization", "new_rules", "retained_rules"]
)
def test_partial_repair_stops_before_fixture_deletion_on_authorization_or_firewall_failure(
    target_repair, monkeypatch, failure
):
    operation, docker, _, _ = target_repair

    def fail(*args):
        raise RuntimeError("fixture authorization rejected")

    monkeypatch.setattr(
        operation, "marker_absent", fail if failure == "old_authorization" else lambda *args: None
    )
    monkeypatch.setattr(
        operation, "ready", fail if failure == "fresh_authorization" else lambda: None
    )
    if failure in {"new_rules", "retained_rules"}:
        calls = 0
        original = operation.firewall_snapshot

        def changed_snapshot():
            nonlocal calls
            calls += 1
            values = original()
            if calls > 1:
                role = "acquire-plan" if failure == "new_rules" else "derive"
                values[role] = [
                    value.replace("-A OUTPUT -j SWINGSET_OUT", "-A OUTPUT -j ACCEPT")
                    for value in values[role]
                ]
            return values

        monkeypatch.setattr(operation, "firewall_snapshot", changed_snapshot)
    with pytest.raises(RuntimeError):
        operation.repair_partial_targets()
    assert not any("rm" in command for command in docker.mutations)
    assert not any(
        command[:3] == ["docker", "network", "disconnect"] for command in docker.mutations
    )


def test_equivalent_new_worker_filter_comparison_keeps_rules_and_ignores_only_other_tables():
    first = "*filter\n:OUTPUT ACCEPT [0:0]\n-A OUTPUT -j SWINGSET_OUT\nCOMMIT\n*nat\n-A OUTPUT -j DNAT --to-destination :12345\nCOMMIT"
    second = first.replace(":12345", ":23456")
    assert Operation.filter_rules(first) == Operation.filter_rules(second)
    assert Operation.filter_rules(first) != Operation.filter_rules(
        first.replace("-j SWINGSET_OUT", "-j ACCEPT")
    )


@pytest.fixture
def relay_ingress(proof, monkeypatch):
    from swingset.deploy.operation import ATTACHMENT

    operation, docker = proof
    networks = {role: {operation.workers} for role in ROLES}
    networks.update(
        proxy={operation.workers, operation.targets},
        resolver={operation.workers},
        relay={operation.workers, operation.targets},
        **{"owned-mock": {operation.targets}, "control": {operation.targets}, "operator": {"none"}},
    )
    pins = {
        role: {
            "id": identifier,
            "image": operation.manifest["runtime_image"],
            "networks": sorted(networks[role]),
        }
        for role, identifier in docker.identifiers.items()
    }
    name = operation.deployment + "-relay-ingress"
    operation.manifest["relay_ingress"] = {
        "network": name,
        "subnet": "172.30.242.0/29",
        "address": "172.30.242.2",
        "containers": pins,
        "existing_networks": {operation.workers: "d" * 64, operation.targets: "e" * 64},
    }
    existing = {
        network: {
            "Name": network,
            "Id": identifier,
            "Labels": {"io.swingset.deployment": operation.deployment},
            "Containers": {pin["id"]: {} for pin in pins.values() if network in pin["networks"]},
            "IPAM": {
                "Config": [
                    {
                        "Subnet": "172.30.240.0/24"
                        if network == operation.workers
                        else "172.30.241.0/24"
                    }
                ]
            },
        }
        for network, identifier in operation.manifest["relay_ingress"]["existing_networks"].items()
    }
    calls = []
    monkeypatch.setattr(operation, "_request", lambda host: calls.append(host))
    monkeypatch.setattr(operation, "_public_denied", lambda: calls.append("public-denied"))

    def run(command):
        if command[:2] == ["docker", "inspect"] and command[-2] == ATTACHMENT:
            role = next(
                role for role, identifier in docker.identifiers.items() if identifier == command[-1]
            )
            value = {
                "image": pins[role]["image"],
                "networks": {network: {"IPAddress": "172.30.242.2"} for network in networks[role]},
            }
            output = json.dumps(value)
        elif command[:3] == ["docker", "network", "ls"]:
            output = "\n".join(existing)
        elif command[:3] == ["docker", "network", "inspect"]:
            value = existing[command[-1]]
            output = json.dumps(value["IPAM"]["Config"] if "--format" in command else [value])
        elif command[:3] == ["docker", "network", "create"]:
            docker.mutations.append(command)
            existing[name] = {
                "Name": name,
                "Id": "f" * 64,
                "Driver": "bridge",
                "Internal": False,
                "EnableIPv6": False,
                "Options": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
                "Labels": {"io.swingset.deployment": operation.deployment},
                "Containers": {},
                "IPAM": {"Config": [{"Subnet": "172.30.242.0/29"}]},
            }
            output = "f" * 64
        elif command[:3] == ["docker", "network", "connect"]:
            docker.mutations.append(command)
            networks["relay"].add(name)
            existing[name]["Containers"][docker.identifiers["relay"]] = {}
            output = ""
        elif command[-1] in {"iptables-save", "ip6tables-save"}:
            output = "*filter\n-A OUTPUT -j SWINGSET_OUT\nCOMMIT\n"
        else:
            return docker(command)
        return subprocess.CompletedProcess(command, 0, output, "")

    operation.runner = run
    return operation, docker, existing, networks, calls


def test_relay_ingress_attaches_only_same_relay_with_no_worker_rule_or_id_changes(relay_ingress):
    operation, docker, existing, networks, calls = relay_ingress
    before = dict(docker.identifiers)
    result = operation.repair_relay_ingress()
    assert result["worker_firewalls_unchanged"] is True
    assert result["loopback_nonce_success"] is True and result["nonloopback_denied"] is True
    assert docker.identifiers == before
    assert calls == ["127.0.0.1", "public-denied"]
    name = operation.ingress_network()
    assert [role for role, values in networks.items() if name in values] == ["relay"]
    assert set(existing[name]["Containers"]) == {before["relay"]}
    assert len(docker.mutations) == 2
    assert docker.mutations[0][:3] == ["docker", "network", "create"]
    assert "com.docker.network.bridge.enable_ip_masquerade=false" in docker.mutations[0]
    assert docker.mutations[1][-1] == before["relay"]


@pytest.mark.parametrize(
    "drift",
    [
        "foreign_name",
        "public_subnet",
        "container",
        "network_id",
        "foreign_member",
        "overlap",
        "existing_ingress",
    ],
)
def test_relay_ingress_refuses_drift_and_subnet_overlap_before_any_mutation(relay_ingress, drift):
    operation, docker, existing, _, _ = relay_ingress
    ingress = operation.manifest["relay_ingress"]
    if drift == "foreign_name":
        ingress["network"] = "foreign-ingress"
    elif drift == "public_subnet":
        ingress.update(subnet="8.8.8.0/29", address="8.8.8.2")
    elif drift == "container":
        ingress["containers"]["relay"]["id"] = "0" * 64
    elif drift == "network_id":
        existing[operation.workers]["Id"] = "0" * 64
    elif drift == "foreign_member":
        existing[operation.targets]["Containers"]["0" * 64] = {}
    elif drift == "overlap":
        ingress.update(subnet="172.30.241.0/29", address="172.30.241.2")
    else:
        existing[ingress["network"]] = {}
    with pytest.raises((ValueError, RuntimeError)):
        operation.repair_relay_ingress()
    assert not docker.mutations


def test_relay_ingress_refuses_configured_only_mapping_without_actual_publication(relay_ingress):
    operation, docker, _, _, calls = relay_ingress
    docker.bindings["network_settings"] = {}
    with pytest.raises(RuntimeError, match="actual relay bindings"):
        operation.repair_relay_ingress()
    assert calls == []


def test_cleanup_includes_only_reviewed_nonce_owned_relay_ingress_network(relay_ingress):
    operation, docker, _, _, _ = relay_ingress
    seen = []
    original = docker.__call__

    def run(command):
        if command[:3] == ["docker", "network", "inspect"]:
            seen.append(command[-1])
            members = (
                {}
                if any(c[:2] == ["docker", "rm"] for c in docker.mutations)
                else {identifier: {} for identifier in docker.containers}
            )
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    [
                        {
                            "Labels": {"io.swingset.deployment": operation.deployment},
                            "Containers": members,
                        }
                    ]
                ),
                "",
            )
        return original(command)

    operation.runner = run
    operation.cleanup()
    assert seen.count(operation.ingress_network()) == 2
    assert any(
        command == ["docker", "network", "rm", operation.ingress_network()]
        for command in docker.mutations
    )
