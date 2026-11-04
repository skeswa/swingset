"""Record controller outcomes for a complete partition, without import side effects."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_PHASES: dict[str, dict[str, float]] = {}


def pytest_sessionstart(session: Any) -> None:
    _PHASES.clear()


def pytest_runtest_logreport(report: Any) -> None:
    phases = _PHASES.setdefault(report.nodeid, {})
    phases[report.when] = phases.get(report.when, 0.0) + report.duration


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if hasattr(session.config, "workerinput"):
        return
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    keys = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")
    counts = {key: len(reporter.stats.get(key, [])) for key in keys}
    executed = sorted({report.nodeid for key in keys for report in reporter.stats.get(key, [])})
    path = Path("/tmp/core-results.json")
    with path.open("w") as stream:
        json.dump(
            {
                "exit_code": int(exitstatus),
                "counts": counts,
                "executed": executed,
                "node_phases": _PHASES,
            },
            stream,
            sort_keys=True,
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
