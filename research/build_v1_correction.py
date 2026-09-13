#!/usr/bin/env python3
"""Build the V1 identity correction under an explicit baseline restriction.

Operational release preparation only: no fetching, publication, or decisions.
The ordinary pipeline must be settled first. H10 owns the later general
correction-only path, including isolation from unrelated failed work.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from swingset.build.builder import BuildInput, BuildMetadata, build_candidate
from swingset.build.identity_restriction import restrict_identity_expansion
from swingset.build.input import read_build_input
from swingset.clock import SystemClock
from swingset.publish.card import render_card
from swingset.publish.service import pending_candidates
from swingset.schedule.cycle import baseline_commit, versions
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    clock = SystemClock()
    with open_database(args.state, lock_timeout=60) as database:
        if not (args.state / "operator-hold").exists():
            raise ValueError("V1 release preparation requires the scheduled-service hold")
        if pending_candidates(args.state):
            raise ValueError("reconcile pending publication before V1 release preparation")
        parent = baseline_commit(args.state)
        if parent is None:
            raise ValueError("V1 correction requires an acknowledged baseline")
        bundle = capture(args.config, args.overrides, args.state, versions())
        accept(database, bundle, clock)
        with database.transaction(immediate=False) as conn:
            data = read_build_input(conn, bundle)
        restricted, report = restrict_identity_expansion(data, (args.state / "baseline").resolve())
        if report["baseline_commit"] != parent:
            raise ValueError("identity restriction baseline changed")
        policy = report["policy"]
        restriction_inputs = {
            "policy": policy,
            "baseline_commit": parent,
            "baseline_file_hashes": report["baseline_file_hashes"],
        }
        restricted = replace(
            restricted,
            captured_file_hashes={
                **restricted.captured_file_hashes,
                "v1_identity_restriction": sha256(
                    json.dumps(restriction_inputs, sort_keys=True).encode()
                ).hexdigest(),
            },
        )
        note = (
            "\n\n## V1 identity correction\n\n"
            f"This release restricts default identity joins to baseline `{parent}`. "
            "It removes unsupported and mixed-person joins, preserves candidate evidence, "
            "and withholds all added or changed default identities. Named judges can have "
            "no WSDC number; their null IDs are valid. Representative reviewed precision "
            "is unavailable. Historical event inventory remains unaccepted where coverage "
            "or review findings are open.\n"
            f"\nPublication policy: `{policy}`.\n"
        ).encode()

        def card(rows: BuildInput) -> bytes:
            return render_card(rows) + note

        captured_versions = json.loads(bundle.files["versions.json"])
        captured_versions["publication_policy"] = policy
        run_id = database.start_run(clock.now())
        metadata = BuildMetadata(
            run_id=run_id,
            repository_commit=captured_versions["repository"],
            expected_parent=parent,
            schema_version=1,
            versions=captured_versions,
            card=card(restricted),
            built_at=clock.now(),
        )
        result = build_candidate(
            args.state,
            restricted,
            metadata,
            suppressions=bundle.csv("suppressions.csv"),
            card_renderer=card,
        )
        receipt = {
            "candidate": str(result.path),
            "manifest_hash": result.manifest_hash,
            "policy": policy,
            "baseline_commit": parent,
            "input_bundle_hash": bundle.digest,
            "restriction": report,
            "published": False,
            "network_requests": 0,
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        with database.transaction() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
                (clock.now().isoformat(), json.dumps(receipt, sort_keys=True), run_id),
            )
        print(result.path)


if __name__ == "__main__":
    main()
