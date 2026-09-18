"""Successor rehearsal preserves schema28 evidence and rejects stale authority."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_admission import Corpus

from journal.tools.runtime import prepare_schema29_rehearsals as builder
from journal.tools.runtime import rehearse_extension_migration as verifier
from swingset.backup.checkpoint import create_checkpoint
from swingset.clock import FakeClock
from swingset.publish.service import RemoteCommit
from swingset.state import db as db_module
from swingset.state.db import open_database

ROOT = Path(__file__).parents[1]
BASES = ROOT / "journal/tools/runtime"


@pytest.fixture
def derived(tmp_path):
    result = {}
    for kind in builder.BASES:
        path = tmp_path / (kind + ".py")
        path.write_text(
            builder.derive(kind, (BASES / f"rehearse_extension_{kind}.py").read_text(), "a" * 64)
        )
        result[kind] = builder.load(path, "test_successor_" + kind)
    return result


@pytest.fixture
def checkpoint28(tmp_path, monkeypatch):
    state, checkpoint = tmp_path / "state", tmp_path / "checkpoint"
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 28)
    with open_database(state) as db:
        corpus = Corpus(db)
        corpus.snapshot("retained")
        (state / "operator-hold").write_text("retained hold\n")
        candidate = state / "candidates" / "cand_8f31cad7226643ae"
        candidate.mkdir(parents=True)
        (candidate / "PUBLISHED").write_text(
            json.dumps(
                dict(commit="2a6c7dc744fb36eabb5163c0a527d787d3721f4f", candidate_id=candidate.name)
            )
        )
        (candidate / "_meta").mkdir()
        manifest = json.dumps(
            dict(candidate_id=candidate.name, expected_parent=None, files={})
        ).encode()
        (candidate / "_meta/manifest.json").write_bytes(manifest)
        (candidate / "BUILT").write_text(
            json.dumps(
                dict(manifest_hash=hashlib.sha256(manifest).hexdigest(), expected_parent=None)
            )
        )
        (state / "baseline").symlink_to(candidate)
        db.connection.execute("INSERT INTO hosts(host) VALUES ('web.archive.org')")
        db.connection.execute(
            "INSERT INTO host_budget VALUES ('web.archive.org','2026-09-17',13,2935070)"
        )
        db.connection.execute(
            "INSERT INTO host_request_spacing VALUES ('web.archive.org','paid',10,'2026-09-17T17:17:03+00:00','2026-09-17T17:17:13+00:00')"
        )
        saved = create_checkpoint(
            state, db.connection, checkpoint, schema_version=28, versions={}, input_bundle_hash=None
        )
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 29)
    return checkpoint, saved.manifest_hash


def test_prepare_migrates_all116_tables_and_copies_spacing_and_archive(
    checkpoint28, derived, tmp_path, monkeypatch
):
    checkpoint, digest = checkpoint28
    helper = derived["inputs"]
    monkeypatch.setattr(helper, "CHECKPOINT_SHA", digest)
    builder.predecessor(checkpoint, digest)
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as conn:
        before = verifier.table_receipts(conn)
    assert len(before) == 116
    original = builder.sha(checkpoint / "state.sqlite")
    scratch = tmp_path / "scratch"
    marker = helper.prepare(checkpoint, scratch, ROOT, verifier)
    assert marker["schema"] == 29
    assert marker["archive_commit"] == builder.ARCHIVE_COMMIT
    assert set(marker["history_limits"]) == set(builder.HISTORY)
    with open_database(scratch, read_only=True) as db:
        assert verifier.compare(before, verifier.table_receipts(db.connection)) == []
        assert (
            db.connection.execute("SELECT released_at FROM host_request_spacing").fetchone()[0]
            == "2026-09-17T17:17:13+00:00"
        )
    assert builder.sha(checkpoint / "state.sqlite") == original
    assert (scratch / "operator-hold").read_bytes() == (checkpoint / "operator-hold").read_bytes()
    helper.verify_files(scratch, marker, check_original=True)


@pytest.mark.parametrize(
    "mutation",
    [
        "PRAGMA user_version=14",
        "UPDATE meta SET value='14' WHERE key='schema_version'",
        "CREATE TABLE unexpected(value)",
    ],
)
def test_checkpoint_schema_and_population_are_independent_gates(checkpoint28, mutation):
    checkpoint, digest = checkpoint28
    with sqlite3.connect(checkpoint / "state.sqlite") as conn:
        conn.execute(mutation)
    conn.close()
    with pytest.raises(ValueError, match="schema markers|table population"):
        builder.predecessor(checkpoint, digest)


def test_real_ordinary_parse_preserves_paid_spacing_and_original_progress(derived, tmp_path):
    helper = derived["inputs"]
    with open_database(tmp_path / "state") as db:
        corpus = Corpus(db)
        corpus.snapshot("one")
        db.connection.execute("INSERT INTO hosts(host) VALUES ('web.archive.org')")
        db.connection.execute(
            "INSERT INTO host_request_spacing VALUES ('web.archive.org','paid',10,'start','complete')"
        )
        before = helper.protected(db.connection, 0)
        result = helper.drain(
            db,
            SimpleNamespace(),
            corpus.clock,
            max_seconds=60,
            max_units=1,
            selection_window=helper.selection_window,
        )
        assert result["attempted"] == 1
        assert helper.protected(db.connection, 0) == before
        db.connection.execute("UPDATE host_request_spacing SET gap_seconds=5")
        assert helper.protected(db.connection, 0) != before


def test_original_history_cannot_be_reset_to_hide_mutation(derived, tmp_path):
    helper = derived["inputs"]
    with open_database(tmp_path / "state") as db:
        db.connection.execute(
            "INSERT INTO event_timing_history VALUES ('episode','one','eepro','one','{}')"
        )
        before = helper.protected(db.connection, 0)
        limits = dict(helper.HISTORY_LIMITS)
        db.connection.execute(
            "INSERT INTO event_timing_history VALUES ('episode','two','eepro','one','{}')"
        )
        assert helper.protected(db.connection, 0) == before
        helper.HISTORY_LIMITS.clear()
        helper.HISTORY_LIMITS.update(limits)
        assert helper.protected(db.connection, 0) == before
        # The normal database guards are stronger; remove only in this corruption test.
        for row in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='event_timing_history'"
        ).fetchall():
            db.connection.execute('DROP TRIGGER "' + row[0] + '"')
        db.connection.execute(
            "UPDATE event_timing_history SET summary_json='changed' WHERE rowid=1"
        )
        assert helper.protected(db.connection, 0) != before


@pytest.mark.parametrize("kind", tuple(builder.BASES))
def test_modified_reviewed_base_is_rejected(kind):
    body = (BASES / f"rehearse_extension_{kind}.py").read_text()
    with pytest.raises(ValueError, match="base helper differs"):
        builder.derive(kind, body + "\n", "a" * 64)


@pytest.mark.parametrize("kind", tuple(builder.BASES))
def test_authority_flags_cannot_be_overridden(kind, tmp_path):
    packet = dict(
        source="sealed",
        checkpoint="sealed",
        checkpoint_sha256="sealed",
        source_receipt_sha256="sealed",
        archive_commit="sealed",
        files={kind + ".py": "sealed"},
    )
    for flag in ("--source", "--source=other", "--checkpoint"):
        with pytest.raises(ValueError, match="override sealed"):
            builder.arguments(kind, [flag, "other"], packet, tmp_path)


def test_migration_driver_runs_actual28_to29_without_network(
    checkpoint28, derived, tmp_path, monkeypatch
):
    checkpoint, digest = checkpoint28
    helper = derived["migration"]
    monkeypatch.setattr(helper, "verify_source", lambda *args: None)
    destination = tmp_path / "migrated"
    runtime = Path(db_module.__file__).resolve().parents[3]
    monkeypatch.setattr(
        "sys.argv",
        [
            "migration",
            "--checkpoint",
            str(checkpoint),
            "--checkpoint-sha256",
            digest,
            "--source",
            str(runtime),
            "--source-receipt",
            str(tmp_path / "receipt"),
            "--source-receipt-sha256",
            "test",
            "--destination",
            str(destination),
        ],
    )
    helper.main()
    receipt = json.loads((destination / "migration-receipt.json").read_bytes())
    assert receipt["passed"] and receipt["old_schema"] == 28 and receipt["schema"] == 29
    assert len(receipt["before"]) == 116 and receipt["changed_existing_tables"] == []
    assert receipt["new_tables"] == ["history_dispatch_fence"]


@pytest.mark.parametrize("remote_correct", [True, False])
def test_actual_restore_keeps28_held_until_verified_then_migrates(
    checkpoint28, derived, tmp_path, remote_correct
):
    checkpoint, _ = checkpoint28
    helper = derived["restore"]
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    fingerprint = builder.sha(checkpoint / "candidates" / helper.CANDIDATE / "_meta/manifest.json")
    state = tmp_path / "restored"

    class Hub:
        def head(self):
            assert (state / "RESTORE_PENDING").is_file()
            assert (state / "operator-hold").read_bytes() == (
                checkpoint / "operator-hold"
            ).read_bytes()
            return helper.BASELINE if remote_correct else "changed"

        def inspect(self, commit):
            return RemoteCommit(commit, None, helper.CANDIDATE, fingerprint, {})

    if not remote_correct:
        with pytest.raises(ValueError, match="public baseline differs"):
            helper.exercise_restore(
                checkpoint, state, manifest, Hub(), FakeClock(), maximum_schema=29
            )
        assert (state / "RESTORE_PENDING").is_file()
        return
    helper.exercise_restore(checkpoint, state, manifest, Hub(), FakeClock(), maximum_schema=29)
    with open_database(state, read_only=True) as db:
        assert db.schema_version == 28
        before = verifier.table_receipts(db.connection)
    assert not (state / "RESTORE_PENDING").exists()
    with open_database(state) as db:
        assert db.schema_version == 29
        assert verifier.compare(before, verifier.table_receipts(db.connection)) == []
    assert (state / "operator-hold").read_bytes() == (checkpoint / "operator-hold").read_bytes()


@pytest.mark.parametrize("corrupt_sequence", [False, True])
def test_restore_main_allows_only_one_pressure_epoch_increment(
    checkpoint28, derived, tmp_path, monkeypatch, corrupt_sequence
):
    checkpoint, digest = checkpoint28
    helper = derived["restore"]
    runtime = Path(db_module.__file__).resolve().parents[3]
    destination = tmp_path / "restore-main"
    fingerprint = builder.sha(checkpoint / "candidates" / helper.CANDIDATE / "_meta/manifest.json")

    class Hub:
        rehearsal_requests = []
        reads = 0

        def head(self):
            self.reads += 1
            if corrupt_sequence and self.reads == 1:
                with closing(sqlite3.connect(destination / "restored-state/state.sqlite")) as conn:
                    conn.execute("UPDATE event_pressure_state SET sequence=sequence+1")
                    conn.commit()
            return helper.BASELINE

        def inspect(self, commit):
            return RemoteCommit(commit, None, helper.CANDIDATE, fingerprint, {})

    monkeypatch.setattr(helper, "guards", lambda: {"specimen": "inactive"})
    monkeypatch.setattr(helper, "frozen_util", lambda *args: verifier)
    monkeypatch.setattr(verifier, "verify_source", lambda *args: None)
    monkeypatch.setattr(helper, "remote_reader", lambda *args: Hub())
    monkeypatch.setattr(
        "sys.argv",
        [
            "restore",
            "--checkpoint",
            str(checkpoint),
            "--checkpoint-sha256",
            digest,
            "--archive-commit",
            builder.ARCHIVE_COMMIT,
            "--target-schema",
            "29",
            "--source",
            str(runtime),
            "--source-receipt",
            "specimen",
            "--source-receipt-sha256",
            "specimen",
            "--helper-sha256",
            builder.sha(Path(helper.__file__)),
            "--public-repo",
            "skeswa/swingset",
            "--destination",
            str(destination),
        ],
    )
    if corrupt_sequence:
        with pytest.raises(ValueError, match="restoration changed retained"):
            helper.main()
    else:
        helper.main()
    receipt = json.loads((destination / "restore-receipt.json").read_bytes())
    assert receipt["passed"] is not corrupt_sequence
    assert (
        receipt["restore_change_contract"] == "event_pressure_state_singleton_epoch_plus_one_only"
    )
    assert verifier.compare(receipt["before"], receipt["expected_restored"]) == [
        "event_pressure_state"
    ]
    assert receipt["operator_hold_present"]
    if not corrupt_sequence:
        assert receipt["schema28_activated_under_hold"]
        assert receipt["schema"] == 29 and receipt["changed_existing_tables"] == []


@pytest.mark.parametrize(
    "mutation", ["PRAGMA user_version=28", "UPDATE meta SET value='28' WHERE key='schema_version'"]
)
def test_scratch_input_phases_require_both_schema29_markers(tmp_path, mutation, monkeypatch):
    # The rehearsal tool is frozen at the schema it was reviewed against (D-0013).
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 29)
    with open_database(tmp_path / "state") as db:
        builder.scratch_markers(db.state_dir)
        db.connection.execute(mutation)
        with pytest.raises(ValueError, match="cannot migrate"):
            builder.scratch_markers(db.state_dir)


def test_schema29_is_exact_literal(tmp_path):
    target = tmp_path / "src/swingset/state/db.py"
    target.parent.mkdir(parents=True)
    for body in ("SCHEMA_VERSION = 28", "SCHEMA_VERSION = 30", "SCHEMA_VERSION = int('29')"):
        target.write_text(body)
        with pytest.raises(ValueError, match="exact schema29"):
            builder.schema_literal(tmp_path)
    target.write_text("SCHEMA_VERSION = 29")
    assert builder.schema_literal(tmp_path) == 29


def test_packet_is_deterministic_and_rejects_changed_or_extra_files(tmp_path):
    source = tmp_path / "source"
    (source / "src/swingset/state").mkdir(parents=True)
    (source / "src/swingset/backup").mkdir(parents=True)
    (source / "src/swingset/state/db.py").write_text("SCHEMA_VERSION = 29")
    (source / "src/swingset/backup/checkpoint.py").write_text("# specimen")
    (source / "extension-source.json").write_text(
        json.dumps(
            {
                "files": {
                    p.relative_to(source).as_posix(): builder.sha(p)
                    for p in source.rglob("*")
                    if p.is_file()
                }
            }
        )
    )
    digest = builder.sha(source / "extension-source.json")
    one, two = tmp_path / "one", tmp_path / "two"
    for out in (one, two):
        builder.build(source, digest, "/nix/store/" + "a" * 32 + "-source", BASES, out)
    assert (one / "packet.json").read_bytes() == (two / "packet.json").read_bytes()
    packet_sha = builder.sha(one / "packet.json")
    builder.packet_at(one, packet_sha)
    (one / "extra").write_text("not sealed")
    with pytest.raises(ValueError, match="extra files"):
        builder.packet_at(one, packet_sha)
    (one / "extra").unlink()
    (one / "inputs.py").write_text("changed")
    with pytest.raises(ValueError, match="helper differs"):
        builder.packet_at(one, packet_sha)


def test_private_archive_acknowledgment_is_exact_retained_evidence(tmp_path):
    evidence = ROOT / "journal/evidence/runtime/schema28-checkpoint-2026-09-17/production-002"
    for name in builder.ARCHIVE_PROOF:
        (tmp_path / ("backup-" + name)).write_bytes((evidence / name).read_bytes())
    builder.archive_proof(tmp_path)
    path = tmp_path / "backup-receipt.json"
    receipt = json.loads(path.read_bytes())
    receipt["passed"] = False
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="private archive evidence differs"):
        builder.archive_proof(tmp_path)
