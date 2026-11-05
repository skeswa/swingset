"""Restore rehydrates accepted caches without replaying immutable decisions."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_postgres_schema import fresh_database  # noqa: F401

from swingset.admission.generations import deserialize_result
from swingset.project.registry import project_dancer
from swingset.sources.base import ParseContext
from swingset.state import postgres_schema as schema
from swingset.state.db import open_database
from swingset.state.engine import write_settings
from swingset.state.postgres_rebuild import rehydrate_observations

NOW = "2026-11-04T00:00:00+00:00"


def seed_registry(conn, *, authority="none"):
    schema.migrate(conn)
    conn.execute("INSERT INTO ops.runs(run_id,started_at,dry_run) VALUES('cache-run',%s,0)", (NOW,))
    conn.execute(
        'INSERT INTO "fetch".watches(watch_id,source,kind,method,url,parser,source_ref,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
        (
            "cache-watch",
            "wsdc_registry",
            "dancer",
            "POST",
            "https://example.test/registry",
            "wsdc_registry.dancer",
            "wsdc:10",
            "registry",
        ),
    )
    conn.execute(
        "INSERT INTO evidence.source_units(unit_key,watch_id,page_kind) VALUES('cache-unit','cache-watch','wsdc_registry.dancer')"
    )
    fixture = json.loads(Path("tests/fixtures/postgres/production37_goldens.json").read_text())
    template = json.loads(
        next(
            row["result_json"]
            for row in fixture["rows"]["source_generations"]
            if row["page_kind"] == "wsdc_registry.dancer"
        )
    )
    for number, outcome in ((1, "found"), (2, "not_found")):
        snapshot = "cache-snapshot-" + str(number)
        conn.execute(
            "INSERT INTO \"fetch\".snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES(%s,'cache-watch','POST','https://example.test/registry',%s,200,0,1,'cache-run','Ok')",
            (snapshot, f"2026-11-0{number}T00:00:00+00:00"),
        )
        context = ParseContext(
            snapshot,
            "cache-watch",
            "https://example.test/registry",
            "wsdc_registry",
            "wsdc_registry.dancer",
            "wsdc:10",
            f"2026-11-0{number}T00:00:00+00:00",
        )
        from dataclasses import asdict

        recipe = json.dumps(
            {
                "context": asdict(context),
                "extract_version": "1",
                "parser_version": "1",
                "accepted_inputs": [],
            },
            separators=(",", ":"),
        )
        value = copy.deepcopy(template)
        value["observations"][0]["scope"] = {"kind": "dancer", "ref": "10"}
        value["observations"][0]["payload"].update(
            outcome=outcome,
            wsdc_id="10" if outcome == "found" else None,
            first_name="Fixture" if outcome == "found" else None,
            last_name="Dancer" if outcome == "found" else None,
        )
        encoded = json.dumps(value, separators=(",", ":"))
        assert len(deserialize_result(encoded).observations) == 1
        generation = "cache-generation-" + str(number)
        conn.execute(
            "INSERT INTO evidence.source_generations(generation_id,unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,run_id,state,removal_authority) VALUES(%s,'cache-unit','wsdc_registry.dancer','1',%s,'[]',%s,%s,'{}',%s,'cache-run','accepted',%s)",
            (generation, "fingerprint-" + str(number), recipe, encoded, NOW, authority),
        )
        conn.execute(
            "INSERT INTO evidence.admission_decisions(generation_id,state,reason,decided_at,policy_revision) VALUES(%s,'accepted','contract_passed',%s,'fixture')",
            (generation, NOW),
        )
    conn.execute(
        "UPDATE evidence.source_units SET accepted_generation_id='cache-generation-2' WHERE unit_key='cache-unit'"
    )
    conn.execute(
        "UPDATE \"fetch\".watches SET current_observation_snapshot_id='cache-snapshot-2' WHERE watch_id='cache-watch'"
    )


def test_rehydrate_keeps_older_found_identity_after_later_missing_lookup(fresh_database, tmp_path):  # noqa: F811
    service, name, conn = fresh_database
    seed_registry(conn)
    state = tmp_path / "state"
    write_settings(
        state, service.dsn(name, role="worker"), control_dsn=service.dsn(name, role="control")
    )
    with open_database(state) as database:
        result = rehydrate_observations(database, enqueued_at=NOW)
        assert result["written_generations"] == 2
        assert result["observations"] == 2
        assert len(project_dancer(database.connection, "10", NOW, "cache-run").rows) == 1
        assert (
            database.connection.execute("SELECT count(*) FROM admission_decisions").fetchone()[0]
            == 2
        )
        assert database.connection.execute("SELECT count(*) FROM findings").fetchone()[0] == 0
        assert (
            database.connection.execute(
                "SELECT current_observation_snapshot_id FROM watches"
            ).fetchone()[0]
            == "cache-snapshot-2"
        )
        assert rehydrate_observations(database, enqueued_at=NOW)["unchanged_generations"] == 2


def test_watch_owned_source_rehydrates_only_selected_generation(fresh_database, tmp_path):  # noqa: F811
    service, name, conn = fresh_database
    seed_registry(conn, authority="watch")
    state = tmp_path / "state"
    write_settings(state, service.dsn(name, role="worker"))
    with open_database(state) as database:
        assert rehydrate_observations(database, enqueued_at=NOW)["observations"] == 1
        assert (
            database.connection.execute("SELECT snapshot_id FROM observations").fetchone()[0]
            == "cache-snapshot-2"
        )
    with open_database(state, lock=False) as unlocked:
        with pytest.raises(RuntimeError, match="exclusive"):
            rehydrate_observations(unlocked, enqueued_at=NOW)


def test_restore_cache_certificate_survives_crash_without_losing_cutoff_witness(
    fresh_database,  # noqa: F811
    tmp_path,  # noqa: F811
):  # noqa: F811
    from datetime import datetime
    from types import SimpleNamespace

    from swingset.clock import FakeClock
    from swingset.fetch.archive import Archive
    from swingset.project.materialization import helper_recipe
    from swingset.project.process import process_unit
    from swingset.schedule.derive import derive_one
    from swingset.state import derivations, queue
    from swingset.state.postgres_rebuild import invalidate_materializations
    from swingset.state.verify import _live_label
    from swingset.state.work import WorkUnit, next_work

    service, name, conn = fresh_database
    seed_registry(conn)
    state = tmp_path / "state"
    write_settings(state, service.dsn(name, role="worker"))
    unit = WorkUnit("project", "dancer", "10")
    bundle = SimpleNamespace(files={})
    clock = FakeClock(datetime.fromisoformat(NOW))
    with open_database(state) as database:
        rehydrate_observations(database, enqueued_at=NOW)
        process_unit(database, unit, bundle, clock, "cache-run")
        expected = _live_label(database.connection, unit)
        original = database.connection.execute(
            "SELECT generation_id,previous_generation_id,output_digest,row_count,created_at,run_id FROM derivation_generations ORDER BY generation_id"
        ).fetchone()
        assert original is not None
        fingerprint = database.connection.execute(
            "SELECT input_fingerprint FROM derivation_generations WHERE generation_id=%s",
            (original[0],),
        ).fetchone()[0]
        with database.transaction() as tx:
            tx.execute("DELETE FROM dancers WHERE wsdc_id='10'")
            tx.execute("UPDATE derivation_scopes SET materialized_signature=NULL")
        # Ordinary opens retain the legacy fingerprint fallback.
        assert derivations.current_for(
            database.connection, unit, recipe=helper_recipe(database.connection, {}, "project")
        )
    (state / "RESTORE_PENDING").write_text("cache replay is not complete\n")
    with pytest.raises(RuntimeError, match="restore verification"):
        open_database(state)
    with open_database(state, allow_restore_pending=True) as database:
        result = invalidate_materializations(database, enqueued_at=NOW)
        assert result["invalidated_anchors"] == 1
        assert derivations.selected_generation(database.connection, unit) == original[0]
        assert not derivations.current(database.connection, unit)
        assert not derivations.materialized_for(
            database.connection,
            (unit.stage, unit.unit_kind, unit.unit_id),
            original[0],
            fingerprint,
        )
        with database.reader() as reader:
            assert reader.restore_pending
            assert not derivations.current(reader, unit)
            assert not derivations.current_for(
                reader, unit, recipe=helper_recipe(reader, {}, "project")
            )
        # A backing-off cold cache cannot count as a settled upstream pointer.
        database.connection.execute(
            "INSERT INTO work_queue(stage,unit_kind,unit_id,partition,state,enqueued_at,updated_at,next_attempt_at) VALUES(%s,%s,%s,%s,'waiting_input',%s,%s,'2026-11-06T00:00:00Z')",
            (unit.stage, unit.unit_kind, unit.unit_id, queue.partition_of(unit), NOW, NOW),
        )
        assert not derivations.ready(database.connection, WorkUnit("project", "inventory", "all"))
        database.connection.execute("DELETE FROM work_queue")
    # Reopening after a crash still sees the durable marker and NULL certificate.
    clock.sleep(86_400)
    with open_database(state, allow_restore_pending=True) as database:
        assert database.connection.restore_pending
        assert derivations.selected_generation(database.connection, unit) == original[0]
        assert not derivations.current(database.connection, unit)
        assert (
            next_work(
                database.connection,
                "project",
                now=clock.now(),
                unit_kind="dancer",
                allowed=lambda candidate: candidate == unit,
            )
            == unit
        )
        run = database.start_run(clock.now())
        outcome = derive_one(database, Archive(state), unit, bundle, clock, run)
        assert not outcome.failed and outcome.reason is None
        assert derivations.current(database.connection, unit)
        assert derivations.selected_generation(database.connection, unit) == original[0]
        assert _live_label(database.connection, unit) == expected
        assert database.connection.execute(
            "SELECT generation_id,previous_generation_id,output_digest,row_count,created_at,run_id FROM derivation_generations ORDER BY generation_id"
        ).fetchall() == [original]


@pytest.mark.parametrize("changed_parser", [False, True])
@pytest.mark.parametrize("resumable", [False, True])
def test_verified_approved_source_rekey_keeps_generation_and_newer_unapproved_hint(
    tmp_path, monkeypatch, changed_parser, resumable
):
    import time
    from dataclasses import replace
    from types import SimpleNamespace

    from test_admission import Corpus

    from swingset.sources.eepro.adapter import RoundPage
    from swingset.state.postgres_rebuild import _reconcile_approved_sources

    with open_database(tmp_path) as database:
        corpus = Corpus(database)
        accepted = corpus.snapshot("approved-source")
        generation, _ = corpus.stage(accepted)
        corpus.review(generation)
        assert corpus.admit(generation) == "accepted"
        newer = corpus.snapshot("newer-unapproved", body=b"<html>unapproved response</html>")
        key = str(database.connection.execute("SELECT unit_key FROM source_units").fetchone()[0])
        original_hint = database.connection.execute(
            "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=%s",
            (newer.snapshot_id,),
        ).fetchone()[0]
        original_decisions = database.connection.execute(
            "SELECT * FROM admission_decisions ORDER BY decision_id"
        ).fetchall()
        original_generations = database.connection.execute(
            "SELECT generation_id FROM source_generations ORDER BY generation_id"
        ).fetchall()
        if changed_parser:
            original_parse = RoundPage.parse

            def changed(self, extract, context):
                result = original_parse(self, extract, context)
                return replace(result, observations=())

            monkeypatch.setattr(RoundPage, "parse", changed)
            with pytest.raises(RuntimeError, match="differs"):
                _reconcile_approved_sources(
                    database,
                    {key},
                    bundle=SimpleNamespace(digest="fixture-approved-bundle"),
                    clock=corpus.clock,
                    run_id=corpus.run,
                    deadline=time.monotonic() + 30,
                    resume=resumable,
                )
            assert (
                database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[
                    0
                ]
                == 0
            )
        else:
            proof = _reconcile_approved_sources(
                database,
                {key},
                bundle=SimpleNamespace(digest="fixture-approved-bundle"),
                clock=corpus.clock,
                run_id=corpus.run,
                deadline=time.monotonic() + 30,
                resume=resumable,
            )
            assert proof["complete"] and proof["comparisons"] == 2
            assert (
                database.connection.execute(
                    "SELECT generation_id FROM source_equivalences"
                ).fetchone()[0]
                == generation
            )
        assert (
            database.connection.execute(
                "SELECT accepted_generation_id FROM source_units"
            ).fetchone()[0]
            == generation
        )
        assert (
            database.connection.execute(
                "SELECT generation_id FROM source_generations ORDER BY generation_id"
            ).fetchall()
            == original_generations
        )
        assert (
            database.connection.execute(
                "SELECT * FROM admission_decisions ORDER BY decision_id"
            ).fetchall()
            == original_decisions
        )
        assert (
            database.connection.execute(
                "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=%s",
                (newer.snapshot_id,),
            ).fetchone()[0]
            == original_hint
        )


def test_legacy_calendar_observation_survives_backup_and_ordinary_reprojection(
    fresh_database,  # noqa: F811
    tmp_path,
):  # noqa: F811
    import uuid
    from types import SimpleNamespace

    from psycopg import sql

    from swingset.clock import FakeClock
    from swingset.fetch.archive import Archive
    from swingset.project.process import process_unit
    from swingset.sources.base import Observation, ObservationScope, ParseResult
    from swingset.sources.records import CalendarRow
    from swingset.state import postgres_backup as backup
    from swingset.state.observations import observation_rows
    from swingset.state.postgres_rebuild import bootstrap
    from swingset.state.verify import _live_label
    from swingset.state.work import WorkUnit

    service, source_name, conn = fresh_database
    seed_registry(conn)
    state = tmp_path / "source"
    write_settings(state, service.dsn(source_name, role="worker"))
    archive = Archive(state)
    raw_body = b"<html>retained legacy calendar</html>"
    body = archive.store_body(raw_body)
    conn.execute(
        "INSERT INTO \"fetch\".watches(watch_id,source,kind,method,url,parser,state) VALUES('legacy-watch','wsdc_calendar','index','GET','https://example.test/calendar','wsdc_calendar.events','live')"
    )
    conn.execute(
        "INSERT INTO \"fetch\".snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES('legacy-snapshot','legacy-watch','GET','https://example.test/calendar',%s,200,%s,%s,1,'cache-run','Ok')",
        (NOW, body, len(raw_body)),
    )
    conn.execute(
        "INSERT INTO evidence.source_units(unit_key,watch_id,page_kind,legacy_snapshot_id,legacy_state) VALUES('legacy-unit','legacy-watch','wsdc_calendar.events','legacy-snapshot','legacy_unassessed')"
    )
    context = ParseContext(
        "legacy-snapshot",
        "legacy-watch",
        "https://example.test/calendar",
        "wsdc_calendar",
        "wsdc_calendar.events",
        None,
        NOW,
    )
    payload = CalendarRow(
        "calendar_row",
        "Legacy Reunion",
        "2026-06-01",
        "2026-06-03",
        "Trial",
        "Example City",
        None,
        "US",
        (),
    )
    rows = observation_rows(
        context,
        ParseResult((Observation(ObservationScope("calendar", "wsdc"), payload.kind, payload),)),
        extract_version="1",
        parser_version="1",
    )
    for row in rows:
        conn.execute(
            "INSERT INTO evidence.observations(observation_id,watch_id,snapshot_id,kind,scope_kind,scope_id,seq,extract_version,parser_version,payload_json) VALUES("
            + ",".join("%s" for _ in row)
            + ")",
            row,
        )
    clock = FakeClock()
    bundle = SimpleNamespace(files={})
    unit = WorkUnit("project", "calendar", "wsdc")
    with open_database(state) as database:
        rehydrate_observations(database, enqueued_at=NOW)
        process_unit(database, unit, bundle, clock, "cache-run")
        expected = _live_label(database.connection, unit)
        assert expected[1] > 0
        original = database.connection.execute(
            "SELECT * FROM observations WHERE watch_id='legacy-watch'"
        ).fetchall()
    target = tmp_path / "backup"
    backup.prepare(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        backup.export(
            conn,
            target,
            files={archive.blob_path(body).relative_to(state).as_posix(): archive.blob_path(body)},
            runtime_identity="legacy-fixture",
        )
    assert backup.verify(target)["tables"]["observations"]["rows"] == 1
    name = "sst_legacy_restore_" + uuid.uuid4().hex[:12]
    with service.connect() as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'").format(
                sql.Identifier(name)
            )
        )
    restored_state = tmp_path / "restored"
    try:
        with service.connect(name) as restored:
            assert backup.restore(
                target, restored, files_target=restored_state, runtime_identity="legacy-fixture"
            )["audit"]["equal"]
        write_settings(restored_state, service.dsn(name, role="worker"))
        (restored_state / "RESTORE_PENDING").write_text("legacy cache replay is pending\n")
        with open_database(restored_state, allow_restore_pending=True) as database:
            assert (
                database.connection.execute(
                    "SELECT * FROM observations WHERE watch_id='legacy-watch'"
                ).fetchall()
                == original
            )
            bootstrap(database, enqueued_at=NOW, inventory=backup.verify(target)["rebuild_units"])
            process_unit(database, unit, bundle, clock, "cache-run")
            assert _live_label(database.connection, unit) == expected
            assert (
                database.connection.execute(
                    "SELECT * FROM observations WHERE watch_id='legacy-watch'"
                ).fetchall()
                == original
            )
            assert tuple(
                database.connection.execute(
                    "SELECT accepted_generation_id,legacy_state FROM source_units WHERE unit_key='legacy-unit'"
                ).fetchone()
            ) == (None, "legacy_unassessed")
            assert (
                database.connection.execute("SELECT count(*) FROM source_generations").fetchone()[0]
                == 2
            )
            assert backup.audit(database.connection, target, after_rebuild=True)["equal"]
    finally:
        with service.connect() as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(name)))


@pytest.mark.parametrize("resumable", [False, True])
def test_approved_source_rekey_refuses_declared_bump_even_when_bytes_match(tmp_path, resumable):
    import time
    from types import SimpleNamespace

    from test_admission import Corpus

    from swingset.state.postgres_rebuild import _reconcile_approved_sources

    with open_database(tmp_path) as database:
        corpus = Corpus(database)
        context = corpus.snapshot("approved-before-version-bump")
        generation, _ = corpus.stage(context)
        corpus.review(generation)
        assert corpus.admit(generation) == "accepted"
        key = str(database.connection.execute("SELECT unit_key FROM source_units").fetchone()[0])
        database.connection.execute(
            "INSERT INTO accepted_inputs(consumer,input_name,digest) VALUES('pipeline','version/parser/eepro.round','999')"
        )
        with pytest.raises(RuntimeError, match="declared transform changed"):
            _reconcile_approved_sources(
                database,
                {key},
                bundle=SimpleNamespace(digest="fixture-approved"),
                clock=corpus.clock,
                run_id=corpus.run,
                deadline=time.monotonic() + 30,
                resume=resumable,
            )
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 0
        )
        assert (
            database.connection.execute(
                "SELECT accepted_generation_id FROM source_units"
            ).fetchone()[0]
            == generation
        )


@pytest.fixture
def source_resume_case(fresh_database, tmp_path, monkeypatch):  # noqa: F811
    from dataclasses import replace
    from types import SimpleNamespace

    from test_admission import Corpus

    from swingset.schedule.watches import upsert_watch
    from swingset.state import recipes

    service, name, owner = fresh_database
    schema.migrate(owner)
    write_settings(
        tmp_path, service.dsn(name, role="worker"), control_dsn=service.dsn(name, role="control")
    )
    with open_database(tmp_path) as database:
        corpus = Corpus(database)
        for number in range(2):
            spec = replace(corpus.spec, url=corpus.spec.url + "?source=" + str(number))
            upsert_watch(database.connection, spec, corpus.clock.now())
            context = corpus.snapshot("resume-source-" + str(number), spec=spec)
            generation, _ = corpus.stage(context)
            corpus.review(generation)
            assert corpus.admit(generation) == "accepted"
        keys = {
            str(row[0]) for row in database.connection.execute("SELECT unit_key FROM source_units")
        }
        assert len(keys) == 2
    # Other agents may edit unrelated source while these transaction tests run.
    # This fixture deliberately fixes the candidate bytes, and mutation tests
    # explicitly replace that capture to exercise refusal.
    runtime = recipes.capture_runtime()
    monkeypatch.setattr(recipes, "capture_runtime", lambda: runtime)
    (tmp_path / "RESTORE_PENDING").write_text("source proof is incomplete\n")
    return SimpleNamespace(
        state=tmp_path,
        owner=owner,
        keys=keys,
        clock=corpus.clock,
        run_id=corpus.run,
        bundle=SimpleNamespace(digest="fixture-approved-source-bundle"),
        runtime=runtime,
    )


def source_turn(case, database, *, deadline=float("inf"), stop=None):
    from swingset.state.postgres_rebuild import _reconcile_approved_sources

    return _reconcile_approved_sources(
        database,
        case.keys,
        bundle=case.bundle,
        clock=case.clock,
        run_id=case.run_id,
        deadline=deadline,
        resume=True,
        stop=stop,
    )


def source_progress(database):
    row = database.connection.execute(
        "SELECT run_id,summary_json FROM ops.runs WHERE run_id LIKE 'restore_source:%%' ORDER BY started_at,run_id"
    ).fetchall()
    return [(str(item[0]), json.loads(item[1])) for item in row]


def one_source_budget(monkeypatch):
    from swingset.state import verify_graph

    original = verify_graph._source
    checked = []

    def observe(*args):
        result = original(*args)
        checked.append(result["unit"])
        return result

    class Deadline:
        def __gt__(self, current):
            return len(checked) < 2

    monkeypatch.setattr(verify_graph, "_source", observe)
    return Deadline(), checked


def test_source_proof_resumes_after_restart_and_completed_turn_is_idempotent(
    source_resume_case, monkeypatch
):
    import signal

    case = source_resume_case
    budget, checked = one_source_budget(monkeypatch)
    timer, handler = signal.getitimer(signal.ITIMER_REAL), signal.getsignal(signal.SIGALRM)
    with open_database(case.state, allow_restore_pending=True) as database:
        first = source_turn(case, database, deadline=budget)
        assert not first["complete"] and first["completed_units"] == first["cursor"] == 1
        assert first["comparisons"] == 2
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 1
        )
        assert source_progress(database)[0][1]["cursor"] == 1
        assert database.connection._write_deadline is None
    with open_database(case.state, allow_restore_pending=True) as database:
        final = source_turn(case, database)
        assert final["complete"] and final["completed_units"] == 2
        assert final["comparisons"] == 4 and len(checked) == 4
        assert final["source_proof_hash_format"] == "swingset-source-proof-chain-v1"
        assert "exact_source_comparisons_sha256" not in final
        assert source_turn(case, database) == final
        assert len(checked) == 4
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 2
        )
        assert (
            database.connection.execute("SELECT count(*) FROM source_generations").fetchone()[0]
            == 2
        )
        assert (
            database.connection.execute("SELECT count(*) FROM admission_decisions").fetchone()[0]
            == 2
        )
        assert database.connection._write_deadline is None
    assert signal.getitimer(signal.ITIMER_REAL) == timer
    assert signal.getsignal(signal.SIGALRM) is handler


@pytest.mark.parametrize(
    "mutation",
    [
        "cursor",
        "comparisons",
        "completed_keys_sha256",
        "chain_sha256",
        "pointer",
        "inputs",
        "runtime",
    ],
)
def test_resumed_source_proof_refuses_changed_binding_or_inconsistent_progress(
    source_resume_case, monkeypatch, mutation
):
    from swingset.state import recipes

    case = source_resume_case
    budget, checked = one_source_budget(monkeypatch)
    with open_database(case.state, allow_restore_pending=True) as database:
        assert not source_turn(case, database, deadline=budget)["complete"]
        ledger_id, progress = source_progress(database)[0]
        if mutation == "pointer":
            database.connection.execute(
                "UPDATE evidence.source_units SET accepted_generation_id=NULL WHERE unit_key=%s",
                (sorted(case.keys)[0],),
            )
        elif mutation == "inputs":
            database.connection.execute(
                "INSERT INTO accepted_inputs(consumer,input_name,digest) VALUES('pipeline','fixture-changed','changed')"
            )
        elif mutation == "runtime":
            changed = {**case.runtime, "recipes/runtime.json": b"different candidate"}
            monkeypatch.setattr(recipes, "capture_runtime", lambda: changed)
        else:
            progress[mutation] = 99 if mutation in {"cursor", "comparisons"} else "invalid"
            database.connection.execute(
                "UPDATE ops.runs SET summary_json=%s WHERE run_id=%s",
                (json.dumps(progress), ledger_id),
            )
        with pytest.raises(RuntimeError, match="binding|progress|required source"):
            source_turn(case, database)
        assert len(checked) == 2
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 1
        )


def test_source_progress_and_equivalences_roll_back_together_on_commit_path_failure(
    source_resume_case,
):
    from swingset.state import engine

    case = source_resume_case
    case.owner.execute(
        "CREATE FUNCTION ops.fixture_source_crash() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.run_id LIKE 'restore_source:%' THEN RAISE EXCEPTION 'injected source progress failure'; END IF; RETURN NEW; END $$"
    )
    case.owner.execute(
        "CREATE TRIGGER fixture_source_crash BEFORE UPDATE ON ops.runs FOR EACH ROW EXECUTE FUNCTION ops.fixture_source_crash()"
    )
    with open_database(case.state, allow_restore_pending=True) as database:
        before = database.connection.execute(
            "SELECT unit_key,desired_fingerprint FROM source_units ORDER BY unit_key"
        ).fetchall()
        with pytest.raises(engine.Error, match="SQLSTATE P0001"):
            source_turn(case, database)
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 0
        )
        assert source_progress(database)[0][1]["cursor"] == 0
        assert (
            database.connection.execute(
                "SELECT unit_key,desired_fingerprint FROM source_units ORDER BY unit_key"
            ).fetchall()
            == before
        )
        case.owner.execute("DROP TRIGGER fixture_source_crash ON ops.runs")
        assert source_turn(case, database)["complete"]


def test_crash_after_committed_source_chunk_resumes_without_repeating_it(
    source_resume_case, monkeypatch
):
    from contextlib import contextmanager

    from swingset.state import verify_graph

    case = source_resume_case
    with open_database(case.state, allow_restore_pending=True) as database:
        transaction = database.transaction

        @contextmanager
        def crash_after_commit():
            with transaction() as conn:
                yield conn
            if source_progress(database)[0][1]["cursor"]:
                raise RuntimeError("injected after source commit")

        with monkeypatch.context() as patch:
            patch.setattr(database, "transaction", crash_after_commit)
            with pytest.raises(RuntimeError, match="after source commit"):
                source_turn(case, database)
        assert source_progress(database)[0][1]["cursor"] == 2
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 2
        )
    monkeypatch.setattr(
        verify_graph, "_source", lambda *args: pytest.fail("committed source was repeated")
    )
    with open_database(case.state, allow_restore_pending=True) as database:
        assert source_turn(case, database)["complete"]


def test_fresh_destination_uuid_never_adopts_restored_source_progress(
    source_resume_case, monkeypatch
):
    import uuid

    case = source_resume_case
    budget, checked = one_source_budget(monkeypatch)
    with open_database(case.state, allow_restore_pending=True) as database:
        assert not source_turn(case, database, deadline=budget)["complete"]
        previous = source_progress(database)[0][0]
        schema.seed_environment(case.owner, environment="dev", database_id=str(uuid.uuid4()))
        assert source_turn(case, database)["complete"]
        ledgers = source_progress(database)
        assert len(ledgers) == 2
        assert next(row[1]["cursor"] for row in ledgers if row[0] == previous) == 1
        assert next(row[1]["cursor"] for row in ledgers if row[0] != previous) == 2
        assert len(checked) == 6


def test_runtime_change_before_chunk_commit_cannot_advance_source_progress(
    source_resume_case, monkeypatch
):
    from swingset.state import verify_graph

    case = source_resume_case
    original = verify_graph._source

    def change_after_proof(*args):
        result = original(*args)
        case.runtime["recipes/runtime.json"] = b"changed"
        return result

    monkeypatch.setattr(verify_graph, "_source", change_after_proof)
    with open_database(case.state, allow_restore_pending=True) as database:
        with pytest.raises(RuntimeError, match="code, inputs or pointers changed"):
            source_turn(case, database)
        assert source_progress(database)[0][1]["cursor"] == 0
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("phase", ["source", "derive"])
@pytest.mark.parametrize("stop_requested", [False, True])
def test_rebuild_budget_or_stop_cannot_start_another_derivation(
    source_resume_case, monkeypatch, phase, stop_requested
):
    from swingset.schedule import derive
    from swingset.state import inputs, postgres_rebuild, work

    case = source_resume_case
    required = {work.WorkUnit("project", "dancer", "1")}
    monkeypatch.setattr(inputs, "accept", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        postgres_rebuild,
        "_required_closure",
        lambda *args: (required, case.keys if phase == "source" else set()),
    )
    monkeypatch.setattr(
        derive, "derive_one", lambda *args: pytest.fail("unproved input reached a derivation")
    )
    monkeypatch.setattr(
        work, "next_work", lambda *args, **kwargs: pytest.fail("unproved input reached selection")
    )
    with open_database(case.state, allow_restore_pending=True) as database:
        report = postgres_rebuild.rebuild(
            database,
            case.bundle,
            case.clock,
            seconds=30 if stop_requested else 0.000001,
            bootstrap_cache=False,
            inventory=[],
            resume_source=True,
            stop=lambda: stop_requested,
        )
        assert not report["complete"]
        assert report["reason"] == (
            "rebuild_stopped" if stop_requested else "rebuild_budget_exhausted"
        )
        assert report["processed_units"] == 0 and report["remaining_units"] == 1
        assert report["approved_source_proof"]["cursor"] == 0
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 0
        )


def test_stop_flushes_only_verified_source_keys_and_next_turn_continues(
    source_resume_case, monkeypatch
):
    case = source_resume_case
    _, checked = one_source_budget(monkeypatch)
    with open_database(case.state, allow_restore_pending=True) as database:
        first = source_turn(case, database, stop=lambda: len(checked) >= 2)
        assert not first["complete"] and first["stopped"]
        assert first["cursor"] == 1 and first["comparisons"] == 2
        assert source_progress(database)[0][1]["cursor"] == 1
        assert (
            database.connection.execute("SELECT count(*) FROM source_equivalences").fetchone()[0]
            == 1
        )
        assert source_turn(case, database)["complete"]
        assert len(checked) == 4


@pytest.mark.parametrize("unpublished_change", [False, True])
def test_empty_restore_rebuild_preserves_public_bytes_and_unpublished_generation_receipt(
    fresh_database,  # noqa: F811
    tmp_path,
    unpublished_change,
):
    import shutil
    import uuid
    from datetime import UTC, datetime

    from psycopg import sql
    from publish.test_recovery import FakeHub
    from test_admission import Corpus

    from swingset.backup.checkpoint import (
        activate_restored_state,
        create_checkpoint,
        restore_checkpoint,
        verify_restored_public,
    )
    from swingset.build.service import build_release
    from swingset.clock import FakeClock
    from swingset.fetch.archive import Archive
    from swingset.publish.service import publish
    from swingset.schedule.derive import derive_one
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.wsdc_registry.adapter import SOURCE
    from swingset.state import derivations
    from swingset.state.inputs import accept, capture
    from swingset.state.work import WorkUnit
    from swingset.versions import versions

    class RetainedHub(FakeHub):
        def create_commit(self, *, parent, message, additions, deletions):
            if getattr(self, "fail_before_commit", False):
                self.fail_before_commit = False
                raise ConnectionError("fixture commit did not land")
            commit = super().create_commit(
                parent=parent, message=message, additions=additions, deletions=deletions
            )
            for name, path in additions.items():
                target = tmp_path / "remote" / commit / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            return commit

        def download(self, name, commit):
            return tmp_path / "remote" / commit / name

    config, overrides = tmp_path / "inputs/config", tmp_path / "inputs/overrides"
    config.mkdir(parents=True)
    overrides.mkdir(parents=True)
    (config / "hosts.toml").write_text('[hosts."points.worldsdc.com"]\n')
    (config / "sources.toml").write_text("[sources.wsdc_registry]\nenabled=true\n")
    for original in Path("overrides").glob("*.csv"):
        (overrides / original.name).write_text(original.read_text().splitlines()[0] + "\n")
    service, source_name, initial = fresh_database
    schema.migrate(initial)
    source = tmp_path / "source"
    write_settings(
        source,
        service.dsn(source_name, role="worker"),
        control_dsn=service.dsn(source_name, role="control"),
    )
    clock = FakeClock(datetime(2026, 11, 4, 1, tzinfo=UTC))
    hub = RetainedHub(None)
    unit = WorkUnit("project", "dancer", "1")
    body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
    checkpoint = tmp_path / "checkpoint"
    with open_database(source) as database:
        bundle = capture(config, overrides, source, versions())
        # A legacy input disappears on ordinary scoped-bundle acceptance.
        # Its explicit absence remains a semantic recipe input, not a cache.
        database.connection.execute(
            "INSERT INTO accepted_inputs(consumer,input_name,digest) VALUES('pipeline','recipe/runtime',%s)",
            ("a" * 64,),
        )
        accept(database, bundle, clock)
        absent_runtime = hashlib.sha256(b"").hexdigest()
        assert (
            database.connection.execute(
                "SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'"
            ).fetchone()[0]
            == absent_runtime
        )
        corpus = Corpus(database)
        corpus.clock = clock
        corpus.run = database.start_run(clock.now())
        spec = SOURCE.watch(1)
        upsert_watch(database.connection, spec, clock.now())
        context = corpus.snapshot("original-registry-body", body=body, spec=spec)
        generation, report = corpus.stage(context, body=body)
        assert not report.failures
        corpus.review(generation)
        assert corpus.admit(generation) == "accepted"
        outcome = derive_one(database, Archive(source), unit, bundle, clock, corpus.run)
        assert not outcome.failed and outcome.reason is None
        expected = build_release(database, bundle, clock, corpus.run, hub)
        assert (
            publish(source, expected.path, hub, connection=database.connection).state == "published"
        )
        if unpublished_change:
            # New approved facts after the public baseline have their own receipt.
            # Rebuild must preserve that receipt, rather than copy baseline clocks.
            clock.sleep(300)
            corpus.run = database.start_run(clock.now())
            changed = json.loads(body)
            for role in ("leader", "follower"):
                dancer = changed.get(role, {}).get("dancer")
                if dancer is not None:
                    dancer["first_name"] = "Unpublished fixture name"
            changed_body = json.dumps(changed).encode()
            context = corpus.snapshot("unpublished-registry-body", body=changed_body, spec=spec)
            generation, report = corpus.stage(context, body=changed_body)
            assert not report.failures and corpus.admit(generation) == "accepted"
            outcome = derive_one(database, Archive(source), unit, bundle, clock, corpus.run)
            assert not outcome.failed and outcome.reason is None
            expected = build_release(database, bundle, clock, corpus.run, hub)
            assert not (expected.path / "PUBLISHED").exists()
            hub.fail_before_commit = True
            with pytest.raises(ConnectionError, match="fixture commit did not land"):
                publish(source, expected.path, hub, connection=database.connection)
            assert (expected.path / "PUBLISHING").is_file()
        selected = derivations.selected_generation(database.connection, unit)
        original_receipt = tuple(
            database.connection.execute(
                "SELECT generation_id,created_at,run_id FROM derivation_generations WHERE generation_id=%s",
                (selected,),
            ).fetchone()
        )
        original_manifest = json.loads((expected.path / "_meta/manifest.json").read_bytes())
        build_receipt = database.connection.execute(
            "SELECT g.* FROM derivation_generations g JOIN derivation_rows r USING(generation_id) WHERE g.stage='build' AND r.table_name='artifact' AND r.record_key=%s",
            (expected.candidate_id,),
        ).fetchone()
        original_build_receipt = tuple(build_receipt)
        original_build_generation = build_receipt["generation_id"]
        retained_manifest_digest = json.loads(build_receipt["recipe_json"])["context"][
            "release_manifest"
        ]
        assert original_manifest["row_counts"]["dancers"] == 1
        assert original_manifest["row_counts"]["registry_placements"] > 0
        assert original_manifest["row_counts"]["changelog"] > 0
        create_checkpoint(
            source,
            database.connection,
            checkpoint,
            schema_version=schema.SCHEMA_VERSION,
            versions=json.loads(bundle.files["versions.json"]),
            input_bundle_hash=bundle.digest,
        )
    destination_name = "sst_public_receipt_" + uuid.uuid4().hex[:16]
    destination = tmp_path / "restored"
    with service.connect() as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'").format(
                sql.Identifier(destination_name)
            )
        )
    try:
        write_settings(
            destination,
            service.dsn(destination_name, role="worker"),
            control_dsn=service.dsn(destination_name, role="control"),
        )
        restore_checkpoint(
            checkpoint,
            destination,
            maximum_schema_version=schema.SCHEMA_VERSION,
            owner_dsn=service.dsn(destination_name),
        )
        clock.sleep(300)
        with open_database(destination, allow_restore_pending=True) as database:
            assert database.connection.execute("SELECT count(*) FROM dancers").fetchone()[0] == 0
            assert (
                database.connection.execute(
                    "SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'"
                ).fetchone()[0]
                == absent_runtime
            )
            build_generation = database.connection.execute(
                "SELECT materialized_generation_id FROM derivation_scopes WHERE stage='build' AND unit_kind='release' AND unit_id='all'"
            ).fetchone()[0]
            # Original files still exist; their historical absolute path cannot
            # certify a missing or rejected artifact in this different volume.
            from swingset.publish.safety import verify_candidate_files

            verify_candidate_files(expected.path)
            assert not derivations._artifact_valid(database.connection, build_generation)
            verify_restored_public(destination, hub, connection=database.connection)
            if unpublished_change:
                rejected = destination / "candidates" / expected.candidate_id
                assert (rejected / "REJECTED").is_file()
                shutil.copytree(expected.path, rejected, dirs_exist_ok=True)
                assert not derivations._artifact_valid(database.connection, build_generation)
            activate_restored_state(
                destination, connection=database.connection, locks=database._locks, clock=clock
            )
            assert not database.connection.restore_pending
            assert derivations.selected_generation(database.connection, unit) == selected
            assert (
                tuple(
                    database.connection.execute(
                        "SELECT generation_id,created_at,run_id FROM derivation_generations WHERE generation_id=%s",
                        (selected,),
                    ).fetchone()
                )
                == original_receipt
            )
            rebuilt = build_release(database, bundle, clock, database.start_run(clock.now()), hub)
            actual = json.loads((rebuilt.path / "_meta/manifest.json").read_bytes())
            if unpublished_change:
                assert rebuilt.candidate_id != expected.candidate_id
            assert (
                database.connection.execute(
                    "SELECT candidate_id FROM release_manifests WHERE manifest_digest=%s",
                    (retained_manifest_digest,),
                ).fetchone()[0]
                == rebuilt.candidate_id
            )
            assert (
                tuple(
                    database.connection.execute(
                        "SELECT * FROM derivation_generations WHERE generation_id=%s",
                        (original_build_generation,),
                    ).fetchone()
                )
                == original_build_receipt
            )
            assert actual["row_counts"] == original_manifest["row_counts"]
            assert actual["files"] == original_manifest["files"]
            assert actual["content_hash"] == original_manifest["content_hash"]
            from swingset.state import postgres_backup

            assert postgres_backup.audit(
                database.connection, checkpoint / "database", after_rebuild=True
            )["equal"]
            assert (
                database.connection.execute(
                    "SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'"
                ).fetchone()[0]
                == absent_runtime
            )
    finally:
        with service.connect() as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(destination_name))
            )
