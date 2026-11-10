"""Rehearsal traffic stays bound to its stored database and owned destinations."""

import json
from uuid import uuid4

import httpx
import pytest

from swingset.deploy.rehearsal import BackupTarget, Policy, RehearsalHub, RehearsalTransport
from swingset.state import environment


class Connection:
    def __init__(self, mode, identifier):
        self.row = mode, identifier

    def execute(self, statement):
        assert (
            statement
            == "SELECT environment,database_id FROM ops.environment_identity WHERE singleton=1"
        )
        return self

    def fetchone(self):
        return self.row


@pytest.fixture
def policy(tmp_path, monkeypatch):
    identifier = str(uuid4())
    document = {
        "database_ids": [identifier],
        "dataset": "skeswa/swingset-rehearsal-test",
        "branch": "dryrun-fixture",
        "backup_bucket": "swingset-backups",
        "backup_endpoint": "https://owned.r2.cloudflarestorage.com",
        "proxy_url": "http://172.30.240.2:3128",
        "mock_origins": ["http://owned-mock.test:8080"],
    }
    path = tmp_path / "rehearsal.json"
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    monkeypatch.setenv("SWINGSET_REHEARSAL_POLICY", str(path))
    monkeypatch.delenv("SWINGSET_ENV", raising=False)
    return identifier, document, path


def test_dryrun_requires_policy_bound_identity_and_real_adapter(policy):
    identifier, _, _ = policy
    hub = RehearsalHub(token="fixture")
    connection = Connection("dryrun", identifier)
    environment.ensure_external_allowed(connection, "publication", hub)
    with pytest.raises(environment.EnvironmentRefused, match="identity"):
        environment.ensure_external_allowed(Connection("dryrun", str(uuid4())), "publication", hub)
    with pytest.raises(environment.EnvironmentRefused):
        environment.ensure_external_allowed(connection, "publication", object())
    with pytest.raises(environment.EnvironmentRefused):
        environment.ensure_external_allowed(Connection("dev", identifier), "publication", hub)


@pytest.mark.parametrize(
    "name", ["skeswa/swingset", "skeswa/swingset-archive", "skeswa/swingset-backup-sets"]
)
def test_live_huggingface_repositories_are_refused_before_api_construction(policy, name):
    _, document, path = policy
    document["dataset"] = name
    path.write_text(json.dumps(document))
    with pytest.raises(environment.EnvironmentRefused):
        RehearsalHub(token="fixture")


@pytest.mark.parametrize("field,value", [("repo_id", "skeswa/swingset"), ("revision", "main")])
def test_rehearsal_hub_refuses_mutated_target_before_any_api_call(policy, field, value):
    class Api:
        def repo_info(self, *args, **kwargs):
            raise AssertionError("the live repository must never be contacted")

    hub = RehearsalHub(token="fixture", api=Api())
    setattr(hub, field, value)
    with pytest.raises(environment.EnvironmentRefused):
        hub.head()


def test_owned_mock_only_transport_blocks_collection_redirect_and_direct_ip(policy):
    requested = []
    transport = RehearsalTransport(
        httpx.MockTransport(
            lambda request: (requested.append(str(request.url)), httpx.Response(200))[1]
        )
    )
    connection = Connection("dryrun", policy[0])
    environment.ensure_external_allowed(connection, "source requests", transport)
    with httpx.Client(transport=transport) as client:
        assert client.get("http://owned-mock.test:8080/robots.txt").status_code == 200
        for target in [
            "https://www.worldsdc.com/",
            "http://203.0.113.1:8080/",
            "http://owned-mock.test:8081/",
            "http://owned-mock.test:8080.evil.test/",
        ]:
            with pytest.raises((environment.EnvironmentRefused, httpx.InvalidURL)):
                client.get(target)
    assert requested == ["http://owned-mock.test:8080/robots.txt"]


def test_backup_destination_is_exact_and_wrong_bucket_has_no_external_effect(policy):
    connection = Connection("dryrun", policy[0])
    environment.ensure_external_allowed(
        connection,
        "backup upload",
        BackupTarget("https://owned.r2.cloudflarestorage.com", "swingset-backups"),
    )
    for endpoint, bucket in [
        ("https://other.r2.cloudflarestorage.com", "swingset-backups"),
        ("https://owned.r2.cloudflarestorage.com", "rhuidean-backups"),
    ]:
        with pytest.raises(environment.EnvironmentRefused):
            environment.ensure_external_allowed(
                connection, "backup upload", BackupTarget(endpoint, bucket)
            )


def test_policy_cannot_be_world_writable_or_select_a_public_proxy(policy):
    _, document, path = policy
    path.chmod(0o622)
    with pytest.raises(environment.EnvironmentRefused):
        Policy.load()
    path.chmod(0o600)
    document["proxy_url"] = "http://8.8.8.8:3128"
    path.write_text(json.dumps(document))
    with pytest.raises(environment.EnvironmentRefused):
        Policy.load()


def test_scheduler_consumes_held_and_missed_slots_once_and_never_repeats_after_clock_rewind():
    from swingset.deploy.scheduler import Task, consume

    task = Task("fixture", (), 60, 0, 1, 1)
    seen = {}
    assert consume(task, 600, seen)
    assert not consume(task, 601, seen)
    assert not consume(task, 500, seen)
    assert consume(task, 1200, seen)
    assert seen == {"fixture": 20}
    assert not consume(task, 1201, seen)


def test_scheduler_hold_checks_need_existing_directory_and_do_not_release_other_holds(
    tmp_path, monkeypatch
):
    from swingset.deploy.scheduler import held

    monkeypatch.setenv("SWINGSET_OOB_HOLD", "0")
    directory = tmp_path / "holds"
    with pytest.raises(RuntimeError):
        held(directory)
    directory.mkdir()
    first = directory / "operator"
    first.write_text("owner: fixture\n")
    assert held(directory)
    assert first.read_text() == "owner: fixture\n"
    first.unlink()
    assert not held(directory)
    monkeypatch.setenv("SWINGSET_OOB_HOLD", "1")
    assert held(directory)


def test_scheduler_stops_a_real_uncooperative_child_within_its_existing_grace():
    import time

    from swingset.deploy.scheduler import Task, child

    task = Task(
        "fixture-timeout",
        ("-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"),
        60,
        0,
        0.2,
        0.2,
    )
    before = time.monotonic()
    assert child(task, lambda: False, lambda: None) != 0
    assert time.monotonic() - before < 3


def test_scheduler_reaps_an_orphan_writer_group_after_the_cli_parent_exits(tmp_path):
    import subprocess
    import sys

    ready, output = tmp_path / "peer-ready", tmp_path / "peer-writes"
    code = """import os,signal,sys,time
from pathlib import Path
ready,output=map(Path,sys.argv[1:])
if os.fork():
 while not ready.exists():time.sleep(.01)
 os._exit(0)
signal.signal(signal.SIGTERM,signal.SIG_IGN)
ready.write_text(str(os.getpgrp()))
while True:
 output.write_text(str(time.time()))
 time.sleep(.01)
"""
    supervisor = """import ctypes,json,os,signal,sys,time
from pathlib import Path
from swingset.deploy.scheduler import Task,child
if sys.platform=='linux':
 assert ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0
ready,output=map(Path,sys.argv[1:3]);beats=[];started=time.monotonic()
task=Task('orphan-writer',('-c',sys.argv[3],str(ready),str(output)),60,0,2,.2)
try:
 try:result=child(task,lambda:False,lambda:beats.append(time.monotonic()))
 except PermissionError:
  if sys.platform!='darwin':raise
  result=1
 assert result!=0 and beats and time.monotonic()-started<2
 before=output.read_text();time.sleep(.1)
 assert output.read_text()==before,'task returned with a writer still alive'
 if sys.platform=='linux':
  try:os.killpg(int(ready.read_text()),0)
  except ProcessLookupError:pass
  else:raise AssertionError('owned Linux group still exists after cleanup')
 print(json.dumps({'failed':True,'writes_stopped':True}))
finally:
 if ready.exists():
  try:os.killpg(int(ready.read_text()),signal.SIGKILL)
  except (ProcessLookupError,PermissionError):pass
"""
    result = subprocess.run(
        [sys.executable, "-c", supervisor, str(ready), str(output), code],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"failed": True, "writes_stopped": True}


def test_proxy_configuration_rejects_ip_wildcard_and_cross_host_inputs():
    from swingset.deploy.configuration import endpoints, squid

    for value in [
        "https://203.0.113.1",
        "https://[2001:db8::1]",
        "https://*.example.test",
        "https://allowed.test/path",
        "https://allowed.test@forbidden.test/path",
        "https://allowed.test:8080",
    ]:
        with pytest.raises(ValueError):
            endpoints([value])
    config = squid(["https://approved.test", "http://owned.test:8080"])
    assert "dstdomain -n approved.test" in config and "http_access deny all" in config
    assert "%ru" not in config  # Presigned URLs and other query secrets never enter logs.


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sha256:" + "a" * 64,
        "swingset-runtime:latest",
        "swingset-runtime@sha256:" + "g" * 64,
        "swingset-runtime@sha256:" + "a" * 63,
    ],
)
def test_deployment_artifact_refuses_missing_config_tag_and_malformed_digest(value):
    from swingset.deploy.configuration import runtime_artifact

    with pytest.raises(ValueError, match="qualified OCI"):
        runtime_artifact(value)


@pytest.mark.parametrize("entrypoint", ["runtime", "utility"])
def test_deployment_entrypoints_reject_config_identity_before_credentials_or_database(
    monkeypatch, entrypoint
):
    import sys

    from swingset.deploy import runtime, utility

    monkeypatch.setenv("SWINGSET_ENV", "dryrun")
    monkeypatch.setenv("SWINGSET_RUNTIME_IMAGE", "sha256:" + "a" * 64)
    role = "jobs" if entrypoint == "runtime" else "operator"
    monkeypatch.setattr(runtime, "authorized", lambda *args, **kwargs: {"role": role})
    monkeypatch.setattr(
        runtime,
        "secrets",
        lambda *args: pytest.fail("credentials were read before artifact validation"),
    )
    monkeypatch.setattr(
        utility,
        "configure",
        lambda *args: pytest.fail("database was opened before artifact validation"),
    )
    with pytest.raises(ValueError, match="qualified OCI"):
        if entrypoint == "runtime":
            monkeypatch.setattr(sys, "argv", ["runtime", "readiness"])
            runtime.main()
        else:
            utility.main(["--role", "operator", "--", "doctor"])


def test_deployment_preserves_artifact_for_backup_metadata_independently_of_config(monkeypatch):
    import os
    import sys

    from swingset.backup.snapshot import current_runtime_identity
    from swingset.deploy import runtime

    artifact = "fixture/swingset-runtime@sha256:" + "a" * 64
    monkeypatch.setenv("SWINGSET_ENV", "dryrun")
    monkeypatch.setenv("SWINGSET_IMAGE", "sha256:" + "b" * 64)
    monkeypatch.setenv("SWINGSET_RUNTIME_IMAGE", artifact)
    monkeypatch.setattr(runtime, "authorized", lambda *args, **kwargs: {"role": "jobs"})
    observed = []
    monkeypatch.setattr(runtime, "liveness", lambda path, role: observed.append(role))
    monkeypatch.setattr(sys, "argv", ["runtime", "liveness"])
    runtime.main()
    assert observed == ["jobs"]
    assert current_runtime_identity() == artifact
    assert os.environ["SWINGSET_IMAGE"] != artifact


def test_health_start_waits_for_namespace_grant_before_reading_credentials(monkeypatch):
    import sys

    from swingset.deploy import health, runtime

    observed = []

    def grant(policy, directory):
        observed.append("grant")
        if observed.count("grant") < 3:
            raise RuntimeError("namespace grant is not ready")
        return {"role": "health"}

    def credentials(directory, role):
        assert observed.count("grant") == 3
        assert role == "health"
        observed.append("credentials")

    monkeypatch.setenv("SWINGSET_ENV", "dryrun")
    monkeypatch.setenv("SWINGSET_RUNTIME_IMAGE", "fixture/swingset@sha256:" + "a" * 64)
    monkeypatch.setattr(runtime.security, "require_authorization", grant)
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: observed.append("wait"))
    monkeypatch.setattr(runtime, "secrets", credentials)
    monkeypatch.setattr(health, "main", lambda args: observed.append("watchdog"))
    monkeypatch.setattr(sys, "argv", ["runtime", "health"])

    runtime.main()

    assert observed == ["grant", "wait", "grant", "wait", "grant", "credentials", "watchdog"]


@pytest.fixture
def network_policy():
    return {
        "deployment": "fixture-stage2-1234",
        "image_id": "sha256:" + "a" * 64,
        "network": "fixture-private",
        "subnet": "172.30.240.0/24",
        "proxy": "172.30.240.2",
        "dns": "172.30.240.4",
        "workers": {"derive": {"address": "172.30.240.10", "database": "172.30.240.3"}},
    }


@pytest.fixture
def inspected_worker(network_policy):
    return {
        "Config": {
            "User": "10001:10001",
            "Labels": {
                "io.swingset.deployment": network_policy["deployment"],
                "io.swingset.role": "derive",
            },
        },
        "Image": network_policy["image_id"],
        "State": {"Running": True},
        "HostConfig": {
            "Privileged": False,
            "CapAdd": None,
            "CapDrop": ["ALL"],
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "NetworkSettings": {
            "Networks": {"fixture-private": {"IPAddress": "172.30.240.10", "GlobalIPv6Address": ""}}
        },
        "Mounts": [],
    }


def test_namespace_authorization_refuses_extra_network_or_privileged_container(
    network_policy, inspected_worker
):
    from swingset.deploy.security import validate_container

    assert validate_container(inspected_worker, network_policy) == "derive"
    inspected_worker["NetworkSettings"]["Networks"]["dokploy-network"] = {"IPAddress": "10.0.0.1"}
    with pytest.raises(ValueError, match="network attachment"):
        validate_container(inspected_worker, network_policy)
    inspected_worker["NetworkSettings"]["Networks"].pop("dokploy-network")
    inspected_worker["HostConfig"]["CapAdd"] = ["NET_ADMIN"]
    with pytest.raises(ValueError, match="capabilities"):
        validate_container(inspected_worker, network_policy)


def test_resolver_bind_capability_is_never_accepted_for_an_application_worker(
    network_policy, inspected_worker
):
    from swingset.deploy.security import validate_container

    inspected_worker["HostConfig"]["CapAdd"] = ["NET_BIND_SERVICE"]
    with pytest.raises(ValueError, match="capabilities"):
        validate_container(inspected_worker, network_policy)


@pytest.mark.parametrize(
    "field,value", [("PidMode", "host"), ("IpcMode", "host"), ("CgroupnsMode", "host")]
)
def test_namespace_authorization_refuses_shared_process_namespaces(
    network_policy, inspected_worker, field, value
):
    from swingset.deploy.security import validate_container

    inspected_worker["HostConfig"][field] = value
    with pytest.raises(ValueError, match="namespace"):
        validate_container(inspected_worker, network_policy)


@pytest.mark.parametrize(
    "destination", ["/host", "/run/secrets/scratch-admin-dsn", "/run/secrets/hf-token"]
)
def test_namespace_authorization_refuses_unexpected_mount_or_credential(
    network_policy, inspected_worker, destination
):
    from swingset.deploy.security import validate_container

    inspected_worker["Mounts"] = [
        {"Source": "/owned/fixture", "Destination": destination, "RW": False}
    ]
    with pytest.raises(ValueError, match="mount|credential"):
        validate_container(inspected_worker, network_policy)


@pytest.mark.parametrize(
    "exit_code,oom,visible", [(0, False, False), (1, False, True), (0, True, True)]
)
def test_status_optional_utility_success_is_absent_but_failure_or_oom_alerts(
    tmp_path, monkeypatch, network_policy, exit_code, oom, visible
):
    import subprocess

    from swingset.deploy import security

    network_policy["workers"]["operator"] = {"required": False}

    def command(arguments, **kwargs):
        if arguments[:3] == ["docker", "ps", "-aq"]:
            output = "derive\noperator\n"
        elif arguments[:2] == ["docker", "inspect"]:
            operator = arguments[-1] == "operator"
            output = json.dumps(
                {
                    "deployment": network_policy["deployment"],
                    "role": "operator" if operator else "derive",
                    "running": not operator,
                    "exit_code": exit_code if operator else 0,
                    "oom_killed": oom if operator else False,
                }
            )
        else:
            output = ""
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(security, "_run", command)
    security.export_status(tmp_path, network_policy)
    record = json.loads((tmp_path / "containers.json").read_text())
    assert record["complete_inventory"] and record["expected_roles"] == ["derive"]
    assert any(item["role"] == "operator" for item in record["containers"]) == visible


def test_namespace_rules_cover_every_interface_and_ipv6_has_no_external_exception(network_policy):
    from swingset.deploy.security import rules

    ipv4 = rules(network_policy, "derive")
    assert all("-i" not in rule for rule in ipv4)
    assert [rule[rule.index("-d") + 1] for rule in ipv4 if "-d" in rule] == [
        "172.30.240.3/32",
        "172.30.240.2/32",
        "172.30.240.4/32",
        "172.30.240.4/32",
    ]
    ipv6 = rules(network_policy, "derive", ipv6=True)
    assert not any("-d" in rule for rule in ipv6)
    assert ipv4[-1][1] == ipv6[-1][1] == "REJECT"


def test_recreated_container_cannot_use_an_old_authorization_file(
    tmp_path, monkeypatch, network_policy
):
    from swingset.deploy.security import require_authorization

    policy_file = tmp_path / "network.json"
    policy_file.write_text(json.dumps(network_policy))
    markers = tmp_path / "authorization"
    markers.mkdir()
    (markers / (("a" * 64) + ".json")).write_text("{}")
    monkeypatch.setenv("HOSTNAME", "b" * 12)
    with pytest.raises(RuntimeError, match="waiting"):
        require_authorization(policy_file, markers)


def test_tunnel_forwards_only_loopback_and_refuses_ssh_option_injection():
    from swingset.deploy.tunnel import ssh_arguments

    arguments = ssh_arguments("rhuidean", 15435, 15435)
    assert "127.0.0.1:15435:127.0.0.1:15435" in arguments
    assert "ExitOnForwardFailure=yes" in arguments
    for host in ["-oProxyCommand=bad", "rhuidean;bad", "rhuidean\nbad"]:
        with pytest.raises(ValueError):
            ssh_arguments(host, 15435, 15435)


def test_manual_rehearsal_publish_honors_oob_hold_but_metadata_reads_remain_allowed(
    policy, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    class Api:
        def __init__(self):
            self.writes = []

        def repo_info(self, *args, **kwargs):
            return SimpleNamespace(sha="A")

        def create_commit(self, **kwargs):
            self.writes.append(kwargs)
            return SimpleNamespace(oid="B")

    directory = tmp_path / "holds"
    directory.mkdir()
    hold = directory / "operator"
    hold.write_text("owner: fixture\n")
    monkeypatch.setenv("SWINGSET_HOLDS_DIR", str(directory))
    monkeypatch.setenv("SWINGSET_OOB_HOLD", "0")
    api = Api()
    hub = RehearsalHub(token="fixture", api=api)
    assert hub.head() == "A"
    with pytest.raises(environment.EnvironmentRefused, match="hold"):
        hub.create_commit(parent="A", message="fixture", additions={}, deletions=())
    assert api.writes == []
    hold.unlink()
    assert hub.create_commit(parent="A", message="fixture", additions={}, deletions=()) == "B"
    assert api.writes[0]["revision"] == "dryrun-fixture"


def test_actual_scheduler_child_owns_its_private_workspace_and_tmpdir(tmp_path):
    from swingset.deploy.health import cleanup_task_directories
    from swingset.deploy.scheduler import Task, child

    output = tmp_path / "child.json"
    code = (
        "import os,json,tempfile; from pathlib import Path; root=Path(os.environ['TMPDIR']); (root/'payload').write_text('temporary'); Path("
        + repr(str(output))
        + ").write_text(json.dumps({'pid':os.getpid(),'group':os.getpgrp(),'tmp':str(root)}))"
    )
    task = Task("fixture-workspace", ("-c", code), 60, 0, 10, 1)
    root = tmp_path / "tasks"
    assert child(task, lambda: False, lambda: None, workspace=root) == 0
    result = json.loads(output.read_text())
    directory = next(root.iterdir())
    owner = json.loads((directory / "owner.json").read_text())
    assert owner["pid"] == owner["process_group"] == result["pid"] == result["group"]
    assert result["tmp"] == str(directory.resolve())
    assert (directory / "payload").read_text() == "temporary"
    assert cleanup_task_directories(root, older_than=0) == [directory.name]


def test_background_rehearsal_release_does_not_advance_hub_during_long_drill(tmp_path):
    from swingset.deploy.scheduler import tasks

    release = tasks("release-maintain", tmp_path)[0]
    assert "--dry-run" in release.arguments and "--publish" not in release.arguments
    assert "--prod" in release.arguments


@pytest.mark.parametrize("failure", ["timeout", "os", "command", "invalid"])
def test_status_observation_failure_is_fresh_blind_and_can_recover(
    tmp_path, monkeypatch, network_policy, capsys, failure
):
    import subprocess
    import time

    from swingset.deploy import health, security

    errors = {
        "timeout": subprocess.TimeoutExpired(
            ["docker", "PRIVATE_ARGUMENT"], 30, output="PRIVATE_OUTPUT"
        ),
        "os": OSError("PRIVATE_DIAGNOSTIC"),
        "command": RuntimeError("PRIVATE_DIAGNOSTIC"),
        "invalid": ValueError("PRIVATE_DIAGNOSTIC"),
    }

    def unavailable(*args, **kwargs):
        raise errors[failure]

    status = tmp_path / "containers.json"
    status.write_text(
        json.dumps({"utc": 1, "complete_inventory": True, "containers": [{"role": "derive"}]})
    )
    monkeypatch.setattr(security, "_run", unavailable)
    security.export_status(tmp_path, network_policy)
    record = json.loads(status.read_text())
    assert record["utc"] > 1 and not record["complete_inventory"]
    assert record["containers"] == record["resources"] == []
    assert record["resource_exit"] == 1
    assert record["observation_error"] == type(errors[failure]).__name__
    assert status.stat().st_mode & 0o777 == 0o444
    monkeypatch.setenv("SWINGSET_CONTAINER_STATUS", str(status))
    monkeypatch.setenv("SWINGSET_HOLDS_DIR", str(tmp_path / "no-holds"))
    signals = health._platform_signals(tmp_path / "runtime" / "health", time.time())
    assert {"platform-incomplete", "platform-observer-blind"} <= signals
    assert "platform-status-stale" not in signals
    assert "PRIVATE" not in json.dumps(record) + capsys.readouterr().err

    def recovered(arguments, **kwargs):
        if arguments[:3] == ["docker", "ps", "-aq"]:
            output = "derive\n"
        elif arguments[:2] == ["docker", "inspect"]:
            output = json.dumps(
                {"deployment": network_policy["deployment"], "role": "derive", "running": True}
            )
        else:
            output = ""
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(security, "_run", recovered)
    security.export_status(tmp_path, network_policy)
    recovered_record = json.loads(status.read_text())
    assert recovered_record["complete_inventory"] and recovered_record["resource_exit"] == 0
    assert "observation_error" not in recovered_record
    assert not health._platform_signals(tmp_path / "runtime" / "health", time.time())


def test_namespace_watch_continues_after_status_timeout_but_authorization_stays_fatal(
    tmp_path, monkeypatch, network_policy
):
    import subprocess
    import sys

    from swingset.deploy import security

    class EndWatch(Exception):
        pass

    iterations = [0]
    authorized = []

    def command(arguments, **kwargs):
        if arguments[:3] == ["docker", "ps", "-aq"]:
            raise subprocess.TimeoutExpired(arguments, 30)
        return subprocess.CompletedProcess(arguments, 0, "worker\n", "")

    def sleep(seconds):
        iterations[0] += 1
        if iterations[0] == 2:
            raise EndWatch

    def authorize(*args):
        authorized.append(iterations[0])
        return {"authorized": True}

    monkeypatch.setattr(sys, "argv", ["security", "policy", str(tmp_path), "--watch"])
    monkeypatch.setattr(security, "load", lambda path: network_policy)
    monkeypatch.setattr(security, "_run", command)
    monkeypatch.setattr(
        security,
        "_inspect",
        lambda identifier: {
            "Id": identifier,
            "State": {
                "Running": True,
                "Pid": iterations[0] + 1,
                "StartedAt": str(iterations[0]),
            },
        },
    )
    monkeypatch.setattr(security, "authorize", authorize)
    monkeypatch.setattr(security.time, "monotonic", lambda: 100 + 31 * iterations[0])
    monkeypatch.setattr(security.time, "sleep", sleep)
    with pytest.raises(EndWatch):
        security.main()
    assert authorized == [0, 1]
    assert not json.loads((tmp_path / "containers.json").read_text())["complete_inventory"]

    def denied(*args):
        raise ValueError("authorization denied")

    iterations[0] = 0
    monkeypatch.setattr(security, "authorize", denied)
    with pytest.raises(ValueError, match="authorization denied"):
        security.main()


def test_status_publication_failure_still_stops_the_guard(tmp_path, monkeypatch, network_policy):
    from swingset.deploy import security

    destination = tmp_path / "not-a-directory"
    destination.write_text("preserved")
    monkeypatch.setattr(security, "_status", lambda policy: {"complete_inventory": False})
    with pytest.raises(FileExistsError):
        security.export_status(destination, network_policy)
    assert destination.read_text() == "preserved"


@pytest.mark.parametrize("retirement", ["exit0", "exit1", "removed"])
def test_namespace_watch_retires_old_actor_and_keeps_authorizing(
    tmp_path, monkeypatch, network_policy, retirement
):
    import subprocess
    import sys

    from swingset.deploy import security

    class EndWatch(Exception):
        pass

    old, new = "1" * 64, "2" * 64
    iteration = [0]
    authorized = []
    marker = tmp_path / "unrelated.json"
    marker.write_text("preserved")

    def command(arguments, **kwargs):
        if arguments[:3] == ["docker", "ps", "-q"]:
            identifiers = [old] if not iteration[0] else [new]
            if iteration[0] and retirement != "removed":
                identifiers.insert(0, old)  # The inventory raced with ordinary exit.
            return subprocess.CompletedProcess(arguments, 0, "\n".join(identifiers), "")
        assert arguments[:3] == ["docker", "ps", "-aq"]
        assert arguments[-1] == "id=" + old and retirement == "removed"
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def inspect(identifier):
        retired = identifier == old and iteration[0] > 0
        if retired and retirement == "removed":
            raise RuntimeError("container inspection failed")
        return {
            "Id": identifier,
            "State": {
                "Running": not retired,
                "Pid": 0 if retired else 111 if identifier == old else 222,
                "StartedAt": identifier,
                "ExitCode": 1 if retirement == "exit1" else 0,
            },
        }

    def authorize(identifier, policy, directory):
        if not inspect(identifier)["State"]["Running"]:
            raise ValueError("container is not running the proved image")
        authorized.append(identifier)
        value = {"container_id": identifier}
        (directory / (identifier + ".json")).write_text(json.dumps(value))
        return value

    def sleep(seconds):
        iteration[0] += 1
        if iteration[0] == 2:
            raise EndWatch

    monkeypatch.setattr(sys, "argv", ["security", "policy", str(tmp_path), "--watch"])
    monkeypatch.setattr(security, "load", lambda path: network_policy)
    monkeypatch.setattr(security, "_run", command)
    monkeypatch.setattr(security, "_inspect", inspect)
    monkeypatch.setattr(security, "authorize", authorize)
    monkeypatch.setattr(security, "export_status", lambda *args: None)
    monkeypatch.setattr(security.time, "sleep", sleep)
    with pytest.raises(EndWatch):
        security.main()
    assert authorized == [old, new]
    assert not (tmp_path / (old + ".json")).exists()
    assert (tmp_path / (new + ".json")).exists()
    assert marker.read_text() == "preserved"


@pytest.mark.parametrize("retired,terminal_pid", [(True, 0), (False, 123), (True, 123)])
def test_namespace_authorization_race_is_ignored_only_after_confirmed_exit(
    tmp_path, monkeypatch, network_policy, retired, terminal_pid
):
    import subprocess
    import sys

    from swingset.deploy import security

    identifier = "3" * 64
    inspected = [False]
    proof = tmp_path / (identifier + ".json")
    proof.write_text("old proof")

    def inspect(_):
        stopped = inspected[0] and retired
        return {
            "Id": identifier,
            "State": {
                "Running": not stopped,
                "Pid": terminal_pid if stopped else 123,
                "StartedAt": "old",
            },
        }

    def authorize(*args):
        inspected[0] = True
        raise ValueError("authorization failed")

    monkeypatch.setattr(sys, "argv", ["security", "policy", str(tmp_path)])
    monkeypatch.setattr(security, "load", lambda path: network_policy)
    monkeypatch.setattr(
        security,
        "_run",
        lambda arguments, **kwargs: subprocess.CompletedProcess(arguments, 0, identifier, ""),
    )
    monkeypatch.setattr(security, "_inspect", inspect)
    monkeypatch.setattr(security, "authorize", authorize)
    monkeypatch.setattr(security, "export_status", lambda *args: None)
    if retired and terminal_pid == 0:
        security.main()
        assert not proof.exists()
    else:
        with pytest.raises(ValueError, match="authorization failed"):
            security.main()
        assert proof.read_text() == "old proof"


def test_namespace_inventory_failure_cannot_turn_an_inspection_error_into_removal(
    tmp_path, monkeypatch, network_policy
):
    import subprocess
    import sys

    from swingset.deploy import security

    identifier = "4" * 64
    proof = tmp_path / (identifier + ".json")
    proof.write_text("preserved while state is unknown")

    def command(arguments, **kwargs):
        if arguments[:3] == ["docker", "ps", "-q"]:
            return subprocess.CompletedProcess(arguments, 0, identifier, "")
        raise subprocess.TimeoutExpired(arguments, 30)

    def inspect(_):
        raise RuntimeError("inspection failed")

    monkeypatch.setattr(sys, "argv", ["security", "policy", str(tmp_path)])
    monkeypatch.setattr(security, "load", lambda path: network_policy)
    monkeypatch.setattr(security, "_run", command)
    monkeypatch.setattr(security, "_inspect", inspect)
    with pytest.raises(subprocess.TimeoutExpired):
        security.main()
    assert proof.read_text() == "preserved while state is unknown"
