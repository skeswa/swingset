"""Subprocess-only crash/SIGTERM harness; no fault injection ships in runtime.

One invocation runs one round of loop ticks (plan, acquire, derive, release;
`helpers/loop_ticks.py`) in this process, then, with no fault, the rounds a
unit's restart clock asks for.
"""

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
from swingset.state.db import Database, open_database

sys.path.insert(0, str(Path(__file__).parent))
from loop_ticks import run_round  # noqa: E402

state = Path(sys.argv[1])
overrides = Path(sys.argv[2])
config = Path(sys.argv[3])
fault = sys.argv[4]
#: `listing`: the state also holds one source listing, so the cycle runs a
#: mapping and a resolution unit (plan F19) and their checkpoints exist.
variant = sys.argv[5] if len(sys.argv) > 5 else ""
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
    # derive_one's processing transaction opens in its nested `process`.
    if caller == ("swingset.schedule.derive", "process"):
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
    if variant == "listing":
        # A listing a source index would have written; its first round maps it
        # to a placeholder event (no catalog event shares its name and dates).
        db.connection.execute(
            "INSERT OR IGNORE INTO source_events(source,source_ref,name_raw,start_date,end_date,"
            "url,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES "
            "('eepro','eepro:crash','Crash Test Weekend','2026-03-05','2026-03-08',"
            "'https://example.test/crash','listing-snapshot','1',?,?,'seed')",
            (clock.now().isoformat(), clock.now().isoformat()),
        )


def ticks():
    return run_round(
        state,
        config_dir=config,
        overrides_dir=overrides,
        clock=clock,
        handler=handler,
        should_stop=lambda: stopped,
    )


result = ticks()
recovery_cycles = 0
# A unit whose process died backs off on its queue clock (D-0216). Exercise
# that durable boundary with the injected clock, then require the same fully
# committed belief as an uninterrupted run.
if fault == "none":
    for _ in range(3):
        with open_database(state, lock=False, read_only=True) as db:
            retries = db.connection.execute(
                "SELECT next_attempt_at FROM work_queue WHERE next_attempt_at IS NOT NULL "
                "AND state IN ('ready','waiting_input')"
            ).fetchall()
            unfinished = db.connection.execute("SELECT 1 FROM pending_work LIMIT 1").fetchone()
        if not retries and not unfinished:
            break
        if retries:
            retry_at = max(datetime.fromisoformat(row[0]) for row in retries)
            clock.sleep(max(0, (retry_at - clock.now()).total_seconds()))
        result = ticks()
        recovery_cycles += 1
print(
    json.dumps(
        {
            "transactions": count,
            "checkpoints": checkpoints,
            "stopped": result.stopped,
            "recovery_cycles": recovery_cycles,
        }
    ),
    flush=True,
)
