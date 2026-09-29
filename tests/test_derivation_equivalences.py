"""Migration 33: equivalence tables and the narrowed source_units watch trigger.

The schema tests write rows by hand to check what the schema guarantees: rows are permanent, each write moves the history dispatch fence,
an active publication blocks them, and they survive a checkpoint. They also
check that staging a parse, which rewrites only `desired_fingerprint`, no
longer bumps the watch token
([D-0206](../journal/decisions/0206-add-migration-33-after-interning.md)).
Then the reads: a scope whose selected generation has an equivalence for
the desired fingerprint is current and is reused, and a replay epoch only
moves forward. The last tests check the writer: `complete()` records an
equivalence only for replayable rows, moves the replay epoch when its replay
may have rewritten live rows, and refuses output that differs between reads
([D-0205](../journal/decisions/0205-cut-off-identical-derivation-output.md)).
Output alone decides a cutoff: a kept generation may name a superseded
upstream one ([D-0224](../journal/decisions/0224-releases-from-partition-artifacts.md)).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from swingset.state import db as db_module
from swingset.state import derivations
from swingset.state.db import Database, open_database
from swingset.state.work import WorkUnit

NOW = datetime(2026, 9, 25, tzinfo=UTC)
AT = "2026-09-25T00:00:00Z"
TABLES = ("derivation_recipes", "derivation_equivalences", "source_equivalences")
#: A column each table's UPDATE test rewrites to its own value.
TOUCHED = {
    "derivation_recipes": "recipe_json",
    "derivation_equivalences": "created_at",
    "source_equivalences": "created_at",
}


def watch_and_unit(conn: sqlite3.Connection, watch_id: str = "w1") -> None:
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) "
        "VALUES (?,'wsdc','dancer','GET','https://example.test/','wsdc_dancer','active')",
        (watch_id,),
    )
    conn.execute(
        "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES (?,?,'dancer')",
        (f"unit-{watch_id}", watch_id),
    )


def source_generation(conn: sqlite3.Connection, run_id: str, unit_key: str = "unit-w1") -> str:
    generation_id = f"sg-{unit_key}"
    conn.execute(
        "INSERT INTO source_generations(generation_id,unit_key,page_kind,contract_version,"
        "input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,run_id,"
        "state,removal_authority) VALUES (?,?,'dancer','v1','fp','[]','{}','{}','{}',?,?,"
        "'staged','none')",
        (generation_id, unit_key, AT, run_id),
    )
    return generation_id


def watch_version(conn: sqlite3.Connection, watch_id: str = "w1") -> int:
    row = conn.execute(
        "SELECT version FROM derivation_input_versions WHERE namespace='watch' AND input_key=?",
        (watch_id,),
    ).fetchone()
    return 0 if row is None else int(row[0])


def fence(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT revision FROM history_dispatch_fence").fetchone()[0])


class Fixture:
    """One kept derivation generation and one staged source generation."""

    def __init__(self, database: Database, kind: str = "event") -> None:
        conn = database.connection
        self.run_id = database.start_run(NOW)
        self.kind = kind
        unit = WorkUnit("project", kind, "kept")
        with database.transaction():
            selection = derivations.capture(conn, unit, now=NOW, recipe={"recipe/test": "one"})
            self.generation_id = derivations.complete(
                conn,
                selection,
                rows=iter([derivations.OutputRow("entries", '["e1"]', {"entry_id": "e1"})]),
                now=NOW,
                run_id=self.run_id,
            )
            self.dependency_set_id = conn.execute(
                "SELECT dependency_set_id FROM derivation_generations WHERE generation_id=?",
                (self.generation_id,),
            ).fetchone()[0]
            watch_and_unit(conn)
            self.source_generation_id = source_generation(conn, self.run_id)

    def insert(self, conn: sqlite3.Connection, table: str, suffix: str = "") -> None:
        if table == "derivation_recipes":
            conn.execute(
                "INSERT INTO derivation_recipes VALUES (?,?)",
                (f"recipe{suffix}", '{"recipe/test":"two"}'),
            )
        elif table == "derivation_equivalences":
            conn.execute(
                "INSERT OR IGNORE INTO derivation_recipes VALUES ('recipe-eq','{}')",
            )
            conn.execute(
                "INSERT INTO derivation_equivalences VALUES "
                "('project',?,'kept',?,?,'recipe-eq',?,?,?)",
                (
                    self.kind,
                    f"fp{suffix}",
                    self.generation_id,
                    self.dependency_set_id,
                    AT,
                    self.run_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO source_equivalences VALUES ('unit-w1',?,?,'{}',NULL,?,?)",
                (f"fp{suffix}", self.source_generation_id, AT, self.run_id),
            )


def test_migration_from_schema_32_adds_empty_tables_and_narrows_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 32)
        with open_database(state, lock=False) as database:
            conn = database.connection
            assert database.schema_version == 32
            with database.transaction():
                watch_and_unit(conn)
            before = watch_version(conn)
            with database.transaction():
                conn.execute("UPDATE source_units SET desired_fingerprint='a'")
            # Schema 32 bumps the token on any update, which is what 33 stops.
            assert watch_version(conn) > before
            for table in TABLES:
                assert (
                    conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
                    is None
                )

    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 33)
    with open_database(state, lock=False) as database:
        conn = database.connection
        assert database.schema_version == 33
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 33
        for table in TABLES:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            for action in ("insert", "update", "delete"):
                for name in (
                    f"history_timing_{table}_{action}",
                    f"publication_fence_{table}_{action}",
                ):
                    assert (
                        conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
                        ).fetchone()
                        is not None
                    ), name
        trigger = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='derivation_source_units_update'"
        ).fetchone()[0]
        assert "OLD.accepted_generation_id IS NOT NEW.accepted_generation_id" in trigger
        assert "desired_fingerprint" not in trigger
        before = watch_version(conn)
        with database.transaction():
            conn.execute("UPDATE source_units SET desired_fingerprint='b'")
        assert watch_version(conn) == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_page_evidence_reads_revocations_at_schema_32(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A read-only open skips migration, so evidence readers meet schema 32.
    from swingset.schedule.event_evidence import admission_reason

    value = {
        "state": "accepted",
        "recipe": {"context": {"snapshot_id": "s"}, "parser_version": "1"},
        "unit_key": "u",
        "input_fingerprint": "f",
        "report": {"failures": ["unsupported"]},
    }
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 32)
        with open_database(tmp_path / "state", lock=False) as database:
            assert database.schema_version == 32
            reason = admission_reason(database.connection, value)
    assert reason == "source_interpretation_unsupported"
    with open_database(tmp_path / "state", lock=False) as database:
        assert admission_reason(database.connection, value) == reason


@pytest.mark.parametrize("table", TABLES)
def test_rows_are_permanent_and_move_the_history_fence(tmp_path: Path, table: str) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        fixture = Fixture(database)
        before = fence(conn)
        with database.transaction():
            fixture.insert(conn, table)
        assert fence(conn) > before
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with database.transaction():
                conn.execute(f"UPDATE {table} SET {TOUCHED[table]}={TOUCHED[table]}")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with database.transaction():
                conn.execute(f"DELETE FROM {table}")
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("table", TABLES)
def test_an_active_publication_blocks_writes(tmp_path: Path, table: str) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        fixture = Fixture(database)
        with database.transaction():
            conn.execute(
                "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,"
                "control_revision,all_sources,all_kinds,state) "
                "VALUES ('publishing','publication',?,0,1,1,'active')",
                (AT,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="active publication fences"):
            with database.transaction():
                fixture.insert(conn, table)
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_only_fields_derivations_read_bump_the_watch_token(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        with database.transaction():
            watch_and_unit(conn)
            generation_id = source_generation(conn, run_id)
        start = watch_version(conn)
        with database.transaction():
            conn.execute(
                "UPDATE source_units SET desired_fingerprint='staged' WHERE unit_key='unit-w1'"
            )
        assert watch_version(conn) == start
        with database.transaction():
            conn.execute(
                "UPDATE source_units SET accepted_generation_id=? WHERE unit_key='unit-w1'",
                (generation_id,),
            )
        bumped = watch_version(conn)
        assert bumped > start
        # Setting a field to its current value is not a change.
        with database.transaction():
            conn.execute(
                "UPDATE source_units SET accepted_generation_id=? WHERE unit_key='unit-w1'",
                (generation_id,),
            )
        assert watch_version(conn) == bumped


def test_only_fields_derivations_read_bump_the_source_selections_token(tmp_path: Path) -> None:
    def version(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT version FROM derivation_input_versions "
            "WHERE namespace='source_selections' AND input_key='all'"
        ).fetchone()
        return 0 if row is None else int(row[0])

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        with database.transaction():
            watch_and_unit(conn)
            generation_id = source_generation(conn, run_id)
        start = version(conn)
        # Every parse stages a desired fingerprint; no certificate reads it.
        with database.transaction():
            conn.execute(
                "UPDATE source_units SET desired_fingerprint='staged' WHERE unit_key='unit-w1'"
            )
        assert version(conn) == start
        with database.transaction():
            conn.execute(
                "UPDATE source_units SET accepted_generation_id=? WHERE unit_key='unit-w1'",
                (generation_id,),
            )
        assert version(conn) > start


def test_a_checkpoint_at_schema_33_verifies_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 33)
    from swingset.backup.checkpoint import (
        create_checkpoint,
        restore_checkpoint,
        verify_checkpoint,
    )

    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        conn = database.connection
        # An event's prerequisites read schema 35's proposals; a dancer has none.
        fixture = Fixture(database, kind="dancer")
        with database.transaction():
            for table in TABLES:
                fixture.insert(conn, table)
        expected = {
            table: sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))
            for table in TABLES
        }
        checkpoint = create_checkpoint(
            state,
            conn,
            tmp_path / "checkpoint",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
    manifest = verify_checkpoint(checkpoint.path, maximum_schema_version=33)
    assert int(manifest["schema_version"]) == 33

    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=33)
    with open_database(restored, lock=False, allow_restore_pending=True) as database:
        assert database.schema_version == 33
        assert {
            table: sorted(
                tuple(row) for row in database.connection.execute(f"SELECT * FROM {table}")
            )
            for table in TABLES
        } == expected


def test_schema_32_code_refuses_a_schema_33_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 33)
    with open_database(state, lock=False) as database:
        assert database.schema_version == 33
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 32)
    with pytest.raises(RuntimeError, match="schema 33 is newer than supported 32"):
        open_database(state, lock=False)


UNIT = WorkUnit("project", "event", "kept")
SCOPE = ("project", "event", "kept")


def accept_runtime(database: Database, value: str) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO accepted_inputs VALUES ('pipeline','recipe/env',?) "
            "ON CONFLICT(consumer,input_name) DO UPDATE SET digest=excluded.digest",
            (value,),
        )
        # A scoped runtime always names the unit's code scope.
        database.connection.execute(
            "INSERT OR IGNORE INTO accepted_inputs "
            "VALUES ('pipeline','recipe/code/project/event','scope')"
        )


def materialize(database: Database, run_id: str, marker: str) -> str:
    conn = database.connection
    with database.transaction():
        selection = derivations.capture(conn, UNIT, now=NOW)
        return derivations.complete(
            conn,
            selection,
            rows=iter([derivations.OutputRow("entries", '["e1"]', {"entry_id": marker})]),
            now=NOW,
            run_id=run_id,
        )


def equivalent(database: Database, run_id: str, fingerprint: str, generation_id: str) -> None:
    conn = database.connection
    set_id = conn.execute(
        "SELECT dependency_set_id FROM derivation_generations WHERE generation_id=?",
        (generation_id,),
    ).fetchone()[0]
    with database.transaction():
        conn.execute("INSERT OR IGNORE INTO derivation_recipes VALUES ('recipe-eq','{}')")
        conn.execute(
            "INSERT INTO derivation_equivalences VALUES ('project','event','kept',?,?,"
            "'recipe-eq',?,?,?)",
            (fingerprint, generation_id, set_id, AT, run_id),
        )


def pointer(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes "
        "WHERE stage='project' AND unit_kind='event' AND unit_id='kept'"
    ).fetchone()
    return None if row is None else row[0]


def test_an_equivalence_for_the_selected_generation_makes_the_scope_current(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = materialize(database, run_id, "e1")
        assert derivations.current(conn, UNIT)

        accept_runtime(database, "code-2")
        stale = derivations.desired(conn, UNIT)
        assert not derivations.current(conn, UNIT)
        assert stale.generation_id != kept
        assert not derivations.materialized_for(conn, SCOPE, kept, stale.fingerprint)
        assert UNIT in set(derivations.pending_units(conn, "project"))

        equivalent(database, run_id, stale.fingerprint, kept)
        assert derivations.materialized_for(conn, SCOPE, kept, stale.fingerprint)
        assert derivations.current(conn, UNIT)
        assert derivations.current_for(conn, UNIT)
        reused = derivations.desired(conn, UNIT)
        assert reused.fingerprint == stale.fingerprint
        assert reused.generation_id == kept == reused.previous_generation_id
        assert UNIT not in set(derivations.pending_units(conn, "project"))
        assert pointer(conn) == kept

        # A later input change is not covered by the old equivalence.
        accept_runtime(database, "code-3")
        assert not derivations.current(conn, UNIT)
        assert not derivations.current_for(conn, UNIT)


def test_an_equivalence_for_another_generation_of_the_scope_does_not_count(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        first = materialize(database, run_id, "e1")
        accept_runtime(database, "code-2")
        second = materialize(database, run_id, "e2")
        assert second != first and pointer(conn) == second

        accept_runtime(database, "code-3")
        wanted = derivations.desired(conn, UNIT).fingerprint
        # The equivalence names the older generation, not the selected one.
        equivalent(database, run_id, wanted, first)
        assert derivations.materialized_for(conn, SCOPE, first, wanted)
        assert not derivations.materialized_for(conn, SCOPE, second, wanted)
        assert not derivations.current(conn, UNIT)
        assert not derivations.current_for(conn, UNIT)
        assert derivations.desired(conn, UNIT).generation_id not in {first, second}
        assert UNIT in set(derivations.pending_units(conn, "project"))
        # Nor does a scope other than the one the equivalence names.
        assert not derivations.materialized_for(conn, ("project", "event", "other"), first, wanted)


def test_current_for_takes_an_explicit_recipe(tmp_path: Path) -> None:
    """The calendar replay guard's question, asked with a captured recipe."""
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        with database.transaction():
            selection = derivations.capture(conn, UNIT, now=NOW, recipe={"recipe/test": "one"})
            kept = derivations.complete(
                conn,
                selection,
                rows=[derivations.OutputRow("entries", '["e1"]', {"entry_id": "e1"})],
                now=NOW,
                run_id=run_id,
            )
        assert derivations.current_for(conn, UNIT, recipe={"recipe/test": "one"})
        changed = {"recipe/test": "two"}
        assert not derivations.current_for(conn, UNIT, recipe=changed)
        equivalent(
            database, run_id, derivations.desired(conn, UNIT, recipe=changed).fingerprint, kept
        )
        assert derivations.current_for(conn, UNIT, recipe=changed)
        assert derivations.desired(conn, UNIT, recipe=changed).generation_id == kept


def test_source_materialized_for_reads_generations_and_equivalences(tmp_path: Path) -> None:
    from swingset.admission.select import source_materialized_for

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        fixture = Fixture(database)
        generation_id = fixture.source_generation_id
        assert source_materialized_for(conn, "unit-w1", generation_id, "fp")
        assert not source_materialized_for(conn, "unit-w1", generation_id, "fp-later")
        assert not source_materialized_for(conn, "unit-w1", None, "fp")
        with database.transaction():
            fixture.insert(conn, "source_equivalences", "-later")
        assert source_materialized_for(conn, "unit-w1", generation_id, "fp-later")
        assert not source_materialized_for(conn, "unit-other", generation_id, "fp-later")


def test_a_replay_epoch_is_monotonic_and_moves_the_history_fence(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        before = fence(conn)
        with database.transaction():
            assert derivations.bump_replay(conn, "project", "calendar") == 1
            assert derivations.bump_replay(conn, "link", "event") == 1
        with database.transaction():
            assert derivations.bump_replay(conn, "project", "calendar") == 2
        assert fence(conn) > before
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT input_key,version FROM derivation_input_versions "
                "WHERE namespace='replayed' ORDER BY input_key"
            )
        ] == [('["link","event"]', 1), ('["project","calendar"]', 2)]
        with pytest.raises(sqlite3.IntegrityError, match="must advance"):
            with database.transaction():
                conn.execute(
                    "UPDATE derivation_input_versions SET version=1 WHERE namespace='replayed'"
                )


def _register(conn: sqlite3.Connection, *units: tuple[str, str, str]) -> None:
    conn.executemany(
        "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,?,?)",
        ((*unit, AT) for unit in units),
    )


def _queued(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return {
        (str(r[0]), str(r[1]), str(r[2]))
        for r in conn.execute("SELECT stage,unit_kind,unit_id FROM pending_work")
    }


def test_a_replay_epoch_queues_hints_for_the_kinds_that_read_it(tmp_path: Path) -> None:
    """A replay bump makes its readers stale by fingerprint alone. They are
    queued so the watchdog, which reads only `pending_work`, sees them (I4);
    one already queued keeps its time, and a new one gets the run's clock."""
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            _register(
                conn,
                ("project", "calendar", "wsdc"),
                ("project", "inventory", "all"),
                ("project", "mapping", "eepro"),
                ("project", "resolution", "e1"),
                ("project", "event", "e1"),
                ("project", "year", "2026"),
                ("link", "event", "e1"),
                ("link", "event", "e2"),
            )
            conn.execute("INSERT INTO pending_work VALUES ('link','event','e2','2020-01-01')")
        with database.transaction():
            derivations.bump_replay(conn, "project", "resolution", now=NOW)
        assert _queued(conn) == {
            ("project", "event", "e1"),
            ("project", "year", "2026"),
            ("link", "event", "e2"),
        }
        assert (
            conn.execute(
                "SELECT enqueued_at FROM pending_work WHERE unit_kind='event' AND unit_id='e2'"
            ).fetchone()[0]
            == "2020-01-01"
        )
        # A new hint carries the run's clock, not the wall clock.
        assert (
            conn.execute("SELECT enqueued_at FROM pending_work WHERE unit_kind='year'").fetchone()[
                0
            ]
            == NOW.isoformat()
        )
        with database.transaction():
            derivations.bump_replay(conn, "project", "dancer")
        assert ("project", "inventory", "all") in _queued(conn)
        # A link reads the dancers it used, not the kind's epoch (plan F20).
        assert ("link", "event", "e1") not in _queued(conn)
        # Nothing reads a link's replay epoch.
        conn.execute("DELETE FROM pending_work")
        with database.transaction():
            derivations.bump_replay(conn, "link", "event")
        assert _queued(conn) == set()


def test_a_derivation_code_scope_or_environment_change_queues_hints(tmp_path: Path) -> None:
    """Project and link units are stale by fingerprint after a code-scope or
    environment change; `affected_work` queues them so the backlog shows."""
    from swingset.state.work import affected_work

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            _register(
                conn,
                ("project", "event", "e1"),
                ("project", "dancer", "d1"),
                ("link", "event", "e1"),
            )

        def hinted(name: str) -> set[tuple[str, str, str]]:
            return {(u.stage, u.unit_kind, u.unit_id) for u in affected_work(conn, name)}

        assert hinted("recipe/code/project/event") == {("project", "event", "e1")}
        assert hinted("recipe/code/link") == {("link", "event", "e1")}
        assert hinted("recipe/code/build") == set()
        for name in ("recipe/env", "recipe/runtime"):
            assert hinted(name) == {
                ("project", "event", "e1"),
                ("project", "dancer", "d1"),
                ("link", "event", "e1"),
            }


def rerun(database: Database, run_id: str, rows, *, captured: bool = True) -> str:
    conn = database.connection
    with database.transaction():
        selection = (
            derivations.capture(conn, UNIT, now=NOW)
            if captured
            else derivations.desired(conn, UNIT)
        )
        return derivations.complete(conn, selection, rows=rows, now=NOW, run_id=run_id)


ROWS = [derivations.OutputRow("entries", '["e1"]', {"entry_id": "e1"})]


def replay_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM derivation_input_versions WHERE namespace='replayed' "
        'AND input_key=\'["project","event"]\''
    ).fetchone()
    return 0 if row is None else int(row[0])


def test_an_equivalence_fingerprint_recomputes_from_its_recipe_and_root_set(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")
        assert rerun(database, run_id, lambda: iter(ROWS)) == kept
        assert pointer(conn) == kept
        assert conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0] == 1
        stage, kind, unit_id, fingerprint, generation_id, recipe_json, manifest = conn.execute(
            "SELECT e.stage,e.unit_kind,e.unit_id,e.input_fingerprint,e.generation_id,"
            "r.recipe_json,d.manifest_json FROM derivation_equivalences e "
            "JOIN derivation_recipes r USING(recipe_sha256) "
            "JOIN derivation_dependency_sets d USING(dependency_set_id)"
        ).fetchone()
        assert generation_id == kept
        recipe = json.loads(recipe_json)
        assert recipe["inputs"]["recipe/env"] == "code-2"
        assert (
            derivations.digest(
                {
                    "format": "derivation-v1",
                    "scope": (stage, kind, unit_id),
                    "recipe": recipe["inputs"],
                    "dependencies": json.loads(manifest),
                    "context": recipe["context"],
                }
            )
            == fingerprint
            == derivations.desired(conn, UNIT).fingerprint
        )
        assert (
            hashlib.sha256(recipe_json.encode()).hexdigest()
            == conn.execute("SELECT recipe_sha256 FROM derivation_equivalences").fetchone()[0]
        )
        # A captured run that rewrote no live rows leaves the replay epoch alone.
        assert replay_version(conn) == 0
        assert derivations.current(conn, UNIT)


def test_a_one_shot_iterator_never_cuts_off(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        first = rerun(database, run_id, iter(ROWS))
        accept_runtime(database, "code-2")
        second = rerun(database, run_id, iter(ROWS))
        assert second != first and pointer(conn) == second
        assert conn.execute("SELECT count(*) FROM derivation_equivalences").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT previous_generation_id FROM derivation_generations WHERE generation_id=?",
                (second,),
            ).fetchone()[0]
            == first
        )


def test_a_cutoff_without_a_witness_moves_the_replay_epoch(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")
        # desired() alone reads no witness, so the cutoff cannot tell whether
        # its replay rewrote live rows and assumes it did.
        assert rerun(database, run_id, ROWS, captured=False) == kept
        assert replay_version(conn) == 1


def test_a_cutoff_whose_replay_moved_a_witnessed_revision_moves_the_replay_epoch(
    tmp_path: Path,
) -> None:
    from swingset.state.work import bump_revision

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")

        def replayed_rows():
            return iter(ROWS)

        with database.transaction():
            selection = derivations.capture(conn, UNIT, now=NOW)
            bump_revision(conn, "canonical")
            assert (
                derivations.complete(conn, selection, rows=replayed_rows, now=NOW, run_id=run_id)
                == kept
            )
        assert replay_version(conn) == 1
        # Findings are outside the witness.
        accept_runtime(database, "code-3")
        with database.transaction():
            selection = derivations.capture(conn, UNIT, now=NOW)
            bump_revision(conn, "findings")
            derivations.complete(conn, selection, rows=replayed_rows, now=NOW, run_id=run_id)
        assert replay_version(conn) == 1


def test_a_reuse_through_an_equivalence_that_rewrote_live_rows_moves_the_replay_epoch(
    tmp_path: Path,
) -> None:
    from swingset.state.work import bump_revision

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")
        assert rerun(database, run_id, ROWS) == kept
        before = replay_version(conn)
        # A second run at the equivalence's fingerprint reuses the kept id,
        # and its replay rewrote live rows its dependents read.
        with database.transaction():
            selection = derivations.capture(conn, UNIT, now=NOW)
            assert selection.generation_id == kept
            bump_revision(conn, "canonical")
            derivations.complete(conn, selection, rows=ROWS, now=NOW, run_id=run_id)
        assert replay_version(conn) == before + 1
        # One that rewrote nothing moves nothing.
        rerun(database, run_id, ROWS)
        assert replay_version(conn) == before + 1


def test_a_rerun_whose_upstream_reminted_identical_output_cuts_off(tmp_path: Path) -> None:
    """F1 of D-0205, inverted (D-0224).

    The upstream resolution re-mints (a one-shot iterator never cuts off)
    with identical output, so the event's dependency names a new generation.
    The event's own output is identical: it keeps its generation, although
    that generation names the superseded upstream one. Before D-0224 the
    closure-stable rule refused this cutoff and minted a new event.
    """
    upstream = WorkUnit("project", "resolution", "kept")
    events = [derivations.OutputRow("events", '["kept"]', {"event_id": "kept"})]

    def run(database: Database, run_id: str, unit: WorkUnit, rows) -> str:
        with database.transaction():
            selection = derivations.capture(database.connection, unit, now=NOW)
            return derivations.complete(
                database.connection, selection, rows=rows, now=NOW, run_id=run_id
            )

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        with database.transaction():
            conn.execute(
                "INSERT OR IGNORE INTO accepted_inputs "
                "VALUES ('pipeline','recipe/code/project/resolution','scope')"
            )
        first_upstream = run(database, run_id, upstream, events)
        kept = run(database, run_id, UNIT, ROWS)
        assert first_upstream in json.dumps(derivations.desired(conn, UNIT).dependency_sets)
        accept_runtime(database, "code-2")
        second_upstream = run(database, run_id, upstream, iter(events))
        assert second_upstream != first_upstream
        assert derivations.desired(conn, UNIT).generation_id != kept
        assert run(database, run_id, UNIT, ROWS) == kept
        assert pointer(conn) == kept
        assert (
            conn.execute(
                "SELECT count(*) FROM derivation_equivalences WHERE unit_kind='event'"
            ).fetchone()[0]
            == 1
        )
        assert derivations.current(conn, UNIT)


def test_an_input_that_moves_during_the_comparison_writes_no_equivalence(
    tmp_path: Path,
) -> None:
    from swingset.state.attempts import SupersededWorkError

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")
        reads = []

        def moving():
            # The first read is the comparison; an input moves under it.
            if not reads:
                conn.execute(
                    "UPDATE accepted_inputs SET digest='code-3' "
                    "WHERE consumer='pipeline' AND input_name='recipe/env'"
                )
            reads.append(1)
            return iter(ROWS)

        with pytest.raises(SupersededWorkError, match="while output was being compared"):
            rerun(database, run_id, moving)
        assert reads == [1]
        assert pointer(conn) == kept
        assert conn.execute("SELECT count(*) FROM derivation_equivalences").fetchone()[0] == 0


def test_changed_output_is_stored_from_a_second_read_that_must_match_the_first(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")
        reads = iter(("e2", "e3"))

        def unstable():
            return iter([derivations.OutputRow("entries", '["e1"]', {"entry_id": next(reads)})])

        with pytest.raises(derivations.NondeterministicOutputError, match="not deterministic"):
            rerun(database, run_id, unstable)
        assert pointer(conn) == kept
        changed = [derivations.OutputRow("entries", '["e1"]', {"entry_id": "e2"})]
        newer = rerun(database, run_id, changed)
        assert newer != kept and pointer(conn) == newer
        assert conn.execute("SELECT count(*) FROM derivation_equivalences").fetchone()[0] == 0


TIER = {"events": ("coverage_tier",)}
EVENT_ROW = {"event_id": "ev1", "name": "Open", "coverage_tier": "sheets_partial"}


def test_a_generation_that_recorded_a_moved_column_is_kept(tmp_path: Path) -> None:
    """A generation from before phase 1 recorded `events.coverage_tier`, which
    history owns now, and the same scope's output leaves it out. The
    comparison takes the moved column out of the kept rows, so the first
    switch cuts off (D-0212); any other difference still mints."""
    old = [derivations.OutputRow("events", '["ev1"]', EVENT_ROW)]
    new = [
        derivations.OutputRow(
            "events", '["ev1"]', {k: v for k, v in EVENT_ROW.items() if k != "coverage_tier"}
        )
    ]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, old)

        def cut(rows, moved) -> str:
            with database.transaction():
                selection = derivations.capture(conn, UNIT, now=NOW)
                return derivations.complete(
                    conn, selection, rows=rows, now=NOW, run_id=run_id, moved=moved
                )

        accept_runtime(database, "code-2")
        assert cut(new, TIER) == kept
        assert pointer(conn) == kept
        # A second run at the same inputs reuses the kept id and reads the
        # same output: not a nondeterminism.
        assert cut(new, TIER) == kept
        # Without the moved columns the labels differ and a generation mints.
        accept_runtime(database, "code-3")
        minted = cut(new, {})
        assert minted != kept and pointer(conn) == minted
        # Only the moved column is left out: a changed name still mints.
        accept_runtime(database, "code-4")
        renamed = [
            derivations.OutputRow("events", '["ev1"]', {"event_id": "ev1", "name": "Closed"})
        ]
        assert cut(renamed, TIER) not in {kept, minted}


def test_projection_materialization_leaves_the_moved_column_out_of_the_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`materializing` names the moved columns of the unit's kind (D-0212):
    an event scope keeps a generation that recorded `coverage_tier`, and a
    year (history before), which owns that column, never leaves it out."""
    from swingset.project import materialization

    assert materialization.moved_columns(UNIT) == TIER
    assert materialization.moved_columns(WorkUnit("project", "calendar", "wsdc")) == TIER
    assert materialization.moved_columns(WorkUnit("project", "history", "all")) == {}
    assert materialization.moved_columns(WorkUnit("project", "year", "2026")) == {}
    assert materialization.moved_columns(WorkUnit("link", "event", "e")) == {}
    old = [derivations.OutputRow("events", '["ev1"]', EVENT_ROW)]
    new = [
        derivations.OutputRow(
            "events", '["ev1"]', {k: v for k, v in EVENT_ROW.items() if k != "coverage_tier"}
        )
    ]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)

        def run(rows) -> None:
            monkeypatch.setattr(materialization, "output_rows", lambda _conn, _unit: iter(rows))
            with database.transaction():
                with materialization.materializing(conn, UNIT, now=NOW, run_id=run_id):
                    pass

        accept_runtime(database, "code-1")
        run(old)
        kept = pointer(conn)
        accept_runtime(database, "code-2")
        run(new)
        assert pointer(conn) == kept
        assert conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0] == 1


def _watchdog_ready(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "nix" / "watchdog.py"
    spec = importlib.util.spec_from_file_location("swingset_watchdog_ready", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Schema 34 leaves out units waiting on a work-queue clock at :now.
    return list(conn.execute(module.QUERIES["ready"], {"now": module._queue_at(NOW.timestamp())}))


@pytest.mark.parametrize("drained", [False, True])
def test_a_reverted_deploy_leaves_no_hint_for_a_current_unit(tmp_path: Path, drained: bool) -> None:
    """An A->B->A deploy queues hints for units that are current again. Only
    a unit run deletes its own hint, so offline selection deletes the ones it
    finds current; otherwise the watchdog counts them as ready forever (I4).
    A row queued again after the read keeps its place. The unit's link was
    never materialized, so its hint is real work and stays."""
    from swingset.schedule.fairness import next_offline_batch
    from swingset.state.work import affected_work, enqueue

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        rerun(database, run_id, ROWS)

        def deploy(code: str, at: str) -> None:
            accept_runtime(database, code)
            with database.transaction():
                enqueue(conn, tuple(affected_work(conn, "recipe/env")), enqueued_at=at)

        deploy("code-2", "2026-09-25T01:00:00Z")
        link = ("link", "event", "kept")
        assert _queued(conn) == {SCOPE, link}
        assert not derivations.current(conn, UNIT)
        if drained:
            # The drain cuts off to the same output and deletes its hints.
            rerun(database, run_id, ROWS)
            with database.transaction():
                conn.execute("DELETE FROM pending_work WHERE stage='project'")
        deploy("code-1", "2026-09-25T02:00:00Z")
        assert derivations.current(conn, UNIT)
        assert SCOPE in _queued(conn)
        assert ("project",) in {row[:1] for row in _watchdog_ready(conn)}

        found: dict[WorkUnit, str] = {}
        assert UNIT not in next_offline_batch(conn, now=NOW, current_hints=found)
        assert set(found) == {UNIT}
        # A hint queued again after the read has a new time and stays.
        with database.transaction():
            enqueue(conn, (UNIT,), enqueued_at="2026-09-25T03:00:00Z")
        assert derivations.drop_current_hints(database, found) == 0
        assert _queued(conn) == {SCOPE, link}

        found.clear()
        assert UNIT not in next_offline_batch(conn, now=NOW, current_hints=found)
        assert derivations.drop_current_hints(database, found) == 1
        assert _queued(conn) == {link}
        assert [row[:2] for row in _watchdog_ready(conn)] == [("link", "event")]


def staged(database: Database, run_id: str, rows, *, move: bool = False) -> str:
    """One staged completion of UNIT (WP4): step A, then pass 1, B and C."""
    from swingset.state.work import bump_revision

    conn = database.connection
    with derivations.chunking(conn, UNIT) as chunk:
        with database.transaction(), derivations.group(conn):
            selection = derivations.capture(conn, UNIT, now=NOW)
            if move:
                bump_revision(conn, "canonical")
            assert derivations.stage(
                conn, selection, output=lambda reader: iter(rows), now=NOW, run_id=run_id
            )
        assert chunk.deferred is not None
        return derivations.finish_building(database, chunk.deferred)


def test_a_staged_cutoff_moves_the_replay_epoch_only_when_its_live_write_did(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        references = conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
        accept_runtime(database, "code-2")
        assert staged(database, run_id, ROWS) == kept
        assert replay_version(conn) == 0
        accept_runtime(database, "code-3")
        assert staged(database, run_id, ROWS, move=True) == kept
        assert replay_version(conn) == 1
        assert pointer(conn) == kept
        assert conn.execute("SELECT count(*) FROM derivation_equivalences").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0] == references
        assert conn.execute("SELECT count(*) FROM derivation_building").fetchone()[0] == 0
        assert derivations.current(conn, UNIT)
        # Changed output after the same checks stores a new generation.
        accept_runtime(database, "code-4")
        changed = [derivations.OutputRow("entries", '["e1"]', {"entry_id": "e2"})]
        minted = staged(database, run_id, changed)
        assert minted != kept and pointer(conn) == minted
        assert derivations.current(conn, UNIT)


def test_a_resumed_staged_cutoff_keeps_the_replay_bump_of_an_a_that_died(
    tmp_path: Path,
) -> None:
    """A retry's own capture already sees what a killed A moved; the building
    row's first witness still tells dependents (review finding, F2)."""
    from swingset.state.work import bump_revision

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")
        # Attempt 1: A rewrites live rows (the witnessed revision moves) and
        # commits; the process dies before pass 1, B and C.
        with derivations.chunking(conn, UNIT):
            with database.transaction(), derivations.group(conn):
                selection = derivations.capture(conn, UNIT, now=NOW)
                bump_revision(conn, "canonical")
                assert derivations.stage(
                    conn, selection, output=lambda reader: iter(ROWS), now=NOW, run_id=run_id
                )
        assert conn.execute("SELECT count(*) FROM derivation_building").fetchone()[0] == 1
        assert not derivations.current(conn, UNIT)
        # Attempt 2, same inputs: its A moves nothing, and its output is kept.
        assert staged(database, run_id, ROWS) == kept
        assert pointer(conn) == kept
        assert replay_version(conn) == 1
        assert conn.execute("SELECT count(*) FROM derivation_building").fetchone()[0] == 0
        assert derivations.current(conn, UNIT)


def test_an_unfinished_a_for_other_inputs_moves_the_replay_epoch_and_is_collected(
    tmp_path: Path,
) -> None:
    """An A whose inputs changed before C committed live rows the pointer does
    not stand for. The unit is not current until a run settles them, and that
    run tells dependents even when it only reuses its generation (I6)."""
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")
        with derivations.chunking(conn, UNIT):
            with database.transaction(), derivations.group(conn):
                selection = derivations.capture(conn, UNIT, now=NOW)
                assert derivations.stage(
                    conn, selection, output=lambda reader: iter(ROWS), now=NOW, run_id=run_id
                )
        # The inputs go back to the pointer's own; the building row remains.
        accept_runtime(database, "code-1")
        assert derivations.building(conn, UNIT)
        assert not derivations.current(conn, UNIT)
        # A single-path run reuses the pointer's generation and moves the epoch.
        assert rerun(database, run_id, ROWS) == kept
        assert replay_version(conn) == 1
        # Its derive_one then drops the orphan row, and the unit is current.
        assert derivations.collect_orphans(database, UNIT, keep=None) == 1
        assert not derivations.building(conn, UNIT)
        assert derivations.current(conn, UNIT)


#: Run in a subprocess: derive_one stages UNIT with a live-row move, then the
#: process is SIGKILLed between A and pass 1.
_KILL_BETWEEN_A_AND_C = """
import os, signal, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, sys.argv[3])
import swingset.project
from test_derivation_equivalences import staging_projector

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.schedule.derive import derive_one
from swingset.state import derivations
from swingset.state.db import open_database
from test_derivation_equivalences import UNIT

swingset.project.process_unit = staging_projector(move=True)


def die(*_args, **_kwargs):
    os.kill(os.getpid(), signal.SIGKILL)


derivations.finish_building = die
state, now = Path(sys.argv[1]), datetime.fromisoformat(sys.argv[2])
with open_database(state) as database:
    run_id = database.start_run(now)
    derive_one(database, Archive(state), UNIT, None, FakeClock(now), run_id)
sys.exit(3)
"""


def staging_projector(*, move: bool):
    """A project processor that stages UNIT's one row, optionally moving live rows in A."""

    def project(database, unit, _bundle, clock, run_id, selection=None):
        from swingset.state.work import bump_revision

        conn = database.connection
        with derivations.group(conn):
            captured = derivations.capture(conn, unit, now=clock.now())
            if move:
                bump_revision(conn, "canonical")
            assert derivations.stage(
                conn, captured, output=lambda reader: iter(ROWS), now=clock.now(), run_id=run_id
            )

    return project


def test_derive_one_killed_between_a_and_c_still_moves_the_replay_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import signal
    import subprocess
    import sys
    from datetime import timedelta

    import swingset.project
    from swingset.clock import FakeClock
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one

    state = tmp_path / "state"
    with open_database(state) as database:
        run_id = database.start_run(NOW)
        accept_runtime(database, "code-1")
        kept = rerun(database, run_id, ROWS)
        accept_runtime(database, "code-2")
    killed = subprocess.run(
        [
            sys.executable,
            "-c",
            _KILL_BETWEEN_A_AND_C,
            str(state),
            NOW.isoformat(),
            str(Path(__file__).parent),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr[-2000:]
    monkeypatch.setattr(swingset.project, "process_unit", staging_projector(move=False))
    clock = FakeClock(NOW)
    with open_database(state) as database:
        conn = database.connection
        assert conn.execute("SELECT count(*) FROM derivation_building").fetchone()[0] == 1
        assert replay_version(conn) == 0 and pointer(conn) == kept
        run_id = database.start_run(clock.now())
        # The dead attempt is taken over and counted; the unit backs off.
        held = derive_one(database, Archive(state), UNIT, None, clock, run_id)
        assert held.reason == "process_interrupted", held
        clock.sleep(timedelta(minutes=16).total_seconds())
        done = derive_one(database, Archive(state), UNIT, None, clock, run_id)
        assert not done.failed and done.reason is None, done
        assert pointer(conn) == kept
        # The resumed A moved nothing itself; the dead one did.
        assert replay_version(conn) == 1
        assert conn.execute("SELECT count(*) FROM derivation_building").fetchone()[0] == 0
