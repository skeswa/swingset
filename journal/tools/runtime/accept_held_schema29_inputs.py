"""Seal and accept only candidate006 inputs on the held schema29 production state.

Preflight is read-only.  Seal rehearses ``capture()`` and ``accept()`` against a
disposable disk-backed database copy and writes the complete expected transition.  Execution
requires that separately reviewed seal, repeats every mutable check while holding
the writer then control locks, and commits only if the live transition is byte-for-
byte equivalent to the seal.  This helper cannot run workers, fetch, repair,
publish, remove the operator hold, or resume ordinary operation.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

STATE = Path("/var/lib/swingset")
SOURCE = Path("/nix/store/rgyryll4d55rgzscdqhjcwmkr325a76f-source")
SYSTEM = Path(
    "/nix/store/5d9nlyflv9d4gb89a5wayhiarj01znnh-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
)
SYSTEMCTL = Path("/run/current-system/sw/bin/systemctl")
CONFIG = SOURCE / "config"
OVERRIDES = Path("/Users/skeswa/repos/skeswa/swingset/overrides")
SOURCE_RECEIPT_SHA256 = "a2704c7c99ce5aef4fbd0f0df8b23343be5df2a688945e236b8e6856b320ad5f"
POSTMIGRATION_SHA256 = "81534a3b61c305afaddd195eecaa8dcae40e6a20323b610d042199a67c8d39c3"
PACKET_SHA256 = "0d455559b7e0cd767cb1739f10271eb12e8178bebb5a4ab283bd8e74733b6ff3"
INPUT_ACCEPT_SHA256 = "9ba0ca7bed02b165ca64fc5595ea3466ca28358740e52498a2fe11770b27beff"
INPUT_DRAIN_SHA256 = "b47e7faff25181a01c53d869a98ccd25e60ce622e880290ae83955c29af4c02f"
ACTUAL_REVIEW_SHA256 = "efe8547205a7d938dbc65a9f720cf47635ddd78cbe7f4ab7833099cf307b803d"
OLD_BUNDLE = "fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a"
TARGET_BUNDLE = "abdf538777c1e4fc05c7f8b9079701bd5a37f276cabe83a8be023673a941d644"
SHADOW_ROOT = Path("/var/tmp")
BASELINE_CANDIDATE = "cand_8f31cad7226643ae"
BASELINE_COMMIT = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
BASELINE_RECEIPT_SHA256 = "d4de650ec774534ae62978aa7dc88c3f8a093de96725f8bb9b64a8a0e0f03cc4"
HOLD_SHA256 = "965468bb03ba65b6d1d4950ab8e85c09c8f2f2a323461b7a3f2b6177bba5c638"
GATE_FORMAT = "held-schema29-input-acceptance-gate-v1"
SEAL_FORMAT = "held-schema29-input-acceptance-seal-v1"
RECEIPT_FORMAT = "held-schema29-input-acceptance-receipt-v1"
PHASES = ("preflight", "seal", "execution")
UNITS = tuple(
    f"swingset-{kind}.{suffix}"
    for kind in ("cycle", "backup", "summary")
    for suffix in ("service", "timer")
)
SIDECARS: dict[str, tuple[int, str]] = {
    "dcn-origin-event-days.json": (
        520,
        "73a5fc787d0c60f1dd7b87633f5770540354e7b9295fffee301be70e6dcf9444",
    ),
    "dcn-origin-robots-cache.json": (
        335,
        "1ff2d9e49b957569cfa0a645e0df0a4555a2d1cda8df9c51adef7d0a7ea77045",
    ),
    "operator-hold": (66, HOLD_SHA256),
    "overnight-registry-result.json": (
        191,
        "9f69848aee480c129d2420a5648e04e519b29f55fbdd6cb20757a84057cb805c",
    ),
    "overnight-registry-worker.py": (
        10308,
        "f93bb995fcbe8e749db6c645f9f593d63209faf123fba1858e55d97715f38f74",
    ),
    "phase1-catalog.json": (
        55922,
        "0355e34d69e23fc5a7096e07a99d420d4353bdb4fc59284497483ae856d084d9",
    ),
    "phase1-ledger.json": (
        99840,
        "370ad76d175e4e4fa9cf344ec2d47268338dbba128194ecef65904a74632653c",
    ),
}
EXTERNAL_FILES = {
    "config/hosts.toml": "a32134c7dd6891b8a2f7856cf6851651d5617af937b8c45cb63b9541e8be74d1",
    "config/sources.toml": "acd354d61fc559a0d29198c6521c41278d4d76103dc87fa96fc3db8e4b121ee1",
    "overrides/event_aliases.csv": "2a0ef9ae578fe194a512f490b6cb80d99d6b78133b57e5409c0f52dad575d9da",
    "overrides/identity_overrides.csv": "e1487f143891001bc7ddb16cfe6e32003e1ac95628fcaa3a03849e3806f6a246",
    "overrides/nicknames.csv": "468106077a4b1aac34289b705b16e38f7bc29de28376d5db052d84a3c5e50dcf",
    "overrides/series_aliases.csv": "92366188e4295b901772dbe1cc3e1f12116fab46be9765a577a8845e31815642",
    "overrides/source_urls.csv": "cce22c27665b53c67291a41dc18e99989b6b3eab4fe817aeb5e902e6f251e9a1",
    "overrides/suppressions.csv": "2362faa4e3e099c1d307b9a72182e016f60818ab067135be75feb53bccd61fd9",
}
EXPECTED_CHANGES = {
    "config/hosts.toml": (
        "6c0b8d7b34e1c4a068374c59d0f41d52459f0113883b9868a6750c2753123122",
        EXTERNAL_FILES["config/hosts.toml"],
    ),
    "config/sources.toml": (
        "276a2bca3c4dea77525d8f241a83531a2fd908fcb5071dddca494e1ba850ff27",
        EXTERNAL_FILES["config/sources.toml"],
    ),
    "overrides/event_aliases.csv": (
        "3fb16a5e6eab2923e3d157341f80bef4a5a5d5685e4f2288e304d79be074570d",
        EXTERNAL_FILES["overrides/event_aliases.csv"],
    ),
    "recipe/runtime": (
        "a82195b31e177abb211842713e7b298fd7775dcde50488d6db6e7c43da09a05a",
        "0c0164c411a89d93babd7984cefb07ce8637858982144d87c39f0e71443d07dc",
    ),
    "version/extract/scoringdance.round": ("3", "4"),
    "version/extract/steprightsolutions.event": ("1", "2"),
    "version/extract/steprightsolutions.round": ("1", "2"),
    "version/parser/scoringdance.round": ("3", "4"),
    "version/parser/steprightsolutions.event": ("1", "2"),
    "version/parser/steprightsolutions.round": ("1", "2"),
    "version/parser/wsdc_newsletter.events": ("6", "8"),
    "version/repository": (
        "uncommitted:z689qy41inndill3d92ym8im852x3649-source",
        "source-sha256:f07ac1cad9fa5b861d3f12a29c5740bbdf2b4489b2ca1a4e90bcbbfa4e2a76cc",
    ),
}
ALLOWED_CHANGED_TABLES = {
    "accepted_inputs",
    "derivation_input_versions",
    "derivation_scopes",
    "history_dispatch_fence",
    "meta",
    "pending_work",
    "watches",
    "work_generations",
}
EVIDENCE_SHA256 = {
    "postmigration": POSTMIGRATION_SHA256,
    "packet": PACKET_SHA256,
    "input_accept": INPUT_ACCEPT_SHA256,
    "input_drain": INPUT_DRAIN_SHA256,
    "actual_review": ACTUAL_REVIEW_SHA256,
}
RUNTIME_TRANSIENTS = {"state.sqlite-wal", "state.sqlite-shm", "state.lock", "control.lock"}


@dataclass(frozen=True)
class Runtime:
    Database: type[Any]
    capture: Callable[[Path, Path, Path, Mapping[str, str]], Any]
    accept: Callable[[Any, Any, Any], set[str]]
    versions: Callable[[], dict[str, str]]


@dataclass(frozen=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        del seconds
        raise RuntimeError("input acceptance cannot sleep")


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    require(len(data) <= 128 * 1024 * 1024, f"JSON exceeds bounded size: {path}")
    value = json.loads(data)
    require(isinstance(value, dict), f"JSON must be an object: {path}")
    return cast(dict[str, Any], value)


def write_new(path: Path, value: dict[str, Any]) -> None:
    require(not path.exists() and not path.is_symlink(), "receipt path must be new")
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def reference(base: Path, spec: Any, label: str) -> Path:
    require(isinstance(spec, dict), f"{label} reference is malformed")
    spec = cast(dict[str, Any], spec)
    name = Path(str(spec.get("path", "")))
    require(".." not in name.parts and str(name) not in {"", "."}, f"{label} path escapes")
    path = name if name.is_absolute() else base / name
    require(not path.is_symlink(), f"{label} cannot be a symlink")
    path = path.resolve(strict=True)
    expected = str(spec.get("sha256", ""))
    require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None, f"{label} SHA malformed")
    require(path.is_file() and sha(path) == expected, f"{label} differs")
    return path


def closed(doc: dict[str, Any], expected_format: str, label: str) -> None:
    require(doc.get("format") == expected_format and doc.get("passed") is True, f"{label} failed")
    try:
        finished = datetime.fromisoformat(str(doc.get("finished_at", "")).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is not closed") from error
    require(finished.tzinfo is not None, f"{label} completion lacks timezone")


def validate_evidence(gate: dict[str, Any], gate_path: Path) -> dict[str, Path]:
    value = gate.get("evidence")
    require(
        isinstance(value, dict) and set(value) == set(EVIDENCE_SHA256), "evidence closure differs"
    )
    specs = cast(dict[str, Any], value)
    for name, digest in EVIDENCE_SHA256.items():
        require(
            isinstance(specs[name], dict) and specs[name].get("sha256") == digest,
            f"{name} pin differs",
        )
    paths = {name: reference(gate_path.parent, spec, name) for name, spec in specs.items()}
    docs = {name: read_json(path) for name, path in paths.items()}
    post = docs["postmigration"]
    closed(post, "held-schema29-postmigration-independent-review-v1", "postmigration review")
    require(
        post.get("status", {}).get("deployed") is True
        and post.get("status", {}).get("production_input_accepted") is False
        and post.get("bindings", {}).get("candidate006", {}).get("source") == str(SOURCE)
        and post.get("bindings", {}).get("candidate006", {}).get("system") == str(SYSTEM)
        and post.get("database", {}).get("live", {}).get("schema_markers") == [29, 29]
        and post.get("database", {}).get("live", {}).get("unsettled_execution_admissions") == 0
        and post.get("held_poststate", {}).get("operator_hold_sha256") == HOLD_SHA256,
        "postmigration authority differs",
    )
    packet = docs["packet"]
    require(
        packet.get("format") == "schema28-to29-current-checkpoint-packet-v1"
        and packet.get("source") == str(SOURCE)
        and packet.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and packet.get("target_schema") == 29
        and packet.get("top_level_sidecars")
        == {name: {"size": size, "sha256": digest} for name, (size, digest) in SIDECARS.items()},
        "packet005 contract differs",
    )
    for name, phase in (("input_accept", "accept"), ("input_drain", "drain")):
        doc = docs[name]
        closed(doc, "extension-input-rehearsal-v1", name)
        require(
            doc.get("phase") == phase
            and doc.get("source") == str(SOURCE)
            and doc.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
            and doc.get("input_bundle_hash") == TARGET_BUNDLE
            and doc.get("production_acceptance") is False
            and doc.get("network_requests") == 0
            and doc.get("publication") is False
            and doc.get("repairs_activated") is False,
            f"{name} contract differs",
        )
    require(
        sorted(docs["input_accept"].get("accepted_inputs", [])) == sorted(EXPECTED_CHANGES),
        "accepted rehearsal inputs differ",
    )
    review = docs["actual_review"]
    closed(review, "schema29-candidate006-actual-rehearsal-independent-review-v1", "actual review")
    require(
        review.get("bindings", {}).get("packet_sha256") == PACKET_SHA256
        and review.get("bindings", {}).get("source") == str(SOURCE)
        and review.get("inputs", {}).get("input_bundle_hash") == TARGET_BUNDLE
        and review.get("inputs", {}).get("accepted_changed_inputs") == 12
        and review.get("gate", {}).get("production_input_acceptance") is False
        and review.get("blockers") == [],
        "actual review contract differs",
    )
    return paths


def verify_source(receipt_path: Path) -> None:
    require(
        receipt_path == SOURCE / "extension-source.json", "candidate source receipt path differs"
    )
    require(sha(receipt_path) == SOURCE_RECEIPT_SHA256, "candidate source receipt differs")
    receipt = read_json(receipt_path)
    files = receipt.get("files")
    require(isinstance(files, dict) and len(files) == 2939, "candidate source inventory differs")
    files = cast(dict[str, Any], files)
    actual = {
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path != receipt_path
    }
    require(
        not any(path.is_symlink() for path in SOURCE.rglob("*")),
        "candidate source contains symlink",
    )
    require(actual == set(files), "candidate source paths differ")
    for name, record in files.items():
        relative = Path(name)
        require(
            not relative.is_absolute() and ".." not in relative.parts, "source inventory escapes"
        )
        expected = record["sha256"] if isinstance(record, dict) else record
        require(sha(SOURCE / relative) == expected, f"candidate source differs: {name}")


def external_files() -> dict[str, str]:
    actual: dict[str, str] = {}
    for name in EXTERNAL_FILES:
        relative = Path(name)
        path = SOURCE / relative if relative.parts[0] == "config" else OVERRIDES / relative.name
        require(
            not path.is_symlink() and path.is_file(), f"external input missing or linked: {name}"
        )
        actual[name] = sha(path)
    require(actual == EXTERNAL_FILES, "external input bytes differ")
    require(
        {path.name for path in OVERRIDES.glob("*.csv")}
        == {Path(name).name for name in EXTERNAL_FILES if name.startswith("overrides/")},
        "external override inventory differs",
    )
    require(not os.environ.get("SWINGSET_REVISION"), "repository identity override is forbidden")
    return actual


def table_receipts(conn: sqlite3.Connection) -> dict[str, Any]:
    result: dict[str, Any] = {}
    names = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for (name,) in names:
        quoted = '"' + str(name).replace('"', '""') + '"'
        columns = list(conn.execute(f"PRAGMA table_info({quoted})"))
        primary = sorted((int(row[5]), str(row[1])) for row in columns if int(row[5]))
        order = ",".join('"' + column.replace('"', '""') + '"' for _, column in primary)
        query = f"SELECT * FROM {quoted} ORDER BY " + (order or "rowid")
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute(query):
            body = json.dumps(
                list(row),
                ensure_ascii=True,
                separators=(",", ":"),
                default=lambda value: (
                    {"bytes": value.hex()} if isinstance(value, bytes) else str(value)
                ),
            ).encode()
            digest.update(len(body).to_bytes(8, "big"))
            digest.update(body)
            count += 1
        result[str(name)] = {
            "rows": count,
            "sha256": digest.hexdigest(),
            "columns": [str(row[1]) for row in columns],
        }
    return result


def query_receipt(conn: sqlite3.Connection, query: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(query):
        body = json.dumps(list(row), separators=(",", ":"), default=str).encode()
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def accepted_map(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline' ORDER BY input_name"
        )
    }


def validate_database(conn: sqlite3.Connection, *, expected_bundle: str) -> dict[str, Any]:
    schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    require(
        conn.execute("PRAGMA user_version").fetchone()[0] == 29
        and schema is not None
        and str(schema[0]) == "29",
        "database is not exact schema29",
    )
    tables = table_receipts(conn)
    require(
        len(tables) == 117 and tables.get("history_dispatch_fence", {}).get("rows") == 1,
        "schema29 table closure differs",
    )
    require(
        [list(row) for row in conn.execute("PRAGMA integrity_check")] == [["ok"]],
        "integrity check failed",
    )
    require(conn.execute("PRAGMA foreign_key_check").fetchone() is None, "foreign key check failed")
    require(
        conn.execute("SELECT 1 FROM execution_admissions WHERE state<>'settled' LIMIT 1").fetchone()
        is None,
        "unsettled execution admission exists",
    )
    bundle = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    require(
        bundle is not None and str(bundle[0]) == expected_bundle, "input bundle authority differs"
    )
    judges = query_receipt(
        conn,
        "SELECT judge_id,name_raw,wsdc_id FROM judges WHERE name_raw IS NOT NULL AND name_raw<>'' ORDER BY judge_id",
    )
    null_ids = conn.execute(
        "SELECT count(*) FROM judges WHERE name_raw IS NOT NULL AND name_raw<>'' AND wsdc_id IS NULL"
    ).fetchone()[0]
    require(judges["rows"] == 4931 and null_ids == 4931, "named judge continuity differs")
    return {"tables": tables, "judges": judges, "accepted": accepted_map(conn)}


def validate_old_inputs(snapshot: dict[str, Any]) -> None:
    accepted = cast(dict[str, str], snapshot["accepted"])
    require(len(accepted) == 51, "accepted input row count differs")
    require(
        all(accepted.get(name) == old for name, (old, _new) in EXPECTED_CHANGES.items()),
        "candidate006 input preimage differs",
    )


def validate_new_inputs(before: dict[str, Any], after: dict[str, Any]) -> None:
    validate_old_inputs(before)
    expected = dict(cast(dict[str, str], before["accepted"]))
    expected.update({name: new for name, (_old, new) in EXPECTED_CHANGES.items()})
    require(
        cast(dict[str, str], after["accepted"]) == expected,
        "candidate006 input result or retained input differs",
    )


def changed_tables(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    require(set(before) == set(after), "input acceptance changed table closure")
    return sorted(name for name in before if before[name] != after[name])


def validate_transition(before: dict[str, Any], after: dict[str, Any], changed: set[str]) -> None:
    require(
        bool(changed) and changed <= ALLOWED_CHANGED_TABLES, "acceptance changed protected tables"
    )
    require(
        "accepted_inputs" in changed and "meta" in changed, "acceptance authority did not change"
    )
    for name in set(before) - changed:
        require(before[name] == after[name], f"protected table changed: {name}")


def input_inventory(path: Path) -> dict[str, dict[str, Any]]:
    require(path.is_dir() and not path.is_symlink(), "captured bundle directory differs")
    result: dict[str, dict[str, Any]] = {}
    for item in sorted(path.rglob("*")):
        require(not item.is_symlink(), "captured bundle contains symlink")
        if item.is_file():
            require(item.stat().st_nlink == 1, "captured bundle file is hard linked")
            result[item.relative_to(path).as_posix()] = {
                "size": item.stat().st_size,
                "sha256": sha(item),
            }
        else:
            require(item.is_dir(), "captured bundle contains special file")
    require("manifest.json" in result, "captured bundle manifest missing")
    return result


def remove_exact_new_bundle(path: Path, state: Path, expected: dict[str, dict[str, Any]]) -> None:
    require(
        path == state / "inputs" / TARGET_BUNDLE
        and path.is_dir()
        and not path.is_symlink()
        and input_inventory(path) == expected,
        "refusing to remove a nonexact acceptance bundle",
    )
    items = sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    for item in items:
        if item.is_file():
            item.unlink()
        else:
            item.rmdir()
    path.rmdir()


def pending_publications(state: Path) -> list[str]:
    candidates = state / "candidates"
    baseline = (state / "baseline").resolve(strict=True)
    found: list[str] = []
    for path in candidates.iterdir():
        if path == baseline or not (path / "PUBLISHING").is_file():
            continue
        receipt = path / "PUBLISHED"
        if receipt.is_file():
            built = read_json(path / "BUILT")
            if built.get("expected_parent") != BASELINE_COMMIT:
                continue
        found.append(path.name)
    return sorted(found)


def environment() -> dict[str, Any]:
    systems = {
        name: str(Path(name).resolve(strict=True))
        for name in ("/run/current-system", "/nix/var/nix/profiles/system")
    }
    require(set(systems.values()) == {str(SYSTEM)}, "active/persistent candidate006 system differs")
    units = {
        unit: subprocess.check_output(
            [str(SYSTEMCTL), "show", unit, "--property=ActiveState", "--value"], text=True
        ).strip()
        for unit in UNITS
    }
    require(all(value == "inactive" for value in units.values()), "ordinary unit is active")
    require(not (STATE / "RESTORE_PENDING").exists(), "restore remains pending")
    actual_names = {
        path.name
        for path in STATE.iterdir()
        if path.is_file() and path.name not in RUNTIME_TRANSIENTS and path.name != "state.sqlite"
    }
    require(actual_names == set(SIDECARS), "held sidecar set differs")
    sidecars: dict[str, str] = {}
    for name, (size, digest) in SIDECARS.items():
        path = STATE / name
        require(
            not path.is_symlink() and path.stat().st_size == size and sha(path) == digest,
            f"held sidecar differs: {name}",
        )
        sidecars[name] = digest
    baseline = STATE / "baseline"
    require(
        baseline.is_symlink()
        and baseline.resolve(strict=True) == STATE / "candidates" / BASELINE_CANDIDATE,
        "baseline symlink differs",
    )
    published = baseline.resolve() / "PUBLISHED"
    doc = read_json(published)
    require(
        sha(published) == BASELINE_RECEIPT_SHA256
        and doc.get("candidate_id") == BASELINE_CANDIDATE
        and doc.get("commit") == BASELINE_COMMIT,
        "publication baseline differs",
    )
    require(not pending_publications(STATE), "pending publication requires reconciliation")
    return {
        "systems": systems,
        "units": units,
        "sidecars": sidecars,
        "baseline_receipt_sha256": sha(published),
    }


def verify_database_path() -> Path:
    path = STATE / "state.sqlite"
    metadata = path.lstat()
    require(
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
        "live database is linked or not regular",
    )
    return path


def connect(*, readonly: bool) -> sqlite3.Connection:
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(
        f"file:{verify_database_path()}?mode={mode}", uri=True, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
    return conn


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """Keep review checks in the same transaction as the nested runtime accept."""
    require(not conn.in_transaction, "acceptance connection already has a transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


@contextmanager
def disk_shadow(source: sqlite3.Connection) -> Iterator[tuple[sqlite3.Connection, Path]]:
    """Back up SQLite to one private /var/tmp tree and always remove that tree."""
    root = SHADOW_ROOT
    require(root == Path("/var/tmp") and root.is_absolute(), "shadow root must be /var/tmp")
    require(root.is_dir() and not root.is_symlink(), "shadow root is missing or linked")
    expected_bytes = int(source.execute("PRAGMA page_count").fetchone()[0]) * int(
        source.execute("PRAGMA page_size").fetchone()[0]
    )
    require(
        shutil.disk_usage(root).free >= expected_bytes + 64 * 1024 * 1024,
        "insufficient /var/tmp space for disk-backed seal",
    )
    directory = Path(tempfile.mkdtemp(prefix="swingset-input-seal-", dir=str(root)))
    connection: sqlite3.Connection | None = None
    try:
        metadata = directory.lstat()
        require(
            directory.parent == root
            and stat.S_ISDIR(metadata.st_mode)
            and not directory.is_symlink(),
            "shadow directory escaped /var/tmp or is linked",
        )
        os.chmod(directory, 0o700)
        database = directory / "shadow.sqlite"
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(database, flags, 0o600)
        os.close(descriptor)
        os.chmod(database, 0o600)
        db_metadata = database.lstat()
        require(
            stat.S_ISREG(db_metadata.st_mode)
            and db_metadata.st_nlink == 1
            and stat.S_IMODE(db_metadata.st_mode) == 0o600
            and not database.is_symlink(),
            "shadow database is linked, shared, or has unsafe permissions",
        )
        connection = sqlite3.connect(database, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA foreign_keys=ON")
        source.backup(connection)
        db_metadata = database.lstat()
        require(
            stat.S_ISREG(db_metadata.st_mode)
            and db_metadata.st_nlink == 1
            and stat.S_IMODE(db_metadata.st_mode) == 0o600
            and not database.is_symlink(),
            "backed-up shadow database path changed",
        )
        yield connection, directory
    finally:
        try:
            if connection is not None:
                connection.close()
        finally:
            shutil.rmtree(directory)
            require(not directory.exists() and not directory.is_symlink(), "shadow cleanup failed")


@contextmanager
def locks() -> Iterator[None]:
    with ExitStack() as stack:
        for name in ("state.lock", "control.lock"):
            path = STATE / name
            require(not path.is_symlink() and path.is_file(), f"lock file differs: {name}")
            handle = stack.enter_context(path.open("r+b"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def runtime_bound() -> None:
    root = SOURCE / "src"
    for name, module in tuple(sys.modules.items()):
        if name == "swingset" or name.startswith("swingset."):
            path = getattr(module, "__file__", None)
            require(
                path is not None and Path(path).resolve().is_relative_to(root),
                f"runtime escaped candidate source: {name}",
            )


def load_runtime() -> Runtime:
    runtime_bound()
    root = str(SOURCE / "src")
    require(root not in sys.path, "candidate runtime path already injected")
    sys.path.insert(0, root)
    db_module = importlib.import_module("swingset.state.db")
    input_module = importlib.import_module("swingset.state.inputs")
    cycle_module = importlib.import_module("swingset.schedule.cycle")
    runtime_bound()
    require(db_module.SCHEMA_VERSION == 29, "candidate runtime schema differs")
    return Runtime(
        db_module.Database, input_module.capture, input_module.accept, cycle_module.versions
    )


def validate_phase_chain(gate: dict[str, Any], gate_path: Path, phase: str) -> None:
    if phase == "preflight":
        require(
            "preflight" not in gate and "seal" not in gate, "preflight gate carries later authority"
        )
        return
    preflight_path = reference(gate_path.parent, gate.get("preflight"), "preflight receipt")
    preflight = read_json(preflight_path)
    closed(preflight, RECEIPT_FORMAT, "preflight receipt")
    require(
        preflight.get("phase") == "preflight"
        and preflight.get("executed") is False
        and preflight.get("gate_sha256") == gate.get("preflight_gate_sha256")
        and preflight.get("helper_sha256") == gate.get("helper_sha256")
        and preflight.get("source") == str(SOURCE)
        and preflight.get("system") == str(SYSTEM)
        and preflight.get("old_bundle") == OLD_BUNDLE
        and preflight.get("target_bundle") == TARGET_BUNDLE,
        "preflight receipt scope differs",
    )
    if phase == "seal":
        require("seal" not in gate, "seal gate carries execution authority")


def validate_gate(
    gate: dict[str, Any], gate_path: Path, gate_sha256: str, helper_sha256: str, phase: str
) -> tuple[Path, dict[str, Path], datetime]:
    require(phase in PHASES and gate.get("phase") == phase, "gate phase differs")
    require(
        gate.get("format") == GATE_FORMAT
        and gate.get("state") == str(STATE)
        and gate.get("source") == str(SOURCE)
        and gate.get("system") == str(SYSTEM)
        and gate.get("config") == str(CONFIG)
        and gate.get("overrides") == str(OVERRIDES)
        and gate.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and gate.get("old_bundle") == OLD_BUNDLE
        and gate.get("target_bundle") == TARGET_BUNDLE
        and gate.get("external_files") == EXTERNAL_FILES
        and gate.get("helper_sha256") == helper_sha256
        and sha(Path(__file__).resolve()) == helper_sha256
        and sha(gate_path) == gate_sha256,
        "gate scope differs",
    )
    source_receipt = reference(gate_path.parent, gate.get("source_receipt"), "source receipt")
    require(source_receipt == SOURCE / "extension-source.json", "source receipt location differs")
    evidence = validate_evidence(gate, gate_path)
    validate_phase_chain(gate, gate_path, phase)
    try:
        accepted_at = datetime.fromisoformat(
            str(gate.get("accepted_at", "")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("gate acceptance time is malformed") from error
    require(
        accepted_at.tzinfo is not None and accepted_at.utcoffset() == UTC.utcoffset(accepted_at),
        "acceptance time must be UTC",
    )
    if phase == "execution":
        require(isinstance(gate.get("seal"), dict), "execution gate lacks reviewed seal")
    return source_receipt, evidence, accepted_at


def revalidate_boundary(
    gate: dict[str, Any],
    gate_path: Path,
    gate_sha256: str,
    helper_sha256: str,
    source_receipt: Path,
    evidence: dict[str, Path],
) -> dict[str, Any]:
    require(
        sha(gate_path) == gate_sha256 and sha(Path(__file__).resolve()) == helper_sha256,
        "gate/helper changed",
    )
    verify_source(source_receipt)
    require(
        all(sha(path) == EVIDENCE_SHA256[name] for name, path in evidence.items()),
        "evidence changed",
    )
    external_files()
    return environment()


def apply_acceptance(
    conn: sqlite3.Connection, runtime: Runtime, state: Path, accepted_at: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    database = runtime.Database(state, conn, None)
    bundle = runtime.capture(CONFIG, OVERRIDES, state, runtime.versions())
    require(
        bundle.digest == TARGET_BUNDLE and bundle.path == state / "inputs" / TARGET_BUNDLE,
        "captured bundle differs",
    )
    inventory = input_inventory(bundle.path)
    with atomic(conn):
        changed = runtime.accept(database, bundle, FixedClock(accepted_at))
        require(changed == set(EXPECTED_CHANGES), "runtime accepted a different input set")
        after = validate_database(conn, expected_bundle=TARGET_BUNDLE)
    return after, {"digest": bundle.digest, "files": inventory}


def execute_sealed_acceptance(
    conn: sqlite3.Connection,
    runtime: Runtime,
    state: Path,
    accepted_at: datetime,
    seal: dict[str, Any],
    verify_held: Callable[[], None],
) -> tuple[dict[str, Any], Any, set[str], set[str]]:
    target = state / "inputs" / TARGET_BUNDLE
    existed = target.exists()
    expected_inventory = cast(dict[str, dict[str, Any]], seal["bundle"]["files"])
    if existed:
        require(input_inventory(target) == expected_inventory, "existing target bundle differs")
    database = runtime.Database(state, conn, None)
    try:
        bundle = runtime.capture(CONFIG, OVERRIDES, state, runtime.versions())
        require(
            bundle.digest == TARGET_BUNDLE and bundle.path == target,
            "captured production bundle differs",
        )
        require(
            input_inventory(bundle.path) == expected_inventory,
            "captured production bundle inventory differs",
        )
        with atomic(conn):
            changed_inputs = runtime.accept(database, bundle, FixedClock(accepted_at))
            require(
                changed_inputs == set(EXPECTED_CHANGES),
                "production accepted input set differs",
            )
            after = validate_database(conn, expected_bundle=TARGET_BUNDLE)
            validate_new_inputs(seal["before"], after)
            require(after == seal["after"], "production transition differs from reviewed seal")
            changed = set(changed_tables(seal["before"]["tables"], after["tables"]))
            validate_transition(seal["before"]["tables"], after["tables"], changed)
            verify_held()
        return after, bundle, changed_inputs, changed
    except BaseException:
        if not existed and target.exists():
            restored = validate_database(conn, expected_bundle=OLD_BUNDLE)
            require(restored == seal["before"], "failed acceptance did not restore sealed prestate")
            remove_exact_new_bundle(target, state, expected_inventory)
        raise


def recover_completed_acceptance(
    conn: sqlite3.Connection, state: Path, seal: dict[str, Any]
) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    require(row is not None and str(row[0]) == TARGET_BUNDLE, "acceptance is not complete")
    target = state / "inputs" / TARGET_BUNDLE
    require(target.exists(), "accepted target bundle is missing")
    require(
        input_inventory(target) == seal["bundle"]["files"],
        "accepted target bundle inventory differs",
    )
    after = validate_database(conn, expected_bundle=TARGET_BUNDLE)
    validate_new_inputs(seal["before"], after)
    require(after == seal["after"], "completed acceptance differs from reviewed seal")
    return after


def make_seal(
    conn: sqlite3.Connection, runtime: Runtime, accepted_at: datetime, common: dict[str, Any]
) -> dict[str, Any]:
    before = validate_database(conn, expected_bundle=OLD_BUNDLE)
    validate_old_inputs(before)
    with disk_shadow(conn) as (shadow, directory):
        scratch = directory / "state"
        scratch.mkdir(mode=0o700)
        after, bundle = apply_acceptance(shadow, runtime, scratch, accepted_at)
        validate_new_inputs(before, after)
        changed = set(changed_tables(before["tables"], after["tables"]))
        validate_transition(before["tables"], after["tables"], changed)
        require(before["judges"] == after["judges"], "acceptance changed named judges")
        return {
            "format": SEAL_FORMAT,
            "passed": True,
            **common,
            "accepted_at": accepted_at.isoformat(),
            "accepted_inputs": sorted(EXPECTED_CHANGES),
            "changed_tables": sorted(changed),
            "before": before,
            "after": after,
            "bundle": bundle,
            "network_requests": 0,
            "workers_started": False,
            "repairs_activated": False,
            "publication": False,
            "hold_retained": True,
            "finished_at": datetime.now(UTC).isoformat(),
        }


def validate_seal(
    gate: dict[str, Any], gate_path: Path, gate_sha256: str, helper_sha256: str
) -> tuple[dict[str, Any], str]:
    spec = cast(dict[str, Any], gate["seal"])
    seal_path = reference(gate_path.parent, spec, "acceptance seal")
    seal_sha = str(spec["sha256"])
    seal = read_json(seal_path)
    closed(seal, SEAL_FORMAT, "acceptance seal")
    require(
        seal.get("gate_sha256") == gate.get("seal_gate_sha256")
        and seal.get("preflight_sha256") == gate.get("preflight", {}).get("sha256")
        and seal.get("helper_sha256") == helper_sha256
        and seal.get("source") == str(SOURCE)
        and seal.get("system") == str(SYSTEM)
        and seal.get("old_bundle") == OLD_BUNDLE
        and seal.get("target_bundle") == TARGET_BUNDLE
        and seal.get("accepted_at") == gate.get("accepted_at")
        and seal.get("accepted_inputs") == sorted(EXPECTED_CHANGES),
        "reviewed seal scope differs",
    )
    require(seal.get("gate_sha256") != gate_sha256, "execution cannot reuse its gate as seal gate")
    changed = set(seal.get("changed_tables", []))
    validate_transition(seal["before"]["tables"], seal["after"]["tables"], changed)
    validate_old_inputs(seal["before"])
    validate_new_inputs(seal["before"], seal["after"])
    require(seal["before"]["judges"] == seal["after"]["judges"], "sealed judge continuity differs")
    preflight_path = reference(gate_path.parent, gate["preflight"], "preflight receipt")
    require(
        seal["before"] == read_json(preflight_path).get("before"),
        "sealed prestate differs from reviewed preflight",
    )
    return seal, seal_sha


def run(gate_path: Path, *, gate_sha256: str, helper_sha256: str, phase: str) -> dict[str, Any]:
    require(
        STATE == Path("/var/lib/swingset") and str(STATE) == "/var/lib/swingset",
        "production state path differs",
    )
    gate = read_json(gate_path)
    source_receipt, evidence, accepted_at = validate_gate(
        gate, gate_path, gate_sha256, helper_sha256, phase
    )
    common = {
        "gate_sha256": gate_sha256,
        "helper_sha256": helper_sha256,
        "source": str(SOURCE),
        "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
        "system": str(SYSTEM),
        "old_bundle": OLD_BUNDLE,
        "target_bundle": TARGET_BUNDLE,
    }
    if phase != "preflight":
        common["preflight_sha256"] = gate["preflight"]["sha256"]
    with locks():
        guards = revalidate_boundary(
            gate, gate_path, gate_sha256, helper_sha256, source_receipt, evidence
        )
        target = STATE / "inputs" / TARGET_BUNDLE
        with closing(connect(readonly=phase != "execution")) as conn:
            if phase == "preflight":
                require(not target.exists(), "target input bundle already exists")
                before = validate_database(conn, expected_bundle=OLD_BUNDLE)
                validate_old_inputs(before)
                return {
                    "format": RECEIPT_FORMAT,
                    "phase": phase,
                    "passed": True,
                    "executed": False,
                    **common,
                    "before": before,
                    "guards": guards,
                    "accepted_inputs": [],
                    "network_requests": 0,
                    "workers_started": False,
                    "repairs_activated": False,
                    "publication": False,
                    "hold_retained": True,
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            runtime = load_runtime()
            if phase == "seal":
                require(not target.exists(), "target input bundle already exists")
                before = validate_database(conn, expected_bundle=OLD_BUNDLE)
                validate_old_inputs(before)
                preflight_path = reference(gate_path.parent, gate["preflight"], "preflight receipt")
                require(
                    before == read_json(preflight_path).get("before"),
                    "live prestate differs from reviewed preflight",
                )
                return make_seal(conn, runtime, accepted_at, common)
            seal, seal_sha = validate_seal(gate, gate_path, gate_sha256, helper_sha256)
            bundle_row = conn.execute(
                "SELECT value FROM meta WHERE key='input_bundle_hash'"
            ).fetchone()
            current_bundle = None if bundle_row is None else str(bundle_row[0])
            if current_bundle == TARGET_BUNDLE:
                after = recover_completed_acceptance(conn, STATE, seal)
                require(environment() == guards, "held environment changed during recovery")
                return {
                    "format": RECEIPT_FORMAT,
                    "phase": phase,
                    "passed": True,
                    "executed": True,
                    "recovery_only": True,
                    "accept_invoked": False,
                    **common,
                    "seal_sha256": seal_sha,
                    "accepted_at": accepted_at.isoformat(),
                    "accepted_inputs": sorted(EXPECTED_CHANGES),
                    "changed_tables": seal["changed_tables"],
                    "before": seal["before"],
                    "after": after,
                    "bundle": seal["bundle"],
                    "guards": guards,
                    "network_requests": 0,
                    "workers_started": False,
                    "repairs_activated": False,
                    "publication": False,
                    "hold_retained": True,
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            require(current_bundle == OLD_BUNDLE, "live input authority is neither sealed state")
            before = validate_database(conn, expected_bundle=OLD_BUNDLE)
            validate_old_inputs(before)
            require(before == seal["before"], "locked live prestate differs from reviewed seal")
            guards2 = revalidate_boundary(
                gate, gate_path, gate_sha256, helper_sha256, source_receipt, evidence
            )
            require(guards2 == guards, "held environment changed at acceptance boundary")
            after, bundle, changed_inputs, changed = execute_sealed_acceptance(
                conn,
                runtime,
                STATE,
                accepted_at,
                seal,
                lambda: require(environment() == guards, "held environment changed before commit"),
            )
            require(environment() == guards, "held environment changed after commit")
            return {
                "format": RECEIPT_FORMAT,
                "phase": phase,
                "passed": True,
                "executed": True,
                "recovery_only": False,
                "accept_invoked": True,
                **common,
                "seal_sha256": seal_sha,
                "accepted_at": accepted_at.isoformat(),
                "accepted_inputs": sorted(changed_inputs),
                "changed_tables": sorted(changed),
                "before": before,
                "after": after,
                "bundle": {"digest": bundle.digest, "files": input_inventory(bundle.path)},
                "guards": guards,
                "network_requests": 0,
                "workers_started": False,
                "repairs_activated": False,
                "publication": False,
                "hold_retained": True,
                "finished_at": datetime.now(UTC).isoformat(),
            }


def output_path(path: Path) -> Path:
    resolved = path.resolve()
    require(
        not path.exists()
        and not path.is_symlink()
        and not any(
            resolved == root or resolved.is_relative_to(root)
            for root in (STATE, SOURCE, CONFIG, OVERRIDES)
        ),
        "output must be new and outside state/input roots",
    )
    return resolved


def offline_audit(event: str, values: tuple[Any, ...]) -> None:
    if event in {
        "socket.connect",
        "socket.bind",
        "socket.getaddrinfo",
        "socket.sendmsg",
        "socket.sendto",
        "os.system",
        "os.fork",
        "os.posix_spawn",
        "os.posix_spawnp",
    } or event.startswith("ftplib."):
        raise RuntimeError(f"input acceptance forbids operation: {event}")
    if event == "subprocess.Popen":
        executable = values[0] if values else None
        command = values[1] if len(values) > 1 else None
        allowed = {
            (str(SYSTEMCTL), "show", unit, "--property=ActiveState", "--value") for unit in UNITS
        }
        require(
            executable == str(SYSTEMCTL)
            and isinstance(command, (list, tuple))
            and tuple(command) in allowed,
            "input acceptance forbids subprocess",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--gate-sha256", required=True)
    parser.add_argument("--helper-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=int, default=1800)
    args = parser.parse_args()
    require(1 <= args.max_seconds <= 3600, "execution bound is invalid")
    os.umask(0o077)
    sys.dont_write_bytecode = True
    gate = args.gate.resolve(strict=True)
    output = output_path(args.output)
    signal.signal(
        signal.SIGALRM,
        lambda _signal, _frame: (_ for _ in ()).throw(
            TimeoutError("input acceptance deadline exceeded")
        ),
    )
    signal.alarm(args.max_seconds)
    sys.addaudithook(offline_audit)
    try:
        result = run(
            gate,
            gate_sha256=args.gate_sha256,
            helper_sha256=args.helper_sha256,
            phase=args.phase,
        )
        write_new(output, result)
    finally:
        signal.alarm(0)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
