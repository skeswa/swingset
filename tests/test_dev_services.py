"""Development services keep disposable test data separate from persistent data."""

import json
import os
import stat
import subprocess
import sys

import pytest

from swingset.dev import commands, services, testing


def _test_container(cpu=2_000_000_000):
    return {
        "Id": "a" * 64,
        "Name": "/swingset-test-postgres",
        "Config": {
            "Image": services.POSTGRES_IMAGE,
            "Labels": {services.LABEL: "test", services.LABEL + ".configuration": "expected"},
        },
        "HostConfig": {
            "Memory": 2 * 1024**3,
            "MemorySwap": 4 * 1024**3,
            "NanoCpus": cpu,
            "Tmpfs": {"/var/lib/postgresql/data": "rw,size=1536m"},
            "Binds": None,
        },
    }


@pytest.mark.parametrize("previous_cpu", [2_000_000_000, 4_000_000_000])
def test_existing_test_cpu_update_keeps_exact_container_and_all_data_mounts(
    monkeypatch, previous_cpu
):
    import copy

    original = _test_container(previous_cpu)
    current = copy.deepcopy(original)
    calls = []

    def docker(*arguments, **kwargs):
        calls.append(arguments)
        if arguments == ("update", "--cpus", "6", original["Id"]):
            current["HostConfig"]["NanoCpus"] = 6_000_000_000
            return subprocess.CompletedProcess(arguments, 0, "updated", "")
        assert arguments == ("container", "inspect", original["Id"])
        return subprocess.CompletedProcess(arguments, 0, json.dumps([current]), "")

    monkeypatch.setattr(services, "_docker", docker)
    services._ensure_test_cpu_limit(original, "expected")
    assert calls == [
        ("container", "inspect", original["Id"]),
        ("update", "--cpus", "6", original["Id"]),
        ("container", "inspect", original["Id"]),
    ]
    expected = copy.deepcopy(original)
    expected["HostConfig"]["NanoCpus"] = 6_000_000_000
    assert current == expected


def test_correct_test_cpu_limit_needs_no_extra_docker_round_trip(monkeypatch):
    monkeypatch.setattr(services, "_docker", lambda *_a, **_k: pytest.fail("extra Docker call"))
    services._ensure_test_cpu_limit(_test_container(6_000_000_000), "expected")


@pytest.mark.parametrize(
    "mismatch", ["identity", "name", "image", "owner", "memory", "tmpfs", "cpu"]
)
def test_test_cpu_update_refuses_unexpected_resources_and_owners(monkeypatch, mismatch):
    container = _test_container()
    if mismatch == "identity":
        container["Id"] = "not-an-exact-id"
    elif mismatch == "name":
        container["Name"] = "/swingset-dev-postgres"
    elif mismatch == "image":
        container["Config"]["Image"] = "postgres:latest"
    elif mismatch == "owner":
        container["Config"]["Labels"][services.LABEL] = "dev"
    elif mismatch == "memory":
        container["HostConfig"]["Memory"] = 4 * 1024**3
    elif mismatch == "tmpfs":
        container["HostConfig"]["Tmpfs"] = {}
    else:
        container["HostConfig"]["NanoCpus"] = 0
    monkeypatch.setattr(services, "_docker", lambda *_a, **_k: pytest.fail("unsafe update"))
    with pytest.raises(RuntimeError, match="Test CPU update"):
        services._ensure_test_cpu_limit(container, "expected")


def test_test_cpu_update_rechecks_identity_before_mutating(monkeypatch):
    original = _test_container()
    replacement = _test_container()
    replacement["Id"] = "b" * 64
    calls = []

    def docker(*arguments, **kwargs):
        calls.append(arguments)
        assert arguments == ("container", "inspect", original["Id"])
        return subprocess.CompletedProcess(arguments, 0, json.dumps([replacement]), "")

    monkeypatch.setattr(services, "_docker", docker)
    with pytest.raises(RuntimeError, match="Test CPU update"):
        services._ensure_test_cpu_limit(original, "expected")
    assert len(calls) == 1


@pytest.mark.parametrize("kind,cpu,memory", [("test", "6", "2g"), ("dev", "2", "4g")])
def test_new_service_cpu_defaults_leave_development_resources_unchanged(
    tmp_path, monkeypatch, kind, cpu, memory
):
    monkeypatch.setattr(services, "home", lambda: tmp_path)
    monkeypatch.setattr(services, "configured_ci_service", lambda: None)
    monkeypatch.setattr(services, "_configuration", lambda _: "expected")
    calls = []

    def docker(*arguments, **kwargs):
        calls.append(arguments)
        if arguments[:2] == ("container", "inspect"):
            return subprocess.CompletedProcess(arguments, 1, "", "")
        assert arguments[0] == "run"
        assert arguments[arguments.index("--cpus") + 1] == cpu
        assert arguments[arguments.index("--memory") + 1] == memory
        if kind == "test":
            assert arguments[arguments.index("--tmpfs") + 1].endswith("size=1536m")
        else:
            assert "--tmpfs" not in arguments
            assert "swingset-dev-postgres:/var/lib/postgresql/data" in arguments
        raise RuntimeError("creation arguments captured")

    monkeypatch.setattr(services, "_docker", docker)
    with pytest.raises(RuntimeError, match="creation arguments captured"):
        services.ensure_service(kind)
    assert len(calls) == 2


def _partition_project(tmp_path):
    """Exercise the real suite selector before the new file partition."""
    (tmp_path / "conftest.py").write_text((commands.ROOT / "tests" / "conftest.py").read_text())
    tests = tmp_path / "tests"
    tests.mkdir()
    files = [f"tests/test_partition_{number}.py" for number in range(8)]
    for name in files:
        (tmp_path / name).write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('side',['left','right'])\n"
            "def test_keep(side): pass\n"
            "@pytest.mark.extended\n"
            "def test_extended(): raise AssertionError('extended case selected')\n"
        )
    (tmp_path / "suite.toml").write_text("core=" + json.dumps(files) + "\nextended=[]\n")
    return files


def _run_partition(tmp_path, index, *arguments, count=2, weights=None, phase=None):
    reports = tmp_path / f"reports-{index}"
    environment = dict(os.environ)
    for key in ("PYTEST_XDIST_WORKER_COUNT", "PYTEST_ADDOPTS", "SWINGSET_CORE_PARTITION_WEIGHTS"):
        environment.pop(key, None)
    environment.update(
        PYTHONPATH=os.pathsep.join((str(commands.ROOT / "src"), environment.get("PYTHONPATH", ""))),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        SWINGSET_DEV_HOME=str(tmp_path / "dev-home"),
        SWINGSET_TEST_PARTITION_REPORT_DIR=str(reports),
    )
    if weights is not None:
        environment["SWINGSET_CORE_PARTITION_WEIGHTS"] = str(weights)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "swingset.dev.pytest_plugin",
            *([] if index is None else [f"--swingset-partition={index}/{count}"]),
            *([] if phase is None else [f"--swingset-core-phase={phase}"]),
            *arguments,
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return result, [json.loads(p.read_text()) for p in sorted(reports.glob("*.json"))]


def _prologue_project(tmp_path):
    from swingset.dev.core_partition import PROLOGUE_NODE

    files = _partition_project(tmp_path)
    name, function = PROLOGUE_NODE.split("::")
    (tmp_path / name).write_text(f"def {function}(): pass\n")
    priority = "tests/test_read_sets.py"
    (tmp_path / priority).write_text("def test_second(): pass\ndef test_first(): pass\n")
    (tmp_path / "suite.toml").write_text(
        "core=" + json.dumps([*files, name, priority]) + "\nextended=[]\n"
    )
    return PROLOGUE_NODE


def test_real_phase_collections_keep_full_selection_and_execute_disjoint_assignments(tmp_path):
    serial_node = _prologue_project(tmp_path)
    result, reports = _run_partition(tmp_path, None, phase="prologue")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(reports) == 1 and reports[0]["assigned"] == [serial_node]
    assert reports[0]["phase"] == "prologue" and reports[0]["worker"] == "controller"
    assert "1 passed" in result.stdout
    selected = reports[0]["selected"]
    assignments = [{serial_node}]
    for index in range(2):
        result, reports = _run_partition(
            tmp_path, index, "-p", "xdist.plugin", "-n", "2", phase="parallel"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert {part["worker"] for part in reports} == {"gw0", "gw1"}
        assert all(part["phase"] == "parallel" and part["selected"] == selected for part in reports)
        assert reports[0]["assigned"] == reports[1]["assigned"]
        assigned = set(reports[0]["assigned"])
        assert f"{len(assigned)} passed" in result.stdout
        assignments.append(assigned)
    assert len(selected) == 19
    assert selected[:2] == [
        "tests/test_read_sets.py::test_second",
        "tests/test_read_sets.py::test_first",
    ]
    assert sum(map(len, assignments)) == len(set().union(*assignments)) == len(selected)
    assert set().union(*assignments) == set(selected)


@pytest.mark.parametrize("problem", ["missing_prologue", "unclassified"])
def test_prologue_filter_cannot_hide_missing_case_or_classification_errors(tmp_path, problem):
    if problem == "missing_prologue":
        _partition_project(tmp_path)
    else:
        _prologue_project(tmp_path)
        (tmp_path / "tests/test_unknown.py").write_text("def test_unknown(): pass\n")
    result, reports = _run_partition(tmp_path, None, "--collect-only", phase="prologue")
    assert result.returncode == 4
    assert (
        "exactly once" if problem == "missing_prologue" else "Update tests/suite.toml"
    ) in result.stderr
    assert not reports


def test_file_partitions_cover_the_core_after_normal_keyword_selection(tmp_path, monkeypatch):
    # A serial child inside xdist inherits this variable but is a controller.
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw42")
    files = _partition_project(tmp_path)
    reports = []
    for index in range(2):
        result, manifest = _run_partition(tmp_path, index, "--collect-only", "-k", "left")
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(manifest) == 1
        assert manifest[0]["worker"] == "controller"
        reports.append(manifest[0])
    assert reports[0]["selected"] == reports[1]["selected"]
    assert len(reports[0]["selected"]) == len(files)
    first, second = (set(report["assigned"]) for report in reports)
    assert first and second
    assert first.isdisjoint(second)
    assert first | second == set(reports[0]["selected"])
    assert all("left" in node and "extended" not in node for node in first | second)


def test_xdist_workers_record_equal_full_and_file_affine_partition_membership(tmp_path):
    import hashlib

    files = _partition_project(tmp_path)
    result, reports = _run_partition(
        tmp_path,
        0,
        "-p",
        "xdist.plugin",
        "-n",
        "2",
        "--dist=loadfile",
        "--no-loadscope-reorder",
        "--max-worker-restart=0",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {report["worker"] for report in reports} == {"gw0", "gw1"}
    assert reports[0]["selected"] == reports[1]["selected"]
    assert reports[0]["assigned"] == reports[1]["assigned"]
    assert reports[0]["selected_count"] == 2 * len(files)
    assigned_files = {node.split("::", 1)[0] for node in reports[0]["assigned"]}
    for file in files:
        expected = int.from_bytes(hashlib.sha256(file.encode()).digest(), "big") % 2 == 0
        assert (file in assigned_files) == expected
        assert sum(node.startswith(file + "::") for node in reports[0]["assigned"]) == (
            2 if expected else 0
        )
    for path in (tmp_path / "reports-0").glob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("problem", ["unclassified", "stale"])
def test_partitioning_cannot_hide_suite_classification_errors(tmp_path, problem):
    _partition_project(tmp_path)
    if problem == "unclassified":
        (tmp_path / "tests" / "test_unknown.py").write_text("def test_unknown(): pass\n")
    else:
        path = tmp_path / "suite.toml"
        path.write_text(path.read_text().replace("core=[", 'core=["tests/test_stale.py",'))
    result, reports = _run_partition(tmp_path, 0, "--collect-only")
    assert result.returncode == 4
    assert "Update tests/suite.toml" in result.stderr
    assert not reports


@pytest.mark.parametrize("index", ["invalid", "-1", "2"])
def test_invalid_partitions_fail_before_collection(tmp_path, index):
    _partition_project(tmp_path)
    result, reports = _run_partition(tmp_path, index, "--collect-only")
    assert result.returncode == 4
    assert "--swingset-partition requires" in result.stderr
    assert not reports


@pytest.mark.parametrize("policy", [None, "failed"])
def test_historical_runner_only_forwards_the_explicit_temporary_policy(
    tmp_path, monkeypatch, policy
):
    from legacy_sqlite import CASES, HistoricalSQLite

    monkeypatch.setenv("PYTEST_ADDOPTS", "-k should_never_select -n 99 --full-suite")
    if policy is None:
        monkeypatch.delenv("SWINGSET_TEST_TMP_RETENTION", raising=False)
    else:
        monkeypatch.setenv("SWINGSET_TEST_TMP_RETENTION", policy)
    file = "test_phase5_migration.py"
    name = CASES[file][0]
    helper = object.__new__(HistoricalSQLite)
    helper.root, helper.runner, helper.completed = tmp_path, tmp_path / "runner", {}
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs["env"]))
        result = {
            "collection_errors": [],
            "results": {name: {"outcome": "passed", "phases": ["call"]}},
        }
        (tmp_path / (file + ".json")).write_text(json.dumps(result))
        return subprocess.CompletedProcess(command, 0, "passed", "")

    monkeypatch.setattr(subprocess, "run", execute)
    helper.assert_case(file, name)
    command, environment = calls[0]
    assert "PYTEST_ADDOPTS" not in environment
    assert "SWINGSET_TEST_TMP_RETENTION" not in environment
    assert "should_never_select" not in " ".join(command)
    assert "99" not in command
    assert ("tmp_path_retention_policy=failed" in command) == (policy == "failed")
    if policy == "failed":
        index = command.index("tmp_path_retention_policy=failed")
        assert command[index - 1] == "-o"
    assert command[-len(CASES[file]) :] == [
        str(tmp_path / "tests" / file) + "::" + case for case in CASES[file]
    ]


def test_historical_runner_refuses_unknown_harness_temporary_policy(tmp_path, monkeypatch):
    from legacy_sqlite import CASES, HistoricalSQLite

    helper = object.__new__(HistoricalSQLite)
    helper.root, helper.runner, helper.completed = tmp_path, tmp_path / "runner", {}
    monkeypatch.setenv("SWINGSET_TEST_TMP_RETENTION", "failed -k another_selection")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("started archive"))
    with pytest.raises(ValueError, match="Unknown historical test temporary retention"):
        helper.assert_case("test_phase5_migration.py", CASES["test_phase5_migration.py"][0])


def test_connection_credentials_are_private_and_never_in_service_repr(tmp_path):
    path = tmp_path / "nested" / "credentials.json"
    services.write_private(path, {"password": "private-value"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"password": "private-value"}
    service = services.Service("test", "test", 15432, "private-value")
    assert "private-value" not in repr(service)


def test_test_run_creation_and_empty_cleanup_do_not_contact_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "home", lambda: tmp_path)
    monkeypatch.setattr(services, "configured_ci_service", lambda: None)
    monkeypatch.setattr(
        services, "_docker", lambda *_args, **_kwargs: pytest.fail("pure test contacted Docker")
    )
    identifier = testing.begin_run()
    path = tmp_path / "test-runs" / f"{identifier}.json"
    assert path.is_file()
    testing.finish_test(identifier, "pure-test")
    testing.finish_run(identifier)
    assert not path.exists()


def test_explicit_restore_target_has_no_application_schema_and_keeps_role_separation(tmp_path):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    from swingset.state.engine import read_settings

    state = tmp_path / "restore-target"
    owner_dsn = testing.provision_empty(state)
    settings = read_settings(state)
    assert conninfo_to_dict(owner_dsn)["user"] != conninfo_to_dict(settings["dsn"])["user"]
    assert (
        conninfo_to_dict(settings["control_dsn"])["user"]
        != conninfo_to_dict(settings["dsn"])["user"]
    )
    with psycopg.connect(owner_dsn) as owner:
        assert owner.execute("SELECT to_regclass('ops.meta')").fetchone()[0] is None
        assert (
            owner.execute(
                "SELECT datcollate FROM pg_database WHERE datname=current_database()"
            ).fetchone()[0]
            == "C"
        )
    with pytest.raises(ValueError, match="already has database settings"):
        testing.provision_empty(state)
    testing.retire_state(state)
    assert not (state / "database.json").exists()
    assert testing.provision_empty(state) == owner_dsn


@pytest.mark.parametrize("kind", ["test", "dev"])
def test_configuration_change_cannot_replace_persistent_or_live_owned_service(
    tmp_path, monkeypatch, kind
):
    monkeypatch.setattr(services, "home", lambda: tmp_path)
    monkeypatch.setattr(services, "configured_ci_service", lambda: None)
    monkeypatch.setattr(services, "_configuration", lambda _kind: "new")
    monkeypatch.setattr(services, "active_runs", lambda: [{"pid": os.getpid()}])
    calls = []

    def docker(*arguments, **kwargs):
        calls.append(arguments)
        assert arguments[:2] == ("container", "inspect")
        return subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(
                [
                    {
                        "Config": {
                            "Image": services.POSTGRES_IMAGE,
                            "Labels": {
                                services.LABEL: kind,
                                services.LABEL + ".configuration": "old",
                            },
                        },
                        "State": {"Running": True},
                    }
                ]
            ),
            "",
        )

    monkeypatch.setattr(services, "_docker", docker)
    with pytest.raises(RuntimeError, match="live test owners"):
        services.ensure_service(kind)
    assert len(calls) == 1


def test_service_with_an_unrelated_owner_is_never_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "home", lambda: tmp_path)
    monkeypatch.setattr(services, "configured_ci_service", lambda: None)
    monkeypatch.setattr(services, "_configuration", lambda _kind: "new")
    monkeypatch.setattr(
        services,
        "_docker",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps([{"Config": {"Labels": {}}}]), ""
        ),
    )
    with pytest.raises(RuntimeError, match="not owned"):
        services.ensure_service()


def test_dead_cleanup_preserves_any_live_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "home", lambda: tmp_path)
    monkeypatch.setattr(services, "configured_ci_service", lambda: None)
    identifier = testing.begin_run()
    monkeypatch.setattr(services, "alive", lambda _pid: False)
    monkeypatch.setattr(services, "_group_alive", lambda _group: True)
    assert testing.clean_dead_runs() == 0
    assert (tmp_path / "test-runs" / f"{identifier}.json").exists()


def test_watch_requires_a_bounded_selector():
    with pytest.raises(ValueError, match="selector"):
        commands.watch([])


def test_invalid_run_identity_cannot_escape_ownership_directory():
    with pytest.raises(RuntimeError, match="identity"):
        testing._record_path("../somebody-else")


def test_states_clone_separate_databases_with_restricted_worker_roles(tmp_path):
    from swingset.state.db import open_database

    first_state, second_state = tmp_path / "first", tmp_path / "second"
    with open_database(first_state) as first, open_database(second_state) as second:
        assert first.environment == second.environment == "dev"
        assert first.connection.info.dbname != second.connection.info.dbname
        assert first.connection.info.user == "swingset_test_worker"
        first.connection.execute(
            "INSERT INTO ops.meta(key,value) VALUES (%s,%s)", ("isolation-probe", "first")
        )
        assert (
            second.connection.execute(
                "SELECT value FROM ops.meta WHERE key=%s", ("isolation-probe",)
            ).fetchone()
            is None
        )
        assert not first.connection.execute(
            "SELECT has_schema_privilege(current_user,'ops','CREATE')"
        ).fetchone()[0]
        assert not first.connection.execute(
            "SELECT has_table_privilege(current_user,'ops.environment_identity','UPDATE')"
        ).fetchone()[0]
    with open_database(first_state) as reopened:
        assert (
            reopened.connection.execute(
                "SELECT value FROM ops.meta WHERE key=%s", ("isolation-probe",)
            ).fetchone()[0]
            == "first"
        )


def test_test_state_marker_is_private_and_child_process_reuses_its_database(tmp_path):
    import sys

    from swingset.state.db import open_database

    state = tmp_path / "shared"
    with open_database(state) as database:
        expected = database.connection.info.dbname
    marker = state / "database.json"
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    script = "from swingset.state.db import open_database; import sys; db=open_database(sys.argv[1]); print(db.connection.info.dbname); db.close()"
    result = subprocess.run(
        [sys.executable, "-c", script, str(state)], text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_role_drift_is_rejected_without_repairing_a_live_service(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "home", lambda: tmp_path)
    monkeypatch.setattr(services, "configured_ci_service", lambda: None)
    monkeypatch.setattr(services, "_configuration", lambda _kind: "expected")
    services.write_private(
        tmp_path / "test-service.json",
        {
            "password": "p",
            "worker_password": "w",
            "control_password": "c",
            "roles_signature": "original",
        },
    )
    inspected = [
        {
            "Config": {
                "Image": services.POSTGRES_IMAGE,
                "Labels": {services.LABEL: "test", services.LABEL + ".configuration": "expected"},
            },
            "State": {"Running": True},
        }
    ]
    monkeypatch.setattr(
        services,
        "_docker",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, json.dumps(inspected), ""),
    )
    monkeypatch.setattr(services, "_roles_signature", lambda _conn: "changed")
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, statement, parameters=()):
            statements.append(statement)
            value = services.TEST_SETTINGS[parameters[0]] if parameters else 170011
            return type("Cursor", (), {"fetchone": lambda _self: (value,)})()

    monkeypatch.setattr(services.Service, "connect", lambda *_a, **_k: Connection())
    with pytest.raises(RuntimeError, match="role definitions changed"):
        services.ensure_service()
    assert all(statement.startswith(("SELECT current_setting", "SHOW")) for statement in statements)


def test_external_ci_service_requires_exact_pinned_image(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv(
        "SWINGSET_TEST_SERVICE_DSN", "host=127.0.0.1 port=15432 user=test password=private"
    )
    monkeypatch.setenv("SWINGSET_TEST_SERVICE_IMAGE", "postgres:latest")
    with pytest.raises(RuntimeError, match="pinned image"):
        services.configured_ci_service()


@pytest.mark.extended
def test_watch_coalesces_changes_and_cancels_child_processes(tmp_path):
    import signal
    import sys
    import time

    watched = tmp_path / "project"
    for name in ("src", "tests", "config", "overrides"):
        (watched / name).mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock"):
        (watched / name).write_text("")
    probe = watched / "src" / "probe.py"
    probe.write_text("VALUE = 1\n")
    events = tmp_path / "started.txt"
    selected = watched / "tests" / "test_probe.py"
    selected.write_text(
        "import os,time\n"
        "def test_probe():\n"
        f"    with open({str(events)!r}, 'a') as out: out.write(str(os.getpid())+'\\n')\n"
        "    time.sleep(30)\n"
    )
    script = (
        "from pathlib import Path; from swingset.dev import commands; commands.ROOT=Path("
        + repr(str(watched))
        + "); raise SystemExit(commands.watch(["
        + repr(str(selected))
        + "]))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    def starts():
        return [int(value) for value in events.read_text().splitlines()] if events.exists() else []

    def until(predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            assert process.poll() is None, "watch process exited before cancellation"
            assert time.monotonic() < deadline, "watch did not run the selected test"
            time.sleep(0.05)

    try:
        until(lambda: len(starts()) == 1)
        for number in range(4):
            probe.write_text(f"VALUE = {number}\n")
            time.sleep(0.02)
        until(lambda: len(starts()) >= 2)
        time.sleep(0.3)
        assert len(starts()) == 2
        assert not services.alive(starts()[0])
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    assert all(not services.alive(pid) for pid in starts())


def test_lifecycle_faults_cannot_restart_warm_services():
    with pytest.raises(RuntimeError, match="cannot restart warm"):
        services.restart_lifecycle(
            services.Service("swingset-test-postgres", "test", 15432, "private")
        )


@pytest.mark.extended
def test_lifecycle_cluster_restart_preserves_its_own_committed_data():
    with services.lifecycle_service() as service:
        with service.connect() as conn:
            conn.execute("CREATE TABLE lifecycle_probe(value TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO lifecycle_probe VALUES ('committed')")
        services.restart_lifecycle(service)
        with service.connect() as conn:
            assert conn.execute("SELECT value FROM lifecycle_probe").fetchone()[0] == "committed"


def test_measured_file_partitions_keep_all_nodes_and_order_heavy_files_first(tmp_path):
    import hashlib

    files = _partition_project(tmp_path)
    weights = tmp_path / "weights.json"
    weights.write_text(
        json.dumps({"format": 1, "weights": {files[0]: 100, files[1]: 90, files[2]: 80}})
    )
    reports = []
    for index in range(3):
        result, manifests = _run_partition(
            tmp_path, index, "--collect-only", count=3, weights=weights
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(manifests) == 1
        report = manifests[0]
        reports.append(report)
        assert report["partition_policy"] == "measured-lpt-v1"
        assert report["weights_sha256"] == hashlib.sha256(weights.read_bytes()).hexdigest()
        assert report["assigned"][0].startswith(files[index] + "::")
        for file in files:
            assigned = [node for node in report["assigned"] if node.startswith(file + "::")]
            assert len(assigned) in {0, 2}
            if assigned:
                assert "left" in assigned[0] and "right" in assigned[1]
    assert all(report["selected"] == reports[0]["selected"] for report in reports)
    assigned = [set(report["assigned"]) for report in reports]
    assert sum(map(len, assigned)) == len(set().union(*assigned))
    assert set().union(*assigned) == set(reports[0]["selected"])


def test_bad_measured_weights_cannot_silently_change_partition_coverage(tmp_path):
    files = _partition_project(tmp_path)
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps({"format": 1, "weights": {files[0]: -1}}))
    result, reports = _run_partition(tmp_path, 0, "--collect-only", count=3, weights=weights)
    assert result.returncode == 4
    assert "finite positive" in result.stderr
    assert reports == []


def test_xdist_worker_interrupt_remains_a_failed_case_with_timings(tmp_path):
    receipt = tmp_path / "crash.json"
    (tmp_path / "test_crash.py").write_text(
        "def test_crash():\n    raise KeyboardInterrupt('owned fixture worker interrupted')\n"
    )
    (tmp_path / "crash_receipt.py").write_text(
        "import json\nfrom pathlib import Path\nreports = []\n"
        "def pytest_runtest_logreport(report):\n"
        "    if report.failed:\n"
        "        reports.append({'nodeid': report.nodeid, 'when': report.when, "
        "'longrepr': str(report.longrepr)})\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    if hasattr(session.config, 'workerinput'):\n        return\n"
        "    reporter = session.config.pluginmanager.getplugin('terminalreporter')\n"
        f"    Path({str(receipt)!r}).write_text(json.dumps({{'exit': int(exitstatus), "
        "'failed': len(reporter.stats.get('failed', [])), 'reports': reports}))\n"
    )
    environment = dict(os.environ)
    for key in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "SWINGSET_TEST_TIMINGS_PATH",
        "SWINGSET_TEST_STARTED",
    ):
        environment.pop(key, None)
    environment["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(commands.ROOT / "src")))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "2",
            "--max-worker-restart=0",
            "--timings",
            "-p",
            "swingset.dev.pytest_plugin",
            "-p",
            "crash_receipt",
            "test_crash.py",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "keyboard-interrupt" in output and "INTERNALERROR" not in output
    recorded = json.loads(receipt.read_text())
    assert recorded["exit"] == 2 and recorded["failed"] == 1
    assert len(recorded["reports"]) == 1
    crash = recorded["reports"][0]
    assert crash["nodeid"] == "test_crash.py::test_crash" and crash["when"] == "???"
    assert "crashed while running" in crash["longrepr"] and "test_crash" in crash["longrepr"]
