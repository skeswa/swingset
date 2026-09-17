from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
EVIDENCE = ROOT / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17"
PACKET_DIR = EVIDENCE / "packet-004"
PACKET_PATH = PACKET_DIR / "packet.json"
RECEIPT_PATH = EVIDENCE / "restore-002/restore-receipt.json"
OBSERVATION_PATH = EVIDENCE / "actual-review-004/restore-observation.json"
OBSERVATION_SCRIPT = EVIDENCE / "actual-review-004/restore-observation.py"
OUTPUT_PATH = Path(__file__).with_name("restore-audit.json")
PACKET_SHA256 = "366b0dbab70048cc2b85c4bdb7e06d24973478d8cfee965ee01ebb40d7fb5300"
RECEIPT_SHA256 = "eb3b33e1b72213b6cbee47b87586ad440b8bfaa841f073371c526ba72747ef97"
SOURCE = "/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source"
SOURCE_RECEIPT_SHA256 = "9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99"
CHECKPOINT_SHA256 = "6ac7bb2d291136ef502abcb33f8762bf4449fd2fdc0160fb995f2aba0921f9d9"
ARCHIVE_COMMIT = "68d738dcd78cb1654d053b479ea6dbd911f4b8ed"
BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
TARGET_TABLE = "history_dispatch_fence"
PRESSURE_TABLE = "event_pressure_state"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


packet = json.loads(PACKET_PATH.read_text())
receipt = json.loads(RECEIPT_PATH.read_text())
observation = json.loads(OBSERVATION_PATH.read_text())
require(sha(RECEIPT_PATH) == RECEIPT_SHA256, "restore receipt digest changed")
require(packet.get("checkpoint_sha256") == CHECKPOINT_SHA256, "packet checkpoint differs")
require(packet.get("archive_commit") == ARCHIVE_COMMIT, "packet archive commit differs")
require(packet.get("source") == SOURCE, "packet source differs")
require(packet.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256, "packet source receipt differs")
for name, digest in packet["files"].items():
    require(sha(PACKET_DIR / name) == digest, f"packet helper changed: {name}")
require(sha(PACKET_DIR / "packet.json") != "", "packet metadata unreadable")
prior = json.loads((EVIDENCE / "packet004-independent-review-001/explicit-review-002/receipt.json").read_text())
require(prior.get("passed") is True and prior.get("packet_sha256") == PACKET_SHA256, "packet review differs")

require(receipt.get("passed") is True, "restore rehearsal did not pass")
require(receipt.get("format") == "extension-operational-restore-rehearsal-v1", "restore receipt format differs")
require(receipt.get("stage") == "finished_held_without_input_acceptance", "restore did not close held")
require(receipt.get("source") == SOURCE and receipt.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256, "restore source differs")
require(receipt.get("helper_sha256") == packet["files"]["restore.py"], "restore helper differs from packet")
require(receipt.get("checkpoint") == packet["checkpoint"] and receipt.get("checkpoint_sha256") == CHECKPOINT_SHA256, "restore checkpoint differs")
require(receipt.get("acknowledged_archive_commit") == ARCHIVE_COMMIT, "restore archive acknowledgment differs")
require(receipt.get("baseline") == BASELINE, "restore public baseline differs")
require(receipt.get("target_schema") == 29 and receipt.get("schema") == 29, "restore target schema differs")
require(receipt.get("restore_change_contract") == "event_pressure_state_singleton_epoch_plus_one_only", "restore change contract differs")
require(receipt.get("schema28_activated_under_hold") is True, "restore did not activate schema28 under hold")

before = receipt.get("before")
expected = receipt.get("expected_restored")
after = receipt.get("after")
require(isinstance(before, dict) and isinstance(expected, dict) and isinstance(after, dict), "restore table receipts missing")
require(len(before) == 116 and len(expected) == 116 and len(after) == 117, "restore table counts differ")
require(set(before) == set(expected), "restore predecessor table inventory differs")
require(all(before[name] == expected[name] for name in before if name != PRESSURE_TABLE), "non-pressure predecessor rows changed")
pressure_before = before.get(PRESSURE_TABLE, {})
pressure_expected = expected.get(PRESSURE_TABLE, {})
require(pressure_before.get("rows") == pressure_expected.get("rows") == 1, "pressure singleton row count differs")
require(pressure_before.get("columns") == pressure_expected.get("columns") == ["singleton", "epoch", "sequence"], "pressure singleton columns differ")
require(pressure_before.get("sha256") != pressure_expected.get("sha256"), "pressure epoch did not change")
require(all(expected[name] == after.get(name) for name in expected), "restored state differs after schema migration")
require(set(after) - set(expected) == {TARGET_TABLE}, "restore introduced unexpected table")
require(after[TARGET_TABLE].get("rows") == 1, "dispatch fence row missing")
require(receipt.get("changed_existing_tables") == [], "restore reports changed existing tables")

requests = receipt.get("public_requests")
require(isinstance(requests, list) and 0 < len(requests) <= 256, "public read request ledger missing or over budget")
by_host: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
for entry in requests:
    require(entry.get("method") in {"GET", "HEAD"}, "public write method present")
    when = datetime.fromisoformat(entry["issued_at"])
    by_host[str(entry["host"])].append((when, str(entry["method"])))
minimum_gaps: dict[str, float] = {}
for host, entries in by_host.items():
    entries.sort()
    gaps = [(entries[index][0] - entries[index - 1][0]).total_seconds() for index in range(1, len(entries))]
    require(all(gap >= 5 for gap in gaps), f"public request spacing below five seconds for {host}")
    if gaps:
        minimum_gaps[host] = round(min(gaps), 6)
require(set(by_host) == {"huggingface.co", "us.aws.cdn.hf.co"}, "unexpected public request host")
require(receipt.get("source_requests") == 0 and receipt.get("public_writes") == 0, "restore reports prohibited activity")
require(receipt.get("input_acceptance") is False and receipt.get("workers_started") is False and receipt.get("repairs_activated") is False, "restore left held scope")
require(receipt.get("restore_pending") is False and receipt.get("operator_hold_present") is True, "restore barrier/hold state differs")
require(receipt.get("units_before") == receipt.get("units_after"), "ordinary units changed during restore")

require(observation.get("receipt_sha256") == RECEIPT_SHA256, "VM observation binds another restore")
require(observation.get("checkpoint_manifest_sha256") == CHECKPOINT_SHA256, "VM observation checkpoint differs")
require(observation.get("network_requests") == 0 and observation.get("database_changes") == 0, "VM observation reports side effects")
require(observation.get("scratch_state") == {"schema_markers": [29, "29"], "restore_pending": False, "unsettled_admissions": 0}, "scratch state differs")
production = observation.get("production", {})
require(production.get("hold_sha256") == "965468bb03ba65b6d1d4950ab8e85c09c8f2f2a323461b7a3f2b6177bba5c638", "production hold differs")
require(production.get("systems", {}).get("/run/current-system") == "/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8" and production.get("systems", {}).get("/nix/var/nix/profiles/system") == "/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8", "production system changed")
require(set(production.get("units", {})) == {f"swingset-{kind}.{suffix}" for kind in ("cycle", "backup", "summary") for suffix in ("service", "timer")}, "production unit inventory differs")
require(all(value == "inactive" for value in production["units"].values()), "ordinary production unit is active")
sidecars = observation.get("scratch_sidecars", {})
require(set(sidecars) == set(packet["top_level_sidecars"]), "restored sidecar inventory differs")
for name, record in packet["top_level_sidecars"].items():
    found = sidecars[name]
    require(found.get("expected_sha256") == record["sha256"] and found.get("sha256") == record["sha256"], f"restored sidecar differs: {name}")

started = datetime.fromisoformat(receipt["started_at"])
finished = datetime.fromisoformat(receipt["finished_at"])
duration = (finished - started).total_seconds()
require(duration >= 0, "restore timestamps reversed")
result = {
    "format": "schema28-to29-restore-independent-audit-v1",
    "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "passed": True,
    "receipt": "../restore-002/restore-receipt.json",
    "receipt_sha256": RECEIPT_SHA256,
    "pins": {
        "packet_sha256": PACKET_SHA256,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "checkpoint": packet["checkpoint"],
        "archive_commit": ARCHIVE_COMMIT,
        "source": SOURCE,
        "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
        "helper_sha256": packet["files"]["restore.py"],
    },
    "restore_contract": {
        "passed": True,
        "stage": receipt["stage"],
        "schema": 29,
        "change_contract": receipt["restore_change_contract"],
        "before_tables": 116,
        "expected_restored_tables": 116,
        "all_non_pressure_rows_equal_expected": True,
        "changed_existing_tables": [],
        "only_new_table_after_schema29_migration": TARGET_TABLE,
        "operator_hold_present": True,
        "restore_pending": False,
        "input_acceptance": False,
        "workers_started": False,
        "repairs_activated": False,
        "source_requests": 0,
        "public_writes": 0,
    },
    "public_request_audit": {
        "requests": len(requests),
        "methods": sorted({entry["method"] for entry in requests}),
        "hosts": {host: len(entries) for host, entries in sorted(by_host.items())},
        "minimum_same_host_issue_gaps_seconds": minimum_gaps,
        "minimum_seconds": 5,
        "all_methods_read_only": True,
    },
    "sidecars": {
        "count": len(sidecars),
        "all_match_packet_and_vm_observation": True,
        "observation_path": "restore-observation.json",
        "observation_sha256": sha(OBSERVATION_PATH),
        "observation_script_path": "restore-observation.py",
        "observation_script_sha256": sha(OBSERVATION_SCRIPT),
    },
    "limitations": [
        "The reviewer used the retained packet-pinned restore receipt and coordinator-provided read-only VM observation; no VM, production, or network access was performed by this review.",
        "The exact epoch increment is tied to packet restore helper code and the receipt's documented singleton-only contract; the public helper stores table digests rather than exposing the singleton's row values.",
    ],
    "failures": [],
}
OUTPUT_PATH.write_text(json.dumps(result, indent=2) + "\n")
print(OUTPUT_PATH)
print(json.dumps(result, indent=2))
