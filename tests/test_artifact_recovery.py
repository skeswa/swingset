"""H12 restores exact historical bytes from actual offline SQLite checkpoints."""

import gzip
import json
import os
import sqlite3

import pytest
from test_admission import BODY, Corpus

from swingset.backup.checkpoint import create_checkpoint
from swingset.fetch.archive import Archive, ArtifactUnavailable, digest
from swingset.fetch.recovery import LocalCheckpointRecovery
from swingset.state.db import SCHEMA_VERSION, open_database


def saved_state(state, *, nested_manifest=False):
    with open_database(state) as db:
        corpus = Corpus(db)
        ctx = corpus.snapshot("historical")
        body_sha = digest(BODY)
        value = {"historical": "source evidence", "rows": [1, 2]}
        extract_sha = corpus.archive.store_extract(value)
        db.connection.execute(
            "UPDATE snapshots SET extract_sha256=? WHERE snapshot_id=?",
            (extract_sha, ctx.snapshot_id),
        )
        if nested_manifest:
            specimen = state / "operations/restore-specimen/checkpoint.json"
            specimen.parent.mkdir(parents=True)
            specimen.write_text('{"specimen":"retained evidence"}')
        checkpoint = create_checkpoint(
            state,
            db.connection,
            state / "checkpoints/backup",
            schema_version=db.schema_version,
            versions={},
            input_bundle_hash=None,
        )
    recovery = LocalCheckpointRecovery(state / "checkpoints", maximum_schema_version=SCHEMA_VERSION)
    return (
        Archive(state, recovery=recovery),
        recovery,
        checkpoint.path,
        body_sha,
        extract_sha,
        value,
    )


def test_nested_manifest_does_not_disqualify_exact_artifact_recovery(tmp_path):
    archive, _, _, body_sha, _, _ = saved_state(tmp_path, nested_manifest=True)
    archive.blob_path(body_sha).unlink()
    assert archive.read_body(body_sha) == BODY


@pytest.mark.parametrize("kind", ["body", "extract"])
@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_content"])
def test_exact_artifact_restored_from_valid_private_checkpoint(tmp_path, kind, damage):
    archive, recovery, checkpoint, body_sha, extract_sha, value = saved_state(tmp_path)
    sha = body_sha if kind == "body" else extract_sha
    path = archive.blob_path(sha) if kind == "body" else archive.extract_path(sha)
    before = {
        p.relative_to(checkpoint).as_posix(): digest(p.read_bytes())
        for p in checkpoint.rglob("*")
        if p.is_file()
    }
    if damage == "missing":
        path.unlink()
    elif damage == "wrong_content":
        path.write_bytes(
            gzip.compress(b"current replacement page")
            if kind == "body"
            else b'{"current":"replacement"}'
        )
    else:
        path.write_bytes(b"corrupt gzip or JSON")
    result = archive.read_body(sha) if kind == "body" else archive.read_extract(sha)
    assert result == (BODY if kind == "body" else value)
    assert recovery.restored[0]["sha256"] == sha
    assert recovery.restored[0]["kind"] == kind
    assert {
        p.relative_to(checkpoint).as_posix(): digest(p.read_bytes())
        for p in checkpoint.rglob("*")
        if p.is_file()
    } == before
    assert not (checkpoint / "state.sqlite-wal").exists()
    assert not (checkpoint / "state.sqlite-shm").exists()


@pytest.mark.parametrize("kind", ["body", "extract"])
def test_no_matching_backup_retains_typed_historical_digest(tmp_path, kind):
    archive = Archive(
        tmp_path,
        recovery=LocalCheckpointRecovery(
            tmp_path / "checkpoints", maximum_schema_version=SCHEMA_VERSION
        ),
    )
    missing = digest(b"historical evidence not present anywhere")
    archive.store_body(b"healthy replacement content")
    with pytest.raises(ArtifactUnavailable) as error:
        archive.read_body(missing) if kind == "body" else archive.read_extract(missing)
    assert error.value.sha256 == missing
    assert error.value.kind == kind
    assert error.value.reason == "no_valid_matching_local_checkpoint"
    assert not archive.blob_path(missing).exists()
    assert not archive.extract_path(missing).exists()


def test_checkpoint_physical_and_logical_digests_are_both_required(tmp_path):
    archive, recovery, checkpoint, sha, _, _ = saved_state(tmp_path)
    archive.blob_path(sha).unlink()
    saved_blob = Archive(checkpoint).blob_path(sha)
    replacement = gzip.compress(b"this page is from the present")
    saved_blob.write_bytes(replacement)
    # Even a consistent physical backup manifest cannot authorize mislabeled
    # source bytes under the historical content-addressed digest.
    manifest_path = checkpoint / "checkpoint.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][saved_blob.relative_to(checkpoint).as_posix()] = {
        "size": len(replacement),
        "sha256": digest(replacement),
    }
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ArtifactUnavailable) as error:
        archive.read_body(sha)
    assert "logical digest" in error.value.attempts[0]["reason"]
    assert not archive.blob_path(sha).exists()
    assert recovery.restored == []


def test_target_bytes_are_reverified_after_cached_qualification_even_with_same_mtime(
    tmp_path, monkeypatch
):
    archive, recovery, checkpoint, sha, extract_sha, _ = saved_state(tmp_path)
    calls = []
    original = recovery._qualify

    def count(*args):
        calls.append(args[0])
        return original(*args)

    monkeypatch.setattr(recovery, "_qualify", count)
    archive.blob_path(sha).unlink()
    assert archive.read_body(sha) == BODY
    archive.extract_path(extract_sha).unlink()
    archive.read_extract(extract_sha)
    assert len(calls) == 1
    saved_blob = Archive(checkpoint).blob_path(sha)
    stat = saved_blob.stat()
    saved_blob.write_bytes(b"x" * stat.st_size)
    os.utime(saved_blob, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    archive.blob_path(sha).unlink()
    with pytest.raises(ArtifactUnavailable):
        archive.read_body(sha)
    assert not archive.blob_path(sha).exists()
    assert len(calls) == 1


def test_unrelated_corrupt_checkpoint_file_blocks_first_qualification_and_repair_is_not_cached(
    tmp_path,
):
    archive, recovery, checkpoint, sha, extract_sha, _ = saved_state(tmp_path)
    archive.blob_path(sha).unlink()
    path = Archive(checkpoint).extract_path(extract_sha)
    original = path.read_bytes()
    path.write_bytes(b"bad backup member")
    with pytest.raises(ArtifactUnavailable):
        archive.read_body(sha)
    path.write_bytes(original)
    assert archive.read_body(sha) == BODY
    assert len(recovery.restored) == 1


def test_checkpoint_declared_schema_cannot_hide_newer_database(tmp_path):
    archive, _, checkpoint, sha, _, _ = saved_state(tmp_path)
    archive.blob_path(sha).unlink()
    # Alter only this disposable test checkpoint, then make its physical
    # manifest consistent so qualification must inspect actual SQLite schema.
    conn = sqlite3.connect(checkpoint / "state.sqlite")
    try:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    finally:
        conn.close()
    manifest_path = checkpoint / "checkpoint.json"
    manifest = json.loads(manifest_path.read_bytes())
    database = checkpoint / "state.sqlite"
    manifest["files"]["state.sqlite"] = {
        "size": database.stat().st_size,
        "sha256": digest(database.read_bytes()),
    }
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ArtifactUnavailable) as error:
        archive.read_body(sha)
    assert "actual database" in error.value.attempts[0]["reason"]


def test_healthy_reads_never_enumerate_or_validate_backups(tmp_path, monkeypatch):
    archive = Archive(tmp_path)
    sha = archive.store_body(BODY)
    extract = archive.store_extract({"ok": True})
    recovery = LocalCheckpointRecovery(
        tmp_path / "checkpoints", maximum_schema_version=SCHEMA_VERSION
    )
    monkeypatch.setattr(
        recovery, "recover", lambda *_: pytest.fail("healthy reads must not recover")
    )
    reading = Archive(tmp_path, recovery=recovery)
    assert reading.read_body(sha) == BODY
    assert reading.read_extract(extract) == {"ok": True}


def test_default_archive_read_contract_is_unchanged(tmp_path):
    with pytest.raises(FileNotFoundError) as error:
        Archive(tmp_path).read_body("0" * 64)
    assert not isinstance(error.value, ArtifactUnavailable)


def test_corrupt_deflate_payload_recovers_exact_bytes(tmp_path):
    archive, _, _, sha, _, _ = saved_state(tmp_path)
    compressed = gzip.compress(b"content")
    archive.blob_path(sha).write_bytes(compressed[:10] + b"\xff" * 20 + compressed[-8:])
    assert archive.read_body(sha) == BODY


def test_manifest_omitting_required_backup_artifact_is_not_a_valid_backup(tmp_path):
    archive, _, checkpoint, sha, extract_sha, _ = saved_state(tmp_path)
    archive.blob_path(sha).unlink()
    required = Archive(checkpoint).extract_path(extract_sha)
    required.unlink()
    manifest_path = checkpoint / "checkpoint.json"
    manifest = json.loads(manifest_path.read_bytes())
    del manifest["files"][required.relative_to(checkpoint).as_posix()]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ArtifactUnavailable) as error:
        archive.read_body(sha)
    assert "referenced artifact is missing" in error.value.attempts[0]["reason"]


def test_recovery_refuses_to_write_into_an_immutable_checkpoint(tmp_path):
    _, recovery, checkpoint, sha, _, _ = saved_state(tmp_path)
    target = Archive(checkpoint).blob_path(sha)
    original = target.read_bytes()
    with pytest.raises(ArtifactUnavailable, match="immutable_checkpoint"):
        recovery.recover("body", sha, target)
    assert target.read_bytes() == original
