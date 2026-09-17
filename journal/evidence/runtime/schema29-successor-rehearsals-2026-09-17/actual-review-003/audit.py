from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
EVIDENCE = ROOT / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17"
PACKET_DIR = EVIDENCE / "packet-004"
PACKET_PATH = PACKET_DIR / "packet.json"
RECEIPT_PATH = EVIDENCE / "migration-002/migration-receipt.json"
OUTPUT_PATH = Path(__file__).with_name("migration-audit.json")
OBSERVATION_PATH = EVIDENCE / "actual-review-002/migration-observation.json"
PACKET_SHA256 = "366b0dbab70048cc2b85c4bdb7e06d24973478d8cfee965ee01ebb40d7fb5300"
RECEIPT_SHA256 = "13adb17d817b3372bfda9b28370a368d24fa24d399ee997ef5b77f009afb1a8c"
SOURCE = "/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source"
SOURCE_RECEIPT_SHA256 = "9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


packet = json.loads(PACKET_PATH.read_text())
receipt = json.loads(RECEIPT_PATH.read_text())
observation = json.loads(OBSERVATION_PATH.read_text())
require(sha(RECEIPT_PATH) == RECEIPT_SHA256, "migration receipt digest changed")
require(packet.get("format") == "schema28-to29-current-checkpoint-packet-v1", "packet format changed")
require(packet.get("source") == SOURCE, "packet source changed")
require(packet.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256, "packet source receipt changed")
require(packet.get("checkpoint_sha256") == "6ac7bb2d291136ef502abcb33f8762bf4449fd2fdc0160fb995f2aba0921f9d9", "checkpoint changed")
require(packet.get("archive_commit") == "68d738dcd78cb1654d053b479ea6dbd911f4b8ed", "archive commit changed")
require(sha(PACKET_DIR / "runner.py") == packet["files"]["runner.py"], "packet runner changed")
for name, digest in packet["files"].items():
    require(sha(PACKET_DIR / name) == digest, f"packet helper changed: {name}")
prior_packet_review = json.loads((EVIDENCE / "packet004-independent-review-001/explicit-review-002/receipt.json").read_text())
require(prior_packet_review.get("passed") is True, "packet-004 independent closure review did not pass")
require(prior_packet_review.get("packet_sha256") == PACKET_SHA256, "packet-004 independent review binds another packet")
require(receipt.get("passed") is True, "migration rehearsal did not pass")
require(receipt.get("checkpoint_sha256") == packet["checkpoint_sha256"], "receipt checkpoint differs")
require(receipt.get("checkpoint") == packet["checkpoint"], "receipt checkpoint path differs")
require(receipt.get("source") == SOURCE, "receipt source differs")
require(receipt.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256, "receipt source receipt differs")
require(receipt.get("helper_sha256") == packet["files"]["migration.py"], "receipt migration helper differs")
require(receipt.get("old_schema") == 28 and receipt.get("target_schema") == 29, "schema transition differs")
require(receipt.get("schema") == 29, "migrated schema differs")
before, after = receipt.get("before"), receipt.get("after")
require(isinstance(before, dict) and isinstance(after, dict), "table receipts missing")
require(len(before) == 116 and len(after) == 117, "table receipt counts differ")
require(all(before[name] == after.get(name) for name in before), "predecessor table receipts differ")
require(set(after) - set(before) == {"history_dispatch_fence"}, "unexpected schema-29 table")
require(receipt.get("changed_existing_tables") == [], "migration reports changed predecessor tables")
require(receipt.get("new_tables") == ["history_dispatch_fence"], "migration new-table receipt differs")
require(receipt.get("integrity") == [["ok"]], "integrity check did not pass")
require(receipt.get("foreign_key_failure") is False, "foreign-key check failed")
sidecars = receipt.get("top_level_sidecars")
require(isinstance(sidecars, dict) and len(sidecars) == 7, "top-level sidecar receipt incomplete")
require(sidecars == packet.get("top_level_sidecars"), "sidecars differ from packet closure")
require(receipt.get("scope") == "migration_only_without_runtime_input_acceptance", "migration scope changed")
require(observation.get("checkpoint_manifest_sha256") == packet["checkpoint_sha256"], "VM observation checkpoint changed")
require(observation.get("destination_receipt_sha256") == RECEIPT_SHA256, "VM observation binds another migration receipt")
require(observation.get("network_requests") == 0 and observation.get("database_changes") == 0, "VM observation reports side effects")
expected_database = observation.get("expected_checkpoint_database", {})
require(observation.get("checkpoint_database") == expected_database, "checkpoint database bytes differ from manifest")
require(observation.get("checkpoint_state", {}).get("schema_markers") == [28, "28"] and observation["checkpoint_state"].get("application_tables") == 116, "checkpoint specimen state differs")
require(observation.get("destination_state", {}).get("schema_markers") == [29, "29"] and observation["destination_state"].get("application_tables") == 117, "destination specimen state differs")
require(observation["destination_state"].get("fence") == [[1, 1]], "migration fence differs")
require(observation["checkpoint_state"].get("foreign_key_sample") == [] and observation["destination_state"].get("foreign_key_sample") == [], "VM observation foreign key checks differ")
require(observation["checkpoint_state"].get("restore_pending") is False and observation["destination_state"].get("restore_pending") is False, "restore pending marker found")
for name, record in packet["top_level_sidecars"].items():
    found = observation.get("sidecars", {}).get(name)
    require(isinstance(found, dict) and found.get("checkpoint_sha256") == record["sha256"] and found.get("destination_sha256") == record["sha256"] and found.get("expected_sha256") == record["sha256"], f"VM sidecar evidence differs: {name}")

started = datetime.fromisoformat(receipt["started_at"])
finished = datetime.fromisoformat(receipt["finished_at"])
duration = (finished - started).total_seconds()
require(duration >= 0, "migration receipt timestamps are reversed")
receipt_scope = {
    "source": SOURCE,
    "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
    "helper_sha256": packet["files"]["migration.py"],
    "packet_sha256": PACKET_SHA256,
    "checkpoint": packet["checkpoint"],
    "checkpoint_sha256": packet["checkpoint_sha256"],
    "archive_commit": packet["archive_commit"],
    "old_schema": 28,
    "target_schema": 29,
    "duration_seconds": round(duration, 3),
}
result = {
    "format": "schema28-to29-migration-independent-audit-v1",
    "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "passed": True,
    "receipt": "../migration-002/migration-receipt.json",
    "receipt_sha256": RECEIPT_SHA256,
    "receipt_scope": receipt_scope,
    "table_preservation": {
        "before_tables": 116,
        "after_tables": 117,
        "changed_existing_tables": [],
        "all_before_receipts_equal_after": True,
        "only_new_table": "history_dispatch_fence",
    },
    "migration_results": {
        "passed": True,
        "scope": receipt["scope"],
        "integrity": receipt["integrity"],
        "foreign_key_failure": receipt["foreign_key_failure"],
        "checkpoint_unchanged": True,
        "checkpoint_manifest_sha256": packet["checkpoint_sha256"],
        "source_receipt_rechecked": True,
        "source_helper_sha256_rechecked": packet["files"]["migration.py"],
        "hold_sha256": sidecars["operator-hold"]["sha256"],
        "restore_pending": False,
    },
    "independent_observation": {
        "evidence_basis": "Recomputed all 116 predecessor digest/column/row-count comparisons from the immutable migration receipt; validated receipt and packet helper hashes, schema result, integrity/FK checks, all seven top-level sidecars, and the coordinator-retained VM read-only checkpoint/specimen observation.",
        "application_tables": 117,
        "history_dispatch_fence": after["history_dispatch_fence"],
        "destination_receipt_sha256_matches_retained_receipt": True,
        "destination_hold_matches_checkpoint_receipt": sidecars["operator-hold"] == packet["top_level_sidecars"]["operator-hold"],
    },
    "failures": [],
    "limitations": [
        "The independent reviewer did not mount the VM filesystems. For direct database bytes/schema/sidecar checks, this review pins the coordinator-retained read-only VM observation script and output; the source-bound migration helper also verifies the checkpoint manifest and state.sqlite hashes before and after the rehearsal.",
        "No VM, network, production database, input acceptance, source request, or public write was performed by this review.",
    ],
}
OUTPUT_PATH.write_text(json.dumps(result, indent=2) + "\n")
print(OUTPUT_PATH)
print(json.dumps(result, indent=2))
