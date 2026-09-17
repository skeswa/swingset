"""The held schema29 input gate has one exact, sealed mutation boundary."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
PATH = ROOT / "journal/tools/runtime/accept_held_schema29_inputs.py"
SPEC = importlib.util.spec_from_file_location("accept_held_schema29_inputs", PATH)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def receipt(value: str) -> dict[str, Any]:
    return {"rows": 1, "sha256": value, "columns": ["value"]}


def snapshot(value: str) -> dict[str, Any]:
    accepted = {
        name: pair[0 if value == "before" else 1] for name, pair in gate.EXPECTED_CHANGES.items()
    }
    accepted.update({f"retained/{number}": str(number) for number in range(39)})
    return {
        "tables": {
            "accepted_inputs": receipt(value),
            "meta": receipt(value),
            "judges": receipt("retained"),
        },
        "judges": {"rows": 4931, "sha256": "judges"},
        "accepted": accepted,
    }


def test_exact_candidate006_authorities_are_compiled_in() -> None:
    assert (
        gate.POSTMIGRATION_SHA256
        == "81534a3b61c305afaddd195eecaa8dcae40e6a20323b610d042199a67c8d39c3"
    )
    assert gate.PACKET_SHA256 == "0d455559b7e0cd767cb1739f10271eb12e8178bebb5a4ab283bd8e74733b6ff3"
    assert (
        gate.INPUT_ACCEPT_SHA256
        == "9ba0ca7bed02b165ca64fc5595ea3466ca28358740e52498a2fe11770b27beff"
    )
    assert (
        gate.INPUT_DRAIN_SHA256
        == "b47e7faff25181a01c53d869a98ccd25e60ce622e880290ae83955c29af4c02f"
    )
    assert (
        gate.ACTUAL_REVIEW_SHA256
        == "efe8547205a7d938dbc65a9f720cf47635ddd78cbe7f4ab7833099cf307b803d"
    )
    assert gate.OLD_BUNDLE == "fe99b47cd65eb909abc7c9ebd64d9ace9524393e5af8cd4d9368c55317ce3c6a"
    assert gate.TARGET_BUNDLE == "abdf538777c1e4fc05c7f8b9079701bd5a37f276cabe83a8be023673a941d644"
    assert len(gate.EXPECTED_CHANGES) == 12
    assert gate.STATE == Path("/var/lib/swingset")


def test_exact_retained_evidence_closure_passes_semantic_validation() -> None:
    migration = ROOT / "journal/evidence/runtime/held-schema29-migration-2026-09-17"
    rehearsals = ROOT / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17"
    paths = {
        "postmigration": migration / "postmigration-review-001/receipt.json",
        "packet": rehearsals / "packet-005/packet.json",
        "input_accept": rehearsals / "inputs-004/accept.json",
        "input_drain": rehearsals / "inputs-004/drain-001.json",
        "actual_review": rehearsals / "actual-review-005/receipt.json",
    }
    document = {
        "evidence": {
            name: {"path": str(path), "sha256": gate.EVIDENCE_SHA256[name]}
            for name, path in paths.items()
        }
    }
    assert gate.validate_evidence(document, ROOT / "gate.json") == paths


def test_disk_shadow_uses_var_tmp_ignores_tmpdir_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.sqlite"
    source = sqlite3.connect(source_path)
    source.execute("CREATE TABLE specimen(value TEXT PRIMARY KEY)")
    source.execute("INSERT INTO specimen VALUES ('retained')")
    source.commit()
    source.close()
    before = gate.sha(source_path)
    trap = tmp_path / "hostile-default-tmp"
    trap.mkdir()
    monkeypatch.setenv("TMPDIR", str(trap))
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    shadow_directory: Path | None = None
    with gate.disk_shadow(source) as (shadow, directory):
        shadow_directory = directory
        database = directory / "shadow.sqlite"
        assert directory.parent == Path("/var/tmp")
        assert not directory.is_symlink() and not database.is_symlink()
        assert database.stat().st_nlink == 1
        assert database.stat().st_mode & 0o777 == 0o600
        assert shadow.execute("SELECT value FROM specimen").fetchone()[0] == "retained"
        (directory / "state").mkdir()
        (directory / "state/captured").write_text("temporary")
    source.close()
    assert shadow_directory is not None and not shadow_directory.exists()
    assert list(trap.iterdir()) == []
    assert gate.sha(source_path) == before
    assert '":memory:"' not in PATH.read_text()


def test_disk_shadow_cleans_up_after_failure(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite"
    source = sqlite3.connect(source_path)
    source.execute("CREATE TABLE specimen(value TEXT)")
    source.commit()
    seen: list[Path] = []
    with pytest.raises(RuntimeError, match="injected seal failure"):
        with gate.disk_shadow(source) as (_shadow, directory):
            seen.append(directory)
            (directory / "state").mkdir()
            (directory / "state/partial").write_text("temporary")
            raise RuntimeError("injected seal failure")
    source.close()
    assert len(seen) == 1 and not seen[0].exists()


def test_disk_shadow_rejects_non_var_tmp_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = sqlite3.connect(":memory:")
    monkeypatch.setattr(gate, "SHADOW_ROOT", tmp_path)
    with pytest.raises(ValueError, match="shadow root must be /var/tmp"):
        with gate.disk_shadow(source):
            pytest.fail("non-/var/tmp shadow was opened")
    source.close()
    assert list(tmp_path.iterdir()) == []


def test_transition_preserves_every_table_outside_exact_invalidation_set() -> None:
    before = {
        "accepted_inputs": receipt("old"),
        "pending_work": receipt("old"),
        "meta": receipt("old"),
        "judges": receipt("same"),
    }
    after = {
        "accepted_inputs": receipt("new"),
        "pending_work": receipt("new"),
        "meta": receipt("new"),
        "judges": receipt("same"),
    }
    gate.validate_transition(before, after, {"accepted_inputs", "pending_work", "meta"})
    after["judges"] = receipt("mutated")
    with pytest.raises(ValueError, match="protected table changed"):
        gate.validate_transition(before, after, {"accepted_inputs", "pending_work", "meta"})


def test_new_input_map_rejects_mutated_retained_row() -> None:
    before = snapshot("before")
    after = snapshot("after")
    gate.validate_new_inputs(before, after)
    after["accepted"]["retained/0"] = "replaced-without-changing-row-count"
    with pytest.raises(ValueError, match="retained input differs"):
        gate.validate_new_inputs(before, after)


@pytest.mark.parametrize("extra", ["runs", "control_state", "execution_admissions"])
def test_transition_rejects_unreviewed_table_families(extra: str) -> None:
    before = {"accepted_inputs": receipt("a"), "meta": receipt("a"), extra: receipt("a")}
    after = {"accepted_inputs": receipt("b"), "meta": receipt("b"), extra: receipt("b")}
    with pytest.raises(ValueError, match="protected tables"):
        gate.validate_transition(before, after, {"accepted_inputs", "meta", extra})


class FakeDatabase:
    def __init__(self, state: Path, connection: sqlite3.Connection, lock: object) -> None:
        del state, lock
        self.connection = connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN")
        try:
            yield self.connection
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()


def test_apply_acceptance_invokes_only_capture_and_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    target = tmp_path / "inputs" / gate.TARGET_BUNDLE

    def capture(config: Path, overrides: Path, state: Path, versions: object) -> Any:
        assert config == gate.CONFIG and overrides == gate.OVERRIDES and state == tmp_path
        assert versions == {"reviewed": "versions"}
        target.mkdir(parents=True)
        (target / "manifest.json").write_text("{}")
        calls.append("capture")
        return SimpleNamespace(digest=gate.TARGET_BUNDLE, path=target)

    def accept(database: FakeDatabase, bundle: object, clock: Any) -> set[str]:
        assert database.connection.in_transaction
        assert bundle is not None
        assert clock.now() == datetime(2026, 9, 17, 23, 30, tzinfo=UTC)
        calls.append("accept")
        return set(gate.EXPECTED_CHANGES)

    monkeypatch.setattr(gate, "validate_database", lambda _conn, expected_bundle: snapshot("after"))
    runtime = gate.Runtime(FakeDatabase, capture, accept, lambda: {"reviewed": "versions"})
    conn = sqlite3.connect(":memory:", isolation_level=None)
    after, bundle = gate.apply_acceptance(
        conn, runtime, tmp_path, datetime(2026, 9, 17, 23, 30, tzinfo=UTC)
    )
    assert calls == ["capture", "accept"]
    assert after == snapshot("after")
    assert bundle["digest"] == gate.TARGET_BUNDLE
    assert set(bundle["files"]) == {"manifest.json"}


def test_apply_acceptance_rolls_back_when_runtime_changes_wrong_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "inputs" / gate.TARGET_BUNDLE

    def capture(*_args: object) -> Any:
        target.mkdir(parents=True)
        (target / "manifest.json").write_text("{}")
        return SimpleNamespace(digest=gate.TARGET_BUNDLE, path=target)

    def accept(database: FakeDatabase, _bundle: object, _clock: object) -> set[str]:
        database.connection.execute("UPDATE marker SET value='mutated'")
        return {"unreviewed"}

    monkeypatch.setattr(gate, "validate_database", lambda _conn, expected_bundle: snapshot("after"))
    runtime = gate.Runtime(FakeDatabase, capture, accept, dict)
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE marker(value TEXT)")
    conn.execute("INSERT INTO marker VALUES ('original')")
    with pytest.raises(ValueError, match="different input set"):
        gate.apply_acceptance(conn, runtime, tmp_path, datetime(2026, 9, 17, tzinfo=UTC))
    assert conn.execute("SELECT value FROM marker").fetchone()[0] == "original"


def test_failed_production_acceptance_removes_new_exact_bundle_and_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "inputs" / gate.TARGET_BUNDLE
    body = b"{}"
    expected_files = {
        "manifest.json": {
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    }
    before, after = snapshot("before"), snapshot("after")
    seal = {"before": before, "after": after, "bundle": {"files": expected_files}}
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE marker(value TEXT)")
    conn.execute("INSERT INTO marker VALUES ('original')")

    def capture(*_args: object) -> Any:
        target.mkdir(parents=True, exist_ok=True)
        (target / "manifest.json").write_bytes(body)
        return SimpleNamespace(digest=gate.TARGET_BUNDLE, path=target)

    def inspect(connection: sqlite3.Connection, *, expected_bundle: str) -> dict[str, Any]:
        value = connection.execute("SELECT value FROM marker").fetchone()[0]
        if expected_bundle == gate.OLD_BUNDLE:
            assert value == "original"
            return before
        assert value == "accepted"
        return after

    def reject(database: FakeDatabase, _bundle: object, _clock: object) -> set[str]:
        database.connection.execute("UPDATE marker SET value='accepted'")
        return {"unexpected"}

    monkeypatch.setattr(gate, "validate_database", inspect)
    rejected = gate.Runtime(FakeDatabase, capture, reject, dict)
    with pytest.raises(ValueError, match="accepted input set differs"):
        gate.execute_sealed_acceptance(
            conn,
            rejected,
            tmp_path,
            datetime(2026, 9, 17, tzinfo=UTC),
            seal,
            lambda: None,
        )
    assert conn.execute("SELECT value FROM marker").fetchone()[0] == "original"
    assert not target.exists()

    def accept(database: FakeDatabase, _bundle: object, _clock: object) -> set[str]:
        database.connection.execute("UPDATE marker SET value='accepted'")
        return set(gate.EXPECTED_CHANGES)

    accepted = gate.Runtime(FakeDatabase, capture, accept, dict)
    result, _bundle, changed_inputs, changed_tables = gate.execute_sealed_acceptance(
        conn,
        accepted,
        tmp_path,
        datetime(2026, 9, 17, tzinfo=UTC),
        seal,
        lambda: None,
    )
    assert result == after
    assert changed_inputs == set(gate.EXPECTED_CHANGES)
    assert changed_tables == {"accepted_inputs", "meta"}
    assert target.is_dir()


def test_exact_completed_acceptance_can_recover_a_missing_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "inputs" / gate.TARGET_BUNDLE
    target.mkdir(parents=True)
    (target / "manifest.json").write_text("{}")
    before, after = snapshot("before"), snapshot("after")
    seal = {
        "before": before,
        "after": after,
        "bundle": {"files": gate.input_inventory(target)},
    }
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('input_bundle_hash',?)", (gate.TARGET_BUNDLE,))
    monkeypatch.setattr(gate, "validate_database", lambda *_args, **_kwargs: after)
    assert gate.recover_completed_acceptance(conn, tmp_path, seal) == after
    mutated = snapshot("after")
    mutated["accepted"]["retained/0"] = "changed"
    monkeypatch.setattr(gate, "validate_database", lambda *_args, **_kwargs: mutated)
    with pytest.raises(ValueError, match="retained input differs"):
        gate.recover_completed_acceptance(conn, tmp_path, seal)


def test_execution_seal_must_come_from_a_distinct_reviewed_gate(
    tmp_path: Path,
) -> None:
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text(json.dumps({"before": snapshot("before")}))
    preflight_sha = gate.sha(preflight_path)
    seal = {
        "format": gate.SEAL_FORMAT,
        "passed": True,
        "finished_at": "2026-09-17T23:40:00+00:00",
        "gate_sha256": "a" * 64,
        "preflight_sha256": preflight_sha,
        "helper_sha256": "b" * 64,
        "source": str(gate.SOURCE),
        "system": str(gate.SYSTEM),
        "old_bundle": gate.OLD_BUNDLE,
        "target_bundle": gate.TARGET_BUNDLE,
        "accepted_at": "2026-09-17T23:30:00+00:00",
        "accepted_inputs": sorted(gate.EXPECTED_CHANGES),
        "changed_tables": ["accepted_inputs", "meta"],
        "before": snapshot("before"),
        "after": snapshot("after"),
    }
    path = tmp_path / "seal.json"
    path.write_text(json.dumps(seal))
    digest = gate.sha(path)
    execution_gate = {
        "seal": {"path": str(path), "sha256": digest},
        "preflight": {"path": str(preflight_path), "sha256": preflight_sha},
        "seal_gate_sha256": "a" * 64,
        "accepted_at": seal["accepted_at"],
    }
    validated, actual = gate.validate_seal(
        execution_gate, tmp_path / "gate.json", "c" * 64, "b" * 64
    )
    assert validated == seal and actual == digest
    with pytest.raises(ValueError, match="cannot reuse"):
        gate.validate_seal(execution_gate, tmp_path / "gate.json", "a" * 64, "b" * 64)


def test_offline_audit_allows_only_read_only_unit_inspection() -> None:
    command = [
        str(gate.SYSTEMCTL),
        "show",
        gate.UNITS[0],
        "--property=ActiveState",
        "--value",
    ]
    gate.offline_audit("subprocess.Popen", (str(gate.SYSTEMCTL), command, None, None))
    with pytest.raises(ValueError, match="forbids subprocess"):
        gate.offline_audit("subprocess.Popen", ("python", ["python", "worker.py"], None, None))
    with pytest.raises(RuntimeError, match="forbids operation"):
        gate.offline_audit("socket.connect", (object(), ("example.com", 443)))


def test_nonexecution_gate_cannot_smuggle_a_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = {
        "format": gate.GATE_FORMAT,
        "phase": "preflight",
        "state": str(gate.STATE),
        "source": str(gate.SOURCE),
        "system": str(gate.SYSTEM),
        "config": str(gate.CONFIG),
        "overrides": str(gate.OVERRIDES),
        "source_receipt_sha256": gate.SOURCE_RECEIPT_SHA256,
        "old_bundle": gate.OLD_BUNDLE,
        "target_bundle": gate.TARGET_BUNDLE,
        "external_files": gate.EXTERNAL_FILES,
        "helper_sha256": "a" * 64,
        "accepted_at": "2026-09-17T23:30:00+00:00",
        "seal": {},
    }
    monkeypatch.setattr(gate, "sha", lambda _path: "a" * 64)
    monkeypatch.setattr(gate, "reference", lambda *_args: gate.SOURCE / "extension-source.json")
    monkeypatch.setattr(gate, "validate_evidence", lambda *_args: {})
    with pytest.raises(ValueError, match="later authority"):
        gate.validate_gate(doc, Path("/gate.json"), "a" * 64, "a" * 64, "preflight")
