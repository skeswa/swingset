"""Apply reviewed V4 admission and phase1 evidence with acquisition held.

This operational driver makes no source requests or publication. Each source,
projection and identity unit commits through the production stage boundary.
An interrupted run retains its pending work and the acknowledged public baseline.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from time import monotonic

from journal.tools.admission.bootstrap_parse import BootstrapParser
from swingset.admission.policy import activate_contract, record_corpus_review
from swingset.admission.support import admission_summary
from swingset.backup.checkpoint import verify_checkpoint
from swingset.build.files import sha256_file
from swingset.build.service import build_release
from swingset.clock import SystemClock
from swingset.fetch.archive import Archive
from swingset.history.transfer import import_evidence
from swingset.link import link_event
from swingset.project import process_unit
from swingset.publish.service import pending_candidates
from swingset.schedule.cycle import baseline_commit, versions
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture
from swingset.state.work import next_work

CONTRACT4_DIGEST = "7c944ac81361b0a343e34a29ead875b3fe6363f7750d41fda47ddec5bb400c71"
CONTRACT5_DIGEST = "6750eccd884a572c00a2f525d719a8c434b1dfdea73367558c0cb490e63c7755"
KINDS4 = (
    "wsdc_registry.dancer",
    "eepro.index",
    "eepro.round",
    "scoringdance.sitemap",
    "scoringdance.recent",
    "scoringdance.event",
    "scoringdance.round",
    "wdr.rounds",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--corpus4", type=Path, required=True)
    parser.add_argument("--corpus5", type=Path, required=True)
    parser.add_argument("--phase1", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--operations", type=Path, required=True)
    parser.add_argument("--expected-parent", required=True)
    args = parser.parse_args()
    if not (args.state / "operator-hold").exists():
        raise ValueError("Bootstrap requires the scheduled-service hold")
    if baseline_commit(args.state) != args.expected_parent or pending_candidates(args.state):
        raise ValueError(
            "Bootstrap requires the reviewed published baseline without an active intent"
        )
    checkpoint = verify_checkpoint(args.checkpoint, maximum_schema_version=9)
    if checkpoint["baseline_candidate"] != (args.state / "baseline").resolve().name:
        raise ValueError("Bootstrap checkpoint must retain the current public baseline")
    if (
        sha256_file(args.phase1 / "phase1-export.json")
        != "7b5954a5cca0bf46f29bc1176c3030787cf0b6fbc36a4849112a51e4822f5720"
    ):
        raise ValueError("Phase1 transfer differs from the independently rehearsed package")
    clock = SystemClock()
    args.operations.mkdir(parents=True, exist_ok=True)
    previous = None
    prior_path = args.operations / "replay.json"
    if prior_path.exists():
        previous = json.loads(prior_path.read_bytes())
        if previous.get("finished_at"):
            raise ValueError("Completed bootstrap already has a release candidate to review")
        if previous.get("expected_parent") != args.expected_parent:
            raise ValueError("Interrupted bootstrap has a different published baseline")
        attempts = args.operations / "attempts"
        attempts.mkdir(exist_ok=True)
        saved = attempts / (previous["run_id"] + ".json")
        if saved.exists() and saved.read_bytes() != prior_path.read_bytes():
            raise ValueError("Prior attempt receipt changed after retention")
        if not saved.exists():
            saved.write_bytes(prior_path.read_bytes())
    started = monotonic()
    receipt = {
        "source": str(args.source),
        "state": str(args.state),
        "expected_parent": args.expected_parent,
        "network_requests": 0,
        "published": False,
        "stages": {},
    }
    if previous is not None:
        receipt["resumed_from"] = {
            "receipt": str(saved),
            "sha256": sha256_file(saved),
            "run_id": previous["run_id"],
            "source": previous["source"],
        }

    def save() -> None:
        (args.operations / "replay.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        )

    with open_database(args.state, lock_timeout=60) as database:
        conn = database.connection
        original_acquisition = {
            str(row[0]) for row in conn.execute("SELECT watch_id FROM watches WHERE kind!='index'")
        }
        prior_acceptance = [
            tuple(row) for row in conn.execute("SELECT * FROM history_acceptance ORDER BY year")
        ]
        bundle = capture(args.source / "config", args.overrides, args.state, versions())
        receipt["changed_inputs"] = sorted(accept(database, bundle, clock))
        run_id = database.start_run(clock.now())
        receipt["run_id"] = run_id
        receipt["phase1_import"] = import_evidence(database, args.phase1, clock=clock)
        policies = {}
        for kind in (*KINDS4, "eepro.autoindex"):
            corpus, digest = (
                (args.corpus5, CONTRACT5_DIGEST)
                if kind == "eepro.autoindex"
                else (args.corpus4, CONTRACT4_DIGEST)
            )
            with database.transaction():
                reviewed = record_corpus_review(
                    conn,
                    corpus,
                    kind,
                    expected_digest=digest,
                    reviewer="Codex coordinator",
                    reviewed_at=clock.now().isoformat(),
                    evidence="Reviewed retained corpus and isolated replay; journal/investigations/2026/h6-contract4-review-2026-09-13.md and journal/investigations/2026/h6-autoindex-contract5-review-2026-09-13.md",
                )
                existing = conn.execute(
                    "SELECT mode,reviewed_report_digest FROM admission_policies WHERE page_kind=?",
                    (kind,),
                ).fetchone()
                if existing is None or tuple(existing) != ("enforce", reviewed):
                    activate_contract(conn, kind, reviewed)
                policies[kind] = reviewed
        receipt["policies"] = policies
        save()
        guarded = BootstrapParser(database, Archive(args.state), clock, run_id)
        for stage in ("parse", "project", "link"):
            stage_started, count = monotonic(), 0
            failures = Counter()
            gated = []
            while (unit := next_work(conn, stage)) is not None:
                if stage == "parse":
                    result = guarded.parse(unit)
                    failures[result.attempt.source] += int(result.attempt.failed)
                    gated.extend(result.gated_watch_ids)
                elif stage == "project":
                    process_unit(database, unit, bundle, clock, run_id)
                else:
                    link_event(database, unit.unit_id, bundle, clock, run_id)
                count += 1
                if next_work(conn, stage) == unit:
                    raise RuntimeError(f"Stage did not complete its current unit: {unit}")
                if count % (25 if stage == "link" else 1000) == 0:
                    print(
                        json.dumps(
                            {
                                "stage": stage,
                                "processed": count,
                                "elapsed_seconds": monotonic() - stage_started,
                            }
                        ),
                        flush=True,
                    )
            receipt["stages"][stage] = {
                "processed": count,
                "elapsed_seconds": monotonic() - stage_started,
                "parse_failures": dict(failures),
                "gated_new_rounds": gated,
            }
            if count == 0 and previous is not None and stage in previous["stages"]:
                receipt["stages"][stage] = {
                    **previous["stages"][stage],
                    "reused_completed_stage_from": previous["run_id"],
                }
            save()
            print(json.dumps({"stage": stage, **receipt["stages"][stage]}), flush=True)
        assert prior_acceptance == [
            tuple(row) for row in conn.execute("SELECT * FROM history_acceptance ORDER BY year")
        ]
        assert original_acquisition == {
            str(row[0]) for row in conn.execute("SELECT watch_id FROM watches WHERE kind!='index'")
        }
        receipt["new_acquisition_watches"] = 0
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        receipt["admission"] = admission_summary(conn)
        receipt["open_findings"] = dict(
            conn.execute("SELECT kind,COUNT(*) FROM findings WHERE closed_at IS NULL GROUP BY kind")
        )
        build_started = monotonic()
        result = build_release(database, bundle, clock, run_id)
        manifest = json.loads((result.path / "_meta/manifest.json").read_bytes())
        receipt["build"] = {
            "candidate": str(result.path),
            "manifest_hash": result.manifest_hash,
            "elapsed_seconds": monotonic() - build_started,
            "row_counts": manifest["row_counts"],
            "release_policy": manifest["release_policy"],
        }
        receipt["elapsed_seconds"] = monotonic() - started
        receipt["finished_at"] = clock.now().isoformat()
        save()
        with database.transaction():
            conn.execute(
                "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
                (receipt["finished_at"], json.dumps(receipt), run_id),
            )
        print(
            json.dumps({"candidate": str(result.path), "manifest_hash": result.manifest_hash}),
            flush=True,
        )


if __name__ == "__main__":
    main()
