"""The import supervisor fences one utility without resetting partial data."""

import argparse
import copy
import hashlib
import json
import signal
import subprocess

import pytest

from swingset.deploy import import_job

APP, PLATFORM = "9" * 64, "8" * 64
IMAGE = "sha256:" + "a" * 64
DEPLOYMENT = "swingset-stage2-app-123456789abc"


class Backend:
    def __init__(self, policy):
        self.policy = policy
        self.calls = []
        self.now = 0
        self.started = False
        self.stopped = False
        self.loops = 0
        self.finish_after = 2
        self.exit_code = 0
        self.oom = False
        self.peer_failure = False
        self.start_timeout = False
        mounts = [
            {"Destination": name, "Source": "owned", "RW": True}
            for name in (
                "/var/lib/swingset",
                "/var/lib/swingset-spill",
                "/var/lib/swingset-runtime",
                "/run/swingset-checker",
            )
        ] + [
            {"Destination": name, "Source": "owned", "RW": False}
            for name in (
                "/run/swingset-config",
                "/run/swingset-authorization",
                "/run/swingset-holds",
                "/var/lib/swingset-source",
                "/run/secrets/owner-dsn",
                "/run/secrets/worker-dsn",
                "/run/secrets/control-dsn",
            )
        ]
        self.application = {
            "Id": APP,
            "Name": "/swingset-import-fixture",
            "Image": IMAGE,
            "Config": {
                "User": "10001:10001",
                "Entrypoint": ["python"],
                "Cmd": [
                    "-m",
                    "swingset.deploy.utility",
                    "--role",
                    "operator",
                    "--",
                    "import-sqlite37",
                ],
                "Labels": {
                    "io.swingset.deployment": DEPLOYMENT,
                    "io.swingset.role": "operator",
                    "io.swingset.guarded": "true",
                },
            },
            "HostConfig": {
                "ReadonlyRootfs": True,
                "Privileged": False,
                "CapAdd": None,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "PidMode": "",
                "Memory": 8 * 1024**3,
                "MemorySwap": 8 * 1024**3,
                "NanoCpus": 6 * 10**9,
                "PidsLimit": 256,
                "Dns": ["172.30.240.4"],
            },
            "Mounts": mounts,
            "NetworkSettings": {
                "Networks": {
                    DEPLOYMENT + "-private": {
                        "IPAMConfig": {"IPv4Address": "172.30.240.15"},
                        "IPAddress": "172.30.240.15",
                    }
                }
            },
        }

    def sleep(self, seconds):
        self.now += seconds

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        action = argv[1]
        if action == "inspect":
            identifier = argv[2]
            if identifier == APP:
                value = copy.deepcopy(self.application)
                if self.started:
                    self.loops += 1
                running = self.started and not self.stopped and self.loops < self.finish_after
                value["State"] = {
                    "Running": running,
                    "Pid": 90 if running else 0,
                    "Status": "running" if running else "exited" if self.started else "created",
                    "ExitCode": self.exit_code,
                    "OOMKilled": self.oom,
                }
            elif identifier == PLATFORM:
                value = {
                    "Id": PLATFORM,
                    "Image": "sha256:824e5ac0bc1ec5e13193141522a3550eac38830847aa4a6ae14eafb2144aab68",
                    "Config": {"Labels": {"io.swingset.deployment": DEPLOYMENT}},
                    "State": {"Running": True, "OOMKilled": False},
                }
            else:
                peer = next(x for x in self.policy["seven_original_apps"] if x["id"] == identifier)
                value = {
                    "Id": identifier,
                    "Name": peer["name"],
                    "Image": peer["image"],
                    "RestartCount": 0,
                    "State": {
                        "Running": not (self.peer_failure and self.started),
                        "OOMKilled": False,
                    },
                }
            output = json.dumps([value])
        elif action == "start":
            self.started = True
            if self.start_timeout:
                raise subprocess.TimeoutExpired(argv, timeout)
            output = APP
        elif action == "stop":
            assert argv[-1] == APP
            self.stopped = True
            output = APP
        elif action == "exec":
            output = json.dumps(
                {"receipt_exists": True, "complete": False, "loaded_tables": 4, "loaded_rows": 200}
            )
        else:
            pytest.fail("unexpected command: " + action)
        return subprocess.CompletedProcess(argv, 0, output, "")


@pytest.fixture
def job(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    argv = [
        "docker",
        "create",
        "--name",
        "swingset-import-fixture",
        "--ip",
        "172.30.240.15",
        "--dns",
        "172.30.240.4",
        IMAGE,
        "-m",
        "swingset.deploy.utility",
        "--role",
        "operator",
        "--",
        "import-sqlite37",
    ]
    policy = {
        "format": "swingset-stage2-attended-import-v1",
        "root": str(root),
        "import_deadline_seconds": 7200,
        "minimum_free_bytes": 200 * 10**9,
        "deployment": DEPLOYMENT,
        "runtime_image": IMAGE,
        "source_sha256": "f" * 64,
        "import_argv": argv,
        "seven_original_apps": [
            {
                "id": str(i) * 64,
                "name": f"/peer-{i}",
                "image": "sha256:" + "b" * 64,
                "running": True,
                "oom": False,
                "restart": 0,
                "health": None,
            }
            for i in range(1, 8)
        ],
    }
    path = root / "policy.json"
    path.write_text(json.dumps(policy))
    path.chmod(0o600)
    backend = Backend(policy)
    instance = import_job.ImportJob(
        path,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        APP,
        PLATFORM,
        runner=backend,
        clock=lambda: backend.now,
        sleep=backend.sleep,
        free=lambda path: 500 * 10**9,
    )
    return instance, backend


def test_success_starts_exactly_once_and_retains_native_audit_requirement(job):
    instance, backend = job
    result = instance.run()
    assert result["complete"] and result["process_succeeded"]
    assert sum(x[1] == "start" for x in backend.calls) == 1
    assert not any(x[1] == "stop" for x in backend.calls)
    assert "separate complete native audit" in result["limits"]
    assert (instance.root / "import-watch.started").exists()
    assert json.loads((instance.root / "import-supervisor-result.json").read_text())["complete"]


@pytest.mark.parametrize("fault", ["image", "root", "command", "network", "socket"])
def test_changed_utility_is_never_started_or_stopped(job, fault):
    instance, backend = job
    value = backend.application
    if fault == "image":
        value["Image"] = "sha256:" + "c" * 64
    elif fault == "root":
        value["Config"]["User"] = "0:0"
    elif fault == "command":
        value["Config"]["Cmd"] = ["-c", "arbitrary"]
    elif fault == "network":
        value["NetworkSettings"]["Networks"]["outside"] = {}
    else:
        value["Mounts"][0]["Source"] = "/var/run/docker.sock"
    assert not instance.run()["complete"]
    assert not any(x[1] in {"start", "stop"} for x in backend.calls)


@pytest.mark.parametrize("reason", ["peer", "disk", "deadline", "lost_start_ack"])
def test_failed_live_run_stops_only_its_bound_utility_without_retry(job, reason):
    instance, backend = job
    backend.finish_after = 10000
    if reason == "peer":
        backend.peer_failure = True
    elif reason == "disk":
        instance.free = lambda path: (199 if backend.started else 500) * 10**9
    elif reason == "deadline":
        backend.sleep = lambda seconds: None
        instance.sleep = lambda seconds: backend.sleep(seconds) or setattr(backend, "now", 7200)
    else:
        backend.start_timeout = True
    result = instance.run()
    assert not result["complete"] and backend.stopped
    assert sum(x[1] == "start" for x in backend.calls) == 1
    assert sum(x[1] == "stop" for x in backend.calls) == 1
    assert all(x[-1] == APP for x in backend.calls if x[1] == "stop")
    assert result.get("no_automatic_retry") or result["stop_reason"] == "7200-second deadline"


@pytest.mark.parametrize("exit_code,oom", [(1, False), (137, True)])
def test_failed_native_exit_never_becomes_success(job, exit_code, oom):
    instance, backend = job
    backend.exit_code, backend.oom = exit_code, oom
    result = instance.run()
    assert not result["complete"] and result["no_automatic_retry"]
    assert sum(x[1] == "start" for x in backend.calls) == 1
    assert result["terminal_state"] == "confirmed"
    assert not result["state"]["Running"] and result["state"]["Pid"] == 0
    assert result["state"]["ExitCode"] == exit_code
    assert not any(x[1] == "stop" for x in backend.calls)


def test_existing_reservation_preserves_previous_receipt_and_never_repeats(job):
    instance, backend = job
    (instance.root / "import-watch.started").write_text("earlier attempt")
    prior = instance.root / "import-supervisor-result.json"
    prior.write_text("sealed")
    assert not instance.run()["complete"]
    assert prior.read_text() == "sealed"
    assert not any(x[1] in {"start", "stop"} for x in backend.calls)


def test_a_native_progress_stall_is_visible_before_the_deadline(job):
    instance, backend = job
    backend.finish_after = 10000
    instance.sleep = lambda seconds: setattr(backend, "now", backend.now + 3600)
    result = instance.run()
    assert not result["complete"]
    assert result["no_native_progress_for_60_minutes"]
    assert result["stop_reason"] == "7200-second deadline"


def test_preflight_table_progress_prevents_false_native_stall(job):
    instance, backend = job
    backend.finish_after = 10000
    original = instance.runner

    def advancing(argv, timeout):
        response = original(argv, timeout)
        if argv[1] == "exec":
            value = json.loads(response.stdout)
            value["loaded_tables"] = 0
            value["preflight"] = {"phase": "values", "table": f"table_{backend.now}"}
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        return response

    instance.runner = advancing
    instance.sleep = lambda seconds: setattr(backend, "now", backend.now + 1800)
    result = instance.run()
    assert result["import_progress"]["loaded_tables"] == 0
    assert not result["no_native_progress_for_60_minutes"]
    assert result["stop_reason"] == "7200-second deadline"


@pytest.mark.parametrize("deadline", [7201, 21601, True, float("inf")])
def test_unreviewed_deadline_is_rejected_before_docker(job, deadline):
    instance, backend = job
    policy = dict(instance.policy, import_deadline_seconds=deadline)
    path = instance.root / "other-plan.json"
    path.write_text(json.dumps(policy))
    path.chmod(0o600)
    before = list(backend.calls)
    with pytest.raises(ValueError):
        import_job.ImportJob(
            path, hashlib.sha256(path.read_bytes()).hexdigest(), APP, PLATFORM, runner=backend
        )
    assert backend.calls == before


def test_reviewed_full_size_deadline_is_bound_and_still_stops(job):
    instance, backend = job
    policy = dict(instance.policy, import_deadline_seconds=21600)
    path = instance.root / "larger-plan.json"
    path.write_text(json.dumps(policy))
    path.chmod(0o600)
    bounded = import_job.ImportJob(
        path,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        APP,
        PLATFORM,
        runner=backend,
        clock=lambda: backend.now,
        sleep=lambda seconds: setattr(backend, "now", backend.now + 7200),
        free=lambda path: 300 * 10**9,
    )
    backend.finish_after = 10000
    result = bounded.run()
    assert result["elapsed_seconds"] == 21600
    assert result["stop_reason"] == "21600-second deadline" and backend.stopped
    assert len([call for call in backend.calls if call[1] == "start"]) == 1


def test_receipt_failure_after_start_is_reported_unknown_and_not_retried(job, monkeypatch, capsys):
    instance, backend = job
    backend.finish_after = 10000
    original = instance.live

    def live():
        if backend.started:
            raise OSError("private diagnostic must not appear")
        original()

    monkeypatch.setattr(instance, "live", live)
    monkeypatch.setattr(import_job, "ImportJob", lambda *args: instance)
    assert (
        import_job.execute(
            argparse.Namespace(plan=None, plan_sha256=None, container=None, platform=None)
        )
        == 1
    )
    output = capsys.readouterr().out
    assert json.loads(output)["mutation_outcome"] == "unknown"
    assert "private diagnostic" not in output
    assert backend.stopped and sum(x[1] == "start" for x in backend.calls) == 1


def test_policy_bytes_hash_and_private_mode_are_checked_together(job):
    instance, backend = job
    path = instance.root / "policy.json"
    with pytest.raises(ValueError):
        import_job.private_json(path, "0" * 64)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        import_job.private_json(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_termination_stops_owned_utility_and_restores_signal_handler(job, monkeypatch):
    instance, backend = job
    backend.finish_after = 10000
    original = instance.runner
    previous = signal.getsignal(signal.SIGTERM)

    def interrupted(argv, timeout):
        response = original(argv, timeout)
        if argv[1] == "start":
            signal.raise_signal(signal.SIGTERM)
        return response

    instance.runner = interrupted
    monkeypatch.setattr(import_job, "ImportJob", lambda *args: instance)
    assert (
        import_job.execute(
            argparse.Namespace(plan=None, plan_sha256=None, container=None, platform=None)
        )
        == 1
    )
    assert backend.stopped
    assert [x[-1] for x in backend.calls if x[1] == "stop"] == [APP]
    assert signal.getsignal(signal.SIGTERM) == previous


@pytest.mark.parametrize("stop_exit", [0, 1])
def test_unconfirmed_stop_is_escalated_without_repeating_it(job, stop_exit):
    instance, backend = job
    backend.finish_after = 10000
    backend.peer_failure = True
    original = instance.runner

    def unconfirmed(argv, timeout):
        if argv[1] == "stop":
            backend.calls.append(argv)
            return subprocess.CompletedProcess(argv, stop_exit, "acknowledged", "")
        return original(argv, timeout)

    instance.runner = unconfirmed
    result = instance.run()
    assert not result["complete"] and result["requires_attended_escalation"]
    assert not backend.stopped
    assert len([call for call in backend.calls if call[1] == "stop"]) == 1


def test_progress_timeout_retains_safe_action_and_current_terminal_state(job):
    instance, backend = job
    backend.finish_after = 10000
    original = instance.runner

    def timeout(argv, bound):
        if argv[1] == "exec":
            raise subprocess.TimeoutExpired(
                [*argv, "PRIVATE_ARGUMENT"], bound, output="PRIVATE_OUTPUT"
            )
        return original(argv, bound)

    instance.runner = timeout
    result = instance.run()
    assert not result["complete"] and result["no_automatic_retry"]
    assert result["docker_command_failure"] == {
        "action": "exec",
        "target": APP,
        "timeout_seconds": 20,
        "error_type": "TimeoutExpired",
    }
    assert not result["state"]["Running"] and result["state"]["Pid"] == 0
    assert result["stop_confirmed"] and result["state"] == result["stopped_state"]
    assert sum(argv[1] == "start" for argv in backend.calls) == 1
    assert sum(argv[1] == "stop" for argv in backend.calls) == 1
    assert "PRIVATE" not in json.dumps(result)


def test_stop_inspection_failure_keeps_last_observation_and_terminal_unknown(job):
    instance, backend = job
    backend.finish_after = 10000
    original = instance.runner
    progress_failed = False

    def timeout(argv, bound):
        nonlocal progress_failed
        if argv[1] == "exec":
            progress_failed = True
            raise subprocess.TimeoutExpired([*argv, "PRIVATE_PROGRESS"], bound)
        if progress_failed and argv[1] == "inspect" and argv[2] == APP:
            raise subprocess.TimeoutExpired([*argv, "PRIVATE_STOP"], bound)
        return original(argv, bound)

    instance.runner = timeout
    result = instance.run()
    assert not result["complete"] and result["requires_attended_escalation"]
    assert result["terminal_state"] == "unknown" and "state" not in result
    assert result["last_observed_state"]["Running"]
    assert result["last_observed_at"]
    assert result["stop_error_type"] == "TimeoutExpired"
    assert result["docker_command_failure"] == {
        "action": "exec",
        "target": APP,
        "timeout_seconds": 20,
        "error_type": "TimeoutExpired",
    }
    assert sum(argv[1] == "start" for argv in backend.calls) == 1
    assert not any(argv[1] == "stop" for argv in backend.calls)
    assert "PRIVATE" not in json.dumps(result)
