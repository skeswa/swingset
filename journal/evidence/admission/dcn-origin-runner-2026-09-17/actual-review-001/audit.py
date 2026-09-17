from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
BASE = ROOT / "journal/evidence/admission/dcn-origin-runner-2026-09-17"
OPS = BASE / "operations-001"
QUARANTINE = BASE / "quarantine-001"
ROBOTS_EXPORT = BASE / "robots-export-001"
DRAIN = ROOT / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17"
EXPECTED_URLS = [
    "https://danceconvention.net/robots.txt",
    "https://danceconvention.net/eventdirector/robots.txt",
    "https://danceconvention.net/eventdirector/en/roundscores/3451330.pdf",
    "https://danceconvention.net/eventdirector/en/roundscores/3451331.pdf",
]
EXPECTED_KINDS = ["robots", "robots", "pdf", "pdf"]


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_bytes())


def main() -> None:
    from protego import Protego

    receipt_path = QUARANTINE / "receipt.json"
    pre_path = OPS / "pre-state.json"
    post_path = OPS / "post-state.json"
    ledger_path = OPS / "dcn-origin-event-days.json"
    cache_path = OPS / "dcn-origin-robots-cache.json"
    robots_path = ROBOTS_EXPORT / "robots.txt"
    robots_receipt_path = ROBOTS_EXPORT / "receipt.json"
    receipt = read_json(receipt_path)
    pre = read_json(pre_path)
    post = read_json(post_path)
    ledger = read_json(ledger_path)
    cache = read_json(cache_path)
    robots_receipt = read_json(robots_receipt_path)
    requests = receipt["requests"]
    targets = receipt["targets"]

    if len(requests) != 4 or [item["url"] for item in requests] != EXPECTED_URLS:
        raise SystemExit("origin request sequence differs from exact four-URL scope")
    if [item["purpose"] for item in requests] != EXPECTED_KINDS:
        raise SystemExit("request purposes differ from exact scope")
    if any(not item["paid_admission"] or not item["socket_dispatch_attempted"] for item in requests):
        raise SystemExit("a dispatched request lacks its paid H13 admission")
    if any(not item["complete"] or item["request_day"] != "2026-09-17" for item in requests):
        raise SystemExit("an origin response is incomplete or has the wrong debit day")
    if any(item.get("redirect_count", 0) != (1 if i == 1 else 0) for i, item in enumerate(requests)):
        raise SystemExit("redirect count differs from the single allowed robots hop")
    if any({key.lower() for key in item["headers"]} & {"set-cookie", "set-cookie2", "cookie"} for item in requests):
        raise SystemExit("a cookie value was retained in response evidence")

    bodies = []
    for item in targets:
        body_path = QUARANTINE / "bodies" / item["body_sha256"]
        body = body_path.read_bytes()
        if digest(body) != item["body_sha256"] or len(body) != item["body_bytes"]:
            raise SystemExit("quarantined PDF digest or byte count differs")
        if not body.startswith(b"%PDF-") or item["content_finding"] != "pdf":
            raise SystemExit("a quarantined target is not a PDF")
        bodies.append({"sha256": digest(body), "bytes": len(body), "path": str(body_path.relative_to(ROOT))})
    if [target["requested_url"] for target in targets] != EXPECTED_URLS[2:]:
        raise SystemExit("PDF targets differ from exact finals/prelims order")
    if receipt["status"] != "captured_pending_independent_review":
        raise SystemExit("runner did not close as a complete quarantine capture")
    if receipt["production_facts_or_watches_created"] != 0:
        raise SystemExit("origin fixture created production facts or watches")

    body_bytes = sum(item["body_bytes"] for item in requests)
    if body_bytes != receipt["received_bytes"] or body_bytes != 132764:
        raise SystemExit("aggregate received-body accounting differs")
    if any(item["body_bytes"] > 2 * 1024 * 1024 for item in requests):
        raise SystemExit("per-response byte limit exceeded")
    dispatch_gaps = [
        requests[index + 1]["dispatched_monotonic"] - requests[index]["completed_monotonic"]
        for index in range(3)
    ]
    if any(gap < 10 for gap in dispatch_gaps):
        raise SystemExit("completion-to-dispatch spacing is below 10 seconds")

    robots_body = robots_path.read_bytes()
    if digest(robots_body) != cache["body_sha256"] or digest(robots_body) != robots_receipt["body_sha256"]:
        raise SystemExit("retained robots body differs from cache proof")
    cache_body_bytes = len(robots_body)
    if cache_body_bytes != robots_receipt["body_bytes"]:
        raise SystemExit("robots body length is inconsistent")
    rules = Protego.parse(robots_body.decode("utf-8"))
    robots_allowed = {
        url: bool(rules.can_fetch(url, "swingset")) for url in EXPECTED_URLS[2:]
    }
    if not all(robots_allowed.values()):
        raise SystemExit("live retained robots policy disallows a requested PDF")
    if cache["requested_url"] != EXPECTED_URLS[0] or cache["final_url"] != EXPECTED_URLS[1]:
        raise SystemExit("robots cache proof has an unexpected redirect chain")
    if datetime.fromisoformat(cache["fetched_at"]) < datetime.fromisoformat(
        requests[1]["completed_at"]
    ):
        raise SystemExit("robots cache fetch time predates the completed response")

    day = ledger["days"].get("2026-09-17")
    if (
        day is None
        or day["event_id"] != receipt["event_id"]
        or day["run_id"] != receipt["operation_id"]
        or [entry["url"] for entry in day["requests"]] != EXPECTED_URLS
        or [entry["sequence"] for entry in day["requests"]] != [1, 2, 3, 4]
    ):
        raise SystemExit("durable event-day sidecar differs from actual request sequence")
    if post["host_budget"] != [{"host": "danceconvention.net", "day": "2026-09-17", "requests": 4, "bytes": 132764}]:
        raise SystemExit("shared host request/byte accounting differs from response receipt")
    if len(post["origin_admissions"]) != 8 or any(
        item["state"] != "settled" for item in post["origin_admissions"]
    ):
        raise SystemExit("origin H13 admissions are not all settled")
    if post["operator_pauses"] or post["unsettled_admissions"]:
        raise SystemExit("post-state has an origin pause or unsettled admission")
    if post["archive_paid_usage"] != pre["archive_paid_usage"]:
        raise SystemExit("Archive paid usage changed during the origin operation")
    if post["baseline"] != pre["baseline"] or post["schema"] != 28 or post["control_revision"] != 2:
        raise SystemExit("published baseline, schema or H13 revision changed")
    if post["active_system"] != pre["active_system"] or post["persistent_system"] != pre["persistent_system"]:
        raise SystemExit("active or persistent system changed")
    if post["units"] != pre["units"] or any(value != "inactive" for value in post["units"].values()):
        raise SystemExit("scheduled units changed or became active")
    if post["hold_sha256"] != pre["hold_sha256"]:
        raise SystemExit("operator hold marker changed")
    if sum(item["body_bytes"] for item in targets) != 129823:
        raise SystemExit("PDF-only captured byte total differs")

    drain = read_json(DRAIN / "inputs-001/drain-002.json")
    blockers = read_json(DRAIN / "inputs-review-001/drain-002-blockers.json")
    if not drain["passed"] or drain["production_acceptance"] or drain["publication"]:
        raise SystemExit("drain rehearsal was incorrectly marked as production acceptance")
    if drain["turn"]["attempted"] != 100 or drain["turn"]["status"] != "bounded_stop":
        raise SystemExit("drain run bounds/status differ")

    report = {
        "format": "dcn-origin-actual-independent-review-v1",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "verdict": "origin_receipt_and_accounting_pass; PDFs remain unreviewed quarantine bodies",
        "http_requests_by_reviewer": 0,
        "production_writes_by_reviewer": 0,
        "origin": {
            "receipt_sha256": digest(receipt_path.read_bytes()),
            "status": receipt["status"],
            "request_count": len(requests),
            "request_urls": [item["url"] for item in requests],
            "response_statuses": [item["http_status"] for item in requests],
            "body_bytes_total": body_bytes,
            "pdf_bytes_total": sum(item["body_bytes"] for item in targets),
            "response_hashes": [item["body_sha256"] for item in requests],
            "target_bodies": bodies,
            "completion_to_dispatch_seconds": dispatch_gaps,
            "robots_sha256": digest(robots_body),
            "robots_bytes": cache_body_bytes,
            "robots_allows_targets": robots_allowed,
            "event_day_sidecar_sha256": digest(ledger_path.read_bytes()),
            "robots_cache_sidecar_sha256": digest(cache_path.read_bytes()),
            "host_budget_requests": post["host_budget"][0]["requests"],
            "host_budget_bytes": post["host_budget"][0]["bytes"],
            "h13_admissions_settled": len(post["origin_admissions"]),
            "archive_paid_usage_unchanged": True,
            "production_facts_or_watches_created": receipt["production_facts_or_watches_created"],
            "production_state_unchanged": True,
        },
        "drain_002": {
            "passed_for_bounded_rehearsal": drain["passed"],
            "production_acceptance": drain["production_acceptance"],
            "publication": drain["publication"],
            "attempted": drain["turn"]["attempted"],
            "elapsed_seconds": drain["turn"]["elapsed_seconds"],
            "outcomes": drain["turn"]["outcomes"],
            "pending_queue_by_stage": drain["turn"]["pending_queue_by_stage"],
            "blockers": blockers["attempts"],
        },
    }
    output = Path(__file__).with_name("review.json")
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(output.relative_to(ROOT))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
