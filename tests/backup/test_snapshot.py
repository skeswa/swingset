"""The outer backup job uses one publication-aligned Postgres history set."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint, verify_checkpoint
from swingset.backup.segments import LOCAL_TRANSPORT, create_set
from swingset.backup.snapshot import BackupBusy, backup_lock, capture
from swingset.backup.transport import LocalDirectoryTransport
from swingset.state.db import open_database


def test_checkpoint_exports_history_and_restores_a_fresh_destination(tmp_path):
    state = tmp_path / "state"
    with open_database(state) as database:
        checkpoint = create_checkpoint(
            state,
            database.connection,
            tmp_path / "checkpoint",
            schema_version=37,
            versions={},
            input_bundle_hash=None,
        )
        raw = tmp_path / "raw-history"
        captured = capture(state, database.connection, raw)
        durable_manifest = raw / "history.json"
        assert captured == {
            **json.loads(durable_manifest.read_text()),
            "manifest_sha256": hashlib.sha256(durable_manifest.read_bytes()).hexdigest(),
            "bytes": sum(path.stat().st_size for path in raw.rglob("*") if path.is_file()),
        }
        manifest = verify_checkpoint(checkpoint.path, maximum_schema_version=37)
        assert manifest["format"] == "swingset-postgres-checkpoint-v1"
        assert not any("database.json" in path for path in manifest["files"])
        restore_checkpoint(checkpoint.path, tmp_path / "restored", maximum_schema_version=37)
    with open_database(
        tmp_path / "restored", read_only=True, allow_restore_pending=True
    ) as restored:
        assert restored.environment == "dev"
        assert restored.schema_version == 37
    assert (tmp_path / "restored/RESTORE_PENDING").is_file()


def test_segmented_set_uses_the_same_history_bundle(tmp_path):
    state = tmp_path / "state"
    with open_database(state) as database:
        transport = LocalDirectoryTransport(state / "checkpoints", name=LOCAL_TRANSPORT)
        result = create_set(database, transport, now=datetime.now(UTC), versions={}, headroom=0)
        manifest = json.loads(
            (state / "checkpoints/sets" / result["set_id"] / "backup.json").read_text()
        )
        assert manifest["database"]["format"] == "postgres-history"
        assert result["missing"] == 0
        from swingset.backup.restore_set import install

        summary = install(transport, tmp_path / "restored", set_id=result["set_id"])
        assert summary["set_id"] == result["set_id"]
    with open_database(tmp_path / "restored", read_only=True) as restored:
        assert restored.schema_version == 37


def test_checkpoint_and_publication_backup_share_one_nonblocking_job_lock(tmp_path):
    with open_database(tmp_path / "state") as database:
        with database.reader() as other:
            with backup_lock(database.connection):
                with pytest.raises(BackupBusy):
                    with backup_lock(other):
                        pass
