"""Place only explicit capacity fixtures on persistent disk in the RAM harness."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass
class Allocation:
    path: Path
    token: str
    device: int
    inode: int
    finished: bool = False
    reports: dict[str, bool] = field(default_factory=dict)

    @property
    def payload(self) -> Path:
        return self.path / "case"


_KEY: pytest.StashKey[Allocation] = pytest.StashKey()
_MARKER = ".swingset-capacity-owner"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "capacity_disk: exercise real persistent-disk capacity checks"
    )
    value = os.environ.get("SWINGSET_TEST_TMP_RETENTION")
    if value and value != "failed":
        raise pytest.UsageError("SWINGSET_TEST_TMP_RETENTION only supports failed")


@pytest.fixture
def tmp_path(tmp_path: Path, request: pytest.FixtureRequest) -> Generator[Path, None, None]:
    """Wrap pytest's own fixture; ordinary tests keep its exact path and policy."""
    if os.environ.get(
        "SWINGSET_TEST_TMP_RETENTION"
    ) != "failed" or not request.node.get_closest_marker("capacity_disk"):
        yield tmp_path
        return
    allocation = allocate_persistent_directory()
    request.node.stash[_KEY] = allocation
    (tmp_path / "capacity-location.json").write_text(
        json.dumps({"directory": allocation.path.name}) + "\n"
    )
    yield allocation.payload
    allocation.finished = True


def allocate_persistent_directory() -> Allocation:
    """Own a private disk-backed payload without putting metadata in its state."""
    path = Path(tempfile.mkdtemp(prefix="swingset-capacity-", dir="/tmp"))
    token = uuid.uuid4().hex
    info = path.lstat()
    allocation = Allocation(path, token, info.st_dev, info.st_ino)
    (path / _MARKER).write_text(token)
    allocation.payload.mkdir(mode=0o700)
    return allocation


def release_persistent_directory(allocation: Allocation) -> None:
    """Remove exact owned payload only after its caller has proved completion."""
    _remove(allocation)


def _owned(allocation: Allocation, path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_dev != allocation.device
        or info.st_ino != allocation.inode
    ):
        raise RuntimeError(f"Capacity fixture ownership changed; retained at {path}")
    descriptor = os.open(path / _MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        marker = os.fstat(descriptor)
        if (
            not stat.S_ISREG(marker.st_mode)
            or marker.st_uid != os.getuid()
            or marker.st_size > len(allocation.token) + 1
            or os.read(descriptor, len(allocation.token) + 1) != allocation.token.encode()
        ):
            raise RuntimeError(f"Capacity fixture owner marker changed; retained at {path}")
    finally:
        os.close(descriptor)


def _remove(allocation: Allocation) -> None:
    path = allocation.path
    if path.parent != Path("/tmp") or not path.name.startswith("swingset-capacity-"):
        raise RuntimeError("Capacity fixture path changed; directory retained")
    if not path.exists() and not path.is_symlink():
        return
    _owned(allocation, path)
    # Verify again after atomically moving into a fresh private parent. A path
    # replacement between the first check and rename is retained, never deleted.
    quarantine = Path(tempfile.mkdtemp(prefix="swingset-capacity-clean-", dir="/tmp"))
    target = quarantine / "payload"
    path.rename(target)
    try:
        _owned(allocation, target)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"Capacity fixture retained at {target}: {error}") from error
    shutil.rmtree(quarantine)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    allocation = item.stash.get(_KEY, None)
    if allocation is not None:
        allocation.reports[report.when] = report.passed
        if (
            report.when == "teardown"
            and allocation.finished
            and all(allocation.reports.get(phase, False) for phase in ("setup", "call", "teardown"))
        ):
            try:
                _remove(allocation)
            except (OSError, RuntimeError) as error:
                report.outcome = "failed"
                report.longrepr = f"Capacity fixture cleanup refused: {error}"

    return report
