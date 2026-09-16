"""Read-only V2/V4 candidate acceptance against an acknowledged V3 baseline.

This checks a completed bootstrap and candidate. It never migrates, projects,
accepts a year, fetches, or publishes. A failed check produces a nonzero exit.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

from swingset.build.files import sha256_file
from swingset.build.identity_policy import correction_token
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
from swingset.publish.safety import StaleCandidateError, _check_files

CORPUS4 = "7c944ac81361b0a343e34a29ead875b3fe6363f7750d41fda47ddec5bb400c71"
CORPUS5 = "6750eccd884a572c00a2f525d719a8c434b1dfdea73367558c0cb490e63c7755"
IMPACT_SHA256 = "3e527751b4053c0aa667b2d7c523db9caf6b9b13584fa3124cb43131572d5ba2"
EXPECTED_GATED = {
    "9dd8e5e2d7b4929a",
    "4206caf83c5140e6",
    "29cb5a5de573eac0",
    "dc43d2a1264754cf",
    "15e48c4d227845a8",
    "df693417d9a0ab77",
    "5f26ff699e205d06",
}
POLICIES = {
    kind: "4"
    for kind in (
        "wsdc_registry.dancer",
        "eepro.index",
        "eepro.round",
        "scoringdance.sitemap",
        "scoringdance.recent",
        "scoringdance.event",
        "scoringdance.round",
        "wdr.rounds",
    )
} | {"eepro.autoindex": "5"}


def scalar(conn: duckdb.DuckDBPyConnection, query: str, parameters: list[Any] | None = None) -> int:
    row = conn.execute(query, parameters or []).fetchone()
    assert row is not None
    return int(row[0])


def load_candidate(conn: duckdb.DuckDBPyConnection, prefix: str, directory: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((directory / "_meta/manifest.json").read_bytes())
    _check_files(directory, json.loads((directory / "BUILT").read_bytes()), manifest)
    for table, schema in SCHEMAS.items():
        paths = sorted((directory / "data" / table).glob("*.parquet"))
        if not paths or any(not pq.read_schema(path).equals(schema) for path in paths):
            raise ValueError(f"Missing or changed {prefix} {table} schema")
        glob = str(directory / "data" / table / "*.parquet").replace("'", "''")
        conn.execute(
            f"CREATE VIEW {prefix}_{table} AS SELECT * FROM read_parquet('{glob}',hive_partitioning=false)"
        )
        if scalar(conn, f"SELECT count(*) FROM {prefix}_{table}") != manifest["row_counts"][table]:
            raise ValueError(f"Manifest row count differs: {prefix} {table}")
    return manifest


def audit_structure(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    duplicates = {}
    for table, keys in PRIMARY_KEYS.items():
        columns = ",".join('"' + key + '"' for key in keys)
        duplicates[table] = scalar(
            conn,
            f"SELECT count(*) FROM (SELECT {columns} FROM new_{table} GROUP BY {columns} HAVING count(*)>1)",
        )
    links = (
        ("contests", "event_id", "events", "event_id"),
        ("rounds", "contest_id", "contests", "contest_id"),
        ("entries", "contest_id", "contests", "contest_id"),
        ("entries", "event_id", "events", "event_id"),
        ("entries", "partner_entry_id", "entries", "entry_id"),
        ("judges", "event_id", "events", "event_id"),
        ("heats", "round_id", "rounds", "round_id"),
        ("heats", "entry_id", "entries", "entry_id"),
        ("callback_marks", "round_id", "rounds", "round_id"),
        ("callback_marks", "entry_id", "entries", "entry_id"),
        ("callback_marks", "judge_id", "judges", "judge_id"),
        ("callbacks", "round_id", "rounds", "round_id"),
        ("callbacks", "entry_id", "entries", "entry_id"),
        ("placements", "round_id", "rounds", "round_id"),
        ("placements", "contest_id", "contests", "contest_id"),
        ("placements", "event_id", "events", "event_id"),
        ("placements", "leader_entry_id", "entries", "entry_id"),
        ("placements", "follower_entry_id", "entries", "entry_id"),
        ("placements", "couple_entry_id", "entries", "entry_id"),
        ("final_marks", "round_id", "rounds", "round_id"),
        ("final_marks", "placement_id", "placements", "placement_id"),
        ("final_marks", "judge_id", "judges", "judge_id"),
        ("registry_placements", "event_id", "events", "event_id"),
        ("registry_placements", "wsdc_id", "dancers", "wsdc_id"),
    )
    missing = {
        f"{child}.{column}": scalar(
            conn,
            f"SELECT count(*) FROM new_{child} c LEFT JOIN new_{parent} p ON c.{column}=p.{key} WHERE c.{column} IS NOT NULL AND p.{key} IS NULL",
        )
        for child, column, parent, key in links
    }
    for table, schema in SCHEMAS.items():
        if table != "snapshots" and "snapshot_id" in schema.names:
            missing[table + ".snapshot_id"] = scalar(
                conn,
                f"SELECT count(*) FROM new_{table} t LEFT JOIN new_snapshots s USING(snapshot_id) WHERE t.snapshot_id IS NULL OR (t.snapshot_id!='override' AND s.snapshot_id IS NULL)",
            )
    return {
        "checks": {
            "unique_keys": not any(duplicates.values()),
            "no_orphans": not any(missing.values()),
        },
        "details": {"duplicates": duplicates, "missing_references": missing},
    }


def audit_unsupported(conn: duckdb.DuckDBPyConnection, impact: dict[str, Any]) -> dict[str, Any]:
    expected = {row["contest_id"] for row in impact["newly_unsupported"]}
    conn.execute("CREATE TEMP TABLE expected_unsupported(contest_id VARCHAR PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO expected_unsupported VALUES (?)",
        [(identifier,) for identifier in sorted(expected)],
    )
    actual = {
        row[0]
        for row in conn.execute(
            "SELECT n.contest_id FROM new_contests n JOIN old_contests o USING(contest_id) WHERE n.parse_status='unsupported' AND o.parse_status!='unsupported'"
        ).fetchall()
    }
    missing = scalar(
        conn,
        "SELECT count(*) FROM expected_unsupported x LEFT JOIN new_contests c USING(contest_id) WHERE c.parse_status IS DISTINCT FROM 'unsupported'",
    )
    before, remaining = {}, {}
    for table in ("rounds", "entries", "placements"):
        before[table] = scalar(
            conn, f"SELECT count(*) FROM old_{table} JOIN expected_unsupported USING(contest_id)"
        )
        remaining[table] = scalar(
            conn, f"SELECT count(*) FROM new_{table} JOIN expected_unsupported USING(contest_id)"
        )
    for table in ("callbacks", "callback_marks", "final_marks"):
        for prefix, result in (("old", before), ("new", remaining)):
            result[table] = scalar(
                conn,
                f"SELECT count(*) FROM {prefix}_{table} t JOIN {prefix}_rounds r USING(round_id) JOIN expected_unsupported x USING(contest_id)",
            )
    expected_counts = impact["prior_rows_in_newly_unsupported_contests"]
    return {
        "checks": {
            "reviewed_67_contests": len(expected) == 67 and len(impact["newly_unsupported"]) == 67,
            "exact_unsupported_transition": actual == expected and missing == 0,
            "baseline_impact_matches": before == expected_counts and before["final_marks"] == 683,
            "unsupported_results_withheld": not any(remaining.values()),
        },
        "details": {
            "missing_or_still_parsed": missing,
            "unreviewed_transitions": sorted(actual - expected),
            "expected_transitions_missing": sorted(expected - actual),
            "baseline_rows": before,
            "remaining_rows": remaining,
        },
    }


def audit_policies(state: sqlite3.Connection, receipt: dict[str, Any]) -> dict[str, Any]:
    policies = {
        r["page_kind"]: dict(r)
        for r in state.execute("SELECT * FROM admission_policies WHERE mode='enforce'")
    }
    failures = []
    for kind, version in POLICIES.items():
        row = policies.get(kind, {})
        review = state.execute(
            "SELECT * FROM admission_reviews WHERE report_digest=?",
            (row.get("reviewed_report_digest"),),
        ).fetchone()
        if review is None:
            failures.append(kind + ":review_missing")
            continue
        cohort = json.loads(review["cohort_json"])
        if (
            row.get("contract_version") != version
            or review["contract_version"] != version
            or review["page_kind"] != kind
            or not review["reviewer"]
            or not review["evidence"]
            or not isinstance(cohort, dict)
            or cohort.get("external_corpus") != (CORPUS5 if version == "5" else CORPUS4)
            or receipt.get("policies", {}).get(kind) != review["report_digest"]
        ):
            failures.append(kind + ":review_mismatch")
    return {
        "checks": {
            "exact_nine_enforced_contracts": set(policies) == set(POLICIES),
            "exact_review_receipts": not failures,
        },
        "details": {
            "failures": failures,
            "versions": {k: v["contract_version"] for k, v in policies.items()},
        },
    }


def audit_acquisition(
    state: sqlite3.Connection,
    checkpoint: sqlite3.Connection,
    receipt: dict[str, Any],
    phase1: dict[str, Any],
) -> dict[str, Any]:
    ignored = {"current_observation_snapshot_id", "fingerprint", "extract_version"}

    def controls(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        return {
            r["watch_id"]: {k: r[k] for k in r.keys() if k not in ignored}
            for r in conn.execute("SELECT * FROM watches WHERE kind!='index'")
        }

    before, after = controls(checkpoint), controls(state)
    new, lost = sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    prior_snapshots = {r[0] for r in checkpoint.execute("SELECT snapshot_id FROM snapshots")}
    new_snapshots = {
        r[0] for r in state.execute("SELECT snapshot_id FROM snapshots")
    } - prior_snapshots
    permitted = {row["snapshot_id"] for row in phase1["snapshots"]}
    gated = set(receipt.get("stages", {}).get("parse", {}).get("gated_new_rounds", []))
    missing_gates = []
    gate_findings = []
    for identifier in sorted(gated):
        row = state.execute(
            "SELECT finding_id,evidence_json FROM findings WHERE owner_kind='acquisition_gate' AND owner_id=? AND closed_at IS NULL",
            (identifier,),
        ).fetchone()
        if row is None:
            missing_gates.append(identifier)
            continue
        evidence = json.loads(row[1])
        gate_findings.append(row[0])
        if (
            evidence.get("intended_watch_id") != identifier
            or not evidence.get("url")
            or not evidence.get("source_event_ref")
            or set(evidence.get("required_gates", [])) != {"G2", "G3"}
            or state.execute("SELECT 1 FROM watches WHERE watch_id=?", (identifier,)).fetchone()
        ):
            missing_gates.append(identifier)
    return {
        "checks": {
            "acquisition_controls_preserved": not new and not lost and not changed,
            "only_phase1_snapshots_imported": new_snapshots <= permitted,
            "driver_reports_zero_acquisition": receipt.get("new_acquisition_watches") == 0
            and receipt.get("network_requests") == 0,
            "gated_intents_retained_without_controls": not missing_gates,
        },
        "details": {
            "new_controls": new,
            "lost_controls": lost,
            "changed_controls": changed,
            "unexpected_snapshots": sorted(new_snapshots - permitted),
            "new_snapshots": len(new_snapshots),
            "gated_watch_ids": sorted(gated),
            "gate_finding_ids": gate_findings,
            "missing_gate_evidence": missing_gates,
        },
    }


def audit_admission_outputs(
    conn: duckdb.DuckDBPyConnection, state: sqlite3.Connection, checkpoint: sqlite3.Connection
) -> dict[str, Any]:
    invalid_pointers = [
        row[0]
        for row in state.execute("""
        SELECT u.unit_key FROM source_units u
        LEFT JOIN source_generations g ON g.generation_id=u.accepted_generation_id
        LEFT JOIN admission_policies p ON p.page_kind=u.page_kind
        WHERE u.accepted_generation_id IS NOT NULL AND
        (g.generation_id IS NULL OR g.unit_key!=u.unit_key OR g.state!='accepted'
         OR g.contract_version IS NOT p.contract_version
         OR coalesce(json_array_length(g.report_json,'$.failures'),1)!=0)
    """)
    ]
    guarded = list(
        state.execute("""
        SELECT u.unit_key,u.watch_id,u.legacy_snapshot_id,u.legacy_state,g.report_json,
               json_extract(g.recipe_json,'$.context.snapshot_id') AS snapshot_id
        FROM source_units u JOIN admission_policies p ON p.page_kind=u.page_kind
        JOIN source_generations g ON g.generation_id=(
            SELECT z.generation_id FROM source_generations z WHERE z.unit_key=u.unit_key
            AND z.input_fingerprint=u.desired_fingerprint
            ORDER BY z.created_at DESC,z.generation_id DESC LIMIT 1)
        WHERE p.mode='enforce' AND g.state IN ('needs_review','waiting_for_inputs')
    """)
    )
    missing_findings, changed_legacy = [], []
    for row in guarded:
        findings = list(
            state.execute(
                """SELECT finding_id,kind,summary FROM findings
            WHERE closed_at IS NULL AND
            ((kind='admission_blocked' AND owner_kind='admission' AND owner_id=?)
             OR (kind='parse_failure' AND snapshot_id=?))""",
                (row["unit_key"], row["snapshot_id"]),
            )
        )
        visible = False
        for finding in findings:
            public = conn.execute(
                "SELECT summary FROM new_review_queue WHERE item_id=? AND kind=?",
                [finding[0], finding[1]],
            ).fetchone()
            failures = json.loads(row["report_json"])["failures"]
            if (
                public is not None
                and public[0] == finding[2]
                and (finding[1] == "parse_failure" or all(code in public[0] for code in failures))
            ):
                visible = True
        if not visible:
            missing_findings.append(row["unit_key"])
        if row["legacy_state"] == "legacy_unassessed" and row["legacy_snapshot_id"]:
            query = "SELECT kind,scope_kind,scope_id,seq,extract_version,parser_version,payload_json FROM observations WHERE watch_id=? AND snapshot_id=? ORDER BY kind,seq"
            parameters = (row["watch_id"], row["legacy_snapshot_id"])
            before = [tuple(r) for r in checkpoint.execute(query, parameters)]
            after = [tuple(r) for r in state.execute(query, parameters)]
            if before != after:
                changed_legacy.append(row["unit_key"])
    return {
        "checks": {
            "accepted_pointers_resolve_passing_contracts": not invalid_pointers,
            "guarded_current_units_have_public_reasons": not missing_findings,
            "failed_legacy_observations_preserved": not changed_legacy,
        },
        "details": {
            "invalid_accepted_units": invalid_pointers,
            "guarded_current_units": len(guarded),
            "missing_public_guard_findings": missing_findings,
            "changed_failed_legacy_units": changed_legacy,
        },
    }


def audit(args: argparse.Namespace) -> dict[str, Any]:
    from journal.tools.releases.v4_history_checks import audit_history
    from journal.tools.releases.v4_identity_checks import audit_identity

    receipt = json.loads(args.bootstrap_receipt.read_bytes())
    phase1 = json.loads((args.phase1 / "phase1-export.json").read_bytes())
    impact = json.loads(args.impact.read_bytes())
    published = json.loads((args.baseline / "PUBLISHED").read_bytes())
    conn = duckdb.connect(config={"memory_limit": "512MB", "threads": "2"})
    state = sqlite3.connect(f"file:{args.state / 'state.sqlite'}?mode=ro", uri=True)
    checkpoint = sqlite3.connect(
        f"file:{args.checkpoint_state / 'state.sqlite'}?mode=ro&immutable=1", uri=True
    )
    state.row_factory = checkpoint.row_factory = sqlite3.Row
    state.execute("BEGIN")
    sections = {}
    try:
        load_candidate(conn, "old", args.baseline)
        manifest = load_candidate(conn, "new", args.candidate)
        policy = manifest.get("release_policy", {})
        revisions = dict(state.execute("SELECT name,value FROM revisions"))
        bundle = state.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
        sections["seal"] = {
            "checks": {
                "published_v3_parent": published["commit"]
                == args.expected_parent
                == policy.get("baseline_commit")
                == manifest.get("expected_parent"),
                "settled_release": policy.get("mode") == "settled"
                and not state.execute("SELECT 1 FROM pending_work LIMIT 1").fetchone(),
                "current_policy_token": policy.get("token") == correction_token(state),
                "current_input_and_revisions": bundle is not None
                and policy.get("input_bundle_hash") == bundle[0]
                and policy.get("revisions") == revisions,
                "driver_matches_candidate": bool(receipt.get("finished_at"))
                and receipt.get("build", {}).get("manifest_hash")
                == sha256_file(args.candidate / "_meta/manifest.json")
                and receipt.get("run_id") == manifest.get("run_id")
                and receipt.get("expected_parent") == args.expected_parent,
                "driver_completed_all_stages": set(receipt.get("stages", {}))
                == {"parse", "project", "link"},
                "v4_schema_matches_pin": manifest.get("schema_version") == 1
                and manifest.get("versions", {}).get("schema") == "1"
                and state.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
                == "9",
                "reviewed_projector_version": str(manifest.get("versions", {}).get("projector"))
                == str(args.expected_projector),
                "reviewed_scoring_impact_pinned": sha256_file(args.impact) == IMPACT_SHA256,
                "all_seven_reviewed_intents_gated": set(
                    receipt.get("stages", {}).get("parse", {}).get("gated_new_rounds", [])
                )
                == EXPECTED_GATED,
                "no_state_foreign_key_errors": not state.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall(),
            },
            "details": {
                "manifest_hash": sha256_file(args.candidate / "_meta/manifest.json"),
                "release_policy": policy,
            },
        }
        sections["structure"] = audit_structure(conn)
        sections["admission"] = audit_policies(state, receipt)
        sections["admission_outputs"] = audit_admission_outputs(conn, state, checkpoint)
        sections["acquisition"] = audit_acquisition(state, checkpoint, receipt, phase1)
        gate_ids = sections["acquisition"]["details"]["gate_finding_ids"]
        sections["acquisition"]["checks"]["public_gate_findings"] = all(
            scalar(
                conn,
                "SELECT count(*) FROM new_review_queue WHERE item_id=? AND kind='acquisition_gate'",
                [identifier],
            )
            == 1
            for identifier in gate_ids
        )
        sections["unsupported_scoring"] = audit_unsupported(conn, impact)
        sections["history"] = audit_history(
            conn, state, phase1=args.phase1, expected_years=tuple(range(2010, 2027))
        )
        sections["identity"] = audit_identity(
            conn, release_policy=policy, baseline_commit=args.expected_parent, state=state
        )
    finally:
        state.close()
        checkpoint.close()
        conn.close()
    return {
        "candidate": str(args.candidate),
        "baseline": str(args.baseline),
        "state": str(args.state),
        "checkpoint": str(args.checkpoint_state),
        "sections": sections,
        "passed": all(
            value for section in sections.values() for value in section["checks"].values()
        ),
        "network_requests": 0,
        "state_mutations": 0,
        "reviewed_identity_precision": "unavailable; H17 human adjudication remains pending",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "baseline",
        "candidate",
        "state",
        "checkpoint-state",
        "bootstrap-receipt",
        "phase1",
        "impact",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-parent", required=True)
    parser.add_argument("--expected-projector", default="19")
    args = parser.parse_args()
    if any(
        args.output.resolve().is_relative_to(path.resolve())
        for path in (args.candidate, args.baseline)
    ):
        raise ValueError("Audit output must be outside immutable candidate/baseline directories")
    try:
        result = audit(args)
    except (ValueError, OSError, KeyError, sqlite3.Error, duckdb.Error, StaleCandidateError) as exc:
        result = {
            "passed": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "network_requests": 0,
            "state_mutations": 0,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "output": str(args.output),
                "failed_checks": [
                    f"{name}.{key}"
                    for name, section in result.get("sections", {}).items()
                    for key, value in section["checks"].items()
                    if not value
                ],
                "error": result.get("error"),
            }
        ),
        flush=True,
    )
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
