from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PACKET_DIR = ROOT / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-004"
PACKET_SHA = "366b0dbab70048cc2b85c4bdb7e06d24973478d8cfee965ee01ebb40d7fb5300"
ORIGIN_DIR = ROOT / "journal/evidence/admission/dcn-origin-runner-2026-09-17/operations-001"
CHECKPOINT_DIR = ROOT / "journal/evidence/runtime/schema28-checkpoint-2026-09-17/production-004"

def load_runner():
    spec = importlib.util.spec_from_file_location("packet004_runner", PACKET_DIR / "runner.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("runner import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

runner = load_runner()
packet = runner.packet_at(PACKET_DIR, PACKET_SHA)
post = json.loads((ORIGIN_DIR / "post-state.json").read_bytes())
checkpoint_receipt = json.loads((CHECKPOINT_DIR / "receipt.json").read_bytes())
checkpoint_summary = json.loads((CHECKPOINT_DIR / "checkpoint-verified.json").read_bytes())
sidecar_summary = json.loads((PACKET_DIR / "checkpoint-sidecars.json").read_bytes())

assert packet["checkpoint_sha256"] == checkpoint_summary["manifest_sha256"]
assert packet["checkpoint"] == checkpoint_summary["path"]
assert packet["predecessor_schema"] == 28 and packet["target_schema"] == 29
assert packet["predecessor_tables"] == 116
assert packet["archive_commit"] == checkpoint_receipt["private_archive_commit"]
assert checkpoint_receipt["checkpoint"] == checkpoint_summary
assert checkpoint_receipt["live_database_changes"] == 0 and checkpoint_receipt["passed"] is True
archive_usage = post["archive_paid_usage"][0]
assert packet["paid_archive_usage"] == {
    "day": archive_usage["day"], "requests": archive_usage["requests"], "bytes": archive_usage["bytes"]
}
origin_budget = next(row for row in post["host_budget"] if row["host"] == "danceconvention.net")
assert packet["paid_origin_usage"] == {
    "host": origin_budget["host"],
    "day": origin_budget["day"],
    "requests": origin_budget["requests"],
    "bytes": origin_budget["bytes"],
}
assert all(row["state"] == "settled" for row in post["origin_admissions"])
assert post["unsettled_admissions"] == []
for name, sidecar in packet["top_level_sidecars"].items():
    assert sidecar == sidecar_summary[name]
for name, sidecar in post["sidecars"].items():
    assert packet["top_level_sidecars"][name] == {
        "sha256": sidecar["sha256"], "size": sidecar["bytes"]
    }
    retained_sidecar = ORIGIN_DIR / name
    assert retained_sidecar.is_file() and sha(retained_sidecar) == sidecar["sha256"]
    assert retained_sidecar.stat().st_size == sidecar["bytes"]

result = {
    "format": "schema29-packet004-independent-review-v1",
    "packet_sha256": sha(PACKET_DIR / "packet.json"),
    "packet_closure": "passed via packet-004 runner.packet_at (helper hashes, exact members, no extras, checkpoint proof pins, sidecar closure, source/schema/usage constants)",
    "checkpoint": checkpoint_summary,
    "production_checkpoint_receipt": {
        "passed": checkpoint_receipt["passed"],
        "live_database_changes": checkpoint_receipt["live_database_changes"],
        "private_archive_commit": checkpoint_receipt["private_archive_commit"],
        "production_source": checkpoint_receipt["source"],
        "system": checkpoint_receipt["system"],
        "baseline": checkpoint_receipt["baseline"],
        "units": checkpoint_receipt["units"],
    },
    "usage": {
        "archive": packet["paid_archive_usage"],
        "dcn_origin": packet["paid_origin_usage"],
        "dcn_admissions_settled": len(post["origin_admissions"]),
        "unsettled_admissions": len(post["unsettled_admissions"]),
    },
    "sidecars": packet["top_level_sidecars"],
    "direct_checkpoint_bytes_available": Path(packet["checkpoint"]).exists(),
    "frozen_source_bytes_available": Path(packet["source"]).exists(),
    "network_or_production_operations": "none",
}
OUT = Path(__file__).resolve().parent / "review.json"
OUT.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
print(json.dumps(result, sort_keys=True, indent=2))
