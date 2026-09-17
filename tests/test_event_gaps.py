"""Unavailable observations account for gaps without inventing successful work."""

import json
import sqlite3

import pytest
from test_cycle import overrides as overrides
from test_event_accounting import event_view, receipts, settle
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event
from test_event_pressure import config, enroll
from test_event_progress import fetch, interpret, observe

from swingset.admission.page_evidence import Limits, Session
from swingset.fetch.archive import canonical, digest
from swingset.schedule import event_gaps
from swingset.schedule.event_accounting_report import report
from swingset.schedule.event_evidence import request
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec


def response(f, name="one.htm", *, label="gone", classification="Gone", status=404, kind="round"):
    spec = WatchSpec(
        "", "eepro", kind, "GET", f.parent.url + name, "eepro.round", source_ref="eepro:test"
    )
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    context = f.corpus.snapshot(label, ("negative " + label).encode(), spec=spec)
    f.conn.execute(
        "UPDATE snapshots SET classification=?,http_status=? WHERE snapshot_id=?",
        (classification, status, context.snapshot_id),
    )
    return context


def ready(f, names=("one.htm",), *, unavailable=True):
    # Activate the real result contract with independent successful support.
    admit_parent(f, ["seed.htm", *names])
    child(f, "seed.htm")
    if unavailable:
        for number, name in enumerate(names):
            response(f, name, label=f"gone-{number}")
    finish_bootstrap(f)
    enroll(f, config())


def gap(f):
    return dict(
        f.conn.execute(
            "SELECT * FROM event_gap_observations WHERE availability=1 LIMIT 1"
        ).fetchone()
    )


@pytest.mark.parametrize(
    "interpreted,unavailable,expected",
    [
        (True, None, True),
        (False, True, True),
        (None, True, True),
        (False, False, False),
        (None, False, None),
        (False, None, None),
        (None, None, None),
    ],
)
def test_accounting_is_three_state_union(interpreted, unavailable, expected):
    assert event_gaps.accounted(interpreted, unavailable) is expected
    with pytest.raises(ValueError):
        event_gaps.accounted(1, unavailable)


def test_supported_gap_and_real_page_account_without_success_inflation(event):
    f = event
    ready(f)
    operations = f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0]
    settle(f)
    value = event_view(f)
    assert value["assessment"] == "locally_accounted" and value["listed_pages"] == 2
    assert value["page_accounting"] == dict(positive=2, negative=0, unknown=0)
    assert value["stages"]["interpreted"] == dict(positive=1, negative=1, unknown=0)
    assert value["unavailable"] == dict(positive=1, negative=1, unknown=0)
    assert value["parents"]["positive"] > 0
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()
    assert f.conn.execute("SELECT count(*) FROM event_stage_operations").fetchone()[0] == operations
    proof = json.loads(gap(f)["evidence_json"])
    assert proof["unavailability_support"]["snapshot_id"] == "gone-0"
    assert proof["acquired"] is proof["interpreted"] is False
    assert value["pagination"] == "unknown" and not value["current_stage_authority"]


def test_33_mixed_pages_finish_rotation_and_parent_checks(event):
    f = event
    names = [f"{n}.htm" for n in range(32)]
    ready(f, names, unavailable=False)
    for n, name in enumerate(names):
        if n % 2:
            child(f, name)
        else:
            response(f, name, label=f"gone-{n}")
    finish_bootstrap(f)
    enroll(f, config())
    first = observe(f)
    assert 0 < first["checked_pages"] <= 32
    assert event_view(f)["assessment"] != "locally_accounted"
    settle(f, attempts=40)
    value = event_view(f)
    assert value["listed_pages"] == value["page_accounting"]["positive"] == 33
    assert value["unavailable"]["positive"] == 16
    assert value["stages"]["interpreted"]["positive"] == 17
    assert value["parents"]["unknown"] == 0
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()


@pytest.mark.parametrize(
    "damage", ["digest", "support", "boolean", "request", "time", "expiry", "token", "revision"]
)
def test_malformed_stored_gap_is_unknown_without_false_reopening(event, damage):
    f = event
    ready(f)
    settle(f)
    row = gap(f)
    proof = json.loads(row["evidence_json"])
    if damage in {"support", "boolean", "request"}:
        if damage == "support":
            proof["unavailability_support"]["http_status"] = 200
        elif damage == "boolean":
            proof["unavailable"] = 1
        else:
            proof["request"]["url"] += "wrong"
        f.conn.execute(
            "UPDATE event_gap_observations SET evidence_json=?,evidence_digest=? WHERE request_id=?",
            (canonical(proof).decode(), digest(canonical(proof)), row["request_id"]),
        )
    else:
        column, value = {
            "digest": ("evidence_digest", "bad"),
            "time": ("observed_at", "2026-01-01T00:00:00"),
            "expiry": ("valid_until", "2099-01-01T00:00:00+00:00"),
            "token": ("token_json", "[]"),
            "revision": ("source_revision", -1),
        }[damage]
        f.conn.execute(
            f"UPDATE event_gap_observations SET {column}=? WHERE request_id=?",
            (value, row["request_id"]),
        )
    before = receipts(f)
    value = event_view(f)
    assert value["assessment"] == "unassessed"
    assert value["page_accounting"]["unknown"] == 1
    assert receipts(f) == before


def test_false_gap_rejects_malformed_stage_metadata_even_with_recomputed_digest(event):
    f = event
    ready(f, unavailable=False)
    observe(f)
    row = dict(
        f.conn.execute(
            "SELECT * FROM event_gap_observations WHERE json_extract(evidence_json,'$.interpreted')=0 LIMIT 1"
        ).fetchone()
    )
    proof = json.loads(row["evidence_json"])
    proof["acquired"] = "False"
    f.conn.execute(
        "UPDATE event_gap_observations SET evidence_json=?,evidence_digest=? WHERE request_id=?",
        (canonical(proof).decode(), digest(canonical(proof)), row["request_id"]),
    )
    assert event_view(f)["assessment"] == "unassessed"


def test_negative_body_loss_restore_and_sampled_report_are_explicit(event, monkeypatch):
    f = event
    ready(f)
    settle(f)
    proof = json.loads(gap(f)["evidence_json"])
    body = f.archive.blob_path(proof["unavailability_support"]["body_sha256"])
    original = body.read_bytes()
    body.unlink()
    # Metadata-only reporting intentionally retains the last bounded observation.
    with monkeypatch.context() as patch:
        patch.setattr(f.archive, "read_body", lambda *_: pytest.fail("report opened body"))
        assert event_view(f)["assessment"] == "locally_accounted"
    observe(f)
    assert event_view(f)["assessment"] == "unassessed"
    assert not any(r["transition"] == "reopened" for r in receipts(f))
    body.write_bytes(original)
    settle(f)
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()


@pytest.mark.parametrize("classification,status", [("Blocked", 403), ("Ok", 200)])
def test_unenrolled_alias_response_invalidates_gap_without_changing_progress_fence(
    event, classification, status
):
    f = event
    ready(f)
    settle(f)
    before = f.conn.execute(
        "SELECT revision FROM event_pressure_subjects WHERE source='eepro' AND source_ref='eepro:test'"
    ).fetchone()[0]
    context = response(
        f, label="alias", classification=classification, status=status, kind="other-round"
    )
    assert not f.conn.execute(
        "SELECT 1 FROM event_pressure_watches WHERE watch_id=?", (context.watch_id,)
    ).fetchone()
    assert (
        f.conn.execute(
            "SELECT revision FROM event_pressure_subjects WHERE source='eepro' AND source_ref='eepro:test'"
        ).fetchone()[0]
        == before
    )
    assert event_view(f)["assessment"] == "unassessed"
    observe(f)
    assert event_view(f)["assessment"] == "unfinished"
    assert event_view(f)["unavailable"]["positive"] == 0
    assert receipts(f)[-1]["transition"] == "reopened"


@pytest.mark.parametrize(
    "column,value",
    [
        ("http_status", 200),
        ("classification", "Blocked"),
        ("fetched_at", "2099-01-01T00:00:00+00:00"),
    ],
)
def test_alias_response_metadata_changes_invalidate_gap(event, column, value):
    f = event
    ready(f, unavailable=False)
    response(f, label="alias", kind="other-round")
    settle(f)
    old = gap(f)["source_revision"]
    f.conn.execute(f"UPDATE snapshots SET {column}=? WHERE snapshot_id='alias'", (value,))
    assert (
        f.conn.execute("SELECT revision FROM event_gap_revisions WHERE source='eepro'").fetchone()[
            0
        ]
        > old
    )
    assert event_view(f)["assessment"] == "unassessed"


def test_watch_source_move_invalidates_both_gap_domains(event):
    f = event
    ready(f)
    response(f, label="alias", kind="other-round")
    settle(f)
    source = f.conn.execute(
        "SELECT revision FROM event_gap_revisions WHERE source='eepro'"
    ).fetchone()[0]
    f.conn.execute(
        "UPDATE watches SET source='wdr' WHERE watch_id=(SELECT watch_id FROM snapshots WHERE snapshot_id='alias')"
    )
    assert (
        f.conn.execute("SELECT revision FROM event_gap_revisions WHERE source='eepro'").fetchone()[
            0
        ]
        > source
    )
    assert (
        f.conn.execute("SELECT revision FROM event_gap_revisions WHERE source='wdr'").fetchone()[0]
        > 0
    )
    assert event_view(f)["assessment"] == "unassessed"


def test_alias_parse_bookkeeping_does_not_invalidate_gap_domain(event):
    f = event
    ready(f, unavailable=False)
    response(f, label="alias", kind="other-round")
    settle(f)
    old = gap(f)["source_revision"]
    epoch = f.conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0]
    f.conn.execute("UPDATE snapshots SET parse_status='failed' WHERE snapshot_id='alias'")
    assert (
        f.conn.execute("SELECT revision FROM event_gap_revisions WHERE source='eepro'").fetchone()[
            0
        ]
        == old
    )
    assert f.conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0] == epoch
    assert event_view(f)["assessment"] == "locally_accounted"


@pytest.mark.parametrize(
    "column,value",
    [
        ("classification", "ServerError"),
        ("http_status", 500),
        ("via", "wayback"),
        ("fetched_at", "2099-01-01T00:00:00+00:00"),
        ("captured_at", "2025-01-01T00:00:00+00:00"),
        ("watch_source", "wdr"),
    ],
)
def test_positive_alias_support_rewrite_invalidates_sampled_stages(event, column, value):
    from test_admission import BODY

    f = event
    admit_parent(f, ["one.htm"])
    alias = WatchSpec(
        "",
        "eepro",
        "event",
        "GET",
        "https://EEPRO.COM:443/results/test/one.htm#alias",
        "eepro.round",
        source_ref="eepro:different-alias",
    )
    upsert_watch(f.conn, alias, f.corpus.clock.now())
    context = f.corpus.snapshot("positive-alias", BODY, spec=alias)
    generation, validation = f.corpus.stage(context)
    assert not validation.failures
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    finish_bootstrap(f)
    enroll(f, config())
    settle(f)
    if column == "watch_source":
        # An identical upsert is bookkeeping, not changed support authority.
        epoch = f.conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0]
        revision = f.conn.execute(
            "SELECT revision FROM event_gap_revisions WHERE source='eepro'"
        ).fetchone()[0]
        f.conn.execute("UPDATE watches SET source=source WHERE watch_id=?", (alias.watch_id,))
        assert f.conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0] == epoch
        assert (
            f.conn.execute(
                "SELECT revision FROM event_gap_revisions WHERE source='eepro'"
            ).fetchone()[0]
            == revision
        )
        f.conn.execute("UPDATE watches SET source=? WHERE watch_id=?", (value, alias.watch_id))
    else:
        f.conn.execute(
            f"UPDATE snapshots SET {column}=? WHERE snapshot_id=?", (value, context.snapshot_id)
        )
    assert event_view(f)["assessment"] == "unassessed"
    if column in {"classification", "watch_source"}:
        observe(f)
        assert event_view(f)["assessment"] == "unfinished"


def test_gap_to_real_acquisition_and_interpretation_still_qualifies_progress(event):
    f = event
    ready(f)
    settle(f)
    fetched = fetch(f)
    assert fetched.snapshot_id
    assert observe(f)["qualified_progress"] == 1
    assert event_view(f)["assessment"] == "unfinished"
    interpret(f, fetched.snapshot_id)
    assert observe(f)["qualified_progress"] == 1
    settle(f)
    assert event_view(f)["unavailable"]["positive"] == 0
    assert f.conn.execute("SELECT count(*) FROM event_progress_receipts").fetchone()[0] == 2


def test_gap_expiry_and_restored_epoch_are_unknown(event):
    f = event
    ready(f)
    settle(f)
    f.corpus.clock.sleep(config().scheduler.event_pressure_max_age_seconds)
    assert event_view(f)["assessment"] == "unassessed"
    settle(f)
    f.conn.execute("UPDATE event_pressure_state SET epoch=epoch+1")
    assert event_view(f)["assessment"] == "unassessed"
    settle(f)
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()


@pytest.mark.parametrize("valid_label,invalid_label", [("aaa", "zzz"), ("zzz", "aaa")])
def test_latest_terminal_tie_cannot_hide_invalid_response_status(event, valid_label, invalid_label):
    f = event
    ready(f, unavailable=False)
    response(f, label=valid_label)
    response(f, label=invalid_label, classification="ExpectedUnavailable", status=200)
    f.conn.execute(
        "UPDATE snapshots SET fetched_at=? WHERE snapshot_id IN (?,?)",
        (f.corpus.clock.now().isoformat(), valid_label, invalid_label),
    )
    with f.db.transaction(immediate=False):
        session = Session(f.conn, f.archive, cutoff=f.corpus.clock.now(), now=f.corpus.clock.now())
        assert (
            session.verify_request(
                request("eepro", "GET", f.parent.url + "one.htm"), classify_unavailability=True
            )["unavailable"]
            is None
        )
    observe(f)
    assert event_view(f)["assessment"] == "unassessed"


def test_concurrent_gap_domain_change_discards_gap_accounting_only(event, monkeypatch):
    from swingset.schedule import event_accounting

    f = event
    ready(f)
    original = event_accounting.parent_support
    changed = False

    def change(*args, **kwargs):
        nonlocal changed
        proof = original(*args, **kwargs)
        if not changed:
            with sqlite3.connect(f.db.state_dir / "state.sqlite") as conn:
                conn.execute(
                    "UPDATE event_gap_revisions SET revision=revision+1 WHERE source='eepro'"
                )
            changed = True
        return proof

    monkeypatch.setattr(event_accounting, "parent_support", change)
    observe(f)
    assert not f.conn.execute("SELECT 1 FROM event_gap_observations").fetchone()
    assert f.conn.execute("SELECT 1 FROM event_progress_observations").fetchone()
    assert receipts(f)[-1]["assessment"] == "unassessed"
    assert event_view(f)["assessment"] == "unassessed"


def test_gap_write_failure_rolls_back_progress_and_accounting(event):
    f = event
    ready(f)
    f.conn.execute(
        "CREATE TRIGGER reject_gap BEFORE INSERT ON event_gap_observations BEGIN SELECT RAISE(ABORT,'gap write failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="gap write failure"):
        observe(f)
    for table in (
        "event_gap_observations",
        "event_progress_observations",
        "event_accounting_receipts",
    ):
        assert not f.conn.execute("SELECT 1 FROM " + table).fetchone()


def test_negative_body_and_parents_share_budget_without_false_completion(event):
    f = event
    ready(f)
    result = observe(f, limits=Limits(decoded_bytes=1))
    assert result["budget_exhausted"]
    assert event_view(f)["assessment"] != "locally_accounted"
    settle(f)


def test_real_pause_suppresses_gap_observation(event, tmp_path, overrides):
    from test_event_progress import (
        test_paused_cycle_does_not_verify_artifacts_or_record_observed_progress,
    )

    test_paused_cycle_does_not_verify_artifacts_or_record_observed_progress(
        event, overrides, tmp_path
    )
    assert not event.conn.execute("SELECT 1 FROM event_gap_observations").fetchone()


def test_backup_preserves_gap_evidence_and_readonly_reporting(event, tmp_path):
    from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint
    from swingset.fetch.archive import Archive
    from swingset.state.db import SCHEMA_VERSION

    f = event
    ready(f)
    settle(f)
    expected = list(map(tuple, f.conn.execute("SELECT * FROM event_gap_observations")))
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
        assert list(map(tuple, conn.execute("SELECT * FROM event_gap_observations"))) == expected
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        before = conn.total_changes
        assert (
            report(conn, Archive(target), config(), now=f.corpus.clock.now())["events"][0][
                "assessment"
            ]
            == "locally_accounted"
        )
        assert conn.total_changes == before
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
