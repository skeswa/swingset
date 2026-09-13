import sqlite3
from datetime import UTC, datetime

import pytest

from swingset.state.db import SCHEMA_VERSION, open_database
from swingset.state.work import WorkUnit, accept_input


def test_fresh_database_has_complete_schema(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        assert database.schema_version == SCHEMA_VERSION
        tables = {
            row[0]
            for row in database.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "watches",
            "snapshots",
            "observations",
            "pending_work",
            "events",
            "placements",
            "identity_links",
        } <= tables
        assert database.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert database.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_input_digest_and_work_cannot_commit_separately(tmp_path) -> None:
    database = open_database(tmp_path, lock=False)
    unit = WorkUnit("link", "event", "2026-08-summer-hummer")
    assert accept_input(
        database, "link", "weights", "one", (unit,), accepted_at="2026-09-08T00:00:00Z"
    )
    database.close()
    with open_database(tmp_path, lock=False) as restarted:
        assert (
            restarted.connection.execute("SELECT digest FROM accepted_inputs").fetchone()[0]
            == "one"
        )
        assert (
            restarted.connection.execute("SELECT unit_id FROM pending_work").fetchone()[0]
            == unit.unit_id
        )


def test_failed_acceptance_rolls_back_digest_and_queue(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        with pytest.raises(sqlite3.IntegrityError):
            accept_input(
                database,
                "link",
                "weights",
                "one",
                (WorkUnit("link", None, "x"),),
                accepted_at="2026-09-08T00:00:00Z",
            )  # type: ignore[arg-type]
        assert (
            database.connection.execute("SELECT count(*) FROM accepted_inputs").fetchone()[0] == 0
        )
        assert database.connection.execute("SELECT count(*) FROM pending_work").fetchone()[0] == 0


def test_runs_started_in_same_second_get_stable_suffix(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        instant = datetime(2026, 9, 8, tzinfo=UTC)
        assert database.start_run(instant) == "run_20260908T000000Z"
        assert database.start_run(instant) == "run_20260908T000000Z-2"
