"""Capture and verify the published H16 state without accepting new inputs.

Run only with the named frozen runtime. The default exports review evidence;
--checkpoint adds a new local checkpoint, and --upload acknowledges it remotely.
Neither operation starts workers, migrates state, or changes public output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path("/nix/store/z689qy41inndill3d92ym8im852x3649-source")
SYSTEM = "/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
STATE = Path("/var/lib/swingset")
BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
BUNDLE = "fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a"
SOURCE_RECEIPT = "0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb"


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def guards() -> dict[str, str]:
    require((STATE / "operator-hold").is_file(), "operator hold missing")
    require(not (STATE / "RESTORE_PENDING").exists(), "restore pending")
    for path in ("/run/current-system", "/nix/var/nix/profiles/system"):
        require(str(Path(path).resolve()) == SYSTEM, "system pin changed")
    units = {}
    for kind in ("cycle", "backup", "summary"):
        for suffix in ("service", "timer"):
            unit = f"swingset-{kind}.{suffix}"
            status = subprocess.check_output(
                ["systemctl", "show", unit, "--property=ActiveState", "--value"], text=True
            ).strip()
            require(status == "inactive", f"ordinary unit active: {unit}")
            units[unit] = status
    require(sha(SOURCE / "h16-source.json") == SOURCE_RECEIPT, "source receipt changed")
    receipt = json.loads((SOURCE / "h16-source.json").read_bytes())
    for name, value in receipt["files"].items():
        expected = value["sha256"] if isinstance(value, dict) else value
        require(sha(SOURCE / name) == expected, f"source file differs: {name}")
    require(
        json.loads((STATE / "baseline/PUBLISHED").read_bytes())["commit"] == BASELINE,
        "public baseline changed",
    )
    return units


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    require(not args.output.exists(), "output already exists")
    require(not args.upload or args.checkpoint is not None, "upload requires checkpoint")
    if args.checkpoint:
        require(not args.checkpoint.exists(), "checkpoint already exists")
        require(args.checkpoint.parent.resolve() == STATE / "checkpoints", "bad checkpoint path")
    from swingset.backup.checkpoint import create_checkpoint, verify_checkpoint
    from swingset.history.review import review_pack
    from swingset.schedule.cycle import versions
    from swingset.state import db as db_module

    import inspect

    for function in (create_checkpoint, verify_checkpoint, review_pack, versions):
        require(
            Path(inspect.getfile(function)).resolve().is_relative_to(SOURCE),
            "helper imported outside frozen source",
        )

    require(
        Path(db_module.__file__).resolve() == SOURCE / "src/swingset/state/db.py"
        and db_module.SCHEMA_VERSION == 14,
        "mixed or unexpected runtime",
    )
    units = guards()
    args.output.mkdir(parents=True)
    report = dict(
        format="h16-post-publication-checkpoint-v1",
        started_at=datetime.now(UTC).isoformat(),
        source=str(SOURCE),
        system=SYSTEM,
        baseline=BASELINE,
        units=units,
        passed=False,
    )
    try:
        with db_module.open_database(STATE, read_only=True, lock=True) as database:
            guards()
            conn = database.connection
            require(database.schema_version == 14, "live schema changed")
            require(
                conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]
                == BUNDLE,
                "accepted bundle changed",
            )
            report["review"] = review_pack(
                database,
                args.output / "year-review",
                now=datetime.now(UTC).isoformat(),
                reconcile=False,
            )
            report["phase1_files"] = {}
            for name in ("phase1-catalog.json", "phase1-ledger.json"):
                data = (STATE / name).read_bytes()
                (args.output / name).write_bytes(data)
                report["phase1_files"][name] = hashlib.sha256(data).hexdigest()
            if args.checkpoint:
                checkpoint = create_checkpoint(
                    STATE,
                    conn,
                    args.checkpoint,
                    schema_version=14,
                    versions=versions(),
                    input_bundle_hash=BUNDLE,
                )
                manifest = verify_checkpoint(checkpoint.path, maximum_schema_version=14)
                require(manifest["baseline_candidate"] == (STATE / "baseline").resolve().name,
                        "checkpoint baseline differs")
                require(manifest["pending_candidate"] is None, "unexpected pending candidate")
                require("operator-hold" in manifest["files"], "checkpoint omitted hold")
                report["checkpoint"] = dict(
                    path=str(checkpoint.path),
                    manifest_sha256=checkpoint.manifest_hash,
                    files=len(manifest["files"]),
                    bytes=sum(row["size"] for row in manifest["files"].values()),
                )
                if args.upload:
                    from swingset.backup.archive import upload_checkpoint
                    from swingset.backup.huggingface import HuggingFaceArchive

                    archive = HuggingFaceArchive("skeswa/swingset-archive", token=os.environ["HF_TOKEN"])
                    commit = upload_checkpoint(checkpoint, archive) or archive.head()
                    require(commit is not None, "archive acknowledgment missing")
                    require(archive.head() == commit, "archive head changed")
                    require(archive.manifest_hash(commit) == checkpoint.manifest_hash,
                            "remote checkpoint manifest differs")
                    report["private_archive_commit"] = commit
            require(conn.total_changes == 0, "live database writes occurred")
            report["live_database_changes"] = conn.total_changes
            guards()
        report["passed"] = True
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        with (args.output / "receipt.json").open("x") as handle:
            json.dump(report, handle, indent=2, default=str)
            handle.write("\n")


if __name__ == "__main__":
    main()
