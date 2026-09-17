"""Successful outputs and observed advancement have distinct durable receipts."""

import gzip
import shutil
import sqlite3
from dataclasses import replace

import httpx
import pytest
from test_admission import BODY
from test_cycle import overrides as overrides
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event
from test_event_pressure import config, enroll, index

from swingset.admission.page_evidence import Limits
from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint
from swingset.fetch.client import FetchClient
from swingset.schedule import event_progress as progress
from swingset.schedule.cycle import run_cycle
from swingset.schedule.event_progress_report import report
from swingset.schedule.parse import parse_snapshot
from swingset.sources import get_page_kind
from swingset.state import event_progress as facts
from swingset.state.controls import Selector, change_control
from swingset.state.db import SCHEMA_VERSION
from swingset.state.work import WorkUnit


def ready(f, names=("one.htm",)):
    # Activate the real round contract before establishing missing baselines.
    admit_parent(f, ["seed.htm", *names])
    child(f, "seed.htm")
    finish_bootstrap(f)
    enroll(f, config())


def observe(f, **kwargs):
    return progress.refresh(
        f.db, f.archive, config(), now=f.corpus.clock.now(), run_id=f.corpus.run, **kwargs
    )


def history(f):
    return report(
        f.conn, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now(), config=config()
    )


def fetch(f, name="one.htm", *, body=BODY, status=200):
    watch = f.conn.execute("SELECT * FROM watches WHERE url=?", (f.parent.url + name,)).fetchone()
    client = FetchClient(
        f.conn,
        config(),
        f.corpus.clock,
        f.archive,
        transport=httpx.MockTransport(
            lambda req: (
                httpx.Response(200, text="User-agent: *\nAllow: /\n")
                if req.url.path == "/robots.txt"
                else httpx.Response(status, content=body)
            )
        ),
        random_value=lambda: 0,
    )
    try:
        return client.fetch(watch["watch_id"], get_page_kind(watch["parser"]), f.corpus.run)
    finally:
        client.close()


def interpret(f, snapshot):
    result = parse_snapshot(
        f.db, f.archive, WorkUnit("parse", "snapshot", snapshot), f.corpus.clock, f.corpus.run
    )
    assert not result.failed and result.selection is None


def test_actual_fetch_and_admission_qualify_once_with_unknown_legacy_times(event):
    f = event
    ready(f)
    assert observe(f)["qualified_progress"] == 0
    assert history(f)["last_observed_progress_at"] is None
    result = fetch(f)
    assert result.snapshot_id
    assert observe(f)["qualified_progress"] == 1
    interpret(f, result.snapshot_id)
    assert observe(f)["qualified_progress"] == 1
    h = history(f)
    assert {r["stage"] for r in h["progress"]} == {"acquired", "interpreted"}
    assert all(r["request_id"] == h["progress"][0]["request_id"] for r in h["progress"])
    assert h["eligible_service_age_seconds"] is None
    assert h["incomplete_observation_coverage"] and not h["current_stage_authority"]
    assert observe(f)["qualified_progress"] == 0
    before = f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0]
    same = fetch(f)
    assert same.snapshot_id is None and not same.changed
    assert f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0] == before
    assert observe(f)["qualified_progress"] == 0


def test_failed_fetch_does_not_record_success(event):
    f = event
    ready(f)
    observe(f)
    before = f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0]
    fetch(f, status=404, body=b"missing")
    assert f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0] == before
    assert observe(f)["qualified_progress"] == 0


def test_fetch_success_receipt_rolls_back_with_snapshot(event, monkeypatch):
    f = event
    ready(f)
    before = f.conn.execute("SELECT count(*) FROM snapshots").fetchone()[0]
    original = facts.acquired

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after receipt")

    monkeypatch.setattr(facts, "acquired", fail)
    with pytest.raises(RuntimeError, match="after receipt"):
        fetch(f)
    assert f.conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == before
    assert not f.conn.execute(
        "SELECT 1 FROM event_stage_operations WHERE stage='acquired'"
    ).fetchone()
    # Requests were really issued and are not refunded by an output failure.
    assert f.conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] >= 1


def test_accepted_decision_receipt_rolls_back_with_output(event, monkeypatch):
    f = event
    ready(f)
    result = fetch(f)
    original = facts.interpreted
    before = f.conn.execute("SELECT count(*) FROM admission_decisions").fetchone()[0]

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after interpretation receipt")

    monkeypatch.setattr(facts, "interpreted", fail)
    with pytest.raises(RuntimeError, match="after interpretation receipt"):
        interpret(f, result.snapshot_id)
    assert f.conn.execute("SELECT count(*) FROM admission_decisions").fetchone()[0] == before
    assert not f.conn.execute(
        "SELECT 1 FROM event_stage_operations WHERE generation_id IN "
        "(SELECT generation_id FROM source_generations WHERE manifest_json LIKE ?)",
        ("%" + result.snapshot_id + "%",),
    ).fetchone()


def test_loss_and_restoration_reopen_without_inventing_progress(event):
    f = event
    ready(f)
    observe(f)
    result = fetch(f)
    interpret(f, result.snapshot_id)
    assert observe(f)["qualified_progress"] == 2
    sha = f.conn.execute(
        "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (result.snapshot_id,)
    ).fetchone()[0]
    path = f.archive.blob_path(sha)
    original = path.read_bytes()
    path.write_bytes(gzip.compress(b"damaged"))
    assert observe(f)["qualified_progress"] == 0
    h = history(f)
    missing = [r for r in h["observations"] if r["availability_when_observed"] == 0]
    assert len(missing) >= 2 and len(h["progress"]) == 2
    path.write_bytes(original)
    assert observe(f)["qualified_progress"] == 0
    assert len(history(f)["progress"]) == 2
    path.unlink()
    observe(f)
    # A genuinely new retained response, unlike restoring the same file, may qualify.
    again = fetch(f, body=BODY.replace(b"Alice Example", b"Alicia Example"))
    assert again.snapshot_id and observe(f)["qualified_progress"] == 1


def test_new_membership_and_denominator_shrink_are_not_progress(event):
    f = event
    ready(f, ("one.htm", "removed.htm"))
    observe(f)
    result = fetch(f)
    interpret(f, result.snapshot_id)
    admit_parent(f, ["one.htm", "new.htm"], authority=True)
    finish_bootstrap(f)
    enroll(f, config())
    assert observe(f)["qualified_progress"] == 0
    assert not history(f)["progress"]
    retired = [r for r in history(f)["observations"] if not r["currently_listed"]]
    assert retired


def test_revocation_reopens_without_erasing_receipts(event):
    f = event
    ready(f)
    observe(f)
    result = fetch(f)
    interpret(f, result.snapshot_id)
    assert observe(f)["qualified_progress"] == 2
    generation = next(
        r["generation_id"] for r in history(f)["progress"] if r["stage"] == "interpreted"
    )
    f.conn.execute(
        "UPDATE source_generations SET state='revoked' WHERE generation_id=?", (generation,)
    )
    assert observe(f)["qualified_progress"] == 0
    assert len(history(f)["progress"]) == 2
    assert any(
        r["stage"] == "interpreted" and r["availability_when_observed"] == 0
        for r in history(f)["observations"]
    )


def test_concurrent_token_change_discards_verified_results(event, monkeypatch):
    f = event
    ready(f)
    original = progress.Session.verify_request
    changed = False

    def changing(session, request, **kwargs):
        nonlocal changed
        value = original(session, request, **kwargs)
        if not changed:
            with sqlite3.connect(f.db.state_dir / "state.sqlite") as other:
                other.execute("UPDATE event_pressure_state SET epoch=epoch+1")
            changed = True
        return value

    monkeypatch.setattr(progress.Session, "verify_request", changing)
    result = observe(f)
    assert result["discarded_events"] == 1
    assert not f.conn.execute("SELECT 1 FROM event_progress_observations").fetchone()


def test_one_shared_session_budget_yields_unknown_and_roundrobin_continues(event, monkeypatch):
    f = event
    ready(f, [f"{i}.htm" for i in range(40)])
    instances = []
    original = progress.Session

    def session(*args, **kwargs):
        value = original(*args, **kwargs)
        instances.append(value)
        return value

    monkeypatch.setattr(progress, "Session", session)
    first = observe(f, limits=Limits(rows=100))
    assert len(instances) == 1 and 0 < first["checked_pages"] <= 32 and first["unknown_pages"] >= 1
    assert f.conn.execute("SELECT after_request_id FROM event_progress_scans").fetchone()[0]
    for _ in range(20):
        observe(f, limits=Limits(rows=100))
        if (
            f.conn.execute("SELECT after_request_id FROM event_progress_scans").fetchone()[0]
            is None
        ):
            break
    assert f.conn.execute("SELECT after_request_id FROM event_progress_scans").fetchone()[0] is None
    assert (
        f.conn.execute(
            "SELECT count(DISTINCT request_id) FROM event_progress_observations"
        ).fetchone()[0]
        == 41
    )
    assert not history(f)["progress"]


def test_readonly_reporting_and_expired_observation(event):
    f = event
    ready(f)
    observe(f)
    f.corpus.clock.sleep(86401)
    before = f.conn.total_changes
    f.conn.execute("PRAGMA query_only=ON")
    try:
        assert all(not item["fresh"] for item in history(f)["observations"])
        assert f.conn.total_changes == before
    finally:
        f.conn.execute("PRAGMA query_only=OFF")


def test_shared_request_has_one_operation_and_progress_for_each_event(event):
    f = event
    ready(f)
    other = replace(f.parent, url="https://eepro.com/results/other/", source_ref="eepro:other")
    from swingset.schedule.watches import upsert_watch

    upsert_watch(f.conn, other, f.corpus.clock.now())
    admit_parent(f, [f.parent.url + "one.htm"], parent=other)
    finish_bootstrap(f)
    enroll(f, config())
    observe(f)
    fetched = fetch(f)
    assert observe(f)["qualified_progress"] == 2
    operations = f.conn.execute(
        "SELECT operation_id FROM event_stage_operations WHERE stage='acquired'"
    ).fetchall()
    assert len(operations) == 1
    assert (
        f.conn.execute(
            "SELECT count(*) FROM event_progress_receipts WHERE operation_id=?", operations[0]
        ).fetchone()[0]
        == 2
    )
    assert fetched.snapshot_id


def test_aggregate_admission_qualifies_non_anchor_member(event, monkeypatch):
    from tests.build.test_event_page_evidence import aggregate

    f = event
    original = f.corpus.admit

    def before_admission(generation, **kwargs):
        kind = f.conn.execute(
            "SELECT page_kind FROM source_generations WHERE generation_id=?", (generation,)
        ).fetchone()[0]
        if kind == "eepro.round":
            finish_bootstrap(f)
            enroll(f, config())
            assert observe(f)["qualified_progress"] == 0
        return original(generation, **kwargs)

    monkeypatch.setattr(f.corpus, "admit", before_admission)
    contexts, generation = aggregate(f)
    result = observe(f)
    assert result["qualified_progress"] == 2
    receipts = history(f)["progress"]
    assert {r["stage"] for r in receipts} == {"interpreted"}
    assert {r["generation_id"] for r in receipts} == {generation}
    assert len({r["request_id"] for r in receipts}) == 2
    assert not f.conn.execute(
        "SELECT 1 FROM source_units WHERE watch_id=?", (contexts[1].watch_id,)
    ).fetchone()


def test_checkpoint_preserves_facts_baselines_and_progress(event, tmp_path):
    f = event
    ready(f)
    observe(f)
    fetched = fetch(f)
    assert observe(f)["qualified_progress"] == 1
    tables = (
        "event_stage_operations",
        "event_progress_observations",
        "event_progress_receipts",
        "event_progress_scans",
        "event_progress_cursor",
        "event_progress_policies",
    )
    expected = {
        table: list(map(tuple, f.conn.execute("SELECT * FROM " + table))) for table in tables
    }
    saved = create_checkpoint(
        f.db.state_dir,
        f.conn,
        tmp_path / "saved",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    target = tmp_path / "restored"
    restore_checkpoint(saved.path, target, maximum_schema_version=SCHEMA_VERSION)
    with sqlite3.connect((target / "state.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        assert {
            table: list(map(tuple, conn.execute("SELECT * FROM " + table))) for table in tables
        } == expected
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        value = report(conn, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now())
        assert value["progress"][0]["snapshot_id"] == fetched.snapshot_id


def test_paused_cycle_does_not_verify_artifacts_or_record_observed_progress(
    event, overrides, tmp_path
):
    f = event
    admit_parent(f, ["one.htm"])
    config_dir = tmp_path / "config"
    shutil.copytree("config", config_dir)
    (config_dir / "sources.toml").write_text("[sources.wsdc_calendar]\nenabled=false\n")
    change_control(
        f.db.state_dir,
        selector=Selector("all", "all"),
        paused=True,
        actor="test",
        reason="maintenance",
        now=f.corpus.clock.now(),
    )
    result = run_cycle(
        f.db,
        config_dir=config_dir,
        overrides_dir=overrides,
        clock=f.corpus.clock,
        budget=1,
        transport=httpx.MockTransport(lambda _: pytest.fail("paused progress fetched")),
    )
    assert not result["failed"]
    assert any(row["action"] == "event_progress_refresh" for row in result["held_operations"])
    assert not f.conn.execute("SELECT 1 FROM event_progress_observations").fetchone()


def test_unknown_schema_and_missing_initial_baseline_never_create_progress(event):
    f = event
    ready(f)
    fetched = fetch(f)
    interpret(f, fetched.snapshot_id)
    assert observe(f)["qualified_progress"] == 0
    assert history(f)["last_observed_progress_at"] is None
    assert history(f)["last_recorded_operation_at"] is not None
    with sqlite3.connect(":memory:") as conn:
        value = report(conn, source="eepro", source_ref="legacy", now=f.corpus.clock.now())
        assert not value["supported"] and value["last_observed_progress_at"] is None


def test_refresh_failure_rolls_back_receipts_observations_and_cursor(event):
    f = event
    ready(f)
    observe(f)
    fetched = fetch(f)
    interpret(f, fetched.snapshot_id)
    tables = (
        "event_progress_receipts",
        "event_progress_observations",
        "event_progress_scans",
        "event_progress_cursor",
    )
    before = {table: list(map(tuple, f.conn.execute("SELECT * FROM " + table))) for table in tables}
    f.conn.execute(
        "CREATE TRIGGER fail_progress BEFORE UPDATE ON event_progress_observations WHEN NEW.stage='interpreted' BEGIN SELECT RAISE(ABORT,'observer failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="observer failure"):
        observe(f)
    assert {
        table: list(map(tuple, f.conn.execute("SELECT * FROM " + table))) for table in tables
    } == before


@pytest.mark.parametrize("invalidation", ["expiry", "input_bundle", "epoch", "policy"])
def test_stale_missing_baseline_cannot_become_progress(event, invalidation):
    f = event
    ready(f)
    observe(f)
    assert any(r["fresh"] for r in history(f)["observations"])
    if invalidation == "expiry":
        f.corpus.clock.sleep(86401)
    elif invalidation == "input_bundle":
        f.conn.execute(
            "INSERT INTO meta VALUES('input_bundle_hash','changed') ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
    elif invalidation == "epoch":
        f.conn.execute("UPDATE event_pressure_state SET epoch=epoch+1")
    else:
        f.conn.execute("UPDATE admission_policies SET policy_revision=policy_revision||'-changed'")
    fetched = fetch(f)
    assert fetched.snapshot_id
    assert observe(f)["qualified_progress"] == 0
    assert not history(f)["progress"]


def test_corrupt_member_request_is_unassessed_not_an_invented_obligation(event):
    f = event
    ready(f)
    observe(f)
    f.conn.execute("DROP TRIGGER event_enumeration_member_no_update")
    f.conn.execute("UPDATE source_event_enumeration_members SET request_json='{}'")
    checked = observe(f)
    assert checked["unknown_pages"] == checked["checked_pages"]
    assert not history(f)["progress"]


def test_repeated_admission_and_observed_progress_are_immutable(event):
    f = event
    ready(f)
    observe(f)
    fetched = fetch(f)
    interpret(f, fetched.snapshot_id)
    assert observe(f)["qualified_progress"] == 2
    generation = next(
        r["generation_id"] for r in history(f)["progress"] if r["stage"] == "interpreted"
    )
    before = f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0]
    assert f.corpus.admit(generation) == "accepted"
    assert f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0] == before
    for table in ("event_stage_operations", "event_progress_receipts"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            f.conn.execute("DELETE FROM " + table)


def test_appended_valid_request_cannot_invent_an_enumeration_obligation(event):
    from swingset.schedule.event_evidence import request, request_id

    f = event
    ready(f)
    observe(f)
    enumeration = f.conn.execute("SELECT enumeration_id FROM source_event_inventory").fetchone()[0]
    page = request("eepro", "GET", f.parent.url + "injected.htm")
    import json

    f.conn.execute(
        "INSERT INTO source_event_enumeration_members VALUES(?,?,?,?,?)",
        (enumeration, request_id(page), json.dumps(page), "[]", f.corpus.clock.now().isoformat()),
    )
    result = observe(f)
    assert result["unassessed_events"] == 1
    assert result["reason_counts"]["event_enumeration_content_changed"] == 1
    h = history(f)
    assert not h["progress"] and all(not row["fresh"] for row in h["observations"])


def test_global_budget_exhaustion_cannot_starve_later_events(event, monkeypatch):
    from swingset.sources.base import WatchSpec

    f = event
    ready(f)
    parents = [f.parent]
    for number in range(1, 16):
        parent = index(f, str(number))
        parents.append(parent)
        admit_parent(f, ["one.htm"], parent=parent)
    # Positions zero and eight would starve the rest if exhausted refreshes
    # advanced blindly over an entire eight-event batch.
    costly = parents[8]
    f.corpus.snapshot(
        "costly",
        BODY,
        spec=WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            costly.url + "one.htm",
            "eepro.round",
            source_ref=costly.source_ref,
        ),
    )
    finish_bootstrap(f)
    enroll(f, config())
    opportunities = set()
    original = progress.Session.verify_request

    def attempt(session, page, **kwargs):
        assert not session.exhausted()
        opportunities.add(page["url"].rsplit("/", 1)[0] + "/")
        return original(session, page, **kwargs)

    monkeypatch.setattr(progress.Session, "verify_request", attempt)
    for _ in range(12):
        observe(f, limits=Limits(decoded_bytes=64))
        if all(parent.url in opportunities for parent in parents):
            break
    assert all(parent.url in opportunities for parent in parents)


def test_exact_interpretation_candidates_survive_busy_source(event):
    from swingset.sources.base import WatchSpec

    f = event
    ready(f)
    observe(f)
    for number in range(35):
        spec = WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            f.parent.url + f"noise{number}.htm",
            "eepro.round",
            source_ref="eepro:noise",
        )
        from swingset.schedule.watches import upsert_watch

        upsert_watch(f.conn, spec, f.corpus.clock.now())
        context = f.corpus.snapshot("noise-" + str(number), spec=spec)
        generation, _ = f.corpus.stage(context)
        assert f.corpus.admit(generation) == "accepted"
    fetched = fetch(f)
    interpret(f, fetched.snapshot_id)
    assert observe(f)["qualified_progress"] == 2


@pytest.mark.parametrize("cell", ["member_json", "prior_token", "subject_ref"])
def test_oversized_metadata_is_bounded_and_unassessed(event, cell):
    f = event
    ready(f)
    observe(f)
    huge = "x" * (2 * 1024 * 1024)
    if cell == "member_json":
        f.conn.execute("DROP TRIGGER event_enumeration_member_no_update")
        f.conn.execute("UPDATE source_event_enumeration_members SET support_json=?", (huge,))
    elif cell == "prior_token":
        f.conn.execute("UPDATE event_progress_observations SET token_json=?", (huge,))
    else:
        f.conn.execute(
            "INSERT INTO source_event_inventory VALUES('eepro',?,NULL,NULL,'legacy_unassessed')",
            (huge,),
        )
    result = observe(f)
    assert result["qualified_progress"] == 0
    assert result["unassessed_events"] or result["unknown_pages"]


def test_observation_time_is_separate_from_successful_operation_time(event):
    f = event
    ready(f)
    observe(f)
    fetch(f)
    operation_time = history(f)["last_recorded_operation_at"]
    f.corpus.clock.sleep(60)
    assert observe(f)["qualified_progress"] == 1
    h = history(f)
    assert h["latest_qualified_operation_at"] == operation_time
    assert h["last_observed_progress_at"] == f.corpus.clock.now().isoformat()
    assert h["last_observed_progress_at"] != operation_time


@pytest.mark.parametrize("exhaust_on", [1, 2])
def test_page_denied_leftover_budget_gets_fresh_opportunity(event, monkeypatch, exhaust_on):
    f = event
    ready(f, ("one.htm", "two.htm"))
    attempts = []
    original = progress.Session.verify_request

    def controlled(session, page, **kwargs):
        attempts.append(page["url"])
        if len(attempts) == exhaust_on:
            session.reader.rows = session.limits.rows
            return {"acquired": None, "interpreted": None, "reasons": {"row_budget": 1}}
        return original(session, page, **kwargs)

    monkeypatch.setattr(progress.Session, "verify_request", controlled)
    observe(f)
    assert len(attempts) == exhaust_on
    observe(f)
    if exhaust_on == 1:
        assert attempts[0] != attempts[1]  # A page exhausting a fresh allowance cannot trap.
    else:
        assert attempts[1] == attempts[2]  # Prior page used budget: retry with a fresh allowance.


def test_policy_receipt_captures_effective_execution_budget(event):
    import json

    f = event
    ready(f)
    observe(f, wall_seconds=0.5)
    stored = json.loads(
        f.conn.execute("SELECT policy_json FROM event_progress_policies").fetchone()[0]
    )
    assert stored["limits"]["seconds"] == 0.5
    assert all(row["current_default_policy_matches"] is False for row in history(f)["observations"])


def test_metadata_exhaustion_preserves_partial_cursor_then_resumes_after_it(event, monkeypatch):
    f = event
    ready(f, [f"{i}.htm" for i in range(40)])
    first = observe(f)
    assert first["checked_pages"] == 32
    saved = f.conn.execute("SELECT after_request_id FROM event_progress_scans").fetchone()[0]
    assert saved is not None
    exhausted = observe(f, limits=Limits(rows=2))
    assert exhausted["unassessed_events"] == 1 and exhausted["checked_pages"] == 0
    assert (
        f.conn.execute("SELECT after_request_id FROM event_progress_scans").fetchone()[0] == saved
    )
    seen = []
    original = progress.Session.verify_request

    def check(session, page, **kwargs):
        from swingset.schedule.event_evidence import request_id

        seen.append(request_id(page))
        return original(session, page, **kwargs)

    monkeypatch.setattr(progress.Session, "verify_request", check)
    resumed = observe(f)
    assert resumed["checked_pages"] == 9
    assert seen and all(identifier > saved for identifier in seen)
    assert f.conn.execute("SELECT after_request_id FROM event_progress_scans").fetchone()[0] is None


@pytest.mark.parametrize("invalid_token", ["[]", "null", "{}", "{"])
def test_refresh_rejects_nonobject_or_incomplete_missing_baseline_token(event, invalid_token):
    f = event
    ready(f)
    observe(f)
    f.conn.execute("UPDATE event_progress_observations SET token_json=?", (invalid_token,))
    fetch(f)
    result = observe(f)
    assert result["qualified_progress"] == 0 and result["unknown_pages"] >= 1
    assert not history(f)["progress"]


@pytest.mark.parametrize(
    "damage",
    [
        "array_token",
        "null_token",
        "incomplete_token",
        "invalid_json_token",
        "missing_policy",
        "array_policy",
        "incomplete_policy",
        "policy_digest",
    ],
)
def test_doctor_marks_only_damaged_observation_unknown(event, damage):
    import json
    from hashlib import sha256

    f = event
    ready(f)
    observe(f)
    row = f.conn.execute(
        "SELECT * FROM event_progress_observations WHERE stage='acquired' ORDER BY request_id LIMIT 1"
    ).fetchone()
    selected = (row["source"], row["source_ref"], row["request_id"], row["stage"])
    value = json.loads(row["token_json"])
    invalid = {
        "array_token": "[]",
        "null_token": "null",
        "incomplete_token": "{}",
        "invalid_json_token": "{",
    }
    if damage in invalid:
        encoded = invalid[damage]
        reason = "observation_token_invalid"
    else:
        reason = (
            "observation_policy_missing"
            if damage == "missing_policy"
            else "observation_policy_invalid"
        )
        policy_json = "[]" if damage == "array_policy" else '{"limits":{}}'
        digest = sha256(policy_json.encode()).hexdigest()
        if damage == "policy_digest":
            digest = "0" * 64
            policy_json = f.conn.execute(
                "SELECT policy_json FROM event_progress_policies LIMIT 1"
            ).fetchone()[0]
        if damage != "missing_policy":
            f.conn.execute("INSERT INTO event_progress_policies VALUES(?,?)", (digest, policy_json))
        value["policy_digest"] = digest
        encoded = json.dumps(value)
    f.conn.execute(
        "UPDATE event_progress_observations SET token_json=? WHERE source=? AND source_ref=? AND request_id=? AND stage=?",
        (encoded, *selected),
    )
    before = f.conn.total_changes
    f.conn.execute("PRAGMA query_only=ON")
    try:
        rows = history(f)["observations"]
        bad = next(
            item
            for item in rows
            if (item["source"], item["source_ref"], item["request_id"], item["stage"]) == selected
        )
        good = [item for item in rows if item is not bad]
        assert bad["availability_when_observed"] is None and not bad["fresh"]
        assert bad["observation_assessment"] == "unassessed" and bad["reasons"][reason] == 1
        assert good and all(
            item["fresh"] and item["observation_assessment"] == "recorded" for item in good
        )
        assert f.conn.total_changes == before
    finally:
        f.conn.execute("PRAGMA query_only=OFF")
