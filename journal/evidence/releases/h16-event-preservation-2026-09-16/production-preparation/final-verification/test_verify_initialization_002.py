"""Offline guards only: no VM, production state, service calls, or network."""

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("final_init_verifier", HERE / "verify-initialization-002.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def write(path, value):
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "OPS", tmp_path)
    args = SimpleNamespace(mode="anchor", gate=tmp_path / "gate.json", marker=tmp_path / "marker.json", prepare=tmp_path / "prepare.json")
    (tmp_path / "prerequisite.json").write_text("{}")
    expected = {"state": str(v.STATE), "source": str(v.SOURCE), "source_receipt_sha256": v.SOURCE_SHA, "input_bundle_hash": v.BUNDLE, "driver_sha256": v.DRIVER_SHA}
    gate = dict(expected, format="h16-production-initialization-gate-v1", stages=["project", "link"], capture_accept_authorized=True, config=str(v.SOURCE / "config"), overrides=str(v.SOURCE / "overrides"), receipts={"production_acceptance": "prerequisite.json"}, evidence_files={"prerequisite.json": v.sha(tmp_path / "prerequisite.json")})
    args.gate_sha256 = write(args.gate, gate)
    marker = dict(expected, format="h16-production-initialization-v1", gate_sha256=args.gate_sha256, checkpoint=str(v.CHECKPOINT), checkpoint_manifest_sha256=v.CHECKPOINT_SHA, protected={"basis": "p"}, controls={"basis": "c"}, prepared_input_authority={"basis": "i"}, preflight_baseline={"commit": "baseline"})
    args.marker_sha256 = write(args.marker, marker)
    prepared = {"format": "h16-production-initialization-receipt-v1", "mode": "prepare", "status": "prepared", "gate_sha256": args.gate_sha256, "marker_sha256": args.marker_sha256, "driver_sha256": v.DRIVER_SHA, "source_receipt_sha256": v.SOURCE_SHA, "input_bundle_hash": v.BUNDLE, "network_requests": 0, "parse_executed": False, "build_executed": False, "published": False, "attempted": 0, "completed": 0, "parse_execution_authorized": False, "finished_at": "finished", "initial_hold": {"baseline": marker["preflight_baseline"]}}
    args.prepare_sha256 = write(args.prepare, prepared)
    return args, gate, marker, prepared


def test_accepts_exact_prepared_authority(inputs):
    args, gate, marker, _ = inputs
    assert v.load_inputs(args) == (gate, marker)


@pytest.mark.parametrize("field,value", [("state", "/tmp/other"), ("source", "/tmp/source"), ("source_receipt_sha256", "0" * 64), ("input_bundle_hash", "0" * 64), ("driver_sha256", "0" * 64), ("stages", ["parse", "project", "link"]), ("capture_accept_authorized", False), ("config", "/tmp/config"), ("overrides", "/tmp/overrides")])
def test_rejects_gate_drift(inputs, field, value):
    args, gate, _, _ = inputs
    gate[field] = value
    args.gate_sha256 = write(args.gate, gate)
    with pytest.raises(ValueError):
        v.load_inputs(args)


@pytest.mark.parametrize("field,value", [("status", "preflight"), ("mode", "run"), ("error", {"message": "failed"}), ("finished_at", None), ("parse_executed", True), ("network_requests", False), ("build_executed", True), ("published", True), ("attempted", 1), ("completed", 1), ("marker_sha256", "0" * 64)])
def test_rejects_unfinished_or_wrong_prepare_receipt(inputs, field, value):
    args, _, _, prepared = inputs
    prepared[field] = value
    args.prepare_sha256 = write(args.prepare, prepared)
    with pytest.raises(ValueError):
        v.load_inputs(args)


def test_rejects_continuation_without_separate_review(inputs):
    args, _, marker, _ = inputs
    marker["continuation"] = {"previous_marker": "other"}
    args.marker_sha256 = write(args.marker, marker)
    with pytest.raises(ValueError, match="continuation"):
        v.load_inputs(args)


def test_rejects_changed_prerequisite_bytes(inputs):
    args, *_ = inputs
    (args.gate.parent / "prerequisite.json").write_text("changed")
    with pytest.raises(ValueError, match="reviewed file changed"):
        v.load_inputs(args)


def test_private_paths_and_exclusive_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "OPS", tmp_path)
    target = tmp_path / "receipt.json"
    v.private_path(target, new=True)
    v.save_new(target, {"passed": False})
    with pytest.raises(FileExistsError):
        v.save_new(target, {"passed": True})
    assert json.loads(target.read_bytes())["passed"] is False
    link = tmp_path / "alias.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="nonsymlink"):
        v.checked(link, v.sha(target))
    with pytest.raises(ValueError, match="operation directory"):
        v.private_path(tmp_path.parent / "outside.json", new=True)
    with pytest.raises(ValueError, match="operation directory"):
        v.private_path(tmp_path / ".." / "outside.json", new=True)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "state.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        PRAGMA user_version=14;
        CREATE TABLE meta(key TEXT,value TEXT);
        CREATE TABLE runs(run_id TEXT,started_at TEXT,finished_at TEXT);
        CREATE TABLE work_attempts(attempt_id INTEGER,stage TEXT,outcome TEXT);
        CREATE TABLE execution_admissions(state TEXT);
        CREATE TABLE derivation_generations(generation_id TEXT);
        CREATE TABLE derivation_scopes(stage TEXT,unit_kind TEXT,materialized_generation_id TEXT);
    """)
    conn.execute("INSERT INTO meta VALUES ('input_bundle_hash',?)", (v.BUNDLE,))
    conn.executemany("INSERT INTO runs VALUES (?, 'old', NULL)", [(str(n),) for n in range(7)])
    conn.commit()
    conn.close()
    marker = {"protected": {"p": 1}, "controls": {"c": 1}}
    driver = SimpleNamespace(protected=lambda c: {"p": 1}, controls=lambda c: {"c": 1}, check_input_authority=lambda c, m: None, check_cache=lambda c, m: None, parse_tokens=lambda c: {"pending_work": "prepared", "work_generations": "old", "work_attempts": "old"}, table_digest=lambda c, table: [tuple(r) for r in c.execute("SELECT * FROM " + table)])
    return path, marker, driver


def inspect(database, **kwargs):
    path, marker, driver = database
    conn = v.readonly(path)
    try:
        return v.inspect_snapshot(conn, driver, marker, **kwargs)
    finally:
        conn.close()


def test_real_query_only_snapshot_cannot_write_meta(database):
    path, *_ = database
    conn = v.readonly(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE meta SET value='wrong'")
        assert conn.total_changes == 0
        assert conn.execute("SELECT value FROM meta").fetchone()[0] == v.BUNDLE
    finally:
        conn.close()


def test_final_snapshot_matches_real_anchor(database):
    anchor = inspect(database)
    assert len(anchor["open_runs"]) == 7
    final = inspect(database, anchor=anchor, pending_units=lambda c, s: [])
    assert final["quick_check"] == ["ok"]
    assert final["unfinished_by_scope"] == {}
    assert final["connection_total_changes"] == 0


@pytest.mark.parametrize("change,reason", [("INSERT INTO work_attempts VALUES(1,'link','running')", "running"), ("INSERT INTO execution_admissions VALUES('active')", "unsettled"), ("UPDATE meta SET value='wrong'", "bundle"), ("PRAGMA user_version=15", "schema")])
def test_final_rejects_unsettled_or_wrong_state(database, change, reason):
    anchor = inspect(database)
    with sqlite3.connect(database[0]) as conn:
        conn.execute(change)
    with pytest.raises(ValueError, match=reason):
        inspect(database, anchor=anchor, pending_units=lambda c, s: [])


def test_final_rejects_parse_drift(database):
    anchor = inspect(database)
    database[2].parse_tokens = lambda c: {"pending_work": "changed"}
    with pytest.raises(ValueError, match="parse tokens"):
        inspect(database, anchor=anchor, pending_units=lambda c, s: [])


def test_final_rejects_inherited_run_closure(database):
    anchor = inspect(database)
    with sqlite3.connect(database[0]) as conn:
        conn.execute("UPDATE runs SET finished_at='changed' WHERE run_id='1'")
    with pytest.raises(ValueError, match="unfinished runs"):
        inspect(database, anchor=anchor, pending_units=lambda c, s: [])


def test_final_rejects_unfinished_scope(database):
    anchor = inspect(database)
    with pytest.raises(ValueError, match="unfinished project/link"):
        inspect(database, anchor=anchor, pending_units=lambda c, s: [SimpleNamespace(stage=s, unit_kind="event")])


@pytest.mark.parametrize("stage,outcome", [("parse", "succeeded"), ("project", "failed"), ("link", "blocked")])
def test_final_rejects_wrong_new_attempts(database, stage, outcome):
    anchor = inspect(database)
    with sqlite3.connect(database[0]) as conn:
        conn.execute("INSERT INTO work_attempts VALUES(1,?,?)", (stage, outcome))
    with pytest.raises(ValueError, match="unexpected or unsuccessful"):
        inspect(database, anchor=anchor, pending_units=lambda c, s: [])


@pytest.fixture
def supervision(inputs):
    args, _, marker, prepared = inputs
    directory = args.gate.parent / "supervision-reviewed-001"
    directory.mkdir()
    args.supervision_preflight, args.supervision_final = directory / "preflight.json", directory / "final.json"
    authority = prepared["initial_hold"]
    header = {"format": "h16-initialization-supervision-v1", "source": str(v.SOURCE), "source_receipt_sha256": v.SOURCE_SHA, "input_bundle_hash": v.BUNDLE, "gate_sha256": args.gate_sha256, "marker_sha256": args.marker_sha256, "driver_sha256": v.DRIVER_SHA, "supervisor_sha256": v.SUPERVISOR_SHA, "authority": authority, "started_at": "start", "max_invocations": 12}
    args.supervision_preflight_sha256 = write(args.supervision_preflight, header)
    args.receipt = directory / "initializer-001.json"
    receipt = {"format": "h16-production-initialization-receipt-v1", "mode": "run", "gate_sha256": args.gate_sha256, "marker_sha256": args.marker_sha256, "driver_sha256": v.DRIVER_SHA, "source_receipt_sha256": v.SOURCE_SHA, "finished_at": "done", "protected_unchanged": True, "parse_tokens_unchanged": True, "controls_unchanged": True, "input_authority_unchanged": True, "network_requests": 0, "parse_executed": False, "build_executed": False, "published": False, "initial_hold": authority, "final_hold": authority, "attempted": 1, "completed": 1, "outcomes": {"succeeded": 1}, "status": "current", "unfinished_by_scope": {}}
    args.receipt_sha256 = write(args.receipt, receipt)
    batch = {"number": 1, "unit": "swingset-h16-init-reviewed-001-001.service", "output": str(args.receipt), "exit_code": 0, "settled": {"writer_lock_released": True}, "receipt_sha256": args.receipt_sha256, "status": "current", "attempted": 1, "completed": 1}
    final = {"format": "h16-initialization-supervision-v1", "started_at": "start", "finished_at": "done", "status": "current", "current": True, "invocations": 1, "completed": 1, "batches": [batch]}
    args.supervision_final_sha256 = write(args.supervision_final, final)
    supervisor = v.load_module(HERE.parent / "supervise-initialization-002.py", v.SUPERVISOR_SHA, "supervisor_guard_test")
    return args, marker, supervisor, header, final, receipt


def test_current_supervision_uses_real_receipt_validator(supervision):
    args, marker, supervisor, _, final, receipt = supervision
    _, actual, receipts = v.validate_supervision(args, marker, supervisor)
    assert actual == final and receipts == [receipt]


@pytest.mark.parametrize("field,value", [("current", False), ("status", "invocation_cap"), ("invocations", 2), ("completed", 2), ("started_at", "other")])
def test_rejects_false_terminal_supervision(supervision, field, value):
    args, marker, supervisor, _, final, _ = supervision
    final[field] = value
    args.supervision_final_sha256 = write(args.supervision_final, final)
    with pytest.raises(ValueError):
        v.validate_supervision(args, marker, supervisor)


@pytest.mark.parametrize("field,value", [("exit_code", 1), ("settled", {"writer_lock_released": False}), ("receipt_sha256", "0" * 64), ("unit", "other.service"), ("attempted", 2), ("error", {"message": "failed"})])
def test_rejects_bad_batch(supervision, field, value):
    args, marker, supervisor, _, final, _ = supervision
    final["batches"][0][field] = value
    args.supervision_final_sha256 = write(args.supervision_final, final)
    with pytest.raises(ValueError):
        v.validate_supervision(args, marker, supervisor)


@pytest.mark.parametrize("field,value", [("parse_tokens_unchanged", False), ("status", "bounded_stop"), ("unfinished_by_scope", {"link/event": 1}), ("published", True), ("outcomes", {"failed": 1})])
def test_rejects_bad_raw_terminal_receipt(supervision, field, value):
    args, marker, supervisor, _, final, receipt = supervision
    receipt[field] = value
    args.receipt_sha256 = write(args.receipt, receipt)
    final["batches"][0]["receipt_sha256"] = args.receipt_sha256
    args.supervision_final_sha256 = write(args.supervision_final, final)
    with pytest.raises(ValueError):
        v.validate_supervision(args, marker, supervisor)
