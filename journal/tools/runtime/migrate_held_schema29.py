"""Preflight and migrate only the held schema-28 production database to schema 29.

This dedicated gate is bound to candidate 006 and checkpoint 004.
Preflight does not change application database content and must precede the system
switch. Its live read-only SQLite connection may create WAL/SHM bookkeeping files,
which are transient runtime files rather than application state. Execution is
permitted only after the candidate system is active. Neither phase accepts
production inputs, resumes collection, activates repairs, makes network requests,
or publishes.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

STATE = Path("/var/lib/swingset")
SOURCE = Path("/nix/store/rgyryll4d55rgzscdqhjcwmkr325a76f-source")
SOURCE_RECEIPT_SHA256 = "a2704c7c99ce5aef4fbd0f0df8b23343be5df2a688945e236b8e6856b320ad5f"
PREDECESSOR_SOURCE = Path("/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source")
PREDECESSOR_SYSTEM = Path(
    "/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
)
TARGET_SYSTEM = Path(
    "/nix/store/5d9nlyflv9d4gb89a5wayhiarj01znnh-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
)
TARGET_PACKAGE = Path("/nix/store/lcay4ij6fa093g7wfk4g2imdsqza7d32-swingset")
TARGET_PACKAGE_SCRIPT_SHA256 = "560fde830aab9a947b39ff4740d48fdce116a3eeb5fd2486166e028a065cd0b7"
HOLD_SHA256 = "965468bb03ba65b6d1d4950ab8e85c09c8f2f2a323461b7a3f2b6177bba5c638"
PUBLIC_BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
CANDIDATE = "cand_8f31cad7226643ae"
TARGET_TABLE = "history_dispatch_fence"
CHECKPOINT = Path("/var/lib/swingset/checkpoints/extension28-held-20260917-004")
CHECKPOINT_SHA256 = "6ac7bb2d291136ef502abcb33f8762bf4449fd2fdc0160fb995f2aba0921f9d9"
ARCHIVE_COMMIT = "68d738dcd78cb1654d053b479ea6dbd911f4b8ed"
CHECKPOINT_CAPTURE_SHA256 = "804e4b8fa2af496ea7640339760a5dec3ac2565d5731547417a835083cf9551d"
CHECKPOINT_VERIFIED_SHA256 = "899500a757936b6fe90c9408c9b47058b3006ec6163e12855991566533d96a41"
CHECKPOINT_HELPER_SHA256 = "10bdfb40799a348ab948cca9934639b873676cf737e11a0532ba603960d099ba"
PACKET_SHA256 = "0d455559b7e0cd767cb1739f10271eb12e8178bebb5a4ab283bd8e74733b6ff3"
PACKET_REVIEW_SHA256 = "258877dba4d98d41ededd5d0d8248b558f90274a2f4704f0add34702e2b584a3"
ACTUAL_REVIEW_SHA256 = "efe8547205a7d938dbc65a9f720cf47635ddd78cbe7f4ab7833099cf307b803d"
MIGRATION_RECEIPT_SHA256 = "325af505bb8abbdade88a7303feee9dbe56250191daec10b12a005aca53d9e75"
RESTORE_RECEIPT_SHA256 = "5f1584086b2e6d8c7eea89242c133316e3c53e8bcd3cf7e176d5d5f61eff23b8"
INPUT_PREPARE_SHA256 = "751962638a5264641d07d3994e22ed81c38da9a1e9b6c91fb8ce1eacd6dc29c1"
INPUT_DRAIN_SHA256 = "b47e7faff25181a01c53d869a98ccd25e60ce622e880290ae83955c29af4c02f"
INPUT_ACCEPT_SHA256 = "9ba0ca7bed02b165ca64fc5595ea3466ca28358740e52498a2fe11770b27beff"
EVIDENCE_SHA256 = {
    "validation": "57c02e301dcc6d1f2c8824b0380d6c28bea85dc49e840a9128cd1f955298d9d1",
    "build": "d3716d6c173c8a67d6612006bbcdfe2a415c006657a52816f047934458411d79",
    "service_binding": "fecbbffcab8233fe9c33ec667a5c6e8387d0cc11a48d483f8e2fda3f761f9e6e",
    "packet_review": PACKET_REVIEW_SHA256,
    "actual_review": ACTUAL_REVIEW_SHA256,
}
UNITS = tuple(
    f"swingset-{kind}.{suffix}"
    for kind in ("cycle", "backup", "summary")
    for suffix in ("service", "timer")
)
LOCKS = ("state.lock", "control.lock")
EXPECTED_OVERRIDES = {
    "event_aliases.csv",
    "identity_overrides.csv",
    "nicknames.csv",
    "series_aliases.csv",
    "source_urls.csv",
    "suppressions.csv",
}
RUNTIME_TRANSIENTS = {"state.sqlite-wal", "state.sqlite-shm", *LOCKS}
EVIDENCE_KEYS = {
    "validation",
    "build",
    "service_binding",
    "packet_review",
    "actual_review",
}
REHEARSAL_RECEIPT_SHA256 = {
    "migration": MIGRATION_RECEIPT_SHA256,
    "restore": RESTORE_RECEIPT_SHA256,
    "inputs_prepare": INPUT_PREPARE_SHA256,
    "inputs_drain": INPUT_DRAIN_SHA256,
    "inputs_accept": INPUT_ACCEPT_SHA256,
}
REHEARSAL_RECEIPT_PATH = {
    "migration": "../migration-004/migration-receipt.json",
    "restore": "../restore-004/restore-receipt.json",
    "inputs_prepare": "../inputs-004/prepare.json",
    "inputs_drain": "../inputs-004/drain-001.json",
    "inputs_accept": "../inputs-004/accept.json",
}
STALE_CROSSCHECK_SNAPSHOTS = [
    "snap_20260909T153428Z_ae7f2b9d688b",
    "snap_20260911T153519Z_ae7f2b9d688b",
    "snap_20260912T131109Z_ae7f2b9d688b",
]
EXPECTED_WDR_REVIEW = "snap_20260909T150549Z_abf51d8711ef"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    require(len(data) <= 64 * 1024 * 1024, f"JSON evidence exceeds size limit: {path}")
    value = json.loads(data)
    require(isinstance(value, dict), f"JSON evidence must be an object: {path}")
    return cast(dict[str, Any], value)


def reference(base: Path, spec: dict[str, Any], label: str) -> Path:
    name = Path(str(spec.get("path", "")))
    require(".." not in name.parts, f"{label} reference traverses parent")
    path = name if name.is_absolute() else base / name
    require(not path.is_symlink(), f"{label} reference cannot be a symlink")
    path = path.resolve(strict=True)
    require(path.is_file(), f"{label} reference is not a regular file")
    expected = str(spec.get("sha256", ""))
    require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None, f"{label} SHA is malformed")
    require(sha(path) == expected, f"{label} SHA differs")
    return path


def finished(receipt: dict[str, Any], expected_format: str, label: str) -> None:
    require(
        receipt.get("format") == expected_format and receipt.get("passed") is True,
        f"{label} did not pass",
    )
    try:
        timestamp = datetime.fromisoformat(
            str(receipt.get("finished_at", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError(f"{label} is not closed") from error
    require(timestamp.tzinfo is not None, f"{label} lacks a timezone-aware completion")


def table_receipts(conn: sqlite3.Connection) -> dict[str, Any]:
    """Hash table rows and columns in stable order without exposing row contents."""
    result: dict[str, Any] = {}
    names = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for (name,) in names:
        quoted = '"' + str(name).replace('"', '""') + '"'
        columns = list(conn.execute(f"PRAGMA table_info({quoted})"))
        query = f"SELECT * FROM {quoted}"
        if name == "meta":
            query += " WHERE key!='schema_version'"
        primary = sorted((int(row[5]), str(row[1])) for row in columns if int(row[5]))
        order = ",".join('"' + column.replace('"', '""') + '"' for _, column in primary)
        query += " ORDER BY " + (order or "rowid")
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute(query):
            encoded = json.dumps(
                list(row),
                ensure_ascii=True,
                separators=(",", ":"),
                default=lambda value: (
                    {"bytes": value.hex()} if isinstance(value, bytes) else str(value)
                ),
            ).encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            count += 1
        result[str(name)] = {
            "rows": count,
            "sha256": digest.hexdigest(),
            "columns": [str(row[1]) for row in columns],
        }
    return result


def inventory_source(source: Path, receipt_path: Path) -> dict[str, Any]:
    require(source.resolve(strict=True) == SOURCE, "candidate source path differs")
    require(sha(receipt_path) == SOURCE_RECEIPT_SHA256, "candidate source receipt differs")
    receipt = read_json(receipt_path)
    files = receipt.get("files")
    require(isinstance(files, dict) and len(files) == 2939, "candidate source inventory differs")
    files = cast(dict[str, Any], files)
    require(
        not any(path.is_symlink() for path in source.rglob("*")),
        "candidate source contains symlinks",
    )
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.resolve() != receipt_path.resolve()
    }
    require(actual == set(files), "candidate source inventory paths differ")
    for name, record in files.items():
        relative = Path(name)
        require(
            not relative.is_absolute() and ".." not in relative.parts,
            "candidate source inventory path escapes",
        )
        expected = record["sha256"] if isinstance(record, dict) else record
        require(sha(source / relative) == expected, f"candidate source file differs: {name}")
    return receipt


def validate_evidence(
    gate: dict[str, Any], gate_path: Path
) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    evidence_value = gate.get("evidence")
    require(
        isinstance(evidence_value, dict) and set(evidence_value) == EVIDENCE_KEYS,
        "evidence closure differs",
    )
    evidence_value = cast(dict[str, Any], evidence_value)
    for name, expected in EVIDENCE_SHA256.items():
        spec = evidence_value.get(name)
        require(
            isinstance(spec, dict) and spec.get("sha256") == expected,
            f"{name} evidence pin differs",
        )
    paths = {
        name: reference(gate_path.parent, cast(dict[str, Any], spec), name)
        for name, spec in evidence_value.items()
    }
    docs = {name: read_json(path) for name, path in paths.items()}

    validation = docs["validation"]
    finished(validation, "event-extension-validation-v1", "candidate validation")
    require(
        validation.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and validation.get("schema") == 29
        and validation.get("source_verified_before") is True
        and validation.get("source_verified_after") is True
        and validation.get("deployed") is False
        and validation.get("published") is False,
        "candidate validation scope differs",
    )
    checks = validation.get("checks")
    require(
        isinstance(checks, list)
        and {item.get("name") for item in checks} == {"pytest", "ruff", "mypy"}
        and all(item.get("exit_code") == 0 for item in checks),
        "candidate validation checks did not pass",
    )
    checks = cast(list[dict[str, Any]], checks)
    validation_logs = {"pytest": "pytest.log", "ruff": "ruff.log", "mypy": "mypy.log"}
    require(
        all(
            sha(paths["validation"].parent / log_name)
            == next(item["log_sha256"] for item in checks if item["name"] == name)
            for name, log_name in validation_logs.items()
        ),
        "candidate validation log differs",
    )

    build = docs["build"]
    require(
        build.get("source") == str(SOURCE)
        and build.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and build.get("source_files") == 2939
        and build.get("source_verified") is True
        and build.get("archived_dependencies_included") is True
        and build.get("build_exit_code") == 0
        and build.get("built_system") == str(TARGET_SYSTEM)
        and build.get("built_package") == str(TARGET_PACKAGE)
        and build.get("deployed") is False
        and build.get("published") is False,
        "candidate system build receipt differs",
    )
    require(
        sha(paths["build"].parent / "build.log") == build.get("build_log_sha256"),
        "candidate system build log differs",
    )

    binding = docs["service_binding"]
    validate_service_binding_contract(binding)
    validate_service_binding_files(binding)

    packet_ref = gate.get("packet")
    require(
        isinstance(packet_ref, dict) and packet_ref.get("sha256") == PACKET_SHA256,
        "gate binds another successor packet",
    )
    validate_packet_review(docs["packet_review"], paths["packet_review"])
    rehearsal_paths, rehearsal_docs = validate_actual_review(
        docs["actual_review"], paths["actual_review"]
    )
    paths.update(rehearsal_paths)
    docs.update(rehearsal_docs)
    return paths, docs


def validate_service_binding_contract(binding: dict[str, Any]) -> None:
    require(
        binding.get("format") == "event-extension-service-binding-review-v1"
        and binding.get("passed") is True
        and isinstance(binding.get("checked_at"), str),
        "candidate service binding did not pass",
    )
    candidate = binding.get("candidate", {})
    require(
        candidate.get("source") == str(SOURCE)
        and candidate.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and candidate.get("system") == str(TARGET_SYSTEM)
        and candidate.get("package") == str(TARGET_PACKAGE)
        and candidate.get("source_files") == 2939,
        "candidate service binding pins differ",
    )
    production = binding.get("production_read_only_state", {})
    require(
        production.get("active_system") == str(PREDECESSOR_SYSTEM)
        and production.get("persistent_system") == str(PREDECESSOR_SYSTEM)
        and production.get("schema") == 28
        and production.get("application_tables") == 116
        and production.get("candidate006_deployed") is False
        and production.get("input_acceptance") is False
        and production.get("published") is False
        and production.get("operator_hold") == {"present": True, "sha256": HOLD_SHA256}
        and production.get("units") == {unit: "inactive" for unit in UNITS}
        and binding.get("failures") == [],
        "service-binding review exceeds held scope",
    )
    require(
        binding.get("normalized_unit_comparison", {}).get("all_six_identical") is True,
        "candidate ordinary service binding comparison failed",
    )
    generated_units = candidate.get("generated_units", {})
    require(set(generated_units) == set(UNITS), "candidate generated unit inventory differs")
    external = binding.get("external_overrides", {})
    require(
        isinstance(external, dict)
        and external.get("all_six_match_candidate_and_production") is True
        and isinstance(external.get("sha256"), dict)
        and set(external["sha256"]) == EXPECTED_OVERRIDES,
        "candidate external override review does not cover all six approved files",
    )


def validate_service_binding_files(binding: dict[str, Any]) -> None:
    candidate = cast(dict[str, Any], binding.get("candidate", {}))
    require(
        sha(TARGET_PACKAGE / "bin/swingset")
        == candidate.get("package_script_sha256")
        == TARGET_PACKAGE_SCRIPT_SHA256,
        "candidate package wrapper differs",
    )
    config_hashes_value = candidate.get("frozen_config_sha256")
    require(
        isinstance(config_hashes_value, dict)
        and set(config_hashes_value)
        == {"config/hosts.toml", "config/sources.toml", "pyproject.toml", "uv.lock"},
        "candidate frozen configuration inventory differs",
    )
    config_hashes = cast(dict[str, Any], config_hashes_value)
    for name, expected in config_hashes.items():
        require(sha(SOURCE / name) == expected, f"candidate frozen configuration differs: {name}")
    generated_units = cast(dict[str, Any], candidate.get("generated_units", {}))
    for unit, expected in generated_units.items():
        require(
            sha(TARGET_SYSTEM / "etc/systemd/system" / unit) == expected,
            f"candidate system unit differs: {unit}",
        )
    external = cast(dict[str, Any], binding.get("external_overrides", {}))
    override_directory = Path(str(external.get("directory", "")))
    override_hashes = cast(dict[str, Any], external.get("sha256", {}))
    require(set(override_hashes) == EXPECTED_OVERRIDES, "candidate override file inventory differs")
    for name, expected in override_hashes.items():
        require(sha(override_directory / name) == expected, f"candidate override differs: {name}")


def validate_packet_review(packet_review: dict[str, Any], receipt_path: Path) -> None:
    require(sha(receipt_path) == PACKET_REVIEW_SHA256, "packet review receipt differs")
    finished(packet_review, "schema29-packet005-independent-review-v1", "packet review")
    packet = packet_review.get("packet")
    candidate = packet_review.get("candidate006")
    checkpoint = packet_review.get("checkpoint004")
    rebuild = packet_review.get("deterministic_rebuild")
    replacements = packet_review.get("source_bound_replacements")
    archived = packet_review.get("archived_dependencies")
    offline = packet_review.get("offline_checks")
    require(
        isinstance(packet, dict)
        and packet.get("path") == "../packet-005"
        and packet.get("sha256") == PACKET_SHA256
        and packet.get("members_including_manifest") == 12
        and packet.get("closure_verified_by_sealed_runner") is True,
        "packet review binds another packet",
    )
    require(
        isinstance(candidate, dict)
        and candidate.get("runtime_source") == str(SOURCE)
        and candidate.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and candidate.get("source_files") == 2939
        and candidate.get("schema") == 29
        and candidate.get("complete_source_inventory_verified") is True
        and candidate.get("validation_passed") is True
        and candidate.get("build_passed") is True
        and candidate.get("service_binding_review_passed") is True,
        "packet review candidate006 binding differs",
    )
    require(
        isinstance(checkpoint, dict)
        and checkpoint.get("path") == str(CHECKPOINT)
        and checkpoint.get("manifest_sha256") == CHECKPOINT_SHA256
        and checkpoint.get("private_archive_commit") == ARCHIVE_COMMIT
        and checkpoint.get("files") == 68866
        and checkpoint.get("bytes") == 8770615161
        and checkpoint.get("schema") == 28
        and checkpoint.get("application_tables") == 116
        and checkpoint.get("capture_passed") is True
        and checkpoint.get("private_acknowledgment_matches") is True
        and checkpoint.get("live_database_changes") == 0
        and checkpoint.get("ordinary_units_inactive") == 6
        and checkpoint.get("complete_post_cleanup_verification_passed") is True
        and checkpoint.get("missing_files") == 0
        and checkpoint.get("extra_files") == 0
        and checkpoint.get("database_bytes_changed") is False
        and checkpoint.get("top_level_sidecars") == 7
        and checkpoint.get("sidecar_manifest_matches_packet_and_capture") is True,
        "packet review checkpoint004 binding differs",
    )
    require(
        isinstance(rebuild, dict)
        and all(
            rebuild.get(key) is True
            for key in (
                "base_and_generated_helpers_rebuilt",
                "runner_rebuilt",
                "copied_checkpoint_evidence_rebuilt",
                "sidecar_manifest_rebuilt",
                "packet_manifest_rebuilt",
                "all_12_member_hashes_exact",
            )
        ),
        "packet review deterministic rebuild differs",
    )
    require(
        isinstance(replacements, dict)
        and replacements.get("packet004_to_packet005_changed_members")
        == ["inputs.py", "packet.json", "runner.py"]
        and replacements.get("inputs_receipt_replaced") is True
        and replacements.get("runner_source_and_receipt_replaced") is True
        and replacements.get("packet_source_receipt_and_changed_member_hashes_replaced") is True
        and replacements.get("migration_and_restore_receive_the_sealed_source_from_runner") is True
        and replacements.get("all_other_members_byte_identical_to_packet004") is True,
        "packet review source-bound replacement checks differ",
    )
    require(
        isinstance(archived, dict)
        and archived.get("candidate_files_over_1_mib") == 13
        and archived.get("scrub_restored_paths") == 13
        and archived.get("all_paths_sizes_and_hashes_match") is True
        and archived.get("omissions_found") == 0
        and archived.get("candidate_build_receipt_records_archived_dependencies_included") is True,
        "packet review archived dependency closure differs",
    )
    require(
        isinstance(offline, dict)
        and offline.get("network_requests") == 0
        and offline.get("production_writes") == 0
        and offline.get("migrations") == 0
        and offline.get("restores") == 0
        and offline.get("input_replays") == 0
        and packet_review.get("blockers") == [],
        "packet review exceeded offline review scope",
    )


def validate_actual_review(
    review: dict[str, Any], receipt_path: Path
) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    require(sha(receipt_path) == ACTUAL_REVIEW_SHA256, "actual rehearsal review differs")
    finished(
        review,
        "schema29-candidate006-actual-rehearsal-independent-review-v1",
        "actual rehearsal review",
    )
    require(review.get("blockers") == [], "actual rehearsal review reports blockers")
    operations = review.get("audit_operations")
    require(
        isinstance(operations, dict)
        and operations.get("input_replays") == 0
        and operations.get("migrations") == 0
        and operations.get("network_requests") == 0
        and operations.get("production_writes") == 0
        and operations.get("restores") == 0,
        "actual review exceeded read-only audit scope",
    )
    bindings = review.get("bindings")
    require(
        isinstance(bindings, dict)
        and bindings.get("all_receipts_match_packet005") is True
        and bindings.get("checkpoint") == str(CHECKPOINT)
        and bindings.get("checkpoint_sha256") == CHECKPOINT_SHA256
        and bindings.get("packet_sha256") == PACKET_SHA256
        and bindings.get("source") == str(SOURCE)
        and bindings.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256,
        "actual review binds another candidate, packet, or checkpoint",
    )
    gate = review.get("gate")
    require(
        gate
        == {
            "candidate006_rehearsal_gate_closed": True,
            "live_deployment_gate_closed": False,
            "production_input_acceptance": False,
            "publication": False,
            "repairs_activated": False,
        },
        "actual review authorizes work beyond the rehearsal",
    )
    production = review.get("production_read_only_state")
    require(
        isinstance(production, dict)
        and production.get("active_system") == str(PREDECESSOR_SYSTEM)
        and production.get("schema") == 28
        and production.get("candidate006_deployed") is False
        and production.get("candidate006_publication") is False
        and production.get("operator_hold_sha256") == HOLD_SHA256
        and production.get("ordinary_units_inactive") == 6
        and production.get("published_candidate") == CANDIDATE
        and production.get("published_commit") == PUBLIC_BASELINE
        and production.get("accepted_inputs_equal_checkpoint004") is True
        and production.get("input_bundle_equal_checkpoint004") is True,
        "actual review production state was not the held schema28 predecessor",
    )
    migration = review.get("migration")
    require(
        isinstance(migration, dict)
        and migration.get("passed") is True
        and migration.get("schema") == 29
        and migration.get("predecessor_tables") == 116
        and migration.get("result_tables") == 117
        and migration.get("changed_predecessor_tables") == []
        and migration.get("new_tables") == [TARGET_TABLE]
        and migration.get("integrity") == "ok"
        and migration.get("foreign_key_failure") is False,
        "actual review did not prove the exact migration contract",
    )
    restore = review.get("restore")
    require(
        isinstance(restore, dict)
        and restore.get("passed") is True
        and restore.get("schema") == 29
        and restore.get("predecessor_tables") == 116
        and restore.get("changed_predecessor_tables_after_permitted_restore") == []
        and restore.get("new_tables") == [TARGET_TABLE]
        and restore.get("operator_hold_present") is True
        and restore.get("input_acceptance") is False
        and restore.get("workers_started") is False
        and restore.get("repairs_activated") is False
        and restore.get("source_requests") == 0
        and restore.get("public_writes") == 0,
        "actual review did not prove held restore behavior",
    )
    inputs = review.get("inputs")
    require(
        isinstance(inputs, dict)
        and inputs.get("prepare_passed") is True
        and inputs.get("accept_passed") is True
        and inputs.get("drain_passed") is True
        and inputs.get("accepted_changed_inputs") == 12
        and inputs.get("network_requests") == 0
        and inputs.get("production_acceptance") is False
        and inputs.get("publication") is False
        and inputs.get("repairs_activated") is False,
        "actual review input replay scope differs",
    )
    drain_value = review.get("drain")
    require(isinstance(drain_value, dict), "actual review drain result is missing")
    drain = cast(dict[str, Any], drain_value)
    exceptions = drain.get("unit_exceptions")
    require(
        drain.get("attempted") == 100
        and drain.get("interrupted_attempts") == 0
        and drain.get("status") == "bounded_stop"
        and drain.get("outcomes")
        == {
            "parse/admission_needs_review": 1,
            "parse/output_committed": 29,
            "parse/unit_exception": 3,
            "project/output_committed": 67,
        }
        and isinstance(exceptions, dict)
        and exceptions.get("count") == 3
        and exceptions.get("parser") == "registry_crosscheck"
        and exceptions.get("method") == "MANUAL"
        and exceptions.get("source") == "crosscheck"
        and exceptions.get("kind") == "registry_dump"
        and exceptions.get("snapshot_ids") == STALE_CROSSCHECK_SNAPSHOTS
        and exceptions.get("attempt_tokens_match_checkpoint") is True
        and exceptions.get("candidate006_acceptance_left_tokens_unchanged") is True
        and exceptions.get("all_checkpoint_manual_crosscheck_pending_tokens_accounted_for") is True,
        "scratch replay contains unproved or changed registry_crosscheck exceptions",
    )
    admission = drain.get("admission_review")
    require(
        admission
        == {
            "classification": "expected ordinary WDR admission review",
            "count": 1,
            "snapshot_id": EXPECTED_WDR_REVIEW,
        },
        "scratch replay admission review differs from the single expected WDR review",
    )
    sidecars = review.get("sidecars")
    require(
        sidecars
        == {
            "expected_top_level_sidecars": 7,
            "input_scratch_exact": True,
            "migration_scratch_exact": True,
            "restore_scratch_exact": True,
        },
        "actual review sidecar closure differs",
    )

    retained = review.get("retained_receipts")
    require(
        isinstance(retained, dict) and set(retained) == set(REHEARSAL_RECEIPT_SHA256),
        "actual review retained receipt closure differs",
    )
    retained = cast(dict[str, Any], retained)
    paths: dict[str, Path] = {}
    for name, expected_sha256 in REHEARSAL_RECEIPT_SHA256.items():
        spec = retained.get(name)
        require(
            isinstance(spec, dict)
            and spec.get("path") == REHEARSAL_RECEIPT_PATH[name]
            and spec.get("sha256") == expected_sha256,
            f"actual review {name} receipt pin differs",
        )
        path = (receipt_path.parent / REHEARSAL_RECEIPT_PATH[name]).resolve(strict=True)
        review_root = receipt_path.parent.parent.resolve(strict=True)
        require(
            path.is_relative_to(review_root)
            and not path.is_symlink()
            and path.is_file()
            and sha(path) == expected_sha256,
            f"actual review {name} receipt differs",
        )
        paths[name] = path
    return paths, {name: read_json(path) for name, path in paths.items()}


def checkpoint_documents(
    gate: dict[str, Any], gate_path: Path
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    checkpoint_value = gate.get("checkpoint")
    require(isinstance(checkpoint_value, dict), "checkpoint gate is malformed")
    checkpoint_value = cast(dict[str, Any], checkpoint_value)
    checkpoint = Path(str(checkpoint_value.get("path", "")))
    require(not checkpoint.is_symlink(), "checkpoint cannot be a symlink")
    checkpoint = checkpoint.resolve(strict=True)
    require(
        checkpoint == CHECKPOINT,
        "checkpoint path is outside exact held scope",
    )
    manifest_path = checkpoint / "checkpoint.json"
    manifest_sha256 = str(checkpoint_value.get("manifest_sha256", ""))
    require(
        manifest_sha256 == CHECKPOINT_SHA256 and sha(manifest_path) == manifest_sha256,
        "checkpoint manifest pin differs",
    )
    manifest = read_json(manifest_path)
    require(
        manifest.get("schema_version") == 28
        and manifest.get("baseline_candidate") == CANDIDATE
        and manifest.get("pending_candidate") is None
        and isinstance(manifest.get("files"), dict),
        "checkpoint is not the held schema28 predecessor",
    )
    for field, label, expected_sha256 in (
        ("capture_receipt", "capture receipt", CHECKPOINT_CAPTURE_SHA256),
        ("verified_summary", "verified checkpoint summary", CHECKPOINT_VERIFIED_SHA256),
        ("checkpoint_helper", "checkpoint helper", CHECKPOINT_HELPER_SHA256),
    ):
        spec = checkpoint_value.get(field)
        require(
            isinstance(spec, dict) and spec.get("sha256") == expected_sha256,
            f"{label} pin is malformed or differs from checkpoint004",
        )
        path = reference(gate_path.parent, cast(dict[str, Any], spec), label)
        checkpoint_value[field + "_resolved"] = str(path)
    receipt = read_json(Path(checkpoint_value["capture_receipt_resolved"]))
    verified = read_json(Path(checkpoint_value["verified_summary_resolved"]))
    helper_digest = str(checkpoint_value["checkpoint_helper"].get("sha256"))
    summary = {
        "path": str(checkpoint),
        "manifest_sha256": manifest_sha256,
        "files": len(manifest["files"]),
        "bytes": sum(int(record["size"]) for record in manifest["files"].values()),
    }
    require(
        verified == summary and receipt.get("checkpoint") == summary,
        "checkpoint acknowledgment does not bind exact manifest",
    )
    archive_commit = str(checkpoint_value.get("archive_commit", ""))
    require(
        archive_commit == ARCHIVE_COMMIT
        and receipt.get("format") == "held-extension-schema28-checkpoint-v1"
        and receipt.get("passed") is True
        and receipt.get("source") == str(PREDECESSOR_SOURCE)
        and receipt.get("system") == str(PREDECESSOR_SYSTEM)
        and receipt.get("private_archive_commit") == archive_commit
        and receipt.get("live_database_changes") == 0
        and receipt.get("baseline") == PUBLIC_BASELINE,
        "checkpoint private acknowledgment differs",
    )
    require(
        receipt.get("units") == {unit: "inactive" for unit in UNITS},
        "checkpoint was not captured with all ordinary units inactive",
    )
    capture_pin = cast(dict[str, Any], checkpoint_value["capture_receipt"])
    verified_pin = cast(dict[str, Any], checkpoint_value["verified_summary"])
    helper_pin = cast(dict[str, Any], checkpoint_value["checkpoint_helper"])
    require(
        capture_pin.get("sha256") == sha(Path(checkpoint_value["capture_receipt_resolved"]))
        and verified_pin.get("sha256") == sha(Path(checkpoint_value["verified_summary_resolved"]))
        and helper_pin.get("sha256") == helper_digest,
        "checkpoint evidence pins differ",
    )
    return checkpoint, manifest, receipt


def validate_rehearsals(
    docs: dict[str, dict[str, Any]],
    packet: dict[str, Any],
    checkpoint: Path,
    manifest_sha: str,
) -> None:
    migration = docs["migration"]
    finished(migration, "event-extension-migration-rehearsal-v1", "migration rehearsal")
    require(
        migration.get("source") == str(SOURCE)
        and migration.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and migration.get("checkpoint") == str(checkpoint)
        and migration.get("checkpoint_sha256") == manifest_sha
        and migration.get("helper_sha256") == packet["files"].get("migration.py")
        and migration.get("old_schema") == 28
        and migration.get("target_schema") == 29
        and migration.get("scope") == "migration_only_without_runtime_input_acceptance",
        "migration rehearsal source or scope differs",
    )
    before = migration.get("before")
    after = migration.get("after")
    require(
        isinstance(before, dict)
        and isinstance(after, dict)
        and len(before) == 116
        and len(after) == 117
        and migration.get("changed_existing_tables") == []
        and all(before[name] == after.get(name) for name in before)
        and migration.get("new_tables") == [TARGET_TABLE]
        and migration.get("top_level_sidecars") == packet.get("top_level_sidecars")
        and migration.get("integrity") == [["ok"]]
        and migration.get("foreign_key_failure") is False,
        "migration rehearsal failed predecessor-preservation checks",
    )

    restore = docs["restore"]
    finished(restore, "extension-operational-restore-rehearsal-v1", "restore rehearsal")
    require(
        restore.get("source") == str(SOURCE)
        and restore.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and restore.get("checkpoint") == str(checkpoint)
        and restore.get("checkpoint_sha256") == manifest_sha
        and restore.get("helper_sha256") == packet["files"].get("restore.py")
        and restore.get("target_schema") == 29
        and restore.get("acknowledged_archive_commit") == packet["archive_commit"]
        and restore.get("stage") == "finished_held_without_input_acceptance"
        and restore.get("operator_hold_present") is True
        and restore.get("restore_pending") is False
        and restore.get("input_acceptance") is False
        and restore.get("workers_started") is False
        and restore.get("repairs_activated") is False
        and restore.get("source_requests") == 0
        and restore.get("public_writes") == 0
        and restore.get("changed_existing_tables") == [],
        "restore rehearsal did not remain held or bind the exact source",
    )

    phases = {
        "inputs_prepare": "prepare",
        "inputs_accept": "accept",
        "inputs_drain": "drain",
    }
    for name, phase in phases.items():
        receipt = docs[name]
        finished(receipt, "extension-input-rehearsal-v1", f"input {phase} rehearsal")
        require(
            receipt.get("phase") == phase
            and receipt.get("source") == str(SOURCE)
            and receipt.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
            and receipt.get("checkpoint_sha256") == manifest_sha
            and receipt.get("helper_sha256") == packet["files"].get("inputs.py")
            and receipt.get("network_requests") == 0
            and receipt.get("production_acceptance") is False
            and receipt.get("publication") is False
            and receipt.get("repairs_activated") is False,
            f"input {phase} rehearsal scope differs",
        )
    accept = docs["inputs_accept"]
    replay = docs["inputs_drain"]
    turn = replay.get("turn")
    require(
        accept.get("input_bundle_hash")
        == "abdf538777c1e4fc05c7f8b9079701bd5a37f276cabe83a8be023673a941d644"
        and replay.get("input_bundle_hash") == accept.get("input_bundle_hash")
        and accept.get("judge_continuity", {}).get("preserved") == 4931
        and accept.get("judge_continuity", {}).get("changed_or_missing") == 0
        and replay.get("judge_continuity", {}).get("preserved") == 4931
        and replay.get("judge_continuity", {}).get("changed_or_missing") == 0
        and isinstance(turn, dict)
        and turn.get("attempted") == 100
        and turn.get("interrupted_attempts") == 0
        and turn.get("status") == "bounded_stop"
        and turn.get("outcomes")
        == {
            "parse/admission_needs_review": 1,
            "parse/output_committed": 29,
            "parse/unit_exception": 3,
            "project/output_committed": 67,
        },
        "bounded input replay differs from the independently reviewed attempt",
    )
    actual = docs["actual_review"]
    bindings = cast(dict[str, Any], actual["bindings"])
    require(
        bindings.get("migration_helper_sha256") == packet["files"].get("migration.py")
        and bindings.get("restore_helper_sha256") == packet["files"].get("restore.py")
        and bindings.get("inputs_helper_sha256") == packet["files"].get("inputs.py"),
        "actual review helper bindings differ from packet 005",
    )


def load_packet(packet_path: Path, expected_sha256: str) -> dict[str, Any]:
    require(not packet_path.is_symlink(), "packet cannot be a symlink")
    packet_path = packet_path.resolve(strict=True)
    require(
        packet_path.name == "packet.json" and sha(packet_path) == expected_sha256,
        "fresh packet digest differs",
    )
    runner_path = packet_path.parent / "runner.py"
    require(not runner_path.is_symlink(), "packet runner cannot be a symlink")
    packet = read_json(packet_path)
    expected_file_sha = packet.get("files", {}).get("runner.py")
    require(
        isinstance(expected_file_sha, str) and sha(runner_path) == expected_file_sha,
        "fresh packet runner differs",
    )
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("held_schema29_packet_runner", runner_path)
        require(spec is not None and spec.loader is not None, "cannot load fresh packet verifier")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        sealed = module.packet_at(packet_path.parent, expected_sha256)
    finally:
        sys.dont_write_bytecode = previous_bytecode
    require(
        sealed.get("source") == str(SOURCE)
        and sealed.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and sealed.get("target_schema") == 29
        and sealed.get("predecessor_schema") == 28
        and sealed.get("predecessor_tables") == 116,
        "fresh packet authority differs",
    )
    result = cast(dict[str, Any], sealed)
    result["packet_sha256"] = expected_sha256
    return result


def verify_packet_binding(
    gate_path: Path,
    packet_ref: dict[str, Any],
    packet_path: Path,
    expected_packet: dict[str, Any],
) -> None:
    """Recheck the exact packet and its runner closure at the migration boundary."""
    current_path = reference(gate_path.parent, packet_ref, "successor packet")
    require(current_path == packet_path, "successor packet path changed after preflight")
    current = load_packet(current_path, str(packet_ref.get("sha256", "")))
    require(current == expected_packet, "successor packet changed after preflight")


def guards(phase: str, expected_phase: str) -> dict[str, Any]:
    require(phase == expected_phase, "migration phase differs")
    systems = {
        path: str(Path(path).resolve(strict=True))
        for path in ("/run/current-system", "/nix/var/nix/profiles/system")
    }
    require_systems(phase, systems["/run/current-system"], systems["/nix/var/nix/profiles/system"])
    require(
        (STATE / "operator-hold").is_file() and not (STATE / "RESTORE_PENDING").exists(),
        "operator hold missing or restore pending",
    )
    require(sha(STATE / "operator-hold") == HOLD_SHA256, "operator hold bytes differ")
    units = {
        unit: subprocess.check_output(
            ["systemctl", "show", unit, "--property=ActiveState", "--value"], text=True
        ).strip()
        for unit in UNITS
    }
    require(all(value == "inactive" for value in units.values()), "ordinary unit is not inactive")
    return {"systems": systems, "units": units, "operator_hold_sha256": HOLD_SHA256}


def require_systems(phase: str, active: str, persistent: str) -> None:
    require(phase in {"before_deployment", "after_deployment"}, "migration phase differs")
    expected = str(PREDECESSOR_SYSTEM if phase == "before_deployment" else TARGET_SYSTEM)
    require(active == expected and persistent == expected, "active/persistent system differs")


def live_sidecars(state: Path, sidecars: dict[str, Any]) -> dict[str, str]:
    actual = {
        path.name
        for path in state.iterdir()
        if path.is_file() and path.name not in RUNTIME_TRANSIENTS and path.name != "state.sqlite"
    }
    require(actual == set(sidecars), "live top-level checkpoint sidecar set differs")
    result: dict[str, str] = {}
    for name, record in sidecars.items():
        path = state / name
        require(not path.is_symlink() and path.is_file(), f"live sidecar missing or linked: {name}")
        require(
            path.stat().st_size == int(record["size"]) and sha(path) == record["sha256"],
            f"live sidecar differs from checkpoint: {name}",
        )
        result[name] = str(record["sha256"])
    require(result.get("operator-hold") == HOLD_SHA256, "live hold sidecar differs")
    return result


def schema_markers(conn: sqlite3.Connection, schema: int) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    require(
        conn.execute("PRAGMA user_version").fetchone()[0] == schema
        and row is not None
        and str(row[0]) == str(schema),
        "database schema markers differ",
    )


def baseline(state: Path, manifest: dict[str, Any]) -> dict[str, str]:
    link = state / "baseline"
    require(
        link.is_symlink() and link.resolve(strict=True) == state / "candidates" / CANDIDATE,
        "live public baseline symlink differs",
    )
    published_path = link.resolve() / "PUBLISHED"
    receipt = read_json(published_path)
    require(
        receipt.get("commit") == PUBLIC_BASELINE and receipt.get("candidate_id") == CANDIDATE,
        "live acknowledged publication differs",
    )
    relative = f"candidates/{CANDIDATE}/PUBLISHED"
    require(relative in manifest["files"], "checkpoint omits baseline receipt")
    record = manifest["files"][relative]
    require(
        sha(published_path) == record["sha256"] and published_path.stat().st_size == record["size"],
        "live baseline receipt differs from checkpoint",
    )
    return {relative: str(record["sha256"])}


def connect(path: Path, *, readonly: bool = False, immutable: bool = False) -> sqlite3.Connection:
    require(not immutable or readonly, "immutable SQLite access must be read-only")
    mode = "ro" if readonly else "rw"
    immutable_query = "&immutable=1" if immutable else ""
    connection = sqlite3.connect(
        f"file:{path}?mode={mode}{immutable_query}", uri=True, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    if not readonly:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
    return connection


def verify_live_database_path(state: Path) -> Path:
    path = state / "state.sqlite"
    metadata = path.lstat()
    require(
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
        "live database must be a singly linked regular file, not a symlink",
    )
    return path


def inspect_checkpoint(
    checkpoint: Path, manifest: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, int | str], dict[str, int | str]]:
    files = cast(dict[str, Any], manifest["files"])
    verify_checkpoint_bytes(checkpoint, files)
    with closing(connect(checkpoint / "state.sqlite", readonly=True, immutable=True)) as conn:
        schema_markers(conn, 28)
        rows = table_receipts(conn)
        require(
            len(rows) == 116 and TARGET_TABLE not in rows, "checkpoint predecessor tables differ"
        )
        require(
            conn.execute(
                "SELECT 1 FROM execution_admissions WHERE state<>'settled' LIMIT 1"
            ).fetchone()
            is None,
            "checkpoint contains unsettled admissions",
        )
        archive_row = conn.execute(
            "SELECT coalesce(sum(requests),0),coalesce(sum(bytes),0) FROM host_budget "
            "WHERE host='web.archive.org' AND day='2026-09-17'"
        ).fetchone()
        origin_row = conn.execute(
            "SELECT coalesce(sum(requests),0),coalesce(sum(bytes),0) FROM host_budget "
            "WHERE host='danceconvention.net' AND day='2026-09-17'"
        ).fetchone()
        archive: dict[str, int | str] = {
            "day": "2026-09-17",
            "requests": int(archive_row[0]),
            "bytes": int(archive_row[1]),
        }
        origin: dict[str, int | str] = {
            "host": "danceconvention.net",
            "day": "2026-09-17",
            "requests": int(origin_row[0]),
            "bytes": int(origin_row[1]),
        }
        require(
            int(archive["requests"]) >= 20 and int(archive["bytes"]) >= 2_952_065,
            "fresh checkpoint paid Archive usage is stale",
        )
        require(
            int(origin["requests"]) >= 4 and int(origin["bytes"]) >= 132_764,
            "fresh checkpoint paid DCN origin usage is stale",
        )
    return rows, archive, origin


def verify_checkpoint_bytes(checkpoint: Path, files: dict[str, Any]) -> None:
    database = checkpoint / "state.sqlite"
    database_record = cast(dict[str, Any], files["state.sqlite"])
    require(
        not database.is_symlink()
        and database.stat().st_nlink == 1
        and database.stat().st_size == int(database_record["size"])
        and sha(database) == database_record["sha256"],
        "checkpoint database bytes differ from acknowledged manifest",
    )
    sidecars = {
        name: cast(dict[str, Any], record)
        for name, record in files.items()
        if len(Path(name).parts) == 1 and name != "state.sqlite"
    }
    for name, record in sidecars.items():
        path = checkpoint / name
        require(
            not path.is_symlink()
            and path.is_file()
            and path.stat().st_size == int(record["size"])
            and sha(path) == record["sha256"],
            f"checkpoint top-level sidecar bytes differ: {name}",
        )


def inspect_live(state: Path, expected: dict[str, Any], schema: int) -> dict[str, Any]:
    with closing(connect(state / "state.sqlite", readonly=True)) as conn:
        schema_markers(conn, schema)
        rows = table_receipts(conn)
        require(rows == expected, "live predecessor rows/columns differ from fresh checkpoint")
        require(
            conn.execute(
                "SELECT 1 FROM execution_admissions WHERE state<>'settled' LIMIT 1"
            ).fetchone()
            is None,
            "live database contains unsettled admissions",
        )
    return rows


def migrate_locked(
    state: Path,
    before: dict[str, Any],
    db_module: Any,
    *,
    verify_inputs: Callable[[], None],
) -> dict[str, Any]:
    database_path = verify_live_database_path(state)
    conn = connect(database_path)
    try:
        database = db_module.Database(state, conn, None)
        require(table_receipts(conn) == before, "live prestate changed at migration boundary")
        verify_inputs()
        db_module._migrate(database)
        schema_markers(conn, 29)
        after = table_receipts(conn)
        changed = [name for name, receipt in before.items() if after.get(name) != receipt]
        new_tables = sorted(set(after) - set(before))
        require(
            not changed and new_tables == [TARGET_TABLE],
            "migration changed protected predecessor tables",
        )
        require(after[TARGET_TABLE]["rows"] == 1, "schema29 dispatch fence row differs")
        require(
            [list(row) for row in conn.execute("PRAGMA integrity_check")] == [["ok"]],
            "migrated database integrity failed",
        )
        require(
            conn.execute("PRAGMA foreign_key_check").fetchone() is None,
            "migrated database foreign keys failed",
        )
    finally:
        conn.close()
    with closing(connect(state / "state.sqlite", readonly=True)) as reopened:
        schema_markers(reopened, 29)
        require(table_receipts(reopened) == after, "reopening changed migrated database")
    return {"after": after, "changed_existing_tables": changed, "new_tables": new_tables}


def validate_gate(
    gate: dict[str, Any], gate_path: Path, helper_sha256: str
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    require(gate.get("format") == "held-schema29-live-migration-gate-v1", "gate format differs")
    require(
        gate.get("helper_sha256") == helper_sha256 == sha(Path(__file__).resolve()),
        "reviewed live migration helper differs",
    )
    require(
        gate.get("state") == str(STATE)
        and gate.get("source") == str(SOURCE)
        and gate.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and gate.get("predecessor_source") == str(PREDECESSOR_SOURCE)
        and gate.get("predecessor_system") == str(PREDECESSOR_SYSTEM)
        and gate.get("target_system") == str(TARGET_SYSTEM),
        "gate source/system scope differs",
    )
    phase = gate.get("phase")
    require(phase in {"before_deployment", "after_deployment"}, "gate phase differs")
    source_receipt_path = reference(
        gate_path.parent, gate.get("source_receipt", {}), "source receipt"
    )
    inventory_source(SOURCE, source_receipt_path)
    checkpoint, manifest, capture_receipt = checkpoint_documents(gate, gate_path)
    packet_ref = gate.get("packet", {})
    require(
        isinstance(packet_ref, dict) and packet_ref.get("sha256") == PACKET_SHA256,
        "packet gate is malformed or binds another packet",
    )
    packet_ref = cast(dict[str, Any], packet_ref)
    packet_path = reference(gate_path.parent, packet_ref, "successor packet")
    packet = load_packet(packet_path, str(packet_ref["sha256"]))
    require(
        packet.get("checkpoint") == str(checkpoint)
        and packet.get("checkpoint_sha256") == gate["checkpoint"]["manifest_sha256"]
        and packet.get("archive_commit") == gate["checkpoint"]["archive_commit"],
        "successor packet checkpoint authority differs",
    )
    _paths, docs = validate_evidence(gate, gate_path)
    validate_rehearsals(docs, packet, checkpoint, gate["checkpoint"]["manifest_sha256"])
    return checkpoint, manifest, packet, docs


def run(
    gate_path: Path, *, gate_sha256: str, helper_sha256: str, execute: bool = False
) -> dict[str, Any]:
    sys.dont_write_bytecode = True
    require(sha(gate_path) == gate_sha256, "reviewed gate SHA differs")
    gate = read_json(gate_path)
    checkpoint, manifest, packet, docs = validate_gate(gate, gate_path, helper_sha256)
    packet_ref = cast(dict[str, Any], gate["packet"])
    packet_path = reference(gate_path.parent, packet_ref, "successor packet")
    requested_phase = "after_deployment" if execute else "before_deployment"
    require(gate["phase"] == requested_phase, "execution mode differs from reviewed gate phase")
    source_receipt = reference(gate_path.parent, gate["source_receipt"], "source receipt")
    expected, archive_usage, origin_usage = inspect_checkpoint(checkpoint, manifest)
    require(
        packet.get("paid_archive_usage") == archive_usage
        and packet.get("paid_origin_usage") == origin_usage,
        "successor packet paid usage differs from checkpoint database",
    )
    packet_sidecars = packet.get("top_level_sidecars")
    require(isinstance(packet_sidecars, dict), "packet top-level sidecar closure missing")
    packet_sidecars = cast(dict[str, Any], packet_sidecars)
    require(
        {"operator-hold", "dcn-origin-event-days.json", "dcn-origin-robots-cache.json"}
        <= set(packet_sidecars),
        "successor packet omits required held-state sidecars",
    )
    checkpoint_files = manifest["files"]
    expected_sidecars = {
        name: record
        for name, record in checkpoint_files.items()
        if len(Path(name).parts) == 1 and name != "state.sqlite"
    }
    require(
        packet_sidecars == expected_sidecars and "operator-hold" in packet_sidecars,
        "packet sidecars differ from fresh checkpoint",
    )
    source_runtime = SOURCE / "src"
    for name, module in tuple(sys.modules.items()):
        if name == "swingset" or name.startswith("swingset."):
            module_path = getattr(module, "__file__", None)
            require(
                module_path is not None
                and Path(module_path).resolve().is_relative_to(source_runtime),
                f"runtime import escaped candidate source: {name}",
            )
    sys.path.insert(0, str(source_runtime))
    db_module = importlib.import_module("swingset.state.db")
    publish_module = importlib.import_module("swingset.publish.service")
    module_file = getattr(db_module, "__file__", None)
    require(
        module_file is not None
        and db_module.SCHEMA_VERSION == 29
        and Path(module_file).resolve().is_relative_to(source_runtime),
        "candidate runtime import differs",
    )
    before_guards = guards(gate["phase"], requested_phase)
    report: dict[str, Any] = {
        "format": "held-schema29-live-migration-receipt-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "executed": False,
        "state": str(STATE),
        "phase": gate["phase"],
        "source": str(SOURCE),
        "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
        "target_system": str(TARGET_SYSTEM),
        "packet_sha256": packet["packet_sha256"],
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": gate["checkpoint"]["manifest_sha256"],
        "archive_commit": gate["checkpoint"]["archive_commit"],
        "input_acceptance": False,
        "collection_resumed": False,
        "repairs_activated": False,
        "publication": False,
        "source_requests": 0,
        "public_writes": 0,
    }
    # Lock order matches production: data writer, control mutex, then SQLite.
    with ExitStack() as stack:
        for name in LOCKS:
            handle = stack.enter_context((STATE / name).open("r+b"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        guards(gate["phase"], requested_phase)
        verify_live_database_path(STATE)
        current_rows = inspect_live(STATE, expected, 28)
        sidecars_before = live_sidecars(STATE, packet_sidecars)
        baseline_before = baseline(STATE, manifest)
        with closing(connect(STATE / "state.sqlite", readonly=True)) as conn:
            admissions = conn.execute(
                "SELECT 1 FROM execution_admissions WHERE state<>'settled' LIMIT 1"
            ).fetchone()
            control_state = [
                list(row) for row in conn.execute("SELECT * FROM control_state ORDER BY singleton")
            ]
        require(admissions is None, "live state contains unsettled execution admissions")
        require(
            not publish_module.pending_candidates(STATE),
            "pending publication requires coordinator reconciliation",
        )
        report.update(
            before=current_rows,
            control_state=control_state,
            sidecars_before=sidecars_before,
            baseline_before=baseline_before,
            guards_before=before_guards,
            preflight_passed=True,
        )

        def verify_inputs() -> None:
            require(
                sha(gate_path) == gate_sha256 and sha(Path(__file__).resolve()) == helper_sha256,
                "reviewed gate or migration helper changed",
            )
            require(
                sha(source_receipt) == SOURCE_RECEIPT_SHA256, "candidate source receipt changed"
            )
            inventory_source(SOURCE, source_receipt)
            require(
                sha(checkpoint / "checkpoint.json") == gate["checkpoint"]["manifest_sha256"],
                "fresh checkpoint manifest changed",
            )
            verify_checkpoint_bytes(checkpoint, cast(dict[str, Any], manifest["files"]))
            for field in ("capture_receipt", "verified_summary", "checkpoint_helper"):
                spec = gate["checkpoint"][field]
                reference(gate_path.parent, spec, field)
            boundary_paths, boundary_docs = validate_evidence(gate, gate_path)
            validate_rehearsals(
                boundary_docs,
                packet,
                checkpoint,
                gate["checkpoint"]["manifest_sha256"],
            )
            require(
                set(boundary_paths) == EVIDENCE_KEYS | set(REHEARSAL_RECEIPT_SHA256),
                "evidence closure changed before migration",
            )
            verify_packet_binding(gate_path, packet_ref, packet_path, packet)
            guards(gate["phase"], requested_phase)
            require(
                not publish_module.pending_candidates(STATE),
                "pending publication requires coordinator reconciliation",
            )
            require(
                live_sidecars(STATE, packet_sidecars) == sidecars_before,
                "live checkpoint sidecars changed before migration",
            )
            require(
                baseline(STATE, manifest) == baseline_before,
                "live publication baseline changed before migration",
            )

        if execute:
            report["executed"] = True
            result = migrate_locked(STATE, current_rows, db_module, verify_inputs=verify_inputs)
            report.update(result)
            with closing(connect(STATE / "state.sqlite", readonly=True)) as conn:
                after_control = [
                    list(row)
                    for row in conn.execute("SELECT * FROM control_state ORDER BY singleton")
                ]
            require(after_control == control_state, "migration changed control state")
            require(
                live_sidecars(STATE, packet_sidecars) == sidecars_before,
                "migration changed checkpoint sidecars",
            )
            require(
                baseline(STATE, manifest) == baseline_before,
                "migration changed publication baseline",
            )
            guards(gate["phase"], requested_phase)
        report["passed"] = True
        report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--gate-sha256", required=True)
    parser.add_argument("--helper-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        result = run(
            args.gate.resolve(strict=True),
            gate_sha256=args.gate_sha256,
            helper_sha256=args.helper_sha256,
            execute=args.execute,
        )
    except BaseException as error:
        print(
            json.dumps(
                {
                    "format": "held-schema29-live-migration-failure-v1",
                    "passed": False,
                    "execution_requested": args.execute,
                    "resulting_schema": (
                        "unknown; coordinator inspection required"
                        if args.execute
                        else "not mutated by preflight"
                    ),
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ),
            flush=True,
        )
        raise
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
