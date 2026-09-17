#!/usr/bin/env python3
"""Prepare an exact phase-one resume gate from read-only, locked production state."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SOURCE = Path("/nix/store/z689qy41inndill3d92ym8im852x3649-source")
RECEIPT_SHA = "0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb"
BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assemble_gate(
    conn: Any, state: Path, driver: Any, driver_path: Path, targets: Any, *, now: datetime
) -> dict[str, Any]:
    """Read actual identities and statuses; the driver validates the finite remainder."""
    ledger_path = state / "phase1-ledger.json"
    ledger = json.loads(ledger_path.read_bytes())["targets"]
    baseline = (state / "baseline").resolve(strict=True)
    if json.loads((baseline / "PUBLISHED").read_bytes())["commit"] != BASELINE:
        raise ValueError("public baseline changed")
    return {
        "format": "phase1-17-resume-v1",
        "coordinator_reviewed": True,
        "state": str(state),
        "source": str(SOURCE),
        "source_receipt": "h16-source.json",
        "source_receipt_sha256": RECEIPT_SHA,
        "driver_sha256": sha(driver_path),
        "schema": 14,
        "inputs": json.loads(json.dumps(driver.inputs(conn))),
        "baseline_path": str(baseline),
        "published_sha256": sha(baseline / "PUBLISHED"),
        "manifest_sha256": sha(baseline / "_meta/manifest.json"),
        "catalog_sha256": sha(state / "phase1-catalog.json"),
        "ledger_sha256": sha(ledger_path),
        "utc_day": now.date().isoformat(),
        "wall_seconds": 600,
        "max_additional_requests": 48,
        "remaining_target_ids": sorted(
            t.target_id for t in targets
            if ledger.get(t.target_id, {}).get("status", "pending") == "pending"
        ),
        "original_targets": {
            t.target_id: {k: getattr(t, k) for k in ("source", "parser", "url", "timestamp")}
            for t in targets if t.target_id in driver.TARGET_IDS
        },
        "target_statuses": {
            k: ledger.get(k, {}).get("status", "pending") for k in sorted(driver.TARGET_IDS)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--driver-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--coordinator-reviewed", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    state = args.state.resolve(strict=True)
    if state != Path("/var/lib/swingset") or not (state / "operator-hold").is_file():
        raise ValueError("held production state required")
    if (state / "RESTORE_PENDING").exists():
        raise ValueError("restore pending")
    if sha(args.driver) != args.driver_sha256 or sha(SOURCE / "h16-source.json") != RECEIPT_SHA:
        raise ValueError("reviewed driver/source receipt changed")
    for name, value in json.loads((SOURCE / "h16-source.json").read_bytes())["files"].items():
        if sha(SOURCE / name) != (value["sha256"] if isinstance(value, dict) else value):
            raise ValueError(f"source inventory differs: {name}")
    spec = importlib.util.spec_from_file_location("reviewed_resume_driver", args.driver)
    assert spec is not None and spec.loader is not None
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    from swingset import sources
    from swingset.history import catalog
    from swingset.state import db

    for module in (catalog, sources, db):
        if not Path(module.__file__).resolve().is_relative_to(SOURCE / "src"):
            raise ValueError("mixed runtime")
    with db.open_database(state, read_only=True, lock=True) as database:
        if not (state / "operator-hold").is_file() or (state / "RESTORE_PENDING").exists():
            raise ValueError("hold or restore state changed while acquiring lock")
        if database.schema_version != 14 or db.SCHEMA_VERSION != 14:
            raise ValueError("exact schema14 required")
        gate = assemble_gate(
            database.connection, state, driver, args.driver,
            catalog.load_catalog(state / "phase1-catalog.json"), now=datetime.now(UTC)
        )
        driver.check_authority(database.connection, state, gate)
        driver.check_targets(database.connection, state, gate, catalog.load_catalog, sources.get_page_kind)
        if database.connection.total_changes:
            raise ValueError("unexpected database mutation")
        with args.output.open("x") as handle:
            json.dump(gate, handle, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps({"gate_sha256": sha(args.output), "remaining": len(gate["remaining_target_ids"])}))


if __name__ == "__main__":
    main()
