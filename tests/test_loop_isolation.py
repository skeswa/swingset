"""Loop isolation: one loop's death, failure or wait never stops the others (D-0215).

The offline acceptance of the loop architecture (docs/plans/architecture.md,
phase 2). A loop killed inside a write leaves the others running and is
recovered by its own next tick; a unit that keeps failing is quarantined
without holding its neighbours; waiting parse work does not hold acquisition;
an exclusive writer waits for the running loops and holds the next ones.
"""

import fcntl
import json
import signal
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from test_loops import ARCHIVE_BODY, _enqueue, _tick
from test_loops import dirs as dirs  # noqa: F401
from test_loops import history as history  # noqa: F401
from test_watchdog import _run as watchdog

from swingset.clock import FakeClock
from swingset.loops import acquire, derive, maintain, plan, release
from swingset.state import derivations, queue, rwlock
from swingset.state.db import DatabaseLockedError, open_database
from swingset.state.work import WorkUnit

HELPER = Path(__file__).with_name("helpers") / "loop_fault.py"
LOOPS = ("acquire", "plan", "derive", "release", "maintain")
BODIES = {
    "acquire": acquire.run,
    "plan": plan.run,
    "derive": derive.run,
    "release": release.run,
    "maintain": maintain.run,
}


def _handler(seen, archive=True):
    """Serve what `tests/helpers/loop_fault.py` serves: the Wayback capture
    parses, and an origin page is gone. Without `archive` every page is gone."""
    body = ARCHIVE_BODY.read_bytes()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request.url)
        if archive and request.url.host == "web.archive.org":
            return httpx.Response(200, content=body)
        return httpx.Response(404)

    return handler


def _run(loop, dirs, clock, seen=None, *, archive=True):
    """One in-process tick of `loop`; returns its record."""
    kwargs = {}
    if loop == "acquire":
        handler = _handler([] if seen is None else seen, archive)
        kwargs["transport"] = httpx.MockTransport(handler)
    budget = {"plan": 600, "release": 1800}.get(loop, 240)
    with _tick(loop, dirs, clock, budget=budget, **kwargs) as current:
        if loop == "release":
            release.run(current, disk_file=dirs[0].parent / "no-disk-quota")
        else:
            BODIES[loop](current)
    assert current.record is not None
    return current.record


def _due(state, watch_id, clock):
    with open_database(state) as db:
        db.connection.execute(
            "UPDATE watches SET next_check_at=? WHERE watch_id=?",
            (clock.now().isoformat(), watch_id),
        )


def _kill(dirs, clock, loop, fault, chunk=None):
    """Run one tick of `loop` in a subprocess that SIGKILLs itself inside a write."""
    state, config, overrides = dirs
    args = [sys.executable, str(HELPER), str(state), str(config), str(overrides), loop]
    args += [clock.now().isoformat(), fault, *([str(chunk)] if chunk else [])]
    done = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert done.returncode == -signal.SIGKILL, (done.returncode, done.stdout[-2000:])


def _scalar(state, sql, *params):
    with open_database(state, lock=False) as db:
        return db.connection.execute(sql, params).fetchone()[0]


#: Ledger rows of a leased unit: none since schema 37 (D-0238).
UNIT_ADMISSIONS = (
    "SELECT count(*) FROM execution_admissions WHERE substr(action_id,1,5)='work_' "
    "OR action_kind IN ('parse','link') OR work_attempt_id IS NOT NULL"
)


@pytest.fixture
def started(dirs, history):
    """Plan and acquire once: an eligible Wayback row is fetched, and its
    capture leaves a parse unit for derive."""
    record = _run("plan", dirs, history.clock)
    assert record["progress"] > 0
    seen = []
    record = _run("acquire", dirs, history.clock, seen)
    assert {url.host for url in seen} == {"web.archive.org", "eepro.com"}
    assert _scalar(dirs[0], "SELECT count(*) FROM pending_work WHERE stage='parse'") == 1
    history.clock.sleep(60)
    return history


# (loop, fault, CHUNK_ROWS): the fault names the write the subprocess dies in.
KILLS = {
    # A fetched response is being stored.
    "acquire": ("acquire", "_fetch:1", None),
    # A request is on the wire: its admission committed, no response came
    # (receipt finding 3: the admission carries the tick's run).
    "acquire-http": ("acquire", "http", None),
    # The plan table is being written.
    "plan": ("plan", "write:1", None),
    # A single-path unit's output is being committed.
    "derive-single": ("derive", "process:1", None),
    # A staged history generation stored chunk 1; chunk 2 is being written.
    "derive-chunks": ("derive", "_store_building:2", 1),
    # The release manifest is being recorded under the heavy lock (D-0224).
    "release": ("release", "store:1", None),
    # Guard rows are being pruned.
    "maintain": ("maintain", "run:1", None),
}


@pytest.mark.parametrize("case", list(KILLS))
def test_a_loop_killed_mid_write_stops_no_other_and_recovers_itself(
    case, dirs, started, monkeypatch
):
    state = dirs[0]
    clock = started.clock
    loop, fault, chunk = KILLS[case]
    if loop == "acquire":
        _due(state, started.origin, clock)
    if chunk:
        # The capture changes no output, and since D-0224 a unit with
        # identical output ends the cascade, so nothing is staged. Without
        # cutoff (D-0205) every unit re-mints and the year stores in chunks.
        sources = dirs[1] / "sources.toml"
        sources.write_text(sources.read_text() + "\n[derivation]\ncutoff = false\n")
    _kill(dirs, clock, loop, fault, chunk)
    dead = _scalar(state, "SELECT run_id FROM runs WHERE loop=? AND finished_at IS NULL", loop)
    assert dead is not None
    # A unit killed mid-write left its lease and attempt, no ledger row (D-0238).
    assert _scalar(state, UNIT_ADMISSIONS) == 0
    building = None
    if chunk:
        with open_database(state, lock=False) as db:
            rows = db.connection.execute(
                "SELECT generation_id,label_rows,rows_stored FROM derivation_building"
            ).fetchall()
        # Killed between chunks: part of the generation is stored.
        (building,) = [row for row in rows if 0 < row[2] < row[1]]
        monkeypatch.setattr(derivations, "CHUNK_ROWS", chunk)
    clock.sleep(60)
    # Every other loop's next tick makes progress beside the dead run.
    for other in LOOPS:
        if other == loop:
            continue
        if other == "acquire":
            _due(state, started.origin, clock)
        # A second capture would change history's inputs and supersede the
        # generation derive was building; acquire's requests find nothing.
        record = _run(other, dirs, clock, archive=False)
        assert record["error"] is None and record["skipped"] is None, record
        assert not record["failed"] and record["progress"] > 0, (other, record)
        clock.sleep(60)
    # The killed loop's own next tick closes its dead run and finishes.
    rerun = _run(loop, dirs, clock)
    assert rerun["error"] is None and rerun["skipped"] is None, rerun
    assert rerun["recovered"]["runs"] == 1
    if loop == "derive":
        # The death is the unit's counted failure: it took the lease back.
        assert rerun["recovered"]["attempts"] == 1 and rerun["recovered"]["leases"] == 0
        # The interrupted unit retries after its 15 minute backoff.
        clock.sleep(16 * 60)
        again = _run(loop, dirs, clock)
        assert again["error"] is None and not again["failed"], again
    with open_database(state, lock=False) as db:
        conn = db.connection
        finished, summary = conn.execute(
            "SELECT finished_at,summary_json FROM runs WHERE run_id=?", (dead,)
        ).fetchone()
        assert finished is not None
        closed = json.loads(summary)
        assert closed["failed"] is True and closed["error"] == "process_interrupted"
        assert json.loads((state / "runs" / f"{dead}.json").read_bytes())["failed"] is True
        # The fixture's own runs carry no loop; every loop run is closed.
        open_runs = "SELECT count(*) FROM runs WHERE loop IS NOT NULL AND finished_at IS NULL"
        assert conn.execute(open_runs).fetchone()[0] == 0
        assert (
            conn.execute("SELECT count(*) FROM work_attempts WHERE outcome='running'").fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) FROM work_queue WHERE state='leased'").fetchone()[0] == 0
        )
        # No admission is left active, with a run or without one, and no
        # admission row exists for a unit.
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_admissions WHERE state='active'"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute(UNIT_ADMISSIONS).fetchone()[0] == 0
        if building is not None:
            # The rerun resumed the building generation and finished it.
            generation_id, label_rows, _ = building
            assert conn.execute("SELECT count(*) FROM derivation_building").fetchone()[0] == 0
            assert (
                conn.execute(
                    "SELECT count(*) FROM derivation_row_refs WHERE generation_id=?",
                    (generation_id,),
                ).fetchone()[0]
                == label_rows
            )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_a_failing_unit_is_quarantined_and_the_others_drain(dirs, monkeypatch):
    import swingset.project

    state = dirs[0]
    clock = FakeClock()
    ran = []

    def project(database, unit, _bundle, clock, run_id, selection=None):
        if unit.unit_id == "bad":
            raise RuntimeError("projector bug")
        ran.append(unit.unit_id)
        derivations.complete(
            database.connection, selection, rows=(), now=clock.now(), run_id=run_id
        )
        database.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

    monkeypatch.setattr(swingset.project, "process_unit", project)
    bad = WorkUnit("project", "dancer", "bad")
    _enqueue(state, clock, ["bad", "a", "b"])
    records = []
    # Backoff: 15 min after the first failure, 30 min after the second.
    for number, (name, wait) in enumerate((("c", 16), ("d", 31), (None, 0))):
        records.append(_run("derive", dirs, clock))
        with open_database(state) as db:
            row = queue.get(db.connection, bad)
        assert row is not None and row.attempts == number + 1
        assert row.state == ("quarantined" if number == 2 else "ready")
        if name:
            _enqueue(state, clock, [name])
            clock.sleep(wait * 60)
    assert records[2]["queue"]["quarantined"]
    assert sorted(ran) == ["a", "b", "c", "d"]
    assert _scalar(state, "SELECT group_concat(unit_id) FROM pending_work") == "bad"
    lines = [
        line
        for line in watchdog(state, now=clock.now().isoformat())
        if line.startswith("[unit-quarantined]")
    ]
    assert len(lines) == 1 and "project/dancer/bad after 3 x" in lines[0]


def test_waiting_and_quarantined_units_do_not_hold_acquisition(dirs, history):
    from swingset.config import load_config
    from swingset.schedule.fairness import backpressure

    state, config, _ = dirs
    clock = history.clock
    assert _run("plan", dirs, clock)["progress"] > 0
    limit = load_config(config).scheduler.pending_parse_items
    stamp = queue.at(clock.now())
    later = queue.at(clock.now() + timedelta(days=1))
    units = [WorkUnit("parse", "snapshot", f"snap_waiting_{n:04}") for n in range(1500)]
    assert len(units) > limit
    with open_database(state) as db, db.transaction() as conn:
        conn.executemany(
            "INSERT INTO pending_work VALUES (?,?,?,?)",
            [(u.stage, u.unit_kind, u.unit_id, stamp) for u in units],
        )
        rows = [(u, "waiting_input", 1, None) for u in units]
        rows.append((WorkUnit("project", "dancer", "bad"), "quarantined", 3, stamp))
        for unit, state_name, attempts, quarantined_at in rows:
            queue.upsert(
                conn,
                queue.QueueRow(
                    stage=unit.stage,
                    unit_kind=unit.unit_kind,
                    unit_id=unit.unit_id,
                    partition=queue.partition_of(unit),
                    state=state_name,
                    attempts=attempts,
                    error_class="blocked" if quarantined_at is None else "unit_exception:X",
                    input_fingerprint="fp",
                    lease_owner=None,
                    lease_until=None,
                    next_attempt_at=later,
                    last_reason=None,
                    last_error=None,
                    enqueued_at=stamp,
                    updated_at=stamp,
                    quarantined_at=quarantined_at,
                ),
            )
        pressure = backpressure(conn, load_config(config), now=clock.now())
    assert pressure["pending"]["parse"] == 1500 and pressure["runnable_parse"] == 0
    assert not pressure["active"]
    seen = []
    record = _run("acquire", dirs, clock, seen)
    assert not record["failed"] and record["skipped"] is None, record
    assert record["history_dispatch"]["reason"] == "dispatched"
    assert any(url.host == "web.archive.org" and "example2019" in url.path for url in seen)
    assert any(url.path == "/results/review-control/" for url in seen if url.host == "eepro.com")


def _intent_held(state) -> bool:
    """Whether an exclusive open holds the intent (it waits for the lock)."""
    with (state / rwlock.STATE_INTENT).open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def test_an_exclusive_writer_waits_for_derive_and_holds_the_next_acquire(dirs, started):
    from swingset.cli import main

    state, config, overrides = dirs
    clock = started.clock
    opened = {}

    def backup():
        # A backup's open: exclusive, waiting up to its lock timeout.
        with open_database(state, lock_timeout=20) as db:
            opened["at"] = time.monotonic()
            db.connection.execute("SELECT count(*) FROM runs").fetchone()

    with _tick("derive", dirs, clock) as running:
        writer = threading.Thread(target=backup)
        writer.start()
        deadline = time.monotonic() + 10
        while not _intent_held(state):
            assert time.monotonic() < deadline, "the exclusive open never arrived"
            time.sleep(0.01)
        # The next acquire tick is refused before it opens anything.
        with pytest.raises(DatabaseLockedError) as refused:
            with _tick("acquire", dirs, clock):
                pass
        assert refused.value.reason == "writer pending"
        common = ["--state", str(state), "--config", str(config), "--overrides", str(overrides)]
        assert main(["acquire", *common, "--timer"]) == 0  # skipped-overlap
        assert main(["acquire", *common]) == 1
        # The running derive tick is not disturbed; the writer waits for it.
        derive.run(running)
        assert "at" not in opened
        released = time.monotonic()
    writer.join(20)
    assert not writer.is_alive() and opened["at"] >= released
    assert running.record is not None and running.record["progress"] > 0
    assert _scalar(state, "SELECT count(*) FROM runs WHERE loop='acquire'") == 1
    # Then every loop resumes.
    clock.sleep(60)
    _due(state, started.origin, clock)
    resumed = _run("acquire", dirs, clock)
    assert resumed["error"] is None and resumed["progress"] > 0
    assert _run("derive", dirs, clock)["error"] is None


def _projector(monkeypatch, ran):
    """Complete every project unit with no rows, recording what ran."""
    import swingset.project

    def project(database, unit, _bundle, clock, run_id, selection=None):
        ran.append(unit.unit_id)
        derivations.complete(
            database.connection, selection, rows=(), now=clock.now(), run_id=run_id
        )
        database.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

    monkeypatch.setattr(swingset.project, "process_unit", project)


def test_a_unit_whose_admission_raises_fails_alone_and_is_quarantined(dirs, monkeypatch):
    """A defect in capture (or the lease, or the attempt) is the unit's failure:
    its neighbours run in the same tick, it backs off, and it quarantines (I1, I3)."""
    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []
    _projector(monkeypatch, ran)
    real = derivations.capture

    def capture(conn, unit, **kwargs):
        if unit.unit_id == "aaa-poison":
            raise RuntimeError("capture defect for one unit")
        return real(conn, unit, **kwargs)

    monkeypatch.setattr(derivations, "capture", capture)
    poison = WorkUnit("project", "dancer", "aaa-poison")
    _enqueue(state, clock, ["aaa-poison", "b1", "b2"])
    first = _run("derive", dirs, clock)
    assert first["error"] is None, first
    assert sorted(ran) == ["b1", "b2"]
    with open_database(state) as db:
        row = queue.get(db.connection, poison)
        assert row is not None and row.state == "ready"
        assert (row.attempts, row.error_class) == (1, "unit_exception:RuntimeError")
        # No attempt exists: the queue row alone carries the clock.
        assert (
            db.connection.execute(
                "SELECT count(*) FROM work_attempts WHERE unit_id='aaa-poison'"
            ).fetchone()[0]
            == 0
        )
    # Not due yet: selection leaves it alone and the tick is clean.
    clock.sleep(60)
    _enqueue(state, clock, ["b3"])
    quiet = _run("derive", dirs, clock)
    assert not quiet["failed"] and quiet["unit_failures"] == [], quiet
    assert "b3" in ran
    for number, wait in ((2, 16), (3, 31)):
        clock.sleep(wait * 60)
        record = _run("derive", dirs, clock)
        assert record["error"] is None
        with open_database(state) as db:
            row = queue.get(db.connection, poison)
        assert row is not None and row.attempts == number
    assert row.state == "quarantined"
    assert record["queue"]["quarantined"] == ["project/dancer/aaa-poison"]


def test_a_unit_that_kills_its_process_is_quarantined_and_the_others_drain(dirs, monkeypatch):
    """An OOM or a stop timeout inside one unit counts as its failure (D-0216)."""
    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []
    _projector(monkeypatch, ran)
    poison = WorkUnit("project", "dancer", "aaa-poison")
    _enqueue(state, clock, ["aaa-poison", "b1", "b2"])
    # Each round: the poison unit is first and kills the tick; the next tick
    # counts the death, holds the unit on its backoff and drains the others.
    for number, (neighbour, wait) in enumerate((("b3", 16), ("b4", 31), (None, 0)), 1):
        _kill(dirs, clock, "derive", "unit:aaa-poison")
        clock.sleep(5)
        record = _run("derive", dirs, clock)
        assert record["error"] is None and record["recovered"]["attempts"] == 1, record
        with open_database(state) as db:
            row = queue.get(db.connection, poison)
        assert row is not None and row.attempts == number
        assert row.error_class == "process_interrupted"
        assert row.state == ("quarantined" if number == 3 else "ready")
        if neighbour:
            _enqueue(state, clock, [neighbour])
            clock.sleep(wait * 60)
    assert sorted(ran) == ["b1", "b2", "b3", "b4"]
    assert _scalar(state, "SELECT group_concat(unit_id) FROM pending_work") == "aaa-poison"
    # Quarantined: the next tick does not pick it, so nothing dies.
    clock.sleep(3600)
    after = _run("derive", dirs, clock)
    assert after["recovered"]["attempts"] == 0 and not after["failed"]


def test_a_unit_that_kills_its_process_in_its_admission_is_quarantined(dirs, monkeypatch):
    """A death between the unit's claim and its attempt (here in its capture)
    leaves only the claimed lease; the next tick counts it as the unit's
    death, so the unit backs off and quarantines like a body death (I1, I3)."""
    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []
    _projector(monkeypatch, ran)
    poison = WorkUnit("project", "dancer", "aaa-poison")
    _enqueue(state, clock, ["aaa-poison", "b1", "b2"])
    for number, (neighbour, wait) in enumerate((("b3", 16), ("b4", 31), (None, 0)), 1):
        _kill(dirs, clock, "derive", "capture:aaa-poison")
        with open_database(state, lock=False) as db:
            # No attempt began: the claimed lease is all the dead run left.
            row = queue.get(db.connection, poison)
            assert row is not None and row.state == "leased"
            assert (
                db.connection.execute(
                    "SELECT count(*) FROM work_attempts WHERE unit_id='aaa-poison'"
                ).fetchone()[0]
                == 0
            )
        clock.sleep(5)
        record = _run("derive", dirs, clock)
        assert record["error"] is None and record["recovered"]["admissions"] == 1, record
        with open_database(state) as db:
            row = queue.get(db.connection, poison)
        assert row is not None and row.attempts == number
        assert row.error_class == "process_interrupted"
        assert row.state == ("quarantined" if number == 3 else "ready")
        if neighbour:
            _enqueue(state, clock, [neighbour])
            clock.sleep(wait * 60)
    assert sorted(ran) == ["b1", "b2", "b3", "b4"]
    clock.sleep(3600)
    after = _run("derive", dirs, clock)
    assert after["recovered"].get("admissions", 0) == 0 and not after["failed"]


def test_an_exception_that_escapes_derive_one_counts_and_quarantines(dirs, monkeypatch):
    """A handler's own defect escapes `derive_one` and leaves its attempt
    running. The offline loop closes it as the unit's failure, so the unit
    backs off and quarantines instead of retrying uncounted every minute."""
    import swingset.project
    import swingset.schedule.derive as derive_module

    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []
    _projector(monkeypatch, ran)
    real_project = swingset.project.process_unit
    real_failed = derive_module._failed

    def project(database, unit, *args, **kwargs):
        if unit.unit_id == "aaa-poison":
            raise RuntimeError("poison")
        return real_project(database, unit, *args, **kwargs)

    def failed(database, attempt, *args, **kwargs):
        if attempt.unit.unit_id == "aaa-poison":
            raise KeyError("handler defect")
        return real_failed(database, attempt, *args, **kwargs)

    monkeypatch.setattr(swingset.project, "process_unit", project)
    monkeypatch.setattr(derive_module, "_failed", failed)
    poison = WorkUnit("project", "dancer", "aaa-poison")
    _enqueue(state, clock, ["aaa-poison", "b1"])
    for number, wait in ((1, 16), (2, 31), (3, 0)):
        record = _run("derive", dirs, clock)
        assert record["error"] is None and record["failed"], record
        with open_database(state) as db:
            conn = db.connection
            row = queue.get(conn, poison)
            assert (
                conn.execute(
                    "SELECT count(*) FROM work_attempts WHERE unit_id='aaa-poison' "
                    "AND outcome='running'"
                ).fetchone()[0]
                == 0
            )
            # No admission row exists for a unit (D-0238).
            assert conn.execute(UNIT_ADMISSIONS).fetchone()[0] == 0
        assert row is not None and row.attempts == number
        assert row.error_class == "unit_exception:KeyError"
        assert row.state == ("quarantined" if number == 3 else "ready")
        clock.sleep(wait * 60)
    assert record["queue"]["quarantined"] == ["project/dancer/aaa-poison"]
    assert ran == ["b1"]


def test_derive_progress_counts_only_committed_output(dirs, monkeypatch):
    """A spurious supersession fails on the queue's clock but is no progress:
    a tick of nothing else reports none, so [loop-idle] can fire."""
    import swingset.project
    from swingset.state.attempts import SupersededWorkError

    state = dirs[0]
    clock = FakeClock()

    def project(database, unit, *args, **kwargs):
        raise SupersededWorkError("a newer attempt owns this work")

    monkeypatch.setattr(swingset.project, "process_unit", project)
    _enqueue(state, clock, ["a"])
    record = _run("derive", dirs, clock)
    assert record["error"] is None and record["progress"] == 0, record
    assert record["not_completed"] == {"inputs_changed_spurious": 1}


def test_a_unit_killed_after_a_stop_request_is_not_counted(dirs, monkeypatch):
    """A deploy's stop that outlasts TimeoutStopSec kills the unit it waited
    for; the stop marker says so, and recovery counts nothing."""
    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []
    _projector(monkeypatch, ran)
    poison = WorkUnit("project", "dancer", "aaa-poison")
    _enqueue(state, clock, ["aaa-poison"])
    _kill(dirs, clock, "derive", "unit:aaa-poison")
    with open_database(state, lock=False) as db:
        (dead,) = db.connection.execute(
            "SELECT run_id FROM runs WHERE loop='derive' AND finished_at IS NULL"
        ).fetchone()
    marker = queue.stop_marker(state, dead)
    marker.parent.mkdir(exist_ok=True)
    marker.write_bytes(b"")
    clock.sleep(5)
    record = _run("derive", dirs, clock)
    assert record["error"] is None and record["recovered"]["attempts"] == 1, record
    with open_database(state) as db:
        row = queue.get(db.connection, poison)
    assert row is not None and row.attempts == 0 and row.error_class is None


def test_a_recovery_record_whose_transaction_rolled_back_is_no_ending(dirs, monkeypatch):
    """A recovery writes the dead run's record, `process_interrupted`, inside
    its transaction. If that rolls back, the next tick must still count the
    death, not read the record as a run that ended on its own."""
    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []
    _projector(monkeypatch, ran)
    poison = WorkUnit("project", "dancer", "aaa-poison")
    _enqueue(state, clock, ["aaa-poison"])
    _kill(dirs, clock, "derive", "unit:aaa-poison")
    with open_database(state, lock=False) as db:
        (dead,) = db.connection.execute(
            "SELECT run_id FROM runs WHERE loop='derive' AND finished_at IS NULL"
        ).fetchone()
    (state / "runs").mkdir(exist_ok=True)
    (state / "runs" / f"{dead}.json").write_text(
        json.dumps(
            {
                "run_id": dead,
                "loop": "derive",
                "failed": True,
                "error": "process_interrupted",
                "finished_at": clock.now().isoformat(),
            }
        )
    )
    with open_database(state, lock=False) as db:
        assert queue.death_counts(db, dead)
    clock.sleep(5)
    record = _run("derive", dirs, clock)
    assert record["error"] is None and record["recovered"]["attempts"] == 1, record
    assert record["recovered"]["completed_from_record"] == 0
    with open_database(state) as db:
        row = queue.get(db.connection, poison)
    assert row is not None and row.attempts == 1 and row.error_class == "process_interrupted"


def test_a_unit_write_that_outwaits_the_busy_timeout_is_never_counted(dirs, monkeypatch):
    """Another process holds the writer lock across the unit's processing
    transaction: the unit returns `write_lock_timeout`, writes nothing, the
    tick does not fail, and the next tick closes the attempt uncounted (F7)."""
    import contextlib
    import sqlite3

    import swingset.schedule.derive as derive_module
    from swingset.state.db import Database

    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []
    _projector(monkeypatch, ran)
    _enqueue(state, clock, ["a"])
    unit = WorkUnit("project", "dancer", "a")
    armed: list[bool] = []
    outcomes: list[object] = []
    real_begin = derive_module.begin_attempt
    real_derive_one = derive_module.derive_one
    real_transaction = Database.transaction

    def begin_attempt(database, begun, **kwargs):
        if begun == unit:
            armed.append(True)
        return real_begin(database, begun, **kwargs)

    @contextlib.contextmanager
    def transaction(self, **kwargs):
        other = None
        if armed and not self.connection.in_transaction:
            # Another process takes the writer lock before the unit's write,
            # and this connection waits only a tenth of a second for it.
            armed.clear()
            other = sqlite3.connect(self.state_dir / "state.sqlite", isolation_level=None)
            other.execute("BEGIN IMMEDIATE")
            self.connection.execute("PRAGMA busy_timeout=100")
        try:
            with real_transaction(self, **kwargs) as conn:
                yield conn
        finally:
            if other is not None:
                other.execute("ROLLBACK")
                other.close()

    def derive_one(*args, **kwargs):
        outcome = real_derive_one(*args, **kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(derive_module, "begin_attempt", begin_attempt)
    monkeypatch.setattr(Database, "transaction", transaction)
    import swingset.schedule.offline as offline_module

    monkeypatch.setattr(offline_module, "derive_one", derive_one)
    record = _run("derive", dirs, clock)
    assert record["error"] is None and not record["failed"], record
    assert [getattr(o, "reason", None) for o in outcomes] == [queue.LOCK_REASON]
    assert record["queue"]["held"] == {queue.LOCK_REASON: 1}
    assert ran == []
    with open_database(state, lock=False) as db:
        row = queue.get(db.connection, unit)
        assert row is not None and row.state == "leased" and row.attempts == 0
    monkeypatch.setattr(Database, "transaction", real_transaction)
    clock.sleep(5)
    _run("derive", dirs, clock)
    with open_database(state, lock=False) as db:
        row = queue.get(db.connection, unit)
        (outcome,) = db.connection.execute(
            "SELECT outcome FROM work_attempts WHERE unit_id='a' ORDER BY attempt_id"
        ).fetchone()
        # Closed uncounted, retried in a minute.
        assert outcome == "interrupted"
        assert row is not None and row.attempts == 0 and row.error_class is None
        assert row.next_attempt_at == queue.at(clock.now() + timedelta(seconds=60))
    clock.sleep(61)
    assert not _run("derive", dirs, clock)["failed"]
    assert ran == ["a"] and _scalar(state, "SELECT count(*) FROM pending_work") == 0


def test_a_publication_that_fences_a_unit_mid_write_is_never_counted(dirs, monkeypatch):
    """A publication admitted between a unit's admission and its write fences
    the write: derive writes nothing more, its tick does not fail, and the
    next tick after the publication closes the attempt uncounted (F6)."""
    import contextlib
    import sqlite3

    from swingset.state.db import Database

    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []
    _projector(monkeypatch, ran)
    _enqueue(state, clock, ["a"])
    unit = WorkUnit("project", "dancer", "a")
    import swingset.schedule.derive as derive_module

    armed: list[bool] = []
    real_begin = derive_module.begin_attempt
    real_transaction = Database.transaction

    def begin_attempt(database, begun, **kwargs):
        # Armed inside the admission: the next top-level write is the unit's.
        if begun == unit:
            armed.append(True)
        return real_begin(database, begun, **kwargs)

    @contextlib.contextmanager
    def transaction(self, **kwargs):
        if armed and not self.connection.in_transaction:
            # Another process admits a publication before the unit's write.
            armed.clear()
            other = sqlite3.connect(self.state_dir / "state.sqlite")
            other.execute(
                "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,"
                "control_revision,all_sources,all_kinds,state) "
                "VALUES ('publication_x','publication',?,0,1,1,'active')",
                (clock.now().isoformat(),),
            )
            other.commit()
            other.close()
        with real_transaction(self, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(derive_module, "begin_attempt", begin_attempt)
    monkeypatch.setattr(Database, "transaction", transaction)
    record = _run("derive", dirs, clock)
    assert record["error"] is None and not record["failed"], record
    assert record["queue"]["held"] == {"publication_fenced": 1}
    assert ran == ["a"]
    monkeypatch.setattr(Database, "transaction", real_transaction)
    with open_database(state, lock=False) as db:
        conn = db.connection
        row = queue.get(conn, unit)
        assert row is not None and row.state == "leased" and row.attempts == 0
        # The publication ends (release settles it).
        conn.execute(
            "UPDATE execution_admissions SET state='settled',settled_at=?,outcome='published' "
            "WHERE action_id='publication_x'",
            (clock.now().isoformat(),),
        )
        conn.commit()
    clock.sleep(5)
    after = _run("derive", dirs, clock)
    assert after["recovered"]["attempts"] == 1 and after["recovered"]["completed_from_record"] == 1
    with open_database(state, lock=False) as db:
        row = queue.get(db.connection, unit)
        # Closed uncounted, retried in a minute.
        assert row is not None and row.attempts == 0 and row.error_class is None
        assert row.next_attempt_at == queue.at(clock.now() + timedelta(seconds=60))
    clock.sleep(61)
    assert not _run("derive", dirs, clock)["failed"]
    assert _scalar(state, "SELECT count(*) FROM pending_work") == 0


def test_a_unit_write_that_meets_the_narrow_fence_is_closed_uncounted_in_its_tick(
    dirs, monkeypatch
):
    """Under a partitioned publication (WP10) a unit that writes what the
    publication's check reads is `publication_fenced`; the queue is
    writable, so its tick closes it uncounted and the other units run."""
    import swingset.project

    state = dirs[0]
    clock = FakeClock()
    ran: list[str] = []

    def project(database, unit, _bundle, clock, run_id, selection=None):
        ran.append(unit.unit_id)
        if unit.unit_id == "fenced":
            database.connection.execute(
                "INSERT INTO admission_policies(page_kind,contract_version,policy_revision) "
                "VALUES ('fixture','1','shadow:1')"
            )
        derivations.complete(
            database.connection, selection, rows=(), now=clock.now(), run_id=run_id
        )
        database.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

    monkeypatch.setattr(swingset.project, "process_unit", project)
    _enqueue(state, clock, ["fenced", "free"])
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,"
            "control_revision,all_sources,all_kinds,state) "
            "VALUES ('publication_n','publication_narrow',?,0,1,1,'active')",
            (clock.now().isoformat(),),
        )
    record = _run("derive", dirs, clock)
    assert record["error"] is None and not record["failed"], record
    assert record["queue"]["held"] == {"publication_fenced": 1}
    assert sorted(ran) == ["fenced", "free"] and record["progress"] == 1
    unit = WorkUnit("project", "dancer", "fenced")
    with open_database(state, lock=False) as db:
        conn = db.connection
        # Nothing is left for the next tick's recovery.
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_admissions WHERE state='active'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT count(*) FROM work_attempts WHERE outcome='running'").fetchone()[0]
            == 0
        )
        row = queue.get(conn, unit)
        assert row is not None and row.attempts == 0 and row.error_class is None
        assert row.state != "leased"
        assert row.next_attempt_at == queue.at(clock.now() + timedelta(seconds=60))
        conn.execute(
            "UPDATE execution_admissions SET state='settled',settled_at=?,outcome='published' "
            "WHERE action_id='publication_n'",
            (clock.now().isoformat(),),
        )
        conn.commit()
    clock.sleep(61)
    after = _run("derive", dirs, clock)
    assert not after["failed"] and after["recovered"]["attempts"] == 0
    assert _scalar(state, "SELECT count(*) FROM pending_work") == 0
