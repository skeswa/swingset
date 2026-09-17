"""Capture a post-prepare anchor or independently verify completed initialization.

No migrating opener, input capture/acceptance, worker, or publication is called.
Only a fresh private receipt is written. Execution needs coordinator review.
"""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import signal
import sqlite3
import stat
import subprocess
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

STATE = Path("/var/lib/swingset")
OPS = STATE / "operations/h16-event-preservation-release-20260916"
SOURCE = Path("/nix/store/z689qy41inndill3d92ym8im852x3649-source")
SOURCE_SHA = "0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb"
BUNDLE = "fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a"
DRIVER_SHA = "b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233"
SUPERVISOR_SHA = "9d48562395750244fb64c8420390c3402c8af2aea2019e46bb66577c1b3c4312"
CHECKPOINT = STATE / "checkpoints/h15-before-h16-20260913"
CHECKPOINT_SHA = "700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444"
CHECKPOINT_DB_SHA = "d81b2e459471c25c430a57c2a4d096180f1094e57c1b8b1e896d66774f12e8dc"
TIME_LIMIT = 600


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def regular(path):
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)), "absolute nonsymlink file required")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "regular file required")
        return stream.read()


def checked(path, expected):
    require(isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected), "exact reviewed SHA256 required")
    raw = regular(path)
    require(hashlib.sha256(raw).hexdigest() == expected, f"reviewed file changed: {path}")
    return raw


def private_path(path, *, new=False):
    require(path.is_absolute() and ".." not in path.parts and path.is_relative_to(OPS) and path != OPS, "receipt must remain in exact operation directory")
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink receipt path forbidden")
    require(path.parent.is_dir(), "receipt parent must already exist")
    if new:
        require(not path.exists(), "output must be new")
    return path


def load_module(path, expected, name):
    checked(path, expected)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_inputs(args):
    values = {}
    for name in ("gate", "marker", "prepare"):
        path = private_path(getattr(args, name))
        values[name] = json.loads(checked(path, getattr(args, name + "_sha256")))
    gate, marker, prepared = (values[k] for k in ("gate", "marker", "prepare"))
    expected = {"state": str(STATE), "source": str(SOURCE), "source_receipt_sha256": SOURCE_SHA, "input_bundle_hash": BUNDLE, "driver_sha256": DRIVER_SHA}
    require(all(gate.get(k) == v and marker.get(k) == v for k, v in expected.items()), "wrong gate/marker authority")
    require(gate.get("format") == "h16-production-initialization-gate-v1" and gate.get("stages") == ["project", "link"] and gate.get("capture_accept_authorized") is True, "explicit initializer gate required")
    require(gate.get("config") == str(SOURCE / "config") and gate.get("overrides") == str(SOURCE / "overrides"), "frozen config and overrides required")
    require(marker.get("format") == "h16-production-initialization-v1" and marker.get("gate_sha256") == args.gate_sha256, "wrong marker gate")
    require(not marker.get("continuation"), "continuation requires separate verifier review")
    require(marker.get("checkpoint") == str(CHECKPOINT) and marker.get("checkpoint_manifest_sha256") == CHECKPOINT_SHA, "wrong inherited checkpoint")
    for key in ("protected", "controls", "prepared_input_authority", "preflight_baseline"):
        require(isinstance(marker.get(key), dict) and marker[key], f"missing prepared {key}")
    expected_prepare = {"format": "h16-production-initialization-receipt-v1", "mode": "prepare", "status": "prepared", "gate_sha256": args.gate_sha256, "marker_sha256": args.marker_sha256, "driver_sha256": DRIVER_SHA, "source_receipt_sha256": SOURCE_SHA, "input_bundle_hash": BUNDLE, "network_requests": 0, "parse_executed": False, "build_executed": False, "published": False, "attempted": 0, "completed": 0, "parse_execution_authorized": False}
    require(all(prepared.get(k) == v and type(prepared.get(k)) is type(v) for k, v in expected_prepare.items()) and prepared.get("finished_at") and not prepared.get("error"), "successful exact preparation receipt required")
    require(prepared.get("initial_hold", {}).get("baseline") == marker["preflight_baseline"], "prepare baseline mismatch")
    require(isinstance(gate.get("evidence_files"), dict) and gate["evidence_files"], "gate evidence hashes required")
    require(all(name in gate["evidence_files"] for name in gate.get("receipts", {}).values()), "unhashed prerequisite receipt")
    for relative, expected_hash in gate["evidence_files"].items():
        path = args.gate.parent / relative
        require(not Path(relative).is_absolute() and ".." not in Path(relative).parts, "evidence escapes gate directory")
        checked(private_path(path), expected_hash)
    return gate, marker


def validate_supervision(args, marker, supervisor):
    header = json.loads(checked(private_path(args.supervision_preflight), args.supervision_preflight_sha256))
    final = json.loads(checked(private_path(args.supervision_final), args.supervision_final_sha256))
    require(args.supervision_preflight.name == "preflight.json" and args.supervision_final.name == "final.json" and args.supervision_preflight.parent == args.supervision_final.parent, "one exact supervision session required")
    directory = args.supervision_final.parent
    session = directory.name.removeprefix("supervision-")
    require(directory.parent == OPS and directory.name.startswith("supervision-") and re.fullmatch("[a-z0-9][a-z0-9-]{0,47}", session), "invalid supervision directory")
    expected = {"format": "h16-initialization-supervision-v1", "source": str(SOURCE), "source_receipt_sha256": SOURCE_SHA, "input_bundle_hash": BUNDLE, "gate_sha256": args.gate_sha256, "marker_sha256": args.marker_sha256, "driver_sha256": DRIVER_SHA, "supervisor_sha256": SUPERVISOR_SHA}
    require(all(header.get(k) == v for k, v in expected.items()), "supervisor preflight binding mismatch")
    require(header.get("authority", {}).get("baseline") == marker["preflight_baseline"], "supervisor baseline mismatch")
    batches = final.get("batches")
    require(final.get("format") == expected["format"] and final.get("status") == "current" and final.get("current") is True and final.get("finished_at") and final.get("started_at") == header.get("started_at"), "supervision is not current")
    require(isinstance(batches, list) and type(header.get("max_invocations")) is int and 1 <= len(batches) <= header["max_invocations"] <= 12 and final.get("invocations") == len(batches), "invalid bounded batch inventory")
    receipt_args = SimpleNamespace(gate_sha256=args.gate_sha256, marker_sha256=args.marker_sha256, driver_sha256=DRIVER_SHA)
    receipts = []
    for number, batch in enumerate(batches, 1):
        output = directory / f"initializer-{number:03}.json"
        require(batch.get("number") == number and batch.get("unit") == f"swingset-h16-init-{session}-{number:03}.service" and batch.get("output") == str(output), "wrong batch locator")
        require(batch.get("exit_code") == 0 and not batch.get("error") and batch.get("settled", {}).get("writer_lock_released") is True, "batch failed or lock unreleased")
        receipt = json.loads(checked(private_path(output), batch.get("receipt_sha256")))
        status = supervisor.validate_receipt(receipt, receipt_args, header["authority"], batch["exit_code"])
        require(status == ("current" if number == len(batches) else "bounded_stop") and batch.get("status") == status, "unexpected batch continuation")
        require(batch.get("attempted") == receipt["attempted"] and batch.get("completed") == receipt["completed"], "batch totals differ from raw receipt")
        receipts.append(receipt)
    require(final.get("completed") == sum(r["completed"] for r in receipts), "supervisor completion sum differs")
    require(args.receipt == Path(batches[-1]["output"]) and args.receipt_sha256 == batches[-1]["receipt_sha256"], "terminal receipt is not final batch")
    checked(args.receipt, args.receipt_sha256)
    return header["authority"], final, receipts


def readonly(path, *, immutable=False):
    conn = sqlite3.connect(path.as_uri() + ("?mode=ro&immutable=1" if immutable else "?mode=ro"), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    return conn


def open_runs(conn):
    return [list(row) for row in conn.execute("SELECT run_id,started_at,finished_at FROM runs WHERE finished_at IS NULL ORDER BY run_id")]


def inspect_snapshot(conn, driver, marker, *, anchor=None, pending_units=None):
    require(conn.execute("PRAGMA user_version").fetchone()[0] == 14, "production schema must remain14")
    require(conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0] == BUNDLE, "accepted bundle changed")
    require(driver.protected(conn) == marker["protected"], "protected source/schema/journal changed")
    require(driver.controls(conn) == marker["controls"], "operator controls changed")
    driver.check_input_authority(conn, marker)
    driver.check_cache(conn, marker)
    require(not conn.execute("SELECT 1 FROM work_attempts WHERE outcome='running' LIMIT 1").fetchone(), "running work attempt remains")
    require(not conn.execute("SELECT 1 FROM execution_admissions WHERE state!='settled' LIMIT 1").fetchone(), "unsettled admission remains")
    value = {"parse_tokens": driver.parse_tokens(conn), "open_runs": open_runs(conn), "protected_unchanged": True, "controls_unchanged": True, "input_authority_unchanged": True, "extract_cache_unchanged": True}
    value["attempt_ledger"] = driver.table_digest(conn, "work_attempts")
    value["max_attempt_id"] = conn.execute("SELECT coalesce(max(attempt_id),0) FROM work_attempts").fetchone()[0]
    if anchor is not None:
        require(value["parse_tokens"] == anchor["parse_tokens"], "post-prepare parse tokens changed")
        require(value["open_runs"] == anchor["open_runs"], "inherited unfinished runs changed")
        value["unfinished_by_scope"] = dict(Counter(unit.stage + "/" + unit.unit_kind for stage in ("project", "link") for unit in pending_units(conn, stage)))
        require(value["unfinished_by_scope"] == {}, "unfinished project/link scopes remain")
        value["attempt_outcomes"] = dict(conn.execute("SELECT outcome,count(*) FROM work_attempts GROUP BY outcome"))
        value["new_attempts_by_stage_outcome"] = [list(row) for row in conn.execute("SELECT stage,outcome,count(*) FROM work_attempts WHERE attempt_id>? GROUP BY stage,outcome ORDER BY stage,outcome", (anchor["max_attempt_id"],))]
        require(all(stage in ("project", "link") and outcome == "succeeded" for stage, outcome, _ in value["new_attempts_by_stage_outcome"]), "unexpected or unsuccessful initialization attempt")
        value["generation_count"] = conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0]
        value["materialized_by_scope"] = {stage + "/" + kind: count for stage, kind, count in conn.execute("SELECT stage,unit_kind,count(*) FROM derivation_scopes WHERE materialized_generation_id IS NOT NULL GROUP BY stage,unit_kind")}
        value["foreign_key_violations"] = sum(1 for _ in conn.execute("PRAGMA foreign_key_check"))
        value["quick_check"] = [row[0] for row in conn.execute("PRAGMA quick_check")]
        require(not value["foreign_key_violations"] and value["quick_check"] == ["ok"], "database integrity failure")
    require(conn.total_changes == 0, "read-only inspection changed SQLite")
    value["connection_total_changes"] = conn.total_changes
    return value


def file_metadata():
    return {name: {"size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for name in ("state.sqlite", "state.sqlite-wal") if (p := STATE / name).exists()}


def save_new(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def execute(args):
    from research.replay_derivations import verify_runtime

    from swingset.state import derivations

    output = private_path(args.output, new=True)
    require(output not in {getattr(args, name, None) for name in ("gate", "marker", "prepare", "anchor", "receipt", "supervision_preflight", "supervision_final")}, "output aliases input")
    started = time.monotonic()
    report = {"format": "h16-production-initialization-" + args.mode + "-v1", "mode": args.mode, "at": datetime.now(UTC).isoformat(), "state": str(STATE), "source": str(SOURCE), "source_receipt_sha256": SOURCE_SHA, "input_bundle_hash": BUNDLE, "driver_sha256": DRIVER_SHA, "script_sha256": sha(Path(__file__).resolve()), "passed": False, "network_requests": 0, "parse_executed": False, "build_executed": False, "published": False}
    for name in ("gate", "marker", "prepare", "anchor", "receipt", "supervision_preflight", "supervision_final"):
        if getattr(args, name, None):
            report[name] = str(getattr(args, name))
            report[name + "_sha256"] = getattr(args, name + "_sha256")
    try:
        verify_runtime(SOURCE, SOURCE_SHA)
        gate, marker = load_inputs(args)
        driver = load_module(OPS / "initialize.py", DRIVER_SHA, "reviewed_initializer_verification")
        anchor, authority, final, receipts = None, None, None, None
        if args.mode == "verify":
            anchor = json.loads(checked(private_path(args.anchor), args.anchor_sha256))
            expected = {"format": "h16-production-initialization-anchor-v1", "mode": "anchor", "passed": True, "state": str(STATE), "source": str(SOURCE), "source_receipt_sha256": SOURCE_SHA, "input_bundle_hash": BUNDLE, "driver_sha256": DRIVER_SHA, "gate_sha256": args.gate_sha256, "marker_sha256": args.marker_sha256, "prepare_sha256": args.prepare_sha256, "script_sha256": report["script_sha256"]}
            require(all(anchor.get(k) == v for k, v in expected.items()), "post-prepare anchor authority mismatch")
            supervisor = load_module(OPS / "supervise-initialization.py", SUPERVISOR_SHA, "reviewed_supervisor_verification")
            authority, final, receipts = validate_supervision(args, marker, supervisor)
            for batch in final["batches"]:
                raw = subprocess.run(["systemctl", "show", batch["unit"], "--property=LoadState,ActiveState,MainPID"], check=True, capture_output=True, text=True, timeout=15).stdout
                state = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
                require(state.get("LoadState") == "not-found" or (state.get("ActiveState") == "inactive" and state.get("MainPID") == "0"), "initializer worker still active")
        require(sha(CHECKPOINT / "checkpoint.json") == CHECKPOINT_SHA and sha(CHECKPOINT / "state.sqlite") == CHECKPOINT_DB_SHA, "inherited checkpoint bytes changed")
        with closing(readonly(CHECKPOINT / "state.sqlite", immutable=True)) as old:
            inherited = open_runs(old)
            checkpoint_attempt_ledger = driver.table_digest(old, "work_attempts")
        require(len(inherited) == 7, "expected exactly seven inherited unfinished runs")
        require(not (STATE / "state.lock").is_symlink(), "writer lock must not be a symlink")
        with (STATE / "state.lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report["lock_exclusive_acquired"] = True
            before = file_metadata()
            held = driver.hold_and_baseline(gate, args.gate)
            require(held["baseline"] == marker["preflight_baseline"] and (authority is None or held == authority), "live hold/baseline differs from reviewed authority")
            with closing(readonly(STATE / "state.sqlite")) as conn:
                conn.set_progress_handler(lambda: int(time.monotonic() - started > TIME_LIMIT - 5), 10000)
                report.update(inspect_snapshot(conn, driver, marker, anchor=anchor, pending_units=derivations.pending_units))
            require(report["open_runs"] == inherited, "original unfinished runs were changed")
            if args.mode == "anchor":
                require(report["attempt_ledger"] == checkpoint_attempt_ledger, "anchor must precede all initializer work")
            if receipts is not None:
                require(report["generation_count"] == receipts[-1]["generation_count"], "terminal generation count differs from SQLite")
                require(sum(count for _, _, count in report["new_attempts_by_stage_outcome"]) == final["completed"], "unaccounted initialization attempts")
                report["supervision_completed"] = final["completed"]
            require(driver.hold_and_baseline(gate, args.gate) == held, "hold/baseline changed during verification")
            report.update(initial_hold=held, database_files_before=before, database_files_after=file_metadata())
            require(before == report["database_files_after"], "main database or WAL bytes changed during inspection")
            for name in ("gate", "marker", "prepare", "anchor", "receipt", "supervision_preflight", "supervision_final"):
                if getattr(args, name, None):
                    checked(getattr(args, name), getattr(args, name + "_sha256"))
            report["passed"] = True
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        save_new(output, report)
        print(json.dumps({"output": str(output), "sha256": sha(output), "passed": report["passed"], "error": report.get("error")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("anchor", "verify"))
    for name in ("gate", "marker", "prepare", "anchor", "receipt", "supervision-preflight", "supervision-final"):
        parser.add_argument("--" + name, type=Path, required=name in ("gate", "marker", "prepare"))
        parser.add_argument("--" + name + "-sha256", required=name in ("gate", "marker", "prepare"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name in ("anchor", "receipt", "supervision_preflight", "supervision_final"):
        require(bool(getattr(args, name)) == (args.mode == "verify") and bool(getattr(args, name + "_sha256")) == (args.mode == "verify"), "verify requires all terminal bindings; anchor forbids them")
    require(os.geteuid() == 0, "run the reviewed verifier as VM root")
    def expired(*_):
        raise TimeoutError("initialization verification exceeded600 seconds")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(TIME_LIMIT)
    try:
        execute(args)
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()
