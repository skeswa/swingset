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
