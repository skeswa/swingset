"""Read-only live schema-14 preflight; --execute permits only reviewed migration to 28.

The coordinator separately deploys the reviewed system under hold. This helper
never accepts runtime inputs, adopts spacing baselines, starts workers or makes
network requests. Its separately reviewed bytes and every prerequisite are pinned.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

SOURCE = Path("/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source")
SOURCE_RECEIPT = "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6"
OLD_SYSTEM = (
    "/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
)
SYSTEM = (
    "/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
)
STATE = Path("/var/lib/swingset")
BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
CANDIDATE = "cand_8f31cad7226643ae"
UTIL = "journal/tools/runtime/rehearse_extension_migration.py"
UNITS = tuple(
    f"swingset-{kind}.{suffix}"
    for kind in ("cycle", "backup", "summary")
    for suffix in ("service", "timer")
)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def document(path: Path) -> dict[str, Any]:
    with path.open("rb") as raw:
        compressed = raw.read(2) == b"\x1f\x8b"
    with gzip.open(path, "rb") if compressed else path.open("rb") as stream:
        data = stream.read(64 * 1024 * 1024 + 1)
    require(len(data) <= 64 * 1024 * 1024, "receipt exceeds bounded JSON limit")
    value = json.loads(data)
    require(isinstance(value, dict), "receipt must be an object")
    return cast(dict[str, Any], value)


def reference(base: Path, spec: dict[str, Any]) -> Path:
    name = Path(spec["path"])
    require(".." not in name.parts, "receipt reference traverses parent")
    path = (name if name.is_absolute() else base / name).resolve(strict=True)
    require(path.is_file() and sha(path) == spec["sha256"], "pinned file differs")
    return path


def closed(receipt: dict[str, Any], format: str) -> None:
    require(
        receipt.get("format") == format and receipt.get("passed") is True, f"{format} did not pass"
    )
    finished = datetime.fromisoformat(receipt.get("finished_at", ""))
    require(finished.tzinfo is not None, f"{format} is not closed with an explicit timezone")


def bound(receipt: dict[str, Any]) -> None:
    require(
        receipt.get("source") == str(SOURCE)
        and receipt.get("source_receipt_sha256") == SOURCE_RECEIPT,
        "upstream source differs",
    )


def validate_receipts(receipts: dict[str, dict[str, Any]], gate: dict[str, Any]) -> None:
    """Require affirmative upstream results and their shared source/checkpoint."""
    checkpoint = gate["checkpoint"]
    backup = receipts["backup"]
    closed(backup, "h16-post-publication-checkpoint-v1")
    require(
        backup.get("baseline") == BASELINE and backup.get("system") == OLD_SYSTEM,
        "backup predecessor differs",
    )
    require(
        backup.get("source") == "/nix/store/z689qy41inndill3d92ym8im852x3649-source",
        "backup runtime is not schema14 predecessor",
    )
    require(
        backup.get("live_database_changes") == 0 and bool(backup.get("private_archive_commit")),
        "backup was not preserved and acknowledged",
    )
    require(
        backup.get("checkpoint", {}).get("path") == checkpoint["path"]
        and backup["checkpoint"].get("manifest_sha256") == checkpoint["manifest_sha256"],
        "backup checkpoint differs",
    )
    for name, format in (
        ("migration", "event-extension-migration-rehearsal-v1"),
        ("restore", "extension-operational-restore-rehearsal-v1"),
    ):
        receipt = receipts[name]
        closed(receipt, format)
        bound(receipt)
        require(
            receipt.get("checkpoint") == checkpoint["path"]
            and receipt.get("checkpoint_sha256") == checkpoint["manifest_sha256"],
            "rehearsal checkpoint differs",
        )
        require(
            receipt.get("target_schema") == 28 and receipt.get("schema") == 28,
            "rehearsal target is not schema28",
        )
        require(
            receipt.get("changed_existing_tables") == [] and len(receipt.get("before", {})) == 71,
            "rehearsal did not preserve all 71 predecessor tables",
        )
        require(
            all(
                value == receipt.get("after", {}).get(key)
                for key, value in receipt["before"].items()
            ),
            "rehearsal protected table result differs",
        )
    migration = receipts["migration"]
    require(
        migration.get("old_schema") == 14
        and migration.get("integrity") == [["ok"]]
        and migration.get("foreign_key_failure") is False,
        "migration integrity/schema result failed",
    )
    require(
        migration.get("scope") == "migration_only_without_runtime_input_acceptance",
        "migration scope differs",
    )
    restore = receipts["restore"]
    require(
        restore.get("stage") == "finished_held_without_input_acceptance"
        and restore.get("schema14_activated_under_hold") is True,
        "restore protocol did not finish under hold",
    )
    require(
        restore.get("baseline") == BASELINE
        and restore.get("restore_heads") == [BASELINE, BASELINE]
        and restore.get("remote", {}).get("commit") == BASELINE,
        "restore public verification differs",
    )
    require(
        restore.get("acknowledged_archive_commit") == backup["private_archive_commit"],
        "restore archive acknowledgment differs",
    )
    require(
        restore.get("operator_hold_present") is True and restore.get("restore_pending") is False,
        "restore did not preserve hold and finish activation",
    )
    require(
        all(
            restore.get(key) is False
            for key in ("input_acceptance", "workers_started", "repairs_activated")
        )
        and restore.get("source_requests") == 0
        and restore.get("public_writes") == 0,
        "restore exceeded reviewed scope",
    )
    validation = receipts["validation"]
    closed(validation, "event-extension-validation-v1")
    bound(validation)
    require(
        validation.get("schema") == 28
        and validation.get("source_verified_before") is True
        and validation.get("source_verified_after") is True,
        "validation source/schema was not frozen",
    )
    checks = [validation.get(key, {}) for key in ("pytest", "ruff", "mypy")]
    require(
        all(
            check.get("exit_code") == 0
            and check.get("log_sha256") == gate["evidence"][name + "_log"]["sha256"]
            for check, name in zip(checks, ("pytest", "ruff", "mypy"), strict=True)
        ),
        "validation check or pinned log failed",
    )
    tests = validation["pytest"]
    require(
        tests.get("full_suite") is True
        and type(tests.get("passed")) is int
        and tests["passed"] > 0
        and tests.get("failed") == 0
        and bool(tests.get("command")),
        "full test suite did not pass",
    )
    require(
        type(validation["mypy"].get("source_files")) is int
        and validation["mypy"]["source_files"] > 0,
        "type-check source population missing",
    )
    binding = receipts["service_binding"]
    closed(binding, "event-extension-service-binding-v1")
    bound(binding)
    require(
        binding.get("system") == SYSTEM
        and all(
            binding.get(key) is True
            for key in ("external_overrides_match", "hold_conditions", "backup_disk_tmp")
        ),
        "reviewed service binding failed",
    )
    require(
        isinstance(binding.get("package"), str) and binding["package"].startswith("/nix/store/"),
        "service package pin missing",
    )


def frozen_util(source_receipt: Path) -> Any:
    require(sha(source_receipt) == SOURCE_RECEIPT, "reviewed source receipt differs")
    record = document(source_receipt)["files"][UTIL]
    require(
        sha(SOURCE / UTIL) == (record["sha256"] if isinstance(record, dict) else record),
        "frozen table/source verifier differs",
    )
    spec = importlib.util.spec_from_file_location("reviewed_extension_migration", SOURCE / UTIL)
    if spec is None or spec.loader is None:
        raise ValueError("cannot import frozen verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_source(SOURCE, source_receipt, SOURCE_RECEIPT)
    return module


def runtime_bound() -> None:
    for name, module in tuple(sys.modules.items()):
        if name == "swingset" or name.startswith("swingset."):
            file = getattr(module, "__file__", None)
            require(
                file is not None and Path(file).resolve().is_relative_to(SOURCE / "src"),
                f"runtime escaped reviewed source: {name}",
            )


def service_files(binding: dict[str, Any]) -> None:
    hashes = binding.get("unit_hashes", {})
    require(
        set(hashes) <= set(UNITS)
        and {unit for unit in UNITS if unit.endswith(".service")} <= set(hashes),
        "ordinary service hashes missing or unexpected",
    )
    for unit, expected in hashes.items():
        require(
            sha(Path(SYSTEM) / "etc/systemd/system" / unit) == expected,
            f"reviewed unit differs: {unit}",
        )
    external = binding.get("external_files", {})
    require(
        isinstance(external, dict) and bool(external), "reviewed external file inventory missing"
    )
    for name, expected in external.items():
        require(
            Path(name).is_absolute() and sha(Path(name)) == expected,
            "external reviewed input differs",
        )


def guards(state: Path, phase: str) -> dict[str, Any]:
    require(
        phase in {"before_deployment", "after_deployment"}, "explicit deployment phase required"
    )
    expected = OLD_SYSTEM if phase == "before_deployment" else SYSTEM
    require(
        (state / "operator-hold").is_file() and not (state / "RESTORE_PENDING").exists(),
        "operator hold missing or restore pending",
    )
    systems = {
        name: str(Path(name).resolve(strict=True))
        for name in ("/run/current-system", "/nix/var/nix/profiles/system")
    }
    require(
        all(value == expected for value in systems.values()),
        "active/persistent system differs from phase",
    )
    units = {
        unit: subprocess.check_output(
            ["systemctl", "show", unit, "--property=ActiveState", "--value"], text=True
        ).strip()
        for unit in UNITS
    }
    require(all(value == "inactive" for value in units.values()), "ordinary unit is not inactive")
    return dict(systems=systems, units=units, operator_hold_sha256=sha(state / "operator-hold"))


def protected(conn: sqlite3.Connection, util: Any, schema: int) -> dict[str, Any]:
    require(
        conn.execute("PRAGMA user_version").fetchone()[0] == schema
        and conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        == str(schema),
        "live schema differs",
    )
    require(
        [tuple(row) for row in conn.execute("PRAGMA integrity_check")] == [("ok",)]
        and conn.execute("PRAGMA foreign_key_check").fetchone() is None,
        "database integrity failed",
    )
    require(
        conn.execute("SELECT 1 FROM execution_admissions WHERE state<>'settled'").fetchone()
        is None,
        "unsettled execution requires coordinator reconciliation",
    )
    return cast(dict[str, Any], util.table_receipts(conn))


def baseline_files(state: Path, checkpoint: Path, manifest: dict[str, Any]) -> dict[str, str]:
    target = (state / "baseline").resolve(strict=True)
    require(
        (state / "baseline").is_symlink() and target == state / "candidates" / CANDIDATE,
        "baseline link differs",
    )
    published = document(target / "PUBLISHED")
    require(
        published.get("commit") == BASELINE
        and published.get("candidate_id") == CANDIDATE
        and bool(published.get("verified_at")),
        "acknowledged baseline differs",
    )
    expected = manifest["files"]
    prefix = f"candidates/{CANDIDATE}/"
    selected = {
        name: record["sha256"] for name, record in expected.items() if name != "state.sqlite"
    }
    require(
        "operator-hold" in selected and prefix + "PUBLISHED" in selected,
        "checkpoint baseline or hold omitted",
    )
    for name, digest in selected.items():
        require(
            sha(state / name) == digest and sha(checkpoint / name) == digest,
            "live retained file differs from checkpoint",
        )
    return selected


def migrate_locked(
    state: Path,
    util: Any,
    db_module: Any,
    before: dict[str, Any],
    expected_after: dict[str, Any],
    *,
    verify_inputs: Callable[[], None],
) -> dict[str, Any]:
    """Caller holds the writer and control locks after all upstream gates pass."""
    conn = sqlite3.connect(
        (state / "state.sqlite").as_uri() + "?mode=rw", uri=True, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    with db_module.Database(state, conn, None) as database:
        require(protected(conn, util, 14) == before, "locked prestate changed before migration")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        verify_inputs()
        # The public opener would reacquire control.lock and deadlock.
        db_module._migrate(database)
        after = protected(conn, util, 28)
        require(
            util.compare(before, after) == [],
            "migration changed predecessor table content or columns",
        )
        require(
            after == expected_after, "live migration differs from rehearsed complete table result"
        )
    with closing(sqlite3.connect((state / "state.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        require(protected(conn, util, 28) == after, "reopening changed migrated state")
    return after


def reviewed_inputs(
    util: Any,
    source_receipt: Path,
    gate_path: Path,
    gate_sha256: str,
    gate: dict[str, Any],
    paths: dict[str, Path],
    binding: dict[str, Any],
    before_guards: dict[str, Any],
) -> None:
    """Repeat mutable operating checks at the final boundary, after long reads."""
    util.verify_source(SOURCE, source_receipt, SOURCE_RECEIPT)
    require(
        sha(Path(__file__)) == gate["helper_sha256"] and sha(gate_path) == gate_sha256,
        "reviewed gate/helper changed during operation",
    )
    require(
        all(sha(path) == gate["evidence"][name]["sha256"] for name, path in paths.items()),
        "reviewed evidence changed during operation",
    )
    reference(gate_path.parent, gate["restore_helper"])
    service_files(binding)
    runtime_bound()
    require(
        guards(STATE, gate["phase"]) == before_guards,
        "operating guards changed before migration boundary",
    )


def run(gate_path: Path, *, gate_sha256: str, execute: bool = False) -> dict[str, Any]:
    sys.dont_write_bytecode = True
    require(sha(gate_path) == gate_sha256, "reviewed gate differs")
    gate = document(gate_path)
    require(
        gate.get("format") == "event-extension-live-migration-gate-v1"
        and gate.get("state") == str(STATE)
        and gate.get("source") == str(SOURCE),
        "gate scope differs",
    )
    require(
        gate.get("helper_sha256") == sha(Path(__file__)),
        "separately reviewed migration helper differs",
    )
    require(
        not execute or gate.get("phase") == "after_deployment",
        "execution requires reviewed system already deployed under hold",
    )
    paths = {name: reference(gate_path.parent, spec) for name, spec in gate["evidence"].items()}
    require(
        set(paths)
        == {
            "backup",
            "migration",
            "restore",
            "validation",
            "service_binding",
            "pytest_log",
            "ruff_log",
            "mypy_log",
        },
        "prerequisite closure differs",
    )
    receipts = {
        name: document(paths[name])
        for name in ("backup", "migration", "restore", "validation", "service_binding")
    }
    validate_receipts(receipts, gate)
    source_receipt = reference(gate_path.parent, gate["source_receipt"])
    util = frozen_util(source_receipt)
    require(
        receipts["migration"].get("helper_sha256") == sha(SOURCE / UTIL),
        "migration rehearsal helper differs from frozen verifier",
    )
    restore_helper = reference(gate_path.parent, gate["restore_helper"])
    require(
        receipts["restore"].get("helper_sha256") == sha(restore_helper),
        "restore rehearsal helper differs",
    )
    runtime_bound()
    sys.path.insert(0, str(SOURCE / "src"))
    from swingset.backup.checkpoint import verify_checkpoint
    from swingset.publish.service import pending_candidates
    from swingset.state import db as db_module

    runtime_bound()
    require(db_module.SCHEMA_VERSION == 28, "runtime target differs from reviewed schema28")
    checkpoint = Path(gate["checkpoint"]["path"]).resolve(strict=True)
    require(
        checkpoint != STATE and "checkpoints" in checkpoint.parts,
        "checkpoint path is not retained checkpoint state",
    )
    require(
        sha(checkpoint / "checkpoint.json") == gate["checkpoint"]["manifest_sha256"],
        "checkpoint manifest differs",
    )
    manifest = verify_checkpoint(checkpoint, maximum_schema_version=28)
    require(
        manifest["schema_version"] == 14
        and manifest["pending_candidate"] is None
        and manifest["baseline_candidate"] == CANDIDATE,
        "checkpoint is not acknowledged schema14 baseline",
    )
    service_files(receipts["service_binding"])
    before_guards = guards(STATE, gate["phase"])
    report: dict[str, Any] = dict(
        format="event-extension-live-migration-receipt-v1",
        started_at=datetime.now(UTC).isoformat(),
        passed=False,
        executed=False,
        source=str(SOURCE),
        source_receipt_sha256=SOURCE_RECEIPT,
        helper_sha256=sha(Path(__file__)),
        gate_sha256=gate_sha256,
        phase=gate["phase"],
        old_schema=14,
        target_schema=28,
        checkpoint_sha256=gate["checkpoint"]["manifest_sha256"],
        source_requests=0,
        public_writes=0,
        input_acceptance=False,
        workers_started=False,
        spacing_baselines_established=False,
        repairs_activated=False,
    )
    # These existing files are opened read-only, including for default preflight.
    # Lock order matches production: writer, controls, then SQLite transaction.
    with ExitStack() as stack:
        for name in ("state.lock", "control.lock"):
            handle = stack.enter_context((STATE / name).open("rb"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(
            guards(STATE, gate["phase"]) == before_guards,
            "operating guards changed before locked preflight",
        )
        require(
            not pending_candidates(STATE), "pending publication requires coordinator reconciliation"
        )
        baseline = baseline_files(STATE, checkpoint, manifest)
        with closing(
            sqlite3.connect(
                (checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True
            )
        ) as conn:
            expected = protected(conn, util, 14)
        require(
            len(expected) == 71
            and all(receipts[name]["before"] == expected for name in ("migration", "restore")),
            "checkpoint prestate differs from rehearsals",
        )
        with closing(
            sqlite3.connect((STATE / "state.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.execute("BEGIN")
            before = protected(conn, util, 14)
        require(
            before == expected,
            "live prestate differs from checkpoint; no metadata exception is allowed",
        )
        report.update(before=before, guards_before=before_guards, preflight_passed=True)

        def check() -> None:
            reviewed_inputs(
                util,
                source_receipt,
                gate_path,
                gate_sha256,
                gate,
                paths,
                receipts["service_binding"],
                before_guards,
            )

        if execute:
            report["executed"] = True
            report["after"] = migrate_locked(
                STATE, util, db_module, before, receipts["migration"]["after"], verify_inputs=check
            )
        require(
            baseline_files(STATE, checkpoint, manifest) == baseline,
            "migration changed baseline or hold",
        )
        check()
        require(
            sha(checkpoint / "checkpoint.json") == gate["checkpoint"]["manifest_sha256"]
            and sha(checkpoint / "state.sqlite") == manifest["files"]["state.sqlite"]["sha256"],
            "checkpoint changed during operation",
        )
        runtime_bound()
        report.update(
            passed=True, schema=28 if execute else 14, finished_at=datetime.now(UTC).isoformat()
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--gate-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        receipt = run(
            args.gate.resolve(strict=True), gate_sha256=args.gate_sha256, execute=args.execute
        )
    except BaseException as error:
        # A migration may have committed some versions before failure. Never
        # claim rollback or infer that production remains on schema14.
        print(
            json.dumps(
                dict(
                    format="event-extension-live-migration-failure-v1",
                    passed=False,
                    execution_requested=args.execute,
                    resulting_schema="unknown; coordinator inspection required",
                    error_type=type(error).__name__,
                    error=str(error),
                    finished_at=datetime.now(UTC).isoformat(),
                ),
                indent=2,
            ),
            flush=True,
        )
        raise
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
