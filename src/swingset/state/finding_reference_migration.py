"""Turn what the file closure guessed about findings into what findings declare.

Migration 30 adds `finding_support_references`. Before it, the closure read every
open finding's evidence JSON, took every string shaped like a sha256, and kept
the blob or extract of that name if such a file existed. This backfill repeats
that exact rule once, so no file that was pinned before the migration stops
being pinned by it, and the closure never has to guess again.

It runs inside the migration's own transaction. A finding whose evidence is not
JSON is recorded with no references rather than failing the migration: evidence
is written by many callers over a long history, and a retention migration is not
where that is discovered.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def backfill_finding_references(conn: sqlite3.Connection, state_dir: Path) -> int:
    """Declare the body and extract digests every finding's evidence already named."""
    from .retention import declared_digests

    written = 0
    findings = conn.cursor()
    for finding_id, evidence_json in findings.execute(
        "SELECT finding_id,evidence_json FROM findings ORDER BY finding_id"
    ):
        try:
            digests = sorted(set(declared_digests(str(evidence_json))))
        except ValueError:
            continue
        for digest in digests:
            blob = state_dir / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest
            for kind, path in (("body", blob), ("extract", state_dir / "extracts" / digest)):
                if not path.is_file():
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO finding_support_references VALUES (?,?,?)",
                    (finding_id, kind, digest),
                )
                written += 1
    return written
