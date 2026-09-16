"""Read one retained SQLite snapshot and emit H14 load evidence; never import runtime.

Example: python journal/tools/runtime/h14_shadow_load.py --state /var/lib/swingset
Use --checkpoint for an immutable checkpoint, never for live WAL state.
Output goes to stdout; redirect it outside the retained state directory.
"""

import argparse
import hashlib
import json
import math
import sqlite3
import time
import tomllib
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse


def rows(conn, query, values=()):
    return [dict(row) for row in conn.execute(query, values)]


def quantiles(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50": None, "p95": None, "maximum": None}
    return {
        "count": len(ordered),
        "p50": ordered[math.ceil(len(ordered) * 0.5) - 1],
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "maximum": ordered[-1],
    }


def collect(state, *, now, checkpoint=False):
    started = time.monotonic()
    database = state / "state.sqlite"
    uri = database.resolve().as_uri() + ("?mode=ro&immutable=1" if checkpoint else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        schema = conn.execute("PRAGMA user_version").fetchone()[0]
        bundle = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]
        accepted = dict(
            conn.execute("SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline'")
        )
        config = {}
        hashes = {}
        for name in ("hosts", "sources"):
            relative = f"config/{name}.toml"
            body = (state / "inputs" / bundle / relative).read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest != accepted[relative]:
                raise ValueError(f"accepted configuration digest differs: {relative}")
            hashes[relative] = digest
            config[name] = tomllib.loads(body.decode())
        sources = config["sources"]["sources"]
        hosts = config["hosts"]["hosts"]
        demand = defaultdict(Counter)
        class_demand = defaultdict(Counter)
        missing_dates = defaultdict(Counter)
        due_ages = defaultdict(list)
        notes = Counter()
        watches = rows(
            conn,
            """SELECT w.*,e.start_date AS event_start,e.end_date AS event_end,
            EXISTS(SELECT 1 FROM snapshots s WHERE s.watch_id=w.watch_id) AS has_snapshot
            FROM watches w LEFT JOIN source_event_map m ON m.source=w.source AND m.source_ref=w.source_ref
            LEFT JOIN events e USING(event_id) ORDER BY w.watch_id""",
        )
        for watch in watches:
            transport = urlparse(watch["archive_url"] or watch["url"])
            network = transport.scheme in {"http", "https"}
            host = (transport.hostname or "unknown") if network else "local_artifact"
            if network:
                hosts.setdefault(
                    host,
                    {
                        "min_gap_seconds": 5,
                        "daily_request_budget": 200,
                        "basis": "default host policy",
                    },
                )
            enabled = sources.get(watch["source"], {}).get("enabled", False)
            due = (
                enabled
                and watch["state"] not in {"gone", "sealed"}
                and watch["next_check_at"] is not None
                and datetime.fromisoformat(watch["next_check_at"]) <= now
            )
            key = (host, watch["source"], watch["kind"], watch["state"])
            demand[key]["retained"] += 1
            demand[key]["due_before_operator_host_budget_gates"] += bool(due)
            demand[key]["unchecked"] += watch["last_checked_at"] is None
            demand[key]["disabled_source"] += not enabled
            demand[key]["via_archive"] += bool(watch["archive_url"])
            if due:
                due_ages[host].append(
                    (now - datetime.fromisoformat(watch["next_check_at"])).total_seconds()
                )
            has_dates = bool(watch["event_start"] and watch["event_end"])
            if watch["source"] != "wsdc_registry" and watch["kind"] != "index" and not has_dates:
                missing_dates[(host, watch["source"], watch["kind"])]["retained"] += 1
                missing_dates[(host, watch["source"], watch["kind"])]["due"] += bool(due)
            note = watch["notes"] or ""
            if watch["source"] == "wsdc_registry":
                notes[note.split(":")[0]] += 1
            category = (
                "local_evidence"
                if not network
                else "newly_discovered"
                if watch["last_checked_at"] is None and not watch["has_snapshot"]
                else "identity_confirmation"
                if watch["source"] == "wsdc_registry" and note.startswith("confirmation:")
                else "registry_discovery"
                if watch["source"] == "wsdc_registry" and note in {"probe", "sweep"}
                else "old_evidence_refresh"
                if watch["source"] == "wsdc_registry"
                or watch["state"] in {"archived", "backfill", "sealed", "gone"}
                else "metadata_recovery"
                if watch["kind"] != "index" and not has_dates
                else "current_event_refresh"
            )
            class_demand[(host, category)]["retained"] += 1
            class_demand[(host, category)]["due"] += bool(due)
        daily = rows(conn, "SELECT * FROM host_budget ORDER BY day,host")
        recent_start = (now - timedelta(days=6)).date().isoformat()
        recent = [row for row in daily if recent_start <= row["day"] <= now.date().isoformat()]
        costs = defaultdict(Counter)
        for row in recent:
            costs[row["host"]]["requests"] += row["requests"]
            costs[row["host"]]["accounted_body_bytes"] += row["bytes"]
            costs[row["host"]]["recorded_host_days"] += 1
        claim_sizes = defaultdict(list)
        for row in conn.execute(
            "SELECT url,archive_url,body_bytes FROM snapshots WHERE body_sha256 IS NOT NULL"
        ):
            transport = urlparse(row["archive_url"] or row["url"])
            host = (
                (transport.hostname or "unknown")
                if transport.scheme in {"http", "https"}
                else "local_artifact"
            )
            claim_sizes[host].append(row["body_bytes"])
        pending = rows(
            conn,
            "SELECT stage,unit_kind,count(*) AS count,min(enqueued_at) AS oldest FROM pending_work GROUP BY stage,unit_kind ORDER BY stage,unit_kind",
        )
        parse_queue = rows(
            conn,
            """SELECT count(*) AS snapshots,coalesce(sum(s.body_bytes),0) AS referenced_body_bytes
            FROM pending_work p JOIN snapshots s ON p.unit_id=s.snapshot_id WHERE p.stage='parse'""",
        )[0]
        parse_queue["distinct_body_bytes"] = conn.execute("""SELECT coalesce(sum(size),0) FROM (
            SELECT s.body_sha256,max(s.body_bytes) AS size FROM pending_work p JOIN snapshots s ON p.unit_id=s.snapshot_id
            WHERE p.stage='parse' AND s.body_sha256 IS NOT NULL GROUP BY s.body_sha256)""").fetchone()[0]
        report = {
            "format": "h14-shadow-load-v1",
            "snapshot_at": now.isoformat(),
            "state": str(state),
            "schema": schema,
            "immutable_checkpoint": checkpoint,
            "input_bundle_hash": bundle,
            "accepted_config_sha256": hashes,
            "runtime_imports": False,
            "source_requests": 0,
            "semantic_writes": 0,
            "operator_hold_file": (state / "operator-hold").exists(),
            "operator_pauses": rows(
                conn, "SELECT * FROM operator_pauses ORDER BY scope_kind,scope_id"
            ),
            "host_pauses": rows(
                conn,
                "SELECT host,paused_until,pause_reason,next_allowed_at FROM hosts ORDER BY host",
            ),
            "host_policy": hosts,
            "enabled_sources": {name: item.get("enabled", False) for name, item in sources.items()},
            "watch_total": len(watches),
            "watch_demand": [
                dict(zip(("host", "source", "kind", "state"), key, strict=True), **value)
                for key, value in sorted(demand.items())
            ],
            "proposed_class_demand": [
                dict(zip(("host", "class"), key, strict=True), **value)
                for key, value in sorted(class_demand.items())
            ],
            "class_basis": "Shadow categories only: local evidence; unchecked without retained snapshot; confirmation note; probe/sweep; registry or archived/backfill/sealed/gone; dateless non-index; remaining current refresh. Not runtime eligibility or an accepted schedule.",
            "dateless_nonregistry_nonindex": [
                dict(zip(("host", "source", "kind"), key, strict=True), **value)
                for key, value in sorted(missing_dates.items())
            ],
            "due_age_seconds": {
                host: quantiles(values) for host, values in sorted(due_ages.items())
            },
            "registry_watch_note_prefixes": dict(notes),
            "host_budget_days": daily,
            "request_cost_window": {
                "first_day": recent_start,
                "last_day": now.date().isoformat(),
                "last_day_partial": True,
            },
            "observed_request_costs": {
                host: dict(
                    value,
                    mean_accounted_bytes_per_request=value["accounted_body_bytes"]
                    / value["requests"]
                    if value["requests"]
                    else None,
                )
                for host, value in sorted(costs.items())
            },
            "retained_claim_body_bytes": {
                host: quantiles(values) for host, values in sorted(claim_sizes.items())
            },
            "pending_work": pending,
            "pending_parse_bytes": parse_queue,
            "work_attempts": rows(
                conn, "SELECT outcome,count(*) AS count FROM work_attempts GROUP BY outcome"
            ),
            "requirements": rows(
                conn,
                "SELECT kind,state,count(*) AS count FROM findings WHERE closed_at IS NULL GROUP BY kind,state ORDER BY kind,state",
            ),
            "archive_queries": rows(
                conn,
                "SELECT source,year,count(*) AS queries,sum(next_page) AS retained_pages,sum(total_pages) AS declared_pages,sum(completed_at IS NOT NULL) AS complete FROM archive_queries GROUP BY source,year ORDER BY source,year",
            ),
            "archive_capture_catalog": rows(
                conn,
                "SELECT source,count(*) AS captures,count(DISTINCT url) AS urls,sum(length) AS declared_capture_bytes FROM archive_captures GROUP BY source",
            ),
            "accepted_history_years": rows(conn, "SELECT * FROM history_acceptance ORDER BY year"),
            "event_inventory": rows(
                conn,
                "SELECT year,coverage_tier,count(*) AS count FROM events GROUP BY year,coverage_tier ORDER BY year,coverage_tier",
            ),
        }
        conn.rollback()
    report["read_seconds"] = time.monotonic() - started
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--checkpoint", action="store_true")
    parser.add_argument("--at", type=datetime.fromisoformat)
    args = parser.parse_args()
    now = args.at or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("--at requires an explicit timezone")
    if (args.state / "checkpoint.json").exists() and not args.checkpoint:
        raise ValueError("checkpoint reads require --checkpoint")
    if args.checkpoint and not (args.state / "checkpoint.json").is_file():
        raise ValueError("--checkpoint requires an immutable checkpoint manifest")
    print(
        json.dumps(
            collect(args.state, now=now, checkpoint=args.checkpoint), indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
