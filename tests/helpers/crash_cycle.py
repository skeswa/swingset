"""Subprocess-only crash/SIGTERM harness; no fault injection ships in runtime."""

import contextlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import httpx

from swingset.clock import FakeClock
from swingset.schedule.cycle import run_cycle
from swingset.state.db import Database, open_database

state = Path(sys.argv[1])
overrides = Path(sys.argv[2])
fault = sys.argv[3]
original = Database.transaction
count = 0
stopped = False


@contextlib.contextmanager
def transaction(self, **kwargs):
    global count
    count += 1
    boundary = count
    if fault == f"before:{boundary}":
        os._exit(91)
    with original(self, **kwargs) as conn:
        yield conn
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
        config_dir=Path("config"),
        overrides_dir=overrides,
        clock=clock,
        transport=httpx.MockTransport(handler),
        should_stop=lambda: stopped,
    )
    print(json.dumps({"transactions": count, "stopped": result["stopped"]}), flush=True)
