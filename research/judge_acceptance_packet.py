"""Export retained H3 source comparisons without changing state or contacting sites.

Run with the standard-library Python in the writer environment, then save the
JSON export locally. Bodies are complete, digest-verified archived responses.
The export contains source-supported comparisons, not human adjudications.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

CASES = (
    ("2026-04-asia-wcs-open/judge/tze-ming-wee", 9285, 8785),
    ("2026-04-city-of-angels-wcs/judge/emily-huang", 16071, 18139),
    ("2025-10-augsburg-westie-station/judge/camille-lange", 8395, 495),
)


def export(state: Path) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{state / 'state.sqlite'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    bodies = {}
    snapshots = {}
    cases = []
    for judge_id, source_id, contrast_id in CASES:
        judge = dict(
            conn.execute(
                "SELECT j.*,e.year FROM judges j JOIN events e USING(event_id) WHERE judge_id=?",
                (judge_id,),
            ).fetchone()
        )
        printed = dict(
            conn.execute(
                "SELECT e.entry_id,e.name_raw,e.wsdc_id,e.snapshot_id FROM entries e JOIN identity_links l ON l.subject_kind='entry' AND l.subject_id=e.entry_id WHERE e.event_id=? AND lower(e.name_raw)=lower(?) AND l.method='source_id' AND e.wsdc_id=? ORDER BY e.entry_id LIMIT 1",
                (judge["event_id"], judge["name_raw"], source_id),
            ).fetchone()
        )
        dancers = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM dancers WHERE wsdc_id IN (?,?) ORDER BY wsdc_id",
                (source_id, contrast_id),
            )
        ]
        candidates = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM link_candidates WHERE subject_kind='judge' AND subject_id=? AND wsdc_id IN (?,?) ORDER BY wsdc_id",
                (judge_id, source_id, contrast_id),
            )
        ]
        cases.append(
            {
                "judge": judge,
                "printed_entry": printed,
                "dancers": dancers,
                "retained_candidates": candidates,
                "source_supported_id": source_id,
                "contrasting_id": contrast_id,
                "adjudication": "source_comparison; human_review_pending",
            }
        )
        for snapshot in {
            judge["snapshot_id"],
            printed["snapshot_id"],
            *(dancer["snapshot_id"] for dancer in dancers),
        }:
            if snapshot in snapshots:
                continue
            row = dict(
                conn.execute(
                    "SELECT snapshot_id,url,form,fetched_at,body_sha256,http_status,via,parser_version FROM snapshots WHERE snapshot_id=?",
                    (snapshot,),
                ).fetchone()
            )
            digest = row["body_sha256"]
            body = gzip.decompress(
                (state / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest).read_bytes()
            )
            if hashlib.sha256(body).hexdigest() != digest:
                raise ValueError(f"corrupt evidence {snapshot}")
            row["fixture"] = f"{snapshot}.body"
            snapshots[snapshot] = row
            bodies[row["fixture"]] = base64.b64encode(body).decode()
    baseline = json.loads((state / "baseline" / "PUBLISHED").read_text())
    manifest = {
        "captured_at": datetime.now(UTC).isoformat(),
        "baseline_receipt": baseline,
        "counts": dict(
            conn.execute(
                "SELECT count(*) AS judges,sum(wsdc_id IS NOT NULL) AS default_ids FROM judges"
            ).fetchone()
        ),
        "cases": cases,
        "snapshots": snapshots,
        "scope": "Three targeted source comparisons; no population precision estimate or human adjudication",
    }
    conn.close()
    return {"manifest": manifest, "bodies": bodies}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("/var/lib/swingset"))
    print(json.dumps(export(parser.parse_args().state), sort_keys=True))
