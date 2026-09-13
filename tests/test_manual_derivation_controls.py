"""Manual commands use real admission and attempt ledgers around their workers."""

import json
import shutil
from datetime import UTC, datetime

import pytest

from swingset.cli import main
from swingset.clock import FakeClock
from swingset.schedule.cycle import versions
from swingset.schedule.parse import ParseAttempt
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.controls import Selector, change_control
from swingset.state.db import open_database
from swingset.state.findings import Finding, replace_findings
from swingset.state.inputs import accept, capture
from swingset.state.work import WorkUnit, enqueue


@pytest.mark.parametrize("stage", ["parse", "project", "link"])
@pytest.mark.parametrize(
    "selector", [Selector("source", "eepro"), Selector("kind", "manual_review")]
)
def test_manual_work_skips_paused_unit_progresses_other_and_resumes_once(
    tmp_path, monkeypatch, stage, selector
):
    import swingset.cli as cli
    import swingset.link
    import swingset.project
    import swingset.schedule.derive

    state, config, overrides = (tmp_path / name for name in ("state", "config", "overrides"))
    shutil.copytree("config", config)
    shutil.copytree("overrides", overrides)
    clock = FakeClock(datetime(2026, 9, 13, tzinfo=UTC))
    monkeypatch.setattr(cli, "SystemClock", lambda: clock)
    calls = []

    def commit(database, unit, *, selection=None, run_id=None):
        if unit.unit_id in {"held", "healthy"}:
            calls.append(unit.unit_id)
        if selection is not None:
            from swingset.project.materialization import output_rows
            from swingset.state.derivations import complete

            complete(
                database.connection,
                selection,
                rows=output_rows(database.connection, unit),
                now=clock.now(),
                run_id=run_id,
            )
        database.connection.execute(
            "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
            (unit.stage, unit.unit_kind, unit.unit_id),
        )

    def parse(database, archive, unit, current_clock, run_id):
        commit(database, unit)
        return ParseAttempt("eepro" if unit.unit_id == "held" else "wdr", False)

    monkeypatch.setattr(swingset.schedule.derive, "parse_snapshot", parse)
    monkeypatch.setattr(
        swingset.project,
        "process_unit",
        lambda database, unit, _bundle, _clock, run_id, **kwargs: commit(
            database, unit, run_id=run_id, **kwargs
        ),
    )
    monkeypatch.setattr(
        swingset.link,
        "link_event",
        lambda database, event_id, _bundle, _clock, run_id, **kwargs: commit(
            database, WorkUnit("link", "event", event_id), run_id=run_id, **kwargs
        ),
    )
    units = [
        WorkUnit(stage, "snapshot" if stage == "parse" else "event", name)
        for name in ("held", "healthy")
    ]
    with open_database(state) as db:
        accept(db, capture(config, overrides, state, versions()), clock)
        db.connection.execute("DELETE FROM pending_work")
        run = db.start_run(clock.now())
        for name, source, parser in (
            ("held", "eepro", "eepro.round"),
            ("healthy", "wdr", "wdr.rounds"),
        ):
            watch = WatchSpec(
                "", source, "round", "GET", f"https://{source}.example/{name}", parser
            )
            upsert_watch(db.connection, watch, clock.now())
            db.connection.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET',?,?,200,0,1,?,'Ok')",
                (name, watch.watch_id, watch.url, clock.now().isoformat(), run),
            )
            db.connection.execute(
                "INSERT INTO source_event_map VALUES (?,?,?,'explicit',1)", (source, name, name)
            )
        replace_findings(
            db.connection,
            owner_kind="test",
            owner_id="held",
            findings=(
                Finding("manual_review", "event", "held", "warning", "review held source", {}),
            ),
            opened_at=clock.now().isoformat(),
            run_id=run,
        )
        enqueue(db.connection, units, enqueued_at=clock.now().isoformat())
        if stage != "parse":
            from swingset.project.materialization import output_rows
            from swingset.state import derivations

            # These are deliberately simulated workers. Establish their exact
            # existing output generations before pausing; removing queue rows
            # alone no longer establishes a settled baseline.
            with db.transaction() as conn:
                for baseline_stage in ("project", "link"):
                    for _ in range(20):
                        pending = list(derivations.pending_units(conn, baseline_stage))
                        if not pending:
                            break
                        for unit in pending:
                            if derivations.ready(conn, unit):
                                selected = derivations.capture(conn, unit, now=clock.now())
                                derivations.complete(
                                    conn,
                                    selected,
                                    rows=output_rows(conn, unit),
                                    now=clock.now(),
                                    run_id=run,
                                )
                    assert not list(derivations.pending_units(conn, baseline_stage))
                # Lost materialization is real derived work even when the
                # operational token did not change.
                conn.executemany(
                    "UPDATE derivation_scopes SET materialized_generation_id=NULL WHERE stage=? AND unit_kind=? AND unit_id=?",
                    [(unit.stage, unit.unit_kind, unit.unit_id) for unit in units],
                )
    change_control(
        state,
        selector=selector,
        paused=True,
        actor="operator",
        reason="manual isolation",
        now=clock.now(),
    )
    args = [stage, "--state", str(state), "--config", str(config), "--overrides", str(overrides)]
    assert main(args) == 0
    assert calls == ["healthy"]
    with open_database(state) as db:
        assert (
            db.connection.execute(
                "SELECT unit_id FROM pending_work WHERE stage=?", (stage,)
            ).fetchone()[0]
            == "held"
        )
        assert [
            tuple(row) for row in db.connection.execute("SELECT unit_id,outcome FROM work_attempts")
        ] == [("healthy", "succeeded")]
        summary = json.loads(
            db.connection.execute(
                "SELECT summary_json FROM runs WHERE summary_json IS NOT NULL ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert summary["held_units"][0]["unit_id"] == "held"
        assert summary["held_units"][0]["pauses"][0]["reason"] == "manual isolation"
    change_control(
        state,
        selector=selector,
        paused=False,
        actor="operator",
        reason="review finished",
        now=clock.now(),
    )
    assert main(args) == 0
    assert main(args) == 0
    assert calls == ["healthy", "held"]
    with open_database(state) as db:
        assert db.connection.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 2
        assert (
            db.connection.execute(
                "SELECT count(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )


def test_manual_startup_recovers_publication_before_accepting_inputs(tmp_path, monkeypatch):
    import swingset.cli as cli
    import swingset.state.inputs

    state = tmp_path / "state"
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state) VALUES ('abandoned','publication','2026-09-13T00:00:00Z',0,1,1,'active')"
        )
    original = swingset.state.inputs.accept
    checked = []

    def accept_after_recovery(database, bundle, clock):
        row = database.connection.execute(
            "SELECT state,outcome FROM execution_admissions WHERE action_id='abandoned'"
        ).fetchone()
        assert tuple(row) == ("uncertain", "receipt_reconciliation_required")
        checked.append(True)
        return original(database, bundle, clock)

    monkeypatch.setattr(swingset.state.inputs, "accept", accept_after_recovery)
    monkeypatch.setattr(cli, "SystemClock", lambda: FakeClock(datetime(2026, 9, 13, tzinfo=UTC)))
    assert main(["reparse", "--state", str(state)]) == 0
    assert checked == [True]
    with open_database(state) as db:
        assert (
            db.connection.execute(
                "SELECT state FROM execution_admissions WHERE action_id='abandoned'"
            ).fetchone()[0]
            == "uncertain"
        )


def test_manual_project_saved_crosscheck_obeys_kind_admission(tmp_path, monkeypatch):
    import swingset.cli as cli
    import swingset.schedule.registry as registry

    state, config, overrides = (tmp_path / name for name in ("state", "config", "overrides"))
    shutil.copytree("config", config)
    shutil.copytree("overrides", overrides)
    clock = FakeClock(datetime(2026, 9, 13, tzinfo=UTC))
    monkeypatch.setattr(cli, "SystemClock", lambda: clock)
    calls = []

    def saved(database, *_args):
        calls.append(True)
        database.connection.execute("DELETE FROM meta WHERE key='registry_crosscheck_due'")

    monkeypatch.setattr(registry, "run_saved_crosscheck_if_due", saved)
    with open_database(state) as db:
        accept(db, capture(config, overrides, state, versions()), clock)
        db.connection.execute("DELETE FROM pending_work")
        db.connection.execute("INSERT INTO meta VALUES ('registry_crosscheck_due','retained-dump')")
    selector = Selector("kind", "registry_event_association")
    change_control(
        state,
        selector=selector,
        paused=True,
        actor="operator",
        reason="historical review",
        now=clock.now(),
    )
    args = [
        "project",
        "--state",
        str(state),
        "--config",
        str(config),
        "--overrides",
        str(overrides),
    ]
    assert main(args) == 0
    assert calls == []
    with open_database(state) as db:
        assert (
            db.connection.execute(
                "SELECT value FROM meta WHERE key='registry_crosscheck_due'"
            ).fetchone()[0]
            == "retained-dump"
        )
        assert db.connection.execute("SELECT count(*) FROM execution_admissions").fetchone()[0] == 0
    change_control(
        state, selector=selector, paused=False, actor="operator", reason="reviewed", now=clock.now()
    )
    assert main(args) == 0
    assert main(args) == 0
    assert calls == [True]
    with open_database(state) as db:
        row = db.connection.execute(
            "SELECT action_kind,state,outcome FROM execution_admissions WHERE action_kind='registry_crosscheck'"
        ).fetchone()
        assert tuple(row) == ("registry_crosscheck", "settled", "completed")


def test_explicit_crosscheck_hold_preserves_archive_only_input_path(tmp_path, monkeypatch):
    import swingset.schedule.registry as registry

    state = tmp_path / "state"
    with open_database(state):
        pass
    calls = []
    monkeypatch.setattr(registry, "replay_crosscheck", lambda *_args: calls.append("interpret"))
    monkeypatch.setattr(registry, "archive_crosscheck_dump", lambda *_args: calls.append("archive"))
    selector = Selector("source", "wsdc_registry")
    change_control(
        state,
        selector=selector,
        paused=True,
        actor="operator",
        reason="registry review",
        now=datetime.now(UTC),
    )
    replay = ["registry-crosscheck", "--state", str(state), "--blob", "retained-digest"]
    assert main(replay) == 0
    assert calls == []
    assert (
        main(
            [
                "registry-crosscheck",
                "--state",
                str(state),
                "--archive-only",
                str(tmp_path / "manual.json"),
            ]
        )
        == 0
    )
    assert calls == ["archive"]
    change_control(
        state,
        selector=selector,
        paused=False,
        actor="operator",
        reason="reviewed",
        now=datetime.now(UTC),
    )
    assert main(replay) == 0
    assert calls == ["archive", "interpret"]
    with open_database(state) as db:
        assert [
            tuple(row)
            for row in db.connection.execute("SELECT action_kind,state FROM execution_admissions")
        ] == [("registry_crosscheck", "settled")]


@pytest.mark.parametrize("correction", [False, True])
def test_manual_build_pause_returns_held_receipt_and_successful_run(
    tmp_path, monkeypatch, capsys, correction
):
    state = tmp_path / "state"
    with open_database(state):
        pass
    change_control(
        state,
        selector=Selector("all", "all"),
        paused=True,
        actor="operator",
        reason="publication review",
        now=datetime.now(UTC),
    )
    calls = []
    monkeypatch.setattr(
        "swingset.build.service.build_release", lambda *_args, **_kwargs: calls.append(True)
    )
    args = ["build", "--state", str(state)] + (["--correction-only"] if correction else [])
    assert main(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["state"] == "held"
    assert receipt["action"] == ("correction_build" if correction else "build")
    assert receipt["pauses"][0]["reason"] == "publication review"
    assert calls == []
    with open_database(state) as db:
        summary = json.loads(
            db.connection.execute(
                "SELECT summary_json FROM runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert not summary["failed"]
        assert summary["held_operations"] == [receipt]
