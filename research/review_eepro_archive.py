"""Reinterpret the retained WP11 index offline without changing its fetch receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from swingset.sources.base import ParseContext
from swingset.sources.eepro import AutoIndexPage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path("research/workflow-output/v2-phase1/wp11-eepro2019"),
    )
    args = parser.parse_args()
    receipt_bytes = (args.directory / "receipt.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    snapshot = receipt["snapshot"]
    body = (args.directory / "freedomswing2019.html").read_bytes()
    if hashlib.sha256(body).hexdigest() != snapshot["body_sha256"]:
        raise ValueError("Retained index does not match the original fetch receipt")
    page = AutoIndexPage()
    result = page.parse(
        page.extract(body),
        ParseContext(
            snapshot["snapshot_id"],
            snapshot["watch_id"],
            snapshot["url"],
            "eepro",
            page.kind,
            "eepro:freedomswing2019",
            snapshot["observed_at"],
        ),
    )
    review = {
        "original_fetch_receipt": "receipt.json",
        "original_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "body_sha256": snapshot["body_sha256"],
        "extract_version": page.EXTRACT_VERSION,
        "parser_version": page.PARSER_VERSION,
        "supersedes": "Only the original receipt's extracted observations and child locators.",
        "reason": "Two full hrefs were omitted because Apache shortened their visible labels.",
        "observations": [asdict(row) for row in result.observations],
        "discovered_child_locators": [asdict(row) for row in result.watches],
        "original_child_count": len(receipt["discovered_child_locators"]),
        "corrected_child_count": len(result.watches),
        "network_requests": 0,
        "persisted_child_watches": 0,
        "score_sheets_fetched": 0,
    }
    output = args.directory / "interpretation-review.json"
    output.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n")
    print(output)


if __name__ == "__main__":
    main()
