"""Independent H16 acceptance over retained score sheets and real release APIs."""

import json
import shutil
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from publish.test_recovery import FakeHub
from test_admission import BODY, Corpus
from test_correction_only import _bundle, _published_fixture
from test_h15_acceptance import source_fixture as make_source_fixture
from test_project_event import EVENT

from swingset.build import service
from swingset.clock import FakeClock
from swingset.publish.service import publish, reconcile
from swingset.state.db import open_database


def candidate_tables(candidate):
    import pyarrow.parquet as pq

    from swingset.build.schema import SCHEMAS

    return {
        table: [
            row
            for path in sorted((candidate / "data" / table).rglob("*.parquet"))
            for batch in pq.ParquetFile(path).iter_batches()
            for row in batch.to_pylist()
        ]
        for table in SCHEMAS
    }


@pytest.fixture
def source_fixture(tmp_path):
    yield from make_source_fixture.__wrapped__(tmp_path)


@pytest.fixture
def release_state(tmp_path):
    """Retain real finals/prelim baseline; review only the unrelated test source."""
    with open_database(tmp_path / "state") as db:
        clock = FakeClock(datetime(2026, 9, 13, 5, tzinfo=UTC))
        corpus = Corpus(db)
        corpus.clock = clock
        initial, report = corpus.stage(corpus.snapshot("unrelated-initial"))
        assert not report.failures
        corpus.review(initial)
        assert corpus.admit(initial) == "accepted"
        clock.sleep(7200)
        baseline, identifier, binding, positive = _published_fixture(db, clock)
        (baseline / "PUBLISHED").write_text(
            json.dumps({"commit": "published-fixture", "verified_at": "2026-09-13T06:00:00+00:00"})
        )
        from materialized_fixture import materialize_seeded_outputs

        from swingset.schedule.cycle import versions
        from swingset.state.identity_journal import encode_journal
        from swingset.state.inputs import accept, capture

        config, overrides = tmp_path / "config", tmp_path / "overrides"
        shutil.copytree("config", config)
        shutil.copytree("overrides", overrides)
        (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
        (overrides / "event_aliases.csv").write_text(
            f"source,source_ref,event_id,note\neepro,eepro:hummer,{EVENT},Retained fixture\n"
        )
        (overrides / "identity_overrides.csv").write_bytes(encode_journal((positive,)))
        (overrides / "suppressions.csv").write_text("wsdc_id,name_norm,reason,date\n")
        bundle = capture(config, overrides, db.state_dir, versions())
        accept(db, bundle, clock)
        # This publication fixture seeds its event directly; give its retained
        # baseline metadata an explicit owner before capturing test-only outputs.
        db.connection.execute(
            "INSERT OR IGNORE INTO canonical_scope_rows VALUES ('calendar','retained-baseline','events',?)",
            (json.dumps([EVENT], separators=(",", ":")),),
        )
        materialize_seeded_outputs(db, now=clock.now(), run_id="run_a")
        yield SimpleNamespace(
            db=db,
            conn=db.connection,
            clock=clock,
            corpus=corpus,
            baseline=baseline,
            identifier=identifier,
            binding=binding,
            positive=positive,
            bundle=bundle,
            config=config,
            overrides=overrides,
            hub=FakeHub("published-fixture"),
            run="run_a",
        )


def arrive(fixture, number):
    """Admit new unrelated evidence under the already reviewed exact contract."""
    body = BODY.replace(b"Alice Example", f"Later Person {number}".encode())
    context = fixture.corpus.snapshot(f"post-cutoff-{number}", body)
    generation, report = fixture.corpus.stage(context, body=body)
    assert not report.failures
    assert fixture.corpus.admit(generation) == "accepted"
    return generation


def test_inventory_collision_keeps_baseline_supported_event_and_named_judges(release_state):
    from materialized_fixture import materialize_seeded_outputs

    from swingset.project.map import project_map
    from swingset.project.materialization import materializing
    from swingset.state.work import WorkUnit

    f = release_state
    before = dict(f.conn.execute("SELECT * FROM events WHERE event_id=?", (EVENT,)).fetchone())
    named = {
        row[0]
        for row in f.conn.execute(
            "SELECT judge_id FROM judges WHERE event_id=? AND name_raw IS NOT NULL AND wsdc_id IS NULL",
            (EVENT,),
        )
    }
    assert named
    with f.db.transaction():
        f.conn.execute(
            "INSERT INTO canonical_scope_rows VALUES ('history','all','events',json_array(?))",
            (EVENT,),
        )
        # An unsupported listing names the same month/slug, but conflicts with
        # the retained event dates. It cannot replace that event's public support.
        f.conn.execute(
            "INSERT INTO source_events(source,source_ref,name_raw,start_date,end_date,url,"
            "snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES "
            "('scoringdance','collision','Summer Hummer','2026-08-01','2026-08-02',"
            "'https://example.test/collision','unassessed-listing','1',?,?,?)",
            (f.clock.now().isoformat(), f.clock.now().isoformat(), f.run),
        )
        # This fixture retains precomputed inventory rows; capture the new
        # listing dependency before running its downstream map projector.
        with materializing(
            f.conn,
            WorkUnit("project", "inventory", "all"),
            now=f.clock.now(),
            run_id=f.run,
        ):
            pass
        project_map(f.conn, f.bundle, f.clock.now().isoformat(), f.run, 19)
    assert (
        dict(f.conn.execute("SELECT * FROM events WHERE event_id=?", (EVENT,)).fetchone()) == before
    )
    materialize_seeded_outputs(f.db, now=f.clock.now(), run_id=f.run)
    candidate = service.build_release(f.db, f.bundle, f.clock, f.run)
    tables = candidate_tables(candidate.path)
    assert any(row["event_id"] == EVENT for row in tables["events"])
    assert named <= {
        row["judge_id"]
        for row in tables["judges"]
        if row["name_raw"] is not None and row["wsdc_id"] is None
    }


def test_coherent_cutoff_survives_repeated_later_admission_and_publishes_once(
    release_state, monkeypatch
):
    f = release_state
    real_build = service.build_candidate
    builds = []
    later = []

    def concurrent_arrival(*args, **kwargs):
        later.extend(arrive(f, index) for index in range(3))
        result = real_build(*args, **kwargs)
        builds.append(result.path)
        return result

    monkeypatch.setattr(service, "build_candidate", concurrent_arrival)
    candidate = service.build_release(f.db, f.bundle, f.clock, f.run, f.hub)
    assert builds == [candidate.path]
    pinned_files = {
        path.relative_to(candidate.path).as_posix(): path.read_bytes()
        for path in candidate.path.rglob("*")
        if path.is_file()
    }
    later.extend(arrive(f, index) for index in range(3, 6))
    public = candidate_tables(candidate.path)
    assert public["final_marks"] and public["callback_marks"]
    assert not any(
        str(row["snapshot_id"]).startswith("post-cutoff-") for row in public["snapshots"]
    )
    result = publish(f.db.state_dir, candidate.path, f.hub)
    assert result.state == "published" and f.hub.calls == 1
    assert reconcile(f.db.state_dir, f.hub, dry_run=False).state == "none"
    assert f.hub.calls == 1
    assert {
        path.relative_to(candidate.path).as_posix(): path.read_bytes()
        for path in candidate.path.rglob("*")
        if path.is_file() and path.name not in {"PUBLISHED", "PUBLISHING"}
    } == pinned_files
    placeholders = ",".join("?" for _ in later)
    assert (
        f.conn.execute(
            f"SELECT count(*) FROM source_generations WHERE generation_id IN ({placeholders})",
            later,
        ).fetchone()[0]
        == 6
    )
    assert f.conn.execute("SELECT count(*) FROM entries WHERE event_id=?", (EVENT,)).fetchone()[
        0
    ] == len([row for row in public["entries"] if row["event_id"] == EVENT])


@pytest.mark.parametrize("change", ["journal", "suppression", "policy"])
def test_cutoff_never_overrides_current_correction_or_acceptance_safety(release_state, change):
    from dataclasses import replace
    from hashlib import sha256

    from test_identity_journal import decision

    from swingset.admission.policy import pause_contract
    from swingset.publish.service import PublishError
    from swingset.state.inputs import accept

    f = release_state
    candidate = service.build_release(f.db, f.bundle, f.clock, f.run, f.hub)
    if change == "journal":
        revoked = decision(
            f.binding,
            "different_person",
            identifier="review-after-cutoff",
            supersedes=f.positive.decision_id,
        )
        accept(f.db, _bundle(f.positive, revoked), f.clock)
    elif change == "suppression":
        files = {
            **f.bundle.files,
            "overrides/suppressions.csv": b"wsdc_id,reason\n100,Private suppression note\n",
        }
        accept(
            f.db,
            replace(
                f.bundle,
                digest=sha256(files["overrides/suppressions.csv"]).hexdigest(),
                files=files,
            ),
            f.clock,
        )
    else:
        pause_contract(f.conn, f.corpus.page.kind)
    with pytest.raises(PublishError):
        publish(f.db.state_dir, candidate.path, f.hub)
    assert f.hub.calls == 0
    assert not (candidate.path / "PUBLISHED").exists()
    assert (f.db.state_dir / "baseline").resolve() == f.baseline.resolve()


def test_unrelated_stuck_parse_allows_coherent_release_and_remains_unfinished(release_state):
    from swingset.state.attempts import begin_attempt, finish_attempt
    from swingset.state.work import WorkUnit, enqueue, unfinished_units

    f = release_state
    stuck = WorkUnit("parse", "snapshot", "unrelated-missing-body")
    enqueue(f.conn, (stuck,), enqueued_at=f.clock.now().isoformat())
    attempt = begin_attempt(f.db, stuck, now=f.clock.now(), run_id=f.run)
    finish_attempt(
        f.db, attempt, now=f.clock.now(), outcome="blocked", reason_code="snapshot_body_missing"
    )
    before = tuple(
        f.conn.execute(
            "SELECT * FROM work_attempts WHERE attempt_id=?", (attempt.attempt_id,)
        ).fetchone()
    )
    candidate = service.build_release(f.db, f.bundle, f.clock, f.run, f.hub)
    public = candidate_tables(candidate.path)
    assert public["final_marks"] and public["callback_marks"]
    assert public["coverage"]
    assert any(
        row["discovery_universe"] == "unknown" and row["discovery_denominator"] is None
        for row in public["coverage"]
    )
    assert all(
        row["scope_kind"] in {"source", "year", "event"} and row["scope_id"]
        for row in public["coverage"]
    )
    assert all(
        row["method"] and row["population"] and row["evidence_cutoff"] for row in public["coverage"]
    )
    assert stuck.unit_id in json.dumps(public["review_queue"], default=str)
    assert stuck in set(unfinished_units(f.conn))
    assert (
        tuple(
            f.conn.execute(
                "SELECT * FROM work_attempts WHERE attempt_id=?", (attempt.attempt_id,)
            ).fetchone()
        )
        == before
    )
    manifest = json.loads((candidate.path / "_meta/manifest.json").read_bytes())
    assert manifest
    assert publish(f.db.state_dir, candidate.path, f.hub).state == "published"
    assert stuck in set(unfinished_units(f.conn))


def test_cutoff_preserves_baseline_changed_completion_fence(release_state, monkeypatch):
    from swingset.build.builder import BuildError

    f = release_state
    real_build = service.build_candidate
    built = []

    def another_release_wins(*args, **kwargs):
        result = real_build(*args, **kwargs)
        built.append(result.path)
        replacement = f.db.state_dir / "candidates/independently-published"
        shutil.copytree(f.baseline, replacement)
        (replacement / "PUBLISHED").write_text('{"commit":"different-acknowledged-release"}')
        (f.db.state_dir / "baseline").unlink()
        (f.db.state_dir / "baseline").symlink_to(replacement)
        return result

    monkeypatch.setattr(service, "build_candidate", another_release_wins)
    with pytest.raises(BuildError, match="baseline changed"):
        service.build_release(f.db, f.bundle, f.clock, f.run, f.hub)
    assert len(built) == 1 and (built[0] / "REJECTED").exists()
    assert f.hub.calls == 0


@pytest.mark.parametrize("changed_scope", ["event", "dancer", "alias"])
def test_incompatible_link_generation_cannot_join_new_structural_closure(
    source_fixture, changed_scope
):
    from pathlib import Path

    from test_h15_acceptance import drain_project

    from swingset.build import closure
    from swingset.build.closure_manifest import digest
    from swingset.link.service import link_event
    from swingset.schedule.cycle import versions
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.wsdc_registry.adapter import SOURCE
    from swingset.state.inputs import accept, capture

    f = source_fixture
    drain_project(f)
    for event in ("event-a", "event-b"):
        link_event(f.db, event, f.bundle, f.corpus.clock, f.corpus.run)
    initial = closure.select(f.conn, cutoff=f.corpus.clock.now())
    closure.validate(f.conn, initial)
    old_link = next(
        row for row in initial.selected if (row["stage"], row["unit_id"]) == ("link", "event-a")
    )
    if changed_scope == "event":
        body = f.body.replace(b"Alice Example", b"Later Participant")
        generation, report = f.corpus.stage(f.corpus.snapshot("changed-event", body), body=body)
        assert not report.failures and f.corpus.admit(generation) == "accepted"
    elif changed_scope == "dancer":
        spec = SOURCE.watch(1)
        upsert_watch(f.conn, spec, f.corpus.clock.now())
        body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
        context = f.corpus.snapshot("changed-registry", body, spec=spec)
        f.conn.execute(
            "UPDATE snapshots SET method=? WHERE snapshot_id=?", (spec.method, context.snapshot_id)
        )
        generation, report = f.corpus.stage(context, body=body)
        assert not report.failures
        f.corpus.review(generation)
        assert f.corpus.admit(generation) == "accepted"
    else:
        f.aliases.write_text(
            "source,source_ref,event_id,note\neepro,eepro:test,event-b,Reviewed move\n"
        )
        f.bundle = capture(f.config, f.overrides, f.db.state_dir, versions())
        accept(f.db, f.bundle, f.corpus.clock)
    drain_project(f)
    selected = closure.select(f.conn, cutoff=f.corpus.clock.now())
    closure.validate(f.conn, selected)
    assert old_link["generation_id"] not in {row["generation_id"] for row in selected.selected}
    assert any(
        row["unit_id"] == "event-a" and row["stage"] == "link" and row["status"] == "withheld"
        for row in selected.inventory
    )
    mixed = selected.manifest()
    mixed["selected"] = [*mixed["selected"], old_link]
    mixed["digest"] = digest({key: value for key, value in mixed.items() if key != "digest"})
    with pytest.raises(closure.ClosureError, match="mixed_dependency_generations"):
        closure.validate(f.conn, mixed)


def test_selected_source_proof_cannot_be_omitted_or_revoked(source_fixture):
    from test_h15_acceptance import drain_project

    from swingset.admission.select import revoke_generation
    from swingset.build import closure
    from swingset.build.closure_manifest import digest
    from swingset.link.service import link_event

    f = source_fixture
    drain_project(f)
    for event in ("event-a", "event-b"):
        link_event(f.db, event, f.bundle, f.corpus.clock, f.corpus.run)
    selected = closure.select(f.conn, cutoff=f.corpus.clock.now())
    closure.validate(f.conn, selected)
    assert selected.source_support and any(
        row["state"] == "accepted" for row in selected.source_support
    )
    omitted = selected.manifest()
    omitted["source_support"] = []
    omitted["digest"] = digest({key: value for key, value in omitted.items() if key != "digest"})
    with pytest.raises(closure.ClosureError):
        closure.validate(f.conn, omitted)
    revoke_generation(
        f.db,
        f.generation,
        now=f.corpus.clock.now().isoformat(),
        run_id=f.corpus.run,
        reason="Offline reviewed support withdrawal",
    )
    with pytest.raises(closure.ClosureError, match="revoked|revocations_changed|support_changed"):
        closure.validate(f.conn, selected)


def test_local_correction_waits_for_verified_publication_receipt(release_state, monkeypatch):
    import argparse

    from test_identity_journal import decision

    from swingset import cli
    from swingset.schedule.cycle import versions
    from swingset.state.identity_journal import encode_journal
    from swingset.state.inputs import accept, capture
    from swingset.state.publication_report import publication_report

    f = release_state
    negative = decision(
        f.binding,
        "different_person",
        identifier="publish-correction",
        supersedes=f.positive.decision_id,
    )
    (f.overrides / "identity_overrides.csv").write_bytes(encode_journal((f.positive, negative)))
    f.bundle = capture(f.config, f.overrides, f.db.state_dir, versions())
    accept(f.db, f.bundle, f.clock)
    candidate = service.build_release(f.db, f.bundle, f.clock, f.run, f.hub, correction_only=True)
    f.clock.sleep(60)
    monkeypatch.setattr(cli, "SystemClock", lambda: f.clock)
    reported = cli.doctor(argparse.Namespace(state=f.db.state_dir, config=f.config))["publication"]
    assert reported["published"]["commit"] == "published-fixture"
    assert reported["published"]["verified_at"] == "2026-09-13T06:00:00+00:00"
    assert reported["awaiting_publication"]["candidate_ids"] == [candidate.candidate_id]
    assert reported["awaiting_publication"]["age_seconds"] == 60
    assert (
        reported["awaiting_publication"]["basis"] == "verified local candidates; no remote receipt"
    )
    assert reported["correction"]["awaiting_publication"]
    assert reported["correction"]["age_seconds"] == 60
    real_commit = f.hub.create_commit

    def in_flight(**kwargs):
        waiting = publication_report(f.conn, f.db.state_dir, f.clock.now())
        assert waiting["published"] == reported["published"]
        assert waiting["correction"]["awaiting_publication"]
        return real_commit(**kwargs)

    f.hub.create_commit = in_flight
    assert publish(f.db.state_dir, candidate.path, f.hub).state == "published"
    receipt = json.loads((candidate.path / "PUBLISHED").read_bytes())
    published = cli.doctor(argparse.Namespace(state=f.db.state_dir, config=f.config))["publication"]
    assert published["published"]["commit"] == receipt["commit"]
    assert published["published"]["verified_at"] == receipt["verified_at"]
    assert published["published"]["candidate_id"] == candidate.candidate_id
    assert published["awaiting_publication"]["candidate_ids"] == []
    assert not published["correction"]["awaiting_publication"]
    assert published["correction"]["age_seconds"] is None
    assert f.hub.calls == 1


@pytest.mark.parametrize(
    "invalid",
    ["rejected", "missing_completion", "corrupt_file", "malformed_receipt", "new_suppression"],
)
def test_publication_readiness_requires_current_complete_verified_candidate(
    release_state, monkeypatch, invalid
):
    from swingset.build import generations
    from swingset.state.publication_report import publication_report

    f = release_state
    candidate = service.build_release(f.db, f.bundle, f.clock, f.run, f.hub)
    report = publication_report(f.conn, f.db.state_dir, f.clock.now())
    assert report["awaiting_publication"]["candidate_ids"] == [candidate.candidate_id]
    if invalid == "rejected":
        (candidate.path / "REJECTED").write_text('{"reason":"Private operator note"}')
    elif invalid == "missing_completion":
        monkeypatch.setattr(generations, "completed", lambda *args: False)
    elif invalid == "malformed_receipt":
        (candidate.path / "BUILT").write_text("[]")
    elif invalid == "corrupt_file":
        next((candidate.path / "data").rglob("*.parquet")).write_bytes(b"damaged")
    else:
        (f.overrides / "suppressions.csv").write_text(
            "wsdc_id,name_norm,reason,date\n100,,Private operator note,2026-09-13\n"
        )
    report = publication_report(f.conn, f.db.state_dir, f.clock.now())
    assert report["published"]["commit"] == "published-fixture"
    assert report["awaiting_publication"]["candidate_ids"] == []
    assert "Private operator note" not in json.dumps(report)
    assert f.hub.calls == 0


def test_publication_report_pins_receipt_target_across_concurrent_promotion(
    release_state, monkeypatch
):
    from swingset.state import publication_report as reporting

    f = release_state
    replacement = f.db.state_dir / "candidates" / "later-published-fixture"
    shutil.copytree(f.baseline, replacement)
    (replacement / "PUBLISHED").write_text(
        json.dumps({"commit": "later-fixture", "verified_at": "2026-09-13T08:00:00+00:00"})
    )
    real_verify = reporting.verify_candidate_files

    def promote_after_verification(path):
        real_verify(path)
        assert path == f.baseline.resolve()
        baseline = f.db.state_dir / "baseline"
        baseline.unlink()
        baseline.symlink_to(replacement)

    monkeypatch.setattr(reporting, "verify_candidate_files", promote_after_verification)
    report = reporting.publication_report(f.conn, f.db.state_dir, f.clock.now())
    assert report["published"]["commit"] == "published-fixture"
    assert report["published"]["verified_at"] == "2026-09-13T06:00:00+00:00"
    assert report["published"]["candidate_id"] == f.baseline.name
    assert (f.db.state_dir / "baseline").resolve() == replacement


def test_repeated_corrections_keep_oldest_clock_until_confirmed_receipt(release_state):
    from test_identity_journal import decision

    from swingset.schedule.cycle import versions
    from swingset.state.identity_journal import encode_journal
    from swingset.state.inputs import accept, capture
    from swingset.state.publication_report import publication_report

    f = release_state
    negative = decision(
        f.binding,
        "different_person",
        identifier="oldest-correction",
        supersedes=f.positive.decision_id,
    )
    (f.overrides / "identity_overrides.csv").write_bytes(encode_journal((f.positive, negative)))
    f.bundle = capture(f.config, f.overrides, f.db.state_dir, versions())
    accept(f.db, f.bundle, f.clock)
    first = f.clock.now().isoformat()
    f.clock.sleep(60)
    (f.overrides / "suppressions.csv").write_text(
        "wsdc_id,name_norm,reason,date\n999999,,Private note,2026-09-13\n"
    )
    f.bundle = capture(f.config, f.overrides, f.db.state_dir, versions())
    accept(f.db, f.bundle, f.clock)
    report = publication_report(f.conn, f.db.state_dir, f.clock.now())
    assert report["correction"]["detected_at"] == first
    assert report["correction"]["age_seconds"] == 60
    candidate = service.build_release(f.db, f.bundle, f.clock, f.run, f.hub, correction_only=True)
    assert publish(f.db.state_dir, candidate.path, f.hub).state == "published"
    f.clock.sleep(60)
    (f.overrides / "suppressions.csv").write_text(
        "wsdc_id,name_norm,reason,date\n999998,,Second private note,2026-09-13\n"
    )
    f.bundle = capture(f.config, f.overrides, f.db.state_dir, versions())
    accept(f.db, f.bundle, f.clock)
    report = publication_report(f.conn, f.db.state_dir, f.clock.now())
    assert report["correction"]["detected_at"] == f.clock.now().isoformat()
    assert report["correction"]["age_seconds"] == 0
    assert "private note" not in json.dumps(report).lower()


def test_correction_clock_uses_exact_generations_and_receipts_without_inventing_old_history():
    import sqlite3

    from swingset.state.correction_age import correction_age

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE identity_journal_acceptances(accepted_at TEXT,generation INTEGER); CREATE TABLE identity_reference_migrations(recorded_at TEXT); CREATE TABLE admission_decisions(decided_at TEXT,state TEXT);"
        )
        conn.execute(
            "INSERT INTO identity_journal_acceptances VALUES ('2026-09-13T07:01:00+00:00',2)"
        )
        unknown = correction_age(
            conn,
            baseline_receipt={"commit": "old"},
            baseline_policy={},
            now=datetime(2026, 9, 13, 7, 10, tzinfo=UTC),
        )
        assert unknown == {"detected_at": None, "age_seconds": None, "basis": "unknown"}
        conn.execute(
            "INSERT INTO identity_journal_acceptances VALUES ('2026-09-13T07:05:00+00:00',4)"
        )
        conn.execute(
            "INSERT INTO identity_reference_migrations VALUES ('2026-09-13T07:03:00+00:00')"
        )
        conn.execute(
            "INSERT INTO admission_decisions VALUES ('2026-09-13T07:04:00+00:00','revoked')"
        )
        conn.executemany(
            "INSERT INTO meta VALUES (?,?)",
            (
                ("correction_pending_since", "2026-09-13T07:02:00+00:00"),
                ("correction_pending_baseline_commit", "old"),
            ),
        )
        arguments = {
            "baseline_receipt": {"commit": "old", "verified_at": "2026-09-13T07:00:00+00:00"},
            "baseline_policy": {"token": {"journal_generation": 3}},
            "now": datetime(2026, 9, 13, 7, 10, tzinfo=UTC),
        }
        assert correction_age(conn, **arguments) == {
            "detected_at": "2026-09-13T07:02:00+00:00",
            "age_seconds": 480.0,
            "basis": "recorded_pending_corrections",
        }
        conn.execute(
            "UPDATE meta SET value='previous' WHERE key='correction_pending_baseline_commit'"
        )
        assert correction_age(conn, **arguments)["detected_at"] == "2026-09-13T07:03:00+00:00"
    finally:
        conn.close()
