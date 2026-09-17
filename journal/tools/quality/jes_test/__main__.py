"""Create an offline coverage and result-agreement report for JesAnn Nail."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import evaluate, load_dataset, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, required=True, help="verified release or candidate directory"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new report directory; existing reports are preserved",
    )
    parser.add_argument(
        "--baseline", type=Path, help="earlier Jes Test report directory to compare"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output directory already exists; choose a new path: {args.output}")
    baseline = None
    if args.baseline is not None:
        try:
            baseline = json.loads((args.baseline / "summary.json").read_text())
            baseline["rows"] = json.loads((args.baseline / "entries.json").read_text())
        except (OSError, json.JSONDecodeError) as error:
            parser.error(f"baseline must contain valid summary.json and entries.json: {error}")
    try:
        tables, release = load_dataset(args.dataset)
        report = evaluate(tables, release)
        write_report(report, args.output, baseline)
    except Exception as error:
        print(f"Jes Test failed: {error}", file=sys.stderr)
        return 2
    coverage = report["coverage"]
    rate = coverage["individual_results_percent"]
    display_rate = f"{rate:.1f}%" if rate is not None else "not assessable"
    print(
        f"Jes Test: {coverage['individual_results']}/{report['denominator']} registry entries covered "
        f"({display_rate}); reports written to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
