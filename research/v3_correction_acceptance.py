"""Verify a correction-only candidate against its acknowledged published baseline."""

import argparse
import json
from pathlib import Path

import duckdb

from swingset.build.schema import PRIMARY_KEYS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    conn = duckdb.connect()
    for prefix, directory in (("old", args.baseline), ("new", args.candidate)):
        for table in PRIMARY_KEYS:
            path = str(directory / "data" / table / "*.parquet").replace("'", "''")
            conn.execute(
                f"CREATE VIEW {prefix}_{table} AS SELECT * FROM read_parquet('{path}', hive_partitioning=false)"
            )
    checks = {}
    mutable = {
        "entries": {"wsdc_id", "link_status", "link_confidence"},
        "judges": {"wsdc_id"},
        "placements": {
            "leader_wsdc_id",
            "follower_wsdc_id",
            "registry_points_leader",
            "registry_points_follower",
            "registry_confirmed",
            "points_matches_expected",
        },
        "link_candidates": {"chosen"},
    }
    for table in PRIMARY_KEYS:
        if table in {"identity_links", "changelog"}:
            continue
        columns = [
            row[0]
            for row in conn.execute(f"DESCRIBE old_{table}").fetchall()
            if row[0] not in mutable.get(table, set())
        ]
        selected = ",".join('"' + name + '"' for name in columns)
        changes = conn.execute(
            f"SELECT COUNT(*) FROM ((SELECT {selected} FROM old_{table} EXCEPT ALL SELECT {selected} FROM new_{table}) UNION ALL (SELECT {selected} FROM new_{table} EXCEPT ALL SELECT {selected} FROM old_{table}))"
        ).fetchone()[0]
        checks[f"{table}_source_facts_unchanged"] = changes == 0
    counts = {}
    for table, key in (("entries", "entry_id"), ("judges", "judge_id")):
        introduced = conn.execute(
            f"SELECT COUNT(*) FROM new_{table} n LEFT JOIN old_{table} o USING({key}) WHERE n.wsdc_id IS NOT NULL AND n.wsdc_id IS DISTINCT FROM o.wsdc_id"
        ).fetchone()[0]
        checks[f"{table}_no_new_default_ids"] = introduced == 0
        for prefix in ("old", "new"):
            counts[f"{prefix}_{table}_default_ids"] = conn.execute(
                f"SELECT COUNT(*) FROM {prefix}_{table} WHERE wsdc_id IS NOT NULL"
            ).fetchone()[0]
        kind = "entry" if table == "entries" else "judge"
        unsupported = conn.execute(
            f"SELECT COUNT(*) FROM new_{table} n LEFT JOIN new_identity_links i ON i.subject_kind='{kind}' AND i.subject_id=n.{key} WHERE n.wsdc_id IS NOT NULL AND (i.status IS DISTINCT FROM 'confirmed' OR i.wsdc_id IS DISTINCT FROM n.wsdc_id OR i.acceptance_state IS DISTINCT FROM 'accepted' OR len(i.source_ref_ids)=0)"
        ).fetchone()[0]
        checks[f"{table}_defaults_have_accepted_assertions"] = unsupported == 0
    manifest = json.loads((args.candidate / "_meta/manifest.json").read_bytes())
    policy = manifest["release_policy"]
    checks["correction_only"] = policy["mode"] == "correction_only"
    checks["accuracy_expansion_held"] = policy["accuracy_expansions"] == "withheld_pending_H17"
    checks["baseline_matches"] = (
        policy["baseline_commit"]
        == json.loads((args.baseline / "PUBLISHED").read_bytes())["commit"]
    )
    checks["journal_metadata_matches"] = (
        conn.execute(
            "SELECT COUNT(*) FROM new_identity_links WHERE journal_digest IS DISTINCT FROM ? OR journal_generation IS DISTINCT FROM ? OR acceptance_policy IS DISTINCT FROM ?",
            [
                policy["token"]["journal_digest"],
                policy["token"]["journal_generation"],
                policy["token"]["decision_policy"],
            ],
        ).fetchone()[0]
        == 0
    )
    receipt = {
        "baseline": str(args.baseline),
        "candidate": str(args.candidate),
        "checks": checks,
        "counts": counts,
        "release_policy": policy,
        "passed": all(checks.values()),
        "network_requests": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt), flush=True)
    if not receipt["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
