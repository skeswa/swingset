"""Take, refresh, or release the one agent-session lock for this checkout.

One agent session works in this checkout at a time. The lock is a small
file under the ignored ``tmp/`` directory holding a label and a UTC
timestamp. ``start`` refuses while another label holds a lock younger than
``STALE_HOURS``; rerunning ``start`` with your own label refreshes it.
``end`` releases it. Pass ``--force`` only after confirming the other
session has stopped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

STALE_HOURS = 6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "end", "status"])
    parser.add_argument(
        "--label",
        default=os.environ.get("AGENT_SESSION_LABEL") or os.environ.get("USER") or "agent",
        help="who is working: for example codex, claude, or your name",
    )
    parser.add_argument(
        "--force", action="store_true", help="replace a live lock after its holder has stopped"
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    lock = root / "tmp" / "agent-session.lock"
    now = dt.datetime.now(dt.timezone.utc)  # noqa: UP017 (mise runs the system python).replace(microsecond=0)

    holder: tuple[str, dt.datetime] | None = None
    if lock.is_file():
        label, _, stamp = lock.read_text().strip().partition(" ")
        try:
            holder = (label, dt.datetime.fromisoformat(stamp))
        except ValueError:
            holder = (label or "unknown", now - dt.timedelta(hours=STALE_HOURS))

    if args.action == "status":
        if holder is None:
            print("no agent session lock")
        else:
            print(f"{holder[0]} since {holder[1].isoformat()} ({now - holder[1]} ago)")
        return

    if args.action == "end":
        if lock.is_file():
            lock.unlink()
            print("released the agent session lock")
        else:
            print("no agent session lock to release")
        return

    if holder is not None:
        label, taken = holder
        live = now - taken < dt.timedelta(hours=STALE_HOURS)
        if live and label != args.label and not args.force:
            print(
                f"FAIL: {label} has held this checkout since {taken.isoformat()} "
                f"({now - taken} ago). Wait for that session to end, or pass --force "
                "after confirming it has stopped."
            )
            sys.exit(1)
    lock.parent.mkdir(exist_ok=True)
    lock.write_text(f"{args.label} {now.isoformat()}\n")
    print(f"agent session lock: {args.label} at {now.isoformat()}")


if __name__ == "__main__":
    main()
