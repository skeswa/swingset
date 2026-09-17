"""Read-only supplementary monitoring after a shared log pathname was replaced.

The original supervisor still owns continuation and resource enforcement. This
observer writes only a new VM-local file, avoiding the active jj worktree.
"""

import json
import os
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

PREFIX = "swingset-h16-event-preservation-replay"
OUTPUT = Path("/var/tmp/swingset-h16-event-preservation-replay-resource-supplement.jsonl")
SUPERVISOR = Path("/proc/96374")
DATABASE = Path("/var/tmp/swingset-h16-event-preservation-replay/state.sqlite")
PROPERTIES = ("LoadState", "ActiveState", "SubState", "Result", "ExecMainStatus", "MainPID", "InvocationID", "ControlGroup", "MemoryCurrent", "MemoryPeak", "CPUUsageNSec")


def command(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=15).stdout


deadline = time.monotonic() + 7200
with OUTPUT.open("x") as stream:
    print(json.dumps({"output": str(OUTPUT), "started_at": datetime.now(UTC).isoformat()}), flush=True)
    while True:
        alive = SUPERVISOR.exists()
        units = command(["systemctl", "list-units", "--all", "--no-legend", "--plain", PREFIX + "-*.service"])
        names = [line.split()[0] for line in units.splitlines() if line.split() and line.split()[0].startswith(PREFIX + "-") and line.split()[0] != PREFIX + "-prepare.service"]
        sample = {"at": datetime.now(UTC).isoformat(), "supervisor_alive": alive, "supplementary_read_only_observer": True}
        if names:
            unit = max(names)
            raw = command(["systemctl", "show", unit, *("--property=" + key for key in PROPERTIES)])
            state = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
            sample.update(unit=unit, service=state)
            group = state.get("ControlGroup")
            memory = Path("/sys/fs/cgroup") / group.lstrip("/") / "memory.stat" if group else None
            if memory and memory.exists():
                sample["cgroup_memory"] = {key: int(value) for key, value in (line.split() for line in memory.read_text().splitlines())}
            receipt = Path("/var/tmp") / (unit.removesuffix(".service") + ".json")
            if receipt.exists():
                sample["receipt"] = json.loads(receipt.read_bytes())
        conn = sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            conn.execute("PRAGMA query_only=ON")
            sample["attempt_outcomes"] = dict(conn.execute("SELECT outcome,count(*) FROM work_attempts GROUP BY outcome"))
        finally:
            conn.close()
        stream.write(json.dumps(sample, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        print(json.dumps({"at": sample["at"], "unit": sample.get("unit"), "attempts": sample["attempt_outcomes"], "supervisor_alive": alive}), flush=True)
        if not alive:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("Supplementary observer finite two-hour limit reached")
        time.sleep(20)
