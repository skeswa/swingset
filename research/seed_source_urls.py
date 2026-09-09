"""Print reviewed WDR source URL override candidates from research CSV.

This is a one-off seed helper. It only emits event-specific UUID URLs; host-only
research references are reported to stderr instead of becoming invalid watches.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID


def rows(path: Path) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            url = row.get("scores_url") or row.get("results_url") or ""
            if urlparse(url).hostname != "scores.worlddanceregistry.com":
                continue
            key = urlparse(url).path.strip("/").split("/")[0]
            try:
                UUID(key)
            except ValueError:
                print(f"Skipped {row['event_key']}: research has no event UUID", file=sys.stderr)
                continue
            output.append(
                {
                    "event_id": row["event_key"],
                    "source": "worlddanceregistry",
                    "kind": "event",
                    "url": f"https://scores.worlddanceregistry.com/{key}/rounds/routeInfo.json",
                    "parser": "wdr.rounds",
                    "notes": f"seeded from research/results-sources.csv; confidence={row.get('confidence', '')}",
                }
            )
    return output


def main() -> None:
    fieldnames = ["event_id", "source", "kind", "url", "parser", "notes"]
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows(Path(__file__).with_name("results-sources.csv")))


if __name__ == "__main__":
    main()
