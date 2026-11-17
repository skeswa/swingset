"""Complete-run evidence and cleanup must reject missing work or foreign owners."""

import copy
import fcntl
import hashlib
import json
import signal
import subprocess
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from swingset.dev import core_runner as core
from swingset.dev.core_partition import PROLOGUE_NODE, assigned_nodes, phase_nodes


def test_public_runner_holds_one_host_budget_until_the_job_finishes(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    failures = []
    home = tmp_path / "private-dev"

    def job(root=None):
        entered.set()
        assert release.wait(3), "test did not release the owned executor"
        return 0

    def run():
        try:
            assert core.run(tmp_path) == 0
        except BaseException as error:
            failures.append(error)

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SWINGSET_TEST_SERVICE_DSN", raising=False)
    monkeypatch.setattr(core.services, "home", lambda: home)
    monkeypatch.setattr(core, "_run_locked", job)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        with (home / "core-executor.lock").open("a+") as independent:
            with pytest.raises(BlockingIOError):
                fcntl.flock(independent.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release.set()
            thread.join(3)
            assert not thread.is_alive()
            fcntl.flock(independent.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(independent.fileno(), fcntl.LOCK_UN)
    finally:
        release.set()
        thread.join(3)
    assert failures == []


def _digest(nodes):
    return hashlib.sha256("\n".join(nodes).encode()).hexdigest()


def _clients(count=3, weights=None, checksum=None):
    selected = [f"tests/{name}.py::test_{case}" for name in "abcdef" for case in range(2)] + [
        PROLOGUE_NODE
    ]
    result = []
    for index in range(count):
        assigned = assigned_nodes(phase_nodes(selected, "parallel"), index, count, weights)
        common = {
            "format": 1,
            "phase": "parallel",
            "index": index,
            "count": count,
            "selected": selected.copy(),
            "assigned": assigned,
            "selected_count": len(selected),
            "assigned_count": len(assigned),
            "selected_sha256": _digest(selected),
            "assigned_sha256": _digest(assigned),
            "partition_policy": "measured-lpt-v1" if weights is not None else "sha256-files-v1",
            "weights_sha256": checksum,
        }
        result.append(
            {
                "index": index,
                "container_id": f"{index + 1:064x}",
                "exit_code": 0,
                "reports": {
                    "partitions": {
                        f"gw{n}.json": {**copy.deepcopy(common), "worker": f"gw{n}"}
                        for n in range(4)
                    },
                    "core-results.json": {
                        "exit_code": 0,
                        "counts": {"passed": len(assigned)},
                        "executed": assigned.copy(),
                        "node_phases": {
                            node: {"setup": 0.2, "call": 0.7, "teardown": 0.1} for node in assigned
                        },
                    },
                    "core-client-summary.json": {
                        "pytest_exit_code": 0,
                        "uid": 10001,
                        "pid_namespace": f"pid:[{index + 100}]",
                        "cleanup": {"complete": True},
                        "failure_artifact_export": {"complete": True},
                    },
                },
            }
        )
    return result


def _prologue(clients):
    base = copy.deepcopy(next(iter(clients[0]["reports"]["partitions"].values())))
    base.update(
        index=0,
        count=1,
        phase="prologue",
        worker="controller",
        partition_policy="one-case-prologue-v1",
        assigned=[PROLOGUE_NODE],
        assigned_count=1,
        assigned_sha256=_digest([PROLOGUE_NODE]),
    )
    return {
        "index": "prologue",
        "container_id": "f" * 64,
        "exit_code": 0,
        "reports": {
            "partitions": {"controller.json": base},
            "core-results.json": {
                "exit_code": 0,
                "counts": {"passed": 1},
                "executed": [PROLOGUE_NODE],
                "node_phases": {PROLOGUE_NODE: {"setup": 0.1, "call": 0.2, "teardown": 0.1}},
            },
            "core-client-summary.json": {
                "pytest_exit_code": 0,
                "uid": 10001,
                "pid_namespace": "pid:[100]",
                "pytest_command": ["python", "-m", "pytest", "--swingset-core-phase=prologue"],
                "cleanup": {"complete": True},
                "failure_artifact_export": {"complete": True},
            },
        },
    }


@pytest.mark.parametrize("count,weights", [(2, None), (3, {"tests/a.py": 10.0, "tests/f.py": 8.0})])
def test_complete_reports_prove_every_case_passed_in_exactly_one_partition(count, weights):
    checksum = "a" * 64 if weights is not None else None
    clients = _clients(count, weights, checksum)
    report = core.validate_reports(
        clients, prologue=_prologue(clients), count=count, weights=weights, weights_sha256=checksum
    )
    assert report["errors"] == []
    assert report["selected_count"] == 13
    assert report["prologue_count"] == 1
    assert sum(report["assigned_counts"]) == 12


@pytest.mark.parametrize(
    "damage",
    ["missing", "duplicate_case", "extra_case", "failed", "cleanup", "workers", "container_reuse"],
)
def test_prologue_cannot_omit_work_hide_failure_or_reuse_a_container(damage):
    clients = _clients(4)
    serial = _prologue(clients)
    part = serial["reports"]["partitions"]["controller.json"]
    if damage == "missing":
        serial = None
    elif damage == "duplicate_case":
        part["selected"].append(PROLOGUE_NODE)
    elif damage == "extra_case":
        part["assigned"].append("tests/a.py::test_0")
    elif damage == "failed":
        serial["reports"]["core-results.json"]["counts"] = {"failed": 1}
    elif damage == "cleanup":
        serial["reports"]["core-client-summary.json"]["cleanup"]["complete"] = False
    elif damage == "workers":
        serial["reports"]["core-client-summary.json"]["pytest_command"] += ["-n", "4"]
    elif damage == "container_reuse":
        serial["container_id"] = clients[0]["container_id"]
    assert core.validate_reports(clients, prologue=serial, count=4)["errors"]


def test_a_dead_prologue_namespace_inode_can_be_reused_by_a_later_client():
    clients = _clients(4)
    serial = _prologue(clients)
    assert (
        serial["reports"]["core-client-summary.json"]["pid_namespace"]
        == clients[0]["reports"]["core-client-summary.json"]["pid_namespace"]
    )
    assert core.validate_reports(clients, prologue=serial, count=4)["errors"] == []


def test_every_phase_contributes_to_the_complete_allpass_cost_cache():
    clients = _clients(4)
    weights = core.measured_weights([_prologue(clients), *clients])
    assert weights[PROLOGUE_NODE.split("::")[0]] == pytest.approx(0.4)
    assert sum(weights.values()) == pytest.approx(12.4)


def test_client_ownership_is_recorded_before_a_failed_process_launch(tmp_path, monkeypatch):
    job = "a" * 24
    clients = []

    def docker(*arguments, **kwargs):
        owner = json.loads((tmp_path / "client-prologue-owner.json").read_text())
        assert owner["job"] == job and owner["index"] == "prologue"
        if arguments[:2] == ("volume", "create"):
            assert clients[0]["volume"] == owner["volume"]
            return subprocess.CompletedProcess(arguments, 0, "created", "")
        assert arguments[:2] == ("volume", "inspect")
        return subprocess.CompletedProcess(
            arguments, 0, json.dumps([{"CreatedAt": "original"}]), ""
        )

    monkeypatch.setattr(core, "_docker", docker)
    monkeypatch.setattr(
        core.subprocess,
        "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("injected launch failure")),
    )
    with pytest.raises(OSError, match="launch failure"):
        core._start_client(
            clients,
            "prologue",
            root=tmp_path,
            directory=tmp_path,
            image="sha256:fixture",
            environment=tmp_path / "private.env",
            primary_id="primary",
            job=job,
        )
    assert len(clients) == 1 and clients[0]["volume_created"] == "original"
    assert clients[0].get("process") is None
    core._stop_clients(clients, job)


@pytest.mark.parametrize("damage", ["failed", "cleanup", "manifest"])
def test_invalid_prologue_stops_before_parallel_launch_and_never_promotes_costs(
    tmp_path, monkeypatch, damage
):
    _run_phase_fixture(tmp_path, monkeypatch, damage)


def test_unclean_physical_inventory_blocks_cache_and_passing_volume_deletion(tmp_path, monkeypatch):
    _run_phase_fixture(tmp_path, monkeypatch, "physical_inventory")


def test_complete_core_over_300_seconds_passes_during_stage2(tmp_path, monkeypatch):
    times = iter([0.0, 301.0, 302.0, 303.0])
    monkeypatch.setattr(core, "time", SimpleNamespace(monotonic=lambda: next(times)))
    report = _run_phase_fixture(tmp_path, monkeypatch, None)
    assert report["wall_seconds"] == 302.0
    assert report["budget_seconds"] == 300
    assert report["within_budget"] is False and report["budget_enforced"] is False


def _run_phase_fixture(tmp_path, monkeypatch, damage):
    home = tmp_path / "private-dev"
    parallel = _clients(4)
    serial = _prologue(parallel)
    if damage == "failed":
        serial["exit_code"] = 1
        serial["reports"]["core-results.json"]["counts"] = {"failed": 1}
    elif damage == "cleanup":
        serial["reports"]["core-client-summary.json"]["cleanup"]["complete"] = False
    elif damage == "manifest":
        serial["reports"]["partitions"]["controller.json"]["assigned"] = []
    launched = []
    stopped = []

    def start(clients, index, **kwargs):
        launched.append(index)
        assert damage in (None, "physical_inventory") or index == "prologue", (
            "an invalid prologue launched concurrent tests"
        )
        client = {"index": index, "process": SimpleNamespace(returncode=serial["exit_code"])}
        clients.append(client)
        return client

    def inventory(service):
        assert stopped and stopped[-1] == launched
        if damage == "physical_inventory":
            raise RuntimeError("retained physical database")

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SWINGSET_TEST_SERVICE_DSN", raising=False)
    monkeypatch.setattr(core.services, "home", lambda: home)
    monkeypatch.setattr(
        core.services, "ensure_service", lambda kind="test": SimpleNamespace(name=kind)
    )
    monkeypatch.setattr(
        core.services,
        "lifecycle_service",
        lambda: pytest.fail("bare core created a fresh fault cluster"),
    )
    monkeypatch.setattr(core.physical, "assert_clean", inventory)
    monkeypatch.setattr(core, "source_binding", lambda root: {"source": "fixed"})
    monkeypatch.setattr(core, "source_revision", lambda root: "source-sha256:fixed")
    monkeypatch.setattr(core.shutil, "disk_usage", lambda root: SimpleNamespace(free=100 * 1024**3))
    monkeypatch.setattr(core.core_images, "ensure", lambda root: "sha256:fixture")
    monkeypatch.setattr(
        core,
        "_container",
        lambda name: {
            "Id": "test",
            "Config": {"Image": core.services.POSTGRES_IMAGE},
            "HostConfig": {"Memory": 2 * 1024**3},
        },
    )
    monkeypatch.setattr(core, "_database_resources", lambda primary: _resources())
    monkeypatch.setattr(core, "_private_environment", lambda *args: {"OFFLINE": "true"})
    monkeypatch.setattr(core, "_start_client", start)
    monkeypatch.setattr(core, "_wait_clients", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        core, "_stop_clients", lambda clients, job: stopped.append([c["index"] for c in clients])
    )
    monkeypatch.setattr(
        core,
        "_client_record",
        lambda root, image, client, job, directory: (
            serial if client["index"] == "prologue" else parallel[client["index"]]
        ),
    )
    monkeypatch.setattr(
        core,
        "_docker",
        lambda *args, **kwargs: (
            subprocess.CompletedProcess(args, 1, "", "")
            if damage in (None, "physical_inventory") and args[:2] == ("container", "inspect")
            else pytest.fail("invalid phase reached Docker")
        ),
    )
    monkeypatch.setattr(
        core,
        "_remove_passed_volume",
        lambda *args: pytest.fail("an unclean job deleted its volume") if damage else None,
    )
    assert core.run(tmp_path) == int(damage is not None)
    assert launched == (
        ["prologue", 0, 1, 2, 3] if damage in (None, "physical_inventory") else ["prologue"]
    )
    assert (home / "core-weights.json").exists() == (damage is None)
    report = json.loads(next((home / "core-runs").glob("*/summary.json")).read_text())
    assert report["checks_passed"] == (damage is None)
    return report


@pytest.mark.parametrize(
    "damage",
    [
        "partition",
        "worker",
        "worker_selection",
        "checksum",
        "assignment",
        "executed_omission",
        "executed_duplicate",
        "skip",
        "xpass",
        "cleanup",
        "export",
        "uid",
        "namespace",
        "weight_digest",
        "exit",
    ],
)
def test_successful_exit_codes_cannot_hide_incomplete_or_different_work(damage):
    weights = {"tests/a.py": 10.0}
    clients = _clients(3, weights, "a" * 64)
    reports = clients[0]["reports"]
    parts = reports["partitions"]
    outcome = reports["core-results.json"]
    summary = reports["core-client-summary.json"]
    if damage == "partition":
        clients.pop()
    elif damage == "worker":
        parts.pop("gw3.json")
    elif damage == "worker_selection":
        parts["gw3.json"]["selected"].pop()
    elif damage == "checksum":
        parts["gw0.json"]["assigned_sha256"] = "0" * 64
    elif damage == "assignment":
        for part in parts.values():
            part["assigned"].pop()
            part["assigned_count"] = len(part["assigned"])
            part["assigned_sha256"] = _digest(part["assigned"])
    elif damage == "executed_omission":
        outcome["executed"].pop()
    elif damage == "executed_duplicate":
        outcome["executed"].append(outcome["executed"][0])
    elif damage in {"skip", "xpass"}:
        outcome["counts"]["skipped" if damage == "skip" else "xpassed"] = 1
    elif damage == "cleanup":
        summary["cleanup"]["complete"] = False
    elif damage == "export":
        summary["failure_artifact_export"]["complete"] = False
    elif damage == "uid":
        summary["uid"] = 0
    elif damage == "namespace":
        summary["pid_namespace"] = clients[1]["reports"]["core-client-summary.json"][
            "pid_namespace"
        ]
    elif damage == "weight_digest":
        parts["gw2.json"]["weights_sha256"] = "b" * 64
    elif damage == "exit":
        clients[0]["exit_code"] = 1
    assert core.validate_reports(
        clients, prologue=_prologue(clients), count=3, weights=weights, weights_sha256="a" * 64
    )["errors"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["--collect-only"],
        ["--lf"],
        ["--full-suite"],
        ["-n", "8"],
        ["-k", "round"],
        ["-m", "core"],
        ["tests/test_state.py"],
        ["--durations=10"],
    ],
)
def test_explicit_pytest_routes_stay_native(arguments, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SWINGSET_TEST_SERVICE_DSN", raising=False)
    assert not core.uses_fast_core(arguments)


def test_only_bare_local_default_controls_docker(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SWINGSET_TEST_SERVICE_DSN", raising=False)
    assert core.uses_fast_core([])
    monkeypatch.setenv("CI", "true")
    assert not core.uses_fast_core([])
    monkeypatch.delenv("CI")
    monkeypatch.setenv("SWINGSET_TEST_SERVICE_DSN", "private configured worker")
    assert not core.uses_fast_core([])


@pytest.mark.parametrize("edited", ["config/hosts.toml", "deploy/compose.yaml", "mise.toml"])
def test_launcher_uses_the_exact_source_identity_and_detects_noncode_input_edits(
    tmp_path, monkeypatch, edited
):
    from swingset.versions import repository_identity

    package = tmp_path / "src/swingset"
    package.mkdir(parents=True)
    module = package / "example.py"
    module.write_text("VERSION = 1\n")
    for name in (
        "Dockerfile",
        "pyproject.toml",
        "uv.lock",
        "mise.toml",
        "uv.toml",
        ".dockerignore",
    ):
        (tmp_path / name).write_text("fixture\n")
    policy = tmp_path / edited
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("first\n")
    monkeypatch.delenv("SWINGSET_REVISION", raising=False)
    identity = repository_identity(package)
    before = core.source_binding(tmp_path)
    monkeypatch.setenv("SWINGSET_REVISION", "stale-image-label")
    assert core.source_revision(tmp_path) == identity
    policy.write_text("second\n")
    assert core.source_binding(tmp_path) != before
    assert core.source_revision(tmp_path) == identity
    module.write_text("VERSION = 2\n")
    assert core.source_revision(tmp_path) != identity


def _resources():
    gib = 1024**3
    return {
        "services": {"test": {"current": gib, "working": gib, "events": {"oom_kill": 7}}},
        "primary_id": "test",
        "test_data": {
            "bytes_used": 100,
            "bytes_total": 1536 * 1024**2,
            "inodes_used": 1,
            "inodes_total": 100,
        },
    }


def test_database_measurement_keeps_primary_identity_and_includes_development_service(monkeypatch):
    sample = _resources()["services"]["test"]

    def docker(*arguments, **kwargs):
        if arguments[0] == "ps":
            assert "label=" + core.services.LABEL in arguments
            return subprocess.CompletedProcess(arguments, 0, "test\ndev\n", "")
        assert arguments[:2] == ("exec", "test")
        return subprocess.CompletedProcess(
            arguments, 0, "used size iused itotal\n100 1000 1 100\n", ""
        )

    monkeypatch.setattr(core, "_docker", docker)
    monkeypatch.setattr(core, "_measure_postgres", lambda identifier: sample.copy())
    report = core._database_resources("test")
    assert report["primary_id"] == "test"
    assert set(report["services"]) == {"test", "dev"}
    assert report["test_data"]["inodes_total"] == 100


@pytest.mark.parametrize(
    "pressure",
    [
        "fresh_client_oom",
        "new_database_oom",
        "aggregate",
        "primary",
        "data_bytes",
        "data_inodes",
        "client_bytes",
        "client_inodes",
        "disk",
    ],
)
def test_measured_resource_limits_stop_the_job_without_increasing_kernel_caps(pressure):
    gib = 1024**3
    clients = {
        "client": {
            "current": gib,
            "working": gib,
            "events": {"oom_kill": 0},
            "test_tmpfs": {
                "bytes_used": 100,
                "bytes_total": 1000,
                "inodes_used": 1,
                "inodes_total": 100,
            },
        }
    }
    databases = _resources()
    baseline = {"test": 7}
    free = 100 * gib
    assert core.resource_failure(clients, databases, baseline, free) is None
    if pressure == "fresh_client_oom":
        clients["client"]["events"]["oom_kill"] = 1
    elif pressure == "new_database_oom":
        databases["services"]["test"]["events"]["oom_kill"] = 8
    elif pressure == "aggregate":
        databases["services"]["dev"] = {
            "current": 6 * gib,
            "working": 6 * gib,
            "events": {"oom_kill": 0},
        }
    elif pressure == "primary":
        databases["services"]["test"]["working"] = int(1.76 * gib)
    elif pressure == "data_bytes":
        databases["test_data"]["bytes_used"] = int(1.26 * gib)
    elif pressure == "data_inodes":
        databases["test_data"]["inodes_used"] = 96
    elif pressure == "client_bytes":
        clients["client"]["test_tmpfs"]["bytes_used"] = 960
    elif pressure == "client_inodes":
        clients["client"]["test_tmpfs"]["inodes_used"] = 96
    elif pressure == "disk":
        free = 79 * gib
    assert core.resource_failure(clients, databases, baseline, free)


def test_cost_cache_uses_every_executed_case_and_all_three_measured_phases():
    clients = _clients()
    weights = core.measured_weights(clients)
    assert weights == {f"tests/{name}.py": pytest.approx(2.0) for name in "abcdef"}
    node = clients[0]["reports"]["core-results.json"]["executed"][0]
    del clients[0]["reports"]["core-results.json"]["node_phases"][node]["teardown"]
    with pytest.raises(RuntimeError, match="phase"):
        core.measured_weights(clients)


@pytest.mark.parametrize("foreign", [False, True])
def test_timed_out_report_reader_removes_only_its_exact_owned_helper(
    tmp_path, monkeypatch, foreign
):
    job = "a" * 24
    client = {"name": f"swingset-core-{job}-0", "volume": f"swingset-core-{job}-0-tmp"}
    calls = []

    def docker(*arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == "run":
            raise subprocess.TimeoutExpired("docker run", 15)
        if arguments[:2] == ("container", "inspect"):
            info = {
                "Id": "exact-owned-id",
                "Config": {"Labels": {core.LABEL: "other" if foreign else job}},
            }
            return subprocess.CompletedProcess(arguments, 0, json.dumps([info]), "")
        assert arguments == ("rm", "--force", "exact-owned-id")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(core, "_docker", docker)
    with pytest.raises(RuntimeError if foreign else subprocess.TimeoutExpired):
        core._read_reports(tmp_path, "sha256:fixture", client, job)
    assert (any(call[0] == "rm" for call in calls)) is (not foreign)


@pytest.mark.parametrize("damage", [None, "name", "label", "created", "reference"])
def test_passed_volume_cleanup_requires_the_original_owner_and_no_container_references(
    monkeypatch, damage
):
    job = "a" * 24
    name = f"swingset-core-{job}-0-tmp"
    client = {"index": 0, "volume": name, "volume_created": "original"}
    volume = {
        "Name": name,
        "CreatedAt": "original",
        "Labels": {core.LABEL: job, core.LABEL + ".partition": "0"},
    }
    if damage == "name":
        client["volume"] = "foreign-volume"
    elif damage == "label":
        volume["Labels"][core.LABEL] = "foreign"
    elif damage == "created":
        volume["CreatedAt"] = "replacement"
    calls = []

    def docker(*arguments, **kwargs):
        calls.append(arguments)
        if arguments[:2] == ("volume", "inspect"):
            return subprocess.CompletedProcess(arguments, 0, json.dumps([volume]), "")
        if arguments[0] == "ps":
            return subprocess.CompletedProcess(
                arguments, 0, "stopped-container" if damage == "reference" else "", ""
            )
        assert arguments == ("volume", "rm", name)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(core, "_docker", docker)
    if damage is None:
        core._remove_passed_volume(client, job)
    else:
        with pytest.raises(RuntimeError):
            core._remove_passed_volume(client, job)
    assert (("volume", "rm", name) in calls) is (damage is None)


def test_monitor_failure_stops_children_before_checking_the_warm_physical_service(
    tmp_path, monkeypatch
):
    home = tmp_path / "dev-home"
    children = []
    signals = []
    physical_removed = []
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}

    class Child:
        returncode = None

        def __init__(self, *args, **kwargs):
            children.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 130
            return self.returncode

    @contextmanager
    def physical():
        try:
            yield SimpleNamespace(name="physical")
        finally:
            assert len(children) == 1
            assert all(child.poll() == 130 for child in children), (
                "DB service removed before child cleanup"
            )
            physical_removed.append(True)

    def docker(*arguments, **kwargs):
        if arguments[:2] == ("volume", "create"):
            return subprocess.CompletedProcess(arguments, 0, "created", "")
        assert arguments[:2] == ("volume", "inspect")
        return subprocess.CompletedProcess(
            arguments, 0, json.dumps([{"CreatedAt": "original"}]), ""
        )

    def monitor(client):
        raise RuntimeError("injected monitor failure")

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SWINGSET_TEST_SERVICE_DSN", raising=False)
    monkeypatch.setattr(core, "PARTITIONS", 2)
    monkeypatch.setattr(core, "source_binding", lambda root: {"source": "fixed"})
    monkeypatch.setattr(core, "source_revision", lambda root: "source-sha256:fixed")
    monkeypatch.setattr(core.shutil, "disk_usage", lambda root: SimpleNamespace(free=100 * 1024**3))
    monkeypatch.setattr(core.core_images, "ensure", lambda root: "sha256:fixture")
    monkeypatch.setattr(core.services, "home", lambda: home)
    monkeypatch.setattr(
        core.services, "ensure_service", lambda kind="test": SimpleNamespace(name=kind)
    )
    monkeypatch.setattr(core.services, "lifecycle_service", physical)
    monkeypatch.setattr(core.physical, "assert_clean", lambda service: None)
    monkeypatch.setattr(
        core,
        "_container",
        lambda name: {
            "Id": "test",
            "Config": {"Image": core.services.POSTGRES_IMAGE},
            "HostConfig": {"Memory": 2 * 1024**3},
        },
    )
    monkeypatch.setattr(core, "_database_resources", lambda primary: _resources())
    monkeypatch.setattr(core, "_private_environment", lambda *args: {"OFFLINE": "true"})
    monkeypatch.setattr(core, "_docker", docker)
    monkeypatch.setattr(core.subprocess, "Popen", Child)
    monkeypatch.setattr(core, "_measure_client", monitor)
    monkeypatch.setattr(
        core,
        "_signal_client",
        lambda client, job, number: signals.append((client["index"], number)),
    )
    monkeypatch.setattr(core, "_read_reports", lambda *args: {"read_error": "interrupted fixture"})
    assert core.run(tmp_path) == 1
    assert physical_removed == []
    assert signals == [("prologue", "SIGINT")]
    directories = list((home / "core-runs").iterdir())
    assert len(directories) == 1
    directory = directories[0]
    assert not (directory / "private.env").exists()
    assert (directory / "client-prologue.log").is_file()
    assert (directory / "client-prologue.json").is_file()
    assert not json.loads((directory / "summary.json").read_text())["checks_passed"]
    assert {number: signal.getsignal(number) for number in previous} == previous


@pytest.mark.parametrize("finish", [0.35, 2.45])
def test_terminal_poll_returns_promptly_without_advancing_live_resource_samples(
    monkeypatch, finish
):
    now = [0.0]
    samples = []
    probes = []
    process = SimpleNamespace(poll=lambda: 0 if now[0] >= finish else None)
    clients = [{"name": "first", "process": process}, {"name": "second", "process": process}]
    monkeypatch.setattr(
        core,
        "time",
        SimpleNamespace(
            monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)
        ),
    )
    monkeypatch.setattr(core, "_measure_client", lambda client: {})

    def resources(primary):
        probes.append(now[0])
        now[0] += 0.2
        return {}

    monkeypatch.setattr(core, "_database_resources", resources)
    monkeypatch.setattr(core.shutil, "disk_usage", lambda root: SimpleNamespace(free=100 * 1024**3))
    monkeypatch.setattr(core, "resource_failure", lambda *args: None)
    assert (
        core._wait_clients(
            clients,
            root=None,
            primary_id="owned",
            job="a" * 24,
            baseline={},
            samples=samples,
            requested=[],
            started=0,
        )
        is None
    )
    if finish < 1:
        assert now[0] <= finish + 0.05 and probes == [0]
    else:
        assert probes == pytest.approx([0, 1.2, 2.4])
        assert [sample["seconds"] for sample in samples] == pytest.approx([0.2, 1.4, 2.6])


def test_interrupt_during_the_same_sampling_gap_is_forwarded_without_extra_probes(monkeypatch):
    now, requested, signals = [0.0], [], []
    finished = [False]
    process = SimpleNamespace(poll=lambda: 130 if finished[0] else None)
    clients = [{"name": "owned", "process": process}]

    def sleep(seconds):
        now[0] += seconds
        requested.append(signal.SIGINT)

    def stop(client, job, number):
        signals.append((now[0], number))
        finished[0] = True

    monkeypatch.setattr(core, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))
    monkeypatch.setattr(core, "_measure_client", lambda client: {})
    probes = []
    monkeypatch.setattr(core, "_database_resources", lambda primary: probes.append(True) or {})
    monkeypatch.setattr(core.shutil, "disk_usage", lambda root: SimpleNamespace(free=100 * 1024**3))
    monkeypatch.setattr(core, "resource_failure", lambda *args: None)
    monkeypatch.setattr(core, "_signal_client", stop)
    assert (
        core._wait_clients(
            clients,
            root=None,
            primary_id="owned",
            job="a" * 24,
            baseline={},
            samples=[],
            requested=requested,
            started=0,
        )
        == "interrupted"
    )
    assert probes == [True]
    assert signals == [(pytest.approx(0.05), "SIGINT")]
