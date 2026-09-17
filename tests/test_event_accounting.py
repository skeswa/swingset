"""Bounded event accounting stays separate from successful work and publication."""

import gzip
import json
import sqlite3

import pytest
from test_cycle import overrides as overrides
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event
from test_event_pressure import config, enroll, index
from test_event_progress import observe

from swingset.admission.page_evidence import Limits
from swingset.schedule import event_progress as progress
from swingset.schedule.event_accounting_report import report


def complete(f, names=("one.htm",)):
    parent = admit_parent(f, names)
    children = [child(f, name) for name in names]
    finish_bootstrap(f)
    enroll(f, config())
    return parent, children


def view(f, **kwargs):
    with f.db.transaction(immediate=False):
        return report(f.conn, f.archive, config(), now=f.corpus.clock.now(), **kwargs)


def event_view(f):
    return view(f, source="eepro", source_ref="eepro:test")["events"][0]


def receipts(f):
    return [
        dict(r)
        for r in f.conn.execute("SELECT * FROM event_accounting_receipts ORDER BY receipt_id")
    ]


def settle(f, attempts=20):
    for _ in range(attempts):
        observe(f)
        if event_view(f)["assessment"] == "locally_accounted":
            return
    pytest.fail("bounded repeated visits did not account for complete fixture")


def test_initial_available_is_not_successful_progress_and_unchanged_checks_are_quiet(event):
    f = event
    complete(f)
    settle(f)
    value = event_view(f)
    assert value["listed_pages"] == 1
    assert value["assessment"] == "locally_accounted" and value["pagination"] == "unknown"
    assert value["parents"]["positive"] == 2
    assert value["retirement"] == "unassessed"
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()
    before = receipts(f)
    observe(f)
    assert receipts(f) == before
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        f.conn.execute("DELETE FROM event_accounting_receipts")


def test_33_pages_require_full_denominator_and_parent_support(event):
    f = event
    complete(f, [f"{n}.htm" for n in range(33)])
    first = observe(f)
    assert 0 < first["checked_pages"] <= 32
    assert event_view(f)["assessment"] != "locally_accounted"
    settle(f)
    value = event_view(f)
    assert value["listed_pages"] == 33
    assert value["stages"]["interpreted"] == dict(positive=33, negative=0, unknown=0)


@pytest.mark.parametrize("loss", ["parent", "member", "revocation"])
def test_loss_reopens_and_restoration_is_availability_not_operation(event, loss):
    f = event
    parent, children = complete(f)
    settle(f)
    operations = f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0]
    generation = parent if loss == "parent" else children[0][1]
    if loss == "revocation":
        f.conn.execute(
            "UPDATE source_generations SET state='revoked' WHERE generation_id=?", (generation,)
        )
    else:
        context = json.loads(
            f.conn.execute(
                "SELECT recipe_json FROM source_generations WHERE generation_id=?", (generation,)
            ).fetchone()[0]
        )["context"]
        sha = f.conn.execute(
            "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (context["snapshot_id"],)
        ).fetchone()[0]
        path = f.archive.blob_path(sha)
        original = path.read_bytes()
        path.write_bytes(gzip.compress(b"corrupt"))
    observe(f)
    assert event_view(f)["assessment"] == "unfinished"
    assert receipts(f)[-1]["transition"] == "reopened"
    observe(f)
    assert sum(r["transition"] == "reopened" for r in receipts(f)) == 1
    if loss != "revocation":
        path.write_bytes(original)
        settle(f)
        assert receipts(f)[-1]["transition"] == "availability_restored"
        assert (
            f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0]
            == operations
        )
        assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()


def test_expiry_and_budget_unknown_do_not_create_reopening(event):
    f = event
    complete(f)
    settle(f)
    first = receipts(f)
    f.corpus.clock.sleep(86401)
    assert event_view(f)["assessment"] == "unassessed"
    assert receipts(f) == first  # Reporting does not synthesize transitions.
    observe(f, limits=Limits(rows=2))
    assert receipts(f)[-1]["assessment"] == "unassessed"
    settle(f)
    assert not any(r["transition"] == "reopened" for r in receipts(f))


def test_unknown_between_negatives_does_not_duplicate_reopening(event):
    f = event
    _, children = complete(f)
    settle(f)
    sha = f.conn.execute(
        "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (children[0][0].snapshot_id,)
    ).fetchone()[0]
    f.archive.blob_path(sha).unlink()
    observe(f)
    observe(f, limits=Limits(rows=2))
    observe(f)
    assert sum(r["transition"] == "reopened" for r in receipts(f)) == 1


def test_membership_change_is_not_progress_or_retirement(event):
    f = event
    complete(f, ("one.htm", "two.htm"))
    settle(f)
    admit_parent(f, ["one.htm"], authority=True)
    finish_bootstrap(f)
    enroll(f, config())
    observe(f)
    assert receipts(f)[-1]["transition"] == "membership_changed"
    assert event_view(f)["retirement"] == "unassessed"
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()


def test_queryonly_report_never_checks_artifacts_and_pages_catalog(event, monkeypatch):
    f = event
    complete(f)
    settle(f)
    for n in range(3):
        admit_parent(f, ["missing.htm"], parent=index(f, str(n)))
    finish_bootstrap(f)
    enroll(f, config())
    before = f.conn.total_changes
    monkeypatch.setattr(
        progress.Session, "verify_request", lambda *a: pytest.fail("report verified an artifact")
    )
    monkeypatch.setattr(
        progress.Session,
        "verify_operation",
        lambda *a, **kw: pytest.fail("report verified an artifact"),
    )
    first = view(f, limit=2)
    second = view(f, limit=2, after_event=first["next_cursor"])
    assert not first["coverage_complete"] and second["coverage_complete"]
    assert len(first["events"]) + len(second["events"]) == 4
    assert {r["source_ref"] for r in first["events"]}.isdisjoint(
        r["source_ref"] for r in second["events"]
    )
    assert f.conn.total_changes == before


def test_fences_and_malformed_observations_are_unassessed(event):
    f = event
    complete(f)
    settle(f)
    f.conn.execute("UPDATE event_accounting_support_observations SET token_json='[]'")
    assert event_view(f)["assessment"] == "unassessed"
    settle(f)
    f.conn.execute("UPDATE event_pressure_state SET epoch=epoch+1")
    assert event_view(f)["assessment"] == "unassessed"
    settle(f)
    f.conn.execute(
        "INSERT INTO meta VALUES('input_bundle_hash','changed') ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    assert event_view(f)["assessment"] == "unassessed"


def test_concurrent_invalidation_discards_accounting_with_progress(event, monkeypatch):
    from swingset.schedule import event_accounting

    f = event
    complete(f)
    original = event_accounting.parent_support

    def change(*args, **kwargs):
        value = original(*args, **kwargs)
        with sqlite3.connect(f.db.state_dir / "state.sqlite") as other:
            other.execute("UPDATE event_pressure_state SET epoch=epoch+1")
        return value

    monkeypatch.setattr(event_accounting, "parent_support", change)
    assert observe(f)["discarded_events"] == 1
    assert not receipts(f)
    assert not f.conn.execute("SELECT 1 FROM event_accounting_support_observations").fetchone()


def test_failed_accounting_write_rolls_back_whole_batch(event):
    f = event
    complete(f)
    f.conn.execute(
        "CREATE TRIGGER fail_accounting BEFORE INSERT ON event_accounting_receipts BEGIN SELECT RAISE(ABORT,'accounting failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="accounting failure"):
        observe(f)
    assert not receipts(f)
    assert not f.conn.execute("SELECT 1 FROM event_progress_observations").fetchone()
    assert not f.conn.execute("SELECT 1 FROM event_accounting_support_observations").fetchone()
    assert tuple(
        f.conn.execute("SELECT last_rowid,high_water FROM event_progress_cursor").fetchone()
    ) == (0, 0)


def test_earliest_expiry_is_not_extended_by_later_parent_check(event):
    f = event
    complete(f)
    settle(f)
    initial = event_view(f)["valid_until"]
    f.corpus.clock.sleep(60)
    f.conn.execute("DELETE FROM event_accounting_support_observations")
    assert observe(f)["checked_pages"] == 0
    value = event_view(f)
    assert value["assessment"] == "locally_accounted" and value["valid_until"] == initial


def test_parent_budget_is_shared_and_other_events_get_opportunity(event, monkeypatch):
    from swingset.schedule import event_accounting

    f = event
    complete(f)
    settle(f)
    f.conn.execute("DELETE FROM event_accounting_support_observations")
    other = index(f, "other")
    admit_parent(f, ["missing.htm"], parent=other)
    finish_bootstrap(f)
    enroll(f, config())
    visited = []
    original = event_accounting.parent_support

    def costly(session, **kwargs):
        visited.append(kwargs["generation_id"])
        session.reader.rows = session.limits.rows
        return dict(usable=None, reason="row_budget", reasons=["row_budget"])

    monkeypatch.setattr(event_accounting, "parent_support", costly)
    for _ in range(4):
        observe(f)
    assert len(set(visited)) >= 2
    assert f.conn.execute(
        "SELECT 1 FROM event_progress_observations WHERE source_ref=?", (other.source_ref,)
    ).fetchone()
    assert event_view(f)["assessment"] == "unassessed"
    monkeypatch.setattr(event_accounting, "parent_support", original)


def test_legacy_and_oversized_membership_never_vacuously_complete(event):
    f = event
    finish_bootstrap(f)
    enroll(f, config())
    observe(f)
    assert event_view(f)["assessment"] == "unassessed"
    admit_parent(f, [f"{n}.htm" for n in range(129)])
    finish_bootstrap(f)
    enroll(f, config())
    observe(f)
    assert event_view(f)["assessment"] == "unassessed"


def test_checkpoint_and_readonly_reopen_preserve_accounting(event, tmp_path):
    from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint
    from swingset.state.db import SCHEMA_VERSION

    f = event
    complete(f)
    settle(f)
    tables = ("event_accounting_support_observations", "event_accounting_receipts")
    before = {t: list(map(tuple, f.conn.execute("SELECT * FROM " + t))) for t in tables}
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
        assert {t: list(map(tuple, conn.execute("SELECT * FROM " + t))) for t in tables} == before
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        from swingset.fetch.archive import Archive

        value = report(conn, Archive(target), config(), now=f.corpus.clock.now())
        assert value["events"][0]["assessment"] == "locally_accounted"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert observe(f)["accounting_transitions"] == 0


def test_paused_real_cycle_cannot_observe_accounting(event, tmp_path, overrides):
    from test_event_progress import (
        test_paused_cycle_does_not_verify_artifacts_or_record_observed_progress,
    )

    test_paused_cycle_does_not_verify_artifacts_or_record_observed_progress(
        event, overrides, tmp_path
    )
    assert not event.conn.execute("SELECT 1 FROM event_accounting_receipts").fetchone()
    assert not event.conn.execute("SELECT 1 FROM event_accounting_support_observations").fetchone()


def test_aggregate_support_counts_normalized_requests_once(event):
    from tests.build.test_event_page_evidence import aggregate

    f = event
    aggregate(f)
    finish_bootstrap(f)
    enroll(f, config())
    settle(f)
    value = event_view(f)
    assert value["listed_pages"] == 2
    assert value["stages"]["interpreted"]["positive"] == 2


def test_report_old_schema_is_explicitly_unsupported(event):
    with sqlite3.connect(":memory:") as conn:
        value = report(conn, event.archive, config(), now=event.corpus.clock.now())
    assert not value["supported"] and value["retirement"] == "unassessed"


def test_report_metadata_exhaustion_retains_pagination(event, monkeypatch):
    f = event
    complete(f)
    settle(f)
    original = progress.Session.read

    def exhausted(session, sql, *args, **kwargs):
        value = original(session, sql, *args, **kwargs)
        if "parent_support_json,pagination" in sql:
            session.reader.rows = session.limits.rows
        return value

    monkeypatch.setattr(progress.Session, "read", exhausted)
    value = view(f)
    assert not value["coverage_complete"] and value["next_cursor"] is not None
    assert value["events"][0]["assessment"] == "unassessed"


def test_chronological_bounds_and_policy_ttl_validation(event):
    from datetime import datetime, timedelta, timezone

    f = event
    complete(f)
    settle(f)
    now = f.corpus.clock.now()
    # Same instants, different ISO offsets. String ordering reverses chronology.
    early = now - timedelta(seconds=60)
    offset = timezone(timedelta(hours=-4))
    f.conn.execute(
        "UPDATE event_progress_observations SET observed_at=?,valid_until=?",
        (early.isoformat(), (early + timedelta(days=1)).isoformat()),
    )
    f.conn.execute(
        "UPDATE event_accounting_support_observations SET observed_at=?,valid_until=?",
        (
            now.astimezone(offset).isoformat(),
            (now + timedelta(days=1)).astimezone(offset).isoformat(),
        ),
    )
    value = event_view(f)
    assert datetime.fromisoformat(value["earliest_checked_at"]) == early
    assert datetime.fromisoformat(value["valid_until"]) == early + timedelta(days=1)
    f.conn.execute(
        "UPDATE event_accounting_support_observations SET valid_until='2099-01-01T00:00:00+00:00'"
    )
    assert event_view(f)["assessment"] == "unassessed"
    settle(f)  # Malformed observations must not trap the priority preflight.


def test_last_definite_history_survives_unknown_sample(event):
    f = event
    complete(f)
    settle(f)
    observe(f, limits=Limits(rows=2))
    value = event_view(f)
    assert value["last_observed_transition"]["assessment"] == "unassessed"
    assert value["last_definite_assessment"]["assessment"] == "locally_accounted"
    assert value["historical_reopening_total"] is None


@pytest.mark.parametrize("damage", ["overlong", "naive"])
def test_malformed_page_times_cannot_trap_parent_priority(event, damage):
    f = event
    complete(f)
    settle(f)
    if damage == "overlong":
        f.conn.execute(
            "UPDATE event_progress_observations SET valid_until='2099-01-01T00:00:00+00:00'"
        )
    else:
        f.conn.execute(
            "UPDATE event_progress_observations SET observed_at=substr(observed_at,1,19)"
        )
    f.conn.execute("DELETE FROM event_accounting_support_observations")
    assert event_view(f)["assessment"] == "unassessed"
    assert observe(f)["checked_pages"] == 1
    settle(f)
