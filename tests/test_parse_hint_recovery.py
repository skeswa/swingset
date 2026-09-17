"""Lost hints cannot strand retained children or reset completed/blocked work."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_admission import BODY
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event

from swingset.admission.report import evaluate
from swingset.fetch.archive import Archive
from swingset.schedule.derive import derive_one
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.attempts import begin_attempt, finish_attempt, request_retry
from swingset.state.control_scopes import unit_allowed
from swingset.state.controls import Selector, change_control
from swingset.state.parse_recovery import CURSOR
from swingset.state.work import WorkUnit, next_work, recover_parse_hints


def ready(f, names=("one.htm",)):
    admit_parent(f, ["seed.htm", *names])
    child(f, "seed.htm")
    finish_bootstrap(f)


def pending(f, name="one.htm", *, identifier=None, body=BODY):
    spec = WatchSpec(
        "", "eepro", "round", "GET", f.parent.url + name, "eepro.round", source_ref="eepro:test"
    )
    context = f.corpus.snapshot(identifier or "pending-" + name, body, spec=spec)
    return context, WorkUnit("parse", "snapshot", context.snapshot_id)


def forget(f, unit):
    token = f.conn.execute(
        "SELECT enqueued_at FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
    ).fetchone()[0]
    f.conn.execute(
        "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
    )
    return token


def recover(f, **kwargs):
    return recover_parse_hints(f.db, now=f.corpus.clock.now(), **kwargs)


@pytest.mark.parametrize("parent_state", ["archived", "sealed"])
def test_retained_child_reconstructs_and_parses_under_normal_pause_gate(
    event, parent_state, monkeypatch
):
    f = event
    ready(f)
    _, unit = pending(f)
    original_token = forget(f, unit)
    f.conn.execute(
        "UPDATE watches SET state=?,next_check_at=NULL WHERE watch_id=?",
        (parent_state, f.parent.watch_id),
    )
    change_control(
        f.db.state_dir,
        selector=Selector("source", "eepro"),
        paused=True,
        actor="test",
        reason="offline hold",
        now=f.corpus.clock.now(),
    )
    before = list(map(tuple, f.conn.execute("SELECT * FROM watches ORDER BY watch_id")))
    with monkeypatch.context() as scoped:
        scoped.setattr(
            Archive, "read_body", Mock(side_effect=AssertionError("reconstruction read artifacts"))
        )
        scoped.setattr(
            Archive,
            "read_extract",
            Mock(side_effect=AssertionError("reconstruction read artifacts")),
        )
        result = recover(f)
    assert result["enqueued"] == 1
    assert (
        f.conn.execute(
            "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=?",
            (unit.unit_id,),
        ).fetchone()[0]
        == original_token
    )
    assert list(map(tuple, f.conn.execute("SELECT * FROM watches ORDER BY watch_id"))) == before
    assert (
        next_work(
            f.conn,
            "parse",
            now=f.corpus.clock.now(),
            allowed=lambda candidate: unit_allowed(f.conn, candidate, now=f.corpus.clock.now()),
        )
        is None
    )
    assert (
        derive_one(f.db, f.archive, unit, SimpleNamespace(), f.corpus.clock, f.corpus.run).reason
        == "operator_pause"
    )
    assert not f.conn.execute("SELECT 1 FROM work_attempts").fetchone()
    change_control(
        f.db.state_dir,
        selector=Selector("source", "eepro"),
        paused=False,
        actor="test",
        reason="resume offline work",
        now=f.corpus.clock.now(),
    )
    assert next_work(f.conn, "parse", now=f.corpus.clock.now()) == unit
    completed = derive_one(f.db, f.archive, unit, SimpleNamespace(), f.corpus.clock, f.corpus.run)
    assert not completed.failed and completed.reason is None
    assert (
        f.conn.execute(
            "SELECT parse_status FROM snapshots WHERE snapshot_id=?", (unit.unit_id,)
        ).fetchone()[0]
        == "ok"
    )
    assert recover(f)["enqueued"] == 0
    assert (
        f.conn.execute(
            "SELECT count(*) FROM work_attempts WHERE unit_id=?", (unit.unit_id,)
        ).fetchone()[0]
        == 1
    )
    assert not f.conn.execute("SELECT 1 FROM host_budget WHERE requests>0").fetchone()


def test_retry_deadline_and_original_attempt_token_survive_missing_hint(event):
    f = event
    ready(f)
    _, unit = pending(f)
    attempt = begin_attempt(f.db, unit, now=f.corpus.clock.now(), run_id=f.corpus.run)
    deadline = f.corpus.clock.now() + timedelta(minutes=5)
    finish_attempt(
        f.db,
        attempt,
        outcome="transient",
        reason_code="temporary_io",
        now=f.corpus.clock.now(),
        retry_at=deadline,
    )
    forget(f, unit)
    before = list(map(tuple, f.conn.execute("SELECT * FROM work_attempts")))
    generation = f.conn.execute(
        "SELECT retry_generation FROM work_generations WHERE unit_id=?", (unit.unit_id,)
    ).fetchone()[0]
    assert recover(f)["enqueued"] == 1
    assert next_work(f.conn, "parse", now=f.corpus.clock.now()) is None
    assert list(map(tuple, f.conn.execute("SELECT * FROM work_attempts"))) == before
    assert (
        f.conn.execute(
            "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=?",
            (unit.unit_id,),
        ).fetchone()[0]
        == attempt.work_token
    )
    assert (
        f.conn.execute(
            "SELECT retry_generation FROM work_generations WHERE unit_id=?", (unit.unit_id,)
        ).fetchone()[0]
        == generation
    )
    f.corpus.clock.sleep(300)
    assert next_work(f.conn, "parse", now=f.corpus.clock.now()) == unit


@pytest.mark.parametrize("outcome", ["blocked", "unavailable", "superseded", "succeeded"])
def test_unchanged_terminal_attempt_is_not_reopened(event, outcome):
    f = event
    ready(f)
    _, unit = pending(f)
    attempt = begin_attempt(f.db, unit, now=f.corpus.clock.now(), run_id=f.corpus.run)
    with f.db.transaction():
        finish_attempt(
            f.db, attempt, outcome=outcome, reason_code="retained_outcome", now=f.corpus.clock.now()
        )
    if outcome != "succeeded":
        forget(f, unit)
    before = list(map(tuple, f.conn.execute("SELECT * FROM work_attempts")))
    assert recover(f)["reasons"]["terminal_attempt"] == 1
    assert recover(f)["enqueued"] == 0
    assert list(map(tuple, f.conn.execute("SELECT * FROM work_attempts"))) == before
    assert next_work(f.conn, "parse", now=f.corpus.clock.now()) is None
    if outcome != "succeeded":
        request_retry(f.conn, unit, now=f.corpus.clock.now(), reason_code="explicit_offline_retry")
        assert recover(f)["enqueued"] == 1
        assert next_work(f.conn, "parse", now=f.corpus.clock.now()) == unit


def test_relevant_changed_fingerprint_reopens_own_terminal_attempt(event):
    f = event
    ready(f)
    _, unit = pending(f)
    attempt = begin_attempt(f.db, unit, now=f.corpus.clock.now(), run_id=f.corpus.run)
    finish_attempt(
        f.db, attempt, outcome="blocked", reason_code="retained_guard", now=f.corpus.clock.now()
    )
    forget(f, unit)
    f.conn.execute("INSERT INTO accepted_inputs VALUES('pipeline','recipe/runtime','changed')")
    assert recover(f)["enqueued"] == 1
    assert next_work(f.conn, "parse", now=f.corpus.clock.now()) == unit
    assert (
        f.conn.execute(
            "SELECT retry_generation FROM work_generations WHERE unit_id=?", (unit.unit_id,)
        ).fetchone()[0]
        == 0
    )


def test_same_input_guard_terminal_admission_without_work_attempt_is_not_reparsed(event):
    f = event
    ready(f)
    context, unit = pending(f)
    generation, _ = f.corpus.stage(
        context,
        report_change=lambda report: evaluate(
            report.page_kind,
            report.contract_version,
            report.fields,
            replace(report.coverage, expected_pages=(*report.coverage.expected_pages, "missing")),
        ),
    )
    assert f.corpus.admit(generation) == "waiting_for_inputs"
    assert not f.conn.execute("SELECT 1 FROM work_attempts").fetchone()
    # The direct admission guard removed its parse hint, while transport status
    # remains pending/NULL; that is a terminal decision, not unstarted work.
    assert f.conn.execute(
        "SELECT parse_status FROM snapshots WHERE snapshot_id=?", (unit.unit_id,)
    ).fetchone()[0] in (None, "pending")
    assert recover(f)["reasons"]["terminal_admission"] == 1
    assert recover(f)["enqueued"] == 0


def test_accepted_own_unit_receipt_suppresses_pending_transport_hint(event):
    f = event
    ready(f)
    context, unit = pending(f)
    generation, _ = f.corpus.stage(context)
    assert f.corpus.admit(generation) == "accepted"
    # Reproduce old retained transport metadata without deleting the accepted receipt.
    f.conn.execute(
        "UPDATE snapshots SET parse_status='pending' WHERE snapshot_id=?", (unit.unit_id,)
    )
    assert recover(f)["reasons"]["terminal_admission"] == 1
    assert recover(f)["enqueued"] == 0


def test_superseded_snapshot_is_not_revived_and_existing_hint_is_unchanged(event):
    f = event
    ready(f)
    _, old = pending(f, identifier="old")
    forget(f, old)
    _, newest = pending(f, identifier="new")
    token = f.conn.execute(
        "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id='new'"
    ).fetchone()[0]
    generation = f.conn.execute(
        "SELECT generation FROM work_generations WHERE unit_id='new'"
    ).fetchone()[0]
    result = recover(f)
    assert result["enqueued"] == 0 and result["reasons"]["superseded_snapshot"] == 1
    assert (
        f.conn.execute(
            "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id='new'"
        ).fetchone()[0]
        == token
    )
    assert (
        f.conn.execute("SELECT generation FROM work_generations WHERE unit_id='new'").fetchone()[0]
        == generation
    )
    assert next_work(f.conn, "parse", now=f.corpus.clock.now()) == newest


def test_other_unit_aggregate_is_not_a_completion_receipt_for_this_snapshot(event):
    from tests.build.test_event_page_evidence import aggregate

    f = event
    contexts, aggregate_generation = aggregate(f)
    finish_bootstrap(f)
    unit = WorkUnit("parse", "snapshot", contexts[1].snapshot_id)
    forget(f, unit)
    before = f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0]
    assert recover(f)["enqueued"] == 1
    assert f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0] == before
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()
    result = derive_one(f.db, f.archive, unit, SimpleNamespace(), f.corpus.clock, f.corpus.run)
    assert not result.failed and result.reason is None
    assert recover(f)["enqueued"] == 0
    assert (
        f.conn.execute(
            "SELECT state FROM source_generations WHERE generation_id=?", (aggregate_generation,)
        ).fetchone()[0]
        == "accepted"
    )


def test_bounded_pass_finishes_old_candidates_before_continuous_arrivals(event):
    f = event
    ready(f, [f"{number}.htm" for number in range(5)])
    original = []
    for number in range(3):
        _, unit = pending(f, f"{number}.htm")
        forget(f, unit)
        original.append(unit)
    first = recover(f, limit=1)
    assert first["scanned"] == first["enqueued"] == 1 and not first["pass_complete"]
    high = json.loads(
        f.conn.execute("SELECT value FROM meta WHERE key=?", (CURSOR,)).fetchone()[0]
    )["high"]
    for number in range(3, 5):
        _, unit = pending(f, f"{number}.htm")
        forget(f, unit)
        result = recover(f, limit=1)
    assert result["pass_complete"]
    for unit in original:
        assert f.conn.execute(
            "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?", (unit.unit_id,)
        ).fetchone()
    assert (
        json.loads(f.conn.execute("SELECT value FROM meta WHERE key=?", (CURSOR,)).fetchone()[0])[
            "high"
        ]
        == high
    )
    assert recover(f)["enqueued"] == 2


def test_undeclared_normalized_alias_and_failed_snapshots_remain_out_of_scope(event):
    f = event
    ready(f)
    _, failed = pending(f)
    forget(f, failed)
    f.conn.execute(
        "UPDATE snapshots SET parse_status='failed' WHERE snapshot_id=?", (failed.unit_id,)
    )
    alias = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        "https://EEPRO.COM:443/results/test/one.htm#alias",
        "eepro.round",
        source_ref="eepro:test",
    )
    upsert_watch(f.conn, alias, f.corpus.clock.now())
    context = f.corpus.snapshot("alias", spec=alias)
    forget(f, WorkUnit("parse", "snapshot", context.snapshot_id))
    assert recover(f)["enqueued"] == 0


def test_oversized_own_unit_receipt_is_unassessed_without_requeue(event):
    f = event
    ready(f)
    context, unit = pending(f)
    generation, _ = f.corpus.stage(context)
    forget(f, unit)
    f.conn.execute("DROP TRIGGER source_generation_immutable")
    f.conn.execute(
        "UPDATE source_generations SET recipe_json=? WHERE generation_id=?",
        ("x" * 70000, generation),
    )
    result = recover(f)
    assert result["unassessed"] == 1 and result["enqueued"] == 0


@pytest.mark.parametrize(
    "table,column",
    [
        ("watches", "kind"),
        ("watches", "url"),
        ("watches", "archive_url"),
        ("snapshots", "via"),
        ("snapshots", "archive_url"),
        ("snapshots", "captured_at"),
        ("snapshots", "body_bytes"),
    ],
)
def test_shared_helper_scalars_are_bounded_before_loading(event, monkeypatch, table, column):
    from swingset.state import parse_recovery

    f = event
    ready(f)
    context, unit = pending(f)
    forget(f, unit)
    key = "watch_id" if table == "watches" else "snapshot_id"
    value = context.watch_id if table == "watches" else context.snapshot_id
    f.conn.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", ("x" * 70000, value))
    monkeypatch.setattr(
        parse_recovery, "selected_snapshot", Mock(side_effect=AssertionError("unguarded helper"))
    )
    result = recover(f)
    assert result["reasons"] == {"retained_metadata_oversized": 1}
    assert result["enqueued"] == 0


def test_accepted_inputs_have_a_total_metadata_cap(event, monkeypatch):
    from swingset.state import parse_recovery

    f = event
    ready(f)
    _, unit = pending(f)
    forget(f, unit)
    f.conn.executemany(
        "INSERT INTO accepted_inputs VALUES(?,?,?)",
        [("bulk", str(i), "x" * 1000) for i in range(100)],
    )
    monkeypatch.setattr(
        parse_recovery, "begin_attempt", Mock(side_effect=AssertionError("unguarded helper"))
    )
    result = recover(f)
    assert result["reasons"] == {"accepted_input_metadata_budget": 1}
    assert result["enqueued"] == 0


def test_shared_budget_does_not_skip_later_unfinished_candidates(event, monkeypatch):
    from swingset.state import parse_recovery

    f = event
    ready(f, ["one.htm", "two.htm", "three.htm"])
    units = []
    for name in ("one.htm", "two.htm", "three.htm"):
        _, unit = pending(f, name)
        forget(f, unit)
        units.append(unit)
    original = parse_recovery._recover

    def cost(conn, budget, rowid):
        budget.charge(60000)
        return original(conn, budget, rowid)

    monkeypatch.setattr(parse_recovery, "TOTAL_METADATA_BYTES", 100000)
    monkeypatch.setattr(parse_recovery, "_recover", cost)
    for index in range(3):
        result = recover(f)
        assert result["enqueued"] == 1
        assert f.conn.execute(
            "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?", (units[index].unit_id,)
        ).fetchone()
        cursor = json.loads(
            f.conn.execute("SELECT value FROM meta WHERE key=?", (CURSOR,)).fetchone()[0]
        )
        if index < 2:
            assert result["reasons"]["metadata_byte_budget"] == 1
            assert (
                cursor["last"]
                == f.conn.execute(
                    "SELECT rowid FROM snapshots WHERE snapshot_id=?", (units[index].unit_id,)
                ).fetchone()[0]
            )
    assert result["pass_complete"]


def test_hint_and_cursor_reconstruction_roll_back_together(event):
    f = event
    ready(f)
    _, unit = pending(f)
    forget(f, unit)
    f.conn.execute(
        "CREATE TRIGGER fail_recovery_cursor BEFORE INSERT ON meta WHEN NEW.key='event_parse_recovery_cursor' BEGIN SELECT RAISE(ABORT,'interrupted recovery'); END"
    )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError, match="interrupted recovery"):
        recover(f)
    assert not f.conn.execute(
        "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?", (unit.unit_id,)
    ).fetchone()
    assert not f.conn.execute("SELECT 1 FROM meta WHERE key=?", (CURSOR,)).fetchone()


def test_elapsed_budget_stops_before_unattempted_candidate(event, monkeypatch):
    from swingset.state import parse_recovery

    f = event
    ready(f, ["one.htm", "two.htm"])
    for name in ("one.htm", "two.htm"):
        _, unit = pending(f, name)
        forget(f, unit)
    original = parse_recovery._recover
    ticks = [0.0]
    monkeypatch.setattr(parse_recovery.time, "monotonic", lambda: ticks[0])

    def cost(conn, budget, rowid):
        result = original(conn, budget, rowid)
        ticks[0] += 3.0
        return result

    monkeypatch.setattr(parse_recovery, "_recover", cost)
    first = recover(f)
    assert first["scanned"] == first["enqueued"] == 1
    assert first["reasons"]["metadata_time_budget"] == 1
    assert not first["pass_complete"]
    assert recover(f)["enqueued"] == 1


def test_uninterrupted_active_attempt_stays_unassessed(event):
    f = event
    ready(f)
    _, unit = pending(f)
    attempt = begin_attempt(f.db, unit, now=f.corpus.clock.now(), run_id=f.corpus.run)
    forget(f, unit)
    result = recover(f)
    assert result["reasons"] == {"active_attempt_requires_normal_interruption_recovery": 1}
    assert (
        f.conn.execute(
            "SELECT outcome FROM work_attempts WHERE attempt_id=?", (attempt.attempt_id,)
        ).fetchone()[0]
        == "running"
    )
    assert result["enqueued"] == 0


@pytest.mark.parametrize("explicit_retry", [False, True])
def test_real_parse_failure_requires_new_explicit_retry_to_rebuild_lost_hint(
    event, monkeypatch, explicit_retry
):
    from swingset.sources.base import ExtractError
    from swingset.sources.eepro.adapter import RoundPage

    f = event
    ready(f)
    _, unit = pending(f)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            RoundPage, "extract", Mock(side_effect=ExtractError("retained failed extraction"))
        )
        result = derive_one(f.db, f.archive, unit, SimpleNamespace(), f.corpus.clock, f.corpus.run)
    assert result.failed and result.reason == "parse_failed"
    assert (
        f.conn.execute(
            "SELECT parse_status FROM snapshots WHERE snapshot_id=?", (unit.unit_id,)
        ).fetchone()[0]
        == "failed"
    )
    assert next_work(f.conn, "parse", now=f.corpus.clock.now()) is None
    if explicit_retry:
        request_retry(f.conn, unit, now=f.corpus.clock.now(), reason_code="operator_retry")
    token = forget(f, unit)
    attempts = list(map(tuple, f.conn.execute("SELECT * FROM work_attempts")))
    retry = tuple(
        f.conn.execute(
            "SELECT retry_generation,retry_reason,retry_requested_at FROM work_generations WHERE unit_id=?",
            (unit.unit_id,),
        ).fetchone()
    )
    result = recover(f)
    assert result["enqueued"] == int(explicit_retry)
    assert list(map(tuple, f.conn.execute("SELECT * FROM work_attempts"))) == attempts
    assert (
        tuple(
            f.conn.execute(
                "SELECT retry_generation,retry_reason,retry_requested_at FROM work_generations WHERE unit_id=?",
                (unit.unit_id,),
            ).fetchone()
        )
        == retry
    )
    if explicit_retry:
        assert (
            f.conn.execute(
                "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=?",
                (unit.unit_id,),
            ).fetchone()[0]
            == token
        )
        assert next_work(f.conn, "parse", now=f.corpus.clock.now()) == unit
        completed = derive_one(
            f.db, f.archive, unit, SimpleNamespace(), f.corpus.clock, f.corpus.run
        )
        assert not completed.failed and completed.reason is None
        assert (
            f.conn.execute(
                "SELECT parse_status FROM snapshots WHERE snapshot_id=?", (unit.unit_id,)
            ).fetchone()[0]
            == "ok"
        )
        assert recover(f)["enqueued"] == 0
    else:
        assert next_work(f.conn, "parse", now=f.corpus.clock.now()) is None


def test_oversized_integer_cursor_restarts_without_sql_binding_overflow(event):
    f = event
    ready(f)
    _, unit = pending(f)
    forget(f, unit)
    f.conn.execute(
        "INSERT INTO meta VALUES(?,?)", (CURSOR, json.dumps({"last": 1, "high": 10**40}))
    )
    assert recover(f)["enqueued"] == 1
    cursor = json.loads(
        f.conn.execute("SELECT value FROM meta WHERE key=?", (CURSOR,)).fetchone()[0]
    )
    assert cursor["high"] < 2**63
