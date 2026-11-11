"""Supervise the reviewed old producer with one separately audited operator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from . import verification_job
from .import_job import interruptions, require
from .unit_diagnostic import (
    ACCEPTED_CHECKPOINT,
    BUSINESS_CLOCK,
    IMAGE,
    INSTALLED_PROOF,
    RUNTIME_ARTIFACT,
    SOURCE,
    SOURCE_CHECKPOINT,
    UNIT,
    WRITE_SECONDS,
)


def operator_directory(path: Path) -> None:
    """The guardian-permitted mount exposes exactly one immutable public file."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o555
        )
        require(os.listdir(descriptor) == ["unit_diagnostic.py"])
    finally:
        os.close(descriptor)


class UnitDiagnosticJob(verification_job.InputProfileJob):
    """One new copy; immutable old business code and a separate read-only probe."""

    FORMAT = "swingset-attended-unit-diagnostic-v1"
    PREFIX = "unit-diagnostic"
    OPERATION = "one inventory unit rollback diagnostic"
    LIMITS = "Separate diagnostic receipt required; never a graph or re-key proof; no base cleanup."

    def review(self, verdict: dict[str, Any]) -> None:
        super().review(verdict)
        argv = self.policy[self.ARGV]
        environment = dict(
            argv[i + 1].split("=", 1) for i, item in enumerate(argv) if item == "--env"
        )
        require(
            environment.get("SWINGSET_COPY_ROOT") == "/var/lib/swingset-runtime/unit-copy-jobs"
            and environment.get("TMPDIR") == "/var/lib/swingset-spill"
            and environment.get("SWINGSET_RUNTIME_IMAGE") == RUNTIME_ARTIFACT
        )
        producer = {
            "image": IMAGE,
            "runtime_artifact": RUNTIME_ARTIFACT,
            "source_revision": SOURCE,
            "installed_proof_sha256": INSTALLED_PROOF,
            "installed_file_count": 560,
            "linker_version": "9",
        }
        require(
            self.policy["runtime_image"] == IMAGE
            and self.policy["producer"] == verdict["producer"] == producer
            and self.policy["unit"] == verdict["unit"] == UNIT
            and self.policy["business_clock"] == BUSINESS_CLOCK
            and self.policy["source_checkpoint"] == SOURCE_CHECKPOINT
            and self.policy["expected_checkpoint"] == ACCEPTED_CHECKPOINT
            and self.policy["freeze_seconds"] == 2700
            and self.policy["write_seconds"] == WRITE_SECONDS
            and self.policy["report_seconds"] == 90
            and self.policy["operator_sha256"] == verdict["operator_sha256"]
        )
        # The checker root is unused by this rollback-only diagnostic. Replace
        # its writable volume with a read-only directory; do not add a new root
        # to the deployed guardian's mount allowlist or overlay business code.
        self.extra_operator_mount()
        self.result.update(
            producer=producer,
            operator_sha256=self.policy["operator_sha256"],
            unit=UNIT,
            write_budget_seconds=WRITE_SECONDS,
            graph_proof=False,
        )

    def extra_operator_mount(self) -> dict[str, Any]:
        directory = self.root / "operator"
        operator_directory(directory)
        path = directory / "unit_diagnostic.py"
        body = verification_job.root_bytes(path)
        require(hashlib.sha256(body).hexdigest() == self.policy["operator_sha256"])
        return {
            "type": "bind",
            "source": str(directory),
            "target": "/run/swingset-checker",
            "readonly": True,
        }

    @staticmethod
    def utility_command() -> list[str]:
        return [
            "/run/swingset-checker/unit_diagnostic.py",
            "--checkpoint",
            "/var/lib/swingset",
            "--capture-state",
            "/var/lib/swingset-runtime/capture",
            "--receipt",
            "/var/lib/swingset-runtime/unit-diagnostic.json",
            "--config",
            "/app/config",
            "--overrides",
            "/app/overrides",
            "--at",
            BUSINESS_CLOCK,
            "--source-checkpoint",
            SOURCE_CHECKPOINT,
            "--expected-checkpoint",
            ACCEPTED_CHECKPOINT,
        ]


def arguments(parser: argparse.ArgumentParser) -> None:
    from .import_job import arguments as common

    common(parser)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--review-sha256", required=True)


def execute(args: argparse.Namespace) -> int:
    try:
        with interruptions():
            result = UnitDiagnosticJob(
                args.plan,
                args.plan_sha256,
                args.container,
                args.platform,
                review=args.review,
                review_sha256=args.review_sha256,
            ).run()
    except BaseException as error:
        result = {
            "complete": False,
            "mutation_outcome": "unknown",
            "no_automatic_retry": True,
            "error_type": type(error).__name__,
        }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["complete"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    return execute(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
