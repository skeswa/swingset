"""Subprocess-only loop fault harness; no fault hooks ship in runtime.

Usage: loop_fault.py STATE CONFIG OVERRIDES LOOP NOW FAULT [CHUNK_ROWS]

Runs one tick of LOOP at the fake time NOW. Inside the loop body, it counts
top-level write transactions; FAULT is `trace` (print each write's caller
and finish), `N` (SIGKILL inside the N-th write, after its body and before
its commit), `CALLER:N` (the same, counting only writes opened by the
function CALLER), `http` (SIGKILL inside the first page request, after its
admission committed), `unit:ID` (a project processor that SIGKILLs on
unit ID and completes any other unit with no rows, as an OOM would), or
`capture:ID` (the same, but the kill comes inside the unit's admission,
in `derivations.capture`).
CHUNK_ROWS, when given, patches `derivations.CHUNK_ROWS`. Acquire gets a
mock transport: the eepro archive fixture from the Wayback Machine, 404 from
any origin.
"""

import contextlib
import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

import httpx

from swingset.clock import FakeClock
from swingset.loops import acquire, derive, maintain, plan, release
from swingset.loops.runtime import tick
from swingset.state import derivations
from swingset.state.db import Database

state, config, overrides, loop, now, fault = sys.argv[1:7]
if len(sys.argv) > 7:
    derivations.CHUNK_ROWS = int(sys.argv[7])
poison = fault.split(":", 1)[1] if fault.startswith(("unit:", "capture:")) else None
caller_filter, _, nth = fault.rpartition(":")
target = None if fault in ("trace", "http") or poison else int(nth)
original = Database.transaction
armed = False
count = 0
trace: list[str] = []


def caller() -> str:
    """The function that opened the transaction, outside contextlib."""
    frame = sys._getframe(2)
    while frame.f_globals.get("__name__") == "contextlib":
        frame = frame.f_back
    return frame.f_code.co_name


@contextlib.contextmanager
def transaction(self, **kwargs):
    global count
    top = armed and kwargs.get("immediate", True) and not self.connection.in_transaction
    name = caller() if top else ""
    counted = top and (not caller_filter or name == caller_filter)
    with original(self, **kwargs) as conn:
        yield conn
        if counted:
            count += 1
            trace.append(name)
            if count == target:
                # Inside the open write transaction, before its commit.
                os.kill(os.getpid(), signal.SIGKILL)


Database.transaction = transaction
body = (
    Path(__file__).parents[1] / "fixtures/sources/eepro-archive/freedomswing2019.html"
).read_bytes()


def handler(request):
    if fault == "http" and request.url.path != "/robots.txt":
        # The request's admission and debit committed; the response never comes.
        os.kill(os.getpid(), signal.SIGKILL)
    # The Wayback capture parses; an origin page is gone (as the tests serve it).
    if request.url.host == "web.archive.org" and request.url.path != "/robots.txt":
        return httpx.Response(200, content=body)
    return httpx.Response(404)


if poison:
    import swingset.project

    def project(database, unit, _bundle, clock, run_id, selection=None):
        if unit.unit_id == poison:
            os.kill(os.getpid(), signal.SIGKILL)
        derivations.complete(
            database.connection, selection, rows=(), now=clock.now(), run_id=run_id
        )
        database.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

    swingset.project.process_unit = project
    if fault.startswith("capture:"):
        real_capture = derivations.capture

        def capture(conn, unit, **kwargs):
            if unit.unit_id == poison:
                os.kill(os.getpid(), signal.SIGKILL)
            return real_capture(conn, unit, **kwargs)

        derivations.capture = capture


runs = {"acquire": acquire.run, "plan": plan.run, "derive": derive.run, "maintain": maintain.run}
with tick(
    loop,
    state=Path(state),
    config_dir=Path(config),
    overrides_dir=Path(overrides),
    clock=FakeClock(datetime.fromisoformat(now)),
    budget=600,
    transport=httpx.MockTransport(handler),
) as current:
    armed = True
    if loop == "release":
        release.run(current, disk_file=Path(state).parent / "no-disk-quota")
    else:
        runs[loop](current)
    armed = False
print(json.dumps({"writes": trace, "record": current.record}, default=str), flush=True)
