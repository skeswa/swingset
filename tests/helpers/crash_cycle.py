"""Subprocess-only crash/SIGTERM harness; no fault injection ships in runtime."""

import contextlib
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from swingset.clock import FakeClock
from swingset.schedule.cycle import run_cycle
from swingset.state.db import Database, open_database

state = Path(sys.argv[1])
overrides = Path(sys.argv[2])
config = Path(sys.argv[3])
fault = sys.argv[4]
original = Database.transaction
count = 0
stopped = False
checkpoints = {}


def checkpoint():
    """Name durable pipeline transitions without adding runtime fault hooks."""
    frame = sys._getframe(2)
    while frame.f_globals.get("__name__") == "contextlib":
        frame = frame.f_back
    caller = (frame.f_globals.get("__name__"), frame.f_code.co_name)
    if caller == ("swingset.state.inputs", "accept"):
        return "inputs_accepted"
    if caller == ("swingset.fetch.client", "_fetch") and "snapshot_id" in frame.f_locals:
        return "snapshot_saved"
    if caller == ("swingset.schedule.derive", "derive_one"):
        unit = frame.f_locals["unit"]
        return f"{unit.stage}_{unit.unit_kind}_completed"
    if caller == ("swingset.build.generations", "complete"):
        return "build_completed"
    return None


@contextlib.contextmanager
def transaction(self, **kwargs):
    global count
    count += 1
    boundary = count
    name = checkpoint()
    if name is not None:
        checkpoints.setdefault(name, boundary)
    if fault == f"before:{boundary}":
        os._exit(91)
    try:
        with original(self, **kwargs) as conn:
            yield conn
    finally:
        # A deferred request now deliberately rolls back its speculative
        # admission. Its rollback boundary needs the same crash coverage as a
        # successful commit; neither should disappear from the trace.
        if fault == f"after:{boundary}":
            os._exit(91)


Database.transaction = transaction


def stop(_signal, _frame):
    global stopped
    stopped = True


signal.signal(signal.SIGTERM, stop)
body = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()


def handler(request):
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    if fault == "sigterm":
        print("READY", flush=True)
        time.sleep(0.5)
    return httpx.Response(200, content=body)


clock = FakeClock()
with open_database(state) as db:
    result = run_cycle(
        db,
        config_dir=config,
        overrides_dir=overrides,
        clock=clock,
        transport=httpx.MockTransport(handler),
        should_stop=lambda: stopped,
    )
    recovery_cycles = 0
    # H12 preserves a restart deadline instead of immediately retrying a crashed
    # unit. Exercise that durable boundary with the injected clock, then require
    # the same fully committed belief as an uninterrupted run.
    if fault == "none":
        for _ in range(3):
            retries = db.connection.execute(
                "SELECT retry_at FROM work_attempts a WHERE outcome IN ('interrupted','transient') "
                "AND retry_at IS NOT NULL AND EXISTS (SELECT 1 FROM pending_work p "
                "WHERE p.stage=a.stage AND p.unit_kind=a.unit_kind AND p.unit_id=a.unit_id) "
                "AND attempt_id=(SELECT max(b.attempt_id) FROM work_attempts b "
                "WHERE b.stage=a.stage AND b.unit_kind=a.unit_kind AND b.unit_id=a.unit_id)"
            ).fetchall()
            if not retries:
                break
            retry_at = max(datetime.fromisoformat(row[0]) for row in retries)
            clock.sleep(max(0, (retry_at - clock.now()).total_seconds()))
            result = run_cycle(
                db,
                config_dir=config,
                overrides_dir=overrides,
                clock=clock,
                transport=httpx.MockTransport(handler),
                should_stop=lambda: stopped,
            )
            recovery_cycles += 1
    print(
        json.dumps(
            {
                "transactions": count,
                "checkpoints": checkpoints,
                "stopped": result["stopped"],
                "recovery_cycles": recovery_cycles,
            }
        ),
        flush=True,
    )
