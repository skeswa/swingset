"""Pressure hints gate new event entry without granting acquisition authority."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_event_enumerations import admit_parent, child, finish_bootstrap, view
from test_event_enumerations import event as event

from swingset.backup.checkpoint import (
    activate_restored_state,
    create_checkpoint,
    restore_checkpoint,
    verify_restored_public,
)
from swingset.build.files import sha256_file
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.controls import issue
from swingset.fetch.politeness import Gate, Grant, Paused
from swingset.schedule import event_pressure as pressure
from swingset.schedule import event_pressure_refresh as worker
from swingset.schedule.fair_policy import SchedulerConfig
from swingset.schedule.fairness import next_delay, next_watch, prepare_event_turns
from swingset.schedule.watches import upsert_watch
from swingset.state.db import SCHEMA_VERSION


def config(**settings):
    return Config(
        {"eepro.com": HostConfig(), "other.test": HostConfig()},
        {"eepro": SourceConfig(True)},
        scheduler=SchedulerConfig(event_pressure_high=2, event_pressure_low=1, **settings),
    )


def index(f, name, *, host="eepro.com"):
    spec = replace(f.parent, url=f"https://{host}/results/{name}/", source_ref=f"eepro:{name}")
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    return spec


def enroll(f, policy):
    while f.conn.execute("SELECT 1 FROM event_pressure_dirty LIMIT 1").fetchone():
        pressure.bootstrap(f.db, policy, now=f.corpus.clock.now())


def gate(f, policy, host="eepro.com"):
    return pressure.admission(f.conn, policy, host=host, now=f.corpus.clock.now())


def refresh(f, policy):
    return pressure.refresh(f.db, f.archive, policy, now=f.corpus.clock.now())


def cohort(f, policy, count=3):
    parents = [index(f, f"started-{i}") for i in range(count)]
    for parent in parents:
        admit_parent(f, ["one.htm"], parent=parent)
    finish_bootstrap(f)
    enroll(f, policy)
    return parents


def settled(f, policy, names=("one.htm",)):
    admit_parent(f, names)
    for name in names:
        child(f, name)
    finish_bootstrap(f)
    enroll(f, policy)


def test_unfetched_indexes_never_create_initial_admission_deadlock(event):
    f, policy = event, config()
    indexes = [index(f, str(i)) for i in range(12)]
    assert not gate(f, policy)["deferred"]  # Pending unfetched index metadata is harmless.
    enroll(f, policy)
    assert not f.conn.execute("SELECT 1 FROM event_pressure_subjects").fetchone()
    assert (
        pressure.request_denial(
            f.conn, policy, watch_id=indexes[0].watch_id, host="eepro.com", now=f.corpus.clock.now()
        )
        is None
    )
    grant, _ = issue(
        f.db,
        Gate(f.conn, policy, f.corpus.clock),
        f.corpus.clock,
        host="eepro.com",
        source="eepro",
        watch=indexes[0],
        page_kind=indexes[0].parser,
        crawl_delay=0,
        sweep=False,
    )
    assert isinstance(grant, Grant)
    row = f.conn.execute("SELECT source_ref,start_basis FROM event_pressure_subjects").fetchone()
    assert tuple(row) == ("eepro:0", "issued_request")
    assert f.conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 1


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("publication", [False, True])
def test_restore_invalidates_observations_before_activation(
    event, monkeypatch, interrupted, publication
):
    from test_event_completion_restore import evidence_rows

    from swingset.state.controls import ActionScope, admission

    f, policy = event, config()
    settled(f, policy)
    refresh(f, policy)
    assert gate(f, policy)["states"] == {"locally_accounted": 1}
    if publication:
        with admission(
            f.db,
            action_id="interrupted-publication",
            action_kind="publication",
            scope=ActionScope(all_sources=True, all_kinds=True),
            now=f.corpus.clock.now(),
        ):
            pass
    durable_rows = evidence_rows(f.conn)
    saved = create_checkpoint(
        f.db.state_dir,
        f.conn,
        f.db.state_dir / "checkpoints" / "pressure",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    checkpoint_hash = sha256_file(saved.path / "state.sqlite")
    restored = f.db.state_dir / "restored"
    restore_checkpoint(saved.path, restored, maximum_schema_version=SCHEMA_VERSION)
    verify_restored_public(restored, SimpleNamespace(head=lambda: None))
    marker = restored / "RESTORE_PENDING"
    if interrupted:
        unlink = Path.unlink

        def fail_barrier_removal(path, *args, **kwargs):
            if path == marker:
                raise OSError("interrupted activation")
            return unlink(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", fail_barrier_removal)
            with pytest.raises(OSError, match="interrupted activation"):
                activate_restored_state(restored)
        assert marker.is_file()
    activate_restored_state(restored)
    assert not marker.exists()
    with sqlite3.connect(restored / "state.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        assert pressure.admission(conn, policy, host="eepro.com", now=f.corpus.clock.now())[
            "states"
        ] == {"changed": 1}
        restored_rows = evidence_rows(conn)
        if publication:
            assert tuple(
                conn.execute(
                    "SELECT state,outcome FROM execution_admissions WHERE action_id='interrupted-publication'"
                ).fetchone()
            ) == ("uncertain", "receipt_reconciliation_required")
            del restored_rows["execution_admissions"], durable_rows["execution_admissions"]
        assert restored_rows == durable_rows
    assert sha256_file(saved.path / "state.sqlite") == checkpoint_hash
    assert gate(f, policy)["states"] == {"locally_accounted": 1}


def test_pressure_keeps_continuations_and_borrowing_but_excludes_new_indexes(event):
    f, policy = event, config()
    started = cohort(f, policy)
    newcomer = index(f, "newcomer")
    continuation = replace(started[0], url=started[0].url + "page2/")
    upsert_watch(f.conn, continuation, f.corpus.clock.now())
    essential = replace(
        f.parent, url="https://eepro.com/platform/", parser="eepro.index", source_ref=None
    )
    upsert_watch(f.conn, essential, f.corpus.clock.now())
    # Block all listed results, leaving eligible discovery to borrow their share.
    f.conn.execute(
        "UPDATE watches SET paused_until='2099-01-01T00:00:00+00:00' WHERE parser='eepro.round'"
    )
    selected_ids = {newcomer.watch_id, continuation.watch_id, essential.watch_id}
    excluded = {row[0] for row in f.conn.execute("SELECT watch_id FROM watches")} - selected_ids
    with f.db.transaction() as conn:
        prepare_event_turns(conn, policy, now=f.corpus.clock.now(), exclude=excluded)
    assert gate(f, policy)["deferred"]
    chosen = next_watch(f.conn, policy, now=f.corpus.clock.now(), exclude=excluded)
    assert chosen.key in {continuation.watch_id, essential.watch_id}
    assert chosen.capacity.lane == "discovery" and not chosen.capacity.competing
    only_new = excluded | {continuation.watch_id, essential.watch_id}
    assert next_watch(f.conn, policy, now=f.corpus.clock.now(), exclude=only_new) is None
    assert next_delay(f.conn, policy, now=f.corpus.clock.now(), exclude=only_new) is None
    denied, action = issue(
        f.db,
        Gate(f.conn, policy, f.corpus.clock),
        f.corpus.clock,
        host="eepro.com",
        source="eepro",
        watch=newcomer,
        page_kind=newcomer.parser,
        crawl_delay=0,
        sweep=False,
    )
    assert isinstance(denied, Paused) and denied.reason == "event expansion pressure"
    assert action is None and not f.conn.execute("SELECT 1 FROM scheduler_requests").fetchone()


def test_unenrolled_retained_event_can_continue_while_new_admission_waits(event):
    f, policy = event, config()
    admit_parent(f, ["one.htm"])
    continuation = replace(f.parent, url=f.parent.url + "page2/")
    upsert_watch(f.conn, continuation, f.corpus.clock.now())
    newcomer = index(f, "newcomer")
    assert not f.conn.execute("SELECT 1 FROM event_pressure_subjects").fetchone()
    assert gate(f, policy)["metadata_pending"]
    assert (
        pressure.request_denial(
            f.conn,
            policy,
            watch_id=continuation.watch_id,
            host="eepro.com",
            now=f.corpus.clock.now(),
        )
        is None
    )
    assert (
        pressure.request_denial(
            f.conn, policy, watch_id=newcomer.watch_id, host="eepro.com", now=f.corpus.clock.now()
        )
        == "event pressure metadata pending"
    )
    enroll(f, policy)
    assert (
        pressure.request_denial(
            f.conn, policy, watch_id=newcomer.watch_id, host="eepro.com", now=f.corpus.clock.now()
        )
        is None
    )


def test_admitted_unfetched_index_alone_cannot_bypass_expansion(event):
    f, policy = event, config()
    cohort(f, policy)
    parent = replace(f.parent, source_ref=None)
    f.conn.execute("UPDATE watches SET source_ref=NULL WHERE watch_id=?", (parent.watch_id,))

    def index_only(result):
        return replace(
            result,
            observations=tuple(
                replace(o, scope=replace(o.scope, ref="eepro:new-index"))
                for o in result.observations
            ),
            watches=tuple(
                replace(w, source_ref="eepro:new-index", kind="autoindex", parser="eepro.autoindex")
                for w in result.watches
            ),
        )

    admit_parent(f, ["index.htm"], parent=parent, result_change=index_only)
    finish_bootstrap(f)
    enroll(f, policy)
    newcomer = f.conn.execute(
        "SELECT watch_id FROM watches WHERE source_ref='eepro:new-index'"
    ).fetchone()[0]
    assert f.conn.execute(
        "SELECT 1 FROM source_event_inventory WHERE source_ref='eepro:new-index' AND enumeration_id IS NOT NULL"
    ).fetchone()
    assert not f.conn.execute(
        "SELECT 1 FROM event_pressure_subjects WHERE source_ref='eepro:new-index'"
    ).fetchone()
    assert gate(f, policy)["deferred"]
    assert (
        pressure.request_denial(
            f.conn, policy, watch_id=newcomer, host="eepro.com", now=f.corpus.clock.now()
        )
        == "event expansion pressure"
    )


def test_partial_then_expired_scan_never_reduces_pressure(event):
    f, policy = event, config(event_pressure_probe_pages=1, event_pressure_max_age_seconds=1)
    settled(f, policy, ("one.htm", "two.htm"))
    assert refresh(f, policy)["partial"] == 1
    assert gate(f, policy)["states"] == {"unknown": 1}
    saved = json.loads(
        f.conn.execute("SELECT scan_json FROM event_pressure_subjects").fetchone()[0]
    )
    assert saved["checked"] == 1 and saved["next_cursor"]
    f.corpus.clock.sleep(2)
    assert refresh(f, policy)["partial"] == 0
    assert gate(f, policy)["states"] == {"stale": 1}
    observation = json.loads(
        f.conn.execute("SELECT observation_json FROM event_pressure_subjects").fetchone()[0]
    )
    assert observation["earliest_checked_at"] == saved["earliest_checked_at"]


@pytest.mark.parametrize(
    "change",
    [
        "snapshot",
        "admission",
        "revocation",
        "revocation_insert",
        "policy",
        "bundle",
        "host",
        "enumeration",
    ],
)
def test_changed_inputs_invalidate_even_when_enumeration_does_not_change(event, change):
    f, policy = event, config()
    settled(f, policy)
    refresh(f, policy)
    assert gate(f, policy)["states"] == {"locally_accounted": 1}
    original_enum = view(f)["enumeration_id"]
    if change == "snapshot":
        f.conn.execute(
            "UPDATE snapshots SET body_sha256=? WHERE snapshot_id='child-one.htm'", ("0" * 64,)
        )
    elif change == "admission":
        row = f.conn.execute(
            "SELECT generation_id,policy_revision FROM admission_decisions LIMIT 1"
        ).fetchone()
        f.conn.execute(
            "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,policy_revision) VALUES (?,'accepted','test',?,?)",
            (row[0], f.corpus.clock.now().isoformat(), row[1]),
        )
    elif change == "revocation":
        f.conn.execute(
            "UPDATE source_generations SET state='revoked' WHERE generation_id=(SELECT generation_id FROM source_generations LIMIT 1)"
        )
    elif change == "revocation_insert":
        row = dict(f.conn.execute("SELECT * FROM source_generations LIMIT 1").fetchone())
        row.update(generation_id="copied-revoked-evidence", state="revoked")
        f.conn.execute(
            f"INSERT INTO source_generations({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
            tuple(row.values()),
        )
    elif change == "policy":
        f.conn.execute("UPDATE admission_policies SET policy_revision='new-policy'")
    elif change == "bundle":
        f.conn.execute(
            "INSERT INTO meta VALUES ('input_bundle_hash','new-runtime') ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
    elif change == "host":
        f.conn.execute(
            "UPDATE watches SET archive_url='https://other.test/capture' WHERE url LIKE '%one.htm'"
        )
    else:
        admit_parent(f, ["one.htm", "two.htm"])
        finish_bootstrap(f)
    assert gate(f, policy)["pressure_subjects"] == 1
    if change != "enumeration":
        assert view(f)["enumeration_id"] == original_enum
    if change == "host":
        enroll(f, policy)
        assert gate(f, policy, "other.test")["pressure_subjects"] == 1


def test_mapping_and_publication_do_not_reopen_local_pressure(event):
    f, policy = event, config()
    settled(f, policy)
    refresh(f, policy)
    f.conn.execute(
        "INSERT INTO source_event_map VALUES ('eepro','eepro:test','canonical','test',1)"
    )
    f.conn.execute("INSERT INTO meta VALUES ('last_published_build','new-release')")
    assert gate(f, policy)["states"] == {"locally_accounted": 1}


def test_shared_parent_admission_invalidates_without_event_watch_membership(event):
    f, policy = event, config()
    parent = replace(f.parent, source_ref=None)
    f.conn.execute("UPDATE watches SET source_ref=NULL WHERE watch_id=?", (parent.watch_id,))

    def event_group(result):
        return replace(
            result,
            observations=tuple(
                replace(o, scope=replace(o.scope, ref="eepro:test")) for o in result.observations
            ),
            watches=tuple(replace(w, source_ref="eepro:test") for w in result.watches),
        )

    generation = admit_parent(f, ["one.htm"], parent=parent, result_change=event_group)
    child(f, "one.htm")
    finish_bootstrap(f)
    enroll(f, policy)
    refresh(f, policy)
    assert gate(f, policy)["states"] == {"locally_accounted": 1}
    assert not f.conn.execute(
        "SELECT 1 FROM event_pressure_watches WHERE watch_id=?", (parent.watch_id,)
    ).fetchone()
    assert f.conn.execute(
        "SELECT 1 FROM event_pressure_parents WHERE watch_id=?", (parent.watch_id,)
    ).fetchone()
    f.conn.execute(
        "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,policy_revision) VALUES (?,'accepted','new parent decision',?,'test')",
        (generation, f.corpus.clock.now().isoformat()),
    )
    assert gate(f, policy)["states"] == {"changed": 1}


def test_shared_result_exposes_each_event_on_each_actual_declared_host(event):
    f, policy = event, config()
    shared = "https://other.test/shared.htm"
    admit_parent(f, [shared])
    other = index(f, "other")
    admit_parent(f, [shared], parent=other)
    finish_bootstrap(f)
    enroll(f, policy)
    assert gate(f, policy, "other.test")["started_subjects"] == 2
    assert gate(f, policy)["started_subjects"] == 2


def test_request_failure_rolls_back_pressure_start_and_actual_debit(event, monkeypatch):
    f, policy = event, config()
    spec = index(f, "new")
    enroll(f, policy)
    original = pressure.record_started

    def failing(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("pressure receipt failed")

    monkeypatch.setattr(pressure, "record_started", failing)
    with pytest.raises(RuntimeError, match="pressure receipt failed"):
        issue(
            f.db,
            Gate(f.conn, policy, f.corpus.clock),
            f.corpus.clock,
            host="eepro.com",
            source="eepro",
            watch=spec,
            page_kind=spec.parser,
            crawl_delay=0,
            sweep=False,
        )
    for table in (
        "event_pressure_subjects",
        "scheduler_requests",
        "execution_admissions",
        "host_budget",
    ):
        assert not f.conn.execute(f"SELECT 1 FROM {table}").fetchone()


def test_probe_unknown_advances_and_observer_rotates_without_settling(event, monkeypatch):
    f, policy = event, config(event_pressure_probe_pages=1, event_pressure_refresh_events=1)
    cohort(f, policy)
    original = worker.probe
    visited = []

    def unknown(*args, **kwargs):
        batch = original(*args, **kwargs)
        visited.append(batch["enumeration_id"])
        return {
            **batch,
            "interpreted": 0,
            "unknown": batch["checked"],
            "reasons": {"candidate_budget": 1},
        }

    monkeypatch.setattr(worker, "probe", unknown)
    for _ in range(3):
        refresh(f, policy)
    assert len(set(visited)) == 3
    assert gate(f, policy)["states"] == {"unknown": 3}


def test_input_changes_between_probe_and_commit_discard_observation(event, monkeypatch):
    f, policy = event, config()
    settled(f, policy)
    original = worker.probe

    def concurrent(*args, **kwargs):
        batch = original(*args, **kwargs)
        with sqlite3.connect(f.db.state_dir / "state.sqlite") as writer:
            writer.execute(
                "UPDATE snapshots SET body_sha256=? WHERE snapshot_id='child-one.htm'", ("0" * 64,)
            )
        return batch

    monkeypatch.setattr(worker, "probe", concurrent)
    assert refresh(f, policy)["discarded"] == 1
    assert gate(f, policy)["states"] == {"unknown": 1}


def test_expired_manual_denial_then_middle_band_refresh_keeps_latch_across_restart(
    event, monkeypatch
):
    f, policy = event, config(event_pressure_max_age_seconds=10)
    cohort(f, policy)
    original = worker.probe

    def accounted(*args, **kwargs):
        batch = original(*args, **kwargs)
        return {
            **batch,
            "acquired": batch["checked"],
            "interpreted": batch["checked"],
            "unknown": 0,
            "parent_valid": True,
        }

    monkeypatch.setattr(worker, "probe", accounted)
    refresh(f, policy)
    assert not gate(f, policy)["deferred"]
    f.corpus.clock.sleep(11)
    newcomer = index(f, "manual-newcomer")
    denied, _ = issue(
        f.db,
        Gate(f.conn, policy, f.corpus.clock),
        f.corpus.clock,
        host="eepro.com",
        source="eepro",
        watch=newcomer,
        page_kind=newcomer.parser,
        crawl_delay=0,
        sweep=False,
    )
    assert isinstance(denied, Paused)
    # The denied admission rolled back. Refresh must latch the pre-refresh
    # pressure before a new observation moves the count into the middle band.
    assert f.conn.execute("SELECT deferred FROM event_pressure_latches").fetchone()[0] == 0
    pressure.refresh(f.db, f.archive, policy, now=f.corpus.clock.now(), max_events=1)
    assert gate(f, policy)["pressure_subjects"] == 2 and gate(f, policy)["deferred"]
    with sqlite3.connect(f.db.state_dir / "state.sqlite") as restarted:
        restarted.row_factory = sqlite3.Row
        restarted.execute("PRAGMA query_only=ON")
        assert pressure.admission(restarted, policy, host="eepro.com", now=f.corpus.clock.now())[
            "deferred"
        ]
    for _ in range(2):
        pressure.refresh(f.db, f.archive, policy, now=f.corpus.clock.now(), max_events=1)
    assert gate(f, policy)["pressure_subjects"] == 0 and not gate(f, policy)["deferred"]


def test_unknown_page_checkpoint_advances_to_later_members(event, monkeypatch):
    f, policy = event, config(event_pressure_probe_pages=1)
    settled(f, policy, ("one.htm", "two.htm"))
    original = worker.probe
    cursors = []

    def unknown(*args, **kwargs):
        batch = original(*args, **kwargs)
        cursors.append(batch["next_cursor"])
        return {**batch, "interpreted": 0, "unknown": 1, "reasons": {"manifest_budget": 1}}

    monkeypatch.setattr(worker, "probe", unknown)
    assert refresh(f, policy)["partial"] == 1
    assert refresh(f, policy)["partial"] == 0
    assert cursors[0] != cursors[1]
    observation = json.loads(
        f.conn.execute("SELECT observation_json FROM event_pressure_subjects").fetchone()[0]
    )
    assert observation["checked"] == observation["unknown"] == 2
    assert gate(f, policy)["states"] == {"unknown": 1}


def test_old_schema_is_read_only_unsupported_and_config_bounds_are_explicit(event):
    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA query_only=ON")
        assert pressure.report(conn, config(), now=event.corpus.clock.now())["supported"] is False
        assert (
            pressure.request_denial(
                conn, config(), watch_id="unknown", host="eepro.com", now=event.corpus.clock.now()
            )
            is None
        )
    for setting in (
        {"event_pressure_low": 100},
        {"event_pressure_probe_pages": 33},
        {"event_pressure_refresh_events": 101},
    ):
        with pytest.raises(ValueError):
            SchedulerConfig(**setting)
