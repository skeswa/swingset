"""Explain exact payload changes after an isolated H7 rehearsal, read-only."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearsal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads((args.rehearsal / "receipt.json").read_text())
    baseline = sqlite3.connect(f"file:{Path(receipt['source']) / 'state.sqlite'}?mode=ro", uri=True)
    current = sqlite3.connect(f"file:{args.rehearsal / 'state/state.sqlite'}?mode=ro", uri=True)
    report = {"rehearsal": str(args.rehearsal), "source": receipt["source"], "kinds": {}}
    try:
        for kind, summary in receipt["kinds"].items():
            fields, methods = Counter(), Counter()
            semantic_changes = 0
            if summary["changed"]:
                for row in current.execute(
                    "SELECT o.watch_id,o.snapshot_id,o.kind,o.seq,o.payload_json FROM watches w "
                    "JOIN observations o USING(watch_id) WHERE w.parser=?",
                    (kind,),
                ):
                    prior = baseline.execute(
                        "SELECT payload_json FROM observations WHERE watch_id=? AND snapshot_id=? AND kind=? AND seq=?",
                        tuple(row[:4]),
                    ).fetchone()
                    if prior is None or prior[0] == row[4]:
                        continue
                    before, after = json.loads(prior[0]), json.loads(row[4])
                    changed = sorted(
                        key
                        for key in before.keys() | after.keys()
                        if before.get(key) != after.get(key) or (key in before) != (key in after)
                    )
                    fields.update(changed)
                    if changed == ["scoring_method_raw"]:
                        methods[str(after.get("scoring_method_raw"))] += 1
                    else:
                        semantic_changes += 1
            report["kinds"][kind] = {
                "payloads_changed": summary["changed"],
                "changed_fields": dict(fields),
                "method_annotation_only": dict(methods),
                "other_payload_changes": semantic_changes,
                "added": summary["added"],
                "removed": summary["removed"],
                "added_scopes": summary["added_scopes"],
                "removed_scopes": summary["removed_scopes"],
                "newly_unaccepted_generations": summary["newly_unaccepted_generations"],
            }
    finally:
        current.close()
        baseline.close()
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
