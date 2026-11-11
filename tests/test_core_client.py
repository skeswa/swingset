"""Failure export retains private regular bytes without following source links."""

import os

from swingset.dev.core_client import export_failures, pytest_command


def test_prologue_effective_command_has_no_workers_or_arbitrary_selector():
    command = pytest_command("prologue")
    assert "--swingset-core-phase=prologue" in command
    assert "-n" not in command and not any("partition=" in value for value in command)
    assert not any("::" in value for value in command)
    parallel = pytest_command("0/4")
    assert parallel[parallel.index("-n") + 1] == "4"
    assert "--swingset-core-phase=parallel" in parallel
    assert "--swingset-partition=0/4" in parallel


def test_failed_payload_and_capacity_export_preserve_source_symlinks_and_special_files(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"source unchanged")
    pytest_root = tmp_path / "pytest-of-swingset"
    case = pytest_root / "test_failure"
    case.mkdir(parents=True)
    (case / "body").write_bytes(b"failed body")
    (case / "source-link").symlink_to(source)
    os.mkfifo(case / "fifo")
    capacity = tmp_path / "swingset-capacity-clean-owned"
    payload = capacity / "payload" / "case"
    payload.mkdir(parents=True)
    (payload / "retained").write_bytes(b"capacity failure")
    (payload / "source-link").symlink_to(source)
    result = export_failures(tmp_path)
    assert result["complete"]
    assert result["files"] == 1
    assert result["special"] == ["test_failure/fifo"]
    exported = tmp_path / "failure-artifacts" / "test_failure"
    assert (exported / "body").read_bytes() == b"failed body"
    assert (exported / "source-link").is_symlink()
    assert not (exported / "fifo").exists()
    assert result["persistent_capacity"][0]["directory"] == capacity.name
    assert (payload / "retained").read_bytes() == b"capacity failure"
    assert source.read_bytes() == b"source unchanged"


def test_persistent_capacity_export_does_not_require_a_ram_fixture_directory(tmp_path):
    capacity = tmp_path / "swingset-capacity-owned"
    capacity.mkdir()
    (capacity / "retained").write_bytes(b"failure")
    result = export_failures(tmp_path)
    assert result["complete"]
    assert result["persistent_capacity"] == [
        {"directory": capacity.name, "files": 1, "bytes": 7, "special": []}
    ]
    assert not (tmp_path / "failure-artifacts").exists()


def _run_phase_receipt(
    tmp_path,
    cases,
    *,
    workers=0,
    test_file="test_cases.py",
    extra_conftest="",
    options=(),
    companion=False,
):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from swingset.dev import core_receipt

    receipt = tmp_path / "result.json"
    (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\nfrom swingset.dev import core_receipt\n"
        f"core_receipt.Path = lambda ignored: Path({str(receipt)!r})\n" + extra_conftest
    )
    path = tmp_path / test_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cases)
    if companion:
        (tmp_path / "test_other.py").write_text("def test_other():\n    pass\n")
    environment = dict(os.environ)
    for name in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "SWINGSET_TEST_TIMINGS_PATH",
        "SWINGSET_TEST_STARTED",
        "SWINGSET_TEST_PARTITION_REPORT_DIR",
        "SWINGSET_CORE_PARTITION_WEIGHTS",
    ):
        environment.pop(name, None)
    source = Path(core_receipt.__file__).resolve().parents[2]
    environment["PYTHONPATH"] = os.pathsep.join((str(source), str(source.parent / "tests")))
    environment["PYTEST_XDIST_WORKER"] = "gw999"  # An inherited label is not execution ownership.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "swingset.dev.core_receipt",
            *(["-n", str(workers)] if workers else []),
            *options,
            test_file,
            *(["test_other.py"] if companion else []),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result, json.loads(receipt.read_text())


def test_phase_receipt_survives_actual_two_worker_serialization_with_execution_owners(tmp_path):
    cases = "from pathlib import Path\n"
    for number in range(4):
        cases += (
            f"def test_case_{number}(request):\n"
            f"    Path('owner-{number}').write_text(request.config.workerinput['workerid'])\n"
            "    assert sum(range(1000)) == 499500\n"
        )
    result, receipt = _run_phase_receipt(tmp_path, cases, workers=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["counts"]["passed"] == 4
    observed = receipt["phase_observations"]
    assert observed["quality"], observed
    assert len(observed["records"]) == 12 and not observed["unobserved"]
    assert {row["worker"] for row in observed["records"]} == {"gw0", "gw1"}
    for number in range(4):
        owner = (tmp_path / f"owner-{number}").read_text()
        phases = [
            row for row in observed["records"] if row["nodeid"].endswith(f"test_case_{number}")
        ]
        assert {row["phase"] for row in phases} == {"setup", "call", "teardown"}
        assert {row["worker"] for row in phases} == {owner}
        assert set(receipt["node_phases"][f"test_cases.py::test_case_{number}"]) == {
            "setup",
            "call",
            "teardown",
        }
        assert all(
            0 <= row["owner_thread_cpu_ns"] <= row["end_ns"] - row["start_ns"] for row in phases
        )


def test_phase_receipt_native_clocks_survive_test_clock_patches(monkeypatch):
    import time
    from types import SimpleNamespace

    import pytest

    from swingset.dev import core_receipt

    item = SimpleNamespace(
        config=SimpleNamespace(workerinput={"workerid": "gw2"}), stash=pytest.Stash()
    )
    with monkeypatch.context() as clocks:
        clocks.setattr(time, "monotonic_ns", lambda: -100)
        clocks.setattr(time, "thread_time_ns", lambda: -100)
        with core_receipt._observe(item, "call"):
            assert sum(range(1000)) == 499500
    value = item.stash[core_receipt._PENDING]["call"]
    assert value["worker"] == "gw2" and value["start_ns"] > 0
    assert 0 <= value["owner_thread_cpu_ns"] <= value["end_ns"] - value["start_ns"]
    recorder = core_receipt._Recorder(item.config)
    recorder.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="case", when="call", user_properties=[(core_receipt._PROPERTY, value)]
        )
    )
    assert recorder.report()["quality"]


def test_nested_pytest_does_not_mix_observation_sessions(tmp_path):
    (tmp_path / "inner").mkdir()
    (tmp_path / "inner/test_inner.py").write_text("def test_inner():\n    pass\n")
    result, receipt = _run_phase_receipt(
        tmp_path,
        "import pytest\ndef test_outer():\n"
        "    assert pytest.main(['-q', '-p', 'swingset.dev.core_receipt', 'inner/test_inner.py']) == 0\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = receipt["phase_observations"]
    assert observed["quality"], observed
    assert len(observed["records"]) == 3
    assert {row["nodeid"] for row in observed["records"]} == {"test_cases.py::test_outer"}
    assert {row["worker"] for row in observed["records"]} == {"controller"}


def test_failed_test_outcome_remains_failed_with_complete_phase_observations(tmp_path):
    result, receipt = _run_phase_receipt(tmp_path, "def test_failure():\n    assert False\n")
    assert result.returncode == 1 and receipt["counts"]["failed"] == 1
    assert receipt["executed"] == ["test_cases.py::test_failure"]
    assert receipt["phase_observations"]["quality"]
    assert {row["phase"] for row in receipt["phase_observations"]["records"]} == {
        "setup",
        "call",
        "teardown",
    }


def test_phase_quality_refuses_impossible_costs_overlap_and_missing_crash_observations(monkeypatch):
    from types import SimpleNamespace

    from swingset.dev import core_receipt

    config = SimpleNamespace()

    def report(value=None, *, phase="call"):
        return SimpleNamespace(
            nodeid="case",
            when=phase,
            user_properties=[] if value is None else [(core_receipt._PROPERTY, value)],
        )

    value = {
        "phase": "call",
        "worker": "controller",
        "start_ns": 10,
        "end_ns": 20,
        "owner_thread_cpu_ns": 3,
        "owner_thread_id": 1,
        "owner_thread_unchanged": True,
    }
    for altered, expected in [
        ({"end_ns": 5}, "negative_duration"),
        ({"owner_thread_cpu_ns": -1}, "negative_duration"),
        ({"owner_thread_cpu_ns": 11}, "thread_cpu_exceeds_wall"),
        ({"owner_thread_unchanged": False}, "owner_thread_changed"),
        ({"worker": "gw9"}, "executing_worker_mismatch"),
    ]:
        recorder = core_receipt._Recorder(config)
        recorder.pytest_runtest_logreport(report({**value, **altered}))
        observed = recorder.report()
        assert not observed["quality"] and expected in observed["errors"]
    recorder = core_receipt._Recorder(config)
    recorder.pytest_runtest_logreport(report(value))
    recorder.pytest_runtest_logreport(report({**value, "start_ns": 15, "end_ns": 25}))
    assert "overlapping_worker_phases" in recorder.report()["errors"]
    recorder.pytest_runtest_logreport(report(phase="???"))
    assert recorder.report()["unobserved"] == [
        {"nodeid": "case", "phase": "???", "worker": "controller"}
    ]
    monkeypatch.setattr(core_receipt, "_LIMIT", 1)
    limited = core_receipt._Recorder(config)
    limited.pytest_runtest_logreport(report(value))
    limited.pytest_runtest_logreport(report({**value, "start_ns": 30, "end_ns": 40}))
    assert not limited.report()["quality"] and limited.report()["dropped"] == 1
    assert len(limited.report()["records"]) == 1


def test_phase_cpu_observation_excludes_a_busy_helper_thread():
    import threading
    import time
    from types import SimpleNamespace

    import pytest

    from swingset.dev import core_receipt

    item = SimpleNamespace(config=SimpleNamespace(), stash=pytest.Stash())

    def helper():
        started = time.thread_time_ns()
        while time.thread_time_ns() - started < 20_000_000:
            pass

    with core_receipt._observe(item, "call"):
        worker = threading.Thread(target=helper)
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive()
    value = item.stash[core_receipt._PENDING]["call"]
    assert value["end_ns"] - value["start_ns"] >= 20_000_000
    assert value["owner_thread_cpu_ns"] < (value["end_ns"] - value["start_ns"]) / 2
    assert value["owner_thread_id"] == threading.get_ident()


def test_observation_clock_failure_does_not_replace_the_original_test_error(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from swingset.dev import core_receipt

    item = SimpleNamespace(config=SimpleNamespace(), stash=pytest.Stash())

    def unavailable():
        raise OSError("clock unavailable")

    native_clock = core_receipt._THREAD_CPU_NS
    with monkeypatch.context() as clocks:
        clocks.setattr(core_receipt, "_THREAD_CPU_NS", unavailable)
        with pytest.raises(ValueError, match="original test failure"):
            with core_receipt._observe(item, "call"):
                raise ValueError("original test failure")
    assert core_receipt._THREAD_CPU_NS is native_clock
    value = item.stash[core_receipt._PENDING]["call"]
    recorder = core_receipt._Recorder(item.config)
    recorder.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="failed-case", when="call", user_properties=[(core_receipt._PROPERTY, value)]
        )
    )
    report = recorder.report()
    assert not report["quality"] and report["unobserved"][0]["nodeid"] == "failed-case"
    assert not report["records"]


def test_observation_transport_does_not_export_unreviewed_report_payloads():
    import json
    from types import SimpleNamespace

    from swingset.dev import core_receipt

    recorder = core_receipt._Recorder(SimpleNamespace())
    recorder.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="case",
            when="call",
            user_properties=[(core_receipt._PROPERTY, {"sql": "private unreviewed payload"})],
        )
    )
    report = recorder.report()
    assert not report["quality"] and report["records"] == []
    assert "private unreviewed payload" not in json.dumps(report)


def _fake_restore_bindings(owner_type=None):
    from types import SimpleNamespace

    from swingset.dev import restore_observer

    class Driver:
        def wait(self, *args, **kwargs):
            if args == ("nested",):
                return self.wait("inner")
            return "original driver result"

    driver, owner, publishes = Driver(), (owner_type or SimpleNamespace)(), []

    def row_digest(*args, **kwargs):
        assert driver.wait(*args, **kwargs) == "original driver result"
        return "original digest result"

    def export(*args, **kwargs):
        for _ in range(282):
            assert owner._row_digest(*args, **kwargs) == "original digest result"
        return "original export result"

    owner._row_digest, owner.export = row_digest, export

    def runtime():
        assert driver.wait("runtime") == "original driver result"
        return "original runtime result"

    owner.capture_runtime = runtime

    def make(label):
        def function(*args, **kwargs):
            assert driver.wait(*args, **kwargs) == "original driver result"
            if label == "inputs.capture":
                from concurrent.futures import Future
                from pathlib import Path

                assert owner.capture_runtime() == "original runtime result"
                future = Future()
                future.set_result("original future result")
                assert future.result() == "original future result"
                assert Path(".").exists()
            if label == "snapshot.capture":
                assert owner.export(*args, **kwargs) == "original export result"
            if label == "restore_checkpoint":
                owner.history_audit("nested")
            if label == "publish":
                publishes.append(True)
                if len(publishes) == 2:
                    raise ConnectionError("private expected unlanded publication")
            return "original function result"

        return function

    bindings = []
    for label in restore_observer.LABELS:
        name = label.replace(".", "_")
        setattr(owner, name, make(label))
        bindings.append((owner, name, label))
    return owner, bindings, Driver


def _exercise_restore_bindings(owner, variant):
    from swingset.dev import restore_observer

    for label in restore_observer.LABELS:
        assert (
            getattr(owner, label.replace(".", "_"))(
                "private argument", parameter="private parameter"
            )
            == "original function result"
        )
    for _ in range(2 if variant else 1):
        assert owner.build_release() == "original function result"
    if variant:
        try:
            owner.publish()
        except ConnectionError as error:
            assert str(error) == "private expected unlanded publication"
        else:
            raise AssertionError("expected publication failure was masked")


def _fake_checkpoint_bindings(module=None):
    from types import ModuleType

    from swingset.dev import restore_observer

    class Driver:
        def wait(self, *args, **kwargs):
            return "unchanged checkpoint result"

    owner, driver = module or ModuleType("checkpoint_fixture"), Driver()

    def original(*args, **kwargs):
        return driver.wait(*args, **kwargs)

    def creating(*args, **kwargs):
        result = original(*args, **kwargs)
        assert owner.snapshot_capture(*args, **kwargs) == result
        assert owner.backup_check(*args, **kwargs) == result
        return result

    bindings = []
    for label in restore_observer.CHECKPOINT_LABELS + restore_observer.CHECKPOINT_CHILD_LABELS:
        name = "state" if label == "checkpoint.state" else label.replace(".", "_")
        setattr(owner, name, creating if label == "create_checkpoint" else original)
        bindings.append((owner, name, label))
    return owner, bindings, Driver


def _exercise_checkpoint_bindings(owner):
    from swingset.dev import restore_observer

    for label in restore_observer.CHECKPOINT_LABELS:
        name = "state" if label == "checkpoint.state" else label
        assert getattr(owner, name)("private argument") == "unchanged checkpoint result"


def test_checkpoint_major_phases_have_real_singleton_xdist_transport(tmp_path):
    from swingset.dev import restore_observer

    extra = (
        "from test_core_client import _fake_checkpoint_bindings\n"
        "from swingset.dev import restore_observer\n"
        "OWNER = None\n"
        "def bindings(module):\n"
        "    global OWNER\n"
        "    OWNER, observed, driver = _fake_checkpoint_bindings(module)\n"
        "    return observed, driver\n"
        "restore_observer._checkpoint_bindings = bindings\n"
    )
    cases = (
        "import conftest\n"
        "def test_restore_public_verification_keeps_activation_explicit():\n"
        "    for name in ('state', 'create_checkpoint', 'restore_checkpoint', 'verify_restored_public', 'activate_restored_state'):\n"
        "        assert getattr(conftest.OWNER, name)('private argument') == 'unchanged checkpoint result'\n"
    )
    result, receipt = _run_phase_receipt(
        tmp_path,
        cases,
        workers=2,
        test_file="tests/backup/test_checkpoint.py",
        extra_conftest=extra,
        companion=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = receipt["restore_call_observations"]
    assert receipt["counts"]["passed"] == 2 and observed["quality"], observed
    assert len(observed["cases"]) == 1
    case = observed["cases"][0]
    assert case["nodeid"] == restore_observer.CHECKPOINT and case["variant"] == "checkpoint"
    assert set(case["coverage"]) == set(
        restore_observer.CHECKPOINT_LABELS + restore_observer.CHECKPOINT_CHILD_LABELS
    )
    assert [row["label"] for row in case["records"] if row["parent"] is None] == list(
        restore_observer.CHECKPOINT_LABELS
    )
    created = next(row for row in case["records"] if row["label"] == "create_checkpoint")
    children = [row for row in case["records"] if row["parent"] is not None]
    assert [row["label"] for row in children] == list(restore_observer.CHECKPOINT_CHILD_LABELS)
    assert all(row["parent"] == created["index"] and row["depth"] == 1 for row in children)
    assert all(row["digest_scans"] is None for row in case["records"])
    assert case["worker"].startswith("gw") and case["hooks_restored"]
    assert case["body"]["wait_calls"] == 7 and "private" not in str(observed)


def test_checkpoint_adapter_checks_original_aliases_and_restores_on_error(monkeypatch):
    from types import ModuleType

    import pytest

    from swingset.backup import checkpoint
    from swingset.dev import restore_observer

    owner = ModuleType("checkpoint_fixture")
    owner.state = lambda: None
    for name in restore_observer.CHECKPOINT_LABELS[1:]:
        setattr(owner, name, getattr(checkpoint, name))
    bindings, driver = restore_observer._checkpoint_bindings(owner)
    originals = [(target, name, getattr(target, name)) for target, name, _ in bindings]
    wait, error = driver.wait, KeyboardInterrupt("private original failure")
    with pytest.raises(KeyboardInterrupt) as caught:
        with restore_observer.observe("controller", restore_observer.CHECKPOINT, owner) as payload:
            raise error
    assert caught.value is error and not payload["quality"] and payload["hooks_restored"]
    assert driver.wait is wait and all(
        getattr(target, name) is original for target, name, original in originals
    )
    assert "private" not in str(payload)
    monkeypatch.setattr(owner, "restore_checkpoint", lambda: None)
    with pytest.raises(ValueError, match="alias differs"):
        restore_observer._checkpoint_bindings(owner)
    with pytest.raises(KeyboardInterrupt) as caught:
        with restore_observer.observe("controller", restore_observer.CHECKPOINT, owner) as drift:
            raise error
    assert caught.value is error and "binding_error" in drift["errors"]
    assert not drift["quality"] and drift["hooks_restored"] and driver.wait is wait
    body = []
    with restore_observer.observe("controller", restore_observer.CHECKPOINT) as missing:
        body.append("unchanged body result")
    assert body == ["unchanged body result"]
    assert not missing["quality"] and "binding_error" in missing["errors"]


def test_checkpoint_quality_refuses_missing_foreign_or_reordered_major_calls(monkeypatch):
    import copy
    import threading

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_checkpoint_bindings()
    monkeypatch.setattr(restore_observer, "_checkpoint_bindings", lambda module: (bindings, driver))
    with restore_observer.observe("controller", restore_observer.CHECKPOINT, owner) as payload:
        assert owner.snapshot_capture() == owner.backup_check() == "unchanged checkpoint result"
        _exercise_checkpoint_bindings(owner)
    assert payload["quality"] and restore_observer.safe(payload)
    assert len(payload["records"]) == 7 and payload["body"]["wait_calls"] == 9
    for mutation in ("missing", "foreign", "order", "missing_child", "wrong_parent"):
        changed = copy.deepcopy(payload)
        if mutation == "missing":
            changed["coverage"]["activate_restored_state"] = 0
        elif mutation == "foreign":
            changed["foreign_function_calls"]["create_checkpoint"] = 1
        elif mutation == "missing_child":
            changed["coverage"]["backup.check"] = 0
        elif mutation == "wrong_parent":
            next(row for row in changed["records"] if row["label"] == "backup.check")["parent"] = 0
        else:
            changed["records"][0]["label"], changed["records"][1]["label"] = (
                changed["records"][1]["label"],
                changed["records"][0]["label"],
            )
        assert not restore_observer.quality(changed)
    with restore_observer.observe("controller", restore_observer.CHECKPOINT, owner) as foreign:
        worker = threading.Thread(target=owner.state)
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive()
        _exercise_checkpoint_bindings(owner)
    assert foreign["foreign_function_calls"]["checkpoint.state"] == 1
    assert not foreign["quality"] and foreign["hooks_restored"]


def test_checkpoint_child_failure_keeps_identity_and_restores_every_hook(monkeypatch):
    import pytest

    from swingset.dev import restore_observer

    for kind in (RuntimeError, KeyboardInterrupt):
        owner, bindings, driver = _fake_checkpoint_bindings()
        failure = kind("private child failure")

        def broken(*args, failure=failure, **kwargs):
            raise failure

        owner.snapshot_capture = broken
        originals = [(target, name, getattr(target, name)) for target, name, _ in bindings]
        wait = driver.wait
        with monkeypatch.context() as scoped:
            scoped.setattr(
                restore_observer,
                "_checkpoint_bindings",
                lambda module, bindings=bindings, driver=driver: (bindings, driver),
            )
            with pytest.raises(kind) as caught:
                with restore_observer.observe(
                    "controller", restore_observer.CHECKPOINT, owner
                ) as payload:
                    _exercise_checkpoint_bindings(owner)
        assert caught.value is failure and not payload["quality"] and payload["hooks_restored"]
        child = next(row for row in payload["records"] if row["label"] == "snapshot.capture")
        assert payload["records"][child["parent"]]["label"] == "create_checkpoint"
        assert child["exception_type"] == kind.__name__ and child["digest_scans"] is None
        assert all(getattr(target, name) is original for target, name, original in originals)
        assert driver.wait is wait and "private" not in str(payload)


def test_restore_subphases_have_real_xdist_call_only_transport_and_variant_ownership(tmp_path):
    from swingset.dev import restore_observer

    extra = (
        "from test_core_client import _fake_restore_bindings\n"
        "from swingset.dev import restore_observer\n"
        "OWNER = None\n"
        "def bindings():\n"
        "    global OWNER\n"
        "    OWNER, observed, driver = _fake_restore_bindings()\n"
        "    return observed, driver\n"
        "restore_observer._bindings = bindings\n"
    )
    cases = (
        "from pathlib import Path\nimport pytest\nimport conftest\n"
        "from test_core_client import _exercise_restore_bindings\n"
        "@pytest.mark.parametrize('unpublished_change', [False, True])\n"
        "def test_empty_restore_rebuild_preserves_public_bytes_and_unpublished_generation_receipt(request, unpublished_change):\n"
        "    Path('owner-' + str(unpublished_change)).write_text(request.config.workerinput['workerid'])\n"
        "    _exercise_restore_bindings(conftest.OWNER, unpublished_change)\n"
    )
    result, receipt = _run_phase_receipt(
        tmp_path,
        cases,
        workers=2,
        test_file="tests/test_postgres_rebuild.py",
        extra_conftest=extra,
        options=("--dist=loadfile",),
        companion=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["counts"]["passed"] == 3 and receipt["phase_observations"]["quality"]
    observed = receipt["restore_call_observations"]
    assert observed["applicable"] and observed["quality"], observed
    assert len(observed["cases"]) == 2
    for case in observed["cases"]:
        assert case["nodeid"] == restore_observer.BASE + "[" + case["variant"] + "]"
        assert case["worker"] == (tmp_path / ("owner-" + case["variant"])).read_text()
        assert case["hooks_restored"] and len(case["records"]) <= 64
        assert set(case["coverage"]) == set(restore_observer.LABELS)
        assert case["nested_owner_wait_calls"] >= 1
        assert any(row["parent"] is not None and row["depth"] > 0 for row in case["records"])
        assert case["body"]["wait_calls"] > 0
        inputs = [
            row["input_capture"] for row in case["records"] if row["label"] == "inputs.capture"
        ]
        assert len(inputs) == 1
        for capture in inputs:
            assert capture["capture_completed"]
            for name in ("runtime", "future_result", "path_exists"):
                assert capture[name]["calls"] == capture[name]["normal_returns"] == 1
                assert capture[name]["exceptions"] == 0
            assert capture["runtime"]["wait_calls"] == 1
        snapshots = [row for row in case["records"] if row["label"] == "snapshot.capture"]
        assert len(snapshots) == 1
        scans = snapshots[0]["digest_scans"]
        assert scans["normal_returns"] == 282 and scans["pairs"] == 141
        assert scans["snapshot_completed"] and scans["export_normal_returns"] == 1
        assert scans["physical"]["wait_calls"] == scans["immutable"]["wait_calls"] == 141
        assert all(row["digest_scans"] is None for row in case["records"] if row not in snapshots)
    import json

    assert "private" not in json.dumps(observed)
    assert (
        next(case for case in observed["cases"] if case["variant"] == "True")["records"][-1][
            "exception_type"
        ]
        == "ConnectionError"
    )


def test_restore_observer_clock_fault_preserves_original_exception_and_all_hooks(monkeypatch):
    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    originals = [(target, name, getattr(target, name)) for target, name, _ in bindings]
    original_wait = driver.wait
    with monkeypatch.context() as scoped:
        scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
        scoped.setattr(
            restore_observer, "_WALL", lambda: (_ for _ in ()).throw(OSError("clock unavailable"))
        )
        import pytest

        for exception in (
            AssertionError("original assertion"),
            KeyboardInterrupt("original interrupt"),
        ):
            with pytest.raises(type(exception)) as caught:
                with restore_observer.observe(
                    "controller", restore_observer.BASE + "[False]"
                ) as payload:
                    owner.schema_migrate()
                    raise exception
            assert caught.value is exception
            assert not payload["quality"] and "clock_error" in payload["errors"]
            assert payload["hooks_restored"]
            assert all(getattr(target, name) is original for target, name, original in originals)
            assert driver.wait is original_wait


def test_restore_observer_ownership_drift_does_not_clobber_foreign_hook_or_error(monkeypatch):
    import pytest

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    original_wait = driver.wait

    def foreign():
        return "foreign owner's result"

    original = RuntimeError("original producer error")
    with pytest.raises(RuntimeError) as caught:
        with restore_observer.observe("controller", restore_observer.BASE + "[False]") as payload:
            owner.schema_migrate = foreign
            raise original
    assert caught.value is original and owner.schema_migrate is foreign
    assert driver.wait is original_wait and not payload["hooks_restored"]
    assert not payload["quality"] and "hook_changed" in payload["errors"]


def test_restore_observer_limit_and_foreign_threads_are_explicit_without_changing_calls(
    monkeypatch,
):
    import threading

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    results = []
    with restore_observer.observe("controller", restore_observer.BASE + "[False]") as payload:
        helper = threading.Thread(target=lambda: results.append(owner.schema_migrate()))
        helper.start()
        helper.join(timeout=1)
        assert not helper.is_alive()
        for _ in range(70):
            results.append(owner.schema_migrate())
    assert results == ["original function result"] * 71
    assert payload["foreign_function_calls"]["schema.migrate"] == 1
    assert payload["foreign_wait_calls"] == 1
    assert len(payload["records"]) == 64 and payload["dropped"] == 6
    assert payload["hooks_restored"] and not payload["quality"]


def _restore_fault(monkeypatch, observer, site, fault, original=None):
    from types import SimpleNamespace

    class Owner(SimpleNamespace):
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name == "history_audit" and value is vars(self).get("restore_original"):
                raise fault

    owner, bindings, driver = _fake_restore_bindings(Owner)
    if original is not None:

        def producer(*args, **kwargs):
            raise original

        owner.schema_migrate = producer
    originals = [(target, name, getattr(target, name)) for target, name, _ in bindings]
    wait = driver.wait
    monkeypatch.setattr(observer, "_bindings", lambda: (bindings, driver))
    states = []
    initialize = observer._Observation.__init__

    def initialize_and_keep(self, *args):
        initialize(self, *args)
        states.append(self)

    monkeypatch.setattr(observer._Observation, "__init__", initialize_and_keep)

    def fail(*args):
        raise fault

    if site in {"thread_init", "thread_dispatch"}:
        actual, calls = observer._THREAD, []

        def thread():
            calls.append(True)
            if site == "thread_dispatch" and len(calls) == 1:
                return actual()
            raise OSError("observer thread identity unavailable")

        monkeypatch.setattr(observer, "_THREAD", thread)
    elif site == "finish":
        monkeypatch.setattr(observer._Observation, "finish", fail)
    elif site == "report":
        monkeypatch.setattr(observer._Observation, "report", fail)
    elif site == "restore":
        owner.restore_original = owner.history_audit
    return owner, originals, driver, wait, states


def test_restore_faults_cannot_replace_original_errors_and_restore_remaining_hooks(monkeypatch):
    import pytest

    from swingset.dev import restore_observer
    from swingset.state.engine import CommitUncertain

    for failure in (ValueError, CommitUncertain, KeyboardInterrupt):
        for site in ("thread_init", "thread_dispatch", "finish", "report", "restore"):
            original = failure("private original producer failure")
            with monkeypatch.context() as scoped:
                owner, hooks, driver, wait, states = _restore_fault(
                    scoped,
                    restore_observer,
                    site,
                    KeyboardInterrupt("secondary observer fault"),
                    original,
                )
                with pytest.raises(failure) as caught:
                    with restore_observer.observe(
                        "controller", restore_observer.BASE + "[False]"
                    ) as payload:
                        owner.schema_migrate()
                assert caught.value is original, site
                assert not payload["quality"], site
                assert states[0].parents == [] and states[0].wait_depth == 0
                assert all(getattr(target, name) is function for target, name, function in hooks)
                assert driver.wait is wait


def test_restore_faults_on_success_never_swallow_a_real_interrupt(monkeypatch):
    import pytest

    from swingset.dev import restore_observer

    for failure in (OSError, KeyboardInterrupt):
        for site in ("finish", "report", "restore"):
            fault = failure("private observer fault")
            with monkeypatch.context() as scoped:
                owner, hooks, driver, wait, states = _restore_fault(
                    scoped, restore_observer, site, fault
                )

                def body(owner=owner):
                    with restore_observer.observe(
                        "controller", restore_observer.BASE + "[False]"
                    ) as payload:
                        assert owner.schema_migrate() == "original function result"
                    return payload

                if failure is KeyboardInterrupt:
                    with pytest.raises(KeyboardInterrupt) as caught:
                        body()
                    assert caught.value is fault
                else:
                    assert not body()["quality"]
                assert states[0].parents == [] and states[0].wait_depth == 0
                assert all(getattr(target, name) is function for target, name, function in hooks)
                assert driver.wait is wait


def test_restore_transport_refuses_wrong_scope_missing_coverage_and_unsafe_payload(monkeypatch):
    import copy
    import json
    from types import SimpleNamespace

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    node = restore_observer.BASE + "[False]"
    with restore_observer.observe("gw0", node) as payload:
        _exercise_restore_bindings(owner, False)
    assert payload["quality"]

    def report(value, phase="call"):
        return SimpleNamespace(
            nodeid=node, when=phase, user_properties=[(restore_observer.PROPERTY, value)]
        )

    for field, value in [("unknown", "private unsafe payload"), ("worker", "gw3")]:
        forged = copy.deepcopy(payload)
        forged[field] = value
        collector = restore_observer.Collector()
        collector.accept(report(forged), "gw0")
        observed = collector.report()
        assert not observed["quality"] and observed["cases"] == []
        assert "private unsafe payload" not in json.dumps(observed)
    for mutation in ("waits", "overlap", "coverage"):
        forged = copy.deepcopy(payload)
        if mutation == "waits":
            forged["body"]["wait_cpu_ns"] = (
                forged["body"]["end_ns"] - forged["body"]["start_ns"] + 1
            )
        elif mutation == "coverage":
            forged["coverage"]["inputs.capture"] = 0
        else:
            forged["records"][1]["start_ns"] = forged["records"][0]["start_ns"]
        assert not restore_observer.quality(forged)
        collector = restore_observer.Collector()
        collector.accept(report(forged), "gw0")
        assert not collector.report()["quality"] and collector.report()["cases"] == []
    collector = restore_observer.Collector()
    collector.accept(report(payload, phase="setup"), "gw0")
    assert "non_call_payload" in collector.report()["errors"]
    collector = restore_observer.Collector()
    collector.accept(report(payload), "gw0")
    assert "incomplete_variant_pair" in collector.report()["errors"]
    collector.accept(report(payload), "gw0")
    assert "missing_or_duplicate_case_payload" in collector.report()["errors"]


def test_restore_thread_identity_interrupt_remains_a_real_interrupt(monkeypatch):
    import pytest

    from swingset.dev import restore_observer

    for boundary in ("init", "dispatch"):
        owner, bindings, driver = _fake_restore_bindings()
        hooks = [(target, name, getattr(target, name)) for target, name, _ in bindings]
        original_wait, actual, calls = driver.wait, restore_observer._THREAD, []
        interrupt = KeyboardInterrupt("actual interrupt during observer work")

        def thread(calls=calls, boundary=boundary, actual=actual, interrupt=interrupt):
            calls.append(True)
            if boundary == "dispatch" and len(calls) == 1:
                return actual()
            raise interrupt

        with monkeypatch.context() as scoped:
            scoped.setattr(
                restore_observer,
                "_bindings",
                lambda bindings=bindings, driver=driver: (bindings, driver),
            )
            scoped.setattr(restore_observer, "_THREAD", thread)
            with pytest.raises(KeyboardInterrupt) as caught:
                with restore_observer.observe("controller", restore_observer.BASE + "[False]"):
                    owner.schema_migrate()
            assert caught.value is interrupt
            assert all(getattr(target, name) is function for target, name, function in hooks)
            assert driver.wait is original_wait


def test_restore_missing_or_generator_binding_remains_unknown_without_changing_calls(monkeypatch):
    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()

    def generator():
        yield "original yielded value"

    owner.inputs_capture = generator
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    with restore_observer.observe("controller", restore_observer.BASE + "[False]") as payload:
        assert list(owner.inputs_capture()) == ["original yielded value"]
        assert owner.schema_migrate() == "original function result"
    assert payload["hooks_restored"] and not payload["quality"]
    assert "unsupported_binding" in payload["errors"]
    assert payload["coverage"]["inputs.capture"] == 0
    assert not any(row["label"] == "inputs.capture" for row in payload["records"])
    assert owner.inputs_capture is generator


def test_restore_fixed_canonical_bindings_are_regular_and_import_safe():
    import inspect

    from swingset.dev import restore_observer

    bindings, driver = restore_observer._bindings()
    assert len(bindings) == len(restore_observer.LABELS)
    assert {label for _, _, label in bindings} == set(restore_observer.LABELS)
    assert len({(id(owner), name) for owner, name, _ in bindings}) == len(bindings)
    assert callable(driver.wait)
    digest_owner = next(owner for owner, _, label in bindings if label == "history.audit")
    functions = [(owner, name) for owner, name, _ in bindings]
    functions.extend((digest_owner, name) for name in ("_row_digest", "export"))
    from concurrent.futures import Future
    from pathlib import Path

    input_owner = next(owner for owner, _, label in bindings if label == "inputs.capture")
    functions.extend(((input_owner, "capture_runtime"), (Future, "result"), (Path, "exists")))
    for owner, name in functions:
        function = getattr(owner, name)
        assert inspect.isfunction(function)
        assert not function.__code__.co_flags & (
            inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
        )


def test_digest_aggregates_require_exact_pairs_one_export_and_normal_snapshot(monkeypatch):
    import pytest

    from swingset.dev import restore_observer

    def exercise(case):
        owner, bindings, driver = _fake_restore_bindings()
        failure = ValueError("private producer error")
        calls = 281 if case == "short" else 283 if case == "extra" else 282

        def export(*args, **kwargs):
            for _ in range(calls):
                assert owner._row_digest(*args, **kwargs) == "original digest result"
            if case == "export_error":
                raise failure
            return "original export result"

        def snapshot(*args, **kwargs):
            for _ in range(2 if case == "twice" else 1):
                assert owner.export(*args, **kwargs) == "original export result"
            if case == "snapshot_error":
                raise failure
            return "original snapshot result"

        owner.export, owner.snapshot_capture = export, snapshot
        original_digest = owner._row_digest
        with monkeypatch.context() as scoped:
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            if case.endswith("error"):
                with pytest.raises(ValueError) as caught:
                    with restore_observer.observe(
                        "gw0", restore_observer.BASE + "[False]"
                    ) as payload:
                        owner.snapshot_capture("private", private="private")
                assert caught.value is failure
            else:
                with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                    assert owner.snapshot_capture() == "original snapshot result"
        scans = payload["records"][0]["digest_scans"]
        assert scans["calls"] == scans["normal_returns"] == calls * (2 if case == "twice" else 1)
        assert scans["pairs"] == scans["normal_returns"] // 2
        assert scans["export_normal_returns"] == (
            0 if case == "export_error" else 2 if case == "twice" else 1
        )
        assert scans["export_exceptions"] == int(case == "export_error")
        assert scans["snapshot_completed"] == (not case.endswith("error"))
        assert not payload["quality"] and "digest_incomplete" in payload["errors"]
        assert payload["hooks_restored"] and owner._row_digest is original_digest
        assert owner.export is export and owner.snapshot_capture is snapshot

    for case in ("short", "extra", "twice", "export_error", "snapshot_error"):
        exercise(case)


def test_digest_callsite_and_nesting_are_not_inferred_from_arguments_or_results(monkeypatch):
    from swingset.dev import restore_observer

    def exercise(case):
        import threading

        owner, bindings, driver = _fake_restore_bindings()
        original_digest, original_snapshot = owner._row_digest, owner.snapshot_capture
        entered = []
        sentinel = object()

        def row_digest(*args, **kwargs):
            assert args == (sentinel,) and kwargs == {"opaque": sentinel}
            if case == "nested" and not entered:
                entered.append(True)
                assert owner._row_digest(*args, **kwargs) is sentinel
            return sentinel

        def indirect(*args, **kwargs):
            return owner._row_digest(*args, **kwargs)

        def export(*args, **kwargs):
            if case == "nested_export" and not entered:
                entered.append(True)
                assert owner.export(*args, **kwargs) is sentinel
            for _ in range(282):
                assert (indirect if case == "indirect" else owner._row_digest)(
                    *args, **kwargs
                ) is sentinel
            return sentinel

        # A conditional-call expression still calls export directly; no arguments
        # or returned sentinels may be read by the observer to classify a role.
        def snapshot(*args, **kwargs):
            if case == "foreign":
                failures = []

                def worker():
                    try:
                        assert owner.export(*args, **kwargs) is sentinel
                    except BaseException as error:
                        failures.append(error)

                thread = threading.Thread(target=worker)
                thread.start()
                thread.join(timeout=1)
                assert not thread.is_alive() and not failures
            elif case == "nested_snapshot" and not entered:
                entered.append(True)
                assert owner.snapshot_capture(*args, **kwargs) is sentinel
            else:
                assert owner.export(*args, **kwargs) is sentinel
            return sentinel

        owner._row_digest, owner.export, owner.snapshot_capture = row_digest, export, snapshot
        with monkeypatch.context() as scoped:
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                assert owner.snapshot_capture(sentinel, opaque=sentinel) is sentinel
        scans = payload["records"][0]["digest_scans"]
        if case == "indirect":
            assert scans["unexpected_callers"] == 282 and scans["normal_returns"] == 282
            assert scans["physical"]["calls"] == scans["immutable"]["calls"] == 0
        elif case == "nested":
            assert scans["nested_calls"] == 1 and scans["unexpected_callers"] == 1
        elif case == "foreign":
            assert scans["foreign_calls"] == 283 and scans["calls"] == 0
        elif case == "nested_export":
            assert scans["export_calls"] == scans["export_normal_returns"] == 2
            assert scans["unexpected_callers"] == 282
        else:
            assert len(payload["records"]) == 2
        assert not payload["quality"] and "digest_scope" in payload["errors"]
        assert payload["hooks_restored"]
        assert owner._row_digest is row_digest and owner.export is export
        assert owner.snapshot_capture is snapshot
        assert original_digest is not row_digest and original_snapshot is not snapshot

    for case in ("indirect", "nested", "foreign", "nested_snapshot", "nested_export"):
        exercise(case)


def test_digest_hooks_ignore_outside_snapshots_and_count_only_consuming_invocations(monkeypatch):
    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    consumed, arguments = [], []
    sentinel = object()

    def stream():
        for _ in range(3):
            consumed.append(True)
            yield sentinel

    def row_digest(rows):
        arguments.append(rows)
        assert list(rows) == [sentinel] * 3
        return sentinel

    def export(*args, **kwargs):
        for _ in range(282):
            assert owner._row_digest(stream()) is sentinel
        return "original export result"

    owner._row_digest, owner.export = row_digest, export
    with monkeypatch.context() as scoped:
        scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
        with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
            assert owner.export() == "original export result"
            before = len(consumed)
            _exercise_restore_bindings(owner, False)
        assert len(arguments) == 564 and len(consumed) == 1692
        assert before == 846
    assert payload["quality"], payload
    scans = next(
        record["digest_scans"]
        for record in payload["records"]
        if record["label"] == "snapshot.capture"
    )
    assert (
        scans["calls"] == 282
        and scans["physical"]["normal_returns"] == scans["immutable"]["normal_returns"] == 141
    )
    assert owner._row_digest is row_digest and owner.export is export


def test_digest_observer_secondary_fault_cannot_replace_original_or_leave_depth(monkeypatch):
    import pytest

    from swingset.dev import restore_observer
    from swingset.state.engine import CommitUncertain

    def exercise(failure_type, site):
        owner, bindings, driver = _fake_restore_bindings()
        original = failure_type("private original failure")
        states = []
        initialize, finish = (
            restore_observer._Observation.__init__,
            restore_observer._Observation.finish,
        )

        def initialize_and_keep(self, *args):
            initialize(self, *args)
            states.append(self)

        def broken_digest(*args, **kwargs):
            raise original

        def finishing(self, before):
            if self.digest_depth:
                raise KeyboardInterrupt("secondary timing failure")
            return finish(self, before)

        def unavailable(*args):
            raise OSError("private unavailable frame")

        owner._row_digest = broken_digest
        original_export = owner.export
        with monkeypatch.context() as scoped:
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            scoped.setattr(restore_observer._Observation, "__init__", initialize_and_keep)
            if site == "finish":
                scoped.setattr(restore_observer._Observation, "finish", finishing)
            else:
                scoped.setattr(restore_observer, "_GETFRAME", unavailable)
            with pytest.raises(failure_type) as caught:
                with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                    owner.snapshot_capture()
        assert caught.value is original
        assert payload["hooks_restored"] and not payload["quality"]
        assert "observer_error" in payload["errors"]
        assert states[0].digest_depth == states[0].export_depth == 0 and states[0].parents == []
        assert owner._row_digest is broken_digest and owner.export is original_export
        scans = payload["records"][0]["digest_scans"]
        assert scans["exceptions"] == scans["export_exceptions"] == 1
        assert scans["normal_returns"] == 0 and not scans["snapshot_completed"]

    for failure_type in (ValueError, CommitUncertain, KeyboardInterrupt):
        for site in ("finish", "caller"):
            exercise(failure_type, site)


def test_digest_finish_fault_on_success_preserves_completion_or_real_interrupt(monkeypatch):
    import pytest

    from swingset.dev import restore_observer

    def exercise(failure_type):
        owner, bindings, driver = _fake_restore_bindings()
        finish, digest, export = (
            restore_observer._Observation.finish,
            owner._row_digest,
            owner.export,
        )
        fault = failure_type("actual fault during digest observation")

        def finishing(self, before):
            if self.digest_depth:
                raise fault
            return finish(self, before)

        with monkeypatch.context() as scoped:
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            scoped.setattr(restore_observer._Observation, "finish", finishing)
            if failure_type is KeyboardInterrupt:
                with pytest.raises(KeyboardInterrupt) as caught:
                    with restore_observer.observe(
                        "gw0", restore_observer.BASE + "[False]"
                    ) as payload:
                        owner.snapshot_capture()
                assert caught.value is fault
            else:
                with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                    _exercise_restore_bindings(owner, False)
                scans = next(
                    row["digest_scans"]
                    for row in payload["records"]
                    if row["label"] == "snapshot.capture"
                )
                assert scans["normal_returns"] == 282 and scans["snapshot_completed"]
        assert not payload["quality"] and "observer_error" in payload["errors"]
        assert payload["hooks_restored"] and owner._row_digest is digest and owner.export is export

    for failure_type in (OSError, KeyboardInterrupt):
        exercise(failure_type)


def test_digest_hook_drift_and_partial_install_restore_only_owned_bindings(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from swingset.dev import restore_observer

    class Owner(SimpleNamespace):
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name == "export" and vars(self).get("reject_export"):
                raise OSError("private partial install")

    def exercise(name):
        owner, bindings, driver = _fake_restore_bindings(Owner)
        digest, export, wait = owner._row_digest, owner.export, driver.wait
        failure = ValueError("original producer error")

        def foreign():
            return "foreign owner"

        owner.reject_export = name == "partial"
        with monkeypatch.context() as scoped:
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            with pytest.raises(ValueError) as caught:
                with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                    if name != "partial":
                        setattr(owner, name, foreign)
                    raise failure
        assert caught.value is failure and not payload["quality"]
        assert owner._row_digest is (foreign if name == "_row_digest" else digest)
        assert owner.export is (foreign if name == "export" else export)
        assert driver.wait is wait
        assert ("binding_error" if name == "partial" else "hook_changed") in payload["errors"]

    for name in ("_row_digest", "export", "partial"):
        exercise(name)


def test_digest_transport_version_bounds_and_pair_arithmetic_are_strict(monkeypatch):
    import copy

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
        _exercise_restore_bindings(owner, False)
    assert payload["quality"] and restore_observer.safe(payload)
    for mutation in (
        "old_format",
        "private",
        "pairs",
        "normal_returns",
        "completed",
        "cpu",
        "waits",
        "negative",
        "exports",
    ):
        forged = copy.deepcopy(payload)
        scans = next(
            record["digest_scans"]
            for record in forged["records"]
            if record["label"] == "snapshot.capture"
        )
        if mutation == "old_format":
            forged["format"] = "swingset-restore-call-observations-v1"
        elif mutation == "private":
            scans["private argument"] = "must not cross transport"
        elif mutation == "pairs":
            scans["pairs"] -= 1
        elif mutation == "normal_returns":
            scans["immutable"]["normal_returns"] -= 1
        elif mutation == "completed":
            scans["snapshot_completed"] = False
        elif mutation == "cpu":
            scans["physical"]["owner_thread_cpu_ns"] = scans["physical"]["wall_ns"] + 1
        elif mutation == "waits":
            scans["physical"]["wait_calls"] = forged["body"]["wait_calls"] + 1
        elif mutation == "negative":
            scans["immutable"]["wall_ns"] = -1
        else:
            scans["export_normal_returns"] = 2
        assert not restore_observer.quality(forged), mutation
        if mutation in {"old_format", "private"}:
            assert not restore_observer.safe(forged)


def test_digest_pair_accounting_resets_for_each_snapshot_and_preserves_partial_error(monkeypatch):
    import pytest

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    original_digest, calls = owner._row_digest, []
    failure = ValueError("original immutable scan failure")

    def row_digest(*args, **kwargs):
        calls.append(True)
        if len(calls) == 566:
            raise failure
        return original_digest(*args, **kwargs)

    owner._row_digest = row_digest
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    with pytest.raises(ValueError) as caught:
        with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
            owner.snapshot_capture()
            owner.snapshot_capture()
            owner.snapshot_capture()
    assert caught.value is failure
    first, second, partial = [row["digest_scans"] for row in payload["records"]]
    assert first is not second and first["physical"] is not second["physical"]
    assert first["calls"] == second["calls"] == 282
    for scans in (first, second):
        assert (
            scans["pairs"] == 141 and scans["normal_returns"] == 282 and scans["snapshot_completed"]
        )
        assert scans["export_normal_returns"] == 1
    assert partial["calls"] == 2 and partial["normal_returns"] == 1 and partial["pairs"] == 0
    assert partial["physical"]["normal_returns"] == 1
    assert (
        partial["immutable"]["exceptions"]
        == partial["exceptions"]
        == partial["export_exceptions"]
        == 1
    )
    assert not partial["snapshot_completed"] and not payload["quality"]
    assert payload["hooks_restored"] and owner._row_digest is row_digest


def test_input_capture_buckets_reset_and_allow_warm_capture_without_future_waits(monkeypatch):
    from concurrent.futures import Future
    from pathlib import Path

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    calls = []

    def capture(*args, **kwargs):
        calls.append(True)
        assert owner.capture_runtime() == "original runtime result"
        if len(calls) == 1:
            future = Future()
            future.set_result("original future result")
            assert future.result() == "original future result"
        assert Path(".").exists()
        return "original function result"

    owner.inputs_capture = capture
    runtime, future_result, exists = owner.capture_runtime, Future.result, Path.exists
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
        _exercise_restore_bindings(owner, False)
        assert owner.inputs_capture() == "original function result"
    assert payload["quality"], payload
    first, second = [
        row["input_capture"] for row in payload["records"] if row["label"] == "inputs.capture"
    ]
    assert first is not second and first["runtime"] is not second["runtime"]
    assert first["future_result"]["calls"] == first["future_result"]["normal_returns"] == 1
    assert second["future_result"]["calls"] == second["future_result"]["wall_ns"] == 0
    assert first["runtime"]["wait_calls"] == second["runtime"]["wait_calls"] == 1
    assert (
        owner.capture_runtime is runtime
        and Future.result is future_result
        and Path.exists is exists
    )


def test_input_capture_excludes_other_callers_and_foreign_threads_without_clocks(monkeypatch):
    import json
    import threading
    from concurrent.futures import Future
    from pathlib import Path

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    sentinel, clocks = object(), []
    wall, cpu = restore_observer._WALL, restore_observer._CPU

    def clock(function):
        def read():
            clocks.append(True)
            return function()

        return read

    def runtime():
        return sentinel

    def helper():
        before = len(clocks)
        assert owner.capture_runtime() is sentinel
        future = Future()
        future.set_result(sentinel)
        assert future.result() is sentinel
        assert Path(".").exists()
        assert len(clocks) == before

    def capture(*args, **kwargs):
        assert owner.capture_runtime() is sentinel
        future = Future()
        future.set_result(sentinel)
        assert future.result() is sentinel
        assert Path(".").exists()
        helper()
        failures = []

        def worker():
            try:
                helper()
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive() and not failures
        return "original function result"

    owner.capture_runtime, owner.inputs_capture = runtime, capture
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    monkeypatch.setattr(restore_observer, "_WALL", clock(wall))
    monkeypatch.setattr(restore_observer, "_CPU", clock(cpu))
    with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
        _exercise_restore_bindings(owner, False)
    assert payload["quality"], payload
    observed = next(
        row["input_capture"] for row in payload["records"] if row["label"] == "inputs.capture"
    )
    for name in ("runtime", "future_result", "path_exists"):
        assert observed[name]["calls"] == observed[name]["normal_returns"] == 1
        assert observed[name]["other_callers"] == observed[name]["foreign_calls"] == 1
    assert "private" not in json.dumps(payload)


def test_input_capture_requires_runtime_path_and_initial_future_coverage(monkeypatch):
    from concurrent.futures import Future
    from pathlib import Path

    from swingset.dev import restore_observer

    def exercise(missing):
        owner, bindings, driver = _fake_restore_bindings()
        entered = []

        def capture(*args, **kwargs):
            if missing == "nested" and not entered:
                entered.append(True)
                assert owner.inputs_capture() == "original function result"
            if missing != "runtime":
                owner.capture_runtime()
            if missing != "future_result":
                future = Future()
                future.set_result(None)
                assert future.result() is None
            if missing != "path_exists":
                assert Path(".").exists()
            return "original function result"

        owner.inputs_capture = capture
        with monkeypatch.context() as scoped:
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                _exercise_restore_bindings(owner, False)
        observed = next(
            row["input_capture"] for row in payload["records"] if row["label"] == "inputs.capture"
        )
        assert observed["capture_completed"] and not payload["quality"]
        if missing == "nested":
            assert "input_scope" in payload["errors"]
            assert sum(row["label"] == "inputs.capture" for row in payload["records"]) == 2
        else:
            assert observed[missing]["calls"] == 0 and "input_incomplete" in payload["errors"]
        assert payload["hooks_restored"]

    for missing in ("runtime", "future_result", "path_exists", "nested"):
        exercise(missing)


def test_input_capture_errors_and_observer_faults_preserve_original_and_hooks(monkeypatch):
    from concurrent.futures import Future
    from pathlib import Path

    import pytest

    from swingset.dev import restore_observer
    from swingset.state.engine import CommitUncertain

    def exercise(failure_type, bucket):
        owner, bindings, driver = _fake_restore_bindings()
        failure = failure_type("private original failure")
        finishing = restore_observer._Observation.finish

        def runtime():
            if bucket == "runtime":
                raise failure
            return "original runtime result"

        def exists(path):
            raise failure

        def capture(*args, **kwargs):
            owner.capture_runtime()
            future = Future()
            if bucket == "future_result":
                future.set_exception(failure)
            else:
                future.set_result(None)
            assert future.result() is None
            Path(".").exists()
            return "original function result"

        def finish(self, before):
            current = self.input_capture()
            if current is not None and current[bucket]["exceptions"]:
                raise KeyboardInterrupt("secondary observer fault")
            return finishing(self, before)

        owner.capture_runtime, owner.inputs_capture = runtime, capture
        with monkeypatch.context() as scoped:
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            scoped.setattr(restore_observer._Observation, "finish", finish)
            if bucket == "path_exists":
                scoped.setattr(Path, "exists", exists)
            original_future, original_exists = Future.result, Path.exists
            with pytest.raises(failure_type) as caught:
                with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                    owner.inputs_capture()
            assert caught.value is failure and not payload["quality"]
            assert payload["hooks_restored"] and owner.capture_runtime is runtime
            assert Future.result is original_future and Path.exists is original_exists
            observed = payload["records"][0]["input_capture"]
            assert observed[bucket]["exceptions"] == 1 and not observed["capture_completed"]
            assert "observer_error" in payload["errors"]

    for failure_type in (ValueError, CommitUncertain, KeyboardInterrupt):
        for bucket in ("runtime", "future_result", "path_exists"):
            exercise(failure_type, bucket)


def test_input_capture_observer_fault_on_success_restores_hooks_before_interrupt(monkeypatch):
    from concurrent.futures import Future
    from pathlib import Path

    import pytest

    from swingset.dev import restore_observer

    def exercise(failure_type):
        owner, bindings, driver = _fake_restore_bindings()
        finish = restore_observer._Observation.finish
        fault = failure_type("actual observer fault")
        runtime, future, exists = owner.capture_runtime, Future.result, Path.exists

        def finishing(self, before):
            if self.input_capture() is not None:
                raise fault
            return finish(self, before)

        with monkeypatch.context() as scoped:
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            scoped.setattr(restore_observer._Observation, "finish", finishing)
            if failure_type is KeyboardInterrupt:
                with pytest.raises(KeyboardInterrupt) as caught:
                    with restore_observer.observe(
                        "gw0", restore_observer.BASE + "[False]"
                    ) as payload:
                        owner.inputs_capture()
                assert caught.value is fault
            else:
                with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                    _exercise_restore_bindings(owner, False)
                assert payload["records"][1]["input_capture"]["capture_completed"]
        assert not payload["quality"] and payload["hooks_restored"]
        assert (
            owner.capture_runtime is runtime and Future.result is future and Path.exists is exists
        )

    for failure_type in (OSError, KeyboardInterrupt):
        exercise(failure_type)


def test_input_capture_drift_and_restore_fault_do_not_clobber_foreign_owner(monkeypatch):
    from concurrent.futures import Future
    from pathlib import Path
    from types import SimpleNamespace

    import pytest

    from swingset.dev import restore_observer

    class Owner(SimpleNamespace):
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name == "capture_runtime" and value is vars(self).get("restore_runtime"):
                raise KeyboardInterrupt("secondary restore interrupt")

    def exercise(bucket):
        owner, bindings, driver = _fake_restore_bindings(Owner)
        future, exists, runtime = Future.result, Path.exists, owner.capture_runtime
        failure = ValueError("original producer error")
        targets = {
            "runtime": (owner, "capture_runtime"),
            "future_result": (Future, "result"),
            "path_exists": (Path, "exists"),
        }
        target, attribute = targets.get(bucket, (owner, "capture_runtime"))

        def foreign(*args, **kwargs):
            return "foreign owner's result"

        with monkeypatch.context() as scoped:
            # Register exact original global values for test teardown after the
            # observer intentionally refuses to overwrite a foreign hook.
            scoped.setattr(Future, "result", future)
            scoped.setattr(Path, "exists", exists)
            scoped.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
            with pytest.raises(ValueError) as caught:
                with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
                    if bucket == "restore":
                        owner.restore_runtime = runtime
                    else:
                        setattr(target, attribute, foreign)
                    raise failure
            assert caught.value is failure and not payload["quality"]
            if bucket != "restore":
                assert getattr(target, attribute) is foreign and "hook_changed" in payload["errors"]
            else:
                assert owner.capture_runtime is runtime and "restore_error" in payload["errors"]
            assert Future.result is (foreign if bucket == "future_result" else future)
            assert Path.exists is (foreign if bucket == "path_exists" else exists)
            assert owner.export.__name__ == "export" and owner._row_digest.__name__ == "row_digest"

    for bucket in ("runtime", "future_result", "path_exists", "restore"):
        exercise(bucket)


def test_input_capture_transport_rejects_incomplete_overlap_and_unreviewed_fields(monkeypatch):
    import copy

    from swingset.dev import restore_observer

    owner, bindings, driver = _fake_restore_bindings()
    monkeypatch.setattr(restore_observer, "_bindings", lambda: (bindings, driver))
    with restore_observer.observe("gw0", restore_observer.BASE + "[False]") as payload:
        _exercise_restore_bindings(owner, False)
    assert payload["quality"] and restore_observer.safe(payload)
    for mutation in (
        "version",
        "private",
        "completed",
        "returns",
        "wall",
        "cpu",
        "waits",
        "negative",
    ):
        forged = copy.deepcopy(payload)
        record = next(row for row in forged["records"] if row["label"] == "inputs.capture")
        observed = record["input_capture"]
        if mutation == "version":
            forged["format"] = "swingset-restore-call-observations-v2"
        elif mutation == "private":
            observed["runtime"]["private path"] = "must not cross transport"
        elif mutation == "completed":
            observed["capture_completed"] = False
        elif mutation == "returns":
            observed["runtime"]["normal_returns"] = 0
        elif mutation == "wall":
            observed["future_result"]["wall_ns"] = record["end_ns"] - record["start_ns"] + 1
        elif mutation == "cpu":
            observed["path_exists"]["owner_thread_cpu_ns"] = observed["path_exists"]["wall_ns"] + 1
        elif mutation == "waits":
            observed["runtime"]["wait_calls"] = record["wait_calls"] + 1
        else:
            observed["runtime"]["owner_thread_cpu_ns"] = -1
        assert not restore_observer.quality(forged), mutation
        if mutation in {"version", "private"}:
            assert not restore_observer.safe(forged)


def test_publication_write_observer_preserves_real_bytes_fsyncs_and_native_clocks(
    tmp_path, monkeypatch
):
    import json
    import stat
    import time

    from swingset.dev import fixture_observer
    from swingset.fetch import archive

    sentinel, flushed = object(), []
    original_write, original_sync = archive.durable_write, archive.os.fsync

    def sync(descriptor):
        original_sync(descriptor)
        flushed.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")

    def fixture():
        archive.durable_write(tmp_path / "sensitive-member", b"sensitive body")
        archive.durable_write(tmp_path / "manifest", b"complete manifest")
        return sentinel

    monkeypatch.setattr(fixture_observer, "_binding", lambda: (archive, fixture.__code__))
    monkeypatch.setattr(archive.os, "fsync", sync)
    monkeypatch.setattr(time, "monotonic_ns", lambda: -1)
    monkeypatch.setattr(time, "thread_time_ns", lambda: -1)
    with fixture_observer.observe("gw0") as payload:
        assert fixture() is sentinel
    assert payload["quality"] and payload["setup_completed"] and payload["hooks_restored"]
    assert payload["calls"] == payload["normal_returns"] == 2 and payload["exceptions"] == 0
    assert archive.durable_write is original_write
    assert flushed == ["file", "directory", "file", "directory"]
    assert (tmp_path / "sensitive-member").read_bytes() == b"sensitive body"
    assert (tmp_path / "manifest").read_bytes() == b"complete manifest"
    assert "sensitive" not in json.dumps(payload) and str(tmp_path) not in json.dumps(payload)


def test_publication_write_observer_excludes_other_callers_and_threads_before_clocks(
    tmp_path, monkeypatch
):
    import threading

    from swingset.dev import fixture_observer
    from swingset.fetch import archive

    reads = []
    wall, cpu = fixture_observer._WALL, fixture_observer._CPU

    def clock(function):
        def read():
            reads.append(True)
            return function()

        return read

    def fixture():
        archive.durable_write(tmp_path / "owner", b"owner")

    def helper():
        before = len(reads)
        archive.durable_write(tmp_path / "other", b"other")
        assert len(reads) == before

    monkeypatch.setattr(fixture_observer, "_binding", lambda: (archive, fixture.__code__))
    monkeypatch.setattr(fixture_observer, "_WALL", clock(wall))
    monkeypatch.setattr(fixture_observer, "_CPU", clock(cpu))
    with fixture_observer.observe("gw0") as payload:
        fixture()
        helper()
        thread = threading.Thread(target=helper)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert payload["quality"], payload
    assert payload["calls"] == payload["normal_returns"] == 1
    assert payload["other_callers"] == payload["foreign_calls"] == 1
    assert (tmp_path / "other").read_bytes() == b"other"


def test_publication_write_observer_preserves_original_errors_and_cleanup_interrupts(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from swingset.dev import fixture_observer
    from swingset.state.engine import CommitUncertain

    def exercise(failure_type):
        original = failure_type("sensitive original error")

        def write(*args, **kwargs):
            raise original

        owner = SimpleNamespace(durable_write=write)

        def fixture():
            owner.durable_write(object(), object())

        def finish(*args):
            raise KeyboardInterrupt("secondary clock failure")

        with monkeypatch.context() as scoped:
            scoped.setattr(fixture_observer, "_binding", lambda: (owner, fixture.__code__))
            scoped.setattr(fixture_observer._Observation, "finish", finish)
            with pytest.raises(failure_type) as caught:
                with fixture_observer.observe("gw0") as payload:
                    fixture()
        assert caught.value is original and owner.durable_write is write
        assert payload["hooks_restored"] and not payload["quality"]
        assert payload["exceptions"] == 1 and not payload["setup_completed"]

    for failure_type in (ValueError, CommitUncertain, KeyboardInterrupt):
        exercise(failure_type)

    def successful_fault(failure_type):
        def write(*args):
            return "original result"

        owner = SimpleNamespace(durable_write=write)
        fault = failure_type("observer failure")

        def fixture():
            return owner.durable_write()

        def finish(*args):
            raise fault

        with monkeypatch.context() as scoped:
            scoped.setattr(fixture_observer, "_binding", lambda: (owner, fixture.__code__))
            scoped.setattr(fixture_observer._Observation, "finish", finish)
            if failure_type is KeyboardInterrupt:
                with pytest.raises(KeyboardInterrupt) as caught:
                    with fixture_observer.observe("gw0") as payload:
                        fixture()
                assert caught.value is fault
            else:
                with fixture_observer.observe("gw0") as payload:
                    assert fixture() == "original result"
        assert owner.durable_write is write and payload["hooks_restored"] and not payload["quality"]

    for failure_type in (OSError, KeyboardInterrupt):
        successful_fault(failure_type)
    original = ValueError("original before reporter failure")

    def write(*args):
        raise original

    owner = SimpleNamespace(durable_write=write)

    def fixture():
        owner.durable_write()

    def report(self, exception):
        assert owner.durable_write is write  # Restoration precedes reporting.
        raise KeyboardInterrupt("secondary reporter fault")

    with monkeypatch.context() as scoped:
        scoped.setattr(fixture_observer, "_binding", lambda: (owner, fixture.__code__))
        scoped.setattr(fixture_observer._Observation, "report", report)
        with pytest.raises(ValueError) as caught:
            with fixture_observer.observe("gw0") as payload:
                fixture()
    assert caught.value is original and payload["errors"] == ["report_error"]
    assert owner.durable_write is write and not payload["quality"]


def test_publication_write_observer_refuses_hook_drift_and_marks_restore_fault(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from swingset.dev import fixture_observer

    class Owner(SimpleNamespace):
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name == "durable_write" and value is vars(self).get("restore_original"):
                raise KeyboardInterrupt("secondary restore failure")

    def exercise(drift):
        def write(*args):
            return "original"

        owner = Owner(durable_write=write)
        original = ValueError("original setup failure")

        def fixture():
            return owner.durable_write()

        def foreign(*args):
            return "foreign owner"

        with monkeypatch.context() as scoped:
            scoped.setattr(fixture_observer, "_binding", lambda: (owner, fixture.__code__))
            with pytest.raises(ValueError) as caught:
                with fixture_observer.observe("gw0") as payload:
                    assert fixture() == "original"
                    if drift:
                        owner.durable_write = foreign
                    else:
                        owner.restore_original = write
                    raise original
        assert caught.value is original and not payload["quality"] and not payload["hooks_restored"]
        assert owner.durable_write is (foreign if drift else write)
        assert ("hook_changed" if drift else "restore_error") in payload["errors"]

    for drift in (False, True):
        exercise(drift)


def test_publication_write_payload_uses_real_two_worker_setup_transport(tmp_path):
    import json

    from swingset.dev import fixture_observer

    extra = (
        "import pytest\nfrom pathlib import Path\n"
        "from swingset.dev import fixture_observer\nfrom swingset.fetch import archive\n"
        "def published():\n"
        "    archive.durable_write(Path('sensitive-member'), b'sensitive bytes')\n"
        "    archive.durable_write(Path('manifest'), b'complete')\n"
        "fixture_observer._binding = lambda: (archive, published.__code__)\n"
        "ORIGINAL_WRITE = archive.durable_write\n"
        "@pytest.hookimpl(wrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
        "    report = yield\n"
        "    assert archive.durable_write is ORIGINAL_WRITE\n"
        "    return report\n"
        "@pytest.fixture\ndef ready(request):\n"
        "    published()\n"
        "    Path('actual-owner').write_text(request.config.workerinput['workerid'])\n"
        "    return 'ready'\n"
    )
    case = (
        "from pathlib import Path\n"
        "def test_a_changed_artifact_file_refuses_publication(ready):\n"
        "    assert ready == 'ready'\n"
        "    assert Path('sensitive-member').read_bytes() == b'sensitive bytes'\n"
    )
    result, receipt = _run_phase_receipt(
        tmp_path,
        case,
        workers=2,
        test_file="tests/build/test_release_paths.py",
        extra_conftest=extra,
        companion=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["counts"]["passed"] == 2 and receipt["phase_observations"]["quality"]
    observed = receipt["publication_fixture_observation"]
    assert observed["applicable"] and observed["quality"] and not observed["errors"]
    value = observed["case"]
    assert value["nodeid"] == fixture_observer.NODE and value["phase"] == "setup"
    assert value["worker"] == (tmp_path / "actual-owner").read_text()
    assert value["calls"] == value["normal_returns"] == 2 and value["hooks_restored"]
    assert value["setup_completed"] and "sensitive" not in json.dumps(observed)
    failed_root = tmp_path / "failed"
    failed_root.mkdir()
    failed_extra = extra.replace(
        "    return 'ready'\n", "    raise ValueError('sensitive original setup failure')\n"
    )
    failed, receipt = _run_phase_receipt(
        failed_root,
        case,
        workers=2,
        test_file="tests/build/test_release_paths.py",
        extra_conftest=failed_extra,
        companion=True,
    )
    assert failed.returncode == 1
    assert receipt["counts"]["error"] == receipt["counts"]["passed"] == 1
    observed = receipt["publication_fixture_observation"]
    assert not observed["quality"] and observed["case"]["hooks_restored"]
    assert observed["case"]["exception_type"] == "ValueError"
    assert not observed["case"]["setup_completed"]
    assert "sensitive" not in json.dumps(observed)


def test_publication_write_collector_rejects_unsafe_missing_wrong_worker_and_overlap(monkeypatch):
    import copy
    import json
    from types import SimpleNamespace

    from swingset.dev import fixture_observer

    def write(*args):
        return "unchanged"

    owner = SimpleNamespace(durable_write=write)

    def fixture():
        return owner.durable_write()

    monkeypatch.setattr(fixture_observer, "_binding", lambda: (owner, fixture.__code__))
    with fixture_observer.observe("gw0") as payload:
        assert fixture() == "unchanged"
    assert payload["quality"]

    def report(value, when="setup", node=fixture_observer.NODE):
        return SimpleNamespace(
            nodeid=node, when=when, user_properties=[(fixture_observer.PROPERTY, value)]
        )

    for mutation in ("private", "worker", "returns", "cpu", "wall", "negative", "overlap"):
        value = copy.deepcopy(payload)
        if mutation == "private":
            value["private argument"] = "sensitive text"
        elif mutation == "worker":
            value["worker"] = "gw3"
        elif mutation == "returns":
            value["normal_returns"] = 0
        elif mutation == "cpu":
            value["owner_thread_cpu_ns"] = value["wall_ns"] + 1
        elif mutation == "wall":
            value["wall_ns"] = value["setup_wall_ns"] + 1
        elif mutation == "negative":
            value["wall_ns"] = -1
        else:
            value["errors"] = ["overlap"]
        collector = fixture_observer.Collector()
        collector.accept(report(value), "gw0")
        observed = collector.report()
        assert not observed["quality"] and observed["case"] is None
        assert "sensitive" not in json.dumps(observed)
    collector = fixture_observer.Collector()
    collector.accept(report(payload, "call"), "gw0")
    assert (
        not collector.report()["quality"]
        and "missing_setup_payload" in collector.report()["errors"]
    )
    collector = fixture_observer.Collector()
    collector.accept(report(payload), "gw0")
    collector.accept(report(payload), "gw0")
    assert "missing_or_duplicate_payload" in collector.report()["errors"]

    entered = []

    def recursive():
        if not entered:
            entered.append(True)
            assert fixture() == "unchanged"
        return "unchanged"

    owner.durable_write = recursive
    with fixture_observer.observe("gw0") as nested:
        assert fixture() == "unchanged"
    assert not nested["quality"] and nested["errors"] == ["overlap"]
    assert nested["calls"] == nested["normal_returns"] == 2 and nested["hooks_restored"]
