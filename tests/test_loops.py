"""Loop runtime, derive and maintain ticks (D-0215)."""

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from swingset.cli import main
from swingset.clock import FakeClock
from swingset.config import load_config
from swingset.loops import derive, maintain
from swingset.loops.runtime import PUBLICATION_IN_FLIGHT, tick
from swingset.state import guards, queue, rwlock
from swingset.state.db import DatabaseLockedError, open_database
from swingset.state.work import WorkUnit


@pytest.fixture
def dirs(tmp_path):
    config = tmp_path / "config"
    shutil.copytree("config", config)
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    return tmp_path / "state", config, overrides


@pytest.fixture
def projected(monkeypatch):
    """A projector that completes its unit with no rows, and the units it ran."""
    import swingset.project
    from swingset.state import derivations

    ran = []

    def project(database, unit, _bundle, clock, run_id, selection=None):
        ran.append(unit.unit_id)
        derivations.complete(
            database.connection, selection, rows=(), now=clock.now(), run_id=run_id
        )
        database.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

    monkeypatch.setattr(swingset.project, "process_unit", project)
    return ran


def _enqueue(state, clock, names):
    from swingset.state.work import enqueue

    with open_database(state) as db:
        enqueue(
            db.connection,
            [WorkUnit("project", "dancer", name) for name in names],
            enqueued_at=clock.now().isoformat(),
        )


def _tick(name, dirs, clock, **kwargs):
    state, config, overrides = dirs
    return tick(
        name,
        state=state,
        config_dir=config,
        overrides_dir=overrides,
        clock=clock,
        budget=kwargs.pop("budget", 60),
        **kwargs,
    )


def _derive(dirs, clock):
    with _tick("derive", dirs, clock) as current:
        derive.run(current)
    assert current.record is not None
    return current.record


def _pending(state, stage):
    with open_database(state) as db:
        return db.connection.execute(
            "SELECT count(*) FROM pending_work WHERE stage=?", (stage,)
        ).fetchone()[0]


def _admit(conn, action_id, kind, run_id, clock):
    conn.execute(
        "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,"
        "run_id,all_sources,all_kinds,state) VALUES (?,?,?,0,?,1,1,'active')",
        (action_id, kind, clock.now().isoformat(), run_id),
    )


def test_a_derive_tick_completes_units_and_writes_its_run_record(dirs, projected):
    state = dirs[0]
    clock = FakeClock()
    _enqueue(state, clock, ["a", "b", "c"])
    record = _derive(dirs, clock)
    assert set(projected) == {"a", "b", "c"}
    assert not record["failed"] and record["skipped"] is None
    assert record["loop"] == "derive"
    assert record["counters"]["completed"] >= 3
    assert record["progress"] == record["counters"]["completed"]
    assert "project" in record["stages"]
    assert record["accepted_inputs"]
    for key in ("derivation", "queue_states", "held_units", "parse_errors", "readiness_errors"):
        assert key in record
    assert _pending(state, "project") == 0
    saved = json.loads((state / "runs" / f"{record['run_id']}.json").read_bytes())
    assert saved == record
    with open_database(state) as db:
        row = db.connection.execute(
            "SELECT loop,finished_at FROM runs WHERE run_id=?", (record["run_id"],)
        ).fetchone()
        assert tuple(row) == ("derive", record["finished_at"])
        progress = db.connection.execute(
            "SELECT run_id,failed,last_progress_at FROM loop_state WHERE loop='derive'"
        ).fetchone()
        assert tuple(progress) == (record["run_id"], 0, record["finished_at"])
    # A quiet tick keeps the time of the last progress.
    clock.sleep(60)
    quiet = _derive(dirs, clock)
    assert quiet["progress"] == 0
    with open_database(state) as db:
        assert (
            db.connection.execute(
                "SELECT last_progress_at FROM loop_state WHERE loop='derive'"
            ).fetchone()[0]
            == (record["finished_at"])
        )


def test_per_loop_recovery_closes_only_the_loops_own_dead_runs(dirs):
    from swingset.state.attempts import begin_attempt, latest_attempt

    state = dirs[0]
    clock = FakeClock()
    _enqueue(state, clock, ["a"])
    with open_database(state) as db:
        conn = db.connection
        for run_id, loop in (("run_dead", "derive"), ("run_live", "acquire")):
            conn.execute(
                "INSERT INTO runs(run_id,started_at,dry_run,loop) VALUES (?,?,1,?)",
                (run_id, clock.now().isoformat(), loop),
            )
        begin_attempt(db, WorkUnit("project", "dancer", "a"), now=clock.now(), run_id="run_dead")
        _admit(conn, "dead_action", "project", "run_dead", clock)
        _admit(conn, "live_action", "fetch", "run_live", clock)
        stamp = queue.at(clock.now())
        for unit_id, owner in (("a", "derive:run_dead"), ("b", "acquire:run_live")):
            queue.upsert(
                conn,
                queue.QueueRow(
                    stage="project",
                    unit_kind="dancer",
                    unit_id=unit_id,
                    partition="x",
                    state="leased",
                    attempts=0,
                    error_class=None,
                    input_fingerprint=None,
                    lease_owner=owner,
                    lease_until=queue.at(clock.now() + timedelta(hours=1)),
                    next_attempt_at=None,
                    last_reason=None,
                    last_error=None,
                    enqueued_at=stamp,
                    updated_at=stamp,
                    quarantined_at=None,
                ),
            )
    clock.sleep(10)
    with _tick("derive", dirs, clock) as current:
        pass
    assert current.summary["recovered"] == {
        "admissions": 1,
        "runs": 1,
        "completed_from_record": 0,
        "attempts": 1,
        # The attempt's failure took unit a's lease; b is acquire's.
        "leases": 0,
    }
    with open_database(state, lock=False) as db:
        conn = db.connection
        dead = conn.execute("SELECT finished_at,summary_json FROM runs WHERE run_id='run_dead'")
        finished, summary = dead.fetchone()
        assert finished is not None
        assert json.loads(summary)["error"] == "process_interrupted"
        assert json.loads((state / "runs" / "run_dead.json").read_bytes())["failed"] is True
        assert (
            conn.execute("SELECT finished_at FROM runs WHERE run_id='run_live'").fetchone()[0]
            is None
        )
        states = dict(conn.execute("SELECT action_id,state FROM execution_admissions"))
        assert states == {"dead_action": "settled", "live_action": "active"}
        # The death is a counted failure: the unit backs off 15 minutes.
        failed = queue.get(conn, WorkUnit("project", "dancer", "a"))
        assert failed is not None and failed.state == "ready" and failed.lease_owner is None
        assert (failed.attempts, failed.error_class) == (1, "process_interrupted")
        backoff = queue.at(clock.now() + timedelta(minutes=15))
        assert failed.next_attempt_at == backoff
        attempt = latest_attempt(conn, WorkUnit("project", "dancer", "a"))
        assert attempt is not None
        assert (attempt["outcome"], attempt["reason_code"]) == (
            "interrupted",
            "process_interrupted",
        )
        assert queue.at(datetime.fromisoformat(attempt["retry_at"])) == backoff
        live = queue.get(conn, WorkUnit("project", "dancer", "b"))
        assert live is not None and live.lease_owner == "acquire:run_live"


def test_a_dead_run_whose_record_finished_keeps_that_record(dirs):
    """A tick whose last row write met a fence left a finished record file."""
    state = dirs[0]
    clock = FakeClock()
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run,loop) VALUES ('run_x',?,1,'maintain')",
            (clock.now().isoformat(),),
        )
    record = {"run_id": "run_x", "loop": "maintain", "failed": False, "finished_at": "2026-01-01"}
    (state / "runs").mkdir(exist_ok=True)
    (state / "runs" / "run_x.json").write_text(json.dumps(record))
    with _tick("maintain", dirs, clock) as current:
        pass
    assert current.summary["recovered"]["completed_from_record"] == 1
    with open_database(state, lock=False) as db:
        row = db.connection.execute(
            "SELECT finished_at,summary_json FROM runs WHERE run_id='run_x'"
        )
        finished, summary = row.fetchone()
        assert finished == "2026-01-01" and json.loads(summary) == record


def test_a_derive_tick_starts_no_unit_while_release_holds_the_heavy_lock(dirs, projected):
    state = dirs[0]
    clock = FakeClock()
    _enqueue(state, clock, ["a", "b"])
    heavy = rwlock.heavy_exclusive(state, timeout=0)
    try:
        record = _derive(dirs, clock)
    finally:
        heavy.release()
    # A build and a derive tick together outgrow the worker (F10).
    assert record["counters"]["heavy_deferred"] == 1 and record["heavy_deferred"] is True
    assert record["stages"] == [] and record["progress"] == 0
    assert not record["failed"]
    assert projected == [] and _pending(state, "project") == 2
    clock.sleep(60)
    after = _derive(dirs, clock)
    assert "heavy_deferred" not in after["counters"]
    assert sorted(projected) == ["a", "b"] and _pending(state, "project") == 0


def test_a_tick_skips_while_a_publication_is_in_flight(dirs):
    state = dirs[0]
    clock = FakeClock()
    with open_database(state) as db:
        _admit(db.connection, "publication_1", "publication", None, clock)
    ran = []
    for name, body in (("derive", derive.run), ("maintain", maintain.run)):
        with _tick(name, dirs, clock) as current:
            body(current)
            ran.append(current.bundle)
        assert current.record is not None
        assert current.record["skipped"] == "publication_in_flight"
        assert not current.record["failed"]
        assert (state / "runs" / f"{current.run_id}.json").is_file()
    assert ran == [None, None]
    with open_database(state, lock=False) as db:
        # Nothing was written: the fence would refuse it.
        assert db.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert db.connection.execute("SELECT count(*) FROM loop_state").fetchone()[0] == 0
    # Release, the only publisher among the loops, settles a dead publication.
    with _tick("release", dirs, clock) as current:
        pass
    assert current.summary["recovered"]["admissions"] == 1
    with open_database(state, lock=False) as db:
        assert (
            db.connection.execute("SELECT state FROM execution_admissions").fetchone()[0]
            == "uncertain"
        )
    assert not _derive(dirs, clock)["skipped"]


def test_a_closure_publication_pauses_the_loops_and_a_narrow_one_does_not(dirs, projected):
    """A partitioned publication fences only its own tables (WP10): derive
    ticks through it; maintain and release, which write those tables, skip."""
    state = dirs[0]
    clock = FakeClock()
    _accepted(dirs, clock)
    _enqueue(state, clock, ["a", "b"])
    with open_database(state) as db:
        _admit(db.connection, "publication_narrow_1", "publication_narrow", None, clock)
    clock.sleep(60)
    record = _derive(dirs, clock)
    assert record["skipped"] is None and not record["failed"], record
    assert record["progress"] == 2 and sorted(projected) == ["a", "b"]
    assert PUBLICATION_IN_FLIGHT not in record["counters"]
    with _tick("maintain", dirs, clock) as current:
        maintain.run(current)
    assert current.record is not None and current.record["skipped"] == PUBLICATION_IN_FLIGHT
    # The narrow publication ends; a closure-mode one pauses derive again.
    _enqueue(state, clock, ["c"])
    with open_database(state) as db:
        db.connection.execute(
            "UPDATE execution_admissions SET state='settled',settled_at=?,outcome='published'",
            (clock.now().isoformat(),),
        )
        _admit(db.connection, "publication_2", "publication", None, clock)
    clock.sleep(60)
    paused = _derive(dirs, clock)
    assert paused["skipped"] == PUBLICATION_IN_FLIGHT and "c" not in projected


def _release_row(state, clock, run_id, skipped, *, completed_build=False):
    """A release tick that finished now, skipped for `skipped` (None: it ran)."""
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,loop,summary_json) "
            "VALUES (?,?,?,1,'release',?)",
            (
                run_id,
                clock.now().isoformat(),
                clock.now().isoformat(),
                json.dumps({"loop": "release", "skipped": skipped}),
            ),
        )
        if completed_build:
            db.connection.execute(
                "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,"
                "control_revision,run_id,all_sources,all_kinds,state,settled_at,outcome) "
                "VALUES (?,'build',?,0,?,1,1,'settled',?,'completed')",
                (f"build-{run_id}", clock.now().isoformat(), run_id, clock.now().isoformat()),
            )


def test_the_release_lag_knob_holds_project_units_and_leaves_a_guard(dirs, projected):
    """Builds are due, release has not progressed for two build intervals and
    its ticks were skipped because derive held the heavy lock: derive runs
    parse and link units only, and says so with a clocked guard."""
    from swingset.loops.derive import RELEASE_LAG

    state = dirs[0]
    clock = FakeClock()
    _accepted(dirs, clock)
    built = _release_tick(dirs, clock)
    assert built["candidate_id"] and built["progress"] == 1
    interval = load_config(dirs[1]).scheduler.build_interval_seconds
    assert load_config(dirs[1]).scheduler.release_lag_intervals == 2
    clock.sleep(3 * interval)
    _release_row(state, clock, "release_busy", "heavy_busy")
    _enqueue(state, clock, ["a", "b"])
    held = _derive(dirs, clock)
    assert not held["failed"] and held["counters"][RELEASE_LAG] == 1
    assert held["release_lag"] == {
        "since": built["finished_at"],
        "release_lag_intervals": 2,
        "build_interval_seconds": interval,
        "engaged": True,
        "cause": "heavy_busy",
    }
    assert "project" not in held["stages"] and projected == []
    with open_database(state) as db:
        row = guards.get(db.connection, "loop:derive", RELEASE_LAG)
        assert row is not None and row.loop == "derive"
        assert row.expires_at == queue.at(clock.now() + timedelta(hours=1))
    # 0 turns the knob off.
    sources = dirs[1] / "sources.toml"
    shipped = sources.read_text()
    sources.write_text(shipped.replace("release_lag_intervals = 2", "release_lag_intervals = 0"))
    clock.sleep(60)
    off = _derive(dirs, clock)
    assert RELEASE_LAG not in off["counters"] and sorted(projected) == ["a", "b"]
    with open_database(state) as db:
        assert guards.get(db.connection, "loop:derive", RELEASE_LAG) is None
    # Back on: a release that built within the interval is not behind.
    sources.write_text(shipped)
    clock.sleep(60)
    _accepted(dirs, clock)
    assert _release_tick(dirs, clock)["candidate_id"]
    _enqueue(state, clock, ["c"])
    clock.sleep(60)
    caught_up = _derive(dirs, clock)
    assert RELEASE_LAG not in caught_up["counters"] and "c" in projected


@pytest.mark.parametrize("skipped", [None, "operator_pause", "disk", "not_ticking"])
def test_a_release_behind_for_other_causes_leaves_derive_alone(dirs, projected, skipped):
    """Holding project units cannot help a release that is stopped, paused or
    out of disk: the lag is reported and derive leases project units (I5)."""
    from swingset.loops.derive import RELEASE_LAG

    state = dirs[0]
    clock = FakeClock()
    _accepted(dirs, clock)
    assert _release_tick(dirs, clock)["candidate_id"]
    interval = load_config(dirs[1]).scheduler.build_interval_seconds
    clock.sleep(3 * interval)
    if skipped != "not_ticking":
        _release_row(state, clock, "release_other", skipped)
    _enqueue(state, clock, ["a", "b"])
    record = _derive(dirs, clock)
    assert RELEASE_LAG not in record["counters"] and sorted(projected) == ["a", "b"]
    lag = record["release_lag"]
    assert lag["engaged"] is False
    assert (
        lag["cause"]
        == {
            None: "release_not_building",
            "operator_pause": "operator_pause",
            "disk": "disk",
            "not_ticking": "release_not_ticking",
        }[skipped]
    )
    with open_database(state) as db:
        assert guards.get(db.connection, "loop:derive", RELEASE_LAG) is None


def test_a_completed_build_is_release_progress(dirs):
    """A reused candidate writes nothing, but its completed build admission
    means release is current: no lag, though its last progress is old."""
    from swingset.loops.derive import release_lag

    state = dirs[0]
    clock = FakeClock()
    _accepted(dirs, clock)
    assert _release_tick(dirs, clock)["candidate_id"]
    config = load_config(dirs[1])
    interval = config.scheduler.build_interval_seconds
    clock.sleep(3 * interval)
    _release_row(state, clock, "release_busy", "heavy_busy")
    with open_database(state) as db:
        assert release_lag(db.connection, config, now=clock.now())["engaged"] is True
    clock.sleep(interval + 60)
    _release_row(state, clock, "release_reused", None, completed_build=True)
    with open_database(state) as db:
        conn = db.connection
        conn.execute(
            "UPDATE execution_admissions SET admitted_at=? WHERE action_kind='build'",
            ((clock.now() - timedelta(seconds=interval + 1)).isoformat(),),
        )
        assert release_lag(conn, config, now=clock.now()) is None


def test_plan_skips_until_derive_accepts_the_inputs(dirs):
    clock = FakeClock()
    with _tick("plan", dirs, clock) as current:
        pass
    assert current.record is not None
    assert current.record["skipped"] == "inputs_not_accepted"
    assert "accepted_inputs" not in current.record
    _derive(dirs, clock)
    with _tick("plan", dirs, clock) as current:
        pass
    assert current.record is not None and current.record["skipped"] is None


def test_maintain_prunes_and_checkpoints(dirs):
    state = dirs[0]
    clock = FakeClock()
    now = clock.now()
    with open_database(state) as db:
        conn = db.connection
        for subject, seen in (("host:old", now - timedelta(days=8)), ("host:new", now)):
            guards.upsert(
                conn,
                guards.GuardRow(
                    subject=subject,
                    reason="request budget",
                    loop="acquire",
                    since=queue.at(seen),
                    last_seen_at=queue.at(seen),
                    expires_at=queue.at(seen + timedelta(hours=1)),
                ),
            )
        # A watch held a week for its requests' errors keeps its count until
        # a week past its expiry, so a fourth failure holds it a week again.
        for subject, seen in (
            ("watch:held", now - timedelta(days=8)),
            ("watch:done", now - timedelta(days=15)),
        ):
            guards.upsert(
                conn,
                guards.GuardRow(
                    subject=subject,
                    reason=guards.REQUEST_ERROR,
                    loop="acquire",
                    since=queue.at(seen),
                    last_seen_at=queue.at(seen),
                    expires_at=queue.at(seen + guards.REQUEST_ERROR_HOLD),
                    seen=3,
                ),
            )
        conn.execute(
            "INSERT INTO derivation_building(generation_id,stage,unit_kind,unit_id,"
            "input_fingerprint,run_id,started_at,updated_at) "
            "VALUES ('g','project','history','all','f','r',?,?)",
            (queue.at(now - timedelta(days=2)),) * 2,
        )
        # A parse whose pending row went (superseded) keeps a failed queue row;
        # a parse still pending keeps its own.
        stamp = queue.at(now)
        conn.execute(
            "INSERT INTO pending_work VALUES ('parse','snapshot','snap_pending',?)", (stamp,)
        )
        for unit_id in ("snap_gone", "snap_pending"):
            queue.upsert(
                conn,
                queue.QueueRow(
                    stage="parse",
                    unit_kind="snapshot",
                    unit_id=unit_id,
                    partition=f"snapshot/{unit_id}",
                    state="ready",
                    attempts=2,
                    error_class="unit_exception:X",
                    input_fingerprint="fp",
                    lease_owner=None,
                    lease_until=None,
                    next_attempt_at=stamp,
                    last_reason="unit_exception",
                    last_error=None,
                    enqueued_at=stamp,
                    updated_at=stamp,
                    quarantined_at=None,
                ),
            )
    runs = state / "runs"
    runs.mkdir(exist_ok=True)
    old = (now - timedelta(days=15)).isoformat()
    older = (now - timedelta(days=31)).isoformat()
    records = {
        "run_idle": {"loop": "acquire", "failed": False, "progress": 0, "finished_at": old},
        "run_failed": {"loop": "acquire", "failed": True, "progress": 0, "finished_at": old},
        "run_worked": {"loop": "derive", "failed": False, "progress": 3, "finished_at": old},
        "run_cycle": {"failed": False, "finished_at": older},
        "run_recent": {"loop": "acquire", "failed": False, "progress": 0, "finished_at": str(now)},
        # Past 30 days every loop record goes, whatever it did.
        "run_month": {"loop": "derive", "failed": True, "progress": 3, "finished_at": older},
    }
    for name, record in records.items():
        (runs / f"{name}.json").write_text(json.dumps(record))
    import os

    for name, age in (("run_stopped_old", 15), ("run_stopped_new", 1)):
        marker = runs / f"{name}.stop"
        marker.write_bytes(b"")
        stamp = (now - timedelta(days=age)).timestamp()
        os.utime(marker, (stamp, stamp))
    # Loop rows past 30 days keep a stub summary; cycle rows stay whole.
    with open_database(state) as db:
        rows = {}
        for name, loop, finished in (
            ("loop_old", "derive", older),
            ("loop_new", "derive", old),
            ("cycle_old", None, older),
        ):
            run = db.start_run(datetime.fromisoformat(finished))
            summary = {"loop": loop, "failed": True, "progress": 2, "big": "x" * 1000}
            db.connection.execute(
                "UPDATE runs SET loop=?,finished_at=?,summary_json=? WHERE run_id=?",
                (loop, finished, json.dumps(summary), run),
            )
            rows[name] = run
    with _tick("maintain", dirs, clock) as current:
        maintain.run(current)
    record = current.record
    assert record is not None and not record["failed"]
    assert record["records_pruned"] == 3
    assert not (runs / "run_stopped_old.stop").exists()
    assert (runs / "run_stopped_new.stop").exists()
    assert record["run_rows_compacted"] == 1
    assert record["incomplete_generations"] == {"rows": 0, "refs": 0}
    with open_database(state, lock=False) as db:
        summaries = {
            name: json.loads(
                db.connection.execute(
                    "SELECT summary_json FROM runs WHERE run_id=?", (run,)
                ).fetchone()[0]
            )
            for name, run in rows.items()
        }
    assert summaries["loop_old"] == {
        "loop": "derive",
        "failed": True,
        "progress": 2,
        "finished_at": older,
        "compacted": True,
    }
    assert "big" in summaries["loop_new"] and "big" in summaries["cycle_old"]
    for name in ("run_idle", "run_month"):
        assert not (runs / f"{name}.json").exists()
    for name in ("run_failed", "run_worked", "run_cycle", "run_recent"):
        assert (runs / f"{name}.json").exists()
    assert record["guards_pruned"] == 2
    assert record["building"] == {"rows": 1, "stale": 1}
    assert record["queue_orphans"] == 1
    assert set(record["wal"]) == {"busy", "frames", "checkpointed", "bytes"}
    assert record["artifacts"]["removed"] == 0
    # The weekly verify sample ran: nothing is derived here to verify.
    assert record["verify"]["mismatches"] == []
    assert record["progress"] == 8
    with open_database(state, lock=False) as db:
        subjects = [
            row[0]
            for row in db.connection.execute("SELECT subject FROM guard_reasons ORDER BY subject")
        ]
        assert subjects == ["host:new", "watch:held"]
        left = [row[0] for row in db.connection.execute("SELECT unit_id FROM work_queue")]
        assert left == ["snap_pending"]


def test_a_second_tick_of_a_running_loop_is_refused(dirs):
    clock = FakeClock()
    with _tick("derive", dirs, clock):
        with pytest.raises(DatabaseLockedError) as refused:
            with _tick("derive", dirs, clock):
                pass
        assert refused.value.reason == "loop running"
        # Another loop runs beside it.
        with _tick("maintain", dirs, clock) as other:
            maintain.run(other)
        assert other.record is not None and not other.record["failed"]


def test_cli_derive_and_maintain_ticks(dirs):
    state, config, overrides = dirs
    common = ["--state", str(state), "--config", str(config), "--overrides", str(overrides)]
    assert main(["derive", *common, "--budget", "60", "--timer"]) == 0
    assert main(["maintain", *common, "--timer"]) == 0
    loops = {json.loads(path.read_bytes()).get("loop") for path in (state / "runs").glob("*.json")}
    assert loops == {"derive", "maintain"}
    held = rwlock.exclusive(state, timeout=0)
    try:
        # A writer holds the state: a timer tick skips, a manual one fails.
        assert main(["derive", *common, "--timer"]) == 0
        assert main(["derive", *common]) == 1
    finally:
        held.release()


def test_the_cycle_summary_keeps_its_keys(dirs):
    """The offline loop moved to schedule/offline.py; the cycle reports as before."""
    from swingset.schedule.cycle import run_cycle

    state, config, overrides = dirs
    (config / "sources.toml").write_text(
        '[sources.wsdc_calendar]\nenabled = true\nindex_urls = ["https://worldsdc.com/events/"]\n'
    )
    fixture = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=fixture)

    with open_database(state) as db:
        summary = run_cycle(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=FakeClock(),
            transport=httpx.MockTransport(handler),
        )
    assert not summary["failed"] and "parse" in summary["stages"]
    assert sorted(summary) == sorted(
        """accepted_inputs bytes_by_host candidate_id changed checked control_timeouts
        controls derivation dry_run duration_seconds event_blocker_observations
        event_enumerations event_pressure_bootstrap event_pressure_refresh
        event_progress_refresh failed finished_at guard_reasons guards held_operations
        held_units interrupted_attempts nonce_unchanged not_modified parse_errors
        parse_hint_recovery partitions paused_hosts peak_rss_bytes phase_two_refusals queue queue_states
        readiness_errors recovered_admissions registry_verification requirements_scanned
        restored_artifacts retention_cap run_id scheduler stage_errors stages
        stages_disabled stopped unit_failures verdict waiting_repeats write_deadline_fired
        write_lock_timeouts write_seconds_max write_wait_seconds_max""".split()
    )


# The plan and acquire loops (D-0218).

ARCHIVE_BODY = Path("tests/fixtures/sources/eepro-archive/freedomswing2019.html")


@pytest.fixture
def history(dirs, monkeypatch):
    """An accepted 2019 year with one retained Wayback capture of a reviewed kind,
    and one due origin watch; the state is closed so loop ticks can open it."""
    from types import SimpleNamespace

    from materialized_fixture import materialize_seeded_outputs
    from test_history_shared_gates import reviewed_index
    from test_platform_backfill import CAPTURES, EVENT, accept

    from swingset.config import Config, HostConfig, SourceConfig
    from swingset.model.canonical import Event
    from swingset.project.writer import Projection, replace_scope
    from swingset.schedule.cycle import versions
    from swingset.schedule.discover import discover
    from swingset.state.inputs import accept as accept_inputs
    from swingset.state.inputs import capture

    state, config, overrides = dirs
    (config / "sources.toml").write_text("[sources.eepro]\nenabled = true\n")
    monkeypatch.setenv("SWINGSET_REVISION", "offline-loop-history")
    clock = FakeClock()
    with open_database(state) as db:
        conn = db.connection
        run = db.start_run(clock.now())
        at = clock.now().isoformat()
        event = Event(
            event_id=EVENT.event_id,
            series_id="series",
            name="Example",
            year=EVENT.year,
            start_date="2019-01-18",
            end_date=EVENT.end_date.isoformat(),
            source="wsdc_registry",
            snapshot_id="inventory",
            parser_version="1",
            first_seen_at=at,
            last_seen_at=at,
            run_id=run,
        )
        replace_scope(
            conn,
            scope_kind="history",
            scope_id="fixture",
            projection=Projection((event,)),
            run_id=run,
            projected_at=at,
        )
        conn.execute("DELETE FROM pending_work")
        conn.execute(
            "INSERT INTO source_event_map VALUES (?,?,?,'explicit',1)",
            (EVENT.source, EVENT.source_ref, EVENT.event_id),
        )
        conn.executemany(
            "INSERT INTO meta VALUES (?,'true')", [("h7_deployed",), ("h10_deployed",)]
        )
        conn.executemany(
            "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,'retained')",
            [("eepro", c.url, c.timestamp, c.digest, c.mimetype, c.length, at) for c in CAPTURES],
        )
        f = SimpleNamespace(
            db=db,
            conn=conn,
            clock=clock,
            run=run,
            config=Config(
                {"web.archive.org": HostConfig(min_gap_seconds=10)}, {"eepro": SourceConfig(True)}
            ),
        )
        materialize_seeded_outputs(db, now=clock.now(), run_id=run)
        reviewed_index(f)
        bundle = capture(config, overrides, state, versions())
        accept_inputs(db, bundle, clock)
        discover(db, bundle, clock.now())
        conn.execute("DELETE FROM pending_work")
        conn.execute("UPDATE watches SET state='paused',next_check_at=NULL")
        accept(f)
        # One ordinary origin watch comes due.
        origin = conn.execute(
            "SELECT watch_id FROM watches WHERE url='https://eepro.com/results/review-control/'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE watches SET state='live',next_check_at=? WHERE watch_id=?", (at, origin)
        )
        conn.execute("DELETE FROM pending_work")
    return SimpleNamespace(clock=clock, origin=origin, event=EVENT)


def _plan_tick(dirs, clock, **kwargs):
    from swingset.loops import plan

    with _tick("plan", dirs, clock, budget=kwargs.pop("budget", 600), **kwargs) as current:
        plan.run(current)
    assert current.record is not None
    return current.record


def _acquire_tick(dirs, clock, handler):
    from swingset.loops import acquire

    transport = httpx.MockTransport(handler)
    with _tick("acquire", dirs, clock, budget=240, transport=transport) as current:
        acquire.run(current)
    assert current.record is not None
    return current.record


def _plan_rows(state):
    from swingset.state import acquisition_plan

    with open_database(state) as db:
        return {
            row[0]: acquisition_plan.get(db.connection, row[0])
            for row in db.connection.execute("SELECT plan_key FROM acquisition_plan")
        }


def _requests():
    seen = []
    body = ARCHIVE_BODY.read_bytes()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request.url)
        return httpx.Response(200, content=body)

    return seen, handler


def test_plan_writes_year_and_page_rows_with_reasons(dirs, history):
    from swingset.state.acquisition_plan import ELIGIBLE

    record = _plan_tick(dirs, history.clock)
    assert not record["failed"] and record["skipped"] is None
    plan = record["plan"]
    assert not plan["truncated"] and plan["evaluated"] >= 1
    assert record["progress"] == plan["written"] >= 2
    rows = _plan_rows(dirs[0])
    year = rows["year:2019"]
    assert year.kind == "year" and year.reason == ELIGIBLE
    detail = json.loads(year.detail_json)
    assert detail["accepted_digest"] and isinstance(detail["projection_pending"], bool)
    pages = [row for row in rows.values() if row.kind == "page"]
    assert pages and all(row.rank is not None for row in pages)
    eligible = [row for row in pages if row.reason == ELIGIBLE]
    assert len(eligible) == 1
    page = eligible[0]
    assert page.url == "https://eepro.com/results/example2019/"
    assert page.archive_url and "web.archive.org" in page.archive_url
    assert page.source_ref == history.event.source_ref and page.year == 2019
    assert json.loads(page.detail_json) == {"via": "archive"}
    assert plan["rows"] == {"page/eligible": 1, "year/eligible": 1}
    # Without acceptance the year and its pages carry the gate's reason.
    with open_database(dirs[0]) as db:
        db.connection.execute("DELETE FROM history_acceptance")
    history.clock.sleep(60)
    _plan_tick(dirs, history.clock)
    rows = _plan_rows(dirs[0])
    assert rows["year:2019"].reason == "year_unaccepted"
    assert rows[page.plan_key].reason == "history_year_unaccepted"
    assert rows[page.plan_key].archive_url is None


def test_a_failing_report_or_stage_never_freezes_the_plan(dirs, history, monkeypatch):
    """projection_pending is information only, and a bookkeeping stage's
    defect is a stage error: the plan rows are still written."""
    import swingset.state.verification as verification
    from swingset.project import history as history_module

    def broken(*_args, **_kwargs):
        raise RuntimeError("defect")

    monkeypatch.setattr(history_module, "projection_pending", broken)
    monkeypatch.setattr(verification, "verification_summary", broken)
    record = _plan_tick(dirs, history.clock)
    assert record["error"] is None and record["failed"]
    assert record["progress"] == record["plan"]["written"] >= 2
    assert "RuntimeError" in record["projection_pending_error"]
    assert record["plan"]["projection_pending"] is None
    assert [error["stage"] for error in record["stage_errors"]] == ["registry_verification"]


def test_a_truncated_plan_pass_keeps_older_page_rows(dirs, history):
    from swingset.state import acquisition_plan

    _plan_tick(dirs, history.clock)
    stale = acquisition_plan.PlanRow(
        acquisition_plan.page_key("gone"), "page", "eligible", "2000-01-01T00:00:00+00:00", "old"
    )
    with open_database(dirs[0]) as db:
        with db.transaction() as conn:
            acquisition_plan.upsert(conn, stale)
    # A budget under the write reserve reaches no page: the pass is cut.
    cut = _plan_tick(dirs, history.clock, budget=5)
    assert cut["plan"]["truncated"] and cut["plan"]["evaluated"] == 0
    rows = _plan_rows(dirs[0])
    assert "page:gone" in rows and "year:2019" in rows
    assert rows["year:2019"].run_id == cut["run_id"]
    assert any(row.kind == "page" and row.reason == "eligible" for row in rows.values())
    # A whole pass deletes the pages it did not write.
    whole = _plan_tick(dirs, history.clock)
    assert not whole["plan"]["truncated"] and whole["plan"]["deleted"] >= 1
    assert "page:gone" not in _plan_rows(dirs[0])


def test_acquire_fetches_an_eligible_plan_row_and_a_due_watch_on_facts_alone(
    dirs, history, monkeypatch
):
    from swingset.project import history as history_module

    _plan_tick(dirs, history.clock)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("acquire asked a derivation predicate")

    monkeypatch.setattr(history_module, "pending_inventory_projection", forbidden)
    monkeypatch.setattr(history_module, "_pending_inventory_projection", forbidden)
    monkeypatch.setattr(history_module, "projection_pending", forbidden)
    seen, handler = _requests()
    record = _acquire_tick(dirs, history.clock, handler)
    assert not record["failed"] and record["error"] is None, record
    assert record["offered"] == 1
    assert record["history_dispatch"]["reason"] == "dispatched"
    hosts = {url.host for url in seen}
    assert "web.archive.org" in hosts and "eepro.com" in hosts
    assert any("example2019" in url.path for url in seen if url.host == "web.archive.org")
    assert any(url.path == "/results/review-control/" for url in seen if url.host == "eepro.com")
    assert record["checked"] >= 2 and record["progress"] >= 2
    with open_database(dirs[0]) as db:
        loop = db.connection.execute(
            "SELECT loop FROM runs WHERE run_id=?", (record["run_id"],)
        ).fetchone()[0]
        assert loop == "acquire"


def test_plan_and_acquire_tick_through_a_narrow_publication(dirs, history):
    """A partitioned publication leaves acquisition running (WP10)."""
    with open_database(dirs[0]) as db:
        _admit(db.connection, "publication_narrow_1", "publication_narrow", None, history.clock)
    planned = _plan_tick(dirs, history.clock)
    assert planned["skipped"] is None and not planned["failed"], planned
    seen, handler = _requests()
    record = _acquire_tick(dirs, history.clock, handler)
    assert record["skipped"] is None and not record["failed"], record
    assert record["offered"] == 1 and record["progress"] >= 2 and seen
    with open_database(dirs[0]) as db:
        assert (
            db.connection.execute(
                "SELECT state FROM execution_admissions WHERE action_id='publication_narrow_1'"
            ).fetchone()[0]
            == "active"
        )


def test_a_request_that_raises_holds_its_watch_and_the_pass_goes_on(dirs, history, monkeypatch):
    """One poisoned plan row stops only itself (I1): the due origin watch is
    fetched in the same tick and later ones, the tick reports the error, and
    the row waits on a clock, an hour doubling, a week from the third (I3).
    Contention skips the request and holds nothing."""
    import sqlite3

    import swingset.history.backfill as backfill

    _plan_tick(dirs, history.clock)
    clock = history.clock
    calls: list[str] = []
    fault: list[BaseException] = [RuntimeError("dispatch defect for one plan row")]
    real_dispatch = backfill.dispatch_one

    def dispatch_one(*_args, **_kwargs):
        calls.append("dispatch")
        raise fault[0]

    monkeypatch.setattr(backfill, "dispatch_one", dispatch_one)

    def due():
        with open_database(dirs[0]) as db:
            db.connection.execute(
                "UPDATE watches SET next_check_at=? WHERE watch_id=?",
                (clock.now().isoformat(), history.origin),
            )

    def tick():
        seen, handler = _requests()
        record = _acquire_tick(dirs, clock, handler)
        assert record["error"] is None, record
        assert any(url.path == "/results/review-control/" for url in seen), seen
        return record

    def held():
        with open_database(dirs[0]) as db:
            return [
                (row.seen, row.expires_at)
                for row in [
                    guards.get(db.connection, subject, guards.REQUEST_ERROR)
                    for (subject,) in db.connection.execute(
                        "SELECT subject FROM guard_reasons WHERE reason=?",
                        (guards.REQUEST_ERROR,),
                    )
                ]
                if row is not None
            ]

    first = tick()
    assert first["failed"] and calls == ["dispatch"]
    (error,) = first["request_errors"]
    assert error["failures"] == 1 and "RuntimeError" in error["error"]
    assert held() == [(1, queue.at(clock.now() + timedelta(hours=1)))]
    # Held: the next tick fetches the origin watch and leaves the row alone.
    clock.sleep(600)
    due()
    quiet = tick()
    assert not quiet["failed"] and "request_errors" not in quiet and calls == ["dispatch"]
    for number, hold in ((2, timedelta(hours=2)), (3, timedelta(days=7))):
        clock.sleep(3 * 3600)
        due()
        again = tick()
        assert again["failed"] and len(calls) == number
        assert held() == [(number, queue.at(clock.now() + hold))]
    # Contention is not the watch's fault: skipped, nothing held or counted.
    with open_database(dirs[0]) as db:
        db.connection.execute("DELETE FROM guard_reasons WHERE reason=?", (guards.REQUEST_ERROR,))
    fault[0] = sqlite3.OperationalError("database is locked")
    clock.sleep(600)
    due()
    busy = tick()
    assert not busy["failed"] and busy["request_errors"][0]["skipped"]
    assert held() == []
    # So is an operator pause or a control-lock timeout met by the request.
    from swingset.state.control_lock import ControlTimeout
    from swingset.state.controls import ControlPaused

    for waited in (ControlPaused([{"pause_id": "p1"}]), ControlTimeout("control.lock busy")):
        fault[0] = waited
        clock.sleep(600)
        due()
        paused = tick()
        assert not paused["failed"] and paused["request_errors"][0]["skipped"], paused
        assert held() == []
    # A request that goes through after its clock clears the row.
    key = busy["request_errors"][0]["watch"]
    with open_database(dirs[0]) as db:
        with db.transaction() as conn:
            guards.request_failed(conn, key, loop="acquire", now=clock.now())
    monkeypatch.setattr(backfill, "dispatch_one", real_dispatch)
    clock.sleep(2 * 3600)
    due()
    healed = tick()
    assert healed["history_dispatch"]["reason"] == "dispatched", healed
    assert held() == []


def test_a_watch_whose_write_after_its_request_raises_waits_on_its_clock(
    dirs, history, monkeypatch
):
    """The request went out, so the pass's guard flush clears the watch as
    requested; its `request_error` row stays, and the next due tick leaves the
    watch alone instead of fetching it again (I3)."""
    import swingset.schedule.acquisition as acquisition

    clock = history.clock
    real_refresh = acquisition.refresh_policy

    def refresh_policy(conn, config, watch_id, *args, **kwargs):
        if watch_id == history.origin:
            raise RuntimeError("write after the request")
        return real_refresh(conn, config, watch_id, *args, **kwargs)

    monkeypatch.setattr(acquisition, "refresh_policy", refresh_policy)

    def origin_fetches(seen):
        return [url for url in seen if url.path == "/results/review-control/"]

    seen, handler = _requests()
    first = _acquire_tick(dirs, clock, handler)
    assert first["error"] is None and first["failed"], first
    assert len(origin_fetches(seen)) == 1
    (error,) = first["request_errors"]
    assert error["watch"] == history.origin and error["failures"] == 1
    with open_database(dirs[0]) as db:
        row = guards.get(db.connection, f"watch:{history.origin}", guards.REQUEST_ERROR)
        assert row is not None and row.expires_at == queue.at(clock.now() + timedelta(hours=1))
        db.connection.execute(
            "UPDATE watches SET next_check_at=? WHERE watch_id=?",
            (clock.now().isoformat(), history.origin),
        )
    clock.sleep(600)
    seen, handler = _requests()
    quiet = _acquire_tick(dirs, clock, handler)
    assert quiet["error"] is None and "request_errors" not in quiet, quiet
    assert origin_fetches(seen) == []


def test_acquire_refuses_a_row_whose_year_digest_changed_after_planning(dirs, history):
    _plan_tick(dirs, history.clock)
    with open_database(dirs[0]) as db:
        # The committed event list moves: the accepted digest no longer matches.
        db.connection.execute("UPDATE events SET start_date='2019-01-17' WHERE year=2019")
    seen, handler = _requests()
    record = _acquire_tick(dirs, history.clock, handler)
    assert record["offered"] == 1
    assert record["history_dispatch"]["reason"] == "history_year_unaccepted"
    assert record["phase_two_refusals"]["history_year_unaccepted"] >= 1
    assert not any(url.host == "web.archive.org" for url in seen)
    assert _plan_rows(dirs[0])["year:2019"].reason == "eligible"


def test_cli_plan_and_acquire_ticks(dirs, history):
    state, config, overrides = dirs
    common = ["--state", str(state), "--config", str(config), "--overrides", str(overrides)]
    assert main(["plan", *common, "--timer"]) == 0
    loops = {json.loads(path.read_bytes()).get("loop") for path in (state / "runs").glob("*.json")}
    assert "plan" in loops


@pytest.mark.parametrize("loop", ["acquire", "plan", "derive", "maintain"])
def test_acquire_plan_and_derive_judge_the_year_gate_on_facts(dirs, loop):
    from swingset.project.history import facts_only

    assert not facts_only()
    with _tick(loop, dirs, FakeClock()) as current:
        inside = facts_only()
    assert current.record is not None
    assert inside == (loop != "maintain")
    assert not facts_only()


# The release loop (WP9).


def _release_tick(dirs, clock, *, disk_file=None, **kwargs):
    from swingset.loops import release

    disk = disk_file if disk_file is not None else dirs[0].parent / "no-disk-quota"
    with _tick("release", dirs, clock, budget=kwargs.pop("budget", 1800), **kwargs) as current:
        release.run(current, disk_file=disk)
    assert current.record is not None
    return current.record


def _accepted(dirs, clock):
    """Derive accepts the inputs; release waits for it."""
    record = _derive(dirs, clock)
    assert not record["failed"]


def _release_path(dirs, path):
    """Set `[release] path` in the test's config copy (whole and closure take the heavy lock)."""
    sources = dirs[1] / "sources.toml"
    body = sources.read_text()
    assert 'path = "partitioned"' in body
    sources.write_text(body.replace('path = "partitioned"', f'path = "{path}"'))


def test_a_release_tick_builds_then_defers_inside_the_build_interval(dirs):
    clock = FakeClock()
    assert _release_tick(dirs, clock)["skipped"] == "inputs_not_accepted"
    _accepted(dirs, clock)
    record = _release_tick(dirs, clock)
    assert record["skipped"] is None and not record["failed"], record
    assert record["candidate_id"] and "build" in record["stages"]
    assert record["counters"]["builds"] == 1 and record["progress"] == 1
    assert record["dry_run"] and "publication" not in record and "reconciliation" not in record
    assert "build_deferred" not in record and record["disk"] is None
    # config/sources.toml sets build_interval_seconds to 6 hours.
    clock.sleep(600)
    again = _release_tick(dirs, clock)
    assert again["build_deferred"] == "interval" and not again["failed"]
    assert "candidate_id" not in again and again["progress"] == 0
    assert "heavy_wait_seconds" not in again
    with open_database(dirs[0]) as db:
        builds = db.connection.execute(
            "SELECT count(*) FROM execution_admissions WHERE action_kind='build'"
        ).fetchone()[0]
        assert builds == 1
        assert (
            db.connection.execute(
                "SELECT loop FROM runs WHERE run_id=?", (again["run_id"],)
            ).fetchone()[0]
            == "release"
        )


def test_derive_starts_no_unit_while_release_builds(dirs, projected, monkeypatch):
    """No project unit and no parse unit starts beside a whole build (F10)."""
    from swingset.schedule import cycle
    from swingset.state.work import enqueue

    state = dirs[0]
    clock = FakeClock()
    _release_path(dirs, "whole")
    _accepted(dirs, clock)
    _enqueue(state, clock, ["a", "b"])
    with open_database(state) as db:
        enqueue(
            db.connection,
            [WorkUnit("parse", "snapshot", "during-build")],
            enqueued_at=clock.now().isoformat(),
        )
    original = cycle.build
    during = []

    def build(*args, **kwargs):
        # Another loop ticks while release holds the heavy lock.
        during.append(_derive(dirs, clock))
        return original(*args, **kwargs)

    monkeypatch.setattr(cycle, "build", build)
    record = _release_tick(dirs, clock)
    assert record["candidate_id"] and not record["failed"]
    assert record["heavy_wait_seconds"] == 0
    (derived,) = during
    assert derived["counters"]["heavy_deferred"] == 1 and not derived["failed"]
    assert derived["stages"] == [] and derived["progress"] == 0
    assert projected == [] and _pending(state, "project") == 2
    assert _pending(state, "parse") == 1
    with open_database(state) as db:
        assert (
            db.connection.execute(
                "SELECT count(*) FROM work_attempts WHERE unit_id='during-build'"
            ).fetchone()[0]
            == 0
        )
    # Once the build is over, derive takes the project units.
    clock.sleep(60)
    assert "heavy_deferred" not in _derive(dirs, clock)["counters"]
    assert sorted(projected) == ["a", "b"]


@pytest.mark.parametrize("locked", [True, False])
def test_a_streamed_build_takes_the_heavy_lock_until_streamed_lock_is_off(
    dirs, projected, monkeypatch, locked
):
    """A partitioned build holds the heavy lock by default: the rehearsal
    measured 6.7 GB before F26's fix. With `streamed_lock = false` (after the
    worker measures it under 2 GB beside derive), derive runs beside it."""
    from swingset.schedule import cycle

    state = dirs[0]
    clock = FakeClock()
    if not locked:
        sources = dirs[1] / "sources.toml"
        body = sources.read_text()
        assert "streamed_lock = true" in body
        sources.write_text(body.replace("streamed_lock = true", "streamed_lock = false"))
    _accepted(dirs, clock)
    _enqueue(state, clock, ["a", "b"])
    original = cycle.build
    during = []

    def build(*args, **kwargs):
        during.append(_derive(dirs, clock))
        if not locked:
            rwlock.heavy_exclusive(state, timeout=0).release()
        return original(*args, **kwargs)

    monkeypatch.setattr(cycle, "build", build)
    record = _release_tick(dirs, clock)
    assert record["candidate_id"] and not record["failed"]
    assert record["heavy_lock"] is locked
    assert record["partitions"]["path"] == "partitioned"
    (derived,) = during
    assert not derived["failed"]
    if locked:
        assert derived["counters"]["heavy_deferred"] == 1 and derived["progress"] == 0
    else:
        assert "heavy_deferred" not in derived["counters"]
        assert derived["progress"] == 2 and sorted(projected) == ["a", "b"]


def test_release_skips_when_derive_holds_the_heavy_lock_past_its_wait(dirs):
    state = dirs[0]
    clock = FakeClock()
    _release_path(dirs, "whole")
    _accepted(dirs, clock)
    heavy = rwlock.heavy_shared(state)
    try:
        record = _release_tick(dirs, clock, budget=30)
    finally:
        heavy.release()
    assert record["skipped"] == "heavy_busy" and not record["failed"]
    assert record["counters"]["heavy_busy"] == 1 and record["heavy_wait_seconds"] >= 30
    assert "candidate_id" not in record


def test_release_refuses_to_build_on_a_full_disk(dirs):
    state = dirs[0]
    clock = FakeClock()
    _accepted(dirs, clock)
    disk = state.parent / "swingset-disk"
    # 250 GB used of 260 GB: 10 GB free, under the 30 GB floor.
    disk.write_text("250000000000 260000000000 2026-09-27T00:00:00Z\n")
    record = _release_tick(dirs, clock, disk_file=disk)
    assert record["skipped"] == "disk" and not record["failed"]
    assert record["disk"] == {
        "free_bytes": 10_000_000_000,
        "read_at": "2026-09-27T00:00:00Z",
        "minimum": 30_000_000_000,
        "stale": False,
    }
    assert "candidate_id" not in record
    with open_database(state) as db:
        row = guards.get(db.connection, "loop:release", "release_disk_low")
        assert row is not None and row.loop == "release"
        assert row.expires_at == queue.at(clock.now() + timedelta(hours=1))
        assert (
            db.connection.execute(
                "SELECT count(*) FROM execution_admissions WHERE action_kind='build'"
            ).fetchone()[0]
            == 0
        )
    # Room again: the tick builds and the guard clears.
    clock.sleep(3600)
    disk.write_text("200000000000 260000000000 2026-09-27T01:00:00Z\n")
    built = _release_tick(dirs, clock, disk_file=disk)
    assert built["skipped"] is None and built["candidate_id"]
    with open_database(state) as db:
        assert guards.get(db.connection, "loop:release", "release_disk_low") is None


def test_a_release_build_error_is_reported_and_fails_the_tick(dirs, monkeypatch):
    from swingset.build.builder import BuildError
    from swingset.schedule import cycle

    clock = FakeClock()
    _accepted(dirs, clock)

    def broken(*_args, **_kwargs):
        raise BuildError("stale generation")

    monkeypatch.setattr(cycle, "build", broken)
    record = _release_tick(dirs, clock)
    assert record["failed"] and record["error"] is None
    assert record["build_error"] == "BuildError: stale generation"
    # The heavy lock is free again.
    rwlock.heavy_exclusive(dirs[0], timeout=0).release()


def test_cli_release_tick(dirs, monkeypatch):
    from swingset.loops import release

    # The worker has a real quota file; the test must not read it.
    monkeypatch.setattr(release, "DISK_FILE", dirs[0].parent / "no-disk-quota")
    state, config, overrides = dirs
    common = ["--state", str(state), "--config", str(config), "--overrides", str(overrides)]
    assert main(["derive", *common, "--timer"]) == 0
    assert main(["release", *common, "--dry-run", "--timer"]) == 0
    records = [json.loads(path.read_bytes()) for path in (state / "runs").glob("*.json")]
    (released,) = [record for record in records if record.get("loop") == "release"]
    assert released["dry_run"] and released["candidate_id"]
    # Publishing needs the Hub token; without it nothing runs.
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert main(["release", *common, "--publish"]) == 1


def test_whole_tick_skips_leave_loop_guards_with_expiries(dirs):
    """A release that waits for inputs or for derive's heavy lock says so in
    `guard_reasons`, with a clock, and the row goes once a tick gets past it."""
    state = dirs[0]
    clock = FakeClock()
    _release_path(dirs, "whole")
    assert _release_tick(dirs, clock)["skipped"] == "inputs_not_accepted"
    with open_database(state) as db:
        row = guards.get(db.connection, "loop:release", "inputs_not_accepted")
        assert row is not None and row.expires_at == queue.at(clock.now() + timedelta(hours=1))
    _accepted(dirs, clock)
    clock.sleep(60)
    heavy = rwlock.heavy_shared(state)
    try:
        busy = _release_tick(dirs, clock, budget=1)
    finally:
        heavy.release()
    assert busy["skipped"] == "heavy_busy"
    with open_database(state) as db:
        assert guards.get(db.connection, "loop:release", "inputs_not_accepted") is None
        row = guards.get(db.connection, "loop:release", "heavy_busy")
        assert row is not None and row.expires_at == queue.at(clock.now() + timedelta(hours=3))
    clock.sleep(60)
    built = _release_tick(dirs, clock)
    assert built["candidate_id"] and built["build_attempted"] is True
    shipped = load_config(Path("config")).scheduler.build_interval_seconds
    assert built["build_interval_seconds"] == shipped
    with open_database(state) as db:
        assert guards.get(db.connection, "loop:release", "heavy_busy") is None
    # A tick that only waited for the interval attempted nothing.
    clock.sleep(60)
    waited = _release_tick(dirs, clock)
    assert waited["build_deferred"] == "interval" and "build_attempted" not in waited


def test_an_old_disk_reading_is_unknown_and_refuses_nothing(dirs):
    state = dirs[0]
    clock = FakeClock()
    _accepted(dirs, clock)
    disk = state.parent / "swingset-disk"
    # 10 GB free, under the floor, but read two hours before this tick.
    stamp = (clock.now() - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    disk.write_text(f"250000000000 260000000000 {stamp}\n")
    record = _release_tick(dirs, clock, disk_file=disk)
    assert record["disk"]["stale"] is True
    assert record["skipped"] is None and record["candidate_id"]


def test_a_tick_record_carries_its_peak_memory(dirs):
    record = _derive(dirs, FakeClock())
    assert record["peak_rss_bytes"] > 10_000_000


def test_held_units_are_counted_only_while_a_pause_exists(dirs, projected):
    from swingset.state.controls import Selector, change_control

    state = dirs[0]
    clock = FakeClock()
    _enqueue(state, clock, ["a"])
    change_control(
        state,
        selector=Selector("all", "all"),
        paused=True,
        actor="offline-test",
        reason="hold dancers",
        now=clock.now(),
        timeout=1,
    )
    record = _derive(dirs, clock)
    held = record["held_units"]
    assert held["count"] == 1 and held["units"][0]["unit_id"] == "a"
    assert projected == []
    change_control(
        state,
        selector=Selector("all", "all"),
        paused=False,
        actor="offline-test",
        reason="release",
        now=clock.now(),
        timeout=1,
    )
    assert _derive(dirs, clock)["held_units"] == {"count": 0, "units": []}


def test_acquire_skips_a_plan_row_whose_page_left_the_retained_plan(dirs, history):
    from swingset.state import acquisition_plan

    _plan_tick(dirs, history.clock)
    gone = acquisition_plan.PlanRow(
        acquisition_plan.page_key("gone"),
        "page",
        acquisition_plan.ELIGIBLE,
        history.clock.now().isoformat(),
        "run_old",
        year=2019,
        source="eepro",
        source_ref="gone",
        page_kind="eepro.result",
        watch_kind="event_results",
        url="https://eepro.com/results/gone2019/",
        rank=0,
    )
    with open_database(dirs[0]) as db, db.transaction() as conn:
        acquisition_plan.upsert(conn, gone)
    seen, handler = _requests()
    record = _acquire_tick(dirs, history.clock, handler)
    assert not record["failed"], record
    # Dispatch would evaluate the whole plan in one write for it (D-0195).
    assert record["stale_plan_rows"] == 1 and record["offered"] == 1
    assert not any("gone2019" in url.path for url in seen)


def test_doctor_and_summary_report_guards_by_reason(dirs, capsys):
    state, config, _ = dirs
    clock = FakeClock()
    with open_database(state) as db, db.transaction() as conn:
        guards.flush(
            conn,
            loop="acquire",
            seen={
                ("host:eepro.com", "request budget"): 2,
                ("loop:acquire", "pending work backpressure"): 1,
            },
            requested=(),
            now=clock.now(),
        )
    capsys.readouterr()
    assert main(["doctor", "--state", str(state), "--config", str(config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["guards"]
    assert report["supported"] is True
    assert report["by_reason"] == {"pending work backpressure": 1, "request budget": 1}
    assert main(["summary", "--state", str(state), "--config", str(config)]) == 0
    line = next(
        value
        for value in capsys.readouterr().err.splitlines()
        if value.startswith('event="summary" ')
    )
    logged, _ = json.JSONDecoder().raw_decode(line.split("guards=", 1)[1])
    assert logged["by_reason"] == report["by_reason"]


def test_a_derive_tick_stops_between_units_on_sigterm(dirs):
    """A stop (SIGTERM from systemd) ends the batch after the unit in hand; the
    tick still writes its finished record, marked stopped."""
    import subprocess
    import sys

    state, config, overrides = dirs
    clock = FakeClock()
    _enqueue(state, clock, [f"u{n:02}" for n in range(12)])
    script = f"""
import os, signal, sys, time
import swingset.project
from swingset.state import derivations
from swingset.cli import main

def project(database, unit, _bundle, clock, run_id, selection=None):
    if unit.unit_id == "u02":
        os.kill(os.getpid(), signal.SIGTERM)
    derivations.complete(database.connection, selection, rows=(), now=clock.now(), run_id=run_id)
    database.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

swingset.project.process_unit = project
sys.exit(main(["derive", "--state", {str(state)!r}, "--config", {str(config)!r},
               "--overrides", {str(overrides)!r}, "--budget", "600"]))
"""
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert done.returncode in (0, 1), done.stderr[-2000:]
    records = [json.loads(path.read_bytes()) for path in (state / "runs").glob("*.json")]
    (record,) = [item for item in records if item.get("loop") == "derive"]
    assert record["stopped"] is True and record["finished_at"] and record["error"] is None
    # The unit in hand finished; the rest wait for the next tick.
    assert 3 <= record["counters"]["completed"] < 12
    with open_database(state, lock=False) as db:
        conn = db.connection
        assert (
            conn.execute("SELECT count(*) FROM work_attempts WHERE outcome='running'").fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) FROM work_queue WHERE state='leased'").fetchone()[0] == 0
        )
        assert (
            conn.execute(
                "SELECT finished_at FROM runs WHERE run_id=?", (record["run_id"],)
            ).fetchone()[0]
            is not None
        )
