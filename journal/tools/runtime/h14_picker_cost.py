"""Read-only retained-demand picker timing; older schemas use empty TEMP counters."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from swingset.config import load_config
from swingset.schedule import fairness
from swingset.schedule.fairness import next_watch


def measure(state: Path, *, checkpoint: bool = False) -> dict[str, object]:
    path = state / "state.sqlite"
    conn = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro" + ("&immutable=1" if checkpoint else ""), uri=True
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        schema = int(
            conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        )
        bundle = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]
        config = load_config(state / "inputs" / bundle / "config")
        if schema < 13:
            conn.execute(
                "CREATE TEMP TABLE scheduler_requests(host TEXT,category TEXT,day TEXT,issued_at TEXT)"
            )
        now = datetime.now(UTC)
        began = time.monotonic()
        # Bound the timing experiment even if an unexpected query regresses.
        conn.set_progress_handler(lambda: int(time.monotonic() - began > 30), 10000)
        choice = next_watch(conn, config, now=now)
        elapsed = time.monotonic() - began
        return {
            "at": now.isoformat(),
            "state": str(state),
            "schema": schema,
            "mode": "readonly_retained_demand_empty_temp_attribution"
            if schema < 13
            else "readonly_current_policy",
            "input_bundle": bundle,
            "seconds": elapsed,
            "selection": None
            if choice is None
            else {"key": choice.key, "host": choice.host, "category": choice.category},
            "watch_count": conn.execute("SELECT count(*) FROM watches").fetchone()[0],
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "policy_sha256": hashlib.sha256(Path(fairness.__file__).read_bytes()).hexdigest(),
            "limitations": "Single current-state timing; no requests or main-database writes. Does not establish throughput or service-gap performance.",
        }
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--checkpoint", action="store_true")
    args = parser.parse_args()
    print(json.dumps(measure(args.state, checkpoint=args.checkpoint), sort_keys=True, indent=2))
