"""Combined extension boundaries missing from otherwise separate module tests."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
from test_event_accounting_restore import activate, branch
from test_event_enumerations import event as event
from test_event_pressure import config, enroll
from test_event_publication import candidate, promote
from test_event_retirement import observe, retirement_view
from test_event_turns import add, debit, select
from test_event_turns import f as f
from test_event_unsupported import prepare, unsupported
from test_source_event_retirement import withdrawn

from swingset.build import event_coverage
from swingset.build.event_artifacts import artifact_source
from swingset.build.files import sha256_file
from swingset.build.schema import SCHEMAS
from swingset.fetch.controls import issue
from swingset.fetch.politeness import Gate, Paused, Wait
from swingset.schedule import event_publication
from swingset.schedule.event_history import report as history
from swingset.schedule.fairness import servicing
from swingset.state.controls import Selector, change_control
from swingset.state.db import open_database


def test_partial_turn_survives_crash_hold_and_daily_reset_without_extra_debits(f):
    f.config = replace(
        f.config,
        hosts={
            host: replace(value, daily_request_budget=1) for host, value in f.config.hosts.items()
        },
    )
    first, second = add(f, "older", 0), add(f, "older", 1)
    add(f, "other", 0)
    choice = select(f)
    assert choice.turn is not None
    debit(f, choice, finish=False)  # Crash after the durable admission, before settlement.
    conn = f.db.connection
    old_day = f.clock.now().date().isoformat()
    pending = [
        tuple(row)
        for row in conn.execute("SELECT watch_id,source_ref FROM watches ORDER BY watch_id")
    ]
    turn = tuple(
        conn.execute(
            "SELECT position,used,target_requests,policy_digest FROM scheduler_event_turns WHERE owner_key=?",
            (choice.turn.owner_key,),
        ).fetchone()
    )
    assert turn[1:3] == (1, 4)
    change_control(
        f.db.state_dir,
        selector=Selector("all", "all"),
        paused=True,
        actor="test",
        reason="restart held",
        now=f.clock.now(),
    )
    path = f.db.state_dir
    f.db.close()
    f.clock.sleep(86400)
    f.db = open_database(path)
    assert select(f) is None
    assert [
        tuple(row)
        for row in f.db.connection.execute(
            "SELECT watch_id,source_ref FROM watches ORDER BY watch_id"
        )
    ] == pending
    assert (
        tuple(
            f.db.connection.execute(
                "SELECT position,used,target_requests,policy_digest FROM scheduler_event_turns WHERE owner_key=?",
                (choice.turn.owner_key,),
            ).fetchone()
        )
        == turn
    )
    change_control(
        path,
        selector=Selector("all", "all"),
        paused=False,
        actor="test",
        reason="resume ordinary limits",
        now=f.clock.now(),
    )
    # Keep one obligation retry-blocked while its sibling can receive the next turn.
    f.db.connection.execute(
        "UPDATE watches SET paused_until=? WHERE watch_id=?",
        ((f.clock.now() + timedelta(seconds=30)).isoformat(), first.watch_id),
    )
    resumed = select(f)
    assert resumed is not None and resumed.key == second.watch_id
    assert resumed.turn.owner_key == choice.turn.owner_key
    # A lost worker's grant timestamp is not dispatch/completion evidence. The
    # resumed owner first waits the retained full host gap, without another debit.
    with servicing(resumed, run_id=f.run):
        waiting, action = issue(
            f.db,
            Gate(f.db.connection, f.config, f.clock),
            f.clock,
            host=resumed.host,
            source="wsdc_calendar",
            watch=SimpleNamespace(watch_id=resumed.key, kind="round"),
            page_kind="wsdc_calendar.events",
            crawl_delay=0,
            sweep=False,
        )
    assert waiting == Wait(5) and action is None
    assert dict(
        f.db.connection.execute("SELECT day,requests FROM host_budget WHERE host='example.test'")
    ) == {old_day: 1}
    assert (
        f.db.connection.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[0] == 1
    )
    f.clock.sleep(waiting.seconds)
    debit(f, resumed)
    new_day = f.clock.now().date().isoformat()
    assert dict(
        f.db.connection.execute("SELECT day,requests FROM host_budget WHERE host='example.test'")
    ) == {old_day: 1, new_day: 1}
    assert select(f) is None
    with servicing(resumed, run_id=f.run):
        blocked, action = issue(
            f.db,
            Gate(f.db.connection, f.config, f.clock),
            f.clock,
            host=resumed.host,
            source="wsdc_calendar",
            watch=SimpleNamespace(watch_id=resumed.key, kind="round"),
            page_kind="wsdc_calendar.events",
            crawl_delay=0,
            sweep=False,
        )
    assert blocked == Paused("request budget") and action is None
    assert (
        f.db.connection.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[0] == 2
    )
    assert not f.db.connection.execute("SELECT 1 FROM event_progress_receipts").fetchone()


def test_unsupported_unmapped_coverage_waits_for_exact_acknowledgment(event):
    f = event
    prepare(f)
    unsupported(f)
    assert not f.conn.execute("SELECT 1 FROM source_event_map").fetchone()
    with (
        f.db.transaction(immediate=False),
        artifact_source(f.conn, f.archive, clock=f.corpus.clock),
    ):
        witness = event_coverage.capture(
            f.conn, cutoff=f.corpus.clock.now().isoformat(), selected_support=()
        )
        event_coverage.validate(f.conn, witness, selected_support=())
    rows = event_coverage.rows(witness, selected_mapping=(), schemas=SCHEMAS)
    current = next(row for row in rows if row["scope_id"] == "eepro:test")
    assert current["listed_pages"] == current["acquired_pages"] == 2
    assert current["interpreted_pages"] == 1 and current["unsupported_pages"] == 1
    assert current["event_id"] is None and current["represented_pages"] == 0
    root = f.db.state_dir
    prior = candidate(root, "older-publication", 1)
    promote(root, prior)
    staged = candidate(root, "reviewed-gap-candidate", 0)
    table = staged / "data/coverage/coverage.parquet"
    pq.write_table(pa.Table.from_pylist(rows), table)
    manifest = staged / "_meta/manifest.json"
    value = json.loads(manifest.read_text())
    value["files"]["data/coverage/coverage.parquet"] = sha256_file(table)
    manifest.write_text(json.dumps(value))
    (staged / "BUILT").write_text(json.dumps({"manifest_hash": sha256_file(manifest)}))
    before = event_publication.report(root, source="eepro", source_ref="eepro:test")
    assert before["candidate_id"] == prior.name and before["published_pages"] == 1
    promote(root, staged)  # Explicit mocked acknowledgment, not a remote publish.
    after = event_publication.report(root, source="eepro", source_ref="eepro:test")
    assert after["candidate_id"] == staged.name
    published = next(row for row in after["rows"] if row["via"] == "unknown")
    assert published["enumeration_id"] == current["enumeration_id"]
    assert published["acquired_pages"] == 2 and published["unsupported_pages"] == 1
    assert published["event_id"] is None and published["represented_pages"] == 0


def test_activated_restore_retains_whole_event_history_but_requires_fresh_proof(event, tmp_path):
    f = event
    withdrawn(f)
    enroll(f, config())
    for _ in range(5):
        observe(f)
    assert retirement_view(f)["whole_event_retired"] is True
    before = history(
        f.conn, source="eepro", source_ref="eepro:test", stream="source_event_retirement"
    )
    assert len(before["rows"]) == 1
    target, _, _ = activate(f, tmp_path)
    with open_database(target) as database:
        restored = branch(f, database)
        recorded = history(
            restored.conn, source="eepro", source_ref="eepro:test", stream="source_event_retirement"
        )
        assert recorded["rows"] == before["rows"]
        assert retirement_view(restored)["whole_event_retired"] is None
        for _ in range(5):
            observe(restored)
        assert retirement_view(restored)["whole_event_retired"] is True
        assert (
            history(
                restored.conn,
                source="eepro",
                source_ref="eepro:test",
                stream="source_event_retirement",
            )["rows"]
            == before["rows"]
        )
        assert not restored.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()
