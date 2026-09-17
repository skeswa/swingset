"""Build a standalone JesAnn Nail history page from the pinned report exports."""

import argparse
import json
from pathlib import Path

TABLES = (
    "profile",
    "registry_results",
    "score_sheet_results",
    "judging_appearances",
    "callbacks",
    "callback_marks_received",
    "final_marks_received",
    "callback_marks_given",
    "final_marks_given",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exports", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--jes-test",
        type=Path,
        required=True,
        help="Jes Test report directory from journal.tools.quality.jes_test",
    )
    args = parser.parse_args()
    source = Path(__file__).parent
    data = {name: json.loads((args.exports / f"{name}.json").read_text()) for name in TABLES}
    data["release"] = json.loads((args.exports / "dataset/PUBLISHED").read_text())
    summary = json.loads((args.jes_test / "summary.json").read_text())
    benchmark_release = summary.get("release", {})
    page_release = data["release"]
    if (
        summary.get("benchmark") != "Jes Test"
        or benchmark_release.get("commit") != page_release.get("commit")
        or benchmark_release.get("candidate_id") != page_release.get("candidate_id")
    ):
        raise ValueError("The Jes Test report must match the page's pinned published dataset")
    data["jes_test"] = {
        "summary": summary,
        "rows": json.loads((args.jes_test / "entries.json").read_text()),
    }
    if [row["wsdc_id"] for row in data["profile"]] != [7849]:
        raise ValueError("This profile page is designed for JesAnn Nail, WSDC 7849")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    page = (source / "page.html").read_text()
    for marker, content in (
        ("/* PAGE_STYLES */", (source / "style.css").read_text()),
        ("/* PAGE_SCRIPT */", (source / "app.js").read_text()),
        ("DATA_PAYLOAD", payload),
    ):
        if page.count(marker) != 1:
            raise ValueError(f"Expected exactly one {marker} marker")
        page = page.replace(marker, content)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page)
    print(f"Built {args.output} ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
