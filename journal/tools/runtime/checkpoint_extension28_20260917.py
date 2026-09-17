"""Capture held schema-28 extension state without accepting new inputs.

Run only with the named frozen runtime. The default exports review evidence;
--checkpoint adds a new local checkpoint, and --upload acknowledges it remotely.
Neither operation starts workers, migrates state, or changes public output.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SOURCE = Path("/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source")
SYSTEM = (
    "/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
)
STATE = Path("/var/lib/swingset")
BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
BUNDLE = "fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a"
SOURCE_RECEIPT = "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6"


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
    require(sha(SOURCE / "extension-source.json") == SOURCE_RECEIPT, "source receipt changed")
    receipt = json.loads((SOURCE / "extension-source.json").read_bytes())
    children = list(SOURCE.rglob("*"))
    actual = {
        p.relative_to(SOURCE).as_posix()
        for p in children
        if p.is_file() and "__pycache__" not in p.parts and p != SOURCE / "extension-source.json"
    }
    require(
        not any(p.is_symlink() for p in children) and actual == set(receipt["files"]),
        "runtime inventory differs",
    )
    for name, value in receipt["files"].items():
        require(
            not Path(name).is_absolute() and ".." not in Path(name).parts,
            "source receipt path escapes",
        )
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
    require(
        args.output.resolve() != STATE
        and not args.output.resolve().is_relative_to(SOURCE)
        and "checkpoints" not in args.output.resolve().parts,
        "unsafe output location",
    )
    require(not args.upload or args.checkpoint is not None, "upload requires checkpoint")
    if args.checkpoint:
        require(
            not args.checkpoint.exists(),
            "fresh checkpoint must not already exist",
        )
        require(args.checkpoint.parent.resolve() == STATE / "checkpoints", "bad checkpoint path")
    from swingset.backup.checkpoint import create_checkpoint, verify_checkpoint
    from swingset.history.review import review_pack
    from swingset.schedule.cycle import versions
    from swingset.state import db as db_module
    from swingset.state.control_lock import control_lock

    for function in (create_checkpoint, verify_checkpoint, review_pack, versions):
        require(
            Path(inspect.getfile(function)).resolve().is_relative_to(SOURCE),
            "helper imported outside frozen source",
        )

    require(
        Path(db_module.__file__).resolve() == SOURCE / "src/swingset/state/db.py"
        and db_module.SCHEMA_VERSION == 28,
        "mixed or unexpected runtime",
    )
    units = guards()
    hold_sha = sha(STATE / "operator-hold")
    args.output.mkdir(parents=True)
    report: dict[str, Any] = dict(
        format="held-extension-schema28-checkpoint-v1",
        started_at=datetime.now(UTC).isoformat(),
        source=str(SOURCE),
        system=SYSTEM,
        baseline=BASELINE,
        units=units,
        passed=False,
    )
    try:
        with (
            db_module.open_database(STATE, read_only=True, lock=True) as database,
            control_lock(STATE, timeout=0),
        ):
            guards()
            conn = database.connection
            require(
                database.schema_version == 28
                and conn.execute("PRAGMA user_version").fetchone()[0] == 28,
                "live schema changed",
            )
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
                guards()
                require(sha(STATE / "operator-hold") == hold_sha, "hold changed before checkpoint")
                checkpoint = create_checkpoint(
                    STATE,
                    conn,
                    args.checkpoint,
                    schema_version=28,
                    versions=versions(),
                    input_bundle_hash=BUNDLE,
                )
                manifest = verify_checkpoint(checkpoint.path, maximum_schema_version=28)
                require(manifest["schema_version"] == 28, "checkpoint schema differs")
                require(manifest["input_bundle_hash"] == BUNDLE, "checkpoint bundle differs")
                require(
                    manifest["baseline_candidate"] == (STATE / "baseline").resolve().name,
                    "checkpoint baseline differs",
                )
                require(manifest["pending_candidate"] is None, "unexpected pending candidate")
                require("operator-hold" in manifest["files"], "checkpoint omitted hold")
                report["checkpoint"] = dict(
                    path=str(checkpoint.path),
                    manifest_sha256=checkpoint.manifest_hash,
                    files=len(manifest["files"]),
                    bytes=sum(row["size"] for row in manifest["files"].values()),
                )
                print(
                    json.dumps(dict(phase="checkpoint_verified", **report["checkpoint"])),
                    flush=True,
                )
                with (args.output / "checkpoint-verified.json").open("x") as handle:
                    json.dump(report["checkpoint"], handle, indent=2)
                if args.upload:
                    from swingset.backup.archive import upload_checkpoint
                    from swingset.backup.huggingface import HuggingFaceArchive

                    guards()
                    require(sha(STATE / "operator-hold") == hold_sha, "hold changed before upload")
                    archive = HuggingFaceArchive(
                        "skeswa/swingset-archive", token=os.environ["HF_TOKEN"]
                    )
                    commit = upload_checkpoint(checkpoint, archive) or archive.head()
                    require(commit is not None, "archive acknowledgment missing")
                    assert commit is not None
                    require(archive.head() == commit, "archive head changed")
                    require(
                        archive.manifest_hash(commit) == checkpoint.manifest_hash,
                        "remote checkpoint manifest differs",
                    )
                    report["private_archive_commit"] = commit
            require(conn.total_changes == 0, "live database writes occurred")
            report["live_database_changes"] = conn.total_changes
            require(sha(STATE / "operator-hold") == hold_sha, "operator hold bytes changed")
            guards()
        report["passed"] = True
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        with (args.output / "receipt.json").open("x") as handle:
            json.dump(report, handle, indent=2, default=str)
            handle.write("\n")


if __name__ == "__main__":
    main()
