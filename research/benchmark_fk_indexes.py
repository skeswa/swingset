"""Audit FK lookup plans and time rolled-back deletes on a disposable copy."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from swingset.state import db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    state = args.state.resolve()
    if state.is_relative_to("/var/lib/swingset") or state.name != "swingset-h11-review":
        raise SystemExit("requires explicitly named disposable swingset-h11-review state")
    if args.output.exists():
        raise SystemExit("receipt already exists")
    start = perf_counter()
    size_before = (state / "state.sqlite").stat().st_size
    with db.open_database(state) as database:
        receipt = {
            "schema_version": database.schema_version,
            "migration_seconds": perf_counter() - start,
            "state": str(state),
            "network_requests": 0,
            "live_mutations": 0,
            "query_plans": [],
        }
        conn = database.connection
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            groups = {}
            for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
                groups.setdefault(row[0], []).append((row[1], row[3]))
            for group in groups.values():
                columns = [r[1] for r in sorted(group)]
                where = " AND ".join(f"{column}=?" for column in columns)
                plans = [
                    r[3]
                    for r in conn.execute(
                        f"EXPLAIN QUERY PLAN SELECT rowid FROM {table} WHERE {where}",
                        ["example"] * len(columns),
                    )
                ]
                assert all(not plan.startswith("SCAN") for plan in plans), (table, columns, plans)
                receipt["query_plans"].append({"table": table, "columns": columns, "plan": plans})
        targets = [
            r[0]
            for r in conn.execute(
                "SELECT entry_id FROM callback_marks GROUP BY entry_id ORDER BY count(*) DESC LIMIT 20"
            )
        ]

        def counts():
            return {
                table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("entries", "callback_marks", "callbacks", "heats")
            }

        before = counts()
        conn.execute("BEGIN IMMEDIATE")
        start = perf_counter()
        for entry_id in targets:
            conn.execute("DELETE FROM entries WHERE entry_id=?", (entry_id,))
        receipt["delete_20_most_marked_entries_seconds"] = perf_counter() - start
        after = counts()
        conn.execute("ROLLBACK")
        assert counts() == before
        receipt["rolled_back_removed_counts"] = {
            table: before[table] - after[table] for table in before
        }
        receipt["rollback_restored_counts"] = True
        receipt["foreign_key_errors"] = [list(r) for r in conn.execute("PRAGMA foreign_key_check")]
        assert not receipt["foreign_key_errors"]
    receipt["db_bytes_before"] = size_before
    receipt["db_bytes_after"] = (state / "state.sqlite").stat().st_size
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "query_plans"}), flush=True)


if __name__ == "__main__":
    main()
