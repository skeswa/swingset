"""Tests for the bounded schema29 dispatch-trigger microbenchmark."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
from pathlib import Path

import pytest

MODULE_PATH = Path("journal/tools/runtime/measure_schema29_overhead.py")
SPEC = importlib.util.spec_from_file_location("measure_schema29_overhead", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MEASURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MEASURE)


def test_migration_trigger_inventory_is_derived_from_exact_sql() -> None:
    sql = """
    CREATE TRIGGER history_timing_alpha_insert AFTER INSERT ON alpha BEGIN SELECT 1; END;
    CREATE TRIGGER history_timing_alpha_update AFTER UPDATE ON alpha BEGIN SELECT 1; END;
    CREATE TRIGGER history_timing_alpha_delete AFTER DELETE ON alpha BEGIN SELECT 1; END;
    CREATE TRIGGER history_timing_beta_update AFTER UPDATE ON beta BEGIN SELECT 1; END;
    """

    inventory = MEASURE.migration_triggers(sql)
    assert set(inventory) == {"alpha", "beta"}
    assert set(inventory["alpha"]) == {
        "history_timing_alpha_insert",
        "history_timing_alpha_update",
        "history_timing_alpha_delete",
    }
    assert set(inventory["beta"]) == {"history_timing_beta_update"}


def test_paired_updates_rollback_data_and_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MEASURE, "MIN_COHORT", 1)
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE history_dispatch_fence "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO history_dispatch_fence VALUES (1,0)")
        connection.execute("CREATE TABLE alpha (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO alpha VALUES (1,'kept')")
        connection.execute("CREATE TABLE legacy_effects (effect_count INTEGER NOT NULL)")
        connection.execute("INSERT INTO legacy_effects VALUES (0)")
        connection.execute(
            "CREATE TRIGGER legacy_alpha_update AFTER UPDATE ON alpha BEGIN "
            "UPDATE legacy_effects SET effect_count=effect_count+1; END"
        )
        sql = """
        CREATE TRIGGER history_timing_alpha_update AFTER UPDATE ON alpha BEGIN
            UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
        END;
        """
        connection.executescript(sql)

        result = MEASURE.paired_measurement(connection, sql, rounds=4, samples=3)

        assert result["target_table_count"] == 1
        assert result["migration_trigger_count"] == 1
        assert result["updates_per_sample"] == 4
        assert result["rollback_verified"] is True
        assert result["preexisting_trigger_count"] == 1
        assert result["preexisting_triggers_disabled_in_both_variants"] is True
        assert result["trigger_inventory_restored"] is True
        assert isinstance(result["median_overhead_ns_per_update"], float)
        assert MEASURE.all_trigger_definitions(connection).keys() == {
            "history_timing_alpha_update",
            "legacy_alpha_update",
        }
        assert connection.execute("SELECT * FROM alpha").fetchall() == [(1, "kept")]
        assert connection.execute("SELECT * FROM legacy_effects").fetchall() == [(0,)]
        assert connection.execute("SELECT * FROM history_dispatch_fence").fetchall() == [(1, 0)]


def test_paired_measurement_rejects_trigger_coverage_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MEASURE, "MIN_COHORT", 1)
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE history_dispatch_fence (singleton INTEGER, revision INTEGER)"
        )
        connection.execute("INSERT INTO history_dispatch_fence VALUES (1,0)")
        connection.execute("CREATE TABLE alpha (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO alpha VALUES (1)")
        connection.execute("CREATE TABLE beta (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO beta VALUES (1)")
        sql = (
            "CREATE TRIGGER history_timing_alpha_update AFTER UPDATE ON alpha BEGIN SELECT 1; END;"
        )
        connection.execute(
            "CREATE TRIGGER history_timing_beta_update AFTER UPDATE ON beta BEGIN SELECT 1; END"
        )

        with pytest.raises(ValueError, match="trigger definitions differ"):
            MEASURE.paired_measurement(connection, sql)


def test_paired_measurement_rejects_ineffective_fence_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MEASURE, "MIN_COHORT", 1)
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE history_dispatch_fence "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO history_dispatch_fence VALUES (1,0)")
        connection.execute("CREATE TABLE alpha (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO alpha VALUES (1,'kept')")
        sql = """
        CREATE TRIGGER history_timing_alpha_update AFTER UPDATE ON alpha BEGIN
            UPDATE history_dispatch_fence SET revision=revision+0 WHERE singleton=1;
        END;
        """
        connection.executescript(sql)

        with pytest.raises(ValueError, match="dispatch fence delta"):
            MEASURE.paired_measurement(connection, sql, rounds=2, samples=3)


def test_paired_measurement_rejects_trigger_sql_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MEASURE, "MIN_COHORT", 1)
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE history_dispatch_fence (singleton INTEGER, revision INTEGER)"
        )
        connection.execute("INSERT INTO history_dispatch_fence VALUES (1,0)")
        connection.execute("CREATE TABLE alpha (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO alpha VALUES (1)")
        expected_sql = (
            "CREATE TRIGGER history_timing_alpha_update AFTER UPDATE ON alpha BEGIN SELECT 1; END;"
        )
        connection.execute(
            "CREATE TRIGGER history_timing_alpha_update AFTER UPDATE ON alpha BEGIN SELECT 2; END"
        )

        with pytest.raises(ValueError, match="trigger definitions differ"):
            MEASURE.paired_measurement(connection, expected_sql)


def test_copy_database_rejects_nonempty_wal(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE specimen (value TEXT)")
        connection.execute("INSERT INTO specimen VALUES ('retained in WAL')")
        connection.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        with pytest.raises(ValueError, match="nonempty source -wal"):
            MEASURE.copy_database(source, tmp_path / "copy.sqlite")


def test_copy_database_rejects_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    destination = tmp_path / "copy.sqlite"
    source.write_bytes(b"specimen")
    destination.write_bytes(b"keep")

    with pytest.raises(ValueError, match="must be new"):
        MEASURE.copy_database(source, destination)
    assert destination.read_bytes() == b"keep"


def test_path_gate_rejects_specimen_and_production_overlap(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory(dir="/var/tmp") as parent:
        root = Path(parent)
        specimen = root / "specimen"
        source = root / "source"
        specimen.mkdir()
        source.mkdir()

        with pytest.raises(ValueError, match="overlaps protected state"):
            MEASURE.validate_isolated_paths(
                specimen, source, specimen / "nested", root / "receipt.json"
            )
        with pytest.raises(ValueError, match="overlaps protected state"):
            MEASURE.validate_isolated_paths(
                specimen, source, root / "scratch", Path("/var/lib/swingset")
            )


def test_timed_updates_roll_back_when_sample_check_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE history_dispatch_fence "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO history_dispatch_fence VALUES (1,0)")
        connection.execute("CREATE TABLE alpha (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO alpha VALUES (1,'kept')")
        connection.execute(
            "CREATE TRIGGER history_timing_alpha_update AFTER UPDATE ON alpha BEGIN "
            "UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1; END"
        )
        cohort = {
            "table": "alpha",
            "key_columns": ["rowid"],
            "key_values": [1],
            "column": "id",
        }
        revision_checks = 0

        def fail_during_check(conn: sqlite3.Connection) -> int:
            nonlocal revision_checks
            revision_checks += 1
            if revision_checks == 2:
                raise RuntimeError("injected post-update failure")
            return 0

        monkeypatch.setattr(MEASURE, "_fence_revision", fail_during_check)
        with pytest.raises(RuntimeError, match="injected post-update failure"):
            MEASURE._timed_updates(
                connection,
                [cohort],
                1,
                disable_dispatch_triggers=False,
                expected_delta=1,
            )
        assert connection.execute("SELECT * FROM alpha").fetchall() == [(1, "kept")]
        assert connection.execute("SELECT * FROM history_dispatch_fence").fetchall() == [(1, 0)]
        assert MEASURE.trigger_names(connection) == ("history_timing_alpha_update",)


def test_pair_boundary_restores_all_triggers_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MEASURE, "MIN_COHORT", 1)
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE history_dispatch_fence "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO history_dispatch_fence VALUES (1,0)")
        connection.execute("CREATE TABLE alpha (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO alpha VALUES (1,'kept')")
        sql = """
        CREATE TRIGGER history_timing_alpha_update AFTER UPDATE ON alpha BEGIN
            UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
        END;
        """
        connection.executescript(sql)
        connection.execute(
            "CREATE TRIGGER legacy_alpha_update AFTER UPDATE ON alpha BEGIN SELECT 1; END"
        )
        original = MEASURE.all_trigger_definitions(connection)

        def fail_cohort(
            _connection: sqlite3.Connection, _tables: set[str]
        ) -> tuple[list[dict[str, object]], list[str]]:
            assert MEASURE.all_trigger_definitions(connection) == {
                "history_timing_alpha_update": original["history_timing_alpha_update"]
            }
            raise RuntimeError("injected cohort failure")

        monkeypatch.setattr(MEASURE, "fixed_cohort", fail_cohort)
        with pytest.raises(RuntimeError, match="injected cohort failure"):
            MEASURE.paired_measurement(connection, sql)
        assert MEASURE.all_trigger_definitions(connection) == original
        assert connection.execute("SELECT revision FROM history_dispatch_fence").fetchone() == (0,)
