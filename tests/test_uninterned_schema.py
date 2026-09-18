"""Everything step 3 ships has to run at schema 31, before interning.

Interning is the last migration, so schemas 30 and 31 are what would actually
be deployed first
([D-0167](../journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md)).
At those schemas `derivation_rows` is still one table and the payload tables do
not exist. These tests pin `SCHEMA_VERSION` to 31, the way
`tests/test_v2_migrations.py` pins an older schema, and run the whole of step 3
on that database: `gc --plan`, `gc --apply`, `gc --reclaim`, holds, doctor, and
a checkpoint that is verified and restored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_retention_apply import KNOBS, aged_candidates, applied, planned
from test_retention_plan import NOW, history

from swingset.state import db as db_module
from swingset.state.db import open_database
from swingset.state.retention import add_hold, holds, report
from swingset.state.retention_apply import eligible_payloads, reclaim

#: Every table interning adds, plus the view it puts back. None of them exists
#: at schema 31, which is the whole point of these tests.
INTERNED = (
    "derivation_payloads",
    "derivation_row_refs",
    "derivation_payload_removal_grant",
    "derivation_payload_removal_authority",
)


@pytest.fixture
def uninterned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the supported schema to 31 for the whole test, migrations included."""
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 31)


def assert_not_interned(database: Any) -> None:
    conn = database.connection
    assert database.schema_version == 31
    for name in INTERNED:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is None
    assert (
        conn.execute("SELECT type FROM sqlite_master WHERE name='derivation_rows'").fetchone()[0]
        == "table"
    )
    # The two migrations step 3 needs are there.
    for name in ("finding_support_references", "retention_applies"):
        assert (
            conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None
        )


def test_plan_apply_and_reclaim_run_on_a_database_without_interning(
    tmp_path: Path, uninterned: None
) -> None:
    """The plan says so, no payload is ever eligible, and files still go."""
    state = tmp_path / "state"
    with open_database(state) as database:
        assert_not_interned(database)
        history(database)
        aged_candidates(state)
        value = planned(database)
        content = value.content
        # The plan says which shape it measured rather than leaving a reader to
        # guess from the empty lists below.
        assert content["payloads_interned"] is False
        assert content["unknown"]["orphan_payloads"] == []
        assert content["unknown"]["unowned_payloads"] == []
        assert content["totals"]["unknown_payloads"] == 0
        # The bytes are still counted and still split between the two lists.
        assert content["totals"]["local_payload_bytes"] > 0
        assert content["totals"]["archivable_payload_bytes"] > 0
        rows = {row["generation_id"]: row for row in content["generations"]}
        assert rows["dg_a1"]["reference_count"] == rows["dg_a1"]["row_count"] == 1
        assert rows["dg_a1"]["payload_count"] == 1
        assert rows["dg_a1"]["payload_bytes"] > 0
        # Nothing can be archived, so nothing can lose its bytes.
        assert eligible_payloads(database.connection, value) == ()

        receipt = applied(database, value.digest)
        assert "candidates/cand_0" in receipt["removed_files"]
        assert receipt["planned_payloads"] == []
        assert receipt["removed_payload_bytes"] == 0
        assert not (state / "candidates" / "cand_0").exists()

        # Reclaim checks the file against the schema it was opened with.
        assert reclaim(database, now=NOW)["after"]["file_bytes"] > 0
        assert_not_interned(database)
        # The rows survived all of it and still read through the same name.
        assert (
            database.connection.execute(
                "SELECT count(*) FROM derivation_rows WHERE generation_id='dg_a1'"
            ).fetchone()[0]
            == 1
        )


def test_holds_and_doctor_run_on_a_database_without_interning(
    tmp_path: Path, uninterned: None
) -> None:
    """Residency is whole by construction, and doctor says nothing is interned."""
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        assert_not_interned(database)
        history(database)
        record = add_hold(
            state,
            database.connection,
            who="operator",
            why="keeping event-b",
            now=NOW,
            generations=("dg_b1",),
        )
        assert [item["hold_id"] for item in holds(state)] == [record["hold_id"]]
        # The hold is a root, so its generation is on the local list.
        content = planned(database).content
        assert "hold" in next(
            row["why"] for row in content["generations"] if row["generation_id"] == "dg_b1"
        )

        measured = report(database.connection, database.state_dir, **KNOBS)
        assert measured["payloads_interned"] is False
        assert measured["unknown"]["orphan_payloads"] == 0
        assert measured["totals"]["local_payload_bytes"] > 0
        assert measured["local_generations"][0]["why"]

    from swingset.cli import main

    assert main(["doctor", "--state", str(state), "--json"]) == 0
    with open_database(state, lock=False) as database:
        assert_not_interned(database)


def test_a_checkpoint_of_a_database_without_interning_verifies_and_restores(
    tmp_path: Path, uninterned: None
) -> None:
    """The closure, the copy and the restore do not know about interning."""
    from swingset.backup.checkpoint import (
        create_checkpoint,
        restore_checkpoint,
        verify_checkpoint,
    )

    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        assert_not_interned(database)
        history(database)
        checkpoint = create_checkpoint(
            state,
            database.connection,
            tmp_path / "checkpoint",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
    manifest = verify_checkpoint(checkpoint.path, maximum_schema_version=31)
    assert int(manifest["schema_version"]) == 31

    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=31)
    assert (restored / "RESTORE_PENDING").is_file()
    with open_database(restored, lock=False, allow_restore_pending=True) as database:
        assert_not_interned(database)
        assert (
            database.connection.execute("SELECT count(*) FROM derivation_rows").fetchone()[0] == 5
        )
