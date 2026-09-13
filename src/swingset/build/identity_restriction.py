"""The V1 release restriction; evidence stays intact while joins cannot expand."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .builder import BuildError, BuildInput
from .files import sha256_file


def _baseline_ids(baseline: Path, table: str, key: str) -> tuple[dict[str, int], dict[str, str]]:
    files = sorted((baseline / "data" / table).glob("*.parquet"))
    if not files:
        raise BuildError(f"identity restriction requires baseline {table}")
    ids: dict[str, int] = {}
    hashes = {}
    seen = set()
    for path in files:
        hashes[str(path.relative_to(baseline))] = sha256_file(path)
        for batch in pq.ParquetFile(path).iter_batches(columns=[key, "wsdc_id"]):
            for row in batch.to_pylist():
                identifier = str(row[key])
                if identifier in seen:
                    raise BuildError(f"duplicate baseline {table} identity: {identifier}")
                seen.add(identifier)
                if row["wsdc_id"] is not None:
                    ids[identifier] = int(row["wsdc_id"])
    return ids, hashes


def restrict_identity_expansion(
    data: BuildInput, baseline: Path
) -> tuple[BuildInput, dict[str, Any]]:
    """Intersect default subject IDs with a published baseline, without writes.

    The release driver must fingerprint the returned baseline identity and its
    policy version. This does not alter the canonical state or evidence tables.
    """
    baseline = baseline.resolve()
    published_path = baseline / "PUBLISHED"
    if not published_path.is_file():
        raise BuildError("identity restriction requires a published baseline receipt")
    published = json.loads(published_path.read_text())
    if not isinstance(published, dict) or not published.get("commit"):
        raise BuildError("identity restriction baseline receipt has no commit")
    tables = dict(data.tables)
    report: dict[str, Any] = {
        "policy": "v1_baseline_default_joins_v1",
        "baseline_commit": str(published["commit"]),
        "baseline_path": str(baseline),
        "baseline_file_hashes": {"PUBLISHED": sha256_file(published_path)},
        "subjects": {},
    }
    for table, key in (("entries", "entry_id"), ("judges", "judge_id")):
        allowed, hashes = _baseline_ids(baseline, table, key)
        report["baseline_file_hashes"].update(hashes)
        retained = withheld = 0
        output = []
        for source in data.tables.get(table, ()):
            row = dict(source)
            dancer = row.get("wsdc_id")
            if dancer is not None:
                if allowed.get(str(row[key])) == dancer:
                    retained += 1
                else:
                    row["wsdc_id"] = None
                    withheld += 1
            output.append(row)
        tables[table] = output
        report["subjects"][table] = {
            "baseline_default_ids": len(allowed),
            "retained_default_ids": retained,
            "withheld_default_ids": withheld,
        }
    entries = {row["entry_id"]: row for row in tables["entries"]}
    placements = []
    placement_ids_withheld = points_withheld = changed_rows = 0
    for source in data.tables.get("placements", ()):
        row = dict(source)
        for role in ("leader", "follower"):
            identity = f"{role}_wsdc_id"
            points = f"registry_points_{role}"
            entry = entries.get(row.get(f"{role}_entry_id"))
            if row.get(identity) is not None and (
                entry is None or entry.get("role") != role or entry.get("wsdc_id") != row[identity]
            ):
                row[identity] = None
                placement_ids_withheld += 1
            if row.get(identity) is None and row.get(points) is not None:
                row[points] = None
                points_withheld += 1
        if row != source:
            # Expected-points agreement was evaluated with the old identities.
            row["points_matches_expected"] = None
        if row.get("registry_points_leader") is None or row.get("registry_points_follower") is None:
            row["registry_confirmed"] = False
        if (
            row.get("registry_points_leader") is None
            and row.get("registry_points_follower") is None
        ):
            row["points_matches_expected"] = None
        changed_rows += row != source
        placements.append(row)
    tables["placements"] = placements
    report["placements"] = {
        "withheld_default_ids": placement_ids_withheld,
        "withheld_registry_points": points_withheld,
        "changed_rows": changed_rows,
    }
    return replace(data, tables=tables), report
