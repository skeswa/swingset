"""Build and time a portable H17 packet from settled retained state, offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

from swingset.fetch.archive import Archive
from swingset.link.evaluation import draw_sample
from swingset.link.evaluation_input import read_population
from swingset.link.evaluation_packet import write_packet
from swingset.state.db import open_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--per-stratum", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("packet destination already exists")
    start = perf_counter()
    with open_database(args.state, read_only=True, lock=False) as db:
        with db.transaction(immediate=False) as conn:
            rows, context = read_population(conn)
    receipt = {
        "state": str(args.state),
        "population_size": len(rows),
        "read_population_seconds": perf_counter() - start,
        "network_requests": 0,
        "live_mutations": 0,
    }
    print(json.dumps(receipt), flush=True)
    start = perf_counter()
    sample = draw_sample(
        rows,
        seed=args.seed,
        cohort=args.cohort,
        cutoff=args.cutoff,
        per_stratum=args.per_stratum,
        context=context,
    )
    receipt["draw_sample_seconds"] = perf_counter() - start
    del rows
    start = perf_counter()
    receipt["packet"] = write_packet(sample, Archive(args.state), args.output)
    receipt["write_packet_seconds"] = perf_counter() - start
    receipt["strata"] = sample["strata"]
    receipt["split_groups"] = sample["split_groups"]
    receipt["sample_digest"] = sample["sample_digest"]
    with (args.output / "timing.json").open("x") as output:
        json.dump(receipt, output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
