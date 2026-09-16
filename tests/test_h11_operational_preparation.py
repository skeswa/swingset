"""Operational preparation stays selective and fails closed before migration."""

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


def module(name):
    path = Path(__file__).parents[1] / "journal/tools/runtime" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_h11_assembler_preserves_v4_memoryfix_and_excludes_unreviewed_work(tmp_path):
    assembler = module("assemble_h11_source")
    base, reviewed, output = (tmp_path / name for name in ("v4", "working", "h11"))
    for name in assembler.OVERLAYS:
        write(reviewed, name, "reviewed H11\n")
    write(base, "src/swingset/state/db.py", "SCHEMA_VERSION = 9\n")
    write(reviewed, "src/swingset/state/db.py", "SCHEMA_VERSION = 10\n")
    write(base, "src/swingset/build/builder.py", "V4 memory fix\n")
    write(base, "src/swingset/schedule/cycle.py", "V4 cycle\n")
    write(reviewed, "src/swingset/build/builder.py", "unreviewed builder edit\n")
    write(reviewed, "src/swingset/schedule/repairs.py", "future H12\n")
    write(reviewed, "journal/tools/admission/fixture_runner.py", "future fixture runner\n")
    result = assembler.assemble(base, reviewed, output)
    assert result["build_runtime_preserved"]
    assert (output / "src/swingset/build/builder.py").read_text() == "V4 memory fix\n"
    assert (output / "src/swingset/schedule/cycle.py").read_text() == "V4 cycle\n"
    assert not (output / "src/swingset/schedule/repairs.py").exists()
    assert not (output / "journal/tools/admission/fixture_runner.py").exists()
    assert set(result["changed"]) == set(assembler.OVERLAYS)
    assert json.loads((output / "h11-source.json").read_text()) == result


def test_h11_assembler_rejects_unrelated_database_edit_before_output(tmp_path):
    assembler = module("assemble_h11_source")
    base, reviewed, output = (tmp_path / name for name in ("v4", "working", "h11"))
    write(base, "src/swingset/state/db.py", "SCHEMA_VERSION = 9\n")
    write(reviewed, "src/swingset/state/db.py", "SCHEMA_VERSION = 10\n# unrelated\n")
    with pytest.raises(ValueError, match="differ.*only"):
        assembler.assemble(base, reviewed, output)
    assert not output.exists()


def test_h11_hold_rejects_active_timer_and_missing_condition(tmp_path, monkeypatch):
    driver = module("accept_h11")
    (tmp_path / "operator-hold").touch()
    active = set()
    condition = [f"ConditionPathExists=!{tmp_path / 'operator-hold'}"]

    def systemctl(args, **kwargs):
        unit = args[2]
        if args[1] == "cat":
            return SimpleNamespace(stdout=condition[0])
        status = "active" if unit in active else "inactive"
        return SimpleNamespace(
            stdout=f"LoadState=loaded\nActiveState={status}\nSubState=dead\nConditionResult=no\n"
        )

    monkeypatch.setattr(driver.subprocess, "run", systemctl)
    assert len(driver.system_hold(tmp_path)) == 6
    active.add("swingset-summary.timer")
    with pytest.raises(ValueError, match="installed and stopped"):
        driver.system_hold(tmp_path)
    active.clear()
    condition[0] = ""
    with pytest.raises(ValueError, match="negative persistent hold"):
        driver.system_hold(tmp_path)


def test_h11_human_receipt_detects_missing_json_fields():
    driver = module("accept_h11")
    report = {
        "available": True,
        "snapshot_at": "2026-09-13",
        "stale": False,
        "unmet": 0,
        "eligible": 0,
        "paused": 0,
        "active_attempts": 0,
        "scan": {"cursor": "", "last_completed_at": "2026-09-13"},
        "requirements": [],
        "execution_enabled": False,
        "pipeline_lag": {"threshold_seconds": 1800},
        "cohorts": [],
    }
    human = driver.human_report(report)
    assert driver.human_matches(report, human)
    assert not driver.human_matches(
        report,
        "\n".join(line for line in human.splitlines() if not line.startswith("pipeline_lag:")),
    )


def test_h11_protected_table_digest_detects_changed_value_not_just_count():
    driver = module("accept_h11")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE accepted_inputs (consumer TEXT, digest TEXT)")
        conn.execute("INSERT INTO accepted_inputs VALUES ('link','before')")
        before = driver.table_digest(conn, "accepted_inputs")
        conn.execute("UPDATE accepted_inputs SET digest='after'")
        after = driver.table_digest(conn, "accepted_inputs")
        assert before["count"] == after["count"] == 1
        assert before["sha256"] != after["sha256"]
    finally:
        conn.close()


def test_h11_checkpoint_path_rejected_before_systemd_or_migration(tmp_path, monkeypatch):
    driver = module("accept_h11")
    (tmp_path / "checkpoint.json").write_text("{}")
    monkeypatch.setattr(driver, "system_hold", lambda _: pytest.fail("must reject first"))
    with pytest.raises(ValueError, match="never migrate"):
        driver.preflight(tmp_path, tmp_path, tmp_path / "gate.json")


def test_h11_driver_migrates_scans_and_restarts_without_consuming_work(tmp_path, monkeypatch):
    from swingset.state import db as database_module
    from swingset.state.db import open_database

    driver = module("accept_h11")
    state = tmp_path / "state"
    root = Path(__file__).parents[1]
    with monkeypatch.context() as old_schema:
        old_schema.setattr(database_module, "SCHEMA_VERSION", 9)
        with open_database(state) as db:
            db.connection.execute(
                "INSERT INTO operator_pauses VALUES ('all','',NULL,'retain operator control')"
            )
            db.connection.execute(
                "INSERT INTO accepted_inputs VALUES ('link','versions','retained digest')"
            )
            before = driver.controls(db.connection)
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 10)
    monkeypatch.setattr(driver, "preflight", lambda *_: {"before": before})
    monkeypatch.setattr(driver, "system_hold", lambda _: {"synthetic_held": True})
    # The real separate interpreter sees the current runtime via pytest's local
    # source path, not an installed older package.
    monkeypatch.setenv("PYTHONPATH", str(root / "src"))
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(
        driver.sys,
        "argv",
        [
            "accept_h11",
            "--state",
            str(state),
            "--source",
            str(root),
            "--gate",
            str(tmp_path / "gate.json"),
            "--output",
            str(output),
            "--execute",
        ],
    )
    driver.main()
    receipt = json.loads(output.read_text())
    assert receipt["passed"]
    assert receipt["before"]["schema"] == 9
    assert receipt["after"]["schema"] == 10
    assert receipt["before"]["tables"] == receipt["after"]["tables"]
    assert receipt["scan"]["complete"]
    assert receipt["fresh_cohort_outside"] == 0
    assert receipt["restart_read"]["cohorts_persisted"]


def test_h11_preflight_verifies_backup_immutably_and_rejects_tampered_gate(tmp_path, monkeypatch):
    from swingset.backup.checkpoint import create_checkpoint
    from swingset.state import db as database_module
    from swingset.state.db import open_database

    driver = module("accept_h11")
    state, checkpoint, source = (tmp_path / name for name in ("live", "checkpoint", "source"))
    with monkeypatch.context() as old_schema:
        old_schema.setattr(database_module, "SCHEMA_VERSION", 9)
        with open_database(state):
            pass
    write(state, "candidates/v4/PUBLISHED", json.dumps({"commit": "verified-v4"}))
    write(state, "candidates/v4/_meta/manifest.json", "{}")
    (state / "baseline").symlink_to(state / "candidates/v4")
    write(state, "candidates/v4/BUILT", "{}")
    with open_database(state, lock=False, read_only=True) as db:
        create_checkpoint(
            state, db.connection, checkpoint, schema_version=9, versions={}, input_bundle_hash=None
        )
    write(source, "src/swingset/state/db.py", "SCHEMA_VERSION = 10\n")
    write(
        source,
        "h11-source.json",
        json.dumps(
            {
                "format": "h11-selective-source-v1",
                "build_runtime_preserved": True,
                "files": {
                    "src/swingset/state/db.py": driver.digest(source / "src/swingset/state/db.py")
                },
            }
        ),
    )
    monkeypatch.setattr(database_module, "__file__", str(source / "src/swingset/state/db.py"))
    monkeypatch.setattr(driver, "system_hold", lambda _: {"synthetic_held": True})
    evidence = tmp_path / "verified.txt"
    evidence.write_text("synthetic verification evidence, never a real deployment receipt")
    gate = {
        "format": "h11-operational-gate-v1",
        "v4_verified": True,
        "private_backup_verified": True,
        "private_backup_commit": "verified-private",
        "verified_at": "2026-09-13",
        "evidence_files": {evidence.name: driver.digest(evidence)},
        "v4_commit": "verified-v4",
        "v4_manifest_sha256": driver.digest(state / "baseline/_meta/manifest.json"),
        "checkpoint": str(checkpoint),
        "checkpoint_manifest_sha256": driver.digest(checkpoint / "checkpoint.json"),
    }
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(gate))
    before = {
        path.relative_to(checkpoint).as_posix(): path.read_bytes()
        for path in checkpoint.rglob("*")
        if path.is_file()
    }
    assert driver.preflight(state, source, gate_path)["before"]["schema"] == 9
    assert {
        path.relative_to(checkpoint).as_posix(): path.read_bytes()
        for path in checkpoint.rglob("*")
        if path.is_file()
    } == before
    evidence.write_text("changed evidence")
    with pytest.raises(ValueError, match="differs from reviewed"):
        driver.preflight(state, source, gate_path)
    evidence.write_text("synthetic verification evidence, never a real deployment receipt")
    gate["private_backup_verified"] = False
    gate_path.write_text(json.dumps(gate))
    with pytest.raises(ValueError, match="completed V4 and private backup"):
        driver.preflight(state, source, gate_path)
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 9


@pytest.mark.parametrize(
    ("statement", "protected_key"),
    [
        ("UPDATE admission_policies SET mode='enforce'", "admission_policies"),
        (
            "INSERT INTO admission_reviews VALUES ('review','eepro.round','1','{}','reviewer','now','retained')",
            "admission_reviews",
        ),
        ("UPDATE source_units SET accepted_generation_id='generation'", "source_units"),
        ("UPDATE source_generations SET state='needs_review'", "source_generation_selection"),
        (
            "INSERT INTO identity_journal_acceptances VALUES ('journal','bundle','now',0,1)",
            "identity_journal_acceptances",
        ),
        (
            "UPDATE identity_source_refs SET locator_json='{\"changed\":true}'",
            "identity_source_refs",
        ),
        (
            "INSERT INTO identity_reference_bindings VALUES ('binding','ref','entry','entry1','event','snapshot','semantic','now')",
            "identity_reference_bindings",
        ),
        (
            "INSERT INTO identity_reference_migrations VALUES ('migration','ref','[]','pending','evidence','reason','author','today',NULL,'now')",
            "identity_reference_migrations",
        ),
        (
            "INSERT INTO meta VALUES ('input_bundle_hash','changed-bundle')",
            "accepted_input_and_journal_meta",
        ),
        (
            "UPDATE meta SET value='changed-journal' WHERE key='identity_journal_digest'",
            "identity_journal_token",
        ),
        (
            "UPDATE revisions SET value=value+1 WHERE name='identity_decisions'",
            "identity_journal_token",
        ),
    ],
)
def test_h11_protection_detects_semantic_policy_changes(tmp_path, statement, protected_key):
    from swingset.state.db import open_database

    driver = module("accept_h11")
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs VALUES ('run','now',NULL,1,NULL)")
        conn.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES ('watch','eepro','round','GET','https://example.test/round','eepro.round','active')"
        )
        conn.execute(
            "INSERT INTO admission_policies VALUES ('eepro.round','1','shadow','revision',NULL,NULL,NULL)"
        )
        conn.execute(
            "INSERT INTO source_units VALUES ('unit','watch','eepro.round','desired',NULL,NULL,'none')"
        )
        conn.execute(
            "INSERT INTO source_generations VALUES ('generation','unit','eepro.round','1','fingerprint','{}','{}','{}','{}',NULL,NULL,'now','run','staged','none')"
        )
        conn.execute(
            "INSERT INTO identity_source_refs VALUES ('ref','eepro','event','contest','round','participant','entry','{}','now')"
        )
        before = driver.controls(conn)
        conn.execute(statement)
        after = driver.controls(conn)
        assert before["tables"][protected_key] != after["tables"][protected_key]
        assert before["tables"]["accepted_inputs"] == after["tables"]["accepted_inputs"]
        assert before["tables"]["identity_decisions"] == after["tables"]["identity_decisions"]
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize(
    "location",
    [
        "checkpoint",
        "custom_checkpoint",
        "candidate",
        "baseline",
        "source",
        "base_source",
        "reviewed_source",
        "symlink_checkpoint",
        "doctor_symlink",
    ],
)
def test_h11_receipt_output_protection_precedes_preflight(tmp_path, monkeypatch, location):
    driver = module("accept_h11")
    state, source = tmp_path / "live", tmp_path / "source"
    checkpoint = tmp_path / "frozen-evidence"
    checkpoint.mkdir()
    (checkpoint / "checkpoint.json").write_text("{}")
    candidate = state / "candidates/v4"
    candidate.mkdir(parents=True)
    (state / "baseline").symlink_to(candidate)
    source.mkdir()
    base_source, reviewed_source = tmp_path / "v4-source", tmp_path / "working-source"
    base_source.mkdir()
    reviewed_source.mkdir()
    (source / "h11-source.json").write_text(
        json.dumps({"base": str(base_source), "reviewed": str(reviewed_source)})
    )
    symlink = tmp_path / "evidence-alias"
    symlink.symlink_to(checkpoint)
    locations = {
        "checkpoint": state / "checkpoints/frozen",
        "custom_checkpoint": checkpoint,
        "candidate": candidate,
        "baseline": state / "baseline",
        "source": source,
        "base_source": base_source,
        "reviewed_source": reviewed_source,
        "symlink_checkpoint": symlink,
        "doctor_symlink": tmp_path / "operations",
    }
    output = locations[location] / "receipt.json"
    if location == "doctor_symlink":
        output.parent.mkdir()
        output.with_suffix(".doctor.json").symlink_to(checkpoint / "new-doctor.json")
    monkeypatch.setattr(
        driver, "preflight", lambda *_: pytest.fail("unsafe output must fail first")
    )
    monkeypatch.setattr(
        driver.sys,
        "argv",
        [
            "accept_h11",
            "--state",
            str(state),
            "--source",
            str(source),
            "--gate",
            str(tmp_path / "gate.json"),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(ValueError, match="receipt output"):
        driver.main()
    assert not output.exists()
    assert not (checkpoint / "new-doctor.json").exists()
    assert not (state / "state.lock").exists()


@pytest.mark.parametrize("invalid", ["string", "size", "sha", "boolean_size", "missing_size"])
def test_h11_checkpoint_record_shape_and_physical_integrity_are_strict(tmp_path, invalid):
    driver = module("accept_h11")
    file = tmp_path / "artifact"
    file.write_bytes(b"retained bytes")
    valid = {"size": file.stat().st_size, "sha256": driver.digest(file)}
    driver.verify_checkpoint_files(tmp_path, {file.name: valid})
    records = {
        "string": valid["sha256"],
        "size": {**valid, "size": valid["size"] + 1},
        "sha": {**valid, "sha256": "0" * 64},
        "boolean_size": {**valid, "size": True},
        "missing_size": {"sha256": valid["sha256"]},
    }
    with pytest.raises(ValueError):
        driver.verify_checkpoint_files(tmp_path, {file.name: records[invalid]})
    with pytest.raises(ValueError, match="require SHA256 strings"):
        driver.verify_files(tmp_path, {file.name: valid})
