"""Local checkpoints preserve event obligations and paid, unfinished turns."""

import json
import sqlite3
from contextlib import nullcontext
from copy import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_admission import BODY
from test_event_enumerations import admit_parent, child, finish_bootstrap, view
from test_event_enumerations import event as event

from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint, verify_checkpoint
from swingset.build.files import sha256_file
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.controls import issue, release
from swingset.fetch.politeness import Gate, Grant
from swingset.schedule.event_inventory import inventory
from swingset.schedule.event_report import report
from swingset.schedule.fairness import next_watch, prepare_event_turns, servicing
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state import db as state_db

TABLES = (
    "source_event_inventory",
    "source_event_enumerations",
    "source_event_enumeration_members",
    "source_event_member_watches",
    "event_enumeration_inputs",
    "scheduler_event_policies",
    "scheduler_event_turns",
    "scheduler_event_requests",
    "scheduler_capacity_policies",
    "scheduler_capacity_service",
    "scheduler_capacity_requests",
    "cursors",
    "scheduler_requests",
    "host_budget",
    "execution_admissions",
    "admission_decisions",
    "source_generations",
    "source_units",
    "snapshots",
    "scheduler_parent_links",
)


def evidence_rows(conn):
    return {
        table: sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))
        for table in TABLES
    }


def file_hashes(path):
    return {
        file.relative_to(path).as_posix(): sha256_file(file)
        for file in path.rglob("*")
        if file.is_file()
    }


def test_checkpoint_restores_obligations_partial_turn_and_artifact_reopening(event):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    first = view(f)
    admit_parent(f, ["one.htm", "three.htm"])
    _, interpreted_generation = child(f, "one.htm")
    finish_bootstrap(f)
    before = view(f)
    assert before["listed_pages"] == 3  # Unauthorized omission did not retire two.htm.
    assert before["interpreted_pages"] == 1 and before["pagination"] == "unknown"
    assert before["first_known_at"] == first["first_known_at"]

    # Keep this assertion within the same host/class: class fairness may choose
    # an already interpreted page separately from these new-page obligations.
    watches = {
        watch
        for member in before["members"]
        if not member["interpreted"]
        for watch in member["watch_ids"]
    }
    competitor = WatchSpec(
        "",
        "eepro",
        "autoindex",
        "GET",
        "https://eepro.com/results/next-event/",
        "eepro.autoindex",
        source_ref="eepro:next-event",
    )
    upsert_watch(f.conn, competitor, f.corpus.clock.now())
    watches.add(competitor.watch_id)
    exclude = {row[0] for row in f.conn.execute("SELECT watch_id FROM watches")} - watches
    config = Config({"eepro.com": HostConfig()}, {"eepro": SourceConfig(True)})
    clock, run = f.corpus.clock, f.corpus.run
    with f.db.transaction() as conn:
        prepare_event_turns(conn, config, now=clock.now(), exclude=exclude, run_id=run)
    choice = next_watch(f.conn, config, now=clock.now(), exclude=exclude, run_id=run)
    assert choice is not None and choice.turn is not None
    assert choice.capacity is not None and choice.capacity.competing
    assert choice.capacity.lane == "listed_result"
    gate = Gate(f.conn, config, clock)
    with servicing(choice, run_id=run):
        grant, action = issue(
            f.db,
            gate,
            clock,
            host=choice.host,
            source="eepro",
            watch=SimpleNamespace(watch_id=choice.key, kind="round"),
            page_kind="eepro.round",
            crawl_delay=0,
            sweep=False,
        )
    assert isinstance(grant, Grant)
    release(
        f.db,
        gate,
        clock,
        action,
        choice.host,
        Classification(Outcome.OK),
        body_bytes=0,
        request_day=clock.now().date().isoformat(),
    )
    clock.sleep(5)
    assert tuple(
        f.conn.execute(
            "SELECT used,target_requests FROM scheduler_event_turns WHERE owner_key=?",
            (choice.turn.owner_key,),
        ).fetchone()
    ) == (1, 4)
    assert f.conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 1
    assert dict(f.conn.execute("SELECT selected_host,credit FROM scheduler_capacity_service")) == {
        "eepro.com": -50
    }
    assert tuple(
        f.conn.execute(
            "SELECT action_id,lane,reason,policy_digest,credit_before,credit_after "
            "FROM scheduler_capacity_requests"
        ).fetchone()
    ) == (action, "listed_result", "competing", choice.capacity.policy_digest, 0, -50)
    manifest = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?",
            (interpreted_generation,),
        ).fetchone()[0]
    )
    extract_sha = manifest[0]["extract_sha256"]
    expected = evidence_rows(f.conn)
    saved = create_checkpoint(
        f.db.state_dir,
        f.conn,
        f.db.state_dir / "checkpoints" / "event-turn",
        schema_version=state_db.SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    assert f"extracts/{extract_sha}" in saved.files
    checkpoint_hashes = file_hashes(saved.path)
    verify_checkpoint(saved.path, maximum_schema_version=state_db.SCHEMA_VERSION)
    restored = f.db.state_dir / "restored"
    restore_checkpoint(saved.path, restored, maximum_schema_version=state_db.SCHEMA_VERSION)
    assert (restored / "RESTORE_PENDING").exists()
    # Inspect only. A locally installed checkpoint is not permission to activate.
    conn = sqlite3.connect((restored / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    conn.execute("BEGIN")
    try:
        assert evidence_rows(conn) == expected
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        reloaded = replace(config, scheduler=replace(config.scheduler, listed_page_percent=75))
        following = next_watch(conn, reloaded, now=clock.now(), exclude=exclude, run_id=run)
        assert following is not None and following.capacity is not None
        assert following.key == competitor.watch_id
        assert following.capacity.lane == "discovery" and following.capacity.competing
        assert following.capacity.listed_page_percent == 75
        assert following.capacity.policy_digest != choice.capacity.policy_digest
        stored_policy = json.loads(
            conn.execute(
                "SELECT policy_json FROM scheduler_capacity_policies WHERE digest=?",
                (choice.capacity.policy_digest,),
            ).fetchone()[0]
        )
        assert stored_policy["listed_page_percent"] == 50
        # When discovery is ineligible, the old partial event turn can borrow
        # without selection rewriting its turn policy or paid share balance.
        resumed = next_watch(
            conn,
            reloaded,
            now=clock.now(),
            exclude=exclude | {competitor.watch_id},
            run_id=run,
        )
        assert resumed is not None and resumed.turn is not None
        assert resumed.capacity is not None and not resumed.capacity.competing
        assert resumed.capacity.lane == "listed_result"
        assert (resumed.turn.owner_key, resumed.turn.position, resumed.turn.policy_digest) == (
            choice.turn.owner_key,
            choice.turn.position,
            choice.turn.policy_digest,
        )
        assert resumed.turn.reason == "continue_turn"
        archive = Archive(restored)
        healthy = inventory(conn, archive, source="eepro", source_ref="eepro:test", now=clock.now())
        assert healthy["enumeration_id"] == before["enumeration_id"]
        assert healthy["listed_pages"] == 3 and healthy["interpreted_pages"] == 1
        # Damage only the disposable restored copy, never checkpoint evidence.
        archive.extract_path(extract_sha).unlink()
        reopened = inventory(
            conn, archive, source="eepro", source_ref="eepro:test", now=clock.now()
        )
        assert reopened["interpreted_pages"] == 0 and reopened["acquired_pages"] == 1
        assert "extract_artifact_unavailable" in reopened["blockers"]
        for field in ("enumeration_id", "membership_digest", "first_known_at", "listed_pages"):
            assert reopened[field] == healthy[field]
        assert [(r["request_id"], r["first_known_at"]) for r in reopened["members"]] == [
            (r["request_id"], r["first_known_at"]) for r in healthy["members"]
        ]
        assert evidence_rows(conn) == expected  # No turn/debit refund or state rewrite.
        after_loss = next_watch(conn, reloaded, now=clock.now(), exclude=exclude, run_id=run)
        assert after_loss == following
    finally:
        conn.close()
    assert file_hashes(saved.path) == checkpoint_hashes
    assert not (saved.path / "state.sqlite-wal").exists()
    assert not (saved.path / "state.sqlite-shm").exists()
    assert (restored / "RESTORE_PENDING").exists()


def test_legacy_checkpoint_inventory_stays_unavailable_without_migration(tmp_path, monkeypatch):
    maximum_schema = state_db.SCHEMA_VERSION
    with monkeypatch.context() as legacy_runtime:
        legacy_runtime.setattr(state_db, "SCHEMA_VERSION", 15)
        with state_db.open_database(tmp_path / "legacy") as db:
            saved = create_checkpoint(
                db.state_dir,
                db.connection,
                tmp_path / "legacy-checkpoint",
                schema_version=15,
                versions={},
                input_bundle_hash=None,
            )
    restored = tmp_path / "legacy-restored"
    restore_checkpoint(saved.path, restored, maximum_schema_version=maximum_schema)
    before = sha256_file(restored / "state.sqlite")
    conn = sqlite3.connect((restored / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    conn.execute("BEGIN")
    try:
        for result in (
            inventory(
                conn,
                Archive(restored),
                source="eepro",
                source_ref="eepro:test",
                now=FakeClock().now(),
            ),
            report(conn, Archive(restored), now=FakeClock().now()),
        ):
            assert result["supported"] is False
            assert result["reason"] == "event_inventory_schema_unavailable"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN ('source_event_inventory','scheduler_event_turns')"
        ).fetchone()
    finally:
        conn.close()
    assert sha256_file(restored / "state.sqlite") == before
    assert not (restored / "state.sqlite-wal").exists()
    assert not (restored / "state.sqlite-shm").exists()
    assert (restored / "RESTORE_PENDING").exists()


def test_activated_restore_resumes_paid_turn_and_converges_with_uninterrupted_state(event):
    from swingset.backup.checkpoint import restore_from_checkpoint
    from swingset.fetch.politeness import Paused, Wait
    from swingset.project.contests import project_event
    from swingset.schedule import event_blocker_history as blockers
    from swingset.schedule import event_pressure as pressure
    from swingset.schedule.parse import parse_snapshot
    from swingset.state.control_scopes import for_unit
    from swingset.state.controls import ControlPaused, Selector, change_control, operation
    from swingset.state.work import WorkUnit

    f = event
    config = Config(
        {"eepro.com": HostConfig(min_gap_seconds=30, daily_request_budget=2)},
        {"eepro": SourceConfig(True)},
    )
    admit_parent(f, ["one.htm", "two.htm"])
    child(f, "one.htm")  # Establish the reviewed round contract before interruption.
    finish_bootstrap(f)
    prior_enumeration = view(f)["enumeration_id"]
    two = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        f.parent.url + "two.htm",
        "eepro.round",
        source_ref="eepro:test",
    )
    three = replace(two, url=f.parent.url + "three.htm", watch_id="")

    def issue_watch(branch, gate, watch, choice=None):
        with servicing(choice, run_id=branch.corpus.run) if choice else nullcontext():
            return issue(
                branch.db,
                gate,
                branch.corpus.clock,
                host="eepro.com",
                source="eepro",
                watch=watch,
                page_kind="eepro.round",
                crawl_delay=0,
                sweep=False,
            )

    def paid_rows(conn):
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in (
                "host_budget",
                "scheduler_requests",
                "scheduler_event_requests",
                "scheduler_capacity_requests",
                "scheduler_event_turns",
            )
        }

    exclude = {row[0] for row in f.conn.execute("SELECT watch_id FROM watches")} - {two.watch_id}
    with f.db.transaction():
        prepare_event_turns(
            f.conn, config, now=f.corpus.clock.now(), exclude=exclude, run_id=f.corpus.run
        )
    choice = next_watch(
        f.conn, config, now=f.corpus.clock.now(), exclude=exclude, run_id=f.corpus.run
    )
    assert choice and choice.turn and choice.key == two.watch_id
    gate = Gate(f.conn, config, f.corpus.clock)
    grant, action = issue_watch(f, gate, two, choice)
    assert isinstance(grant, Grant)
    release(
        f.db,
        gate,
        f.corpus.clock,
        action,
        "eepro.com",
        Classification(Outcome.OK),
        body_bytes=len(BODY),
        request_day=f.corpus.clock.now().date().isoformat(),
    )
    f.corpus.snapshot("pending-two", BODY, spec=two)  # Paid body retained; interpretation pending.
    admit_parent(f, ["one.htm", "two.htm", "three.htm"])
    assert view(f)["enumeration_id"] == prior_enumeration
    assert view(f)["listed_pages"] == 2
    assert tuple(
        f.conn.execute(
            "SELECT used,target_requests FROM scheduler_event_turns WHERE owner_key=?",
            (choice.turn.owner_key,),
        ).fetchone()
    ) == (1, 4)
    while f.conn.execute("SELECT 1 FROM event_pressure_dirty LIMIT 1").fetchone():
        pressure.bootstrap(f.db, config, now=f.corpus.clock.now())
    pressure.refresh(f.db, f.archive, config, now=f.corpus.clock.now())
    change_control(
        f.db.state_dir,
        selector=Selector("all", "all"),
        paused=True,
        actor="offline-test",
        reason="checkpoint interruption",
        now=f.corpus.clock.now(),
    )
    blockers.refresh(
        f.db, config, now=f.corpus.clock.now(), run_id=f.corpus.run, operator_hold=False
    )
    history_before = blockers.report(f.conn, source="eepro", source_ref="eepro:test")
    assert "operator_pause" in history_before["latest"]["facts"]["reasons"]
    paid_before = paid_rows(f.conn)
    epoch = f.conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0]
    hints = [
        tuple(row) for row in f.conn.execute("SELECT * FROM event_pressure_subjects ORDER BY rowid")
    ]
    saved = create_checkpoint(
        f.db.state_dir,
        f.conn,
        f.db.state_dir / "checkpoints" / "resume",
        schema_version=state_db.SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    checkpoint_hashes = file_hashes(saved.path)
    restored_path = f.db.state_dir / "activated-twin"
    # Real install/verification/activation, with an offline empty public head.
    heads = []
    hub = SimpleNamespace(head=lambda: heads.append(None))
    restore_from_checkpoint(
        saved.path, restored_path, hub, FakeClock(f.corpus.clock.now()), lock_timeout=1
    )
    assert len(heads) == 2 and not (restored_path / "RESTORE_PENDING").exists()

    def resume(branch):
        conn, clock, run = branch.conn, branch.corpus.clock, branch.corpus.run
        gate = Gate(conn, config, clock)
        pending = WorkUnit("parse", "snapshot", "pending-two")
        before = paid_rows(conn)
        assert next_watch(conn, config, now=clock.now(), run_id=run) is None
        assert issue_watch(branch, gate, two) == (Paused("operator"), None)
        with pytest.raises(ControlPaused):
            with operation(
                branch.db,
                action_id="paused-parse",
                action_kind="parse",
                scope=for_unit(conn, pending),
                clock=clock,
                run_id=run,
            ):
                pytest.fail("paused parse entered its body")
        assert paid_rows(conn) == before
        change_control(
            branch.db.state_dir,
            selector=Selector("all", "all"),
            paused=False,
            actor="offline-test",
            reason="resume disposable twin",
            now=clock.now(),
        )
        finish_bootstrap(branch)
        assert view(branch)["listed_pages"] == 3
        assert view(branch)["enumeration_id"] != prior_enumeration
        deferred, _ = issue_watch(branch, gate, three)
        assert isinstance(deferred, Wait)  # Restored cooldown is not reset.
        assert paid_rows(conn) == before
        with operation(
            branch.db,
            action_id="resume-parse-two",
            action_kind="parse",
            scope=for_unit(conn, pending),
            clock=clock,
            run_id=run,
        ):
            assert not parse_snapshot(branch.db, branch.archive, pending, clock, run).failed
        assert view(branch)["interpreted_pages"] == 2
        clock.sleep(deferred.seconds)
        excluded = {row[0] for row in conn.execute("SELECT watch_id FROM watches")} - {
            three.watch_id
        }
        with branch.db.transaction():
            prepare_event_turns(conn, config, now=clock.now(), exclude=excluded, run_id=run)
        selected = next_watch(conn, config, now=clock.now(), exclude=excluded, run_id=run)
        assert selected and selected.turn
        assert selected.turn.owner_key == choice.turn.owner_key
        assert selected.turn.policy_digest == choice.turn.policy_digest
        assert selected.turn.reason == "continue_turn"
        grant, action = issue_watch(branch, gate, three, selected)
        assert isinstance(grant, Grant)
        release(
            branch.db,
            gate,
            clock,
            action,
            "eepro.com",
            Classification(Outcome.OK),
            body_bytes=len(BODY),
            request_day=clock.now().date().isoformat(),
        )
        branch.corpus.snapshot("resumed-three", BODY, spec=three)
        unit = WorkUnit("parse", "snapshot", "resumed-three")
        with operation(
            branch.db,
            action_id="resume-parse-three",
            action_kind="parse",
            scope=for_unit(conn, unit),
            clock=clock,
            run_id=run,
        ):
            assert not parse_snapshot(branch.db, branch.archive, unit, clock, run).failed
        clock.sleep(30)
        paid = paid_rows(conn)
        assert issue_watch(branch, gate, three) == (Paused("request budget"), None)
        assert paid_rows(conn) == paid
        assert conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 2
        assert (
            conn.execute(
                "SELECT used FROM scheduler_event_turns WHERE owner_key=?", (choice.turn.owner_key,)
            ).fetchone()[0]
            == 2
        )
        for table in (
            "scheduler_requests",
            "scheduler_event_requests",
            "scheduler_capacity_requests",
        ):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 2
            assert paid_rows(conn)[table][0] == paid_before[table][0]
        while conn.execute("SELECT 1 FROM event_pressure_dirty LIMIT 1").fetchone():
            pressure.bootstrap(branch.db, config, now=clock.now())
        pressure.refresh(branch.db, branch.archive, config, now=clock.now())
        blockers.refresh(branch.db, config, now=clock.now(), run_id=run, operator_hold=False)
        observed = blockers.report(conn, source="eepro", source_ref="eepro:test")
        assert observed["recent"][-1] == history_before["latest"]
        assert "operator_pause" not in observed["latest"]["facts"]["reasons"]
        state = view(branch)
        assert state["listed_pages"] == state["acquired_pages"] == state["interpreted_pages"] == 3
        conn.execute(
            "INSERT INTO source_event_map VALUES ('eepro','eepro:test','offline-event','override',1)"
        )
        projected = project_event(conn, "offline-event", clock.now().isoformat(), run)
        assert projected.rows
        assert any(type(row).__name__ == "Placement" for row in projected.rows)
        return state, projected, [tuple(row) for row in conn.execute("SELECT * FROM host_budget")]

    with state_db.open_database(restored_path) as restored_db:
        restored_corpus = copy(f.corpus)
        restored_corpus.db, restored_corpus.conn = restored_db, restored_db.connection
        restored_corpus.archive = Archive(restored_path)
        restored_corpus.clock = FakeClock(f.corpus.clock.now())
        twin = SimpleNamespace(
            db=restored_db,
            conn=restored_db.connection,
            corpus=restored_corpus,
            archive=restored_corpus.archive,
            parent=f.parent,
        )
        assert paid_rows(twin.conn) == paid_before
        assert blockers.report(twin.conn, source="eepro", source_ref="eepro:test") == history_before
        assert (
            twin.conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0] == epoch + 1
        )
        assert [
            tuple(row)
            for row in twin.conn.execute("SELECT * FROM event_pressure_subjects ORDER BY rowid")
        ] == hints
        assert resume(twin) == resume(f)
        assert not twin.conn.execute("PRAGMA foreign_key_check").fetchall()
    assert file_hashes(saved.path) == checkpoint_hashes
