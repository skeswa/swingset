"""Migration 34: the work queue and the other loop tables.

The migration adds `runs.loop` and five mutable tables, and seeds a queue row
for each pending unit whose last attempt failed: a blocked unit waits on a
clock, and a transient one keeps its run of failures one short of quarantine
(docs/plans/architecture.md, section 9). The schema tests check the CHECK
constraints, that an active publication blocks writes to each table, that a
checkpoint at 34 round-trips, and that code at 33 refuses a 34 file.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from swingset.clock import FakeClock
from swingset.state import db as db_module
from swingset.state import loop_state, queue
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

NOW = datetime(2026, 9, 25, tzinfo=UTC)
AT = "2026-09-25T00:00:00Z"
TABLES = ("work_queue", "acquisition_plan", "guard_reasons", "loop_state", "derivation_building")


def insert(conn: sqlite3.Connection, table: str) -> None:
    """One valid row in `table`."""
    values = {
        "work_queue": "INSERT INTO work_queue(stage,unit_kind,unit_id,partition,state,"
        "enqueued_at,updated_at) VALUES ('parse','snapshot','s1','snapshot/s1','ready',?,?)",
        "acquisition_plan": "INSERT INTO acquisition_plan(plan_key,kind,year,reason,"
        "planned_at,run_id) VALUES ('year:2024','year',2024,'eligible',?,?)",
        "guard_reasons": "INSERT INTO guard_reasons(subject,reason,loop,since,last_seen_at,"
        "expires_at) VALUES ('host:a.test','request budget','acquire',?,?,'2026-09-26')",
        "loop_state": "INSERT INTO loop_state(loop,run_id,updated_at) VALUES ('derive',?,?)",
        "derivation_building": "INSERT INTO derivation_building(generation_id,stage,unit_kind,"
        "unit_id,input_fingerprint,run_id,started_at,updated_at) "
        "VALUES ('g1','project','history','all','fp','r1',?,?)",
    }
    conn.execute(values[table], (AT, AT))


def attempt(
    conn: sqlite3.Connection,
    run_id: str,
    unit: WorkUnit,
    outcome: str,
    reason: str | None,
    *,
    fingerprint: str = "fp",
    retry_at: datetime | None = None,
    evidence: dict[str, str] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO work_attempts(stage,unit_kind,unit_id,queue_generation,retry_generation,"
        "work_token,input_fingerprint,run_id,started_at,finished_at,outcome,reason_code,"
        "evidence_json,retry_at) VALUES (?,?,?,1,0,'token',?,?,?,?,?,?,?,?)",
        (
            unit.stage,
            unit.unit_kind,
            unit.unit_id,
            fingerprint,
            run_id,
            AT,
            AT,
            outcome,
            reason,
            json.dumps(evidence or {}),
            None if retry_at is None else retry_at.isoformat(),
        ),
    )


def pending(conn: sqlite3.Connection, unit: WorkUnit) -> None:
    conn.execute(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES (?,?,?,?)",
        (unit.stage, unit.unit_kind, unit.unit_id, AT),
    )


BLOCKED = WorkUnit("parse", "snapshot", "blocked")
FAILING = WorkUnit("project", "event", "failing")
EXCEPTION = WorkUnit("project", "event", "raising")
IO = WorkUnit("link", "dancer", "io")
DONE = WorkUnit("parse", "snapshot", "done")
FRESH = WorkUnit("parse", "snapshot", "fresh")
CHANGED = WorkUnit("project", "event", "changed")


def test_migration_from_33_seeds_failing_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 33)
        with open_database(state, lock=False) as database:
            assert database.schema_version == 33
            conn = database.connection
            run_id = database.start_run(NOW)
            with database.transaction():
                for unit in (BLOCKED, FAILING, EXCEPTION, IO, DONE, FRESH, CHANGED):
                    pending(conn, unit)
                attempt(conn, run_id, BLOCKED, "blocked", "parse_failed")
                for _ in range(3):
                    attempt(
                        conn,
                        run_id,
                        FAILING,
                        "transient",
                        "write_deadline_exceeded",
                        retry_at=NOW + timedelta(minutes=15),
                    )
                for _ in range(4):
                    attempt(
                        conn,
                        run_id,
                        EXCEPTION,
                        "transient",
                        "unit_exception",
                        evidence={"exception": "ValueError", "message": "bad row"},
                        retry_at=NOW + timedelta(hours=1),
                    )
                for _ in range(25):
                    attempt(conn, run_id, IO, "transient", "unit_io_error", retry_at=NOW)
                attempt(conn, run_id, DONE, "transient", "unit_io_error", retry_at=NOW)
                attempt(conn, run_id, DONE, "succeeded", None)
                # Two failures on an old fingerprint, one on the new: a run of 1.
                attempt(conn, run_id, CHANGED, "transient", "nondeterministic_output")
                attempt(conn, run_id, CHANGED, "transient", "nondeterministic_output")
                attempt(
                    conn, run_id, CHANGED, "transient", "nondeterministic_output", fingerprint="new"
                )
            for table in TABLES:
                assert (
                    conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
                    is None
                )

    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 34)
    with open_database(state, lock=False, clock=FakeClock(NOW)) as database:
        conn = database.connection
        assert database.schema_version == 34
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 34
        assert "loop" in {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in TABLES:
            for action in ("insert", "update", "delete"):
                name = f"publication_fence_{table}_{action}"
                assert conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
                ).fetchone()
            assert not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name LIKE ?",
                (f"history_timing_{table}_%",),
            ).fetchone()

        blocked = queue.get(conn, BLOCKED)
        assert blocked is not None
        assert blocked.state == "waiting_input"
        assert blocked.attempts == 1
        assert blocked.error_class == "parse_failed"
        assert blocked.partition == "snapshot/blocked"
        assert blocked.next_attempt_at == queue.at(NOW + timedelta(hours=24))

        failing = queue.get(conn, FAILING)
        assert failing is not None
        assert (failing.state, failing.attempts) == ("ready", 2)
        assert failing.next_attempt_at == queue.at(NOW + timedelta(minutes=15))
        assert failing.lease_owner is None and failing.quarantined_at is None

        raising = queue.get(conn, EXCEPTION)
        assert raising is not None
        assert (raising.state, raising.attempts) == ("ready", 2)
        assert raising.error_class == "unit_exception:ValueError"
        assert raising.last_error == "bad row"

        io = queue.get(conn, IO)
        assert io is not None
        assert (io.state, io.attempts) == ("ready", 19)

        changed = queue.get(conn, CHANGED)
        assert changed is not None
        assert (changed.attempts, changed.input_fingerprint) == (1, "new")

        assert queue.get(conn, DONE) is None
        assert queue.get(conn, FRESH) is None
        assert queue.counts_by_state(conn) == {
            "ready": 4,
            "leased": 0,
            "waiting_input": 1,
            "quarantined": 0,
        }


def test_seed_keeps_a_later_retry_and_parks_unsupported_pages(tmp_path: Path) -> None:
    unsupported = WorkUnit("parse", "snapshot", "manual")
    unavailable = WorkUnit("parse", "snapshot", "lost")
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run_id = database.start_run(NOW)
        with database.transaction():
            pending(conn, unsupported)
            pending(conn, unavailable)
            attempt(
                conn,
                run_id,
                unsupported,
                "transient",
                "unsupported_page_kind",
                retry_at=NOW + timedelta(days=30),
            )
            attempt(
                conn,
                run_id,
                unavailable,
                "unavailable",
                "artifact_unavailable",
                retry_at=NOW + timedelta(hours=1),
            )
            assert queue.seed(conn, NOW) == {
                "ready": 0,
                "leased": 0,
                "waiting_input": 2,
                "quarantined": 0,
            }
        parked = queue.get(conn, unsupported)
        assert parked is not None
        assert parked.state == "waiting_input"
        assert parked.next_attempt_at == queue.at(NOW + timedelta(days=30))
        lost = queue.get(conn, unavailable)
        assert lost is not None
        assert lost.next_attempt_at == queue.at(NOW + timedelta(hours=24))


def test_check_constraints_refuse_bad_rows(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        base = (
            "INSERT INTO work_queue(stage,unit_kind,unit_id,partition,state,attempts,"
            "lease_owner,lease_until,next_attempt_at,enqueued_at,updated_at) "
            "VALUES (?,'snapshot','s1','snapshot/s1',?,?,?,?,?,?,?)"
        )
        refused = [
            ("parse", "leased", 0, None, "2026-09-26", None),
            ("parse", "leased", 0, "derive:r1", None, None),
            ("parse", "ready", 0, "derive:r1", "2026-09-26", None),
            ("parse", "quarantined", 3, None, None, None),
            ("parse", "waiting_input", 1, None, None, None),
            ("parse", "sleeping", 0, None, None, None),
            ("build", "ready", 0, None, None, None),
            ("parse", "ready", -1, None, None, None),
        ]
        for stage, state, attempts, owner, until, due in refused:
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                with database.transaction():
                    conn.execute(base, (stage, state, attempts, owner, until, due, AT, AT))
        with database.transaction():
            conn.execute(base, ("parse", "leased", 0, "derive:r1", "2026-09-26", None, AT, AT))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            with database.transaction():
                conn.execute(
                    "INSERT INTO acquisition_plan(plan_key,kind,reason,planned_at,run_id) "
                    "VALUES ('x','month','eligible',?,'r1')",
                    (AT,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            with database.transaction():
                conn.execute("INSERT INTO loop_state(loop,updated_at) VALUES ('fetch',?)", (AT,))


@pytest.mark.parametrize("table", TABLES)
def test_an_active_publication_blocks_writes(tmp_path: Path, table: str) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            conn.execute(
                "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,"
                "control_revision,all_sources,all_kinds,state) "
                "VALUES ('publishing','publication',?,0,1,1,'active')",
                (AT,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="active publication fences"):
            with database.transaction():
                insert(conn, table)
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_queue_and_loop_state_rows_round_trip(tmp_path: Path) -> None:
    unit = WorkUnit("project", "event", "e1")
    row = queue.QueueRow(
        stage="project",
        unit_kind="event",
        unit_id="e1",
        partition=queue.partition_of(unit),
        state="leased",
        attempts=1,
        error_class=None,
        input_fingerprint="fp",
        lease_owner="derive:r1",
        lease_until=AT,
        next_attempt_at=None,
        last_reason=None,
        last_error=None,
        enqueued_at=AT,
        updated_at=AT,
        quarantined_at=None,
    )
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            queue.upsert(conn, row)
            assert queue.get(conn, unit) == row
            ready = queue.QueueRow(
                **{**row.__dict__, "state": "ready", "lease_owner": None, "lease_until": None}
            )
            queue.upsert(conn, ready)
            assert queue.get(conn, unit) == ready
            assert queue.counts_by_class(conn, "ready") == {"": 1}
            assert queue.delete(conn, unit)
            assert not queue.delete(conn, unit)

            state = loop_state.LoopStateRow(loop="derive", updated_at=AT, run_id="r1", failed=0)
            loop_state.upsert(conn, state)
            assert loop_state.get(conn, "derive") == state
            assert loop_state.all_rows(conn) == [state]
            assert loop_state.delete(conn, "derive")
            assert loop_state.get(conn, "derive") is None


def test_error_class_names_the_exception_type() -> None:
    assert queue.error_class("unit_exception", '{"exception": "KeyError"}') == (
        "unit_exception:KeyError"
    )
    assert queue.error_class("unit_exception", "{}") == "unit_exception"
    assert queue.error_class("unit_exception", "not json") == "unit_exception"
    assert queue.error_class("unit_io_error", '{"exception": "OSError"}') == "unit_io_error"
    assert queue.Policy().quarantine_threshold("unit_io_error") == 20
    assert queue.Policy().quarantine_threshold("unit_exception:KeyError") == 3


def test_a_checkpoint_at_schema_34_verifies_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swingset.backup.checkpoint import (
        create_checkpoint,
        restore_checkpoint,
        verify_checkpoint,
    )

    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 34)
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        conn = database.connection
        with database.transaction():
            for table in TABLES:
                insert(conn, table)
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
    manifest = verify_checkpoint(checkpoint.path, maximum_schema_version=34)
    assert int(manifest["schema_version"]) == 34

    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=34)
    with open_database(restored, lock=False, allow_restore_pending=True) as database:
        assert database.schema_version == 34
        assert {
            table: sorted(
                tuple(row) for row in database.connection.execute(f"SELECT * FROM {table}")
            )
            for table in TABLES
        } == expected


def test_schema_33_code_refuses_a_schema_34_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 34)
    with open_database(state, lock=False) as database:
        assert database.schema_version == 34
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 33)
    with pytest.raises(RuntimeError, match="schema 34 is newer than supported 33"):
        open_database(state, lock=False)


# WP3: the queue's policy, in `derive_one` and on its own (D-0216).


@pytest.mark.parametrize(
    "attempts, minutes", [(1, 15), (2, 30), (3, 60), (5, 240), (6, 360), (9, 360)]
)
def test_backoff_doubles_from_fifteen_minutes_to_six_hours(attempts: int, minutes: int) -> None:
    assert queue.backoff(attempts) == timedelta(minutes=minutes)


@pytest.mark.parametrize("attempts, hours", [(1, 24), (2, 48), (3, 96), (4, 168), (8, 168)])
def test_waiting_doubles_from_a_day_to_a_week(attempts: int, hours: int) -> None:
    assert queue.waiting_delay(attempts) == timedelta(hours=hours)


def _fail(database, unit: WorkUnit, reason: str, *, fingerprint: str = "fp", now=NOW, **kwargs):
    with database.transaction() as conn:
        return queue.fail(conn, unit, reason=reason, fingerprint=fingerprint, now=now, **kwargs)


def test_three_failures_of_one_class_quarantine_and_a_new_fingerprint_restarts(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "state") as database:
        for count, delay in ((1, timedelta(minutes=15)), (2, timedelta(minutes=30))):
            failure = _fail(database, FAILING, "unit_exception", exception="ValueError")
            assert (failure.state, failure.attempts, failure.next_attempt_at) == (
                "ready",
                count,
                NOW + delay,
            )
        failure = _fail(database, FAILING, "unit_exception", exception="ValueError")
        assert failure.quarantined and failure.counted
        assert failure.next_attempt_at == NOW + timedelta(days=7)
        row = queue.get(database.connection, FAILING)
        assert row is not None
        assert (row.state, row.attempts, row.error_class, row.quarantined_at) == (
            "quarantined",
            3,
            "unit_exception:ValueError",
            queue.at(NOW),
        )
        # New input starts the count again.
        failure = _fail(
            database, FAILING, "unit_exception", exception="ValueError", fingerprint="new"
        )
        assert (failure.state, failure.attempts) == ("ready", 1)


def test_alternating_classes_never_quarantine(tmp_path: Path) -> None:
    with open_database(tmp_path / "state") as database:
        for _ in range(5):
            for exception in ("ValueError", "KeyError"):
                failure = _fail(database, FAILING, "unit_exception", exception=exception)
                assert (failure.state, failure.attempts) == ("ready", 1)
            failure = _fail(database, FAILING, "write_deadline_exceeded")
            assert (failure.state, failure.attempts) == ("ready", 1)


def test_io_errors_retry_in_a_minute_and_quarantine_at_twenty(tmp_path: Path) -> None:
    with open_database(tmp_path / "state") as database:
        for count in range(1, 20):
            failure = _fail(database, IO, "unit_io_error", exception="OSError")
            assert (failure.state, failure.attempts) == ("ready", count)
            assert failure.next_attempt_at == NOW + timedelta(seconds=60)
        failure = _fail(database, IO, "unit_io_error", exception="OSError")
        assert failure.quarantined and failure.attempts == 20


def test_waiting_classes_wait_and_a_publication_fence_is_never_counted(tmp_path: Path) -> None:
    with open_database(tmp_path / "state") as database:
        first = _fail(database, BLOCKED, "parse_failed", waiting=True)
        assert (first.state, first.repeat, first.counted) == ("waiting_input", False, True)
        assert first.next_attempt_at == NOW + timedelta(hours=24)
        fenced = _fail(database, BLOCKED, "publication_fenced")
        assert (fenced.state, fenced.counted, fenced.attempts) == ("ready", False, 1)
        assert fenced.next_attempt_at == NOW + timedelta(seconds=60)
        again = _fail(database, BLOCKED, "parse_failed", waiting=True)
        assert (again.state, again.repeat, again.counted) == ("waiting_input", True, False)
        assert again.next_attempt_at == NOW + timedelta(hours=48)
        parked = _fail(database, EXCEPTION, "unsupported_page_kind", exception="KeyError")
        assert (parked.state, parked.counted) == ("waiting_input", False)
        assert parked.next_attempt_at == NOW + timedelta(days=30)
        for _ in range(5):
            assert not _fail(database, EXCEPTION, "unsupported_page_kind").quarantined


def _unit_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raises: dict[str, type]):
    """Project units whose processor completes, or raises for the named units."""
    import swingset.project
    from swingset.state import derivations

    clock = FakeClock()
    seen: list[str | None] = []

    def project(database, unit, _bundle, _clock, run_id, selection=None):
        row = queue.get(database.connection, unit)
        seen.append(row.lease_owner if row else None)
        if unit.unit_id in raises:
            raise raises[unit.unit_id]("broken projector")
        derivations.complete(
            database.connection,
            selection,
            rows=(),
            now=clock.now(),
            run_id=run_id,
        )
        database.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

    monkeypatch.setattr(swingset.project, "process_unit", project)
    return clock, seen


def _bundle(tmp_path: Path):
    from swingset.config import Config
    from swingset.state.inputs import InputBundle

    return InputBundle("test", tmp_path, {}, Config({}, {}))


def test_a_lease_is_held_while_the_unit_runs_and_success_deletes_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state.work import enqueue

    unit = WorkUnit("project", "dancer", "d1")
    clock, seen = _unit_world(tmp_path, monkeypatch, {})
    with open_database(tmp_path / "state") as db:
        run = db.start_run(clock.now())
        enqueue(db.connection, [unit], enqueued_at=clock.now().isoformat())
        with queue.owning("derive", run):
            outcome = derive_one(db, Archive(tmp_path), unit, _bundle(tmp_path), clock, run)
        assert not outcome.failed
        assert seen == [f"derive:{run}"]
        assert queue.get(db.connection, unit) is None


def test_a_poison_unit_quarantines_on_its_third_failure_and_waits_seven_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state.attempts import eligible, latest_attempt
    from swingset.state.work import enqueue, next_work

    unit = WorkUnit("project", "dancer", "poison")
    clock, _ = _unit_world(tmp_path, monkeypatch, {"poison": ValueError})
    with open_database(tmp_path / "state") as db:
        run = db.start_run(clock.now())
        enqueue(db.connection, [unit], enqueued_at=clock.now().isoformat())
        for delay in (15, 30):
            outcome = derive_one(db, Archive(tmp_path), unit, _bundle(tmp_path), clock, run)
            assert outcome.failed and not outcome.quarantined
            assert next_work(db.connection, "project", now=clock.now()) is None
            clock.sleep(delay * 60)
            assert next_work(db.connection, "project", now=clock.now()) == unit
        outcome = derive_one(db, Archive(tmp_path), unit, _bundle(tmp_path), clock, run)
        assert outcome.failed and outcome.quarantined
        row = queue.get(db.connection, unit)
        assert row is not None
        assert (row.state, row.attempts, row.error_class) == (
            "quarantined",
            3,
            "unit_exception:ValueError",
        )
        latest = latest_attempt(db.connection, unit)
        assert latest is not None
        assert latest["retry_at"] == (clock.now() + timedelta(days=7)).isoformat()
        # Not before its clock, whatever the backoff would have said.
        clock.sleep(6 * 3600 + 1)
        assert next_work(db.connection, "project", now=clock.now()) is None
        # A different input fingerprint makes it eligible at once.
        assert eligible(db.connection, unit, "a new input", clock.now())
        # The seven-day clock wakes it; the next failure quarantines again.
        clock.sleep(7 * 86400)
        assert queue.wake(db, clock.now()) == 1
        assert queue.get(db.connection, unit).state == "ready"  # type: ignore[union-attr]
        assert next_work(db.connection, "project", now=clock.now()) == unit


def _running(db, unit: WorkUnit, owner: queue.Owner, now: datetime) -> int:
    """A unit mid-attempt under `owner`, as a process that died would leave it."""
    from swingset.state.attempts import begin_attempt
    from swingset.state.control_scopes import for_unit
    from swingset.state.controls import admission, bind_work_attempt

    with admission(
        db,
        action_id="work_dead",
        action_kind=unit.stage,
        scope=for_unit(db.connection, unit),
        now=now,
        run_id=owner.run_id,
    ) as conn:
        queue.lease(db, unit, owner=owner, now=now)
        attempt = begin_attempt(db, unit, now=now, run_id=owner.run_id)
        bind_work_attempt(conn, "work_dead", attempt.attempt_id)
    return attempt.attempt_id


@pytest.mark.parametrize(
    "holder, wait, taken",
    [
        ("cycle", 0, False),  # another loop's live lease is left alone
        ("cycle", 1801, True),  # an expired lease is dead
        ("derive", 0, True),  # this loop's own lock proves its owner dead
    ],
)
def test_a_dead_lease_is_taken_over_and_its_attempt_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, holder: str, wait: int, taken: bool
) -> None:
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state.attempts import eligible
    from swingset.state.work import enqueue

    unit = WorkUnit("project", "dancer", "d1")
    clock, _ = _unit_world(tmp_path, monkeypatch, {})
    with open_database(tmp_path / "state") as db:
        dead_run = db.start_run(clock.now())
        enqueue(db.connection, [unit], enqueued_at=clock.now().isoformat())
        dead = _running(db, unit, queue.Owner(holder, dead_run), clock.now())
        clock.sleep(wait)
        run = db.start_run(clock.now())
        with queue.owning("derive", run):
            fingerprint = db.connection.execute(
                "SELECT input_fingerprint FROM work_attempts WHERE attempt_id=?", (dead,)
            ).fetchone()[0]
            assert eligible(db.connection, unit, fingerprint, clock.now()) is taken
            outcome = derive_one(db, Archive(tmp_path), unit, _bundle(tmp_path), clock, run)
        closed = db.connection.execute(
            "SELECT outcome,reason_code FROM work_attempts WHERE attempt_id=?", (dead,)
        ).fetchone()
        admission_state = db.connection.execute(
            "SELECT state,outcome FROM execution_admissions WHERE action_id='work_dead'"
        ).fetchone()
        if taken:
            # The death counts: the unit waits on its backoff, then runs.
            assert outcome.reason == "process_interrupted" and not outcome.failed
            assert tuple(closed) == ("interrupted", "process_interrupted")
            assert tuple(admission_state) == ("settled", "process_interrupted")
            row = queue.get(db.connection, unit)
            assert row is not None and row.state == "ready" and row.lease_owner is None
            assert (row.attempts, row.error_class) == (1, "process_interrupted")
            assert row.next_attempt_at == queue.at(clock.now() + timedelta(minutes=15))
            # Only the dead attempt's admission row is left: the held one was forgotten.
            assert (
                db.connection.execute("SELECT count(*) FROM execution_admissions").fetchone()[0]
                == 1
            )
            clock.sleep(15 * 60)
            with queue.owning("derive", run):
                again = derive_one(db, Archive(tmp_path), unit, _bundle(tmp_path), clock, run)
            assert again.reason is None and not again.failed
            assert queue.get(db.connection, unit) is None
        else:
            assert outcome.reason == "lease_busy"
            assert tuple(closed) == ("running", None)
            assert tuple(admission_state) == ("active", None)
            row = queue.get(db.connection, unit)
            assert row is not None and row.lease_owner == f"cycle:{dead_run}"
            # Only this attempt's admission row exists: the skip wrote nothing.
            assert (
                db.connection.execute("SELECT count(*) FROM execution_admissions").fetchone()[0]
                == 1
            )


def test_release_all_returns_leases_and_forgets_fresh_ones(tmp_path: Path) -> None:
    owner = queue.Owner("derive", "run_a")
    with open_database(tmp_path / "state") as db:
        _fail(db, FAILING, "unit_exception", exception="ValueError")
        with db.transaction():
            queue.lease(db, FAILING, owner=owner, now=NOW)
            queue.lease(db, FRESH, owner=owner, now=NOW)
            assert queue.counts_by_state(db.connection)["leased"] == 2
            assert queue.release_all(db.connection, NOW) == 2
        assert queue.get(db.connection, FRESH) is None
        row = queue.get(db.connection, FAILING)
        assert row is not None
        assert (row.state, row.lease_owner, row.attempts) == ("ready", None, 1)


def test_requeue_by_class_resets_counts_and_requests_a_retry(tmp_path: Path) -> None:
    from swingset import cli
    from swingset.state.work import enqueue

    state = tmp_path / "state"
    with open_database(state) as db:
        enqueue(db.connection, [FAILING, EXCEPTION, BLOCKED], enqueued_at=AT)
        for _ in range(3):
            _fail(db, FAILING, "unit_exception", exception="ValueError")
            _fail(db, EXCEPTION, "unit_exception", exception="KeyError")
        _fail(db, BLOCKED, "parse_failed", waiting=True)
    assert cli.main(["queue", "requeue", "--state", str(state), "--by", " "]) == 1
    assert (
        cli.main(
            [
                "queue",
                "requeue",
                "--state",
                str(state),
                "--error-class",
                "unit_exception:ValueError",
                "--by",
                "operator test",
            ]
        )
        == 0
    )
    with open_database(state, lock=False, read_only=True) as db:
        conn = db.connection
        requeued = queue.get(conn, FAILING)
        assert requeued is not None and (requeued.state, requeued.attempts) == ("ready", 0)
        assert queue.get(conn, EXCEPTION).state == "quarantined"  # type: ignore[union-attr]
        assert queue.get(conn, BLOCKED).state == "waiting_input"  # type: ignore[union-attr]
        reason = conn.execute(
            "SELECT retry_reason FROM work_generations WHERE unit_id=?", (FAILING.unit_id,)
        ).fetchone()[0]
        assert reason == "operator_requeue by operator test"
    assert cli.main(["queue", "--state", str(state), "--json"]) == 0
