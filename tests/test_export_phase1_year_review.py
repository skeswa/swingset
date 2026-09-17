"""Tests for the read-only parser-8 year-review exporter."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "journal/tools/admission/export_phase1_year_review.py"
SPEC = importlib.util.spec_from_file_location("export_phase1_year_review", PATH)
assert SPEC is not None and SPEC.loader is not None
helper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = helper
SPEC.loader.exec_module(helper)


def _review_report(now: str, *, accepted: bool = False) -> dict[str, object]:
    return {
        "generated_at": now,
        "run_id": "read-only-review",
        "catalog_targets": 213,
        "target_status_counts": {
            "pending": 0,
            "parsed": 213,
            "empty": 0,
            "duplicate": 0,
            "finding": 0,
        },
        "years": [
            {"year": year, "events_accepted": accepted if year == 2010 else False}
            for year in range(2010, 2027)
        ],
        "acceptance": (
            "Owner review is required. This pack does not set events_accepted or authorize phase2."
        ),
    }


def _write_review(output: Path, report: dict[str, object]) -> None:
    output.mkdir()
    for name in helper.REVIEW_FILES:
        body = helper.canonical(report) if name == "review.json" else (name + "\n").encode()
        (output / name).write_bytes(body)


def _write_fake_runtime(root: Path, marker: str) -> None:
    package = root / "src/swingset"
    (package / "history").mkdir(parents=True)
    (package / "state").mkdir()
    (package / "__init__.py").write_text(f"MARKER = {marker!r}\n")
    (package / "history/__init__.py").write_text("")
    (package / "history/review.py").write_text(
        f"MARKER = {marker!r}\ndef review_pack(*args, **kwargs):\n    return {{}}\n"
    )
    (package / "state/__init__.py").write_text("")
    (package / "state/db.py").write_text(
        f"MARKER = {marker!r}\nSCHEMA_VERSION = 29\nclass Database:\n    pass\n"
    )


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    from swingset.state.db import Database, open_database

    monkeypatch.setattr(helper, "SCRATCH_ROOT", tmp_path)
    monkeypatch.setattr(helper, "SCRATCH_PREFIX", "scratch-")
    monkeypatch.setattr(helper, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(helper, "OUTPUT_PREFIX", "export-")
    state = tmp_path / "scratch-completed"
    with open_database(state) as database:
        run_id = database.start_run(datetime(2026, 9, 17, 11, tzinfo=UTC), dry_run=True)
        assert run_id == "run_20260917T110000Z"
    catalog = {"version": 1, "targets": {f"target-{index}": {} for index in range(213)}}
    predecessor = {
        "version": 1,
        "run_id": "predecessor",
        "updated_at": "2026-09-17T10:00:00+00:00",
        "targets": {f"target-{index}": {"status": "pending"} for index in range(213)},
    }
    successor = {
        **predecessor,
        "run_id": run_id,
        "targets": {f"target-{index}": {"status": "parsed"} for index in range(213)},
    }
    catalog_body = helper.canonical(catalog)
    predecessor_body = helper.canonical(predecessor)
    successor_body = helper.canonical(successor)
    (state / "phase1-catalog.json").write_bytes(catalog_body)
    (state / "phase1-ledger.json").write_bytes(predecessor_body)
    (state / "phase1-ledger-parser8.json").write_bytes(successor_body)
    with contextlib.closing(sqlite3.connect(state / "state.sqlite")) as conn:
        table_hashes = helper.all_table_hashes(conn)
    monkeypatch.setattr(helper, "CATALOG_SHA256", helper.hashlib.sha256(catalog_body).hexdigest())
    monkeypatch.setattr(
        helper,
        "PREDECESSOR_LEDGER_SHA256",
        helper.hashlib.sha256(predecessor_body).hexdigest(),
    )
    monkeypatch.setattr(
        helper,
        "SUCCESSOR_LEDGER_SHA256",
        helper.hashlib.sha256(successor_body).hexdigest(),
    )
    replay = {
        "format": "phase1-newsletter-parser8-replay-v1",
        "passed": True,
        "source": str(helper.SOURCE),
        "source_receipt_sha256": helper.SOURCE_RECEIPT_SHA256,
        "catalog": {"rows": 213, "sha256": helper.CATALOG_SHA256},
        "ledger_before_sha256": helper.PREDECESSOR_LEDGER_SHA256,
        "ledger_after_sha256": helper.SUCCESSOR_LEDGER_SHA256,
        "original_ledger_preserved": True,
        "targets": [{} for _index in range(28)],
        "invariants": {"after": {"accepted_year_count": 0}},
        "table_hashes": {"after": table_hashes},
        "replay_run_id": run_id,
        "network_requests": 0,
        "subprocesses": 0,
        "production_operations": 0,
        "production_acceptance": False,
        "year_acceptance": False,
        "publication": False,
    }
    replay_body = helper.canonical(replay)
    (state / "phase1-newsletter-parser8-receipt.json").write_bytes(replay_body)
    monkeypatch.setattr(
        helper, "REPLAY_RECEIPT_SHA256", helper.hashlib.sha256(replay_body).hexdigest()
    )

    calls: list[dict[str, object]] = []

    def review_pack(database, output, *, now, reconcile):
        assert reconcile is False
        assert (
            helper.sha(database.state_dir / "phase1-ledger.json") == helper.SUCCESSOR_LEDGER_SHA256
        )
        assert database.connection.execute("PRAGMA query_only").fetchone()[0] == 1
        report = _review_report(now)
        _write_review(output, report)
        calls.append({"now": now, "state": database.state_dir})
        return report

    monkeypatch.setattr(helper, "load_runtime", lambda: (review_pack, Database))
    return SimpleNamespace(
        state=state,
        output=tmp_path / "export-current",
        calls=calls,
        Database=Database,
        replay=replay,
    )


def test_required_external_pins_are_exact():
    assert helper.SOURCE == Path("/nix/store/rgyryll4d55rgzscdqhjcwmkr325a76f-source")
    assert (
        helper.SOURCE_RECEIPT_SHA256
        == "a2704c7c99ce5aef4fbd0f0df8b23343be5df2a688945e236b8e6856b320ad5f"
    )
    assert (
        helper.REPLAY_RECEIPT_SHA256
        == "65f3627429d2ae47f3e5b1216d16c9ee19e365a583abbd21a954d885379765e2"
    )
    assert (
        helper.SUCCESSOR_LEDGER_SHA256
        == "6db1bf303401537b2927ffbbaa5046e222d20c87cbb7ae75cf5860368289773c"
    )
    assert helper.SCHEMA == 29
    assert helper.APPLICATION_TABLES == 117


def test_table_hash_matches_replay_receipt_v1_fixture():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE sample(key TEXT PRIMARY KEY,payload BLOB,value REAL,note TEXT COLLATE NOCASE)"
    )
    conn.executemany(
        "INSERT INTO sample VALUES (?,?,?,?)",
        (
            (None, b"\x00\xff", -0.0, "a"),
            (None, None, float("inf"), "A"),
            ("key", b"x", -3.5, None),
        ),
    )
    assert helper.table_hash(conn, "sample") == {
        "rows": 3,
        "sha256": "7b00a4be8ec2d35c12a02c3396ee31c46a0515d6eb0e57b7b22a661930daeaca",
    }


def test_export_uses_successor_ledger_and_preserves_database(prepared):
    before = helper.database_closure(prepared.state)
    receipt = helper.export(prepared.state, prepared.output)
    assert helper.database_closure(prepared.state) == before
    assert len(prepared.calls) == 1
    assert prepared.calls[0]["now"] == "2026-09-17T11:00:00.000000Z"
    assert not (prepared.output / ".review-input").exists()
    assert set(path.name for path in prepared.output.iterdir()) == {"year-review", "receipt.json"}
    assert json.loads((prepared.output / "receipt.json").read_bytes()) == receipt
    assert set(receipt["outputs"]) == helper.REVIEW_FILES
    assert receipt["history_acceptance_rows"] == 0
    assert receipt["table_hashes"]["rows"] == 117
    assert receipt["year_acceptance"] is False
    assert receipt["production_operations"] == 0
    assert receipt["publication"] is False


def test_review_failure_removes_partial_output(prepared, monkeypatch):
    def fail(_database, output, *, now, reconcile):
        assert now and reconcile is False
        output.mkdir()
        (output / "partial.csv").write_text("partial")
        raise RuntimeError("injected review failure")

    monkeypatch.setattr(helper, "load_runtime", lambda: (fail, prepared.Database))
    with pytest.raises(RuntimeError, match="injected"):
        helper.export(prepared.state, prepared.output)
    assert not prepared.output.exists()


def test_sql_write_attempt_is_denied_and_database_is_unchanged(prepared, monkeypatch):
    before = helper.database_closure(prepared.state)

    def write(database, _output, *, now, reconcile):
        assert now and reconcile is False
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('forbidden','now',1)"
        )
        return {}

    monkeypatch.setattr(helper, "load_runtime", lambda: (write, prepared.Database))
    with pytest.raises(sqlite3.DatabaseError):
        helper.export(prepared.state, prepared.output)
    assert not prepared.output.exists()
    assert helper.database_closure(prepared.state) == before
    with contextlib.closing(sqlite3.connect(prepared.state / "state.sqlite")) as conn:
        assert conn.execute("SELECT 1 FROM runs WHERE run_id='forbidden'").fetchone() is None


@pytest.mark.parametrize(
    "filename",
    [
        "phase1-catalog.json",
        "phase1-ledger.json",
        "phase1-ledger-parser8.json",
        "phase1-newsletter-parser8-receipt.json",
    ],
)
def test_every_pinned_input_mismatch_fails_before_output(prepared, filename):
    with (prepared.state / filename).open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="hash differs"):
        helper.export(prepared.state, prepared.output)
    assert not prepared.output.exists()
    assert prepared.calls == []


def test_existing_output_is_rejected_without_changes(prepared):
    prepared.output.mkdir()
    sentinel = prepared.output / "sentinel"
    sentinel.write_text("keep")
    with pytest.raises(ValueError, match="must be new"):
        helper.export(prepared.state, prepared.output)
    assert sentinel.read_text() == "keep"


def test_state_path_rejects_relative_wrong_root_and_symlink(prepared, tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        helper.verify_state_path(Path("relative"), prepared.output)
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    with pytest.raises(ValueError, match="explicit disposable"):
        helper.verify_state_path(wrong, prepared.output)
    link = tmp_path / "scratch-link"
    link.symlink_to(prepared.state, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        helper.verify_state_path(link, prepared.output)


def test_output_rejects_other_roots_and_symlinks(prepared, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="fresh /var/tmp"):
        helper.verify_state_path(prepared.state, other / "export-wrong-root")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        helper.verify_state_path(prepared.state, linked_parent / "export-linked")
    output_link = prepared.output
    output_link.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="must be new"):
        helper.verify_state_path(prepared.state, output_link)


def test_state_and_output_reject_production_paths(prepared, monkeypatch):
    monkeypatch.setattr(helper, "PRODUCTION_ROOT", prepared.state)
    with pytest.raises(ValueError, match="production state"):
        helper.verify_state_path(prepared.state, prepared.output)
    monkeypatch.setattr(helper, "PRODUCTION_ROOT", prepared.output)
    with pytest.raises(ValueError, match="production output"):
        helper.verify_state_path(prepared.state, prepared.output)


@pytest.mark.parametrize("defect", ["schema", "tables", "acceptance"])
def test_database_authority_defects_fail_and_cleanup(prepared, defect):
    with contextlib.closing(sqlite3.connect(prepared.state / "state.sqlite")) as conn:
        if defect == "schema":
            conn.execute("PRAGMA user_version=28")
        elif defect == "tables":
            conn.execute("CREATE TABLE unexpected_table(value TEXT)")
        else:
            conn.execute(
                "INSERT INTO history_acceptance VALUES "
                "(2010,'2026-09-17T00:00:00+00:00','owner','digest')"
            )
        conn.commit()
    with pytest.raises(ValueError):
        helper.export(prepared.state, prepared.output)
    assert not prepared.output.exists()


def test_arbitrary_table_content_mutation_fails_replay_binding(prepared):
    with contextlib.closing(sqlite3.connect(prepared.state / "state.sqlite")) as conn:
        conn.execute(
            "UPDATE runs SET summary_json='tampered' WHERE run_id=?",
            (prepared.replay["replay_run_id"],),
        )
        conn.commit()
    with pytest.raises(ValueError, match="completed replay tables: runs"):
        helper.export(prepared.state, prepared.output)
    assert not prepared.output.exists()


def test_review_cannot_imply_year_acceptance(prepared, monkeypatch):
    def accepted(_database, output, *, now, reconcile):
        assert reconcile is False
        report = _review_report(now, accepted=True)
        _write_review(output, report)
        return report

    monkeypatch.setattr(helper, "load_runtime", lambda: (accepted, prepared.Database))
    with pytest.raises(ValueError, match="authority differs"):
        helper.export(prepared.state, prepared.output)
    assert not prepared.output.exists()


def test_wrong_or_linked_review_output_is_removed(prepared, monkeypatch):
    def linked(_database, output, *, now, reconcile):
        assert now and reconcile is False
        report = _review_report(now)
        _write_review(output, report)
        (output / "events.csv").unlink()
        (output / "events.csv").symlink_to(output / "years.csv")
        return report

    monkeypatch.setattr(helper, "load_runtime", lambda: (linked, prepared.Database))
    with pytest.raises(ValueError, match="closure differs"):
        helper.export(prepared.state, prepared.output)
    assert not prepared.output.exists()


def test_import_binding_accepts_only_candidate_source(tmp_path, monkeypatch):
    source = tmp_path / "source"
    module_path = source / "src/swingset/history/review.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# pinned")
    monkeypatch.setattr(helper, "SOURCE", source)
    helper.verify_runtime_modules(
        SimpleNamespace(__name__="swingset.history.review", __file__=str(module_path))
    )
    foreign = tmp_path / "foreign.py"
    foreign.write_text("# mixed")
    with pytest.raises(ValueError, match="mixed imported runtime"):
        helper.verify_runtime_modules(
            SimpleNamespace(__name__="swingset.history.review", __file__=str(foreign))
        )


def test_load_runtime_injects_candidate_before_importable_alternate(tmp_path):
    candidate = tmp_path / "candidate"
    alternate = tmp_path / "alternate"
    _write_fake_runtime(candidate, "candidate")
    _write_fake_runtime(alternate, "alternate")
    receipt = candidate / "extension-source.json"
    receipt.write_text("candidate receipt")
    code = f"""
import hashlib, importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location('export_review', {str(PATH)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
candidate = pathlib.Path({str(candidate)!r})
alternate = pathlib.Path({str(alternate)!r})
module.SOURCE = candidate
module.SOURCE_RECEIPT_SHA256 = hashlib.sha256(
    (candidate / 'extension-source.json').read_bytes()
).hexdigest()
sys.path.insert(0, str(alternate / 'src'))
review_pack, database = module.load_runtime()
print(json.dumps({{
    'candidate_first': sys.path[0] == str(candidate / 'src'),
    'package_path': sys.modules['swingset'].__file__,
    'review_marker': sys.modules[review_pack.__module__].MARKER,
    'database_marker': sys.modules[database.__module__].MARKER,
}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    )
    observed = json.loads(result.stdout)
    assert observed == {
        "candidate_first": True,
        "package_path": str(candidate / "src/swingset/__init__.py"),
        "review_marker": "candidate",
        "database_marker": "candidate",
    }


def test_load_runtime_rejects_preloaded_alternate_before_injection(tmp_path):
    candidate = tmp_path / "candidate"
    alternate = tmp_path / "alternate"
    _write_fake_runtime(candidate, "candidate")
    _write_fake_runtime(alternate, "alternate")
    receipt = candidate / "extension-source.json"
    receipt.write_text("candidate receipt")
    code = f"""
import hashlib, importlib.util, pathlib, sys
candidate = pathlib.Path({str(candidate)!r})
alternate = pathlib.Path({str(alternate)!r})
sys.path.insert(0, str(alternate / 'src'))
import swingset
spec = importlib.util.spec_from_file_location('export_review', {str(PATH)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.SOURCE = candidate
module.SOURCE_RECEIPT_SHA256 = hashlib.sha256(
    (candidate / 'extension-source.json').read_bytes()
).hexdigest()
try:
    module.load_runtime()
except ValueError as error:
    print(str(error))
    print(str(candidate / 'src') in sys.path)
else:
    raise AssertionError('preloaded alternate runtime was accepted')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    )
    assert result.stdout.splitlines() == ["mixed imported runtime module: swingset", "False"]


@pytest.mark.parametrize("alias", ["exact", "trailing", "symlink"])
def test_load_runtime_rejects_resolved_candidate_path_aliases(tmp_path, alias):
    candidate = tmp_path / "candidate"
    _write_fake_runtime(candidate, "candidate")
    receipt = candidate / "extension-source.json"
    receipt.write_text("candidate receipt")
    symlink = tmp_path / "candidate-src-link"
    symlink.symlink_to(candidate / "src", target_is_directory=True)
    code = f"""
import hashlib, importlib.util, os, pathlib, sys
candidate = pathlib.Path({str(candidate)!r})
alias = {alias!r}
entries = {{
    'exact': candidate / 'src',
    'trailing': str(candidate / 'src') + os.sep,
    'symlink': {str(symlink)!r},
}}
sys.path.insert(0, entries[alias])
spec = importlib.util.spec_from_file_location('export_review', {str(PATH)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.SOURCE = candidate
module.SOURCE_RECEIPT_SHA256 = hashlib.sha256(
    (candidate / 'extension-source.json').read_bytes()
).hexdigest()
try:
    module.load_runtime()
except ValueError as error:
    print(str(error))
else:
    raise AssertionError('candidate runtime alias was accepted')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    )
    assert result.stdout.strip() == "candidate runtime path already injected"


def test_read_only_uri_is_wal_aware_and_not_immutable(prepared, monkeypatch):
    real_connect = helper.sqlite3.connect
    seen: list[str] = []

    def connect(database_uri, **kwargs):
        seen.append(str(database_uri))
        return real_connect(database_uri, **kwargs)

    monkeypatch.setattr(helper.sqlite3, "connect", connect)
    conn = helper.open_read_only_database(prepared.state)
    conn.close()
    assert seen and seen[0].endswith("?mode=ro")
    assert "immutable=1" not in seen[0]


def test_offline_hook_denies_dns_socket_and_process_routes():
    code = f"""
import importlib.util, socket, subprocess, sys
spec=importlib.util.spec_from_file_location('export_review', {str(PATH)!r})
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
module.install_offline_audit_hook()
blocked=0
for operation in (
    lambda: socket.getaddrinfo('example.com', 443),
    lambda: socket.socket().connect(('127.0.0.1', 9)),
    lambda: subprocess.Popen(['/bin/true']),
):
    try:
        operation()
    except RuntimeError:
        blocked += 1
print(blocked)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    )
    assert result.stdout.strip() == "3"
