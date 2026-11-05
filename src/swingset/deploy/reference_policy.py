"""Exact inputs and resource rules for one frozen offline reference build."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

GIB = 1024**3
FORMAT = "swingset-reference-build-v2"
FILES = ("reference.py", "reference_policy.py", "reference_workspace.py", "reference_worker.py")
POLICY_KEYS = frozenset(
    {
        "format",
        "operation",
        "nonce",
        "vm_root",
        "filesystem_uuid",
        "filesystem_label",
        "checkpoint_root",
        "set_id",
        "manifest_sha256",
        "raw_bytes",
        "raw_sha256",
        "source_tree",
        "source_identity",
        "runtime_ld_library_path",
        "runtime_path",
        "runtime_wrapper",
        "runtime_wrapper_sha256",
        "runtime_python",
        "input_bundle",
        "baseline_candidate",
        "baseline_commit",
        "maximum_backend_bytes",
        "stop_free_bytes",
        "hard_floor_bytes",
        "btrfs",
        "mkfs",
    }
)


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def validate_policy(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != POLICY_KEYS:
        raise ValueError("reference policy fields differ")
    policy = dict(value)
    numeric = {
        "raw_bytes",
        "maximum_backend_bytes",
        "stop_free_bytes",
        "hard_floor_bytes",
    }
    if any(not isinstance(item, str) for key, item in policy.items() if key not in numeric):
        raise ValueError("reference policy string field has another type")
    if policy["format"] != FORMAT:
        raise ValueError("unknown reference policy format")
    nonce = policy["nonce"]
    if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{12}", nonce):
        raise ValueError("invalid reference nonce")
    if policy["vm_root"] != f"/var/tmp/swingset-stage2-reference-r-{nonce}":
        raise ValueError("reference root must be the fresh nonce path")
    if policy["filesystem_label"] != f"swingset-s2-R-{nonce}":
        raise ValueError("reference filesystem label differs")
    if str(uuid.UUID(policy["filesystem_uuid"])) != policy["filesystem_uuid"]:
        raise ValueError("reference filesystem UUID is not canonical")
    if policy["checkpoint_root"] != "/var/lib/swingset/checkpoints":
        raise ValueError("reference checkpoint source differs")
    if not isinstance(policy["set_id"], str) or not re.fullmatch(
        r"set_\d{8}T\d{6}Z(?:-\d+)?", policy["set_id"]
    ):
        raise ValueError("invalid reference checkpoint set")
    for key in ("manifest_sha256", "raw_sha256", "runtime_wrapper_sha256", "input_bundle"):
        if not isinstance(policy[key], str) or not re.fullmatch(r"[a-f0-9]{64}", policy[key]):
            raise ValueError(f"invalid reference {key}")
    if not isinstance(policy["source_identity"], str) or not re.fullmatch(
        r"source-sha256:[a-f0-9]{64}", policy["source_identity"]
    ):
        raise ValueError("invalid frozen source identity")
    if not isinstance(policy["baseline_commit"], str) or not re.fullmatch(
        r"[a-f0-9]{40}", policy["baseline_commit"]
    ):
        raise ValueError("invalid reference baseline commit")
    if not isinstance(policy["baseline_candidate"], str) or not re.fullmatch(
        r"cand_[a-f0-9]{16}", policy["baseline_candidate"]
    ):
        raise ValueError("invalid reference baseline candidate")
    for key in ("source_tree", "runtime_wrapper", "btrfs", "mkfs"):
        path = Path(policy[key])
        if (
            not path.is_absolute()
            or not path.is_relative_to("/nix/store")
            or ".." in path.parts
            or any(x in str(path) for x in "\n\r\0")
        ):
            raise ValueError(f"invalid pinned reference path {key}")
    if (
        Path(policy["btrfs"]).name != "btrfs"
        or Path(policy["mkfs"]).name != "mkfs.btrfs"
        or Path(policy["btrfs"]).parent != Path(policy["mkfs"]).parent
    ):
        raise ValueError("reference filesystem tools differ")
    if policy["runtime_python"] != "/var/lib/swingset/venv/bin/python":
        raise ValueError("reference interpreter differs")
    for key in ("runtime_path", "runtime_ld_library_path"):
        if not isinstance(policy[key], str) or not policy[key]:
            raise ValueError("reference runtime path is empty")
        for entry in policy[key].split(":"):
            path = Path(entry)
            if (
                not path.is_absolute()
                or ".." in path.parts
                or not (
                    entry.startswith("/nix/store/")
                    or entry in {"/run/current-system/sw/bin", "/run/wrappers/bin"}
                )
            ):
                raise ValueError("reference runtime path is not pinned")
    exact = {
        "maximum_backend_bytes": 20 * GIB,
        "stop_free_bytes": 88 * GIB,
        "hard_floor_bytes": 80 * GIB,
    }
    for key, expected in exact.items():
        if type(policy[key]) is not int or policy[key] != expected:
            raise ValueError(f"reference resource bound differs: {key}")
    if type(policy["raw_bytes"]) is not int or not 0 < policy["raw_bytes"] <= 128 * GIB:
        raise ValueError("invalid frozen database size")
    if (
        not isinstance(policy["operation"], str)
        or not policy["operation"].strip()
        or len(policy["operation"]) > 200
    ):
        raise ValueError("reference operation is empty")
    return policy


def validate_review(
    review: object, policy: Mapping[str, Any], policy_bytes: bytes, hashes: Mapping[str, str]
) -> None:
    if (
        not isinstance(review, dict)
        or review.get("verdict") != "ACCEPT"
        or review.get("operation") != policy["operation"]
        or review.get("nonce") != policy["nonce"]
        or review.get("policy_sha256") != sha256(policy_bytes)
        or review.get("script_sha256") != dict(hashes)
        or review.get("exclusive_large_job") is not True
    ):
        raise ValueError("independent review does not accept the exact reference operation")
    if set(hashes) != set(FILES) or any(
        not re.fullmatch(r"[a-f0-9]{64}", x) for x in hashes.values()
    ):
        raise ValueError("reference runner identities differ")


def backend_budget(policy: Mapping[str, Any], host_free: int, vm_free: int) -> int:
    cap = min(
        policy["maximum_backend_bytes"],
        host_free - policy["stop_free_bytes"] - GIB,
        vm_free - policy["stop_free_bytes"] - GIB,
    )
    cap = cap // 4096 * 4096
    if cap < 12 * GIB:
        raise RuntimeError("less than 12 GiB safely allocatable")
    return int(cap)


def validate_mount(mount: Mapping[str, Any], filesystem_uuid: str) -> None:
    options = set(str(mount.get("options", "")).split(","))
    if mount.get("uuid") != filesystem_uuid or mount.get("fstype") != "btrfs":
        raise RuntimeError("private mount identity differs")
    if (
        options & {"nodatacow", "nodatasum", "nodiscard", "discard=async"}
        or not options & {"discard", "discard=sync"}
        or "compress=zstd:3" not in options
    ):
        raise RuntimeError("private compression/discard mount options differ")
    if not {"nodev", "nosuid", "noexec"} <= options:
        raise RuntimeError("private mount safety options differ")


def identity(path: Path) -> dict[str, int]:
    value = path.lstat()
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "uid": value.st_uid,
        "mode": stat.S_IMODE(value.st_mode),
        "logical_bytes": value.st_size,
        "allocated_bytes": value.st_blocks * 512,
    }


def same_identity(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return all(actual[key] == expected[key] for key in ("device", "inode", "uid"))


def validate_lease(lease: Mapping[str, Any], policy: Mapping[str, Any], now: float) -> None:
    if (
        lease.get("nonce") != policy["nonce"]
        or lease.get("stop")
        or now - lease["vm_monotonic_received"] > 10
        or lease["host_free_bytes"] < policy["stop_free_bytes"]
    ):
        raise RuntimeError("host storage lease expired or stopped")


def runtime_environment(
    policy: Mapping[str, Any], mount: Path, *, preflight: bool = False
) -> dict[str, str]:
    temporary = "/nonexistent-reference-r-" + policy["nonce"] if preflight else str(mount / "tmp")
    cache = "/nonexistent-reference-r-" + policy["nonce"] if preflight else str(mount / "cache")
    return {
        "PATH": policy["runtime_path"],
        "LD_LIBRARY_PATH": policy["runtime_ld_library_path"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TMPDIR": temporary,
        "TMP": temporary,
        "TEMP": temporary,
        "XDG_CACHE_HOME": cache,
        "HF_HOME": cache + "/huggingface",
        "HF_HUB_OFFLINE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "ARROW_NUM_THREADS": "1",
        "TZ": "UTC",
    }


def decode_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    policy_bytes = payload["policy_text"].encode()
    policy = validate_policy(json.loads(policy_bytes))
    sources = payload["runner_sources"]
    if set(sources) != set(FILES) or any(
        not isinstance(body, str) or len(body.encode()) > 256 * 1024 for body in sources.values()
    ):
        raise ValueError("reference runner payload differs")
    hashes = {name: sha256(body.encode()) for name, body in sources.items()}
    if hashes != payload["script_sha256"]:
        raise ValueError("reference runner payload digest differs")
    review_bytes = payload["review_text"].encode()
    validate_review(json.loads(review_bytes), policy, policy_bytes, hashes)
    if (
        not re.fullmatch(r"[a-f0-9]{32}", payload["invocation_id"])
        or type(payload["host_free_bytes"]) is not int
    ):
        raise ValueError("invalid reference invocation or host lease")
    return {
        **policy,
        "invocation_id": payload["invocation_id"],
        "host_free_bytes": payload["host_free_bytes"],
        "script_sha256": hashes,
        "policy_sha256": sha256(policy_bytes),
        "review_sha256": sha256(review_bytes),
        "_operation": dict(payload),
    }


def validate_runtime(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = decode_payload(payload["_operation"])
    if any(
        payload.get(key) != expected[key]
        for key in (
            *POLICY_KEYS,
            "invocation_id",
            "script_sha256",
            "policy_sha256",
            "review_sha256",
        )
    ):
        raise ValueError("reference runtime changed its reviewed inputs")
    return dict(payload)


def command_failure(
    root: Path,
    argv: list[str],
    returncode: int | None,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    error: str = "",
) -> None:
    """Retain a bounded diagnostic before raising, including in RO preflight."""

    def bounded(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        return (value or "")[-16384:]

    row = {
        "argv": argv,
        "exit_code": returncode,
        "stdout": bounded(stdout),
        "stderr": bounded(stderr),
        "error": error[:1000],
    }
    body = canonical(row) + b"\n"
    try:
        path = root / f"command-failure-{time.time_ns()}.json"
        with path.open("xb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        # Preflight deliberately makes this root read-only; the supervisor
        # persists its captured stderr before inspecting the child exit.
        print(body.decode(), file=sys.stderr, flush=True)


def checked_command(root: Path, argv: list[str], timeout: float = 30) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        command_failure(
            root,
            argv,
            None,
            exc.stdout if isinstance(exc, subprocess.TimeoutExpired) else None,
            exc.stderr if isinstance(exc, subprocess.TimeoutExpired) else None,
            str(exc),
        )
        raise
    if result.returncode:
        command_failure(root, argv, result.returncode, result.stdout, result.stderr)
        raise RuntimeError("owned command failed: " + " ".join(argv[:3]))
    return result.stdout
