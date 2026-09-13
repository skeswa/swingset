from __future__ import annotations

import fcntl
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from swingset.backup.checkpoint import (
    CheckpointError,
    activate_restored_state,
    create_checkpoint,
    restore_checkpoint,
    restore_from_checkpoint,
    verify_checkpoint,
    verify_restored_public,
)
from swingset.build.files import canonical_json
from swingset.clock import FakeClock
from swingset.publish.service import RemoteCommit


def state(root: Path) -> sqlite3.Connection:
    root.mkdir()
    connection = sqlite3.connect(root / "state.sqlite")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
    connection.execute("CREATE TABLE snapshots(body_sha256 TEXT, extract_sha256 TEXT)")
    connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO parent VALUES (1)")
    connection.commit()
    candidate = root / "candidates" / "cand_base"
    candidate.mkdir(parents=True)
    (candidate / "BUILT").write_text("{}")
    (candidate / "PUBLISHED").write_text('{"commit":"abc"}')
    (root / "baseline").symlink_to(Path("candidates/cand_base"))
    body_hash = hashlib.sha256(b"evidence").hexdigest()
    blob = root / "blobs" / "sha256" / body_hash[:2] / body_hash[2:4] / body_hash
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"evidence")
    connection.execute("INSERT INTO snapshots VALUES (?, NULL)", (body_hash,))
    connection.execute("INSERT INTO meta VALUES ('input_bundle_hash', 'bundle-a')")
    bundle = root / "inputs" / "bundle-a"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text("{}")
    connection.commit()
    (root / "venv").mkdir()
    (root / "venv" / "secret").write_text("excluded")
    return connection


def test_checkpoint_uses_sqlite_backup_and_restores_pending(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={"parser": "1"},
        input_bundle_hash=None,
    )
    connection.close()
    assert "venv/secret" not in checkpoint.files
    assert "inputs/bundle-a/manifest.json" in checkpoint.files
    assert (
        verify_checkpoint(checkpoint.path, maximum_schema_version=1)["baseline_candidate"]
        == "cand_base"
    )
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=1)
    assert (restored / "RESTORE_PENDING").exists()
    assert (restored / "baseline").resolve().name == "cand_base"
    copy = sqlite3.connect(restored / "state.sqlite")
    assert copy.execute("SELECT id FROM parent").fetchone() == (1,)
    copy.close()


def test_checkpoint_excludes_service_home_tool_caches(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    cache = source / ".cache" / "huggingface"
    cache.mkdir(parents=True)
    (cache / ".agent_harnesses.json").write_text('{"tool":"metadata"}')
    (cache / "token").write_text("credential")

    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash="bundle-a",
    )
    connection.close()

    assert not any(name == ".cache" or name.startswith(".cache/") for name in checkpoint.files)
    assert "inputs/bundle-a/manifest.json" in checkpoint.files
    verify_checkpoint(checkpoint.path, maximum_schema_version=1)


def test_nested_checkpoint_manifest_is_retained_verified_and_restored(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    relative = Path("operations/restore-specimen/checkpoint/checkpoint.json")
    nested = source / relative
    nested.parent.mkdir(parents=True)
    body = b'{"format": "retained specimen evidence"}'
    nested.write_bytes(body)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash="bundle-a",
    )
    connection.close()
    assert (
        relative.as_posix() in verify_checkpoint(checkpoint.path, maximum_schema_version=1)["files"]
    )
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=1)
    assert (restored / relative).read_bytes() == body
    assert not (restored / "checkpoint.json").exists()
    (checkpoint.path / relative).write_bytes(b"changed nested evidence")
    with pytest.raises(CheckpointError, match="file failed verification"):
        verify_checkpoint(checkpoint.path, maximum_schema_version=1)


def test_changed_checkpoint_file_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash=None,
    )
    connection.close()
    blob = next(path for path in (checkpoint.path / "blobs").rglob("*") if path.is_file())
    blob.write_bytes(b"corrupt")
    with pytest.raises(CheckpointError, match="failed verification"):
        verify_checkpoint(checkpoint.path, maximum_schema_version=1)


def test_missing_referenced_extract_blocks_checkpoint(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    connection.execute("INSERT INTO snapshots VALUES (NULL, ?)", ("a" * 64,))
    connection.commit()
    with pytest.raises(CheckpointError, match="referenced artifact is missing"):
        create_checkpoint(
            source,
            connection,
            tmp_path / "checkpoint",
            schema_version=1,
            versions={},
            input_bundle_hash=None,
        )
    connection.close()


class StaticHub:
    def __init__(self, head: str, commits: dict[str, RemoteCommit] | None = None) -> None:
        self.value = head
        self.commits = commits or {}

    def head(self) -> str:
        return self.value

    def inspect(self, commit: str) -> RemoteCommit:
        return self.commits[commit]

    def is_initial_head(self, commit: str) -> bool:
        return False

    def create_commit(self, **_kwargs: object) -> str:
        raise AssertionError("restore never publishes")


def test_restore_public_verification_keeps_activation_explicit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash="bundle-a",
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=1)
    verify_restored_public(restored, StaticHub("abc"))
    assert (restored / "RESTORE_PENDING").exists()
    activate_restored_state(restored)
    assert not (restored / "RESTORE_PENDING").exists()


def test_remote_ahead_keeps_restore_disabled(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash="bundle-a",
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=1)
    with pytest.raises(CheckpointError, match="differs"):
        verify_restored_public(restored, StaticHub("newer"))
    assert (restored / "RESTORE_PENDING").exists()


def test_restore_ignores_historical_published_intent_from_old_checkpoint(
    tmp_path: Path,
) -> None:
    restored = tmp_path / "restored"
    connection = state(restored)
    connection.close()
    historical = restored / "candidates" / "cand_historical"
    historical.mkdir()
    (historical / "BUILT").write_text('{"expected_parent":"initial"}')
    (historical / "PUBLISHED").write_text('{"commit":"old"}')
    (historical / "PUBLISHING").write_text("{}")
    (restored / "RESTORE_PENDING").write_text("verification pending\n")

    verify_restored_public(restored, StaticHub("abc"))

    assert (restored / "baseline").resolve().name == "cand_base"
    assert (historical / "PUBLISHING").exists()


def test_restore_recovers_landed_pending_candidate_without_publish(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    pending = source / "candidates" / "cand_pending"
    (pending / "_meta").mkdir(parents=True)
    (pending / "data").mkdir()
    (pending / "data" / "x").write_bytes(b"new")
    data_hash = hashlib.sha256(b"new").hexdigest()
    manifest = {
        "candidate_id": pending.name,
        "expected_parent": "abc",
        "input_bundle_hash": "bundle-a",
        "files": {"data/x": data_hash},
    }
    manifest_bytes = canonical_json(manifest)
    (pending / "_meta" / "manifest.json").write_bytes(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    (pending / "BUILT").write_bytes(
        canonical_json({"manifest_hash": manifest_hash, "expected_parent": "abc"})
    )
    (pending / "PUBLISHING").write_text("{}")
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash="bundle-a",
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=1)
    remote = RemoteCommit(
        "landed",
        "abc",
        pending.name,
        manifest_hash,
        {"data/x": data_hash, "_meta/manifest.json": manifest_hash},
    )
    verify_restored_public(restored, StaticHub("landed", {"landed": remote}))
    restored_pending = restored / "candidates" / pending.name
    assert json.loads((restored_pending / "PUBLISHED").read_text())["commit"] == "landed"
    assert (restored / "baseline").resolve() == restored_pending.resolve()
    assert (restored / "RESTORE_PENDING").exists()


def test_restore_from_checkpoint_holds_protocol_through_activation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash="bundle-a",
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_from_checkpoint(
        checkpoint.path, restored, StaticHub("abc"), FakeClock(), lock_timeout=1
    )
    assert not (restored / "RESTORE_PENDING").exists()
    assert (restored / "baseline").resolve().name == "cand_base"


def test_restore_lock_timeout_makes_no_staging_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash="bundle-a",
    )
    connection.close()
    target = tmp_path / "target"
    target.mkdir()
    held = (target / "state.lock").open("a+b")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(CheckpointError, match="timed out"):
        restore_from_checkpoint(
            checkpoint.path, target, StaticHub("abc"), FakeClock(), lock_timeout=0.2
        )
    assert not (target / "RESTORE_PENDING").exists()
    held.close()


def test_restore_refuses_valid_unmarked_state_and_resumes_marked_staging(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={},
        input_bundle_hash="bundle-a",
    )
    connection.close()
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "state.sqlite"
    sentinel.write_bytes(b"valid state sentinel")
    with pytest.raises(CheckpointError, match="active or unmarked"):
        restore_from_checkpoint(
            checkpoint.path, target, StaticHub("abc"), FakeClock(), lock_timeout=1
        )
    assert sentinel.read_bytes() == b"valid state sentinel"
    sentinel.unlink()
    (target / "RESTORE_PENDING").write_text("pending")
    (target / "partial").write_text("discard me")
    restore_from_checkpoint(checkpoint.path, target, StaticHub("abc"), FakeClock(), lock_timeout=1)
    assert not (target / "partial").exists()
    assert not (target / "RESTORE_PENDING").exists()


@pytest.mark.parametrize("blocked", [False, True])
def test_checkpoint_retains_generation_extract_not_on_snapshot_and_restores_it(tmp_path, blocked):
    from dataclasses import replace

    from test_admission import Corpus

    from swingset.admission.report import Guard
    from swingset.fetch.archive import Archive
    from swingset.state.db import open_database

    source = tmp_path / "source"
    with open_database(source) as db:
        c = Corpus(db)
        context = c.snapshot("staged-only")
        generation, _ = c.stage(
            context,
            report_change=lambda report: (
                replace(
                    report,
                    guards=(
                        *report.guards,
                        Guard("review_required", False, "Synthetic blocked source evidence"),
                    ),
                )
                if blocked
                else report
            ),
        )
        row = db.connection.execute(
            "SELECT manifest_json,state FROM source_generations WHERE generation_id=?",
            (generation,),
        ).fetchone()
        manifest = json.loads(row["manifest_json"])
        extract_sha = manifest[0]["extract_sha256"]
        assert (
            db.connection.execute(
                "SELECT extract_sha256 FROM snapshots WHERE snapshot_id=?", (context.snapshot_id,)
            ).fetchone()[0]
            is None
        )
        assert (
            db.connection.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0]
            is None
        )
        value = c.archive.read_extract(extract_sha)
        saved = create_checkpoint(
            source,
            db.connection,
            tmp_path / "checkpoint",
            schema_version=db.schema_version,
            versions={},
            input_bundle_hash=None,
        )
        schema = db.schema_version
    assert f"extracts/{extract_sha}" in saved.files
    before = {
        path.relative_to(saved.path).as_posix(): path.read_bytes()
        for path in saved.path.rglob("*")
        if path.is_file()
    }
    verify_checkpoint(saved.path, maximum_schema_version=schema)
    restored = tmp_path / "restored"
    restore_checkpoint(saved.path, restored, maximum_schema_version=schema)
    assert Archive(restored).read_extract(extract_sha) == value
    assert {
        path.relative_to(saved.path).as_posix(): path.read_bytes()
        for path in saved.path.rglob("*")
        if path.is_file()
    } == before
    assert not (saved.path / "state.sqlite-wal").exists()
    assert not (saved.path / "state.sqlite-shm").exists()


def test_missing_generation_only_extract_blocks_checkpoint_instead_of_losing_evidence(tmp_path):
    from test_admission import Corpus

    from swingset.state.db import open_database

    with open_database(tmp_path / "source") as db:
        c = Corpus(db)
        generation, _ = c.stage(c.snapshot("only-in-generation"))
        manifest = json.loads(
            db.connection.execute(
                "SELECT manifest_json FROM source_generations WHERE generation_id=?", (generation,)
            ).fetchone()[0]
        )
        c.archive.extract_path(manifest[0]["extract_sha256"]).unlink()
        with pytest.raises(CheckpointError, match="referenced artifact is missing"):
            create_checkpoint(
                db.state_dir,
                db.connection,
                tmp_path / "incomplete",
                schema_version=db.schema_version,
                versions={},
                input_bundle_hash=None,
            )
        assert not (tmp_path / "incomplete").exists()


def test_new_closure_validation_rejects_backup_that_omitted_generation_extract(tmp_path):
    from test_admission import Corpus

    from swingset.state.db import open_database

    with open_database(tmp_path / "source") as db:
        c = Corpus(db)
        generation, _ = c.stage(c.snapshot("staged"))
        item = json.loads(
            db.connection.execute(
                "SELECT manifest_json FROM source_generations WHERE generation_id=?", (generation,)
            ).fetchone()[0]
        )[0]
        saved = create_checkpoint(
            db.state_dir,
            db.connection,
            tmp_path / "checkpoint",
            schema_version=db.schema_version,
            versions={},
            input_bundle_hash=None,
        )
        schema = db.schema_version
    relative = "extracts/" + item["extract_sha256"]
    (saved.path / relative).unlink()
    manifest = json.loads((saved.path / "checkpoint.json").read_bytes())
    del manifest["files"][relative]
    (saved.path / "checkpoint.json").write_text(json.dumps(manifest))
    with pytest.raises(CheckpointError, match="referenced artifact is missing"):
        verify_checkpoint(saved.path, maximum_schema_version=schema)
