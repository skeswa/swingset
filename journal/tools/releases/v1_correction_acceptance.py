#!/usr/bin/env python3
"""Read-only V1 correction audit; never publishes or changes pipeline state."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from selectolax.parser import HTMLParser

from swingset.fetch.archive import Archive
from swingset.sources.common import text

PAIRS = (
    ("2026-02-swing-crush/hi-lo-j-j-hi-lead-lo-follow", "291", "Gabi Wasserman and Lynne Yun"),
    ("2026-06-jack-jill-o-rama/all-american-60s-era-j-j", "22", "Ben McHenry and Lynne Yun"),
)
UNRESTRICTED = (
    "2025-10-atlanta-swing-classic/jj-proam-follower-new-nov/F-940",
    "2025-11-northeast-swing-clasic/jj-proam-new-nov-follower/F-175",
    "2026-03-madjam/jj-proam-new-nov-follower/F-975",
    "2025-10-sass/jj-spooky-all-american/F-110",
)
TABLES = (
    "entries",
    "judges",
    "placements",
    "contests",
    "identity_links",
    "link_candidates",
    "callback_marks",
)


class Dataset:
    def __init__(self, path: Path, *, state: bool = False):
        self.path = path
        self.hashes = {}
        if state:
            self.db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            self.db.execute("BEGIN")
        else:
            self.db = duckdb.connect(
                config={"memory_limit": "128MB", "threads": "1", "temp_directory": ""}
            )
            for table in TABLES:
                files = sorted((path / "data" / table).glob("*.parquet"))
                if not files:
                    raise ValueError(f"missing {table} parquet under {path}")
                for file in files:
                    with file.open("rb") as stream:
                        self.hashes[str(file.relative_to(path))] = hashlib.file_digest(
                            stream, "sha256"
                        ).hexdigest()
                glob = str(path / "data" / table / "*.parquet").replace("'", "''")
                self.db.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{glob}')")

    def rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self.db.execute(sql, args)
        names = [item[0] for item in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def count(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        return int(self.db.execute(sql, args).fetchone()[0])


def audit(baseline: Path, state_dir: Path, candidate: Path | None = None) -> dict[str, Any]:
    old = Dataset(baseline)
    state = Dataset(state_dir / "state.sqlite", state=True)
    current = Dataset(candidate) if candidate else state
    report: dict[str, Any] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "baseline": str(baseline),
        "candidate": str(candidate) if candidate else None,
        "state_dir": str(state_dir),
        "publication_verified": False,
        "baseline_file_hashes": old.hashes,
        "candidate_file_hashes": current.hashes,
        "checks": {},
        "paired": [],
        "unrestricted": [],
    }
    checks = report["checks"]
    report["baseline_commit"] = json.loads((baseline / "PUBLISHED").read_text())["commit"]
    if candidate:
        manifest_body = (candidate / "_meta" / "manifest.json").read_bytes()
        manifest = json.loads(manifest_body)
        built = json.loads((candidate / "BUILT").read_text())
        report["candidate_identity"] = {
            "candidate_id": manifest["candidate_id"],
            "manifest_sha256": hashlib.sha256(manifest_body).hexdigest(),
            "content_hash": manifest["content_hash"],
            "publication_policy": manifest["versions"].get("publication_policy"),
            "input_bundle_hash": manifest["input_bundle_hash"],
            "repository_commit": manifest["repository_commit"],
        }
        checks["candidate_manifest_receipt_matches"] = (
            built["manifest_hash"] == report["candidate_identity"]["manifest_sha256"]
        )
        checks["candidate_baseline_matches"] = (
            manifest["baseline_commit"] == report["baseline_commit"]
        )
        checks["candidate_uses_v1_restriction"] = (
            manifest["versions"].get("publication_policy") == "v1_baseline_default_joins_v1"
        )
        checks["audited_candidate_files_match_manifest"] = all(
            manifest["files"].get(name) == digest for name, digest in current.hashes.items()
        )
    pending = state.rows(
        "SELECT stage,count(*) AS count FROM pending_work WHERE stage IN ('parse','project','link') GROUP BY stage"
    )
    report["pending_work"] = pending
    checks["settled"] = not pending
    archive = Archive(state_dir)
    for contest, bib, name in PAIRS:
        old_id, new_id = f"{contest}/L-{bib}", f"{contest}/C-{bib}"
        entry_fields = "entry_id,contest_id,bib,role,name_raw,wsdc_id,link_status,rounds_danced,best_round,snapshot_id"
        before = old.rows(f"SELECT {entry_fields} FROM entries WHERE entry_id=?", (old_id,))
        after = current.rows(f"SELECT {entry_fields} FROM entries WHERE entry_id=?", (new_id,))
        source = state.rows(
            "SELECT e.snapshot_id,s.url,s.body_sha256 FROM entries e JOIN snapshots s USING(snapshot_id) WHERE e.entry_id=?",
            (new_id,),
        )
        evidence: dict[str, Any] = {"available": False}
        if source:
            evidence = source[0]
            body = archive.read_body(evidence["body_sha256"])
            evidence["matching_source_rows"] = [
                cells
                for row in HTMLParser(body).css("tr")
                if name in (cells := [text(cell.text(separator=" ")) for cell in row.css("td")])
                and bib in cells
            ]
            evidence["available"] = bool(evidence["matching_source_rows"])
            evidence["body_bytes"] = len(body)
        old_placements = old.rows(
            "SELECT placement_id,contest_id,round_id,place FROM placements WHERE ? IN (leader_entry_id,follower_entry_id,couple_entry_id) ORDER BY placement_id",
            (old_id,),
        )
        new_placements = current.rows(
            "SELECT placement_id,contest_id,round_id,place FROM placements WHERE ? IN (leader_entry_id,follower_entry_id,couple_entry_id) ORDER BY placement_id",
            (new_id,),
        )
        checks[f"pair_{bib}_baseline_wrong_join"] = (
            len(before) == 1 and before[0]["wsdc_id"] == 26843
        )
        checks[f"pair_{bib}_preserved_without_individual_join"] = (
            len(after) == 1
            and after[0]["name_raw"] == name
            and after[0]["bib"] == bib
            and after[0]["role"] == "couple"
            and after[0]["wsdc_id"] is None
            and after[0]["link_status"] == "unmatched"
            and current.count("SELECT count(*) FROM entries WHERE entry_id=?", (old_id,)) == 0
            and current.count(
                "SELECT count(*) FROM link_candidates WHERE subject_id IN (?,?)", (old_id, new_id)
            )
            == 0
            and current.count(
                "SELECT count(*) FROM identity_links WHERE subject_id IN (?,?) AND wsdc_id IS NOT NULL",
                (old_id, new_id),
            )
            == 0
        )
        checks[f"pair_{bib}_source_supported"] = evidence["available"]
        checks[f"pair_{bib}_source_provenance_preserved"] = (
            bool(after and source) and after[0]["snapshot_id"] == source[0]["snapshot_id"]
        )
        checks[f"pair_{bib}_placement_observations_preserved"] = old_placements == new_placements
        marks_sql = "SELECT round_id,judge_id,mark_raw FROM callback_marks WHERE entry_id=? ORDER BY round_id,judge_id"
        old_marks = old.rows(marks_sql, (old_id,))
        new_marks = current.rows(marks_sql, (new_id,))
        checks[f"pair_{bib}_raw_callback_marks_preserved"] = (
            bool(old_marks) and old_marks == new_marks
        )
        report["paired"].append(
            {
                "old_entry": before,
                "new_entry": after,
                "source": evidence,
                "baseline_placements": old_placements,
                "current_placements": new_placements,
                "baseline_callback_marks": old_marks,
                "current_callback_marks": new_marks,
                "placement_note": "These audited rows are prelims; empty placement lists do not assert a finals result.",
            }
        )
    for identifier in UNRESTRICTED:
        rows = current.rows(
            "SELECT e.entry_id,e.name_raw,e.role,e.wsdc_id,e.link_status,c.division,l.score,l.division_ok FROM entries e JOIN contests c USING(contest_id) LEFT JOIN link_candidates l ON l.subject_kind='entry' AND l.subject_id=e.entry_id AND l.wsdc_id=26843 WHERE e.entry_id=?",
            (identifier,),
        )
        checks[f"unrestricted_{identifier}"] = (
            len(rows) == 1
            and rows[0]["name_raw"] == "Lynne Yun"
            and rows[0]["role"] == "follower"
            and rows[0]["division"] == "none"
            and rows[0]["division_ok"] is None
            and rows[0]["score"] is not None
            and rows[0]["score"] >= 0.9
            and rows[0]["wsdc_id"] is None
        )
        report["unrestricted"].append(
            {
                "baseline": old.rows(
                    "SELECT entry_id,wsdc_id,link_status,link_confidence FROM entries WHERE entry_id=?",
                    (identifier,),
                ),
                "current": rows,
            }
        )
    report["judges"] = {
        "baseline_rows": old.count("SELECT count(*) FROM judges"),
        "current_rows": current.count("SELECT count(*) FROM judges"),
        "baseline_default_ids": old.count("SELECT count(*) FROM judges WHERE wsdc_id IS NOT NULL"),
        "current_default_ids": current.count(
            "SELECT count(*) FROM judges WHERE wsdc_id IS NOT NULL"
        ),
    }
    checks["judge_rows_preserved_and_ids_withheld"] = report["judges"] == {
        "baseline_rows": 4919,
        "current_rows": 4919,
        "baseline_default_ids": 0,
        "current_default_ids": 0,
    }
    unsupported = current.rows(
        "SELECT entry_id,wsdc_id,link_status FROM entries e WHERE e.wsdc_id IS NOT NULL AND (e.link_status<>'confirmed' OR e.role='couple' OR NOT EXISTS (SELECT 1 FROM identity_links l WHERE l.subject_kind='entry' AND l.subject_id=e.entry_id AND l.wsdc_id=e.wsdc_id AND l.status='confirmed'))"
    )
    stale_placements = current.rows(
        "SELECT placement_id,leader_wsdc_id,follower_wsdc_id FROM placements p WHERE (leader_wsdc_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM entries e WHERE e.entry_id=p.leader_entry_id AND e.role='leader' AND e.wsdc_id=p.leader_wsdc_id AND e.link_status='confirmed')) OR (follower_wsdc_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM entries e WHERE e.entry_id=p.follower_entry_id AND e.role='follower' AND e.wsdc_id=p.follower_wsdc_id AND e.link_status='confirmed'))"
    )
    old_defaults = {
        r["entry_id"]: r["wsdc_id"]
        for r in old.rows("SELECT entry_id,wsdc_id FROM entries WHERE wsdc_id IS NOT NULL")
    }
    new_defaults = current.rows("SELECT entry_id,wsdc_id FROM entries WHERE wsdc_id IS NOT NULL")
    expansions = [r for r in new_defaults if old_defaults.get(r["entry_id"]) != r["wsdc_id"]]
    report["default_joins"] = {
        "baseline_entry_ids": len(old_defaults),
        "current_entry_ids": len(new_defaults),
        "unsupported": unsupported,
        "stale_placements": stale_placements,
        "expansions": expansions,
    }
    checks["no_unsupported_default_entry_ids"] = not unsupported
    checks["no_stale_placement_ids"] = not stale_placements
    checks["no_default_join_expansions"] = not expansions
    if candidate:
        state_defaults = state.rows(
            "SELECT entry_id,wsdc_id FROM entries WHERE wsdc_id IS NOT NULL"
        )
        withheld = [r for r in state_defaults if old_defaults.get(r["entry_id"]) != r["wsdc_id"]]
        retained = {
            r["entry_id"]: r["wsdc_id"]
            for r in state_defaults
            if old_defaults.get(r["entry_id"]) == r["wsdc_id"]
        }
        actual = {r["entry_id"]: r["wsdc_id"] for r in new_defaults}
        expected_nulls = {r["entry_id"] for r in withheld}
        placeholders = ",".join("?" for _ in expected_nulls)
        actual_nulls = (
            current.rows(
                f"SELECT entry_id,wsdc_id FROM entries WHERE entry_id IN ({placeholders})",
                tuple(expected_nulls),
            )
            if expected_nulls
            else []
        )
        checks["all_state_expansions_withheld_in_candidate"] = len(actual_nulls) == len(
            expected_nulls
        ) and all(r["wsdc_id"] is None for r in actual_nulls)
        checks["retained_default_joins_unchanged"] = actual == retained
        report["default_joins"]["expected_withheld_state_expansions"] = len(withheld)
    report["candidate_edges"] = current.count("SELECT count(*) FROM link_candidates")
    report["passed"] = all(checks.values())
    for dataset in (old, current, state):
        dataset.db.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.baseline, args.state_dir, args.candidate)
    rendered = json.dumps(result, indent=2, default=str) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
