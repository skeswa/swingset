"""Read-only postmigration verification; run with explicit production Python -B."""
import gzip
import hashlib
import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

STATE = Path("/var/lib/swingset")
RUN = STATE / "operations/v2-continuation-20260917/rollout-001"
SOURCE = Path("/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source")
SYSTEM = "/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
CANDIDATE = "cand_8f31cad7226643ae"
BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
checks = {}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)

def check(name, condition):
    checks[name] = bool(condition)

receipt_path = RUN / "migration-receipt.json"
receipt_hash = sha(receipt_path)
receipt = read(receipt_path)
gate_path = RUN / "migration-gate.json"
gate = read(gate_path)
check("closed_migration_passed", receipt.get("passed") is True and receipt.get("executed") is True and receipt.get("preflight_passed") is True and bool(receipt.get("finished_at")))
check("receipt_pin", receipt_hash == "9ed991d1389d36d36aa541f45fcd3bcdd2367b18206a429a357d62f079e8b636")
check("schema_transition", (receipt["old_schema"], receipt["target_schema"], receipt["schema"]) == (14, 28, 28))
check("all_71_predecessor_tables_preserved", len(receipt["before"]) == 71 and all(row == receipt["after"].get(name) for name, row in receipt["before"].items()))
check("gate_and_helper_binding", sha(gate_path) == receipt["gate_sha256"] and sha(RUN / "accept.py") == receipt["helper_sha256"] == gate["helper_sha256"])
check("source_receipt_binding", receipt["source"] == str(SOURCE) and sha(SOURCE / "extension-source.json") == receipt["source_receipt_sha256"] == "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6")
source_inventory = read(SOURCE / "extension-source.json")["files"]
check("schema_module_binding", sha(SOURCE / "src/swingset/state/db.py") == source_inventory["src/swingset/state/db.py"])
evidence = {}
for name, reference in gate["evidence"].items():
    path = Path(reference["path"])
    if not path.is_absolute():
        path = RUN / path
    check("evidence_hash_" + name, sha(path) == reference["sha256"])
    if name not in {"pytest_log", "ruff_log", "mypy_log"}:
        evidence[name] = read(path)
        check("evidence_passed_" + name, evidence[name].get("passed") is True)
for name in ("migration", "restore"):
    check("predecessor_bound_to_" + name, evidence[name]["before"] == receipt["before"] and evidence[name]["after"] == receipt["after"])
check("migration_performed_no_other_operations", receipt["source_requests"] == receipt["public_writes"] == 0 and all(receipt[key] is False for key in ("input_acceptance", "workers_started", "spacing_baselines_established", "repairs_activated")))

def guards():
    return {
        "systems": {name: str(Path(name).resolve(strict=True)) for name in ("/run/current-system", "/nix/var/nix/profiles/system")},
        "units": {f"swingset-{kind}.{suffix}": subprocess.check_output(["systemctl", "show", f"swingset-{kind}.{suffix}", "--property=ActiveState", "--value"], text=True).strip() for kind in ("cycle", "backup", "summary") for suffix in ("service", "timer")},
        "operator_hold_sha256": sha(STATE / "operator-hold"),
    }

before_guards = guards()
check("guarded_before", before_guards == receipt["guards_before"] and all(value == SYSTEM for value in before_guards["systems"].values()) and all(value == "inactive" for value in before_guards["units"].values()) and not (STATE / "RESTORE_PENDING").exists())

def table_receipt(conn, name):
    columns = list(conn.execute('PRAGMA table_info("' + name + '")'))
    primary = sorted((row[5], row[1]) for row in columns if row[5])
    query = 'SELECT * FROM "' + name + '"'
    if name == "meta":
        query += " WHERE key!='schema_version'"
    query += " ORDER BY " + (",".join('"' + name + '"' for _, name in primary) or "rowid")
    count, digest = 0, hashlib.sha256()
    for row in conn.execute(query):
        raw = json.dumps(list(row), separators=(",", ":"), ensure_ascii=True, default=lambda value: {"bytes": value.hex()} if isinstance(value, bytes) else str(value)).encode()
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        count += 1
    return {"rows": count, "sha256": digest.hexdigest(), "columns": [row[1] for row in columns]}

with sqlite3.connect((STATE / "state.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    check("live_schema_28", conn.execute("PRAGMA user_version").fetchone()[0] == 28 and conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "28")
    actual_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    check("live_table_inventory", actual_tables == set(receipt["after"]))
    selected = {name: table_receipt(conn, name) for name in ("meta", "hosts", "host_budget", "operator_pauses", "control_state", "control_events", "execution_admissions")}
    for name, actual in selected.items():
        check("live_preserved_" + name, actual == receipt["after"][name])
    check("no_unsettled_admissions", conn.execute("SELECT 1 FROM execution_admissions WHERE state!='settled' LIMIT 1").fetchone() is None)
    archive_usage = conn.execute("SELECT day,requests,bytes FROM host_budget WHERE host='web.archive.org' ORDER BY day DESC LIMIT 1").fetchone()
    check("archive_paid_usage_preserved", archive_usage == ("2026-09-17", 10, 2934701))
    spacing_baselines = conn.execute("SELECT count(*) FROM host_request_spacing_baselines").fetchone()[0]
    check("no_spacing_baseline_applied", spacing_baselines == 0)
    check("read_connection_made_no_changes", conn.total_changes == 0)

baseline = STATE / "baseline"
published_path = baseline / "PUBLISHED"
published = read(published_path)
check("acknowledged_baseline", baseline.is_symlink() and baseline.resolve(strict=True) == STATE / "candidates" / CANDIDATE and published.get("candidate_id") == CANDIDATE and published.get("commit") == BASELINE and bool(published.get("verified_at")))
checkpoint = Path(gate["checkpoint"]["path"])
manifest = read(checkpoint / "checkpoint.json")
check("checkpoint_manifest_pin", sha(checkpoint / "checkpoint.json") == receipt["checkpoint_sha256"] == gate["checkpoint"]["manifest_sha256"])
check("baseline_ack_file_preserved", sha(published_path) == manifest["files"][f"candidates/{CANDIDATE}/PUBLISHED"]["sha256"])
check("guarded_after", guards() == before_guards)
check("migration_receipt_unchanged", sha(receipt_path) == receipt_hash)
print(json.dumps({"format": "event-extension-independent-postmigration-v1", "finished_at": datetime.now(UTC).isoformat(), "passed": all(checks.values()), "checks": checks, "receipt_sha256": receipt_hash, "source": str(SOURCE), "schema": 28, "predecessor_table_count": len(receipt["before"]), "current_table_count": len(receipt["after"]), "guards": before_guards, "targeted_table_receipts": selected, "archive_latest_paid_usage": list(archive_usage), "baseline_candidate": CANDIDATE, "baseline_commit": BASELINE, "source_requests": 0, "production_writes": 0, "limits": ["All 71 predecessor table preservation is independently compared from pinned closed receipt inventories; only seven critical live tables are freshly rehashed.", "Acknowledged local public baseline checked; no network remote-head query performed.", "No source fetch, migration, input acceptance, spacing baseline or worker activation performed."]}, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
