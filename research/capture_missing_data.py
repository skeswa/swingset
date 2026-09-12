#!/usr/bin/env python3
"""Capture a consistent SQLite backup and pin the published candidate for an offline audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be empty so captures cannot be mixed")
    args.output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    baseline = (args.state / "baseline").resolve(strict=True)
    receipt = json.loads((baseline / "PUBLISHED").read_text())
    source = sqlite3.connect(f"file:{args.state / 'state.sqlite'}?mode=ro", uri=True)
    with sqlite3.connect(args.output / "state.sqlite") as destination:
        source.backup(destination)
    source.close()
    shutil.copytree(baseline, args.output / "candidate")
    with sqlite3.connect(f"file:{args.output / 'state.sqlite'}?mode=ro", uri=True) as captured:
        sha = captured.execute(
            "SELECT value FROM meta WHERE key='registry_crosscheck_blob'"
        ).fetchone()[0]
    shutil.copy2(
        args.state / "blobs/sha256" / sha[:2] / sha[2:4] / sha, args.output / "comparison.json.gz"
    )
    metadata = {
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "candidate": str(baseline),
        "publication": receipt,
        "baseline_unchanged": (args.state / "baseline").resolve() == baseline,
        "comparison_sha256": sha,
        "state_sha256": hashlib.sha256((args.output / "state.sqlite").read_bytes()).hexdigest(),
    }
    (args.output / "capture.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
