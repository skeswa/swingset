"""Replay explicitly reviewed admission contracts on a new disposable SQLite copy.

No network client is imported. The retained archive is read-only; new extracts
and all database mutations stay in the new output directory. This compares
selected source observations and their scopes, not public canonical tables.
"""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from swingset.admission.policy import activate_contract, record_corpus_review
from swingset.admission.support import admission_summary
from swingset.clock import SystemClock
from swingset.fetch.archive import Archive
from swingset.schedule.parse import parse_snapshot
from swingset.sources.base import JsonValue
from swingset.state.db import open_database
from swingset.state.work import WorkUnit


class OverlayArchive(Archive):
    """Read retained artifacts without writing through into the source archive."""

    def __init__(self, state: Path, retained: Path):
        super().__init__(state)
        self.retained = Archive(retained)

    def read_body(self, sha256: str) -> bytes:
        try:
            return super().read_body(sha256)
        except FileNotFoundError:
            return self.retained.read_body(sha256)

    def read_extract(self, sha256: str) -> JsonValue:
        try:
            return super().read_extract(sha256)
        except FileNotFoundError:
            return self.retained.read_extract(sha256)


def selected(conn: sqlite3.Connection, kind: str) -> dict[str, tuple[str, str]]:
    records = {}
    for row in conn.execute(
        "SELECT o.watch_id,o.snapshot_id,o.kind,o.scope_kind,o.scope_id,o.seq,o.payload_json "
        "FROM watches w JOIN observations o USING(watch_id) WHERE w.parser=?",
        (kind,),
    ):
        key = json.dumps(tuple(row[:6]), separators=(",", ":"))
        records[key] = (hashlib.sha256(row[6].encode()).hexdigest(), f"{row[3]}:{row[4]}")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument("--page-kind", action="append", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--reviewed-at", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    started = monotonic()
    args.output.mkdir(parents=True, exist_ok=False)
    state = args.output / "state"
    state.mkdir()
    source = sqlite3.connect(f"file:{args.source / 'state.sqlite'}?mode=ro", uri=True)
    target = sqlite3.connect(state / "state.sqlite")
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    archive, clock = OverlayArchive(state, args.source), SystemClock()
    report = {
        "source": str(args.source),
        "scratch": str(state),
        "started_at": datetime.now(UTC).isoformat(),
        "reviewer": args.reviewer,
        "reviewed_at": args.reviewed_at,
        "review_evidence": args.evidence,
        "corpus_digest": args.expected_digest,
        "network_requests": 0,
        "production_mutations": 0,
        "comparison": "Selected source observations and scopes; canonical projection/publication not run",
        "kinds": {},
    }
    with open_database(state) as db:
        conn = db.connection
        run = db.start_run(clock.now())
        for kind in dict.fromkeys(args.page_kind):
            before = selected(conn, kind)
            with db.transaction():
                reviewed = record_corpus_review(
                    conn,
                    args.corpus,
                    kind,
                    expected_digest=args.expected_digest,
                    reviewer=args.reviewer,
                    reviewed_at=args.reviewed_at,
                    evidence=args.evidence,
                )
                activate_contract(conn, kind, reviewed)
            units = [
                str(row[0])
                for row in conn.execute(
                    "SELECT p.unit_id FROM pending_work p JOIN snapshots s ON s.snapshot_id=p.unit_id "
                    "JOIN watches w USING(watch_id) WHERE p.stage='parse' AND p.unit_kind='snapshot' AND w.parser=? "
                    "ORDER BY COALESCE(s.observed_at,s.fetched_at),s.snapshot_id",
                    (kind,),
                )
            ]
            for number, snapshot in enumerate(units, 1):
                parse_snapshot(db, archive, WorkUnit("parse", "snapshot", snapshot), clock, run)
                if number % 1000 == 0:
                    print(
                        json.dumps({"page_kind": kind, "processed": number, "total": len(units)}),
                        flush=True,
                    )
            after = selected(conn, kind)
            added, removed = after.keys() - before.keys(), before.keys() - after.keys()
            changed = {key for key in after.keys() & before.keys() if after[key] != before[key]}
            before_scopes, after_scopes = (
                {v[1] for v in before.values()},
                {v[1] for v in after.values()},
            )
            decisions = dict(
                Counter(
                    row[0]
                    for row in conn.execute(
                        "SELECT d.reason FROM admission_decisions d JOIN source_generations g USING(generation_id) "
                        "WHERE g.page_kind=? AND g.run_id=?",
                        (kind, run),
                    )
                )
            )
            report["kinds"][kind] = {
                "attempts": len(units),
                "before": len(before),
                "after": len(after),
                "added": len(added),
                "removed": len(removed),
                "changed": len(changed),
                "added_scopes": sorted(after_scopes - before_scopes),
                "removed_scopes": sorted(before_scopes - after_scopes),
                "changed_witness_examples": sorted(changed)[:100],
                "removed_witness_examples": sorted(removed)[:100],
                "decisions": decisions,
                "newly_unaccepted_generations": conn.execute(
                    "SELECT count(*) FROM source_generations WHERE page_kind=? AND run_id=? AND state IN ('needs_review','waiting_for_inputs')",
                    (kind, run),
                ).fetchone()[0],
                "legacy_units_remaining": conn.execute(
                    "SELECT count(*) FROM source_units WHERE page_kind=? AND legacy_state='legacy_unassessed'",
                    (kind,),
                ).fetchone()[0],
            }
            (args.output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({"page_kind": kind, **report["kinds"][kind]}), flush=True)
        report["admission"] = admission_summary(conn)
        report["foreign_key_violations"] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        report["elapsed_seconds"] = monotonic() - started
        with db.transaction():
            conn.execute(
                "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
                (
                    clock.now().isoformat(),
                    json.dumps({"admission_rehearsal": True}),
                    run,
                ),
            )
    (args.output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
