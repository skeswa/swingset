"""Seal and prepare the original finite phase-one intake for a reviewed extension schema.

Build writes a new packet only. Prepare reads held state only. Run defaults to
preflight; --execute retains ordinary FetchClient policies and the original
17-target driver. It never migrates, accepts inputs, activates repairs or years.
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

BASE_SHA = "1ddbe1ebae293dc7f97ec1de50a809c93588066d4f26ddb5afa9a59359f93752"
SCOPE_SHA = "c575347878de8ea5181919e26e8607d5d4aa31bfc08c899b82c4c0c25c3ecd98"
BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
UNITS = tuple(f"swingset-{kind}.{suffix}" for kind in ("cycle", "backup", "summary") for suffix in ("service", "timer"))


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write_new(path: Path, value: Any) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def verify_source(source: Path, receipt_name: str, receipt_sha: str, expected_schema: int) -> None:
    require(not Path(receipt_name).is_absolute() and ".." not in Path(receipt_name).parts, "invalid source receipt")
    receipt = source / receipt_name
    require(sha(receipt) == receipt_sha, "frozen source receipt differs")
    files = json.loads(receipt.read_bytes())["files"]
    children = list(source.rglob("*"))
    require(bool(files) and not any(p.is_symlink() for p in children), "linked or empty source inventory")
    actual = {p.relative_to(source).as_posix() for p in children if p.is_file() and "__pycache__" not in p.parts and p != receipt}
    require(actual == set(files), "source inventory is not closed")
    for name, expected in files.items():
        path = source / name
        require(not Path(name).is_absolute() and ".." not in Path(name).parts and path.resolve().is_relative_to(source.resolve()), "source path escapes inventory")
        require(sha(path) == (expected["sha256"] if isinstance(expected, dict) else expected), "frozen source bytes differ: " + name)
    assignments = ast.parse((source / "src/swingset/state/db.py").read_text()).body
    schemas = [ast.literal_eval(node.value) for node in assignments if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SCHEMA_VERSION" for t in node.targets)]
    require(type(expected_schema) is int and expected_schema >= 28 and schemas == [expected_schema], "frozen extension schema differs")


def build(source: Path, deployed_source: str, receipt_name: str, receipt_sha: str, output: Path, *, schema_version: int) -> dict[str, Any]:
    source = source.resolve(strict=True)
    require(bool(re.fullmatch(r"/nix/store/[a-z0-9]{32}-source", deployed_source)), "deployed source must be an immutable Nix source path")
    require(not output.exists() and not output.is_symlink(), "packet must be new")
    verify_source(source, receipt_name, receipt_sha, schema_version)
    root = Path(__file__).resolve().parents[3]
    retained = root / "journal/evidence/collection/phase1-resume-2026-09-17"
    require(sha(retained / "resume.py") == BASE_SHA and sha(retained / "gate-001.json") == SCOPE_SHA, "original driver or17-target scope differs")
    files = {"runner.py":Path(__file__).read_bytes(), "base.py":(retained / "resume.py").read_bytes(), "original-scope.json":(retained / "gate-001.json").read_bytes()}
    packet = {"format":"extension-phase1-packet-v1", "source":deployed_source, "source_receipt":receipt_name, "source_receipt_sha256":receipt_sha, "schema":schema_version, "files":{name:hashlib.sha256(body).hexdigest() for name,body in files.items()}, "original_driver_sha256":BASE_SHA, "original_scope_sha256":SCOPE_SHA, "published_commit":BASELINE}
    output.mkdir(parents=True)
    for name, body in files.items():
        with (output / name).open("xb") as handle:
            handle.write(body)
    write_new(output / "packet.json", packet)
    verify_source(source, receipt_name, receipt_sha, schema_version)
    return {"packet":str(output), "packet_sha256":sha(output / "packet.json"), "network_requests":0, "production_operations":0, "executed":False}


def verify_packet(packet_path: Path, expected: str) -> dict[str, Any]:
    require(sha(packet_path) == expected, "packet hash differs")
    packet = json.loads(packet_path.read_bytes())
    require(packet.get("format") == "extension-phase1-packet-v1" and type(packet.get("schema")) is int and packet["schema"] >= 28, "unknown extension packet")
    require(packet["original_driver_sha256"] == BASE_SHA and packet["original_scope_sha256"] == SCOPE_SHA and packet["published_commit"] == BASELINE, "original authority differs")
    require(set(packet["files"]) == {"runner.py", "base.py", "original-scope.json"}, "helper closure differs")
    root = packet_path.parent.resolve(strict=True)
    require(packet_path.name == "packet.json" and {p.name for p in root.iterdir()} == {*packet["files"], "packet.json"}, "unexpected helper closure file")
    for name, expected_sha in packet["files"].items():
        path = root / name
        require(not path.is_symlink() and path.is_file() and sha(path) == expected_sha, "helper closure bytes differ: " + name)
    require(sha(root / "base.py") == BASE_SHA and sha(root / "original-scope.json") == SCOPE_SHA, "original dependency differs")
    require(sha(Path(__file__)) == packet["files"]["runner.py"], "executed wrapper differs")
    return cast(dict[str, Any], packet)


def load_base(packet_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("extension_phase1_base", packet_path.parent / "base.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def runtime(packet: dict[str, Any]) -> Path:
    source = Path(packet["source"]).resolve(strict=True)
    require(str(source) == packet["source"] and bool(re.fullmatch(r"/nix/store/[a-z0-9]{32}-source", str(source))), "immutable runtime source required")
    verify_source(source, packet["source_receipt"], packet["source_receipt_sha256"], packet["schema"])
    require(not os.environ.get("SWINGSET_REVISION"), "repository identity override is not reviewed")
    from swingset.schedule import cycle
    from swingset.state import db, inputs
    require(db.SCHEMA_VERSION == packet["schema"], "imported schema differs")
    for module in (cycle, db, inputs):
        require(Path(str(module.__file__)).resolve().is_relative_to(source / "src"), "mixed imported runtime")
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name.startswith("swingset.") and path:
            require(Path(path).resolve().is_relative_to(source / "src"), "mixed imported runtime module: " + name)
    return source


def external_inputs(source: Path, config: Path, overrides: Path) -> dict[str, str]:
    require(config.is_dir() and overrides.is_dir(), "external input directories missing")
    expected = {"config/" + name:sha(source / "config" / name) for name in ("hosts.toml", "sources.toml")}
    expected.update({"overrides/" + p.name:sha(p) for p in (source / "overrides").glob("*.csv")})
    actual = {"config/" + name:sha(config / name) for name in ("hosts.toml", "sources.toml")}
    actual.update({"overrides/" + p.name:sha(p) for p in overrides.glob("*.csv")})
    require(actual == expected, "external inputs differ from frozen reviewed inventory")
    return actual


def expected_bundle(source: Path, config: Path, overrides: Path, now: datetime) -> dict[str, Any]:
    from swingset.fetch.archive import digest
    from swingset.schedule.cycle import versions
    from swingset.state.inputs import capture
    from swingset.state.recipes import captured_recipe_inputs
    files = external_inputs(source, config, overrides)
    with tempfile.TemporaryDirectory(prefix="swingset-phase1-input-check-") as directory:
        bundle = capture(config, overrides, Path(directory), versions())
        require(bundle.config.enabled("wsdc_calendar"), "configured calendar source is disabled")
        require(bundle.config.history_start.isoformat() == "2010-01-01", "history floor differs")
        accepted = {name:value for name,value in bundle.file_hashes.items() if not name.startswith(("runtime/", "recipes/"))}
        accepted.update(captured_recipe_inputs(bundle.files, history_start=bundle.config.history_start.isoformat()))
        accepted["policy/inventory_year"] = digest(canonical(now.year))
        del accepted["versions.json"]
        accepted.update({"version/" + name:value for name,value in json.loads(bundle.files["versions.json"]).items()})
        result = {"digest":bundle.digest, "files":bundle.file_hashes, "accepted":accepted, "external_files":files}
    external_inputs(source, config, overrides)
    return result


def check_accepted(conn: sqlite3.Connection, state: Path, expected: dict[str, Any], *, verify_bytes: bool) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    require(row is not None and row[0] == expected["digest"], "frozen runtime/input bundle is not accepted")
    actual = dict(conn.execute("SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline'"))
    required = expected["accepted"]
    require(all(actual.get(name) == digest for name,digest in required.items()), "accepted semantic input differs")
    empty = hashlib.sha256(b"").hexdigest()
    require(all(name in required or (name.startswith("overrides/") and value == empty) for name,value in actual.items()), "unreviewed accepted input remains")
    if verify_bytes:
        bundle = state / "inputs" / expected["digest"]
        require(json.loads((bundle / "manifest.json").read_bytes()) == expected["files"], "retained accepted bundle manifest differs")
        for name,digest in expected["files"].items():
            require(sha(bundle / name) == digest, "retained accepted input bytes differ: " + name)


def schema(conn: sqlite3.Connection, expected_schema: int) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    require(row is not None and row[0] == str(expected_schema) and conn.execute("PRAGMA user_version").fetchone()[0] == expected_schema, "both live schema markers must match reviewed runtime; no migration")


def operating_guards(state: Path, hold_sha: str | None = None, *, units: bool = False) -> str:
    hold = state / "operator-hold"
    require(hold.is_file() and not hold.is_symlink() and not (state / "RESTORE_PENDING").exists(), "hold or restore interlock differs")
    actual = sha(hold)
    require(hold_sha is None or hold_sha == actual, "operator hold bytes changed")
    if units:
        result = subprocess.run(["systemctl", "show", "--property=ActiveState", "--value", *UNITS], check=True, text=True, capture_output=True)
        require([line.strip() for line in result.stdout.splitlines() if line.strip()] == ["inactive"] * len(UNITS), "ordinary units must all be inactive")
    return actual


def check_scope(base: Any, conn: sqlite3.Connection, state: Path, gate: dict[str, Any], packet_path: Path) -> Any:
    from swingset.history.catalog import load_catalog
    from swingset.sources import get_page_kind
    original = json.loads((packet_path.parent / "original-scope.json").read_bytes())
    require(gate["original_targets"] == original["original_targets"], "original17 identities expanded or changed")
    return base.check_targets(conn, state, gate, load_catalog, get_page_kind)


def assemble(base: Any, conn: sqlite3.Connection, state: Path, source: Path, packet_path: Path, packet_sha: str, packet: dict[str, Any], expected: dict[str, Any], config: Path, overrides: Path, now: datetime) -> dict[str, Any]:
    from swingset.history.catalog import load_catalog
    schema(conn, packet["schema"])
    hold_sha = operating_guards(state, units=True)
    require(conn.execute("SELECT 1 FROM execution_admissions WHERE state='admitted' LIMIT 1").fetchone() is None, "unsettled request or control admissions")
    check_accepted(conn, state, expected, verify_bytes=True)
    ledger_path = state / "phase1-ledger.json"
    ledger = json.loads(ledger_path.read_bytes())["targets"]
    targets = load_catalog(state / "phase1-catalog.json")
    original = json.loads((packet_path.parent / "original-scope.json").read_bytes())
    baseline = (state / "baseline").resolve(strict=True)
    require(json.loads((baseline / "PUBLISHED").read_bytes())["commit"] == BASELINE, "public baseline changed")
    gate = {"format":"phase1-17-resume-v1", "extension_format":"extension-phase1-gate-v1", "coordinator_reviewed":True, "state":str(state), "source":str(source), "source_receipt":packet["source_receipt"], "source_receipt_sha256":packet["source_receipt_sha256"], "driver_sha256":BASE_SHA, "schema":packet["schema"], "inputs":json.loads(json.dumps(base.inputs(conn))), "baseline_path":str(baseline), "published_sha256":sha(baseline / "PUBLISHED"), "manifest_sha256":sha(baseline / "_meta/manifest.json"), "catalog_sha256":sha(state / "phase1-catalog.json"), "ledger_sha256":sha(ledger_path), "utc_day":now.date().isoformat(), "wall_seconds":600, "max_additional_requests":48, "remaining_target_ids":sorted(t.target_id for t in targets if ledger.get(t.target_id,{}).get('status','pending')=='pending'), "original_targets":original["original_targets"], "target_statuses":{key:ledger.get(key,{}).get('status','pending') for key in original["original_targets"]}, "packet_sha256":packet_sha, "operator_hold_sha256":hold_sha, "config":str(config), "overrides":str(overrides), "expected_bundle":expected}
    base.check_authority(conn, state, gate)
    check_scope(base, conn, state, gate, packet_path)
    require(conn.total_changes == 0, "preparation mutated state")
    return gate


def prepare(packet_path: Path, packet_sha: str, state: Path, config: Path, overrides: Path, output: Path) -> dict[str, Any]:
    packet = verify_packet(packet_path, packet_sha)
    source = runtime(packet)
    state, config, overrides = state.resolve(strict=True), config.resolve(strict=True), overrides.resolve(strict=True)
    require(state == Path("/var/lib/swingset"), "live coordinator state required")
    require(not output.resolve().is_relative_to(packet_path.parent.resolve()), "gate must be outside immutable helper packet")
    require(output.parent.resolve() == output.parent and output.is_relative_to(state / "operations"), "gate must be in the real operations directory")
    now = datetime.now(UTC)
    expected = expected_bundle(source, config, overrides, now)
    base = load_base(packet_path)
    operating_guards(state, units=True)
    from swingset.state.control_lock import control_lock
    with (state / "state.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with control_lock(state):
            operating_guards(state, units=True)
            conn = sqlite3.connect(f"file:{state}/state.sqlite?mode=ro", uri=True, isolation_level=None)
            try:
                gate = assemble(base, conn, state, source, packet_path, packet_sha, packet, expected, config, overrides, now)
                external_inputs(source, config, overrides)
                operating_guards(state, gate["operator_hold_sha256"], units=True)
                write_new(output, gate)
            finally:
                conn.close()
    return {"gate":str(output), "gate_sha256":sha(output), "remaining":len(gate["remaining_target_ids"]), "database_changes":0, "requests":0, "accepted_inputs":False}


def guard_base(base: Any, source: Path, state: Path, gate: dict[str, Any], expected: dict[str, Any], config: Path, overrides: Path, packet_path: Path) -> None:
    """Add checks at the original locked preflight and each ordinary debit boundary."""
    original_authority, original_debit, original_targets = base.check_authority, base.debit_gate, base.check_targets
    first = True
    def authority(conn: sqlite3.Connection, actual_state: Path, actual_gate: dict[str, Any]) -> None:
        nonlocal first
        schema(conn, gate["schema"])
        operating_guards(state, gate["operator_hold_sha256"], units=first)
        external_inputs(source, config, overrides)
        check_accepted(conn, state, expected, verify_bytes=first)
        original_authority(conn, actual_state, actual_gate)
        if first:
            require(conn.execute("SELECT 1 FROM execution_admissions WHERE state='admitted' LIMIT 1").fetchone() is None, "unsettled request or control admissions")
        first = False
    def debit(*args: Any, **kwargs: Any) -> Any:
        try:
            return original_debit(*args, **kwargs)
        except ValueError as exc:
            return "extension phase1 gate: " + str(exc)
    def targets(conn: sqlite3.Connection, actual_state: Path, actual_gate: dict[str, Any], load_catalog: Any, get_page_kind: Any) -> Any:
        original = json.loads((packet_path.parent / "original-scope.json").read_bytes())
        require(actual_gate["original_targets"] == original["original_targets"], "original17 identities changed")
        return original_targets(conn, actual_state, actual_gate, load_catalog, get_page_kind)
    base.check_authority, base.debit_gate, base.check_targets = authority, debit, targets


def run(packet_path: Path, packet_sha: str, gate_path: Path, gate_sha: str, output: Path, execute: bool) -> None:
    packet = verify_packet(packet_path, packet_sha)
    source = runtime(packet)
    require(sha(gate_path) == gate_sha, "gate hash differs")
    gate = json.loads(gate_path.read_bytes())
    require(gate.get("extension_format") == "extension-phase1-gate-v1" and gate.get("packet_sha256") == packet_sha, "extension gate is not bound to packet")
    for name in ("source", "source_receipt", "source_receipt_sha256", "schema"):
        require(gate[name] == packet[name], "gate runtime binding differs")
    require(gate["driver_sha256"] == BASE_SHA, "base driver binding differs")
    state = Path(gate["state"]).resolve(strict=True)
    require(state == Path("/var/lib/swingset"), "live coordinator state required")
    require(not output.resolve().is_relative_to(packet_path.parent.resolve()), "receipt must be outside immutable helper packet")
    config, overrides = Path(gate["config"]), Path(gate["overrides"])
    expected = expected_bundle(source, config, overrides, datetime.now(UTC))
    require(expected == gate["expected_bundle"], "current runtime/environment/input bundle differs from prepared gate")
    base = load_base(packet_path)
    guard_base(base, source, state, gate, expected, config, overrides, packet_path)
    previous = sys.argv
    sys.argv = [str(packet_path.parent / "base.py"), "--gate", str(gate_path), "--gate-sha256", gate_sha, "--output", str(output)] + (["--execute"] if execute else [])
    try:
        base.main()
    finally:
        sys.argv = previous


def main() -> None:
    sys.dont_write_bytecode = True
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    builder.add_argument("--source", type=Path, required=True)
    builder.add_argument("--deployed-source", required=True)
    builder.add_argument("--source-receipt", default="extension-source.json")
    builder.add_argument("--source-receipt-sha256", required=True)
    builder.add_argument("--output", type=Path, required=True)
    builder.add_argument("--schema", type=int, required=True)
    for name in ("prepare", "run"):
        command = commands.add_parser(name)
        command.add_argument("--packet", type=Path, required=True)
        command.add_argument("--packet-sha256", required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--state", type=Path, required=True)
            command.add_argument("--config", type=Path, required=True)
            command.add_argument("--overrides", type=Path, required=True)
            command.add_argument("--coordinator-reviewed", action="store_true", required=True)
        else:
            command.add_argument("--gate", type=Path, required=True)
            command.add_argument("--gate-sha256", required=True)
            command.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build(args.source, args.deployed_source, args.source_receipt, args.source_receipt_sha256, args.output, schema_version=args.schema), indent=2))
    elif args.command == "prepare":
        print(json.dumps(prepare(args.packet, args.packet_sha256, args.state, args.config, args.overrides, args.output), indent=2))
    else:
        run(args.packet, args.packet_sha256, args.gate, args.gate_sha256, args.output, args.execute)


if __name__ == "__main__":
    main()
