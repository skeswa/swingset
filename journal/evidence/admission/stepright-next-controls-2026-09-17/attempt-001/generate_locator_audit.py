"""Generate an offline locator audit from retained Step Right evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
CDX = ROOT / "journal/evidence/collection/wayback-2026-09-11/cdx_steprightsolutions_html_all.json"
PROVENANCE = ROOT / "tests/fixtures/sources/steprightsolutions/real-provenance-20260917.json"
RECEIPT = ROOT / "journal/evidence/admission/fixture-exception-2026-09-17/quarantine/receipt.json"
OUTPUT = Path(__file__).with_name("locator-audit.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    cdx = json.loads(CDX.read_bytes())
    provenance = json.loads(PROVENANCE.read_bytes())
    receipt = json.loads(RECEIPT.read_bytes())
    pairs = {(str(row[0]), str(row[1])) for row in cdx[1:]}
    by_id = {item["id"]: item for item in provenance}

    event2013 = ("20130401090810", "http://www.steprightsolutions.com:80/events/asianopen2013")
    event2015 = ("20150711035813", "http://steprightsolutions.com:80/events/asianopen2015")
    rounds2013 = {
        (timestamp, f"http://steprightsolutions.com/events/asianopen2013/round/{number}")
        for timestamp, number in (
            ("20160909170855", 507),
            ("20160909171011", 508),
            ("20160909170745", 509),
            ("20160909170245", 510),
            ("20160909170412", 511),
            ("20160909121357", 512),
            ("20160909171715", 513),
        )
    }
    rounds2015 = {
        (timestamp, f"http://steprightsolutions.com/events/asianopen2015/round/{number}")
        for timestamp, number in (
            ("20160909171350", 1126),
            ("20160909171451", 1127),
            ("20160909180454", 1128),
            ("20160909171307", 1129),
            ("20160909131957", 1130),
            ("20160909173025", 1131),
            ("20160909171401", 1132),
            ("20160909111516", 1133),
            ("20160909170258", 1134),
            ("20160909170707", 1135),
            ("20160909111826", 1136),
            ("20160909171042", 1137),
        )
    }
    expected = {event2013, event2015, *rounds2013, *rounds2015}
    if not expected <= pairs:
        raise SystemExit("retained CDX corpus no longer contains the exact selected rows")
    if sha256(CDX) != "b9562bc37c62b14161dafd34c570ad55e229a1dff44a833e573f7bf84bf77b46":
        raise SystemExit("retained CDX source hash changed")

    existing = {}
    for identifier in ("srs-event", "srs-round507", "srs-round508"):
        item = by_id[identifier]
        existing[identifier] = {
            **item,
            "fixture_sha256": sha256(ROOT / "tests/fixtures/sources/steprightsolutions" / item["file"]),
        }
    result = {
        "format": "stepright-next-controls-retained-locator-audit-v1",
        "network_requests": 0,
        "production_reads": 0,
        "source_files": {
            "cdx_path": CDX.relative_to(ROOT).as_posix(),
            "cdx_sha256": sha256(CDX),
            "real_fixture_provenance_path": PROVENANCE.relative_to(ROOT).as_posix(),
            "real_fixture_provenance_sha256": sha256(PROVENANCE),
            "fixture_exception_receipt_path": RECEIPT.relative_to(ROOT).as_posix(),
            "fixture_exception_receipt_sha256": sha256(RECEIPT),
        },
        "existing_event_and_round_controls": existing,
        "candidate_event_page": {
            "capture": event2015[0],
            "original_url": event2015[1],
            "archive_url": "https://web.archive.org/web/20150711035813id_/http://steprightsolutions.com:80/events/asianopen2015",
            "result_round_cdx_rows": [
                {"capture": ts, "original_url": url} for ts, url in sorted(rounds2015)
            ],
            "evidence_level": "exact_retained_cdx_locator_only",
            "unverified": [
                "whether the event page body exists at this capture",
                "whether it prints contest-to-round links",
                "whether any linked round rows are listed on that exact body",
                "event dates and whether this capture is post-event",
            ],
        },
        "bounded_next_body_scope": {
            "maximum_archive_body_requests": 1,
            "only_archive_url": "https://web.archive.org/web/20150711035813id_/http://steprightsolutions.com:80/events/asianopen2015",
            "automatic_redirects_or_alternates": 0,
            "origin_requests": 0,
            "child_round_requests": 0,
            "stop_condition": "If the exact capture is unavailable, empty, or does not show round links, retain that outcome and make no alternate request.",
        },
        "cdx_rows_checked": len(expected),
        "expected_cdx_rows_found": len(expected),
        "fixture_exception_receipt_request_summary": [
            {
                "purpose": item["purpose"],
                "url": item["url"],
                "status": item["http_status"],
                "body_sha256": item["body_sha256"],
                "body_bytes": item["body_bytes"],
            }
            for item in receipt["requests"]
            if item["purpose"] in {"srs-event", "srs-round507", "srs-round508"}
        ],
    }
    with OUTPUT.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(OUTPUT), "sha256": sha256(OUTPUT), "selected_rows": len(expected)}))


if __name__ == "__main__":
    main()
