"""Identity override CSV parsing."""

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IdentityOverride:
    entry_id: str
    wsdc_id: int | None
    reason: str
    author: str
    date: str


def load_overrides(path: Path) -> dict[str, IdentityOverride]:
    result: dict[str, IdentityOverride] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw = row["wsdc_id"].strip()
            result[row["entry_id"]] = IdentityOverride(
                row["entry_id"],
                None if raw.upper() == "NONE" else int(raw),
                row["reason"],
                row["author"],
                row["date"],
            )
    return result
