"""Historical SQLite upgrades run against a checksummed, test-only source fixture.

Every archived case keeps its original assertions. Contemporary runtime paths
are exercised against Postgres by the ordinary state, retention and backup tests.
"""

from pathlib import Path

import pytest

from swingset.state import db


def test_v1_upgrade_preserves_parent_child_rows_and_review_history(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_v2_migrations.py", "test_v1_upgrade_preserves_parent_child_rows_and_review_history"
    )


def test_failed_evidence_recovery_rolls_back_entire_migration(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_v2_migrations.py", "test_failed_evidence_recovery_rolls_back_entire_migration"
    )


def test_checkpoint_cannot_label_v2_database_as_v1(tmp_path: Path) -> None:
    import json

    from swingset.backup.checkpoint import CheckpointError, create_checkpoint, verify_checkpoint

    with db.open_database(tmp_path / "state", lock=False) as database:
        with pytest.raises(CheckpointError, match="differs from database"):
            create_checkpoint(
                database.state_dir,
                database.connection,
                tmp_path / "bad",
                schema_version=1,
                versions={},
                input_bundle_hash=None,
            )
        checkpoint = create_checkpoint(
            database.state_dir,
            database.connection,
            tmp_path / "checkpoint",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
    manifest_path = checkpoint.path / "checkpoint.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 1
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(CheckpointError, match="schema differs from its history manifest"):
        verify_checkpoint(checkpoint.path, maximum_schema_version=1)
