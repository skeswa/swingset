"""The graph supervisor owns one verifier and never the retained input base."""

import copy
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from test_deploy_import_job import APP, DEPLOYMENT, IMAGE, PLATFORM, Backend
from test_deploy_import_job import job as job

from swingset.deploy import verification_job

SUPPORT = [letter * 64 for letter in "abcde"]
NATIVE = "f" * 64
DATABASE = "sst_copy_" + "1" * 24 + "_0"
DATABASE_ID = "11111111-1111-4111-8111-111111111111"
COMMAND = [
    "-m",
    "swingset.deploy.utility",
    "--role",
    "verifier",
    "--",
    "verify-derivations",
    "--state",
    "/var/lib/swingset",
    "--checkpoint",
    "/var/lib/swingset",
    "--processes",
    "2",
    "--config",
    "/app/config",
    "--overrides",
    "/app/overrides",
    "--receipt",
    "/var/lib/swingset-runtime/receipt.json",
]


class VerifierBackend(Backend):
    def __init__(self, policy):
        super().__init__(policy)
        self.finish_after = 10
        self.bad_identity = False
        self.application["RestartCount"] = 0
        self.application["Config"].update(
            Cmd=list(COMMAND), Env=["PATH=/bin", "SWINGSET_ENV=dryrun"]
        )
        self.application["Config"]["Labels"]["io.swingset.role"] = "verifier"
        self.application["Mounts"] = [
            {
                "Type": m["type"],
                "Source": m["source"]
                if m["type"] == "bind"
                else "/var/lib/docker/volumes/" + m["source"] + "/_data",
                "Name": m["source"] if m["type"] == "volume" else None,
                "Destination": m["target"],
                "RW": not m["readonly"],
            }
            for m in policy["mounts"]
        ]
        self.application["HostConfig"]["Mounts"] = [
            {
                "Type": m["type"],
                "Source": m["source"],
                "Target": m["target"],
                "ReadOnly": m["readonly"],
                "VolumeOptions": {"Subpath": m["subpath"]} if "subpath" in m else {},
            }
            for m in policy["mounts"]
        ]
        net = self.application["NetworkSettings"]["Networks"][DEPLOYMENT + "-private"]
        net["IPAMConfig"]["IPv4Address"] = net["IPAddress"] = "172.30.240.18"

    def __call__(self, argv, timeout):
        action = argv[1]
        if action == "image":
            self.calls.append(argv)
            return subprocess.CompletedProcess(
                argv, 0, json.dumps([{"Config": {"Env": ["PATH=/bin"], "Labels": {}}}]), ""
            )
        if action == "logs":
            self.calls.append(argv)
            value = {
                "environment": "dev",
                "database": DATABASE,
                "database_id": "wrong" if self.bad_identity else DATABASE_ID,
                "unexpected_private_field": "must-not-be-retained",
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        if action == "exec":
            self.calls.append(argv)
            if argv[2] == PLATFORM:
                assert argv[3:8] == ["/usr/bin/stat", "-L", "--format=%i", "--", "/proc/90/ns/net"]
                return subprocess.CompletedProcess(argv, 0, "123\n", "")
            compile(argv[-1], "<verifier-progress>", "exec")
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"transactions": self.now + 1, "tuples": self.now + 2, "databases": 4}),
                "",
            )
        if action == "inspect" and argv[2] in SUPPORT:
            self.calls.append(argv)
            peer = next(x for x in self.policy["support5"] if x["id"] == argv[2])
            value = {
                "Id": peer["id"],
                "Name": peer["name"],
                "Image": peer["image"],
                "RestartCount": 0,
                "State": {"Running": True, "OOMKilled": False, "Pid": 100},
                "HostConfig": {
                    "Memory": 8589934592,
                    "MemorySwap": 8589934592,
                    "NanoCpus": 6000000000,
                    "PidsLimit": 256,
                },
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps([value]), "")
        if action == "inspect" and argv[2] == NATIVE:
            self.calls.append(argv)
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    [
                        {
                            "Id": NATIVE,
                            "State": {
                                "Running": False,
                                "Pid": 0,
                                "ExitCode": 0,
                                "OOMKilled": False,
                                "FinishedAt": "native-finished",
                            },
                        }
                    ]
                ),
                "",
            )
        response = super().__call__(argv, timeout)
        if action != "inspect":
            return response
        value = json.loads(response.stdout)[0]
        value["State"]["Pid"] = (
            90
            if argv[2] == APP and value["State"]["Running"]
            else 0
            if argv[2] == APP
            else 80
            if argv[2] == PLATFORM
            else 100
        )
        if argv[2] == APP:
            value["State"]["StartedAt"] = "verifier-started"
        if argv[2] == PLATFORM:
            value.update(Name="/namespace", RestartCount=0)
            value["Config"]["Labels"]["io.swingset.role"] = "namespace-operator"
            value["State"]["StartedAt"] = "platform-started"
            value["Config"].update(
                User="0:0",
                Entrypoint=["python"],
                Cmd=[
                    "/operator/security.py",
                    "/operator/network.json",
                    "/authorization",
                    "--watch",
                ],
            )
            value["HostConfig"] = {
                "ReadonlyRootfs": True,
                "Privileged": False,
                "NetworkMode": "none",
                "PidMode": "host",
                "CapDrop": ["ALL"],
                "CapAdd": ["SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE"],
                "SecurityOpt": ["no-new-privileges", "label=disable"],
                "Memory": 268435456,
                "MemorySwap": 268435456,
                "NanoCpus": 1000000000,
                "PidsLimit": 128,
            }
            app = "/var/tmp/" + DEPLOYMENT
            value["Mounts"] = [
                {"Destination": dst, "Type": "bind", "Source": src, "RW": rw}
                for dst, src, rw in [
                    ("/var/run/docker.sock", "/var/run/docker.sock", False),
                    ("/operator/security.py", app + "/security.py", False),
                    ("/operator/network.json", app + "/config/network.json", False),
                    ("/authorization", app + "/authorization", True),
                ]
            ]
        return subprocess.CompletedProcess(argv, 0, json.dumps([value]), "")


@pytest.fixture
def graph(job, monkeypatch, request):
    original, _ = job
    profile = getattr(request, "param", False)
    job_class = verification_job.InputProfileJob if profile else verification_job.VerificationJob
    command = verification_job.InputProfileJob.utility_command() if profile else COMMAND
    policy = copy.deepcopy(original.policy)
    private = DEPLOYMENT + "-000000000001-private-state"
    app = "/var/tmp/" + DEPLOYMENT
    mounts = [
        {
            "type": "volume",
            "source": private
            if suffix == "private-state"
            else DEPLOYMENT + "-000000000001-" + suffix,
            "target": target,
            "readonly": False,
        }
        for target, suffix in [
            ("/var/lib/swingset", "private-state"),
            ("/var/lib/swingset-spill", "verifier-spill"),
            ("/var/lib/swingset-runtime", "verifier-runtime"),
            ("/run/swingset-checker", "verifier-checker"),
        ]
    ]
    mounts += [
        {"type": "bind", "source": app + "/" + suffix, "target": target, "readonly": True}
        for target, suffix in [
            ("/run/swingset-config", "config"),
            ("/run/swingset-authorization", "authorization"),
            ("/run/swingset-holds", "holds"),
        ]
    ]
    mounts += [
        {
            "type": "volume",
            "source": DEPLOYMENT + "-state",
            "target": "/var/lib/swingset-source",
            "readonly": True,
            "subpath": "blobs",
        }
    ]
    mounts += [
        {
            "type": "bind",
            "source": "/var/lib/docker/volumes/" + private + "/_data/private-secrets/" + name,
            "target": "/run/secrets/" + name,
            "readonly": True,
        }
        for name in ("worker-dsn", "control-dsn", "scratch-admin-dsn")
    ]
    if profile:
        mounts[0]["readonly"] = True
    argv = [
        "docker",
        "create",
        "--name",
        "swingset-import-fixture",
        "--ip",
        "172.30.240.18",
        "--dns",
        "172.30.240.4",
        "--env",
        "SWINGSET_ENV=dryrun",
    ]
    for name, value in (
        ("io.swingset.deployment", DEPLOYMENT),
        ("io.swingset.role", "verifier"),
        ("io.swingset.guarded", "true"),
    ):
        argv += ["--label", name + "=" + value]
    argv += [IMAGE, *command]
    support = [
        {
            "id": cid,
            "name": "/support-" + cid[0],
            "image": IMAGE,
            "running": True,
            "oom": False,
            "restart": 0,
            "health": None,
        }
        for cid in SUPPORT
    ]
    network = json.dumps({"deployment": DEPLOYMENT, "image_id": IMAGE}, sort_keys=True).encode()
    security = b"owned security code"
    hold = {"owner": "fixture", "reason": "graph", "created_at": "earlier"}
    policy.update(
        format=job_class.FORMAT,
        verification_deadline_seconds=3600 if profile else 86400,
        verifier_argv=argv,
        support5=support,
        database=DATABASE,
        database_id=DATABASE_ID,
        expected_unit_count=2,
        expected_unit_set_sha256="d" * 64,
        native_container=NATIVE,
        native_finished_at="native-finished",
        postgres_ids={"shared": "3" * 64, "private": SUPPORT[-1]},
        current_platform={
            "container_id": PLATFORM,
            "name": "namespace",
            "pid": 80,
            "start_ticks": "80",
            "started_at": "platform-started",
            "boot_id": "boot",
        },
        mounts=mounts,
        hold_identity=hold,
        shared_database_bytes=1000000000,
        private_base_database_bytes=1000000000,
        security_sha256=hashlib.sha256(security).hexdigest(),
        network_policy_sha256=hashlib.sha256(network).hexdigest(),
    )
    if profile:
        policy.update(owned_database_limit=1, diagnostic_only=True)
    path = original.root / "graph-policy.json"
    path.write_text(json.dumps(policy))
    path.chmod(0o600)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    review = original.root / "review.json"
    review.write_text(
        json.dumps(
            {
                "verdict": "ACCEPT_PROFILE_AND_OWNED_CLEANUP"
                if profile
                else "ACCEPT_GRAPH_AND_OWNED_CLEANUP",
                "plan_sha256": checksum,
                "container_id": APP,
                "graph_owned_cleanup_authorized": True,
                "owned_database_limit": 1,
                "base_cleanup_authorized": False,
            }
        )
    )
    review.chmod(0o600)
    proof = {
        "container_id": APP,
        "role": "verifier",
        "policy_sha256": hashlib.sha256(
            json.dumps(json.loads(network), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "boot_id": "boot",
        "process_start": "90",
        "network_inode": 123,
    }
    files = {
        app + "/security.py": security,
        app + "/config/network.json": network,
        app + "/holds/stage2-import.json": json.dumps(
            {**hold, "review_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
        ).encode(),
        app + "/authorization/" + APP + ".json": json.dumps(proof).encode(),
    }
    monkeypatch.setattr(verification_job, "root_bytes", lambda path: files[str(path)])
    monkeypatch.setattr(verification_job, "birth", lambda pid: str(pid))
    monkeypatch.setattr(verification_job, "boot_id", lambda: "boot")
    monkeypatch.setattr(
        verification_job,
        "memory",
        lambda pid: {"cgroup": "/fixture/" + str(pid), "peak": 12345, "events": {"oom_kill": 0}},
    )
    backend = VerifierBackend(policy)
    backend.application["Config"]["Cmd"] = list(command)
    instance = job_class(
        path,
        checksum,
        APP,
        PLATFORM,
        review=review,
        review_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
        runner=backend,
        clock=lambda: backend.now,
        sleep=backend.sleep,
        free=lambda path: 500000000000,
    )
    return instance, backend, files


def test_clean_private_run_starts_once_and_keeps_base_cleanup_separate(graph):
    instance, backend, _ = graph
    result = instance.run()
    assert result["complete"] and result["verified_startup_identity"] == {
        "environment": "dev",
        "database": DATABASE,
        "database_id": DATABASE_ID,
    }
    assert result["protected12_unchanged"]
    assert result["verifier_incarnation"] == {
        "container_id": APP,
        "pid": 90,
        "start_ticks": "90",
        "started_at": "verifier-started",
        "boot_id": "boot",
        "network_inode": 123,
        "cgroup": "/fixture/90",
    }
    assert result["initial_capacity"]["required_bytes"] == 208000000000
    assert result["verification_progress"]["transactions"] >= 1
    assert "separate receipt" in result["limits"] and "no base cleanup" in result["limits"]
    assert "must-not-be-retained" not in json.dumps(result)
    assert sum(x[1] == "start" for x in backend.calls) == 1
    assert not any(x[1] == "stop" for x in backend.calls)
    assert json.loads((instance.root / "verification-supervisor-result.json").read_text())[
        "complete"
    ]


@pytest.mark.parametrize("graph", [True], indirect=True)
@pytest.mark.parametrize("fault", [None, "writable_base", "graph_command", "deadline"])
def test_input_profile_supervisor_owns_only_its_bounded_readonly_diagnostic(graph, fault):
    instance, backend, _ = graph
    if fault == "writable_base":
        next(
            mount
            for mount in backend.application["Mounts"]
            if mount["Destination"] == "/var/lib/swingset"
        )["RW"] = True
    elif fault == "graph_command":
        backend.application["Config"]["Cmd"] = list(COMMAND)
    elif fault == "deadline":
        backend.finish_after = 100000
        instance.sleep = lambda seconds: setattr(backend, "now", 3600)
    result = instance.run()
    if fault in {"writable_base", "graph_command"}:
        assert not result["complete"]
        assert not any(x[1] in {"start", "stop"} for x in backend.calls)
    else:
        assert sum(x[1] == "start" for x in backend.calls) == 1
        assert result["complete"] == (fault is None)
        assert "not a graph proof" in result["limits"]
        assert [x[-1] for x in backend.calls if x[1] == "stop"] == (
            [APP] if fault == "deadline" else []
        )
        assert (instance.root / "profile-supervisor-result.json").is_file()


@pytest.mark.parametrize("graph", [True], indirect=True)
@pytest.mark.parametrize("fault", ["graph_review", "two_copies", "boolean_limit", "graph_policy"])
def test_input_profile_supervisor_refuses_wider_review_or_policy(graph, fault):
    instance, backend, _ = graph
    review = {
        "verdict": "ACCEPT_PROFILE_AND_OWNED_CLEANUP",
        "plan_sha256": instance.result["policy_sha256"],
        "container_id": APP,
        "owned_database_limit": 1,
        "base_cleanup_authorized": False,
    }
    if fault == "graph_review":
        review["verdict"] = "ACCEPT_GRAPH_AND_OWNED_CLEANUP"
    elif fault == "two_copies":
        review["owned_database_limit"] = 2
    elif fault == "boolean_limit":
        review["owned_database_limit"] = True
    else:
        instance.policy["diagnostic_only"] = False
    with pytest.raises(ValueError):
        instance.review(review)
    assert not backend.calls


@pytest.mark.parametrize(
    "fault", ["shared_secret", "partial_graph", "ambient_secret", "capacity", "expired_hold"]
)
def test_unsafe_verifier_is_refused_before_start(graph, fault):
    instance, backend, files = graph
    if fault == "shared_secret":
        secret = next(
            x
            for x in backend.application["HostConfig"]["Mounts"]
            if x["Target"] == "/run/secrets/worker-dsn"
        )
        secret["Source"] = "/shared/worker-dsn"
    elif fault == "partial_graph":
        backend.application["Config"]["Cmd"] += ["--scopes", "parse"]
    elif fault == "ambient_secret":
        backend.application["Config"]["Env"].append("HF_TOKEN=fixture")
    elif fault == "capacity":
        instance.free = lambda path: 201000000000
    else:
        path = str(instance.app / "holds/stage2-import.json")
        hold = json.loads(files[path])
        hold["review_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        files[path] = json.dumps(hold).encode()
    assert not instance.run()["complete"]
    assert not any(x[1] in {"start", "stop"} for x in backend.calls)


@pytest.mark.parametrize("fault", ["peer", "wrong_identity", "deadline", "lost_start_ack"])
def test_live_failure_stops_only_the_owned_verifier_without_retry(graph, fault):
    instance, backend, _ = graph
    backend.finish_after = 100000
    if fault == "peer":
        backend.peer_failure = True
    elif fault == "wrong_identity":
        backend.bad_identity = True
    elif fault == "deadline":
        instance.sleep = lambda seconds: setattr(backend, "now", 86400)
    else:
        backend.start_timeout = True
    result = instance.run()
    assert not result["complete"] and backend.stopped
    assert sum(x[1] == "start" for x in backend.calls) == 1
    assert [x[-1] for x in backend.calls if x[1] == "stop"] == [APP]


def test_existing_reservation_is_never_replayed(graph):
    instance, backend, _ = graph
    (instance.root / "verification-watch.started").write_text("earlier")
    assert not instance.run()["complete"]
    assert not any(x[1] in {"start", "stop"} for x in backend.calls)


def test_changed_running_incarnation_requires_attended_escalation(graph, monkeypatch):
    instance, backend, _ = graph
    backend.finish_after = 100000
    monkeypatch.setattr(
        verification_job,
        "birth",
        lambda pid: "replacement" if pid == 90 and backend.loops >= 4 else str(pid),
    )
    result = instance.run()
    assert not result["complete"] and result["requires_attended_escalation"]
    assert not backend.stopped
    assert sum(x[1] == "start" for x in backend.calls) == 1


def test_stale_namespace_authorization_stops_only_owned_job(graph):
    instance, backend, files = graph
    key = str(instance.app / "authorization" / (APP + ".json"))
    proof = json.loads(files[key])
    proof["policy_sha256"] = "0" * 64
    files[key] = json.dumps(proof).encode()
    assert not instance.run()["complete"]
    assert [x[-1] for x in backend.calls if x[1] == "stop"] == [APP]


def test_positive_but_wrong_network_inode_is_refused(graph):
    instance, backend, files = graph
    key = str(instance.app / "authorization" / (APP + ".json"))
    proof = json.loads(files[key])
    proof["network_inode"] = 456
    files[key] = json.dumps(proof).encode()
    assert not instance.run()["complete"]
    assert [x[-1] for x in backend.calls if x[1] == "stop"] == [APP]


@pytest.mark.parametrize("slow", ["logs", "inode"])
def test_startup_subprocess_crossing_120_seconds_is_refused(graph, slow):
    instance, backend, _ = graph
    backend.finish_after = 100000
    original = instance.runner

    def delayed(argv, timeout):
        response = original(argv, timeout)
        if argv[1] == "logs" if slow == "logs" else argv[1:3] == ["exec", PLATFORM]:
            backend.now = 121
        return response

    instance.runner = delayed
    result = instance.run()
    assert not result["complete"] and backend.stopped
    assert "verified_startup_identity" not in result


@pytest.mark.parametrize("late", ["proof", "identity"])
def test_valid_startup_arriving_after_120_seconds_is_refused(graph, monkeypatch, late):
    instance, backend, _ = graph
    backend.finish_after = 100000
    if late == "proof":
        original_bytes = verification_job.root_bytes

        def delayed(path):
            if path.name == APP + ".json" and backend.now < 140:
                raise FileNotFoundError
            return original_bytes(path)

        monkeypatch.setattr(verification_job, "root_bytes", delayed)
    else:
        original = instance.runner

        def delayed_logs(argv, timeout):
            if argv[1] == "logs" and backend.now < 140:
                backend.calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")
            return original(argv, timeout)

        instance.runner = delayed_logs
    result = instance.run()
    assert not result["complete"] and backend.stopped
    assert "verified_startup_identity" not in result
    assert [x[-1] for x in backend.calls if x[1] == "stop"] == [APP]


def test_duplicate_statistics_labels_reproduce_wait_failure_and_named_fields_work(tmp_path):
    from swingset.state.db import open_database

    with open_database(tmp_path / "state") as db:
        with pytest.raises(ValueError, match="duplicate column labels"):
            db.connection.execute(
                "SELECT coalesce(1,0),coalesce(1,0),count(*) FROM pg_catalog.pg_database"
            ).fetchone()
        row = db.connection.execute(verification_job.STATISTICS_SQL).fetchone()
        assert row.keys() == ["transactions", "tuples", "databases"]
        assert all(type(value) is int and value >= 0 for value in row)


@pytest.mark.parametrize("oom", [0, 1])
def test_kernel_memory_events_are_checked_before_accepting_a_sample(monkeypatch, oom):
    def read(path):
        if path.name == "cgroup":
            return "0::/fixture"
        if path.name == "memory.events":
            return f"oom_kill {oom}\noom_group_kill 0\n"
        if path.name == "memory.peak":
            return "12345"
        pytest.fail("unexpected host read")

    with monkeypatch.context() as patch:
        patch.setattr(verification_job.Path, "read_text", read)
        if oom:
            with pytest.raises(ValueError):
                verification_job.memory(9)
        else:
            assert verification_job.memory(9)["peak"] == 12345
