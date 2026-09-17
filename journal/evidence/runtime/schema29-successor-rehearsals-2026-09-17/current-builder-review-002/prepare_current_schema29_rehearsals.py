"""Prepare a sealed schema28→29 packet for a newly verified held checkpoint.

This is a local packaging tool. It does not acknowledge, restore, migrate, accept
inputs, contact services, deploy or publish. The caller must provide and pin the
fresh checkpoint and its retained acknowledgment evidence.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, cast

SOURCE = Path("/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source")
SOURCE_RECEIPT_SHA256 = "9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99"
RUNTIME_SOURCE = str(SOURCE)
DEPLOYED_SOURCE = "/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source"
DEPLOYED_SYSTEM = (
    "/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
)
PUBLIC_BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
CANDIDATE = "cand_8f31cad7226643ae"
BUNDLE = "fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a"
HOLD_SHA256 = "965468bb03ba65b6d1d4950ab8e85c09c8f2f2a323461b7a3f2b6177bba5c638"
PAID_ARCHIVE_DAY = "2026-09-17"
MIN_ARCHIVE_REQUESTS = 20
MIN_ARCHIVE_BYTES = 2_952_065
ORIGIN_HOST = "danceconvention.net"
MIN_ORIGIN_REQUESTS = 4
MIN_ORIGIN_BYTES = 132_764
ORIGIN_SIDECARS = {"dcn-origin-event-days.json", "dcn-origin-robots-cache.json"}
CHECKPOINT_ROOT = Path("/var/lib/swingset/checkpoints")
PACKET_RUNNER = (
    Path(__file__).resolve().parents[2]
    / "evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-003/runner.py"
)
PACKET_RUNNER_SHA256 = "f91a09a313984bfe2468cb4d2ade400e3658bf93a8731b6e8a5f48b5e576dfd9"
BASES = {
    "inputs": "ddb2761bc302d53436f0a257c853cb827d42f071234761fe27c28bf94a8ab861",
    "restore": "1e816a9af567344a6917e8fccd01362f6a2075ca480306bf48f2a0df96e93edf",
    "migration": "225badb71efd35e5ed03b287bcd66c1cf01d4f7dd9e07c81570ccdd456d20f44",
}
OLD_CHECKPOINT = "/var/lib/swingset/checkpoints/extension28-held-20260917-002"
OLD_CHECKPOINT_SHA256 = "4929859092cbd3e15122f3598b5da477a65498976cc744c52cf2b142d1cebea3"
OLD_ARCHIVE_COMMIT = "d71060d4f77b6073f797bd7232bf2aa3694ba685"
EVIDENCE_FILES = {
    "receipt.json": "receipt",
    "checkpoint.py": "helper",
    "checkpoint-verified.json": "verified",
}
PACKET_NAMES = {
    "runner.py",
    *(f"{prefix}{kind}.py" for prefix in ("", "base-") for kind in BASES),
    *("backup-" + name for name in EVIDENCE_FILES),
}


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest_text(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def validate_checkpoint(
    checkpoint: Path,
    manifest_sha256: str,
    receipt_path: Path,
    receipt_sha256: str,
    verified_path: Path,
    verified_sha256: str,
    helper_path: Path,
    helper_sha256: str,
) -> dict[str, Any]:
    for path, label, expected in (
        (receipt_path, "capture receipt", receipt_sha256),
        (verified_path, "verified summary", verified_sha256),
        (helper_path, "checkpoint helper", helper_sha256),
    ):
        require(path.is_file() and not path.is_symlink(), f"{label} must be a regular file")
        require(digest_text(expected) and sha(path) == expected, f"{label} pin differs")
    require(not checkpoint.is_symlink(), "checkpoint cannot be a symlink")
    checkpoint = checkpoint.resolve(strict=True)
    require(checkpoint.is_dir(), "checkpoint must be a real directory")
    require(
        checkpoint.parent == CHECKPOINT_ROOT.resolve(strict=True),
        "checkpoint must be under the protected checkpoint root",
    )
    require(
        checkpoint.name.startswith("extension28-held-"),
        "checkpoint name is outside the held schema28 scope",
    )
    require(digest_text(manifest_sha256), "manifest digest must be SHA-256")
    manifest_path = checkpoint / "checkpoint.json"
    require(
        not manifest_path.is_symlink() and sha(manifest_path) == manifest_sha256,
        "checkpoint manifest differs",
    )
    manifest = read_json(manifest_path)
    require(
        manifest.get("format") == 1
        and manifest.get("schema_version") == 28
        and manifest.get("baseline_candidate") == CANDIDATE
        and manifest.get("pending_candidate") is None,
        "checkpoint is not the held acknowledged schema28 predecessor",
    )
    files_value = manifest.get("files")
    require(isinstance(files_value, dict) and bool(files_value), "checkpoint inventory is empty")
    assert isinstance(files_value, dict)
    files: dict[str, Any] = files_value
    require(
        "state.sqlite" in files and "operator-hold" in files,
        "checkpoint omits required top-level state",
    )
    require(
        all(
            isinstance(name, str) and not Path(name).is_absolute() and ".." not in Path(name).parts
            for name in files
        ),
        "checkpoint inventory path escapes",
    )
    require(
        all(
            isinstance(record, dict)
            and isinstance(record.get("size"), int)
            and digest_text(str(record.get("sha256", "")))
            for record in files.values()
        ),
        "checkpoint inventory record is malformed",
    )
    actual_files: set[str] = set()
    for path in checkpoint.rglob("*"):
        require(not path.is_symlink(), "checkpoint closure cannot contain symlinks")
        if path.is_file() and path != manifest_path:
            actual_files.add(path.relative_to(checkpoint).as_posix())
    require(actual_files == set(files), "checkpoint file closure differs from manifest")
    sidecars: dict[str, Any] = {
        name: dict(record)
        for name, record in files.items()
        if len(Path(name).parts) == 1 and name != "state.sqlite"
    }
    require("operator-hold" in sidecars, "operator hold is not a top-level sidecar")
    require(ORIGIN_SIDECARS <= set(sidecars), "checkpoint omits current DCN origin sidecars")
    verified = read_json(verified_path)
    count = len(files)
    size = sum(int(record["size"]) for record in files.values())
    expected_summary = {
        "path": str(checkpoint),
        "manifest_sha256": manifest_sha256,
        "files": count,
        "bytes": size,
    }
    require(verified == expected_summary, "verified checkpoint summary differs from manifest")
    receipt = read_json(receipt_path)
    require(
        receipt.get("format") == "held-extension-schema28-checkpoint-v1"
        and receipt.get("passed") is True
        and receipt.get("source") == DEPLOYED_SOURCE
        and receipt.get("system") == DEPLOYED_SYSTEM
        and receipt.get("baseline") == PUBLIC_BASELINE
        and receipt.get("live_database_changes") == 0,
        "checkpoint capture/acknowledgment receipt authority differs",
    )
    units = receipt.get("units")
    expected_units = {
        f"swingset-{kind}.{suffix}": "inactive"
        for kind in ("cycle", "backup", "summary")
        for suffix in ("service", "timer")
    }
    require(units == expected_units, "checkpoint was not taken with all ordinary units inactive")
    require(
        receipt.get("checkpoint") == expected_summary
        and re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("private_archive_commit", "")))
        is not None,
        "private archive acknowledgment does not bind the verified checkpoint",
    )
    baseline_path = checkpoint / "candidates" / CANDIDATE / "PUBLISHED"
    require(
        baseline_path.relative_to(checkpoint).as_posix() in files,
        "checkpoint omits the acknowledged baseline sidecar",
    )
    published = read_json(baseline_path)
    require(
        published.get("commit") == PUBLIC_BASELINE
        and published.get("candidate_id") == CANDIDATE
        and isinstance(published.get("verified_at"), str)
        and isinstance(published.get("closure_digest"), (str, type(None)))
        and "evidence_cutoff" in published,
        "checkpoint public baseline differs",
    )
    try:
        verified_at = datetime.fromisoformat(published["verified_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("checkpoint PUBLISHED timestamp differs") from error
    require(
        verified_at.tzinfo is not None, "checkpoint PUBLISHED timestamp must include a timezone"
    )
    database = checkpoint / "state.sqlite"
    require(
        not database.is_symlink()
        and database.stat().st_nlink == 1
        and database.stat().st_size == int(files["state.sqlite"]["size"]),
        "checkpoint database file differs",
    )
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        require(
            conn.execute("PRAGMA user_version").fetchone()[0] == 28,
            "checkpoint pragma schema differs",
        )
        schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        require(schema is not None and str(schema[0]) == "28", "checkpoint meta schema differs")
        require(
            conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            == 116,
            "checkpoint predecessor table population differs",
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
            "WHERE host='web.archive.org' AND day=?",
            (PAID_ARCHIVE_DAY,),
        ).fetchone()
        archive_requests, archive_bytes = int(archive_row[0]), int(archive_row[1])
        archive_usage: dict[str, int | str] = {
            "day": PAID_ARCHIVE_DAY,
            "requests": archive_requests,
            "bytes": archive_bytes,
        }
        require(
            archive_requests >= MIN_ARCHIVE_REQUESTS and archive_bytes >= MIN_ARCHIVE_BYTES,
            "checkpoint omits current paid Archive usage",
        )
        origin_row = conn.execute(
            "SELECT coalesce(sum(requests),0),coalesce(sum(bytes),0) FROM host_budget "
            "WHERE host=? AND day=?",
            (ORIGIN_HOST, PAID_ARCHIVE_DAY),
        ).fetchone()
        origin_requests, origin_bytes = int(origin_row[0]), int(origin_row[1])
        origin_usage: dict[str, int | str] = {
            "host": ORIGIN_HOST,
            "day": PAID_ARCHIVE_DAY,
            "requests": origin_requests,
            "bytes": origin_bytes,
        }
        require(
            origin_requests >= MIN_ORIGIN_REQUESTS and origin_bytes >= MIN_ORIGIN_BYTES,
            "checkpoint omits current paid DCN origin usage",
        )
    for name, record in files.items():
        path = checkpoint / name
        require(
            path.is_file() and not path.is_symlink(),
            f"checkpoint artifact missing or linked: {name}",
        )
        require(
            path.stat().st_size == int(record["size"]) and sha(path) == record["sha256"],
            f"checkpoint artifact differs: {name}",
        )
    require(
        sha(checkpoint / "operator-hold") == HOLD_SHA256,
        "checkpoint operator hold differs",
    )
    require(manifest.get("input_bundle_hash") == BUNDLE, "checkpoint input bundle differs")
    return {
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": manifest_sha256,
        "archive_commit": receipt["private_archive_commit"],
        "capture_receipt_sha256": receipt_sha256,
        "verified_summary_sha256": verified_sha256,
        "checkpoint_helper_sha256": helper_sha256,
        "predecessor_schema": 28,
        "predecessor_tables": 116,
        "checkpoint_files": count,
        "checkpoint_bytes": size,
        "paid_archive_usage": archive_usage,
        "paid_origin_usage": origin_usage,
        "top_level_sidecars": sidecars,
        "receipt": receipt,
        "verified_summary": verified,
    }


def replace(body: str, old: str, new: str, count: int) -> str:
    require(body.count(old) == count, f"reviewed transformation seam differs: {old!r}")
    return body.replace(old, new)


def reviewed_packet_runner() -> Any:
    require(sha(PACKET_RUNNER) == PACKET_RUNNER_SHA256, "reviewed packet runner differs")
    spec = importlib.util.spec_from_file_location("reviewed_schema29_packet_runner", PACKET_RUNNER)
    require(spec is not None and spec.loader is not None, "cannot load reviewed packet runner")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def derive_helper(
    kind: str,
    body: str,
    receipt_sha256: str,
    checkpoint_sha256: str,
    archive_commit: str,
    sidecars: dict[str, Any],
) -> str:
    require(
        hashlib.sha256(body.encode()).hexdigest() == BASES[kind], "reviewed base helper differs"
    )
    result = reviewed_packet_runner().derive(kind, body, receipt_sha256)
    result = replace(result, OLD_CHECKPOINT_SHA256, checkpoint_sha256, 1 if kind == "inputs" else 0)
    result = replace(result, OLD_ARCHIVE_COMMIT, archive_commit, 1 if kind == "inputs" else 0)
    if kind == "migration":
        seam = (
            '        shutil.copyfile(checkpoint / "operator-hold", destination / "operator-hold")'
        )
        replacement = (
            "        top_level_sidecars = {}\n"
            '        for name, record in manifest["files"].items():\n'
            '            if len(Path(name).parts) != 1 or name == "state.sqlite":\n'
            "                continue\n"
            "            target = destination / name\n"
            "            shutil.copyfile(checkpoint / name, target)\n"
            '            if target.is_symlink() or target.stat().st_size != int(record["size"]) or sha(target) != record["sha256"]:\n'
            '                raise ValueError(f"top-level checkpoint sidecar changed: {name}")\n'
            "            top_level_sidecars[name] = dict(record)\n"
            '        report["top_level_sidecars"] = top_level_sidecars'
        )
        result = replace(result, seam, replacement, 1)
        result = replace(
            result,
            '        if (sha(destination / "operator-hold") != manifest["files"]["operator-hold"]["sha256"]',
            '        if (report["top_level_sidecars"] != {name: record for name, record in manifest["files"].items() if len(Path(name).parts) == 1 and name != "state.sqlite"}\n'
            '                or sha(destination / "operator-hold") != manifest["files"]["operator-hold"]["sha256"]',
            1,
        )
        result = replace(
            result,
            '        report["passed"] = True',
            '        for name, record in report["top_level_sidecars"].items():\n'
            "            path = destination / name\n"
            "            if (not path.is_file() or path.is_symlink()\n"
            '                    or path.stat().st_size != int(record["size"])\n'
            '                    or sha(path) != record["sha256"]):\n'
            '                raise ValueError(f"migrated top-level sidecar changed: {name}")\n'
            '        report["passed"] = True',
            1,
        )
    compile(result, f"derived_{kind}.py", "exec")
    return result


def derive_runner(
    body: str,
    checkpoint: str,
    checkpoint_sha256: str,
    archive_commit: str,
    source: str,
    source_receipt_sha256: str,
    evidence_hashes: dict[str, str],
) -> str:
    require(
        hashlib.sha256(body.encode()).hexdigest() == PACKET_RUNNER_SHA256,
        "reviewed packet runner differs",
    )
    result = replace(body, f'CHECKPOINT = "{OLD_CHECKPOINT}"', f"CHECKPOINT = {checkpoint!r}", 1)
    result = replace(result, OLD_CHECKPOINT_SHA256, checkpoint_sha256, 1)
    result = replace(result, OLD_ARCHIVE_COMMIT, archive_commit, 1)
    constants_seam = f'ARCHIVE_COMMIT = "{archive_commit}"\nBASES = {{'
    result = replace(
        result,
        constants_seam,
        constants_seam.replace(
            "\nBASES = {",
            f"\nRUNTIME_SOURCE = {source!r}\nRUNTIME_RECEIPT_SHA256 = {source_receipt_sha256!r}\n"
            f"PAID_ARCHIVE_DAY = {PAID_ARCHIVE_DAY!r}\n"
            f"MIN_ARCHIVE_REQUESTS = {MIN_ARCHIVE_REQUESTS}\n"
            f"MIN_ARCHIVE_BYTES = {MIN_ARCHIVE_BYTES}\n"
            f"MIN_ORIGIN_REQUESTS = {MIN_ORIGIN_REQUESTS}\n"
            f"MIN_ORIGIN_BYTES = {MIN_ORIGIN_BYTES}\n"
            f"ORIGIN_HOST = {ORIGIN_HOST!r}\n"
            f"ORIGIN_SIDECARS = {sorted(ORIGIN_SIDECARS)!r}\nBASES = {{",
        ),
        1,
    )
    tree = ast.parse(result)
    archive_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "ARCHIVE_PROOF" for target in node.targets
        )
    )
    lines = result.splitlines(keepends=True)
    start, end = archive_assignment.lineno - 1, archive_assignment.end_lineno
    updated = "ARCHIVE_PROOF = " + repr(evidence_hashes) + "\n"
    result = "".join(lines[:start]) + updated + "".join(lines[end:])
    # A sealed runner can execute a packet but cannot build a replacement packet.
    build_start = result.index("def build(")
    build_end = result.index("\ndef packet_at(", build_start)
    result = (
        result[:build_start]
        + 'def build(*_args: Any, **_kwargs: Any) -> None:\n    raise ValueError("sealed runner cannot build packets")\n'
        + result[build_end:]
    )
    names_seam = '        *("backup-" + name for name in ARCHIVE_PROOF),'
    result = replace(result, names_seam, names_seam + '\n        "checkpoint-sidecars.json",', 1)
    closure_seam = "    archive_proof(root)\n    require(\n"
    result = replace(
        result,
        closure_seam,
        "    archive_proof(root)\n"
        '    sidecars = json.loads((root / "checkpoint-sidecars.json").read_bytes())\n'
        '    require(packet["top_level_sidecars"] == sidecars, "checkpoint sidecar closure differs")\n'
        '    require(packet["source"] == RUNTIME_SOURCE and packet["source_receipt_sha256"] == RUNTIME_RECEIPT_SHA256, "runtime authority differs")\n'
        '    require(packet["evidence"] == {"capture_receipt_sha256": ARCHIVE_PROOF["receipt.json"], "checkpoint_helper_sha256": ARCHIVE_PROOF["checkpoint.py"], "verified_summary_sha256": ARCHIVE_PROOF["checkpoint-verified.json"]}, "checkpoint evidence pins differ")\n'
        '    paid = packet["paid_archive_usage"]\n'
        '    require(paid["day"] == PAID_ARCHIVE_DAY and paid["requests"] >= MIN_ARCHIVE_REQUESTS and paid["bytes"] >= MIN_ARCHIVE_BYTES, "paid Archive usage is stale")\n'
        '    origin = packet["paid_origin_usage"]\n'
        '    require(origin["host"] == ORIGIN_HOST and origin["day"] == PAID_ARCHIVE_DAY and origin["requests"] >= MIN_ORIGIN_REQUESTS and origin["bytes"] >= MIN_ORIGIN_BYTES, "paid DCN origin usage is stale")\n'
        '    require(set(ORIGIN_SIDECARS) <= set(sidecars), "DCN origin sidecars are missing")\n'
        "    require(\n",
        1,
    )
    compile(result, "runner.py", "exec")
    return result


def build(
    *,
    source: Path,
    source_receipt_sha256: str,
    runtime_source: str,
    checkpoint: Path,
    checkpoint_sha256: str,
    capture_receipt: Path,
    capture_receipt_sha256: str,
    checkpoint_verified: Path,
    checkpoint_verified_sha256: str,
    checkpoint_helper: Path,
    checkpoint_helper_sha256: str,
    bases: Path,
    output: Path,
) -> dict[str, Any]:
    require(source.resolve(strict=True) == SOURCE, "source is not frozen candidate005")
    require(runtime_source == RUNTIME_SOURCE, "runtime source path is not exact candidate005")
    require(source_receipt_sha256 == SOURCE_RECEIPT_SHA256, "candidate005 receipt pin differs")
    require(not output.exists() and not output.is_symlink(), "packet output must be new")
    resolved_output = output.resolve()
    production = Path("/var/lib/swingset")
    require(
        not resolved_output.is_relative_to(production)
        and not production.is_relative_to(resolved_output)
        and not resolved_output.is_relative_to(SOURCE)
        and not SOURCE.is_relative_to(resolved_output),
        "packet output overlaps protected state",
    )
    proof = validate_checkpoint(
        checkpoint,
        checkpoint_sha256,
        capture_receipt,
        capture_receipt_sha256,
        checkpoint_verified,
        checkpoint_verified_sha256,
        checkpoint_helper,
        checkpoint_helper_sha256,
    )
    checkpoint_path = Path(proof["checkpoint"])
    require(
        not resolved_output.is_relative_to(checkpoint_path)
        and not checkpoint_path.is_relative_to(resolved_output),
        "packet output overlaps checkpoint",
    )
    packet_runner = reviewed_packet_runner()
    originals = {kind: bases / f"rehearse_extension_{kind}.py" for kind in BASES}
    for kind, path in originals.items():
        require(sha(path) == BASES[kind], f"reviewed {kind} base helper differs")
    verifier = packet_runner.load(
        bases / "rehearse_extension_migration.py", "current_schema29_build_verifier"
    )
    verifier.verify_source(source, source / "extension-source.json", source_receipt_sha256)
    require(packet_runner.schema_literal(source) == 29, "candidate runtime is not exact schema29")
    evidence_paths = {
        "receipt.json": capture_receipt,
        "checkpoint.py": checkpoint_helper,
        "checkpoint-verified.json": checkpoint_verified,
    }
    evidence_hashes = {name: sha(path) for name, path in evidence_paths.items()}
    output.mkdir(parents=True)
    for name, path in evidence_paths.items():
        (output / ("backup-" + name)).write_bytes(path.read_bytes())
    for kind, path in originals.items():
        body = path.read_text()
        (output / f"base-{kind}.py").write_bytes(path.read_bytes())
        (output / f"{kind}.py").write_text(
            derive_helper(
                kind,
                body,
                source_receipt_sha256,
                checkpoint_sha256,
                proof["archive_commit"],
                proof["top_level_sidecars"],
            )
        )
    runner_source = PACKET_RUNNER.read_text()
    (output / "runner.py").write_text(
        derive_runner(
            runner_source,
            proof["checkpoint"],
            checkpoint_sha256,
            proof["archive_commit"],
            runtime_source,
            source_receipt_sha256,
            evidence_hashes,
        )
    )
    sidecar_file = output / "checkpoint-sidecars.json"
    sidecar_file.write_text(
        json.dumps(proof["top_level_sidecars"], sort_keys=True, indent=2) + "\n"
    )
    packet_names = PACKET_NAMES | {"checkpoint-sidecars.json"}
    packet = {
        "format": "schema28-to29-current-checkpoint-packet-v1",
        "source": runtime_source,
        "source_receipt_sha256": source_receipt_sha256,
        "checkpoint": proof["checkpoint"],
        "checkpoint_sha256": checkpoint_sha256,
        "archive_commit": proof["archive_commit"],
        "predecessor_schema": 28,
        "predecessor_tables": 116,
        "target_schema": 29,
        "top_level_sidecars": proof["top_level_sidecars"],
        "paid_archive_usage": proof["paid_archive_usage"],
        "paid_origin_usage": proof["paid_origin_usage"],
        "evidence": {
            "capture_receipt_sha256": capture_receipt_sha256,
            "verified_summary_sha256": checkpoint_verified_sha256,
            "checkpoint_helper_sha256": checkpoint_helper_sha256,
        },
        "source_acceptance": "not_established_by_packet_build",
        "files": {path.name: sha(path) for path in sorted(output.iterdir())},
    }
    require(set(packet["files"]) == packet_names, "packet builder closure differs")
    (output / "packet.json").write_text(json.dumps(packet, sort_keys=True, indent=2) + "\n")
    return packet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True).add_parser("build")
    for name in (
        "source",
        "checkpoint",
        "capture-receipt",
        "checkpoint-verified",
        "checkpoint-helper",
        "bases",
        "output",
    ):
        command.add_argument("--" + name, type=Path, required=True)
    for name in (
        "source-receipt-sha256",
        "runtime-source",
        "checkpoint-sha256",
        "capture-receipt-sha256",
        "checkpoint-verified-sha256",
        "checkpoint-helper-sha256",
    ):
        command.add_argument("--" + name, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    build(
        source=args.source,
        source_receipt_sha256=args.source_receipt_sha256,
        runtime_source=args.runtime_source,
        checkpoint=args.checkpoint,
        checkpoint_sha256=args.checkpoint_sha256,
        capture_receipt=args.capture_receipt,
        capture_receipt_sha256=args.capture_receipt_sha256,
        checkpoint_verified=args.checkpoint_verified,
        checkpoint_verified_sha256=args.checkpoint_verified_sha256,
        checkpoint_helper=args.checkpoint_helper,
        checkpoint_helper_sha256=args.checkpoint_helper_sha256,
        bases=args.bases,
        output=args.output,
    )
    print(
        json.dumps({"packet_sha256": sha(args.output / "packet.json"), "output": str(args.output)})
    )


if __name__ == "__main__":
    main()
