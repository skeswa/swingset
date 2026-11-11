"""One-shot diagnostics retain real outcomes and never turn partial evidence into a pass."""

import argparse
import copy
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.dev import core_client, diagnostics


def outcome():
    return {
        "case": "scoring",
        "mode": "cpu",
        "node": diagnostics.NODE,
        "limits": dict(diagnostics.LIMITS),
        "exit_code": 0,
        "source_stable": True,
        "abort": None,
        "host_cleanup_ceiling_reached": False,
        "interrupted_signals": [],
        "actor_container_absent": True,
        "reports": {
            "core-results.json": {
                "counts": {"passed": 1},
                "executed": [diagnostics.NODE],
                "exit_code": 0,
                "cpu_profile": {"quality_pass": True},
                "diagnostic_measurement": {
                    "case": "scoring",
                    "mode": "cpu",
                    "node": diagnostics.NODE,
                },
            },
            "core-client-summary.json": {
                "pytest_exit_code": 0,
                "interrupted_signals": [],
                "diagnostic": {"measurement_ceiling_reached": False},
                "cleanup": {"complete": True},
                "failure_artifact_export": {"complete": True},
            },
        },
    }


@pytest.mark.parametrize(
    "damage",
    [
        "timeout",
        "late_abort",
        "interrupt",
        "lost_actor",
        "cleanup",
        "export",
        "quality",
        "wrong_case",
        "source",
        "nonzero",
    ],
)
def test_passing_pytest_report_cannot_hide_an_incomplete_diagnostic(damage):
    result = outcome()
    assert diagnostics.accepted(result)
    if damage == "timeout":
        result["reports"]["core-client-summary.json"]["diagnostic"][
            "measurement_ceiling_reached"
        ] = True
    elif damage == "late_abort":
        result["host_cleanup_ceiling_reached"] = True
    elif damage == "interrupt":
        result["interrupted_signals"] = [2]
    elif damage == "lost_actor":
        result["actor_container_absent"] = False
    elif damage in {"cleanup", "export"}:
        key = "cleanup" if damage == "cleanup" else "failure_artifact_export"
        result["reports"]["core-client-summary.json"][key]["complete"] = False
    elif damage == "quality":
        result["reports"]["core-results.json"]["cpu_profile"]["quality_pass"] = False
    elif damage == "wrong_case":
        result["reports"]["core-results.json"]["executed"] = ["another case"]
    elif damage == "source":
        result["source_stable"] = False
    else:
        result["exit_code"] = 124
    assert not diagnostics.accepted(result)


def test_generator_suspension_and_cumulative_time_never_win_the_cpu_ranking():
    def regular():
        return 1

    def generator():
        yield 1

    async def coroutine():
        return 1

    rows = [
        SimpleNamespace(code=regular.__code__, inlinetime=0.02, callcount=2, totaltime=900),
        SimpleNamespace(code=generator.__code__, inlinetime=500, callcount=2, totaltime=900),
        SimpleNamespace(code=coroutine.__code__, inlinetime=600, callcount=1, totaltime=900),
        SimpleNamespace(code="<built-in hash>", inlinetime=0.03, callcount=4, totaltime=999),
    ]
    result = diagnostics.cpu_report(SimpleNamespace(getstats=lambda: rows), wall=1, cpu=0.8)
    assert result["quality_pass"]
    assert [row["self_cpu_seconds"] for row in result["functions"]] == [0.03, 0.02]
    assert len(result["excluded"]) == 2
    assert "900" not in json.dumps(result)
    assert "999" not in json.dumps(result)
    assert result["body_thread_cpu_seconds"] == 0.8
    assert not result["normal_timing_claimed"]


@pytest.mark.parametrize("bad", [-1.0, float("inf"), float("nan")])
def test_invalid_profile_self_cost_is_explicitly_excluded_and_fails_quality(bad):
    rows = [SimpleNamespace(code="builtin", inlinetime=bad, callcount=1)]
    result = diagnostics.cpu_report(SimpleNamespace(getstats=lambda: rows), wall=1, cpu=0.8)
    assert not result["quality_pass"] and not result["functions"]
    assert result["excluded"] == [{"function": "builtin", "reason": "invalid self time"}]


@pytest.mark.parametrize(
    "case,mode",
    [
        (case, mode)
        for case in diagnostics.CASES
        for mode in diagnostics.MODES
        if case != "sql-guard" or mode == "cpu"
    ],
)
def test_prepare_records_current_binding_without_starting_or_replacing_an_actor(
    tmp_path, monkeypatch, case, mode
):
    calls = []
    image = "sha256:" + "a" * 64
    monkeypatch.setattr(
        diagnostics.core_runner,
        "_container",
        lambda name: {
            "Id": "b" * 64,
            "State": {"Running": True},
            "Config": {"Image": diagnostics.services.POSTGRES_IMAGE},
        },
    )

    def docker(*args):
        calls.append(args)
        assert args == ("image", "inspect", image), "prepare mutated Docker state"
        return SimpleNamespace(
            stdout=json.dumps([{"Id": image, "Os": "linux", "Architecture": "arm64"}])
        )

    monkeypatch.setattr(diagnostics.core_runner, "_docker", docker)
    monkeypatch.setattr(diagnostics.core_images, "native_platform", lambda: "linux/arm64")
    monkeypatch.setattr(diagnostics, "_binding", lambda root: ("source-current", "binding-current"))
    directory = tmp_path / "packet"
    plan = diagnostics.prepare(directory, image=image, case=case, mode=mode)
    assert plan["source"] == "source-current" and plan["binding"] == "binding-current"
    assert plan["node"] == diagnostics.CASES[case] and plan["mode"] == mode and plan["case"] == case
    assert plan["phase"] == diagnostics.CASE_PHASES[case]
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "plan.json").stat().st_mode) == 0o600
    assert not (directory / "started.json").exists()
    original = (directory / "plan.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        diagnostics.prepare(directory, image=image)
    assert (directory / "plan.json").read_bytes() == original and len(calls) == 1


def test_one_attempt_fence_is_file_and_directory_durable_and_never_replaced(tmp_path, monkeypatch):
    seen = []
    fsync = os.fsync

    def sync(fd):
        seen.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        fsync(fd)

    monkeypatch.setattr(diagnostics.os, "fsync", sync)
    diagnostics._consume(tmp_path, {"job": "owned"})
    assert seen == ["file", "directory"]
    original = (tmp_path / "started.json").read_bytes()
    with pytest.raises(FileExistsError):
        diagnostics._consume(tmp_path, {"job": "different"})
    assert (tmp_path / "started.json").read_bytes() == original


def test_busy_host_lock_refuses_without_mutating_the_packet(tmp_path):
    lock = tmp_path / "existing.lock"
    lock.write_bytes(b"unchanged")
    with diagnostics._lock(lock):
        with pytest.raises(BlockingIOError):
            with diagnostics._lock(lock):
                pytest.fail("another diagnostic entered the shared resource budget")
    assert lock.read_bytes() == b"unchanged"


def test_source_change_refuses_before_any_docker_or_attempt_effect(tmp_path, monkeypatch):
    plan = {
        "format": "swingset-diagnostic-v1",
        "mode": "cpu",
        "case": "scoring",
        "node": diagnostics.NODE,
        "measurement_seconds": 30,
        "cleanup_seconds": 120,
        "directory": str(tmp_path),
        "source": "old",
        "binding": "old",
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(diagnostics, "_binding", lambda root: ("new", "new"))
    monkeypatch.setattr(
        diagnostics.core_runner, "_docker", lambda *a, **kw: pytest.fail("Docker touched")
    )
    with pytest.raises(RuntimeError, match="source changed"):
        diagnostics._run_locked(tmp_path)
    assert not (tmp_path / "started.json").exists()


def test_registered_subcommand_prepares_only_the_requested_packet(tmp_path, monkeypatch, capsys):
    from swingset.dev import cli

    seen = []
    monkeypatch.setattr(
        diagnostics,
        "prepare",
        lambda directory, image, case, mode: (
            seen.append((directory, image, case, mode)) or {"prepared": True}
        ),
    )
    parser = argparse.ArgumentParser()
    cli.register_arguments(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        ["dev-diagnostic", "prepare", str(tmp_path / "new"), "--image", "pinned"]
    )
    assert cli.command(args) == 0
    assert seen == [(tmp_path / "new", "pinned", "scoring", "cpu")]
    assert json.loads(capsys.readouterr().out) == {"prepared": True}


@pytest.mark.parametrize("seconds", [0, -1, 31, float("nan")])
def test_invalid_measurement_bound_cannot_install_a_timer(seconds, monkeypatch):
    monkeypatch.setattr(
        core_client.threading, "Timer", lambda *a, **kw: pytest.fail("timer installed")
    )
    with pytest.raises(ValueError, match="at most 30"):
        core_client.run_command(["python"], partition="diagnostic", measurement_seconds=seconds)


def test_late_measurement_expiry_remains_failure_even_when_test_and_cleanup_pass(
    tmp_path, monkeypatch
):
    reports = []
    signal_handlers = {}
    monkeypatch.setattr(core_client, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        core_client,
        "os",
        SimpleNamespace(
            getpid=lambda: 1, getuid=lambda: 10001, environ={}, readlink=lambda path: "private-pid"
        ),
    )
    original = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda path: True if str(path) == "/.dockerenv" else original(path)
    )
    monkeypatch.setattr(
        core_client.signal,
        "signal",
        lambda number, handler: signal_handlers.setdefault(number, handler),
    )

    class Timer:
        def __init__(self, seconds, callback):
            self.callback = callback

        def start(self):
            pass

        def cancel(self):
            self.callback()

        def join(self, timeout):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(core_client.threading, "Timer", Timer)
    monkeypatch.setattr(
        core_client.subprocess,
        "Popen",
        lambda *a, **kw: SimpleNamespace(pid=20, poll=lambda: 0, wait=lambda: 0),
    )
    monkeypatch.setattr(core_client, "_stop_helpers", lambda: [])
    monkeypatch.setattr(core_client, "reap_copies", lambda *a: 2)
    monkeypatch.setattr(
        core_client, "_write_report", lambda path, value: reports.append(copy.deepcopy(value))
    )
    monkeypatch.setattr(core_client, "export_failures", lambda: {"complete": True})
    from swingset.dev import services, testing

    monkeypatch.setattr(testing, "clean_dead_runs", lambda: 0)
    monkeypatch.setattr(services, "ensure_service", lambda: object())
    monkeypatch.setattr(services, "configured_ci_lifecycle_service", lambda: object())
    monkeypatch.setattr(services, "home", lambda: tmp_path)
    assert (
        core_client.run_command(
            ["python", "original-case"], partition="diagnostic", measurement_seconds=30
        )
        == 124
    )
    assert reports[-1]["pytest_exit_code"] == 0 and reports[-1]["cleanup"]["complete"]
    assert reports[-1]["diagnostic"]["measurement_ceiling_reached"]


def test_oversized_cpu_report_is_bounded_and_cannot_be_accepted(monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_PROFILE_ENTRIES", 1)
    rows = [SimpleNamespace(code=name, inlinetime=0.1, callcount=1) for name in ("first", "second")]
    report = diagnostics.cpu_report(SimpleNamespace(getstats=lambda: rows), wall=1, cpu=0.8)
    assert not report["quality_pass"]
    assert report["entries_seen"] == 2 and report["entries_omitted"] == 1
    assert len(report["functions"]) == 1


@pytest.fixture
def ready_packet(tmp_path, monkeypatch):
    primary_id, job, image = "c" * 64, "a" * 24, "sha256:" + "d" * 64
    plan = {
        "format": "swingset-diagnostic-v1",
        "mode": "cpu",
        "case": "scoring",
        "node": diagnostics.NODE,
        "measurement_seconds": 30,
        "cleanup_seconds": 120,
        "directory": str(tmp_path),
        "source": "current",
        "binding": "binding",
        "primary_id": primary_id,
        "image": image,
        "job": job,
        "name": f"swingset-core-{job}-prologue",
        "volume": f"swingset-core-{job}-prologue-tmp",
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(diagnostics, "_binding", lambda root: ("current", "binding"))
    monkeypatch.setattr(diagnostics, "_image", lambda image: None)
    monkeypatch.setattr(
        diagnostics.core_runner,
        "_container",
        lambda value: {
            "Id": primary_id,
            "Name": "/swingset-test-postgres",
            "State": {"Running": True},
            "Config": {
                "Image": diagnostics.services.POSTGRES_IMAGE,
                "Labels": {diagnostics.services.LABEL: "test"},
            },
            "HostConfig": {"Memory": 2 * 1024**3, "NanoCpus": 6_000_000_000},
        },
    )
    private = tmp_path / "private-home"
    private.mkdir()
    (private / "test-service.json").write_text(
        json.dumps(
            {
                "configuration": "pinned",
                "password": "private",
                "worker_password": "worker",
                "control_password": "control",
                "roles_signature": "roles",
            }
        )
    )
    monkeypatch.setattr(diagnostics.services, "home", lambda: private)
    monkeypatch.setattr(diagnostics.services, "_configuration", lambda kind: "pinned")
    monkeypatch.setattr(diagnostics.services, "_validate_service", lambda service: None)
    monkeypatch.setattr(diagnostics.services, "_roles_signature", lambda conn: "roles")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            return SimpleNamespace(fetchone=lambda: (True, False))

    monkeypatch.setattr(diagnostics.services.Service, "connect", lambda *a: Connection())
    monkeypatch.setattr(diagnostics.physical, "_read", lambda: {})
    monkeypatch.setattr(diagnostics.physical, "_service", lambda record: object())
    monkeypatch.setattr(diagnostics.physical, "assert_clean", lambda service: None)
    monkeypatch.setattr(diagnostics.services, "active_runs", lambda: [])
    monkeypatch.setattr(diagnostics, "_idle", lambda *a: None)
    monkeypatch.setattr(diagnostics, "_absent", lambda *a: None)
    monkeypatch.setattr(
        diagnostics.core_runner, "_database_resources", lambda primary: {"services": {}}
    )
    monkeypatch.setattr(diagnostics.core_runner, "resource_failure", lambda *a: None)
    monkeypatch.setattr(
        diagnostics.core_runner, "_private_environment", lambda *a: {"PRIVATE": "never-retained"}
    )
    return tmp_path, plan


@pytest.mark.parametrize("refusal", ["owner", "resources"])
def test_idle_or_resource_refusal_precedes_one_attempt_fence_and_volume(
    ready_packet, monkeypatch, refusal
):
    directory, _ = ready_packet
    if refusal == "owner":
        monkeypatch.setattr(diagnostics.services, "active_runs", lambda: [{"pid": 1}])
    else:
        monkeypatch.setattr(
            diagnostics.core_runner, "resource_failure", lambda *a: "budget exceeded"
        )
    monkeypatch.setattr(
        diagnostics.core_runner,
        "_docker",
        lambda *a, **k: pytest.fail("preflight created a volume"),
    )
    with pytest.raises(RuntimeError, match="owners|budget"):
        diagnostics._run_locked(directory)
    assert not (directory / "started.json").exists()
    assert not (directory / "private.env").exists()


@pytest.mark.parametrize(
    "case,mode",
    [("scoring", "cpu"), ("uncertain-publication", "waits"), ("publication-fixture", "waits")],
)
def test_failed_actor_launch_retains_exact_owner_but_removes_private_environment(
    ready_packet, monkeypatch, case, mode
):
    directory, plan = ready_packet
    plan.update(
        case=case, mode=mode, node=diagnostics.CASES[case], phase=diagnostics.CASE_PHASES[case]
    )
    (directory / "plan.json").write_text(json.dumps(plan))
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        assert json.loads((directory / "started.json").read_text())["job"] == plan["job"]
        owner = json.loads((directory / "owner.json").read_text())
        assert owner["name"] == plan["name"] and owner["volume"] == plan["volume"]
        assert args[0] == "volume", "launch failure attempted unrelated Docker work"
        return SimpleNamespace(
            stdout=json.dumps(
                [
                    {
                        "Labels": {
                            diagnostics.core_runner.LABEL: plan["job"],
                            diagnostics.core_runner.LABEL + ".partition": "prologue",
                        },
                        "CreatedAt": "exact-created",
                    }
                ]
            )
        )

    monkeypatch.setattr(diagnostics.core_runner, "_docker", docker)
    monkeypatch.setattr(diagnostics.core_runner, "_client_command", lambda *a: [plan["image"]])

    def failed_launch(*args, **kwargs):
        environment = (directory / "private.env").read_text()
        assert f"{diagnostics.CASE_ENV}={case}\n" in environment
        assert f"{diagnostics.MODE_ENV}={mode}\n" in environment
        assert f"{diagnostics.PHASE_ENV}={diagnostics.CASE_PHASES[case]}\n" in environment
        raise OSError("injected")

    monkeypatch.setattr(diagnostics.subprocess, "Popen", failed_launch)
    with pytest.raises(OSError, match="injected"):
        diagnostics._run_locked(directory)
    assert [call[:2] for call in calls] == [("volume", "create"), ("volume", "inspect")]
    assert not (directory / "private.env").exists()
    failure = json.loads((directory / "failed.json").read_text())
    assert failure == {
        "error_type": "OSError",
        "actor_started": False,
        "volume_retained_for_review": True,
    }
    assert "never-retained" not in (directory / "failed.json").read_text()
    assert json.loads((directory / "owner.json").read_text())["volume_created"] == "exact-created"
    with pytest.raises(FileExistsError):
        diagnostics._consume(directory, plan)


@pytest.mark.parametrize(
    "code,message,allowed",
    [
        (0, "", False),
        (1, "Cannot connect to Docker daemon", False),
        (1, "Error: No such volume: owned", True),
    ],
)
def test_resource_absence_must_be_proved_not_assumed_from_an_error(
    monkeypatch, code, message, allowed
):
    monkeypatch.setattr(
        diagnostics.core_runner,
        "_docker",
        lambda *a, **k: SimpleNamespace(returncode=code, stderr=message),
    )
    if allowed:
        diagnostics._absent("volume", "owned")
    else:
        with pytest.raises(RuntimeError, match="absence is unconfirmed"):
            diagnostics._absent("volume", "owned")


def test_existing_nonce_resource_cannot_be_adopted_before_attempt_fence(ready_packet, monkeypatch):
    directory, _ = ready_packet

    def existing(*args):
        raise RuntimeError("resource already exists")

    monkeypatch.setattr(diagnostics, "_absent", existing)
    monkeypatch.setattr(
        diagnostics.core_runner, "_docker", lambda *a, **k: pytest.fail("resource created")
    )
    with pytest.raises(RuntimeError, match="already exists"):
        diagnostics._run_locked(directory)
    assert not (directory / "started.json").exists()
    assert not (directory / "owner.json").exists()


@pytest.mark.parametrize(
    "problem", ["client", "case_database", "admin_lookalike", "ledger", "test_run", "clean"]
)
def test_idle_admission_reads_existing_state_and_never_drops_or_reaps_it(
    tmp_path, monkeypatch, problem
):
    monkeypatch.setattr(diagnostics.services, "home", lambda: tmp_path)
    copy_root = tmp_path / "copies"
    copy_root.mkdir(mode=0o700)
    monkeypatch.setattr(diagnostics, "_copy_roots", lambda: (copy_root,))
    monkeypatch.setattr(diagnostics, "_endpoint", lambda service: service.name)
    if problem == "ledger":
        (copy_root / "old.json").write_text('{"endpoint":"primary"}')
    if problem == "test_run":
        (tmp_path / "test-runs").mkdir()
        (tmp_path / "test-runs" / "old.json").write_text("{}")
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql):
            statements.append(sql)
            assert sql.startswith("SELECT"), "idle preflight mutated prior server state"
            if "pg_stat_activity" in sql:
                return SimpleNamespace(fetchone=lambda: (1 if problem == "client" else 0,))
            rows = [
                ("postgres", False, True),
                ("template0", True, False),
                ("template1", True, True),
                (diagnostics.services.ROLE, False, True),
                ("sst_template_" + "a" * 20, True, False),
                ("sst_template_" + "b" * 20, True, False),
            ]
            if problem == "case_database":
                rows.append(("sst_owned_case", False, True))
            if problem == "admin_lookalike":
                rows.append((diagnostics.services.ROLE + "_case", False, True))
            return rows

    primary = SimpleNamespace(name="primary", connect=Connection)
    peer = SimpleNamespace(name="peer", connect=Connection)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*.json")}
    if problem == "clean":
        diagnostics._idle(primary, peer)
        assert len(statements) == 3
    else:
        with pytest.raises(RuntimeError, match="reviewed|connected"):
            diagnostics._idle(primary, peer)
    assert before == {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*.json")
    }


@pytest.mark.parametrize("prestarted", [False, True])
def test_cpu_profile_ignores_real_worker_threads_and_keeps_owner_accounting(
    monkeypatch, prestarted
):
    import math
    import threading
    import time

    original_clock = time.thread_time
    owner = threading.get_ident()
    timer_threads = set()
    ready, release = threading.Event(), threading.Event()

    def clock():
        timer_threads.add(threading.get_ident())
        return original_clock()

    def owner_work():
        stop = original_clock() + 0.003
        while original_clock() < stop:
            pass

    def worker_work():
        stop = original_clock() + 0.004
        while original_clock() < stop:
            pass

    def worker():
        ready.set()
        release.wait()
        worker_work()

    child = threading.Thread(target=worker)
    if prestarted:
        child.start()
        assert ready.wait(1)
    monkeypatch.setattr(diagnostics.time, "thread_time", clock)
    wall, cpu = time.monotonic(), original_clock()
    try:
        with diagnostics._owner_profile() as profile:
            if not prestarted:
                child.start()
                assert ready.wait(1)
            release.set()
            owner_work()
            child.join(1)
            assert not child.is_alive()
    finally:
        release.set()
        child.join(1)
    cpu = original_clock() - cpu
    entries = profile.getstats()
    assert timer_threads == {owner}
    assert any(entry.code == owner_work.__code__ for entry in entries)
    assert not any(entry.code == worker_work.__code__ for entry in entries)
    assert all(math.isfinite(entry.inlinetime) and entry.inlinetime >= 0 for entry in entries)
    result = diagnostics.cpu_report(profile, wall=time.monotonic() - wall, cpu=cpu)
    assert result["quality_pass"]
    assert (
        sum(row["self_cpu_seconds"] for row in result["functions"])
        <= cpu + result["accounting_tolerance_seconds"]
    )


@pytest.mark.parametrize("fail_body", [False, True])
def test_cpu_profiler_restores_unused_tool_callbacks_and_exception_state(fail_body):
    import sys

    monitoring = sys.monitoring
    tool, event = monitoring.PROFILER_ID, monitoring.events.PY_START
    assert monitoring.get_tool(tool) is None

    def marker(*args):
        return None

    monitoring.use_tool_id(tool, "fixture")
    old = monitoring.register_callback(tool, event, marker)
    monitoring.free_tool_id(tool)
    try:
        if fail_body:
            with pytest.raises(ValueError, match="body failed"), diagnostics._owner_profile():
                raise ValueError("body failed")
        else:
            with diagnostics._owner_profile():
                pass
        assert monitoring.get_tool(tool) is None and monitoring.get_events(tool) == 0
        monitoring.use_tool_id(tool, "fixture-check")
        assert monitoring.register_callback(tool, event, old) is marker
        monitoring.free_tool_id(tool)
    finally:
        if monitoring.get_tool(tool) is None:
            monitoring.use_tool_id(tool, "fixture-restore")
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, event, old)
        monitoring.free_tool_id(tool)


def test_cpu_profiler_never_claims_or_disables_a_foreign_profiler():
    import sys

    monitoring = sys.monitoring
    tool, event = monitoring.PROFILER_ID, monitoring.events.PY_START

    def marker(*args):
        return None

    monitoring.use_tool_id(tool, "foreign-fixture")
    old = monitoring.register_callback(tool, event, marker)
    try:
        with pytest.raises(RuntimeError, match="Another profiler"), diagnostics._owner_profile():
            pytest.fail("foreign profiler adopted")
        assert monitoring.get_tool(tool) == "foreign-fixture"
        assert monitoring.register_callback(tool, event, old) is marker
    finally:
        monitoring.register_callback(tool, event, old)
        monitoring.free_tool_id(tool)


@pytest.mark.parametrize("values", [[1.0], [0.5, 0.5]])
def test_impossible_positive_cpu_costs_fail_quality(values):
    rows = [
        SimpleNamespace(code=f"builtin-{i}", inlinetime=value, callcount=1)
        for i, value in enumerate(values)
    ]
    result = diagnostics.cpu_report(SimpleNamespace(getstats=lambda: rows), wall=1, cpu=0.8)
    assert not result["quality_pass"]


def test_partial_profiler_enable_failure_releases_its_own_monitoring_state(monkeypatch):
    import sys

    original = diagnostics._OwnerProfile.enable

    def fail_after_enable(self, **kwargs):
        original(self, **kwargs)
        raise RuntimeError("enable failed")

    monkeypatch.setattr(diagnostics._OwnerProfile, "enable", fail_after_enable)
    with pytest.raises(RuntimeError, match="enable failed"), diagnostics._owner_profile():
        pytest.fail("failed profiler yielded")
    monitoring = sys.monitoring
    assert monitoring.get_tool(monitoring.PROFILER_ID) is None
    assert monitoring.get_events(monitoring.PROFILER_ID) == 0


def test_unsupported_python_profile_api_is_rejected_before_claiming_a_tool(monkeypatch):
    import sys

    monkeypatch.setattr(diagnostics.sys, "version_info", (3, 13, 0))
    with pytest.raises(RuntimeError, match="supported CPython"), diagnostics._owner_profile():
        pytest.fail("unsupported profile API yielded")
    assert sys.monitoring.get_tool(sys.monitoring.PROFILER_ID) is None


@pytest.mark.parametrize("fail_body", [False, True])
def test_profile_body_disables_once_before_reporting_or_dumping(monkeypatch, fail_body):
    import contextlib
    import sys

    events = []
    original_disable = diagnostics._OwnerProfile.disable
    original_report = diagnostics.cpu_report

    def disable(profile):
        events.append("disable")
        return original_disable(profile)

    def report(*args, **kwargs):
        assert sys.monitoring.get_tool(sys.monitoring.PROFILER_ID) is None
        events.append("report")
        return original_report(*args, **kwargs)

    monkeypatch.setattr(diagnostics._OwnerProfile, "disable", disable)
    monkeypatch.setattr(
        diagnostics._OwnerProfile, "dump_stats", lambda *args: events.append("dump")
    )
    monkeypatch.setattr(diagnostics, "cpu_report", report)
    if fail_body:
        with pytest.raises(ValueError), contextlib.contextmanager(diagnostics._profile_body)():
            raise ValueError("body failed")
    else:
        with contextlib.contextmanager(diagnostics._profile_body)():
            pass
    assert events == ["disable", "report", "dump"]


def test_partial_callback_registration_restores_untouched_prior_callbacks(monkeypatch):
    import sys

    monitoring = sys.monitoring
    tool = monitoring.PROFILER_ID
    event = monitoring.events.PY_RESUME

    def marker(*args):
        return None

    monitoring.use_tool_id(tool, "fixture")
    old = monitoring.register_callback(tool, event, marker)
    monitoring.free_tool_id(tool)

    def partial(profile, **kwargs):
        monitoring.use_tool_id(tool, "cProfile")
        monitoring.register_callback(tool, monitoring.events.PY_START, profile._pystart_callback)
        raise RuntimeError("registration failed")

    monkeypatch.setattr(diagnostics._OwnerProfile, "enable", partial)
    try:
        with pytest.raises(RuntimeError, match="registration failed"), diagnostics._owner_profile():
            pytest.fail("partially registered profiler yielded")
        assert monitoring.get_tool(tool) is None and monitoring.get_events(tool) == 0
        monitoring.use_tool_id(tool, "fixture-check")
        assert monitoring.register_callback(tool, event, old) is marker
        monitoring.free_tool_id(tool)
    finally:
        if monitoring.get_tool(tool) is None:
            monitoring.use_tool_id(tool, "fixture-restore")
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, event, old)
        monitoring.free_tool_id(tool)


@pytest.mark.parametrize("foreign_after_cleanup", [False, True])
def test_disable_pending_error_still_restores_callbacks_or_preserves_foreign_owner(
    monkeypatch, foreign_after_cleanup
):
    import sys

    monitoring = sys.monitoring
    tool, event = monitoring.PROFILER_ID, monitoring.events.PY_START

    def marker(*args):
        return None

    def foreign(*args):
        return None

    monitoring.use_tool_id(tool, "fixture")
    old = monitoring.register_callback(tool, event, marker)
    monitoring.free_tool_id(tool)
    original_disable = diagnostics._OwnerProfile.disable
    pending = RuntimeError("pending profiler error after cleanup")

    def disable(profile):
        original_disable(profile)
        assert monitoring.get_tool(tool) is None and monitoring.get_events(tool) == 0
        if foreign_after_cleanup:
            monitoring.use_tool_id(tool, "foreign-after-cleanup")
            monitoring.register_callback(tool, event, foreign)
        raise pending

    monkeypatch.setattr(diagnostics._OwnerProfile, "disable", disable)
    try:
        with pytest.raises(RuntimeError) as raised, diagnostics._owner_profile():
            pass
        if foreign_after_cleanup:
            assert "ownership changed" in str(raised.value)
            assert raised.value.__context__ is pending
            assert monitoring.get_tool(tool) == "foreign-after-cleanup"
            assert monitoring.register_callback(tool, event, old) is foreign
            monitoring.free_tool_id(tool)
        else:
            assert raised.value is pending
            assert monitoring.get_tool(tool) is None and monitoring.get_events(tool) == 0
            monitoring.use_tool_id(tool, "fixture-check")
            assert monitoring.register_callback(tool, event, old) is marker
            monitoring.free_tool_id(tool)
    finally:
        if monitoring.get_tool(tool) is None:
            monitoring.use_tool_id(tool, "fixture-restore")
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, event, old)
        monitoring.free_tool_id(tool)


def test_python_frame_profile_disables_c_events_and_charges_native_work_to_python():
    import hashlib
    import sys
    import time

    def caller():
        for value in range(100):
            hashlib.sha256(str(value).encode()).digest()

    wall, cpu = time.monotonic(), time.thread_time()
    with diagnostics._owner_profile() as profile:
        monitoring = sys.monitoring
        mask = monitoring.get_events(monitoring.PROFILER_ID)
        expected = 0
        for name in ("PY_START", "PY_RESUME", "PY_THROW", "PY_RETURN", "PY_YIELD", "PY_UNWIND"):
            expected |= getattr(monitoring.events, name)
        assert mask == expected
        assert not mask & (
            monitoring.events.CALL | monitoring.events.C_RETURN | monitoring.events.C_RAISE
        )
        caller()
    elapsed = time.thread_time() - cpu
    entries = profile.getstats()
    assert any(entry.code == caller.__code__ and entry.inlinetime > 0 for entry in entries)
    assert not any(isinstance(entry.code, str) for entry in entries)
    result = diagnostics.cpu_report(profile, wall=time.monotonic() - wall, cpu=elapsed)
    assert result["quality_pass"] and result["profile_scope"] == "python_frames_only"
    assert "without direct C-function ranking" in result["accounting"]


@pytest.mark.parametrize("driver", ["mock", "psycopg"])
@pytest.mark.parametrize("failure", [False, True])
def test_wait_wrapper_preserves_real_driver_signature_result_errors_and_restores(
    monkeypatch, driver, failure
):
    import psycopg

    class MockConnection:
        pass

    connection_type = psycopg.Connection if driver == "psycopg" else MockConnection
    connection = object()
    token = object()
    error = ValueError("secret-query-and-parameters")
    seen = []

    def original(self, generator, *, interval=0.1):
        seen.append((self, generator, interval))
        if failure:
            raise error
        return token

    monkeypatch.setattr(connection_type, "wait", original, raising=False)
    secret_generator = iter(["secret-query-and-parameters"])
    with diagnostics._owner_waits(connection_type) as tracking:
        if failure:
            with pytest.raises(ValueError) as caught:
                connection_type.wait(connection, secret_generator, interval=0.05)
            assert caught.value is error
        else:
            assert connection_type.wait(connection, secret_generator, interval=0.05) is token
    assert connection_type.wait is original
    assert seen == [(connection, secret_generator, 0.05)]
    report = tracking.report(wall=1, cpu=1, restored=True)
    assert report["owner_wait_calls"] == 1
    assert report["owner_wait_exceptions"] == int(failure)
    assert report["quality_pass"]
    assert "secret-query-and-parameters" not in json.dumps(report)
    assert "generator" not in tracking.__dict__ and "args" not in tracking.__dict__


def test_nested_owner_waits_count_once_and_read_only_outer_clocks(monkeypatch):
    calls = []

    class Driver:
        def wait(self, value, *, interval):
            calls.append(value)
            if value == "outer":
                return self.wait("inner", interval=interval)
            return "result"

    original = Driver.wait
    wall = iter([10.0, 14.0])
    cpu = iter([20.0, 21.0])
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: next(wall))
    monkeypatch.setattr(diagnostics.time, "thread_time", lambda: next(cpu))
    with diagnostics._owner_waits(Driver) as tracking:
        assert Driver().wait("outer", interval=0.1) == "result"
    assert Driver.wait is original and calls == ["outer", "inner"]
    report = tracking.report(wall=5, cpu=2, restored=True)
    assert report["owner_wait_calls"] == 1 and report["nested_owner_wait_calls"] == 1
    assert report["owner_wait_wall_seconds"] == 4 and report["owner_wait_thread_cpu_seconds"] == 1
    assert report["quality_pass"]


def test_foreign_wait_calls_preserve_behavior_without_reading_any_clock(monkeypatch):
    import threading

    class Driver:
        def wait(self, value, **kwargs):
            return value

    monkeypatch.setattr(
        diagnostics.time, "monotonic", lambda: pytest.fail("foreign wall clock read")
    )
    monkeypatch.setattr(
        diagnostics.time, "thread_time", lambda: pytest.fail("foreign CPU clock read")
    )
    values = []
    with diagnostics._owner_waits(Driver) as tracking:
        child = threading.Thread(
            target=lambda: values.append(Driver().wait("result", private="not-retained"))
        )
        child.start()
        child.join(1)
        assert not child.is_alive()
    assert values == ["result"]
    report = tracking.report(wall=1, cpu=1, restored=True)
    assert report["foreign_thread_wait_calls"] == 1 and report["owner_wait_calls"] == 0
    assert report["owner_wait_wall_seconds"] == report["owner_wait_thread_cpu_seconds"] == 0
    assert "not-retained" not in json.dumps(report)


@pytest.mark.parametrize(
    "field,value",
    [
        ("wall", -1),
        ("cpu", float("nan")),
        ("wall", float("inf")),
        ("wait_wall", 1.01),
        ("wait_cpu", 0.81),
        ("restored", False),
    ],
)
def test_wait_accounting_requires_finite_consistent_owner_clocks(field, value):
    args = {"wall": 1.0, "cpu": 0.8, "wait_wall": 0.9, "wait_cpu": 0.4, "restored": True}
    args[field] = value
    tracking = diagnostics._OwnerWaits()
    tracking.calls = 2
    tracking.wall, tracking.cpu = args.pop("wait_wall"), args.pop("wait_cpu")
    assert not tracking.report(**args)["quality_pass"]


@pytest.mark.parametrize("failure", [False, True])
def test_wait_body_restores_driver_on_body_exception_and_records_owner_scope(monkeypatch, failure):
    import psycopg

    def original(self, *args, **kwargs):
        return 42

    monkeypatch.setattr(psycopg.Connection, "wait", original)
    result = {}
    try:
        with diagnostics._wait_body() as result:
            assert psycopg.Connection.wait(object(), "secret") == 42
            if failure:
                raise ValueError("body failure")
    except ValueError:
        assert failure
    assert psycopg.Connection.wait is original
    assert result["driver_wrapper_restored"] and result["quality_pass"]
    assert result["owner_wait_calls"] == 1


def test_wait_wrapper_cannot_overwrite_a_peer_replacement():
    class Driver:
        def wait(self):
            pass

    def peer(self):
        return None

    with pytest.raises(RuntimeError, match="ownership changed"):
        with diagnostics._owner_waits(Driver):
            Driver.wait = peer
    assert Driver.wait is peer


@pytest.mark.parametrize(
    "case,mode",
    [
        (case, mode)
        for case in diagnostics.CASES
        for mode in diagnostics.MODES
        if case != "sql-guard" or mode == "cpu"
    ],
)
def test_actor_binds_only_named_case_mode_and_restores_environment(monkeypatch, case, mode):
    monkeypatch.setenv(diagnostics.CASE_ENV, case)
    monkeypatch.setenv(diagnostics.MODE_ENV, mode)
    monkeypatch.setenv(diagnostics.PHASE_ENV, diagnostics.CASE_PHASES[case])
    monkeypatch.setenv(diagnostics.PROFILE_ENV, "previous")
    before = {
        k: os.environ.get(k)
        for k in [
            diagnostics.CASE_ENV,
            diagnostics.MODE_ENV,
            diagnostics.PHASE_ENV,
            diagnostics.PROFILE_ENV,
        ]
    }

    def run(command, **kwargs):
        assert command[4] == diagnostics.CASES[case]
        assert os.environ[diagnostics.PROFILE_ENV] == "1"
        assert os.environ[diagnostics.CASE_ENV] == case and os.environ[diagnostics.MODE_ENV] == mode
        assert os.environ[diagnostics.PHASE_ENV] == diagnostics.CASE_PHASES[case]
        assert kwargs == {"partition": "diagnostic", "measurement_seconds": 30}
        return 0

    monkeypatch.setattr(diagnostics.core_client, "run_command", run)
    assert diagnostics.actor() == 0
    assert {k: os.environ.get(k) for k in before} == before


def test_actor_driver_failure_restores_all_diagnostic_environment(monkeypatch):
    monkeypatch.setenv(diagnostics.CASE_ENV, "uncertain-publication")
    monkeypatch.setenv(diagnostics.MODE_ENV, "waits")
    monkeypatch.setenv(diagnostics.PHASE_ENV, "call")
    monkeypatch.delenv(diagnostics.PROFILE_ENV, raising=False)

    def fail(*args, **kwargs):
        os.environ[diagnostics.MODE_ENV] = "changed"
        os.environ[diagnostics.PHASE_ENV] = "changed"
        raise OSError("launch failure")

    monkeypatch.setattr(diagnostics.core_client, "run_command", fail)
    with pytest.raises(OSError):
        diagnostics.actor()
    assert diagnostics.PROFILE_ENV not in os.environ
    assert os.environ[diagnostics.CASE_ENV] == "uncertain-publication"
    assert os.environ[diagnostics.MODE_ENV] == "waits"
    assert os.environ[diagnostics.PHASE_ENV] == "call"


@pytest.mark.parametrize("case,mode", [("arbitrary::selector", "cpu"), ("scoring", "io"), ("", "")])
def test_invalid_actor_and_prepare_scope_refuse_before_docker_or_driver(
    tmp_path, monkeypatch, case, mode
):
    monkeypatch.setenv(diagnostics.CASE_ENV, case)
    monkeypatch.setenv(diagnostics.MODE_ENV, mode)
    monkeypatch.setattr(
        diagnostics.core_client, "run_command", lambda *a, **kw: pytest.fail("test launched")
    )
    monkeypatch.setattr(
        diagnostics.core_runner, "_container", lambda *a, **kw: pytest.fail("Docker touched")
    )
    with pytest.raises(ValueError, match="case or mode"):
        diagnostics.actor()
    with pytest.raises(ValueError, match="case or mode"):
        diagnostics.prepare(tmp_path / "packet", image="pinned", case=case, mode=mode)
    assert not (tmp_path / "packet").exists()


def test_cli_passes_named_wait_case_and_rejects_arbitrary_selectors(tmp_path, monkeypatch, capsys):
    from swingset.dev import cli

    seen = []
    monkeypatch.setattr(
        diagnostics,
        "prepare",
        lambda directory, **kwargs: seen.append(kwargs) or {"prepared": True},
    )
    parser = argparse.ArgumentParser()
    cli.register_arguments(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "dev-diagnostic",
            "prepare",
            str(tmp_path),
            "--image",
            "pinned",
            "--case",
            "uncertain-publication",
            "--mode",
            "waits",
        ]
    )
    assert cli.command(args) == 0
    assert seen == [{"image": "pinned", "case": "uncertain-publication", "mode": "waits"}]
    capsys.readouterr()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["dev-diagnostic", "prepare", str(tmp_path), "--case", "tests/other.py::test_any"]
        )


@pytest.mark.parametrize("damage", [None, "case", "mode", "node", "quality", "clock", "old_report"])
def test_wait_acceptance_requires_exact_case_mode_node_and_independent_quality(damage):
    value = outcome()
    value.update(
        case="uncertain-publication", mode="waits", node=diagnostics.CASES["uncertain-publication"]
    )
    node = diagnostics.CASES["uncertain-publication"]
    measured = value["reports"]["core-results.json"]
    measured["executed"] = [node]
    measured["diagnostic_measurement"] = {
        "case": "uncertain-publication",
        "mode": "waits",
        "node": node,
    }
    tracking = diagnostics._OwnerWaits()
    tracking.calls, tracking.wall, tracking.cpu = 1, 0.5, 0.1
    measured["wait_profile"] = tracking.report(wall=1, cpu=0.2, restored=True)
    if damage in {"case", "mode", "node"}:
        measured["diagnostic_measurement"][damage] = "different"
    elif damage == "quality":
        measured["wait_profile"]["quality_pass"] = False
    elif damage == "clock":
        measured["wait_profile"]["owner_wait_wall_seconds"] = 2
    elif damage == "old_report":
        measured.pop("diagnostic_measurement")
        value.pop("case")
    assert diagnostics.accepted(value) is (damage is None)


@pytest.mark.parametrize("mode", diagnostics.MODES)
def test_checkpoint_acceptance_requires_mode_quality_and_complete_singleton(monkeypatch, mode):
    from test_core_client import _exercise_checkpoint_bindings, _fake_checkpoint_bindings

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_checkpoint_bindings()
    monkeypatch.setattr(restore_observer, "_checkpoint_bindings", lambda module: (bindings, driver))
    original_wait = driver.wait
    with diagnostics._owner_waits(driver) as tracking:
        with restore_observer.observe("controller", restore_observer.CHECKPOINT, owner) as payload:
            _exercise_checkpoint_bindings(owner)
    assert driver.wait is original_wait and tracking.calls == payload["body"]["wait_calls"] == 7
    collector = restore_observer.Collector()
    collector.accept(
        SimpleNamespace(
            nodeid=restore_observer.CHECKPOINT,
            when="call",
            user_properties=[(restore_observer.PROPERTY, payload)],
        ),
        "controller",
    )
    value = outcome()
    value.update(case="checkpoint", mode=mode, node=restore_observer.CHECKPOINT)
    measured = value["reports"]["core-results.json"]
    measured["executed"] = [restore_observer.CHECKPOINT]
    measured["diagnostic_measurement"] = {
        "case": "checkpoint",
        "mode": mode,
        "node": restore_observer.CHECKPOINT,
    }
    measured["wait_profile"] = tracking.report(wall=1, cpu=0.2, restored=True)
    measured["cpu_profile"] = diagnostics.cpu_report(
        SimpleNamespace(
            getstats=lambda: [
                SimpleNamespace(
                    code=_exercise_checkpoint_bindings.__code__,
                    inlinetime=0.01,
                    callcount=1,
                )
            ]
        ),
        wall=1,
        cpu=0.2,
    )
    measured["restore_call_observations"] = collector.report()
    assert diagnostics.accepted(value)
    profile = "cpu_profile" if mode == "cpu" else "wait_profile"
    for damage in (
        "missing",
        "missing_phase",
        "unsafe",
        "wrong_node",
        "false_flag",
        "mode_quality",
        "missing_profile",
        "old_version",
    ):
        changed = copy.deepcopy(value)
        result = changed["reports"]["core-results.json"]
        if damage == "missing":
            result.pop("restore_call_observations")
        elif damage == "mode_quality":
            result[profile]["quality_pass"] = False
        elif damage == "missing_profile":
            result.pop(profile)
        elif damage == "old_version":
            result["restore_call_observations"]["format"] = "swingset-restore-call-observations-v4"
        else:
            case = result["restore_call_observations"]["cases"][0]
            if damage == "missing_phase":
                case["coverage"]["activate_restored_state"] = 0
            elif damage == "unsafe":
                case["private"] = "unreviewed"
            elif damage == "wrong_node":
                case["nodeid"] = restore_observer.BASE + "[False]"
            else:
                case["quality"] = False
        assert not diagnostics.accepted(changed), damage


@pytest.mark.parametrize(
    "mutation", ["case", "mode", "node", "phase", "measurement_seconds", "cleanup_seconds"]
)
def test_reviewed_plan_scope_or_caps_cannot_drift_before_allocation(
    ready_packet, monkeypatch, mutation
):
    directory, plan = ready_packet
    plan[mutation] = {
        "case": "unknown",
        "mode": "unknown",
        "node": "arbitrary",
        "phase": "setup",
        "measurement_seconds": 31,
        "cleanup_seconds": 121,
    }[mutation]
    (directory / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(
        diagnostics.core_runner, "_docker", lambda *a, **kw: pytest.fail("Docker touched")
    )
    with pytest.raises(RuntimeError, match="plan or source"):
        diagnostics._run_locked(directory)
    assert not (directory / "started.json").exists()


def test_checkpoint_cpu_plan_passes_scope_validation_before_any_allocation(
    ready_packet, monkeypatch
):
    directory, plan = ready_packet
    plan.update(case="checkpoint", mode="cpu", node=diagnostics.CASES["checkpoint"], phase="call")
    (directory / "plan.json").write_text(json.dumps(plan))
    admitted = RuntimeError("scope admitted; stop before resource checks")

    def image_check(image):
        raise admitted

    monkeypatch.setattr(diagnostics, "_image", image_check)
    monkeypatch.setattr(
        diagnostics.core_runner, "_docker", lambda *args: pytest.fail("allocated a resource")
    )
    with pytest.raises(RuntimeError) as caught:
        diagnostics._run_locked(directory)
    assert caught.value is admitted
    assert not (directory / "started.json").exists()
    assert not (directory / "private.env").exists()


@pytest.mark.parametrize("failure", [False, True])
def test_real_psycopg_wait_consumes_completed_generator_without_database_or_socket(failure):
    import psycopg

    original = psycopg.Connection.wait
    sentinel = object()
    error = ValueError("private-generator-input")

    def generator():
        if failure:
            raise error
        if False:
            yield None
        return sentinel

    # A completed generator requires no socket readiness or server connection.
    connection = SimpleNamespace(pgconn=SimpleNamespace(socket=-1))
    with diagnostics._owner_waits(psycopg.Connection) as tracking:
        if failure:
            with pytest.raises(ValueError) as caught:
                psycopg.Connection.wait(connection, generator())
            assert caught.value is error
        else:
            assert psycopg.Connection.wait(connection, generator()) is sentinel
    assert psycopg.Connection.wait is original
    report = tracking.report(wall=1, cpu=1, restored=True)
    assert report["quality_pass"] and report["owner_wait_calls"] == 1
    assert report["owner_wait_exceptions"] == int(failure)
    assert "private-generator-input" not in json.dumps(report)


def test_negative_wait_sample_cannot_be_hidden_by_later_positive_sample(monkeypatch):
    class Driver:
        def wait(self):
            return 1

    wall = iter([10.0, 9.0, 10.0, 12.0])
    cpu = iter([0.0, 0.1, 0.1, 0.2])
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: next(wall))
    monkeypatch.setattr(diagnostics.time, "thread_time", lambda: next(cpu))
    with diagnostics._owner_waits(Driver) as tracking:
        assert Driver().wait() == Driver().wait() == 1
    report = tracking.report(wall=5, cpu=1, restored=True)
    assert report["owner_wait_wall_seconds"] == 1
    assert report["invalid_clock_samples"] == 1 and not report["quality_pass"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("body_wall_seconds", None),
        ("owner_wait_calls", True),
        ("owner_wait_exceptions", -1),
        ("invalid_clock_samples", None),
    ],
)
def test_corrupt_wait_report_is_rejected_without_trusting_quality_flag(field, value):
    tracking = diagnostics._OwnerWaits()
    report = tracking.report(wall=1, cpu=0.5, restored=True)
    assert report["quality_pass"]
    report[field] = value
    assert not diagnostics.wait_quality(report)


@pytest.mark.parametrize("case", tuple(case for case in diagnostics.CASES if case != "sql-guard"))
@pytest.mark.parametrize("failure", [False, True])
def test_named_phase_measures_only_its_real_driver_work_and_restores_on_error(
    monkeypatch, case, failure
):
    import psycopg

    expected_phase = diagnostics.CASE_PHASES[case]
    error = ValueError("private-fixture-body")
    calls = []
    measurement = {}

    def original(connection, generator):
        calls.append(next(generator))
        if failure and calls[-1] == expected_phase:
            raise error
        return "ordinary-result"

    monkeypatch.setattr(psycopg.Connection, "wait", original)
    for phase in ("setup", "call"):
        try:
            with diagnostics._measure_phase(
                diagnostics.CASES[case], phase, case=case, mode="waits", measurement=measurement
            ):
                assert (psycopg.Connection.wait is not original) is (phase == expected_phase)
                assert psycopg.Connection.wait(object(), iter([phase])) == "ordinary-result"
        except ValueError as caught:
            assert failure and phase == expected_phase and caught is error
        assert psycopg.Connection.wait is original

    assert calls == ["setup", "call"], "the ordinary non-measured phase must still execute"
    assert measurement["phase"] == expected_phase
    assert measurement["owner_wait_calls"] == 1
    assert measurement["owner_wait_exceptions"] == int(failure)
    assert measurement["driver_wrapper_restored"]
    assert measurement["quality_pass"] and diagnostics.wait_quality(measurement)
    assert "private-fixture-body" not in json.dumps(measurement)


def test_phase_selection_keeps_cpu_profiling_on_only_the_named_phase(monkeypatch):
    events = []

    def profile():
        events.append("enabled")
        try:
            yield {"quality_pass": True}
        finally:
            events.append("disabled")

    monkeypatch.setattr(diagnostics, "_profile_body", profile)
    measurement = {}
    for phase in ("setup", "call"):
        with diagnostics._measure_phase(
            diagnostics.CASES["publication-fixture"],
            phase,
            case="publication-fixture",
            mode="cpu",
            measurement=measurement,
        ):
            events.append(phase)
    assert events == ["enabled", "setup", "disabled", "call"]
    assert measurement == {"quality_pass": True, "phase": "setup"}


@pytest.mark.parametrize("phase", [None, "call", "teardown"])
def test_setup_actor_refuses_missing_or_changed_phase_before_running(monkeypatch, phase):
    monkeypatch.setenv(diagnostics.CASE_ENV, "publication-fixture")
    monkeypatch.setenv(diagnostics.MODE_ENV, "waits")
    if phase is None:
        monkeypatch.delenv(diagnostics.PHASE_ENV, raising=False)
    else:
        monkeypatch.setenv(diagnostics.PHASE_ENV, phase)
    monkeypatch.setattr(
        diagnostics.core_client, "run_command", lambda *a, **kw: pytest.fail("actor started")
    )
    with pytest.raises(ValueError, match="phase"):
        diagnostics.actor()


@pytest.mark.parametrize("phase", [None, "call", "teardown"])
def test_setup_plan_requires_explicit_named_phase_before_allocation(
    ready_packet, monkeypatch, phase
):
    directory, plan = ready_packet
    plan.update(
        case="publication-fixture", mode="waits", node=diagnostics.CASES["publication-fixture"]
    )
    if phase is not None:
        plan["phase"] = phase
    (directory / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(
        diagnostics.core_runner, "_docker", lambda *a, **kw: pytest.fail("Docker touched")
    )
    with pytest.raises(RuntimeError, match="plan or source"):
        diagnostics._run_locked(directory)
    assert not (directory / "started.json").exists()


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "result_missing",
        "result_call",
        "profile_missing",
        "profile_call",
        "measurement_missing",
        "measurement_call",
    ],
)
def test_setup_acceptance_cannot_substitute_a_short_call_measurement(damage):
    value = outcome()
    node = diagnostics.CASES["publication-fixture"]
    value.update(case="publication-fixture", mode="waits", node=node, phase="setup")
    measured = value["reports"]["core-results.json"]
    measured["executed"] = [node]
    measured["diagnostic_measurement"] = {
        "case": "publication-fixture",
        "mode": "waits",
        "node": node,
        "phase": "setup",
    }
    measured["wait_profile"] = diagnostics._OwnerWaits().report(wall=1, cpu=0.2, restored=True)
    measured["wait_profile"]["phase"] = "setup"
    if damage:
        target, mutation = damage.split("_")
        report = {
            "result": value,
            "profile": measured["wait_profile"],
            "measurement": measured["diagnostic_measurement"],
        }[target]
        if mutation == "missing":
            report.pop("phase")
        else:
            report["phase"] = "call"
    assert diagnostics.accepted(value) is (damage is None)


def test_publication_fixture_cli_has_no_arbitrary_phase_or_selector(tmp_path, monkeypatch, capsys):
    from swingset.dev import cli

    seen = []
    monkeypatch.setattr(
        diagnostics, "prepare", lambda directory, **kw: seen.append(kw) or {"prepared": True}
    )
    parser = argparse.ArgumentParser()
    cli.register_arguments(parser.add_subparsers(dest="command"))
    command = [
        "dev-diagnostic",
        "prepare",
        str(tmp_path),
        "--image",
        "pinned",
        "--case",
        "publication-fixture",
        "--mode",
        "waits",
    ]
    assert cli.command(parser.parse_args(command)) == 0
    assert seen == [{"image": "pinned", "case": "publication-fixture", "mode": "waits"}]
    capsys.readouterr()
    with pytest.raises(SystemExit):
        parser.parse_args(command + ["--phase", "call"])


@pytest.fixture
def sql_guard_module(tmp_path, monkeypatch):
    """Execute the exact original guard bytecode over two small source files."""
    import importlib
    import types

    original = importlib.import_module("test_output_packs")
    module = types.ModuleType(original.__name__)
    module.__dict__.update(vars(original))
    source = tmp_path / "source"
    (source / "build").mkdir(parents=True)
    (source / "build" / "partitions.py").write_text(
        'def _refs():\n    return "SELECT derivation_rows"\n'
        'def _dancer_rows():\n    return "SELECT derivation_row_refs"\n'
        "def ordinary():\n    return [number for number in range(3)]\n"
    )
    (source / "ordinary.py").write_text('"""derivation_rows is a docstring"""\nvalue = 1\n')
    module.SRC = source
    for name in (
        "_docstrings",
        "_naming",
        "test_only_allowlisted_modules_name_the_row_tables_in_sql",
    ):
        function = getattr(original, name)
        module.__dict__[name] = types.FunctionType(function.__code__, vars(module), name)
    monkeypatch.setitem(diagnostics.sys.modules, "test_output_packs", module)
    return module


def _run_sql_guard(module):
    module.test_only_allowlisted_modules_name_the_row_tables_in_sql()


def test_sql_guard_aggregates_preserve_real_original_guard_and_do_not_profile(
    sql_guard_module, monkeypatch
):
    import ast

    module = sql_guard_module
    original_parse, original_naming = ast.parse, module._naming
    _run_sql_guard(module)
    monkeypatch.setattr(
        diagnostics, "_profile_body", lambda: pytest.fail("whole-body profiler used")
    )
    measurement = {}
    with diagnostics._measure_phase(
        diagnostics.CASES["sql-guard"],
        "call",
        case="sql-guard",
        mode="cpu",
        measurement=measurement,
    ):
        _run_sql_guard(module)
    assert ast.parse is original_parse and module._naming is original_naming
    assert measurement["phase"] == "call"
    assert diagnostics.guard_quality(measurement) and measurement["quality_pass"], json.dumps(
        measurement, sort_keys=True
    )
    rows = measurement["buckets"]
    assert rows["global_parse"]["calls"] == rows["global_naming"]["calls"] == 2
    assert rows["partition_parse"]["calls"] == 1
    assert rows["function_naming"]["calls"] == 3, "one naming evaluation per fixture FunctionDef"
    assert all(
        row["calls"] == row["normal_returns"] and row["exceptions"] == 0 for row in rows.values()
    )
    assert "derivation_rows" not in json.dumps(measurement)
    assert str(module.SRC) not in json.dumps(measurement)
    assert "membership" in measurement["scope"]


def test_sql_guard_cpu_only_selection_and_plan_refuse_before_any_effect(ready_packet, monkeypatch):
    directory, plan = ready_packet
    assert diagnostics.selection("sql-guard", "cpu") == diagnostics.CASES["sql-guard"]
    with pytest.raises(ValueError, match="Unsupported"):
        diagnostics.selection("sql-guard", "waits")
    plan.update(case="sql-guard", mode="waits", node=diagnostics.CASES["sql-guard"], phase="call")
    (directory / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(diagnostics, "_image", lambda image: pytest.fail("image inspected"))
    with pytest.raises(RuntimeError, match="plan or source changed"):
        diagnostics._run_locked(directory)
    assert not (directory / "started.json").exists()


def test_sql_guard_original_helper_exception_is_unchanged_and_hooks_restore(sql_guard_module):
    import ast

    module = sql_guard_module
    original_parse, original_naming = ast.parse, module._naming
    error = ValueError("private-original-helper-failure")

    def fail(_value):
        raise error

    module.TABLES = SimpleNamespace(search=fail)
    result = {}
    with pytest.raises(ValueError) as raised:
        with diagnostics._guard_body() as result:
            _run_sql_guard(module)
    assert raised.value is error
    assert ast.parse is original_parse and module._naming is original_naming
    assert result["hooks_restored"] and not result["quality_pass"]
    assert "private-original-helper-failure" not in json.dumps(result)
    assert "case_exception" in result["errors"]


@pytest.mark.parametrize("bad", [False, -1, float("nan")])
def test_sql_guard_clock_faults_fail_quality_without_changing_guard_result(
    sql_guard_module, monkeypatch, bad
):
    monkeypatch.setattr(diagnostics, "_GUARD_CPU", lambda: bad)
    with diagnostics._guard_body() as result:
        _run_sql_guard(sql_guard_module)
    assert result["completed"] and result["hooks_restored"]
    assert not result["quality_pass"] and "clock_error" in result["errors"]


def test_sql_guard_clock_interrupt_during_install_restores_both_originals(
    sql_guard_module, monkeypatch
):
    import ast

    original_parse, original_naming = ast.parse, sql_guard_module._naming
    error = KeyboardInterrupt()

    def interrupt():
        raise error

    monkeypatch.setattr(diagnostics, "_GUARD_CPU", interrupt)
    with pytest.raises(KeyboardInterrupt) as raised:
        with diagnostics._guard_body():
            pytest.fail("body entered after clock interruption")
    assert raised.value is error
    assert ast.parse is original_parse and sql_guard_module._naming is original_naming


def test_sql_guard_foreign_and_other_callers_are_untimed_and_fail_quality(sql_guard_module):
    import ast
    import threading

    values = []
    with diagnostics._guard_body() as result:
        worker = threading.Thread(target=lambda: values.append(ast.parse("private_foreign = 1")))
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert isinstance(ast.parse("private_other = 2"), ast.Module)
        _run_sql_guard(sql_guard_module)
    assert len(values) == 1 and isinstance(values[0], ast.Module)
    assert result["foreign_calls"] == 1 and result["other_calls"] == 1
    assert result["buckets"]["global_parse"]["calls"] == 2
    assert not result["quality_pass"]
    assert "private_foreign" not in json.dumps(result) and "private_other" not in json.dumps(result)


def test_sql_guard_hook_tampering_retains_peer_and_is_not_accepted(sql_guard_module):
    import ast

    original = ast.parse

    def peer(*args, **kwargs):
        return original(*args, **kwargs)

    try:
        with diagnostics._guard_body() as result:
            _run_sql_guard(sql_guard_module)
            ast.parse = peer
        assert ast.parse is peer
        assert not result["hooks_restored"] and not result["quality_pass"]
        assert "hook_changed" in result["errors"]
    finally:
        ast.parse = original


def test_sql_guard_count_ceiling_preserves_original_execution_and_rejects_quality(
    sql_guard_module, monkeypatch
):
    monkeypatch.setattr(diagnostics, "_GUARD_LIMIT", 1)
    with diagnostics._guard_body() as result:
        _run_sql_guard(sql_guard_module)
    assert result["completed"] and result["hooks_restored"]
    assert not result["quality_pass"] and "count_limit" in result["errors"]


def test_sql_guard_different_helper_globals_refuse_before_hook_install(sql_guard_module):
    import ast
    import types

    original_parse = ast.parse
    naming = sql_guard_module._naming
    sql_guard_module._naming = types.FunctionType(naming.__code__, dict(naming.__globals__))
    with pytest.raises(RuntimeError, match="original regular module"):
        with diagnostics._guard_body():
            pytest.fail("foreign helper admitted")
    assert ast.parse is original_parse


@pytest.mark.parametrize(
    "damage",
    [
        "bool_count",
        "negative_clock",
        "missing_partition",
        "false_owner",
        "truncated",
        "fake_quality",
    ],
)
def test_sql_guard_acceptance_validates_actual_payload_not_only_quality_flag(
    sql_guard_module, damage
):
    with diagnostics._guard_body() as measured:
        _run_sql_guard(sql_guard_module)
    measured["phase"] = "call"
    value = outcome()
    node = diagnostics.CASES["sql-guard"]
    value.update(case="sql-guard", mode="cpu", node=node, phase="call")
    report = value["reports"]["core-results.json"]
    report.update(
        executed=[node],
        cpu_profile=measured,
        diagnostic_measurement={"case": "sql-guard", "mode": "cpu", "node": node, "phase": "call"},
    )
    assert diagnostics.accepted(value), json.dumps(measured, sort_keys=True)
    if damage == "bool_count":
        measured["buckets"]["global_parse"]["calls"] = True
    elif damage == "negative_clock":
        measured["body"]["wall_ns"] = -1
    elif damage == "missing_partition":
        measured["buckets"]["partition_parse"]["calls"] = 0
    elif damage == "false_owner":
        measured["owner_unchanged"] = False
    elif damage == "truncated":
        measured["errors"] = ["count_limit"]
    else:
        measured.clear()
    measured["quality_pass"] = True
    assert not diagnostics.accepted(value)


def test_sql_guard_nested_original_execution_is_preserved_but_not_attributed(sql_guard_module):
    module = sql_guard_module
    search = module.TABLES.search
    entered = False

    def reenter_once(value):
        nonlocal entered
        if not entered:
            entered = True
            _run_sql_guard(module)
        return search(value)

    module.TABLES = SimpleNamespace(search=reenter_once)
    with diagnostics._guard_body() as result:
        _run_sql_guard(module)
    assert entered and result["completed"] and result["hooks_restored"]
    assert result["nested_calls"] > 0 and "nested_call" in result["errors"]
    assert not result["quality_pass"]


def test_sql_guard_partial_install_failure_restores_original_parser(sql_guard_module):
    import ast
    import types

    original_parse, original_naming = ast.parse, sql_guard_module._naming
    error = RuntimeError("fixture installation refusal")

    class RefusingModule(types.ModuleType):
        def __setattr__(self, key, value):
            if key == "_naming" and value is not original_naming:
                raise error
            super().__setattr__(key, value)

    sql_guard_module.__class__ = RefusingModule
    with pytest.raises(RuntimeError) as raised:
        with diagnostics._guard_body():
            pytest.fail("body entered after partial installation")
    assert raised.value is error
    assert ast.parse is original_parse and sql_guard_module._naming is original_naming


def test_sql_guard_binds_the_original_optimized_direct_caller(sql_guard_module):
    module, parser, naming, code = diagnostics._guard_bindings()
    assert module is sql_guard_module and naming is module._naming
    assert parser is module.ast
    assert code is module.test_only_allowlisted_modules_name_the_row_tables_in_sql.__code__


@pytest.mark.parametrize("primary_type", [ValueError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("observer_type", [KeyboardInterrupt, SystemExit])
def test_sql_guard_helper_primary_survives_final_clock_fatal_by_identity(
    sql_guard_module, monkeypatch, primary_type, observer_type
):
    import ast

    module = sql_guard_module
    original_parse, original_naming = ast.parse, module._naming
    native_cpu = diagnostics._GUARD_CPU
    primary, observer = primary_type("private-primary"), observer_type("private-observer")
    armed = False

    def fail_original(_value):
        nonlocal armed
        armed = True
        raise primary

    def clock():
        nonlocal armed
        if armed:
            armed = False
            raise observer
        return native_cpu()

    module.TABLES = SimpleNamespace(search=fail_original)
    monkeypatch.setattr(diagnostics, "_GUARD_CPU", clock)
    with pytest.raises(primary_type) as caught:
        with diagnostics._guard_body() as result:
            _run_sql_guard(module)
    assert caught.value is primary
    assert ast.parse is original_parse and module._naming is original_naming
    assert result["hooks_restored"] and not result["quality_pass"]
    assert "clock_error" in result["errors"]
    assert "private-primary" not in json.dumps(result) and "private-observer" not in json.dumps(
        result
    )


@pytest.mark.parametrize("primary_type", [AssertionError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("observer_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("callback", ["clock", "thread"])
def test_sql_guard_body_primary_survives_final_observer_fatal_by_identity(
    sql_guard_module, monkeypatch, primary_type, observer_type, callback
):
    import ast

    module = sql_guard_module
    original_parse, original_naming = ast.parse, module._naming
    attribute = "_GUARD_CPU" if callback == "clock" else "_GUARD_THREAD"
    native = getattr(diagnostics, attribute)
    primary, observer = primary_type("private-primary"), observer_type("private-observer")
    armed = False

    def final_callback():
        nonlocal armed
        if armed:
            armed = False
            raise observer
        return native()

    monkeypatch.setattr(diagnostics, attribute, final_callback)
    with pytest.raises(primary_type) as caught:
        with diagnostics._guard_body() as result:
            _run_sql_guard(module)
            armed = True
            raise primary
    assert caught.value is primary
    assert ast.parse is original_parse and module._naming is original_naming
    assert result["hooks_restored"] and not result["quality_pass"]
    assert ("clock_error" if callback == "clock" else "thread_error") in result["errors"]
    assert "private-primary" not in json.dumps(result) and "private-observer" not in json.dumps(
        result
    )


@pytest.mark.parametrize("observer_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("boundary", ["helper_clock", "body_clock", "body_thread"])
def test_sql_guard_observer_fatal_without_primary_propagates_after_restoration(
    sql_guard_module, monkeypatch, observer_type, boundary
):
    import ast

    module = sql_guard_module
    original_parse, original_naming = ast.parse, module._naming
    attribute = "_GUARD_THREAD" if boundary == "body_thread" else "_GUARD_CPU"
    native = getattr(diagnostics, attribute)
    observer = observer_type("private-observer")
    armed = False
    if boundary == "helper_clock":
        search = module.TABLES.search

        def ordinary_original(value):
            nonlocal armed
            result = search(value)
            armed = True
            return result

        module.TABLES = SimpleNamespace(search=ordinary_original)

    def final_callback():
        nonlocal armed
        if armed:
            armed = False
            raise observer
        return native()

    monkeypatch.setattr(diagnostics, attribute, final_callback)
    with pytest.raises(observer_type) as caught:
        with diagnostics._guard_body() as result:
            _run_sql_guard(module)
            armed = True
    assert caught.value is observer
    assert ast.parse is original_parse and module._naming is original_naming
    assert result["hooks_restored"] and not result["quality_pass"]
    assert "private-observer" not in json.dumps(result)


@pytest.mark.parametrize("edge", ["initial_finish", "selection_start"])
def test_sql_guard_phase_edges_bracket_clock_read_work(sql_guard_module, monkeypatch, edge):
    import ast

    module = sql_guard_module
    original_parse, original_read = ast.parse, Path.read_text
    state = {"time": 0, "delay": False, "partition_reads": 0}

    def read(path, *args, **kwargs):
        text = original_read(path, *args, **kwargs)
        if path == module.SRC / "build" / "partitions.py":
            state["partition_reads"] += 1
            if edge == "initial_finish" and state["partition_reads"] == 2:
                state["delay"] = True
        return text

    def parse(*args, **kwargs):
        tree = original_parse(*args, **kwargs)
        if edge == "selection_start" and not kwargs:
            state["delay"] = True
        return tree

    def clock(kind):
        delayed = state["delay"] and kind == ("cpu" if edge == "initial_finish" else "wall")
        state["time"] += 1000 if delayed else 100
        if delayed:
            state["delay"] = False
        return state["time"]

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(ast, "parse", parse)
    monkeypatch.setattr(diagnostics, "_GUARD_WALL", lambda: clock("wall"))
    monkeypatch.setattr(diagnostics, "_GUARD_CPU", lambda: clock("cpu"))
    with diagnostics._guard_body() as result:
        _run_sql_guard(module)
    assert result["quality_pass"] and diagnostics.guard_quality(result), json.dumps(result)
    assert result["buckets"]["partition_parse"]["calls"] == 1
    for key in ("wall_ns", "owner_thread_cpu_ns"):
        assert (
            result["initial_scan_to_partition_parse"][key]
            + result["buckets"]["partition_parse"][key]
            + result["function_selection_and_assertion"][key]
            < result["body"][key]
        ), "separate endpoint reads leave a conservative observer-only gap"


@pytest.mark.parametrize("observer_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize(
    "edge,primary_type",
    [
        ("initial_finish", None),
        ("selection_start", None),
        ("selection_start", ValueError),
        ("selection_start", KeyboardInterrupt),
        ("selection_start", SystemExit),
    ],
)
def test_sql_guard_partition_endpoint_fatals_preserve_exception_precedence(
    sql_guard_module, monkeypatch, observer_type, edge, primary_type
):
    import ast

    module = sql_guard_module
    original_parse, original_read, original_naming = ast.parse, Path.read_text, module._naming
    native_cpu = diagnostics._GUARD_CPU
    primary = primary_type("private-primary") if primary_type else None
    observer = observer_type("private-observer")
    state = {"remaining": 0, "reads": 0}

    def read(path, *args, **kwargs):
        text = original_read(path, *args, **kwargs)
        if path == module.SRC / "build" / "partitions.py":
            state["reads"] += 1
            if edge == "initial_finish" and state["reads"] == 2:
                state["remaining"] = 1
        return text

    def parse(*args, **kwargs):
        tree = original_parse(*args, **kwargs)
        if edge == "selection_start" and not kwargs:
            state["remaining"] = 2  # Parse FINISH, then separate selection START.
            if primary is not None:
                raise primary
        return tree

    def clock():
        if state["remaining"]:
            state["remaining"] -= 1
            if state["remaining"] == 0:
                raise observer
        return native_cpu()

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(ast, "parse", parse)
    monkeypatch.setattr(diagnostics, "_GUARD_CPU", clock)
    expected = primary if primary is not None else observer
    with pytest.raises(type(expected)) as caught:
        with diagnostics._guard_body() as result:
            _run_sql_guard(module)
    assert caught.value is expected
    assert ast.parse is parse and module._naming is original_naming
    assert result["hooks_restored"] and not result["quality_pass"]
    assert "private-primary" not in json.dumps(result) and "private-observer" not in json.dumps(
        result
    )
