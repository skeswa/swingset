"""Run H6 source admission contracts over retained archives, entirely offline."""

import argparse
import json
from pathlib import Path

from swingset.admission.corpus import write_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--page-kind", action="append")
    args = parser.parse_args()
    receipt = write_corpus(
        tuple(args.state),
        args.output,
        cutoff=args.cutoff,
        page_kinds=None if args.page_kind is None else tuple(args.page_kind),
    )
    print(
        json.dumps({key: value for key, value in receipt.items() if key != "examples"}), flush=True
    )


if __name__ == "__main__":
    main()
