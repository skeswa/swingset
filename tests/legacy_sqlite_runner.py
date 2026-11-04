"""Offline reporter loaded only by the archived SQLite regression subprocess."""

import json
import os
import socket
from pathlib import Path

import pytest

RESULTS = {}
COLLECTION_ERRORS = []


def pytest_configure():
    def offline(*args, **kwargs):
        raise AssertionError("Historical SQLite tests may not use the network")

    socket.socket.connect = offline
    socket.getaddrinfo = offline


def pytest_runtest_logreport(report):
    name = report.nodeid.split("::", 1)[1]
    value = RESULTS.setdefault(name, {"outcome": "passed", "phases": []})
    value["phases"].append(report.when)
    if report.failed or report.skipped:
        value.update(outcome=report.outcome, detail=report.longreprtext)


def pytest_collectreport(report):
    if report.failed:
        COLLECTION_ERRORS.append(report.longreprtext)


def pytest_sessionfinish(session, exitstatus):
    Path(os.environ["SWINGSET_LEGACY_RESULT"]).write_text(
        json.dumps(
            {
                "exit_status": int(exitstatus),
                "results": RESULTS,
                "collection_errors": COLLECTION_ERRORS,
            }
        )
    )


@pytest.fixture(autouse=True)
def no_worker_quota(monkeypatch, tmp_path):
    from swingset.backup import drill
    from swingset.loops import release
    from swingset.state import disk

    absent = tmp_path / "no-worker-quota"
    monkeypatch.setattr(disk, "QUOTA_FILE", absent)
    monkeypatch.setattr(release, "DISK_FILE", absent)
    monkeypatch.setattr(drill, "QUOTA_CACHE", absent)
