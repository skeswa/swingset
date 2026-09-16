#!/usr/bin/env python3
"""Verify artifacts referenced by a captured state against a local archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.capture / 'state.sqlite'}?mode=ro", uri=True)
    result: dict[str, object] = {"checked_at": datetime.now(UTC).isoformat()}
    for kind, column in [("body", "body_sha256"), ("extract", "extract_sha256")]:
        digests = [
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT {column} FROM snapshots WHERE {column} IS NOT NULL"
            )
        ]
        missing, corrupt = [], []
        for sha in digests:
            path = (
                (args.archive / "blobs/sha256" / sha[:2] / sha[2:4] / sha)
                if kind == "body"
                else args.archive / "extracts" / sha
            )
            try:
                body = path.read_bytes()
                if kind == "body":
                    body = gzip.decompress(body)
                if hashlib.sha256(body).hexdigest() != sha:
                    corrupt.append(sha)
            except FileNotFoundError:
                missing.append(sha)
            except (OSError, EOFError, ValueError):
                corrupt.append(sha)
        result[kind] = {
            "referenced_unique_artifacts": len(digests),
            "missing": missing,
            "corrupt": corrupt,
        }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    conn.close()


if __name__ == "__main__":
    main()
