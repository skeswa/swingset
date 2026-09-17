"""Fixture gate setup is byte-bound, single-use and read-only toward live state."""

import fcntl
import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from journal.tools.admission import prepare_fixture_exception as preparation

PACKET = Path("journal/evidence/admission/fixture-exception-2026-09-16")
NOW = datetime(2026, 9, 17, 15, 40, tzinfo=UTC)


@pytest.fixture
def specimen(tmp_path, monkeypatch):
    packet = tmp_path / "packet"
    packet.mkdir()
    shutil.copyfile(PACKET / preparation.DRIVER, packet / preparation.DRIVER)
    shutil.copytree(PACKET / "helper-closure", packet / "helper-closure")
    state = tmp_path / "state"
    state.mkdir()
    (state / "state.lock").touch()
    (state / "operator-hold").write_text("retain this hold")
    baseline = state / preparation.BASELINE_CANDIDATE
    (baseline / "_meta").mkdir(parents=True)
    (baseline / "PUBLISHED").write_text(json.dumps({"commit": preparation.BASELINE_COMMIT}))
    (baseline / "_meta/manifest.json").write_text("{}")
    (state / "baseline").symlink_to(baseline, target_is_directory=True)
    with sqlite3.connect(state / "state.sqlite") as conn:
        conn.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            INSERT INTO meta VALUES('schema_version','14');
            CREATE TABLE host_budget(host TEXT,day TEXT,requests INTEGER,bytes INTEGER);
            CREATE TABLE hosts(host TEXT,paused_until TEXT,next_allowed_at TEXT);
            CREATE TABLE operator_pauses(scope_kind TEXT,scope_id TEXT,until_at TEXT);
            CREATE TABLE pending_work(id TEXT);
            INSERT INTO pending_work VALUES('unrelated production parse backlog');
        """)
    # This fixture exercises construction without pretending it supplies an
    # installed Nix runtime. Real receipt verification is tested independently.
    monkeypatch.setattr(preparation, "verify_runtime", lambda source: 638)
    return dict(
        packet=packet,
        state=state,
        quarantine=tmp_path / "quarantine",
        approved_by="offline-test",
        approved_at=NOW - timedelta(minutes=1),
        decision="synthetic offline decision only",
        publication="synthetic published receipt",
        now=NOW,
    )


def test_setup_preserves_state_and_emits_exact_execution_pins(specimen):
    state, packet = specimen["state"], specimen["packet"]
    before = preparation.sha(state / "state.sqlite")
    result = preparation.prepare(**specimen)
    assert preparation.sha(state / "state.sqlite") == before
    assert not specimen["quarantine"].exists()
    assert result["executed"] is False and result["network_requests"] == 0
    assert result["state_observation"]["pending_work"] == 1
    assert result["state_observation"]["archive_requests_remaining_under_200"] == 200
    gate = json.loads((packet / "execution-gate.json").read_bytes())
    assert gate["source"] == str(preparation.SOURCE) and gate["schema"] == 14
    assert gate["driver_sha256"] == preparation.DRIVER_SHA
    assert gate["helper_root"] == str(packet / "helper-closure")
    assert gate["source_receipt_sha256"] == preparation.SOURCE_RECEIPT_SHA
    authorization = json.loads((packet / "authorization.json").read_bytes())
    assert authorization["manifest_canonical_sha256"] == preparation.MANIFEST_SHA
    assert authorization["approval"] == "approved_new_source_fixture_exception_only"
    assert result["command"][0] == "/run/current-system/sw/bin/env"
    assert result["command"][-1] == "--execute"
    assert (packet / "execute.sh").read_text().count("--execute") == 1
    with pytest.raises(ValueError, match="single-use"):
        preparation.prepare(**specimen)


@pytest.mark.parametrize("damage", ["driver", "helper", "extra", "symlink"])
def test_changed_packet_cannot_supply_authorizing_gate(specimen, damage):
    packet = specimen["packet"]
    if damage == "driver":
        (packet / preparation.DRIVER).write_text("changed")
    elif damage == "helper":
        (packet / "helper-closure/fixture_helpers/__init__.py").write_text("changed")
    elif damage == "extra":
        (packet / "helper-closure/extra.pyc").write_bytes(b"unreviewed")
    else:
        (packet / "helper-closure/link").symlink_to(packet / preparation.DRIVER)
    with pytest.raises(ValueError):
        preparation.prepare(**specimen)
    assert not (packet / "authorization.json").exists()


@pytest.mark.parametrize(
    "condition", ["migrated", "hold_removed", "restore", "budget", "baseline", "quarantine"]
)
def test_live_gates_fail_before_any_preparation_receipt(specimen, condition):
    state, packet = specimen["state"], specimen["packet"]
    with sqlite3.connect(state / "state.sqlite") as conn:
        if condition == "migrated":
            conn.execute("UPDATE meta SET value='26'")
        elif condition == "budget":
            conn.execute("INSERT INTO host_budget VALUES('web.archive.org','2026-09-17',200,10)")
    if condition == "hold_removed":
        (state / "operator-hold").unlink()
    elif condition == "restore":
        (state / "RESTORE_PENDING").touch()
    elif condition == "baseline":
        (state / "baseline/PUBLISHED").write_text('{"commit":"other"}')
    elif condition == "quarantine":
        specimen["quarantine"] = state / "operations/fixtures"
    with pytest.raises(ValueError):
        preparation.prepare(**specimen)
    assert not (packet / "authorization.json").exists()


def test_running_worker_lock_blocks_setup_and_budgets_are_not_reset(specimen):
    state = specimen["state"]
    with (state / "state.lock").open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            preparation.prepare(**specimen)
    with sqlite3.connect(state / "state.sqlite") as conn:
        conn.execute("INSERT INTO host_budget VALUES('web.archive.org','2026-09-17',197,1234)")
        conn.execute("INSERT INTO operator_pauses VALUES('source','dcn',NULL)")
    result = preparation.prepare(**specimen)
    assert result["state_observation"]["archive_requests_remaining_under_200"] == 3
    assert result["state_observation"]["operator_pauses"][0]["scope_id"] == "dcn"
    with sqlite3.connect(state / "state.sqlite") as conn:
        assert conn.execute("SELECT requests,bytes FROM host_budget").fetchone() == (197, 1234)


def test_unverified_runtime_receipt_is_rejected(tmp_path):
    (tmp_path / "h16-source.json").write_text('{"files":{}}')
    with pytest.raises(ValueError, match="receipt changed"):
        preparation.verify_runtime(tmp_path)
