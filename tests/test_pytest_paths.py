"""The RAM harness preserves real disk checks and failed fixture payloads."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, source, *, enabled=True):
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_probe.py").write_text(source)
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTEST_ADDOPTS": "",
    }
    for name in ("PYTEST_PLUGINS", "SWINGSET_TEST_TIMINGS_PATH"):
        environment.pop(name, None)
    environment.pop("SWINGSET_TEST_TMP_RETENTION", None)
    if enabled:
        environment["SWINGSET_TEST_TMP_RETENTION"] = "failed"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "swingset.dev.pytest_paths",
            "-o",
            "tmp_path_retention_policy=failed",
            "--basetemp",
            str(tmp_path / "child"),
        ],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_capacity_placement_preserves_failures_finalizers_and_symlink_sources(tmp_path):
    records = tmp_path / "records.jsonl"
    source = tmp_path / "source"
    source.mkdir()
    (source / "original").write_text("unchanged")
    script = f"""
import json,shutil
from pathlib import Path
import pytest
RECORDS=Path({str(records)!r})
def record(name,path):
    with RECORDS.open("a") as stream:
        stream.write(json.dumps({{"name":name,"path":str(path)}})+"\\n")
@pytest.fixture
def setup_bad(tmp_path):
    record("setup",tmp_path)
    raise RuntimeError("setup failed")
@pytest.fixture
def teardown_bad(tmp_path):
    yield
    (tmp_path/"dependent-finalizer").write_text("ran")
    raise RuntimeError("teardown failed")
@pytest.mark.capacity_disk
def test_pass(tmp_path):
    record("pass",tmp_path)
    assert shutil.disk_usage(tmp_path).free >= 20_000_000_000
    assert not (tmp_path/".swingset-capacity-owner").exists()
    (tmp_path/"source-link").symlink_to({str(source)!r},target_is_directory=True)
@pytest.mark.capacity_disk
def test_fail(tmp_path):
    record("fail",tmp_path)
    (tmp_path/"failure").write_text("retained")
    assert False,"expected call failure"
@pytest.mark.capacity_disk
def test_setup(setup_bad):pass
@pytest.mark.capacity_disk
def test_teardown(tmp_path,teardown_bad):record("teardown",tmp_path)
def test_ordinary(tmp_path):record("ordinary",tmp_path)
"""
    result = _run(tmp_path, script)
    assert result.returncode == 1, result.stdout + result.stderr
    found = {
        row["name"]: Path(row["path"]) for row in map(json.loads, records.read_text().splitlines())
    }
    try:
        assert set(found) == {"pass", "fail", "setup", "teardown", "ordinary"}
        assert all(
            found[name].name == "case" and found[name].parent.parent == Path("/tmp")
            for name in ("pass", "fail", "setup", "teardown")
        )
        assert found["ordinary"].is_relative_to(tmp_path / "child")
        assert not found["ordinary"].exists()
        assert not found["pass"].exists()
        assert (found["fail"] / "failure").read_text() == "retained"
        assert found["setup"].is_dir()
        assert (found["teardown"] / "dependent-finalizer").read_text() == "ran"
        assert (source / "original").read_text() == "unchanged"
    finally:
        for name in ("fail", "setup", "teardown"):
            if name in found:
                shutil.rmtree(found[name].parent)


def test_capacity_marker_does_not_change_native_tmp_path(tmp_path):
    recorded = tmp_path / "native-path"
    result = _run(
        tmp_path,
        "from pathlib import Path\nimport pytest\n@pytest.mark.capacity_disk\n"
        f"def test_native(tmp_path):Path({str(recorded)!r}).write_text(str(tmp_path))\n",
        enabled=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(recorded.read_text()).is_relative_to(tmp_path / "child")


@pytest.mark.parametrize("tamper", ["wrong", "fifo", "oversize"])
def test_changed_capacity_owner_is_retained_and_reported_as_teardown_failure(tmp_path, tamper):
    recorded = tmp_path / "path"
    change = {
        "wrong": "marker.write_text('changed')",
        "fifo": "marker.unlink(); os.mkfifo(marker)",
        "oversize": "marker.write_bytes(b'x' * 1_000_000)",
    }[tamper]
    result = _run(
        tmp_path,
        "import os\nfrom pathlib import Path\nimport pytest\n@pytest.mark.capacity_disk\n"
        "def test_tamper(tmp_path):\n"
        f"    Path({str(recorded)!r}).write_text(str(tmp_path))\n"
        "    marker=tmp_path.parent/'.swingset-capacity-owner'\n"
        f"    {change}\n",
    )
    path = Path(recorded.read_text())
    try:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "cleanup refused" in result.stdout
        assert "INTERNALERROR" not in result.stdout + result.stderr
        assert path.is_dir()
    finally:
        shutil.rmtree(path.parent)


def test_capacity_metadata_marks_without_changing_node_selection():
    import importlib.util

    specification = importlib.util.spec_from_file_location(
        "capacity_conftest", ROOT / "tests/conftest.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    class Item:
        def __init__(self, nodeid):
            self.nodeid, self.markers = nodeid, []

        def get_closest_marker(self, _name):
            return None

        def add_marker(self, name):
            self.markers.append(name)

    class Config:
        args_source = pytest.Config.ArgsSource.ARGS

        def getoption(self, _name):
            return False

    nodes = [
        Item(
            "tests/publish/test_recovery.py::test_lost_response_is_reconciled_without_duplicate_commit"
        ),
        Item("tests/test_loops.py::test_maintain_runs_the_storage_tasks_and_the_weekly_drill"),
        Item("tests/test_loops.py::test_a_derive_tick_completes_units_and_writes_its_run_record"),
    ]
    before = [item.nodeid for item in nodes]
    module.pytest_collection_modifyitems(Config(), nodes)
    assert [item.nodeid for item in nodes] == before
    assert ["capacity_disk" in item.markers for item in nodes] == [True, True, False]


def test_interrupted_capacity_case_retains_its_directory(tmp_path):
    recorded = tmp_path / "interrupted-path"
    result = _run(
        tmp_path,
        "import os,signal\nfrom pathlib import Path\nimport pytest\n"
        "@pytest.mark.capacity_disk\ndef test_interrupt(tmp_path):\n"
        f"    Path({str(recorded)!r}).write_text(str(tmp_path))\n"
        "    (tmp_path/'unfinished').write_text('retained')\n"
        "    os.kill(os.getpid(),signal.SIGINT)\n",
    )
    path = Path(recorded.read_text())
    try:
        assert result.returncode == 2, result.stdout + result.stderr
        assert (path / "unfinished").read_text() == "retained"
    finally:
        shutil.rmtree(path.parent)
