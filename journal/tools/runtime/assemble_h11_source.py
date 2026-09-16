"""Prepare a selective H11 tree from the acknowledged V4 source pin; never deploy."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

OVERLAYS = (
    "src/swingset/state/db.py",
    "src/swingset/state/migrations/0010_cohort_discovery_cutoff.sql",
    "src/swingset/state/requirements.py",
    "src/swingset/state/requirement_report.py",
    "src/swingset/state/lag_report.py",
    "tests/test_requirements.py",
    "tests/test_requirement_performance.py",
    "tests/test_research_checkpoint_readers.py",
    "tests/test_h11_operational_preparation.py",
    "docs/reference/state.md",
    "docs/reference/operations.md",
    "journal/tools/runtime/benchmark_requirements.py",
    "journal/tools/runtime/benchmark_identity_references.py",
    "journal/investigations/2026/h11-acceptance-2026-09-13.md",
    "journal/investigations/2026/h11-retained-inventory-2026-09-13.md",
    "journal/investigations/undated/h11-deployment-preparation.md",
    "journal/evidence/runtime/h11/h11-schema10-cohort-benchmark-20260913.json",
    "journal/evidence/runtime/h11/h11-final-lag-report-20260913.json",
    "journal/tools/runtime/assemble_h11_source.py",
    "journal/tools/runtime/accept_h11.py",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def assemble(base: Path, reviewed: Path, output: Path) -> dict:
    base, reviewed, output = base.resolve(), reviewed.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(base) or output.is_relative_to(reviewed):
        raise ValueError("output must be a new directory outside both source trees")
    before = files(base)
    if not before or (base / ".git").exists() or (base / ".jj").exists():
        raise ValueError("base must be a prepared source pin, not a working checkout")
    old_db = (base / OVERLAYS[0]).read_text()
    new_db = (reviewed / OVERLAYS[0]).read_text()
    if old_db.replace("SCHEMA_VERSION = 9", "SCHEMA_VERSION = 10", 1) != new_db:
        raise ValueError("db.py must differ from V4 only by schema 9 to 10")
    for name in OVERLAYS:
        if not (reviewed / name).is_file():
            raise ValueError(f"missing reviewed H11 file: {name}")
    if any(path.is_symlink() for path in base.rglob("*")):
        raise ValueError("base source pin must contain regular files, not symlinks")
    shutil.copytree(
        base,
        output,
        copy_function=shutil.copyfile,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for directory in [output, *(path for path in output.rglob("*") if path.is_dir())]:
        directory.chmod(0o755)
    for name in OVERLAYS:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reviewed / name, target)
    after = files(output)
    changed = sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
    if not set(changed) <= set(OVERLAYS):
        raise ValueError("assembled tree changed a non-H11 file")
    receipt = {
        "format": "h11-selective-source-v1",
        "base": str(base),
        "reviewed": str(reviewed),
        "base_files": before,
        "files": after,
        "changed": changed,
        "allowed_overlays": list(OVERLAYS),
        "build_runtime_preserved": all(
            after[name] == sha
            for name, sha in before.items()
            if name.startswith("src/swingset/build/")
        ),
        "deployed": False,
    }
    (output / "h11-source.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = assemble(args.base, args.reviewed, args.output)
    print(
        json.dumps(
            {key: report[key] for key in ("changed", "build_runtime_preserved", "deployed")},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
