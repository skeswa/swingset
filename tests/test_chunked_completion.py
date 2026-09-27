"""Staged completion: large generations store in chunks after their live write (WP4)."""

import contextlib
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_h15_acceptance import source_fixture

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.schedule.cycle import versions
from swingset.schedule.derive import derive_one
from swingset.state import derivations
from swingset.state.db import Database, open_database
from swingset.state.inputs import capture
from swingset.state.work import WorkUnit, next_work

HISTORY = WorkUnit("project", "history", "all")


@pytest.fixture
def arranged(tmp_path):
    """The source fixture with every project unit but history materialized.

    Returns a closed state directory, so each test copies it and opens its own.
    """
    from swingset.project.process import process_unit

    generator = source_fixture.__wrapped__(tmp_path / "fixture")
    f = next(generator)
    for _ in range(40):
        unit = next_work(f.conn, "project", now=f.corpus.clock.now())
        if unit is None or unit == HISTORY:
            break
        process_unit(f.db, unit, f.bundle, f.corpus.clock, f.corpus.run)
    assert HISTORY in set(derivations.pending_units(f.conn, "project"))
    assert derivations.selected_generation(f.conn, HISTORY) is None
    f.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    arranged = SimpleNamespace(
        state=f.db.state_dir,
        now=f.corpus.clock.now(),
        run=f.corpus.run,
        bundle=f.bundle,
        config=f.config,
        overrides=f.overrides,
    )
    with contextlib.suppress(StopIteration):
        next(generator)
    return arranged


def twin(arranged, target: Path) -> Path:
    shutil.copytree(arranged.state, target)
    return target


def run_history(database: Database, arranged, tmp_path: Path, clock=None):
    return derive_one(
        database,
        Archive(tmp_path / "archive"),
        HISTORY,
        arranged.bundle,
        clock or FakeClock(arranged.now),
        arranged.run,
    )


def single(monkeypatch) -> None:
    monkeypatch.setattr(derivations, "stageable", lambda conn, unit: False)


def stored(conn: sqlite3.Connection, generation_id: str) -> dict[str, object]:
    label = conn.execute(
        "SELECT output_digest,row_count FROM derivation_generations WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    return {
        "generation_id": generation_id,
        "label": tuple(label),
        "refs": [
            tuple(row)
            for row in conn.execute(
                "SELECT ordinal,table_name,record_key,payload_sha256 FROM derivation_row_refs "
                "WHERE generation_id=? ORDER BY ordinal",
                (generation_id,),
            )
        ],
    }


def building(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT generation_id,label_rows,rows_stored FROM derivation_building "
            "ORDER BY generation_id"
        )
    ]


def expected_history(arranged, tmp_path, monkeypatch) -> dict[str, object]:
    """History on the single path, on a twin of the same state."""
    with monkeypatch.context() as patch:
        single(patch)
        with open_database(twin(arranged, tmp_path / "single")) as db:
            assert not run_history(db, arranged, tmp_path).failed
            generation = derivations.selected_generation(db.connection, HISTORY)
            assert generation is not None
            return stored(db.connection, generation)


def test_staged_history_stores_what_the_single_path_stores_one_chunk_per_write(
    arranged, tmp_path, monkeypatch
):
    expected = expected_history(arranged, tmp_path, monkeypatch)
    rows = expected["label"][1]
    assert rows > 3, "the fixture must span two chunks"
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    finished = []
    real_finish = derivations.finish_building
    monkeypatch.setattr(
        derivations,
        "finish_building",
        lambda *a, **k: finished.append(1) or real_finish(*a, **k),
    )
    writes: list[int] = []
    original = Database.transaction

    @contextlib.contextmanager
    def measured(self, **kwargs):
        top = not self.connection.in_transaction
        before = self.connection.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
        with original(self, **kwargs) as conn:
            yield conn
            if top:
                after = conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
                writes.append(after - before)

    monkeypatch.setattr(Database, "transaction", measured)
    with open_database(twin(arranged, tmp_path / "staged")) as db:
        outcome = run_history(db, arranged, tmp_path)
        assert not outcome.failed, outcome
        conn = db.connection
        generation = derivations.selected_generation(conn, HISTORY)
        assert finished == [1]
        assert stored(conn, str(generation)) == expected
        assert building(conn) == []
        assert derivations.current(conn, HISTORY)
        assert (
            conn.execute("SELECT count(*) FROM pending_work WHERE unit_kind='history'").fetchone()[
                0
            ]
            == 0
        )
        assert (
            conn.execute(
                "SELECT outcome FROM work_attempts WHERE stage='project' AND unit_kind='history' "
                "ORDER BY attempt_id DESC LIMIT 1"
            ).fetchone()[0]
            == "succeeded"
        )
        progress = derivations.cycle_progress(conn, arranged.run, None)
        assert progress["generations"].get("project/history") == 1
    # No write stored more than one chunk; A stored none of the output.
    assert max(writes) == 3
    assert sum(1 for count in writes if count) == -(-rows // 3)


def test_the_single_path_keeps_small_units_and_callers_in_a_transaction(arranged, tmp_path):
    with open_database(twin(arranged, tmp_path / "state")) as db:
        conn = db.connection
        event = WorkUnit("project", "event", "event-a")
        # No context: `accept-year` and other callers never stage.
        assert not derivations.stageable(conn, HISTORY)
        with derivations.chunking(conn, HISTORY):
            assert derivations.stageable(conn, HISTORY)
            assert not derivations.stageable(conn, event)
        with db.transaction(), derivations.chunking(conn, HISTORY):
            assert not derivations.stageable(conn, HISTORY)
        with derivations.chunking(conn, event):
            # event-a's kept generation is small.
            assert not derivations.stageable(conn, event)


KILL = textwrap.dedent(
    """
    import os
    import sys
    from datetime import datetime
    from pathlib import Path

    from swingset.clock import FakeClock
    from swingset.fetch.archive import Archive
    from swingset.schedule.cycle import versions
    from swingset.schedule.derive import derive_one
    from swingset.state import derivations
    from swingset.state.db import open_database
    from swingset.state.inputs import capture
    from swingset.state.work import WorkUnit

    state, config, overrides, now, run = sys.argv[1:6]
    derivations.CHUNK_ROWS = 3
    real = derivations._store_chunk
    calls = 0

    def store(*args, **kwargs):
        global calls
        calls += 1
        yield from real(*args, **kwargs)
        if calls == 2:
            # Inside chunk 2's write transaction, before it commits.
            os._exit(91)

    derivations._store_chunk = store
    with open_database(state) as db:
        bundle = capture(Path(config), Path(overrides), db.state_dir, versions())
        derive_one(
            db,
            Archive(Path(state) / "archive"),
            WorkUnit("project", "history", "all"),
            bundle,
            FakeClock(datetime.fromisoformat(now)),
            run,
        )
    sys.exit(0)
    """
)


def test_a_run_killed_between_chunks_resumes_by_ordinal(arranged, tmp_path, monkeypatch):
    expected = expected_history(arranged, tmp_path, monkeypatch)
    state = twin(arranged, tmp_path / "killed")
    killed = subprocess.run(
        [
            sys.executable,
            "-c",
            KILL,
            str(state),
            str(arranged.config),
            str(arranged.overrides),
            arranged.now.isoformat(),
            arranged.run,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert killed.returncode == 91, killed.stderr
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    with open_database(state) as db:
        conn = db.connection
        assert building(conn) == [(expected["generation_id"], expected["label"][1], 3)]
        assert derivations.selected_generation(conn, HISTORY) is None
        # The labelled but partial generation is not progress and not current.
        assert (
            "project/history"
            not in derivations.cycle_progress(conn, arranged.run, None)["generations"]
        )
        assert not derivations.current(conn, HISTORY)
        assert (
            conn.execute("SELECT count(*) FROM pending_work WHERE unit_kind='history'").fetchone()[
                0
            ]
            == 1
        )
        bundle = capture(arranged.config, arranged.overrides, db.state_dir, versions())
        clock = FakeClock(arranged.now)

        def resume():
            return derive_one(
                db, Archive(tmp_path / "archive"), HISTORY, bundle, clock, arranged.run
            )

        # The dead lease is taken over and the attempt closed; the unit waits
        # on that failure's clock, then resumes.
        held = resume()
        assert held.reason == "process_interrupted" and not held.failed, held
        assert building(conn) == [(expected["generation_id"], expected["label"][1], 3)]
        clock.sleep(16 * 60)
        resumed = resume()
        assert not resumed.failed and resumed.reason is None, resumed
        assert stored(conn, str(derivations.selected_generation(conn, HISTORY))) == expected
        assert building(conn) == []
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert derivations.current(conn, HISTORY)


def kill_in_chunk_two(arranged, state: Path) -> None:
    killed = subprocess.run(
        [
            sys.executable,
            "-c",
            KILL,
            str(state),
            str(arranged.config),
            str(arranged.overrides),
            arranged.now.isoformat(),
            arranged.run,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert killed.returncode == 91, killed.stderr


@pytest.mark.parametrize("staged", [True, False], ids=["staged", "inline"])
def test_a_partial_generation_whose_building_row_was_collected_is_finished_not_reused(
    arranged, tmp_path, monkeypatch, staged
):
    """`collect_orphans` can drop the building row of a partial generation. When
    the unit's inputs return to it, the run finishes storing it by ordinal; the
    pointer never selects it with rows missing."""
    expected = expected_history(arranged, tmp_path, monkeypatch)
    state = twin(arranged, tmp_path / "killed")
    kill_in_chunk_two(arranged, state)
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    if not staged:
        single(monkeypatch)
    generation_id = str(expected["generation_id"])
    with open_database(state) as db:
        conn = db.connection
        with db.transaction():
            # What collect_orphans(keep=None) leaves after another run of the unit.
            conn.execute("DELETE FROM derivation_building")
        assert len(stored(conn, generation_id)["refs"]) < expected["label"][1]
        bundle = capture(arranged.config, arranged.overrides, db.state_dir, versions())
        clock = FakeClock(arranged.now)
        archive = Archive(tmp_path / "archive")
        held = derive_one(db, archive, HISTORY, bundle, clock, arranged.run)
        assert held.reason == "process_interrupted" and not held.failed, held
        clock.sleep(16 * 60)
        resumed = derive_one(db, archive, HISTORY, bundle, clock, arranged.run)
        assert not resumed.failed and resumed.reason is None, resumed
        assert derivations.selected_generation(conn, HISTORY) == generation_id
        assert stored(conn, generation_id) == expected
        assert building(conn) == []
        assert derivations.current(conn, HISTORY)


def test_step_c_refuses_to_select_a_partial_generation_as_reused(arranged, tmp_path, monkeypatch):
    """The last guard: step C counts a reused generation's references too."""
    expected = expected_history(arranged, tmp_path, monkeypatch)
    state = twin(arranged, tmp_path / "killed")
    kill_in_chunk_two(arranged, state)
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    # Without the stage-time check the run would take the reuse branch.
    monkeypatch.setattr(derivations, "_partial_label", lambda conn, generation_id: None)
    generation_id = str(expected["generation_id"])
    with open_database(state) as db:
        conn = db.connection
        with db.transaction():
            conn.execute("DELETE FROM derivation_building")
        bundle = capture(arranged.config, arranged.overrides, db.state_dir, versions())
        clock = FakeClock(arranged.now)
        archive = Archive(tmp_path / "archive")
        derive_one(db, archive, HISTORY, bundle, clock, arranged.run)
        clock.sleep(16 * 60)
        refused = derive_one(db, archive, HISTORY, bundle, clock, arranged.run)
        assert refused.reason is not None, refused
        assert derivations.selected_generation(conn, HISTORY) != generation_id
        assert not derivations.current(conn, HISTORY)


def test_identical_output_records_an_equivalence_and_stores_no_rows(
    arranged, tmp_path, monkeypatch
):
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    with open_database(twin(arranged, tmp_path / "state")) as db:
        conn = db.connection
        assert not run_history(db, arranged, tmp_path).failed
        kept = derivations.selected_generation(conn, HISTORY)
        references = conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
        generations = conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0]
        with db.transaction():
            # A new input to history that changes none of its output.
            derivations.bump_replay(conn, "project", "event", now=arranged.now)
        assert not derivations.current(conn, HISTORY)
        clock = FakeClock(arranged.now)
        clock.sleep(60)
        assert not run_history(db, arranged, tmp_path, clock).failed
        assert derivations.selected_generation(conn, HISTORY) == kept
        assert conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0] == references
        assert (
            conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0] == generations
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM derivation_equivalences WHERE unit_kind='history'"
            ).fetchone()[0]
            == 1
        )
        assert building(conn) == []
        assert derivations.current(conn, HISTORY)


def test_nondeterministic_output_fails_transient_and_keeps_the_building_row(
    arranged, tmp_path, monkeypatch
):
    from swingset.project import materialization

    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    real = materialization.output_rows
    reads = 0

    def unstable(conn, unit):
        nonlocal reads
        reads += 1
        for index, row in enumerate(real(conn, unit)):
            if reads == 2 and index == 3:
                row = derivations.OutputRow(row.table, row.key, {**row.payload, "drift": 1})
            yield row

    monkeypatch.setattr(materialization, "output_rows", unstable)
    with open_database(twin(arranged, tmp_path / "state")) as db:
        conn = db.connection
        outcome = run_history(db, arranged, tmp_path)
        assert outcome.failed and outcome.reason == "nondeterministic_output"
        attempt = conn.execute(
            "SELECT outcome,reason_code FROM work_attempts WHERE unit_kind='history' "
            "ORDER BY attempt_id DESC LIMIT 1"
        ).fetchone()
        assert tuple(attempt) == ("transient", "nondeterministic_output")
        assert len(building(conn)) == 1
        assert derivations.selected_generation(conn, HISTORY) is None
        assert (
            conn.execute("SELECT count(*) FROM pending_work WHERE unit_kind='history'").fetchone()[
                0
            ]
            == 1
        )


def test_an_input_change_between_a_and_c_supersedes_and_the_retry_rewrites(
    arranged, tmp_path, monkeypatch
):
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    real = derivations._store_building
    moved = []

    def move_input(database, deferred, label):
        if not moved:
            moved.append(deferred.selection.generation_id)
            with database.transaction() as conn:
                derivations.bump_replay(conn, "project", "event", now=deferred.now)
        return real(database, deferred, label)

    monkeypatch.setattr(derivations, "_store_building", move_input)
    written = []
    from swingset.project import process

    real_dispatch = process._dispatch

    def dispatch(conn, unit, *args, **kwargs):
        written.append(unit)
        return real_dispatch(conn, unit, *args, **kwargs)

    monkeypatch.setattr(process, "_dispatch", dispatch)
    with open_database(twin(arranged, tmp_path / "state")) as db:
        conn = db.connection
        first = run_history(db, arranged, tmp_path)
        assert first.reason == "inputs_changed" and not first.failed
        assert derivations.selected_generation(conn, HISTORY) is None
        # The superseded generation's building row is an orphan for the collector.
        assert [row[0] for row in building(conn)] == moved
        clock = FakeClock(arranged.now)
        clock.sleep(1)
        second = run_history(db, arranged, tmp_path, clock)
        assert not second.failed, second
        assert written == [HISTORY, HISTORY]
        generation = derivations.selected_generation(conn, HISTORY)
        assert generation is not None and generation != moved[0]
        # The superseded A's building row was collected once the retry
        # finished (I6).
        assert building(conn) == []
        assert derivations.current(conn, HISTORY)


def test_a_grouped_source_event_completes_on_the_single_path(arranged, tmp_path, monkeypatch):
    # Every unit whose kept generation has any rows at all is large here.
    monkeypatch.setattr(derivations, "CHUNK_ROWS", -1)
    staged = []
    real = derivations.stage
    monkeypatch.setattr(
        derivations, "stage", lambda *a, **k: staged.append(real(*a, **k)) or staged[-1]
    )
    with open_database(twin(arranged, tmp_path / "state")) as db:
        conn = db.connection
        units = [
            WorkUnit(*row)
            for row in conn.execute(
                "SELECT stage,unit_kind,unit_id FROM derivation_scopes "
                "WHERE stage='project' AND unit_kind='source_event'"
            )
        ]
        assert units
        with db.transaction():
            derivations.bump_replay(conn, "project", "map", now=arranged.now)
        outcome = derive_one(
            db,
            Archive(tmp_path / "archive"),
            units[0],
            arranged.bundle,
            FakeClock(arranged.now),
            arranged.run,
        )
        assert not outcome.failed, outcome
        # It was eligible, but its group also completed its event.
        assert staged == [None]
        assert building(conn) == []
        assert derivations.current(conn, units[0])


def test_accept_year_completes_history_on_the_single_path(arranged, tmp_path, monkeypatch):
    from swingset.project.history import accept_year

    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    finished = []
    monkeypatch.setattr(derivations, "finish_building", lambda *a, **k: finished.append(1))
    with open_database(twin(arranged, tmp_path / "state")) as db:
        conn = db.connection
        year = conn.execute("SELECT min(year) FROM events").fetchone()[0]
        accept_year(
            conn,
            int(year),
            accepted_by="offline-owner",
            accepted_at=datetime.fromisoformat(arranged.now.isoformat()).isoformat(),
        )
        assert finished == []
        assert building(conn) == []
        assert derivations.selected_generation(conn, HISTORY) is not None


def test_the_accept_year_command_stores_history_in_chunks(arranged, tmp_path, monkeypatch):
    """Rehearsal finding 2: one 61 s single-path write on the Mac. The command
    commits the acceptance with history's live rows, then stores in chunks."""
    from swingset.cli import main

    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    finished = []
    real_finish = derivations.finish_building
    monkeypatch.setattr(
        derivations,
        "finish_building",
        lambda *a, **k: finished.append(1) or real_finish(*a, **k),
    )
    state = twin(arranged, tmp_path / "state")
    with open_database(state) as db:
        year = db.connection.execute("SELECT min(year) FROM events").fetchone()[0]
    command = ["accept-year", "--state", str(state), "--config", str(arranged.config)]
    assert main([*command, "--year", str(year), "--by", "offline-owner"]) == 0
    assert finished == [1]
    with open_database(state) as db:
        conn = db.connection
        assert conn.execute("SELECT count(*) FROM history_acceptance").fetchone()[0] >= 1
        assert building(conn) == []
        generation = derivations.selected_generation(conn, HISTORY)
        assert generation is not None
        label = conn.execute(
            "SELECT row_count FROM derivation_generations WHERE generation_id=?", (generation,)
        ).fetchone()[0]
        references = conn.execute(
            "SELECT count(*) FROM derivation_row_refs WHERE generation_id=?", (generation,)
        ).fetchone()[0]
        assert references == label > 3
        assert (
            conn.execute("SELECT count(*) FROM pending_work WHERE unit_kind='history'").fetchone()[
                0
            ]
            == 0
        )


def test_a_resume_after_its_generation_was_removed_starts_over(arranged, tmp_path, monkeypatch):
    """A building row whose generation is gone (a restore from an older copy)
    restarts: same id and label, every ordinal stored once."""
    expected = expected_history(arranged, tmp_path, monkeypatch)
    state = twin(arranged, tmp_path / "killed")
    killed = subprocess.run(
        [
            sys.executable,
            "-c",
            KILL,
            str(state),
            str(arranged.config),
            str(arranged.overrides),
            arranged.now.isoformat(),
            arranged.run,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert killed.returncode == 91, killed.stderr
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    generation_id = str(expected["generation_id"])
    with open_database(state) as db:
        conn = db.connection
        with db.transaction():
            conn.execute("DROP TRIGGER derivation_row_ref_no_delete")
            conn.execute("DROP TRIGGER derivation_generation_no_delete")
            conn.execute("DELETE FROM derivation_row_refs WHERE generation_id=?", (generation_id,))
            conn.execute(
                "DELETE FROM derivation_generations WHERE generation_id=?", (generation_id,)
            )
        bundle = capture(arranged.config, arranged.overrides, db.state_dir, versions())
        clock = FakeClock(arranged.now)
        archive = Archive(tmp_path / "archive")
        held = derive_one(db, archive, HISTORY, bundle, clock, arranged.run)
        assert held.reason == "process_interrupted"
        clock.sleep(16 * 60)
        resumed = derive_one(db, archive, HISTORY, bundle, clock, arranged.run)
        assert not resumed.failed and resumed.reason is None, resumed
        assert derivations.selected_generation(conn, HISTORY) == generation_id
        assert stored(conn, generation_id) == expected
        assert building(conn) == []


def test_unfinished_staged_output_is_never_read_as_output(arranged, tmp_path, monkeypatch):
    """A history generation a staged run stopped storing, with or without its
    building row, is no release anchor, takes no retention window slot, and
    the plan lists it `incomplete` (F8, I6)."""
    from datetime import timedelta

    from swingset.build.closure import select
    from swingset.state.retention import plan

    single(monkeypatch)
    with open_database(twin(arranged, tmp_path / "state")) as db:
        conn = db.connection
        assert not run_history(db, arranged, tmp_path).failed
        whole = derivations.selected_generation(conn, HISTORY)
        assert whole is not None
        later = (arranged.now + timedelta(minutes=5)).isoformat()
        with db.transaction():
            # A later generation whose B stored one of its 5 rows, then stopped.
            conn.execute(
                "INSERT INTO derivation_generations SELECT 'dg_partial',stage,unit_kind,unit_id,"
                "'fp-partial',recipe_json,dependency_set_id,generation_id,output_digest,5,?,run_id "
                "FROM derivation_generations WHERE generation_id=?",
                (later, whole),
            )
            conn.execute(
                "INSERT INTO derivation_row_refs SELECT 'dg_partial',0,table_name,record_key,"
                "payload_sha256 FROM derivation_row_refs WHERE generation_id=? AND ordinal=0",
                (whole,),
            )
        cutoff = arranged.now + timedelta(hours=1)
        chosen = {row["generation_id"] for row in select(conn, cutoff=cutoff).selected}
        assert whole in chosen and "dg_partial" not in chosen

        def listed():
            content = plan(
                conn,
                db.state_dir,
                max_database_bytes=10**12,
                recent_window=1,
                collect_older_than=0,
            ).content
            windows = {
                generation
                for root in content["roots"]
                if root["kind"] == "window"
                for generation in root["generations"]
            }
            lists = {row["generation_id"]: row["list"] for row in content["generations"]}
            return windows, lists, content["totals"]

        windows, lists, totals = listed()
        # The newest window slot of history is still the whole generation.
        assert whole in windows and "dg_partial" not in windows
        assert lists["dg_partial"] == "incomplete" and lists[whole] == "local"
        assert totals["incomplete_generations"] == 1
        # Maintain counts it while no building row names it.
        assert derivations.incomplete_generations(conn) == {"rows": 1, "refs": 1}
        # Still building: the same, by its building row.
        with db.transaction():
            conn.execute(
                "INSERT INTO derivation_building(generation_id,stage,unit_kind,unit_id,"
                "input_fingerprint,run_id,started_at,updated_at) "
                "VALUES ('dg_partial','project','history','all','fp-partial',?,?,?)",
                (arranged.run, later, later),
            )
        windows, lists, _ = listed()
        assert "dg_partial" not in windows and lists["dg_partial"] == "incomplete"
        assert derivations.incomplete_generations(conn) == {"rows": 0, "refs": 0}
