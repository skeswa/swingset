"""Held schema29 live migration gate must fail closed and preserve the predecessor."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "migrate_held_schema29", ROOT / "journal/tools/runtime/migrate_held_schema29.py"
)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migration
SPEC.loader.exec_module(migration)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeDatabase:
    def __init__(self, state: Path, connection: sqlite3.Connection, _lock: Any) -> None:
        self.state_dir = state
        self.connection = connection


def fixture_database(directory: Path) -> tuple[Path, dict[str, Any]]:
    directory.mkdir()
    state_db = directory / "state.sqlite"
    with sqlite3.connect(state_db) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO meta VALUES ('schema_version','28')")
        connection.execute("CREATE TABLE execution_admissions (state TEXT NOT NULL)")
        connection.execute("INSERT INTO execution_admissions VALUES ('settled')")
        connection.execute(
            "CREATE TABLE host_budget (host TEXT NOT NULL, day TEXT NOT NULL, requests INTEGER, bytes INTEGER)"
        )
        connection.execute(
            "INSERT INTO host_budget VALUES ('web.archive.org','2026-09-17',20,2952065)"
        )
        connection.execute(
            "INSERT INTO host_budget VALUES ('danceconvention.net','2026-09-17',4,132764)"
        )
        connection.execute(
            "CREATE TABLE control_state (singleton INTEGER PRIMARY KEY, revision INTEGER)"
        )
        connection.execute("INSERT INTO control_state VALUES (1,2)")
        fixed = {"meta", "execution_admissions", "host_budget", "control_state"}
        for index in range(112):
            table = f"retained_{index:03}"
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value TEXT)')
            connection.execute(f'INSERT INTO "{table}" VALUES (1,?)', (f"row-{index}",))
        assert len(fixed) + 112 == 116
        connection.execute("PRAGMA user_version=28")
    with closing(sqlite3.connect(state_db)) as connection:
        return state_db, migration.table_receipts(connection)


def runtime_module(
    *, corrupt_predecessor: bool = False, fail_before_commit: bool = False
) -> ModuleType:
    module = ModuleType("fake_runtime_db")
    module.Database = FakeDatabase  # type: ignore[attr-defined]

    def migrate(database: FakeDatabase) -> None:
        connection = database.connection
        connection.executescript(
            "BEGIN IMMEDIATE;"
            "CREATE TABLE history_dispatch_fence (singleton INTEGER PRIMARY KEY, revision INTEGER NOT NULL);"
            "INSERT INTO history_dispatch_fence VALUES (1,1);"
            "UPDATE meta SET value='29' WHERE key='schema_version';"
            "PRAGMA user_version=29;"
        )
        if corrupt_predecessor:
            connection.execute("UPDATE retained_000 SET value='corrupt' WHERE id=1")
        if fail_before_commit:
            raise RuntimeError("synthetic interrupted migration")
        connection.commit()

    module._migrate = migrate  # type: ignore[attr-defined]
    return module


def test_schema29_live_migration_preserves_all_116_tables_and_only_adds_fence(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    state_db, before = fixture_database(state / "seed")
    live = state / "live"
    live.mkdir()
    target_db = live / "state.sqlite"
    target_db.write_bytes(state_db.read_bytes())
    result = migration.migrate_locked(live, before, runtime_module(), verify_inputs=lambda: None)
    assert result["changed_existing_tables"] == []
    assert result["new_tables"] == ["history_dispatch_fence"]
    with sqlite3.connect(target_db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (29,)
        assert connection.execute("SELECT * FROM history_dispatch_fence").fetchall() == [(1, 1)]
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert len(migration.table_receipts(connection)) == 117


def test_migration_checks_inputs_at_the_boundary(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    seed, before = fixture_database(state / "seed")
    live = state / "live"
    live.mkdir()
    (live / "state.sqlite").write_bytes(seed.read_bytes())
    checked: list[str] = []
    result = migration.migrate_locked(
        live, before, runtime_module(), verify_inputs=lambda: checked.append("verified")
    )
    assert checked == ["verified"]
    assert result["new_tables"] == ["history_dispatch_fence"]


def test_migration_rejects_predecessor_table_mutation(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    seed, before = fixture_database(state / "seed")
    live = state / "live"
    live.mkdir()
    (live / "state.sqlite").write_bytes(seed.read_bytes())
    with pytest.raises(ValueError, match="protected predecessor"):
        migration.migrate_locked(
            live, before, runtime_module(corrupt_predecessor=True), verify_inputs=lambda: None
        )


def test_failed_migration_transaction_leaves_schema28_intact(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    seed, before = fixture_database(state / "seed")
    live = state / "live"
    live.mkdir()
    database = live / "state.sqlite"
    database.write_bytes(seed.read_bytes())
    with pytest.raises(RuntimeError, match="synthetic interrupted"):
        migration.migrate_locked(
            live, before, runtime_module(fail_before_commit=True), verify_inputs=lambda: None
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (28,)
        assert migration.table_receipts(connection) == before


def test_checkpoint_database_and_sidecar_bytes_must_match_manifest(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    database, before = fixture_database(checkpoint / "seed")
    (checkpoint / "state.sqlite").write_bytes(database.read_bytes())
    sidecar = checkpoint / "operator-hold"
    sidecar.write_bytes(b"held")
    manifest = {
        "files": {
            "state.sqlite": {
                "size": (checkpoint / "state.sqlite").stat().st_size,
                "sha256": digest(checkpoint / "state.sqlite"),
            },
            "operator-hold": {"size": 4, "sha256": digest(sidecar)},
        }
    }
    members_before = {path.name for path in checkpoint.iterdir()}
    assert migration.inspect_checkpoint(checkpoint, manifest)[0] == before
    assert {path.name for path in checkpoint.iterdir()} == members_before
    assert not (checkpoint / "state.sqlite-wal").exists()
    assert not (checkpoint / "state.sqlite-shm").exists()
    sidecar.write_bytes(b"hold")
    with pytest.raises(ValueError, match="sidecar bytes differ"):
        migration.inspect_checkpoint(checkpoint, manifest)


def test_checkpoint_database_byte_drift_is_rejected_even_when_rows_match(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    database, _before = fixture_database(checkpoint / "seed")
    copied = checkpoint / "state.sqlite"
    copied.write_bytes(database.read_bytes())
    manifest = {
        "files": {
            "state.sqlite": {"size": copied.stat().st_size, "sha256": digest(copied)},
        }
    }
    with copied.open("ab") as stream:
        stream.write(b"same rows, different bytes")
    with pytest.raises(ValueError, match="database bytes differ"):
        migration.inspect_checkpoint(checkpoint, manifest)


def test_live_database_must_be_a_singly_linked_regular_file(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    database, _before = fixture_database(state / "seed")
    live = state / "state.sqlite"
    live.write_bytes(database.read_bytes())
    assert migration.verify_live_database_path(state) == live

    target = state / "linked-target.sqlite"
    live.rename(target)
    live.symlink_to(target)
    with pytest.raises(ValueError, match="singly linked regular file"):
        migration.verify_live_database_path(state)

    live.unlink()
    live.write_bytes(target.read_bytes())
    hardlink = state / "second-link.sqlite"
    hardlink.hardlink_to(live)
    with pytest.raises(ValueError, match="singly linked regular file"):
        migration.verify_live_database_path(state)


def test_active_and_persistent_systems_must_match_reviewed_phase() -> None:
    migration.require_systems(
        "before_deployment", str(migration.PREDECESSOR_SYSTEM), str(migration.PREDECESSOR_SYSTEM)
    )
    migration.require_systems(
        "after_deployment", str(migration.TARGET_SYSTEM), str(migration.TARGET_SYSTEM)
    )
    with pytest.raises(ValueError, match="active/persistent system"):
        migration.require_systems(
            "after_deployment", str(migration.PREDECESSOR_SYSTEM), str(migration.TARGET_SYSTEM)
        )


def test_verified_packet_is_imported_without_mutating_packet_closure(tmp_path: Path) -> None:
    packet_root = tmp_path / "packet"
    packet_root.mkdir()
    runner = packet_root / "runner.py"
    runner.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "def packet_at(root, expected):\n"
        "    packet = json.loads((root / 'packet.json').read_bytes())\n"
        "    assert packet['target_schema'] == 29\n"
        "    return packet\n"
    )
    packet = {
        "source": str(migration.SOURCE),
        "source_receipt_sha256": migration.SOURCE_RECEIPT_SHA256,
        "target_schema": 29,
        "predecessor_schema": 28,
        "predecessor_tables": 116,
        "files": {"runner.py": digest(runner)},
    }
    packet_path = packet_root / "packet.json"
    packet_path.write_text(json.dumps(packet, sort_keys=True))
    expected = digest(packet_path)
    names_before = {path.name for path in packet_root.iterdir()}
    loaded = migration.load_packet(packet_path, expected)
    assert loaded["packet_sha256"] == expected
    assert {path.name for path in packet_root.iterdir()} == names_before
    assert "__pycache__" not in names_before


def test_packet_and_runner_closure_are_rechecked_after_preflight(tmp_path: Path) -> None:
    packet_root = tmp_path / "packet"
    packet_root.mkdir()
    runner = packet_root / "runner.py"
    runner.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "def packet_at(root, expected):\n"
        "    return json.loads((root / 'packet.json').read_bytes())\n"
    )
    packet = {
        "source": str(migration.SOURCE),
        "source_receipt_sha256": migration.SOURCE_RECEIPT_SHA256,
        "target_schema": 29,
        "predecessor_schema": 28,
        "predecessor_tables": 116,
        "files": {"runner.py": digest(runner)},
    }
    packet_path = packet_root / "packet.json"
    packet_path.write_text(json.dumps(packet, sort_keys=True))
    packet_sha = digest(packet_path)
    packet_ref = {"path": "packet/packet.json", "sha256": packet_sha}
    reviewed = migration.load_packet(packet_path, packet_sha)

    migration.verify_packet_binding(tmp_path / "gate.json", packet_ref, packet_path, reviewed)
    runner.write_text(runner.read_text() + "# changed after preflight\n")
    with pytest.raises(ValueError, match="runner differs"):
        migration.verify_packet_binding(tmp_path / "gate.json", packet_ref, packet_path, reviewed)

    packet_path.write_text(json.dumps({**packet, "target_schema": 28}, sort_keys=True))
    with pytest.raises(ValueError, match="SHA differs"):
        migration.verify_packet_binding(tmp_path / "gate.json", packet_ref, packet_path, reviewed)


def test_candidate006_service_binding_receipt_uses_real_retained_shape() -> None:
    current = (
        ROOT
        / "journal/evidence/runtime/event-extension-2026-09-17/service-binding-006-review-001/service-binding.json"
    )
    receipt = json.loads(current.read_bytes())
    migration.validate_service_binding_contract(receipt)

    candidate005 = (
        ROOT
        / "journal/evidence/runtime/event-extension-2026-09-17/service-binding-005-review-001/service-binding.json"
    )
    with pytest.raises(ValueError, match="candidate service binding pins differ"):
        migration.validate_service_binding_contract(json.loads(candidate005.read_bytes()))

    partial = json.loads(current.read_bytes())
    partial["external_overrides"]["sha256"] = {}
    with pytest.raises(ValueError, match="all six approved files"):
        migration.validate_service_binding_contract(partial)


def test_packet005_review_receipt_uses_exact_retained_shape_and_hash() -> None:
    path = (
        ROOT
        / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet005-independent-review-001/receipt.json"
    )
    receipt = json.loads(path.read_bytes())
    assert digest(path) == migration.PACKET_REVIEW_SHA256
    migration.validate_packet_review(receipt, path)

    tampered = json.loads(path.read_bytes())
    tampered["candidate006"]["source_files"] = 2572
    with pytest.raises(ValueError, match="candidate006 binding differs"):
        migration.validate_packet_review(tampered, path)

    packet004 = (
        ROOT
        / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet004-independent-review-001/explicit-review-002/receipt.json"
    )
    with pytest.raises(ValueError, match="receipt differs"):
        migration.validate_packet_review(json.loads(packet004.read_bytes()), packet004)


def actual_review() -> tuple[Path, dict[str, Any]]:
    path = (
        ROOT
        / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/actual-review-005/receipt.json"
    )
    return path, json.loads(path.read_bytes())


def test_actual_review_is_the_single_audit_and_binds_exact_underlying_receipts() -> None:
    path, receipt = actual_review()
    assert digest(path) == migration.ACTUAL_REVIEW_SHA256
    paths, docs = migration.validate_actual_review(receipt, path)
    assert set(paths) == set(migration.REHEARSAL_RECEIPT_SHA256)
    assert set(docs) == set(migration.REHEARSAL_RECEIPT_SHA256)
    assert {
        name: digest(receipt_path) for name, receipt_path in paths.items()
    } == migration.REHEARSAL_RECEIPT_SHA256

    wrong_path = json.loads(path.read_bytes())
    wrong_path["retained_receipts"]["migration"]["path"] = "../restore-004/restore-receipt.json"
    with pytest.raises(ValueError, match="migration receipt pin differs"):
        migration.validate_actual_review(wrong_path, path)

    wrong_hash = json.loads(path.read_bytes())
    wrong_hash["retained_receipts"]["migration"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="migration receipt pin differs"):
        migration.validate_actual_review(wrong_hash, path)


def test_actual_review_accepts_only_three_unchanged_crosscheck_tokens_and_one_wdr_review() -> None:
    path, receipt = actual_review()
    migration.validate_actual_review(receipt, path)

    extra_token = json.loads(path.read_bytes())
    extra_token["drain"]["unit_exceptions"]["snapshot_ids"].append("unexpected")
    extra_token["drain"]["unit_exceptions"]["count"] = 4
    with pytest.raises(ValueError, match="unproved or changed registry_crosscheck"):
        migration.validate_actual_review(extra_token, path)

    changed_token = json.loads(path.read_bytes())
    changed_token["drain"]["unit_exceptions"]["candidate006_acceptance_left_tokens_unchanged"] = (
        False
    )
    with pytest.raises(ValueError, match="unproved or changed registry_crosscheck"):
        migration.validate_actual_review(changed_token, path)

    second_review = json.loads(path.read_bytes())
    second_review["drain"]["admission_review"]["count"] = 2
    with pytest.raises(ValueError, match="single expected WDR review"):
        migration.validate_actual_review(second_review, path)


def test_exact_candidate006_rehearsal_receipts_satisfy_preservation_contract() -> None:
    review_path, review = actual_review()
    _paths, docs = migration.validate_actual_review(review, review_path)
    docs["actual_review"] = review
    packet_path = (
        ROOT
        / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-005/packet.json"
    )
    packet = migration.load_packet(packet_path, migration.PACKET_SHA256)
    migration.validate_rehearsals(docs, packet, migration.CHECKPOINT, migration.CHECKPOINT_SHA256)

    mixed = dict(docs)
    mixed_migration = dict(docs["migration"])
    mixed_migration["source_receipt_sha256"] = (
        "9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99"
    )
    mixed["migration"] = mixed_migration
    with pytest.raises(ValueError, match="migration rehearsal source or scope differs"):
        migration.validate_rehearsals(
            mixed, packet, migration.CHECKPOINT, migration.CHECKPOINT_SHA256
        )


def test_gate_rejects_candidate005_or_unreviewed_evidence_pins_before_path_use(
    tmp_path: Path,
) -> None:
    evidence = {
        name: {"path": name + ".json", "sha256": digest_value}
        for name, digest_value in migration.EVIDENCE_SHA256.items()
    }
    gate = {
        "evidence": evidence,
        "packet": {"path": "packet.json", "sha256": migration.PACKET_SHA256},
    }
    evidence["validation"]["sha256"] = (
        "819e286b06e61450a0a68cf097325dc6c6fc572983069b61b230468c5b942aef"
    )
    with pytest.raises(ValueError, match="validation evidence pin differs"):
        migration.validate_evidence(gate, tmp_path / "gate.json")


def test_packet_authority_rejects_candidate005_source_or_receipt(tmp_path: Path) -> None:
    packet_root = tmp_path / "packet"
    packet_root.mkdir()
    runner = packet_root / "runner.py"
    runner.write_text(
        "import json\n"
        "def packet_at(root, expected):\n"
        "    return json.loads((root / 'packet.json').read_bytes())\n"
    )
    packet = {
        "source": "/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source",
        "source_receipt_sha256": (
            "9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99"
        ),
        "target_schema": 29,
        "predecessor_schema": 28,
        "predecessor_tables": 116,
        "files": {"runner.py": digest(runner)},
    }
    packet_path = packet_root / "packet.json"
    packet_path.write_text(json.dumps(packet, sort_keys=True))
    with pytest.raises(ValueError, match="fresh packet authority differs"):
        migration.load_packet(packet_path, digest(packet_path))
