from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.build.builder import BuildError
from swingset.build.input import read_build_input
from swingset.build.schema import SCHEMAS
from swingset.state.db import open_database


def test_real_migrated_database_reads_every_public_table(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        bundle = SimpleNamespace(file_hashes={"config/sources.toml": "abc"}, digest="bundle")
        data = read_build_input(database.connection, bundle)
        assert set(data.tables) == set(SCHEMAS)
        assert data.revisions["canonical"] == 0
        assert data.input_bundle_hash == "bundle"


def test_real_pending_queue_blocks_consistent_build_read(tmp_path: Path) -> None:
    with open_database(tmp_path) as database:
        database.connection.execute(
            "INSERT INTO pending_work VALUES ('project','event','e','2026-01-01T00:00:00Z')"
        )
        bundle = SimpleNamespace(file_hashes={}, digest="bundle")
        with pytest.raises(BuildError, match="pending"):
            read_build_input(database.connection, bundle)
