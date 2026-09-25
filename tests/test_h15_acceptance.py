"""Independent H15 acceptance: immutable inputs, atomic output, and derived work."""

import json
import shutil
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_admission import BODY, Corpus

from swingset.model.canonical import Event
from swingset.project.writer import Projection, replace_scope, replace_source_event_map
from swingset.schedule.cycle import versions
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture
from swingset.state.recipes import capture_runtime, captured_recipe_inputs


def test_actual_code_and_schema_change_recipe_with_unchanged_human_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("SWINGSET_REVISION", "unchanged-human-label")
    package = tmp_path / "src/swingset"
    (package / "project").mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "unchanged"\n')
    (package / "project/__init__.py").write_text("")
    implementation = package / "project/process.py"
    implementation.write_text("PROJECTOR_VERSION = 19\nVALUE = 'before'\n")
    schema = package / "project/schema.sql"
    schema.write_text("CREATE TABLE example(value TEXT);\n")
    scope = "recipe/code/project/event"
    initial = capture_runtime(package)
    first = captured_recipe_inputs(initial, history_start="2010-01-01")
    implementation.write_text("PROJECTOR_VERSION = 19\nVALUE = 'after'\n")
    changed_code = captured_recipe_inputs(capture_runtime(package), history_start="2010-01-01")
    assert changed_code[scope] != first[scope]
    assert changed_code["recipe/env"] == first["recipe/env"]
    assert changed_code["policy/history_start"] == first["policy/history_start"]
    schema.write_text("CREATE TABLE example(value TEXT, revision INTEGER);\n")
    changed_schema = captured_recipe_inputs(capture_runtime(package), history_start="2010-01-01")
    assert changed_schema[scope] != changed_code[scope]
    # Retained old artifacts remain sufficient to reproduce their old recipe.
    assert captured_recipe_inputs(initial, history_start="2010-01-01") == first
    corrupted = dict(initial)
    corrupted["runtime/swingset/project/process.py"] = b"changed without a new manifest"
    with pytest.raises(ValueError, match="differs from its recipe"):
        captured_recipe_inputs(corrupted, history_start="2010-01-01")


@pytest.fixture
def source_fixture(tmp_path):
    config, overrides = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config)
    shutil.copytree("overrides", overrides)
    (config / "sources.toml").write_text("[sources.eepro]\nenabled=true\n")
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    aliases = overrides / "event_aliases.csv"
    aliases.write_text(
        "source,source_ref,event_id,note\neepro,eepro:test,event-a,Offline mapping\n"
    )
    with open_database(tmp_path / "state") as db:
        corpus = Corpus(db)
        body = BODY.replace(b"Bob Example", b"Diane Abele")
        ctx = corpus.snapshot("round-one", body)
        generation, report = corpus.stage(ctx, body=body)
        assert not report.failures
        corpus.review(generation)
        assert corpus.admit(generation) == "accepted"
        now = corpus.clock.now().isoformat()
        events = tuple(
            Event(
                event_id=identifier,
                series_id=identifier,
                name=identifier,
                year=2026,
                start_date="2026-01-01",
                end_date="2026-01-04",
                wsdc_status="registry",
                source="wsdc_calendar",
                snapshot_id="retained-calendar-baseline",
                parser_version="1",
                first_seen_at=now,
                last_seen_at=now,
                run_id=corpus.run,
            )
            for identifier in ("event-a", "event-b")
        )
        replace_scope(
            db.connection,
            scope_kind="fixture_inventory",
            scope_id="baseline",
            projection=Projection(events),
            run_id=corpus.run,
            projected_at=now,
        )
        replace_source_event_map(
            db.connection, (("eepro", "eepro:test", "event-a", "override", 1.0),)
        )
        bundle = capture(config, overrides, db.state_dir, versions())
        accept(db, bundle, corpus.clock)
        yield SimpleNamespace(
            db=db,
            conn=db.connection,
            corpus=corpus,
            generation=generation,
            body=body,
            bundle=bundle,
            config=config,
            overrides=overrides,
            aliases=aliases,
        )


def output_state(conn):
    """Independent row-level receipts, including associations and revisions."""
    tables = (
        "events",
        "source_event_map",
        "contests",
        "rounds",
        "entries",
        "judges",
        "placements",
        "callback_marks",
        "final_marks",
        "identity_links",
        "link_candidates",
        "revisions",
    )
    return {
        table: sorted(
            (tuple(row) for row in conn.execute("SELECT * FROM " + table)),
            key=lambda row: json.dumps(row, default=str),
        )
        for table in tables
    }


def test_dependency_manifests_and_materialized_generations_are_immutable(tmp_path):
    import hashlib
    import sqlite3

    from swingset.clock import FakeClock

    with open_database(tmp_path) as db:
        now = FakeClock().now()
        run = db.start_run(now)
        conn = db.connection
        manifest = '{"source_generations":[{"unit":"retained-source","generation":"selected-before"}],"captured_recipe":"before"}'
        with db.transaction():
            conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project','event','fixture',?)",
                (now.isoformat(),),
            )
            conn.execute(
                "INSERT INTO derivation_dependency_sets VALUES ('dependencies',?)", (manifest,)
            )
            conn.execute(
                "INSERT INTO derivation_generations VALUES ('generation','project','event','fixture','input-fingerprint','{}','dependencies',NULL,'output-digest',1,?,?)",
                (now.isoformat(), run),
            )
            payload = '{"name_raw":"Retained name"}'
            payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
            conn.execute("INSERT INTO derivation_payloads VALUES (?,?)", (payload_sha256, payload))
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES ('generation',0,'entries','[\"retained-row\"]',?)",
                (payload_sha256,),
            )
            conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id='generation' WHERE unit_id='fixture'"
            )
        immutable = {
            "derivation_dependency_sets": "manifest_json= '{}'",
            "derivation_generations": "input_fingerprint='different-input'",
            "derivation_row_refs": "record_key='[\"changed-row\"]'",
        }
        for table, assignment in immutable.items():
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute(f"UPDATE {table} SET {assignment}")
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute("DELETE FROM " + table)
        # Payload bytes are the one derivation record a written removal plan may
        # take away, so their delete gate is a permission row, not immutability.
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE derivation_payloads SET payload_json='{}'")
        with pytest.raises(sqlite3.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads")
        assert (
            conn.execute("SELECT manifest_json FROM derivation_dependency_sets").fetchone()[0]
            == manifest
        )
        assert (
            conn.execute("SELECT input_fingerprint FROM derivation_generations").fetchone()[0]
            == "input-fingerprint"
        )
        assert json.loads(
            conn.execute("SELECT payload_json FROM derivation_rows").fetchone()[0]
        ) == {"name_raw": "Retained name"}
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def scope_rows(conn, event):
    from swingset.model.schema import TABLES
    from swingset.state.derivations import OutputRow

    result = []
    for table, key in conn.execute(
        "SELECT table_name,record_key FROM canonical_scope_rows WHERE scope_kind='event' AND scope_id=? ORDER BY table_name,record_key",
        (event,),
    ):
        columns = TABLES[table].primary_key
        row = conn.execute(
            "SELECT * FROM "
            + table
            + " WHERE "
            + " AND ".join(column + "=?" for column in columns),
            json.loads(key),
        ).fetchone()
        assert row is not None
        result.append(OutputRow(table, key, dict(row)))
    return result


def write_round_projection(fixture, event="event-a"):
    from swingset.project.contests import project_event

    replace_scope(
        fixture.conn,
        scope_kind="event",
        scope_id=event,
        projection=project_event(
            fixture.conn, event, fixture.corpus.clock.now().isoformat(), fixture.corpus.run
        ),
        run_id=fixture.corpus.run,
        projected_at=fixture.corpus.clock.now().isoformat(),
    )
    return scope_rows(fixture.conn, event)


@pytest.mark.parametrize("boundary", ["during_rows", "after_completion"])
def test_completion_crash_rolls_back_real_rows_fingerprint_and_revision_then_restart_is_current(
    source_fixture, boundary
):
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    unit = WorkUnit("project", "event", "event-a")
    with f.db.transaction():
        selection = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
    before = output_state(f.conn)
    with pytest.raises(KeyboardInterrupt):
        with f.db.transaction():
            rows = write_round_projection(f)
            assert len(rows) > 2

            def interrupted_rows():
                yield rows[0]
                raise KeyboardInterrupt("crash during immutable output retention")

            derivations.complete(
                f.conn,
                selection,
                rows=interrupted_rows() if boundary == "during_rows" else rows,
                now=f.corpus.clock.now(),
                run_id=f.corpus.run,
            )
            assert derivations.current(f.conn, unit)
            raise KeyboardInterrupt("crash immediately before outer commit")
    assert output_state(f.conn) == before
    assert not derivations.current(f.conn, unit)
    assert not f.conn.execute("SELECT 1 FROM derivation_generations").fetchone()
    assert not f.conn.execute("SELECT 1 FROM derivation_rows").fetchone()
    with f.db.transaction():
        rows = write_round_projection(f)
        generation = derivations.complete(
            f.conn, selection, rows=rows, now=f.corpus.clock.now(), run_id=f.corpus.run
        )
    committed = output_state(f.conn)
    assert derivations.current(f.conn, unit)
    assert (
        f.conn.execute("SELECT COUNT(*) FROM entries WHERE event_id='event-a'").fetchone()[0] == 2
    )
    # A new SQLite reader sees the committed output and receipt together. The
    # fixture retains its writer lock; no services or recovery jobs are started.
    with open_database(f.db.state_dir, lock=False, read_only=True) as restarted:
        assert derivations.current(restarted.connection, unit)
        assert output_state(restarted.connection) == committed
        assert derivations.selected_generation(restarted.connection, unit) == generation
    with f.db.transaction():
        repeated = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        derivations.complete(
            f.conn,
            repeated,
            rows=scope_rows(f.conn, "event-a"),
            now=f.corpus.clock.now(),
            run_id=f.corpus.run,
        )
    assert output_state(f.conn) == committed
    assert f.conn.execute("SELECT COUNT(*) FROM derivation_generations").fetchone()[0] == 1


def test_new_desired_generation_blocks_late_worker_and_preserves_new_output(source_fixture):
    from swingset.state import derivations
    from swingset.state.attempts import SupersededWorkError
    from swingset.state.work import WorkUnit

    f = source_fixture
    unit = WorkUnit("project", "event", "event-a")
    with f.db.transaction():
        old = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
    from swingset.project.contests import project_event

    old_projection = project_event(
        f.conn, "event-a", f.corpus.clock.now().isoformat(), f.corpus.run
    )
    changed_body = f.body.replace(b"Alice Example", b"Newer Name")
    context = f.corpus.snapshot("round-newer", changed_body)
    new_source, report = f.corpus.stage(context, body=changed_body)
    assert not report.failures and f.corpus.admit(new_source) == "accepted"
    with f.db.transaction():
        newer = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        assert newer.fingerprint != old.fingerprint
        derivations.complete(
            f.conn,
            newer,
            rows=write_round_projection(f),
            now=f.corpus.clock.now(),
            run_id=f.corpus.run,
        )
    committed = output_state(f.conn)
    assert f.conn.execute("SELECT 1 FROM entries WHERE name_raw='Newer Name'").fetchone()
    with pytest.raises(SupersededWorkError):
        with f.db.transaction():
            replace_scope(
                f.conn,
                scope_kind="event",
                scope_id="event-a",
                projection=old_projection,
                run_id=f.corpus.run,
                projected_at=f.corpus.clock.now().isoformat(),
            )
            derivations.complete(
                f.conn,
                old,
                rows=scope_rows(f.conn, "event-a"),
                now=f.corpus.clock.now(),
                run_id=f.corpus.run,
            )
    assert output_state(f.conn) == committed
    assert derivations.selected_generation(f.conn, unit) == newer.generation_id
    assert derivations.current(f.conn, unit)
    assert not f.conn.execute(
        "SELECT 1 FROM derivation_generations WHERE generation_id=?", (old.generation_id,)
    ).fetchone()


def test_lost_enqueue_and_deleted_materialization_cannot_hide_stale_scope(source_fixture):
    from swingset.state import derivations
    from swingset.state.requirement_report import human_report, inventory
    from swingset.state.work import WorkUnit, next_work

    f = source_fixture
    unit = WorkUnit("project", "event", "event-a")
    drain_project(f)
    first = derivations.desired(f.conn, unit)
    f.conn.execute("DELETE FROM pending_work WHERE stage IN ('project','link')")
    assert unit not in set(derivations.pending_units(f.conn, "project"))
    f.conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id=NULL WHERE stage='project' AND unit_kind='event' AND unit_id='event-a'"
    )
    assert unit in set(derivations.pending_units(f.conn, "project"))
    assert (
        next_work(
            f.conn, "project", now=f.corpus.clock.now(), allowed=lambda selected: selected == unit
        )
        == unit
    )
    f.conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id=? WHERE stage='project' AND unit_kind='event' AND unit_id='event-a'",
        (first.generation_id,),
    )
    changed_body = f.body.replace(b"Alice Example", b"Changed Evidence")
    context = f.corpus.snapshot("round-changed", changed_body)
    generation, report = f.corpus.stage(context, body=changed_body)
    assert not report.failures and f.corpus.admit(generation) == "accepted"
    f.conn.execute("DELETE FROM pending_work WHERE stage IN ('project','link')")
    # No scan, refresh, manual enqueue or version bump intervenes here.
    assert unit in set(derivations.pending_units(f.conn, "project"))
    assert (
        next_work(
            f.conn, "project", now=f.corpus.clock.now(), allowed=lambda selected: selected == unit
        )
        == unit
    )
    assert derivations.desired(f.conn, unit).fingerprint != first.fingerprint
    report = inventory(f.conn, f.corpus.clock.now())
    projected = next(row for row in report["derivation"]["pending"] if row["stage"] == "project")
    assert projected["count"] > 0
    assert "desired versus materialized" in report["derivation"]["basis"]
    assert "desired versus materialized" in human_report(report)
    lag = report["pipeline_lag"]["derivation"]
    assert lag["by_stage"]["project"] == projected["count"]
    assert lag["unknown_enqueue_times"] > 0
    assert (
        f.conn.execute("SELECT count(*) FROM pending_work WHERE stage='project'").fetchone()[0] == 0
    )


def test_previous_generation_keeps_exact_source_manifest_after_new_source_selection(source_fixture):
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    unit = WorkUnit("project", "event", "event-a")
    with f.db.transaction():
        first = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        derivations.complete(
            f.conn,
            first,
            rows=write_round_projection(f),
            now=f.corpus.clock.now(),
            run_id=f.corpus.run,
        )
    generation_before = tuple(
        f.conn.execute(
            "SELECT * FROM derivation_generations WHERE generation_id=?", (first.generation_id,)
        ).fetchone()
    )
    root_set = f.conn.execute(
        "SELECT dependency_set_id FROM derivation_generations WHERE generation_id=?",
        (first.generation_id,),
    ).fetchone()[0]
    set_ids = {root_set, *first.dependency_sets}
    manifests_before = {
        identifier: f.conn.execute(
            "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
            (identifier,),
        ).fetchone()[0]
        for identifier in set_ids
    }
    assert f.generation in json.dumps(manifests_before)
    rows_before = [
        tuple(row)
        for row in f.conn.execute(
            "SELECT * FROM derivation_rows WHERE generation_id=? ORDER BY ordinal",
            (first.generation_id,),
        )
    ]
    changed_body = f.body.replace(b"Alice Example", b"Later Evidence")
    ctx = f.corpus.snapshot("round-replacement", changed_body)
    replacement, report = f.corpus.stage(ctx, body=changed_body)
    assert not report.failures and f.corpus.admit(replacement) == "accepted"
    with f.db.transaction():
        newer = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        derivations.complete(
            f.conn,
            newer,
            rows=write_round_projection(f),
            now=f.corpus.clock.now(),
            run_id=f.corpus.run,
        )
    assert newer.generation_id != first.generation_id
    assert (
        tuple(
            f.conn.execute(
                "SELECT * FROM derivation_generations WHERE generation_id=?", (first.generation_id,)
            ).fetchone()
        )
        == generation_before
    )
    assert {
        identifier: f.conn.execute(
            "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
            (identifier,),
        ).fetchone()[0]
        for identifier in set_ids
    } == manifests_before
    assert [
        tuple(row)
        for row in f.conn.execute(
            "SELECT * FROM derivation_rows WHERE generation_id=? ORDER BY ordinal",
            (first.generation_id,),
        )
    ] == rows_before


def change_code(fixture, monkeypatch, marker="changed artifact with unchanged labels"):
    """Accept a runtime bundle whose only change is a comment in one module."""
    from swingset.fetch.archive import canonical, digest
    from swingset.state import inputs, recipe_scope

    retained_runtime = {
        name: body
        for name, body in fixture.bundle.files.items()
        if name.startswith(("runtime/", "recipes/"))
    }
    name = "runtime/swingset/project/contests.py"
    retained_runtime[name] += f"\n# Independent fixture: {marker}.\n".encode()
    manifest = json.loads(retained_runtime["recipes/runtime.json"])
    manifest["files"][name] = digest(retained_runtime[name])
    retained_runtime["recipes/runtime.json"] = canonical(manifest)
    retained_runtime["recipes/scopes.json"] = canonical(recipe_scope.manifest(retained_runtime))
    monkeypatch.setattr(inputs, "capture_runtime", lambda: retained_runtime)
    unchanged_labels = json.loads(fixture.bundle.files["versions.json"])
    newer_bundle = inputs.capture(
        fixture.config, fixture.overrides, fixture.db.state_dir, unchanged_labels
    )
    assert newer_bundle.files["versions.json"] == fixture.bundle.files["versions.json"]
    inputs.accept(fixture.db, newer_bundle, fixture.corpus.clock)
    fixture.bundle = newer_bundle
    fixture.conn.execute("DELETE FROM pending_work WHERE stage IN ('project','link')")


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def pointers(conn):
    return {
        tuple(row[:3]): row[3]
        for row in conn.execute(
            "SELECT stage,unit_kind,unit_id,materialized_generation_id FROM derivation_scopes"
        )
    }


def epochs(conn, namespace):
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT input_key,version FROM derivation_input_versions WHERE namespace=?",
            (namespace,),
        )
    }


def test_changed_captured_code_replays_scope_without_manual_version_bump(
    source_fixture, monkeypatch
):
    """A code-only change re-runs the scope; identical output keeps its id (D-0205)."""
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    unit = WorkUnit("project", "event", "event-a")
    with f.db.transaction():
        first = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        derivations.complete(
            f.conn,
            first,
            rows=write_round_projection(f),
            now=f.corpus.clock.now(),
            run_id=f.corpus.run,
        )
    before = output_state(f.conn)
    change_code(f, monkeypatch)
    assert not derivations.current(f.conn, unit)
    assert unit in set(derivations.pending_units(f.conn, "project"))
    with f.db.transaction():
        newer = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        kept = derivations.complete(
            f.conn,
            newer,
            rows=write_round_projection(f),
            now=f.corpus.clock.now(),
            run_id=f.corpus.run,
        )
    assert newer.recipe != first.recipe and newer.fingerprint != first.fingerprint
    assert kept == first.generation_id
    assert derivations.selected_generation(f.conn, unit) == first.generation_id
    assert derivations.current(f.conn, unit)
    assert output_state(f.conn) == before
    assert count(f.conn, "derivation_generations") == 1
    assert [
        tuple(row)
        for row in f.conn.execute(
            "SELECT input_fingerprint,generation_id FROM derivation_equivalences"
        )
    ] == [(newer.fingerprint, first.generation_id)]


def signed_for(conn, unit, generation_id):
    """Whether the scope's fast-path signature proves this generation."""
    from swingset.state.derivation_signatures import certificate, materialized_proof
    from swingset.state.recipes import recipe_inputs

    signature = conn.execute(
        "SELECT materialized_signature FROM derivation_scopes WHERE stage=? AND unit_kind=? AND unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
    ).fetchone()[0]
    proof = certificate(conn, unit, recipe_inputs(conn, unit.stage, unit.unit_kind), {})
    return signature == materialized_proof(generation_id, proof)[1]


def test_identical_rerun_writes_no_generation_and_moves_no_pointer(source_fixture, monkeypatch):
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    drain_project(f)
    generations = count(f.conn, "derivation_generations")
    kept = pointers(f.conn)
    selected = epochs(f.conn, "selected")
    before = output_state(f.conn)
    change_code(f, monkeypatch)
    rerun = drain_project(f)
    assert rerun and all(unit.stage == "project" for unit in rerun)
    assert count(f.conn, "derivation_generations") == generations
    assert pointers(f.conn) == kept
    assert epochs(f.conn, "selected") == selected
    assert count(f.conn, "derivation_equivalences") >= len(set(rerun))
    assert output_state(f.conn) == before
    for unit in set(rerun):
        generation = kept[(unit.stage, unit.unit_kind, unit.unit_id)]
        # Fast path: the signature was certified for the kept id.
        assert signed_for(f.conn, unit, generation), unit
        assert derivations.current(f.conn, unit)
        # Slow path: an equivalence makes the kept id a materialization.
        assert derivations.current_for(f.conn, unit)
        assert derivations.desired(f.conn, unit).generation_id == generation
    event = WorkUnit("project", "event", "event-a")
    assert event in rerun
    assert list(derivations.pending_units(f.conn, "project")) == []


def test_with_cutoff_off_a_code_only_rerun_mints_as_before(source_fixture, monkeypatch):
    from swingset import cutoff
    from swingset.state import derivations

    f = source_fixture
    drain_project(f)
    generations = count(f.conn, "derivation_generations")
    kept = pointers(f.conn)
    change_code(f, monkeypatch)
    with cutoff.configured(False):
        rerun = drain_project(f)
    assert rerun
    # The operator's switch (`[derivation] cutoff = false`): every re-run mints.
    assert count(f.conn, "derivation_generations") == generations + len(set(rerun))
    assert count(f.conn, "derivation_equivalences") == 0
    moved = {key for key, value in pointers(f.conn).items() if kept.get(key) != value}
    assert moved == {(unit.stage, unit.unit_kind, unit.unit_id) for unit in set(rerun)}
    assert list(derivations.pending_units(f.conn, "project")) == []


def test_different_output_after_a_cutoff_names_the_kept_generation_as_previous(
    source_fixture, monkeypatch
):
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    unit = WorkUnit("project", "event", "event-a")
    drain_project(f)
    kept = derivations.selected_generation(f.conn, unit)
    change_code(f, monkeypatch)
    drain_project(f)
    assert derivations.selected_generation(f.conn, unit) == kept
    changed_body = f.body.replace(b"Alice Example", b"Really Changed")
    context = f.corpus.snapshot("round-really-changed", changed_body)
    generation, report = f.corpus.stage(context, body=changed_body)
    assert not report.failures and f.corpus.admit(generation) == "accepted"
    drain_project(f)
    newer = derivations.selected_generation(f.conn, unit)
    assert newer != kept
    assert (
        f.conn.execute(
            "SELECT previous_generation_id FROM derivation_generations WHERE generation_id=?",
            (newer,),
        ).fetchone()[0]
        == kept
    )
    assert f.conn.execute("SELECT 1 FROM entries WHERE name_raw='Really Changed'").fetchone()


def test_process_death_immediately_after_commit_leaves_current_generation(source_fixture, tmp_path):
    import sqlite3
    import subprocess
    import sys
    from contextlib import closing
    from pathlib import Path

    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    clone = tmp_path / "after-commit-process"
    clone.mkdir()
    with closing(sqlite3.connect(clone / "state.sqlite")) as target:
        f.conn.backup(target)
    code = """
import os,sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_h15_acceptance import scope_rows
from swingset.project.contests import project_event
from swingset.project.writer import replace_scope
from swingset.state import derivations
from swingset.state.db import open_database
from swingset.state.work import WorkUnit
state,now,run=sys.argv[1:]
with open_database(Path(state)) as db:
    unit=WorkUnit('project','event','event-a')
    with db.transaction():
        captured=derivations.capture(db.connection,unit,now=now)
        replace_scope(db.connection,scope_kind='event',scope_id='event-a',projection=project_event(db.connection,'event-a',now,run),run_id=run,projected_at=now)
        derivations.complete(db.connection,captured,rows=scope_rows(db.connection,'event-a'),now=now,run_id=run)
    os._exit(73)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(clone), f.corpus.clock.now().isoformat(), f.corpus.run],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 73, result.stderr
    with open_database(clone) as restarted:
        unit = WorkUnit("project", "event", "event-a")
        assert derivations.current(restarted.connection, unit)
        assert unit not in set(derivations.pending_units(restarted.connection, "project"))
        assert (
            restarted.connection.execute(
                "SELECT COUNT(*) FROM entries WHERE event_id='event-a'"
            ).fetchone()[0]
            == 2
        )
        assert (
            restarted.connection.execute("SELECT COUNT(*) FROM derivation_generations").fetchone()[
                0
            ]
            == 1
        )
        assert restarted.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def drain_project(fixture):
    from swingset.project.process import process_unit
    from swingset.state import derivations
    from swingset.state.work import next_work

    completed = []
    for _ in range(40):
        unit = next_work(fixture.conn, "project", now=fixture.corpus.clock.now())
        if unit is None:
            assert list(derivations.pending_units(fixture.conn, "project")) == []
            return completed
        completed.append(unit)
        process_unit(fixture.db, unit, fixture.bundle, fixture.corpus.clock, fixture.corpus.run)
    pytest.fail(f"projection did not settle: {completed}")


def test_alias_move_replays_old_and_new_scopes_and_preserves_exact_mapping_generation(
    source_fixture,
):
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    drain_project(f)
    old_rows = scope_rows(f.conn, "event-a")
    assert any(row.table == "entries" for row in old_rows)
    old_generation = derivations.selected_generation(
        f.conn, WorkUnit("project", "event", "event-a")
    )
    f.aliases.write_text(
        "source,source_ref,event_id,note\neepro,eepro:test,event-b,Reviewed move\n"
    )
    f.bundle = capture(f.config, f.overrides, f.db.state_dir, versions())
    accept(f.db, f.bundle, f.corpus.clock)
    f.conn.execute("DELETE FROM pending_work WHERE stage IN ('project','link')")
    completed = drain_project(f)
    assert WorkUnit("project", "map", "all") in completed
    assert (
        f.conn.execute(
            "SELECT event_id FROM source_event_map WHERE source_ref='eepro:test'"
        ).fetchone()[0]
        == "event-b"
    )
    assert scope_rows(f.conn, "event-a") == []
    entries = f.conn.execute(
        "SELECT event_id,name_raw,snapshot_id FROM entries ORDER BY name_raw"
    ).fetchall()
    assert [tuple(row) for row in entries] == [
        ("event-b", "Alice Example", "round-one"),
        ("event-b", "Diane Abele", "round-one"),
    ]
    map_generation = derivations.selected_generation(f.conn, WorkUnit("project", "map", "all"))
    for identifier in ("event-a", "event-b"):
        unit = WorkUnit("project", "event", identifier)
        assert derivations.current(f.conn, unit)
        manifest = f.conn.execute(
            "SELECT d.manifest_json FROM derivation_generations g JOIN derivation_dependency_sets d USING(dependency_set_id) WHERE g.generation_id=?",
            (derivations.selected_generation(f.conn, unit),),
        ).fetchone()[0]
        assert map_generation in manifest + json.dumps(
            derivations.desired(f.conn, unit).dependency_sets
        )
    retained = f.conn.execute(
        "SELECT payload_json FROM derivation_rows WHERE generation_id=? AND table_name='entries'",
        (old_generation,),
    ).fetchall()
    assert len(retained) == 2 and all(
        json.loads(row[0])["event_id"] == "event-a" for row in retained
    )
    assert drain_project(f) == []
    assert f.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_new_registry_dancer_reconsiders_unmatched_entry_without_prior_candidate_edge(
    source_fixture,
):
    from pathlib import Path

    from swingset.link.service import link_event
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.wsdc_registry.adapter import SOURCE
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    drain_project(f)
    link_event(f.db, "event-a", f.bundle, f.corpus.clock, f.corpus.run)
    unit = WorkUnit("link", "event", "event-a")
    assert derivations.current(f.conn, unit)
    entry = f.conn.execute("SELECT entry_id FROM entries WHERE name_raw='Diane Abele'").fetchone()[
        0
    ]
    assert not f.conn.execute(
        "SELECT 1 FROM link_candidates WHERE subject_id=?", (entry,)
    ).fetchone()
    previous = derivations.selected_generation(f.conn, unit)
    spec = SOURCE.watch(1)
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
    context = f.corpus.snapshot("registry-newcomer", body, spec=spec)
    f.conn.execute(
        "UPDATE snapshots SET method=? WHERE snapshot_id=?", (spec.method, context.snapshot_id)
    )
    generation, report = f.corpus.stage(context, body=body)
    assert not report.failures
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    drain_project(f)
    f.conn.execute("DELETE FROM pending_work WHERE stage='link'")
    assert unit in set(derivations.pending_units(f.conn, "link"))
    assert not derivations.current(f.conn, unit)
    link_event(f.db, "event-a", f.bundle, f.corpus.clock, f.corpus.run)
    assert f.conn.execute(
        "SELECT 1 FROM link_candidates WHERE subject_id=? AND wsdc_id=1", (entry,)
    ).fetchone()
    assert derivations.current(f.conn, unit)
    assert derivations.selected_generation(f.conn, unit) != previous
    assert drain_project(f) == []


def test_reference_migration_changes_desired_link_without_journal_or_source_change(source_fixture):
    from swingset.link.service import link_event
    from swingset.state import derivations
    from swingset.state.identity_references import record_migration
    from swingset.state.work import WorkUnit

    f = source_fixture
    drain_project(f)
    link_event(f.db, "event-a", f.bundle, f.corpus.clock, f.corpus.run)
    unit = WorkUnit("link", "event", "event-a")
    old = derivations.desired(f.conn, unit)
    binding = f.conn.execute(
        "SELECT ref_id,semantic_hash FROM identity_reference_bindings ORDER BY ref_id LIMIT 1"
    ).fetchone()
    assert binding is not None and derivations.current(f.conn, unit)
    journal = f.conn.execute(
        "SELECT value FROM meta WHERE key='identity_journal_digest'"
    ).fetchone()
    with f.db.transaction():
        record_migration(
            f.conn,
            migration_id="offline-continuity-review",
            from_ref_id=binding[0],
            to_ref_ids=(binding[0],),
            status="approved",
            evidence=json.dumps({"semantic_hash": binding[1]}),
            reason="Fixture continuity",
            author="offline reviewer",
            date="2026-09-13",
            now=f.corpus.clock.now().isoformat(),
        )
    assert (
        f.conn.execute("SELECT value FROM meta WHERE key='identity_journal_digest'").fetchone()
        == journal
    )
    f.conn.execute("DELETE FROM pending_work WHERE stage='link'")
    assert not derivations.current(f.conn, unit)
    assert derivations.desired(f.conn, unit).fingerprint != old.fingerprint
    assert unit in set(derivations.pending_units(f.conn, "link"))


def test_sibling_source_index_evidence_invalidates_shared_metadata_generation(tmp_path):
    from dataclasses import replace

    from test_project_events import add

    from swingset.clock import FakeClock
    from swingset.project.process import process_unit
    from swingset.sources.records import SourceEventRow
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        row = SourceEventRow(
            "source_event_row",
            "scoringdance:418",
            "Retained Event",
            "08/28/2026",
            "https://scoring.dance/event/418",
        )
        add(db, "sitemap", "sitemap", "sitemap", "sitemap-before", row, "2026-09-01T00:00:00Z")
        unit = WorkUnit("project", "source_index", "sitemap")
        process_unit(db, unit, SimpleNamespace(files={}), FakeClock(), "run_a")
        old = derivations.desired(conn, unit)
        assert derivations.current(conn, unit)
        add(
            db,
            "detail",
            "event",
            "event:418",
            "detail-after",
            replace(row, name_raw="New detail name"),
            "2026-09-02T00:00:00Z",
        )
        conn.execute("DELETE FROM pending_work WHERE stage='project'")
        assert not derivations.current(conn, unit)
        assert derivations.desired(conn, unit).fingerprint != old.fingerprint


def test_registry_enrichment_keeps_captured_calendar_identity_and_replay_quiet(source_fixture):
    from swingset.model.observations import encode_payload
    from swingset.project.process import process_unit
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.records import CalendarRow, DancerLookup, RegistryPlacement
    from swingset.sources.wsdc_registry.adapter import SOURCE
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    row = CalendarRow(
        "calendar_row",
        "Registry Joined Weekend",
        "2026-01-01",
        "2026-01-04",
        "Registry",
        "Denver, USA",
        None,
        "US",
        (),
    )
    f.conn.execute(
        "INSERT INTO observations VALUES ('calendar-research',?,'round-one','calendar_row','calendar','2026',0,'1','1',?)",
        (f.corpus.spec.watch_id, encode_payload(row)),
    )
    unit = WorkUnit("project", "calendar", "2026")
    process_unit(f.db, unit, f.bundle, f.corpus.clock, f.corpus.run)
    first = derivations.desired(f.conn, unit)
    original = dict(
        f.conn.execute("SELECT * FROM events WHERE name='Registry Joined Weekend'").fetchone()
    )
    continuity_id = first.continuity["dependency_set_id"]
    captured_continuity = f.conn.execute(
        "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
        (continuity_id,),
    ).fetchone()[0]
    assert all(
        item["row"].get("series_id") != "wsdc-999" for item in json.loads(captured_continuity)
    )
    assert derivations.current(f.conn, unit)
    spec = SOURCE.watch(999)
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    context = f.corpus.snapshot(
        "registry-occurrence", b"offline typed registry occurrence", spec=spec
    )
    registry = DancerLookup(
        "dancer_lookup",
        "found",
        999,
        999,
        first_name="Offline",
        last_name="Dancer",
        primary_role_raw="L",
        placements=(
            RegistryPlacement("leader", "NOV", "999", row.name_raw, "January 2026", "1", 1, "wcs"),
        ),
    )
    f.conn.execute(
        "INSERT INTO observations VALUES ('registry-occurrence',?,'registry-occurrence','dancer_lookup','dancer','999',0,'1','1',?)",
        (context.watch_id, encode_payload(registry)),
    )
    completed = drain_project(f)
    assert WorkUnit("project", "dancer", "999") in completed
    assert WorkUnit("project", "inventory", "all") in completed
    enriched = dict(
        f.conn.execute("SELECT * FROM events WHERE name='Registry Joined Weekend'").fetchone()
    )
    assert enriched["event_id"] == original["event_id"]
    assert enriched["series_id"] == "wsdc-999" and enriched["held"] == "held"
    assert "registry" in json.loads(enriched["history_source"])
    assert derivations.current(f.conn, unit)
    assert derivations.selected_generation(f.conn, unit) == first.generation_id
    assert derivations.desired(f.conn, unit).fingerprint == first.fingerprint
    before = output_state(f.conn)
    assert process_unit(f.db, unit, f.bundle, f.corpus.clock, f.corpus.run) is False
    assert output_state(f.conn) == before
    assert drain_project(f) == []
    # Captured continuity remains byte-identical after live inventory changed.
    assert (
        f.conn.execute(
            "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
            (continuity_id,),
        ).fetchone()[0]
        == captured_continuity
    )
    recipe = json.loads(
        f.conn.execute(
            "SELECT recipe_json FROM derivation_generations WHERE generation_id=?",
            (first.generation_id,),
        ).fetchone()[0]
    )
    assert recipe["continuity"]["dependency_set_id"] == continuity_id


def add_dancer(fixture, first_name="Offline", *, name_raw="Registry Joined Weekend"):
    """Retain one typed registry dancer observation, replacing any earlier one."""
    from swingset.model.observations import encode_payload
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.records import DancerLookup, RegistryPlacement
    from swingset.sources.wsdc_registry.adapter import SOURCE

    spec = SOURCE.watch(999)
    upsert_watch(fixture.conn, spec, fixture.corpus.clock.now())
    snapshot = "registry-" + first_name.casefold()
    context = fixture.corpus.snapshot(
        snapshot, f"offline registry {first_name}".encode(), spec=spec
    )
    registry = DancerLookup(
        "dancer_lookup",
        "found",
        999,
        999,
        first_name=first_name,
        last_name="Dancer",
        primary_role_raw="L",
        placements=(
            RegistryPlacement("leader", "NOV", "999", name_raw, "January 2026", "1", 1, "wcs"),
        ),
    )
    with fixture.db.transaction():
        fixture.conn.execute(
            "DELETE FROM observations WHERE scope_kind='dancer' AND scope_id='999'"
        )
        fixture.conn.execute(
            "INSERT INTO observations VALUES (?,?,?,'dancer_lookup','dancer','999',0,'1','1',?)",
            (snapshot, context.watch_id, snapshot, encode_payload(registry)),
        )


def add_calendar(fixture):
    """Retain one calendar row that registry enrichment later joins."""
    from swingset.model.observations import encode_payload
    from swingset.sources.records import CalendarRow

    row = CalendarRow(
        "calendar_row",
        "Registry Joined Weekend",
        "2026-01-01",
        "2026-01-04",
        "Registry",
        "Denver, USA",
        None,
        "US",
        (),
    )
    fixture.conn.execute(
        "INSERT INTO observations VALUES ('calendar-research',?,'round-one','calendar_row','calendar','2026',0,'1','1',?)",
        (fixture.corpus.spec.watch_id, encode_payload(row)),
    )


def link_all(fixture):
    from swingset.link.service import link_event
    from swingset.state import derivations

    linked = []
    for unit in list(derivations.pending_units(fixture.conn, "link")):
        link_event(
            fixture.db, unit.unit_id, fixture.bundle, fixture.corpus.clock, fixture.corpus.run
        )
        linked.append(unit)
    assert list(derivations.pending_units(fixture.conn, "link")) == []
    return linked


def select_closure(fixture):
    from swingset.build import closure

    fixture.corpus.clock.sleep(60)
    return closure.select(fixture.conn, cutoff=fixture.corpus.clock.now())


def selected(value, stage, kind=None):
    return {
        row["unit_id"]: row["generation_id"]
        for row in value.selected
        if row["stage"] == stage and (kind is None or row["unit_kind"] == kind)
    }


def withheld_links(value):
    return [
        row for row in value.inventory if row["reason"] == "compatible_link_generation_unavailable"
    ]


def test_release_closure_keeps_every_link_across_a_code_only_rerun(source_fixture, monkeypatch):
    f = source_fixture
    add_dancer(f)
    drain_project(f)
    assert link_all(f)
    before = select_closure(f)
    links = selected(before, "link")
    assert links and not withheld_links(before)
    generations = count(f.conn, "derivation_generations")
    change_code(f, monkeypatch)
    rerun = drain_project(f)
    assert {unit.unit_kind for unit in rerun} >= {"dancer", "event", "history"}
    assert link_all(f)
    assert count(f.conn, "derivation_generations") == generations
    after = select_closure(f)
    assert selected(after, "link") == links
    assert selected(after, "project") == selected(before, "project")
    assert not withheld_links(after)
    assert before.semantic_fingerprint == after.semantic_fingerprint


def test_a_real_dancer_change_still_mints_history_and_links_that_name_it(
    source_fixture, monkeypatch
):
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    add_dancer(f)
    drain_project(f)
    link_all(f)
    dancer = WorkUnit("project", "dancer", "999")
    history = WorkUnit("project", "history", "all")
    old_dancer = derivations.selected_generation(f.conn, dancer)
    old_history = derivations.selected_generation(f.conn, history)
    old_links = selected(select_closure(f), "link")
    add_dancer(f, "Renamed")
    drain_project(f)
    link_all(f)
    new_dancer = derivations.selected_generation(f.conn, dancer)
    new_history = derivations.selected_generation(f.conn, history)
    assert new_dancer != old_dancer
    # History re-ran with the same output, but its selected dancer set changed,
    # so it is not closure-stable and names the new dancer (finding F1).
    assert new_history != old_history
    labels = {
        row[0]: tuple(row[1:])
        for row in f.conn.execute(
            "SELECT generation_id,output_digest,row_count FROM derivation_generations "
            "WHERE generation_id IN (?,?)",
            (old_history, new_history),
        )
    }
    assert labels[old_history] == labels[new_history]
    assert new_dancer in f.conn.execute(
        "SELECT d.manifest_json FROM derivation_generations g JOIN derivation_dependency_sets d "
        "ON d.dependency_set_id=g.dependency_set_id WHERE g.generation_id=?",
        (new_history,),
    ).fetchone()[0] + json.dumps(derivations.desired(f.conn, history).dependency_sets)
    after = select_closure(f)
    assert selected(after, "project", "dancer")["999"] == new_dancer
    assert selected(after, "project", "history")["all"] == new_history
    new_links = selected(after, "link")
    assert set(new_links) == set(old_links) and not withheld_links(after)
    assert all(new_links[event] != old_links[event] for event in old_links)


def test_retention_walk_from_a_kept_pointer_reaches_what_the_rerun_consumed(
    source_fixture, monkeypatch
):
    from swingset.state import derivations, retention
    from swingset.state.work import WorkUnit

    f = source_fixture
    add_dancer(f)
    drain_project(f)
    link_all(f)
    change_code(f, monkeypatch)
    drain_project(f)
    link_all(f)
    assert count(f.conn, "derivation_equivalences") > 0
    unit = WorkUnit("link", "event", "event-a")
    kept = derivations.selected_generation(f.conn, unit)
    reached, absent = retention.reachable(
        f.conn, (retention.Root("pointer", "link/event-a", (kept,)),)
    )
    assert not absent
    for root_set in [
        row[0]
        for row in f.conn.execute(
            "SELECT dependency_set_id FROM derivation_equivalences WHERE generation_id=?", (kept,)
        )
    ]:
        manifest = json.loads(
            f.conn.execute(
                "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
                (root_set,),
            ).fetchone()[0]
        )
        consumed = {
            member["generation_id"]
            for entry in manifest
            if entry["kind"] == "dependency_set"
            for member in json.loads(
                f.conn.execute(
                    "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
                    (entry["fingerprint"],),
                ).fetchone()[0]
            )
            if member.get("kind") == "derivation" and member.get("generation_id")
        }
        assert consumed and consumed <= set(reached)


def test_a_member_whose_provisional_id_a_sibling_captured_never_cuts_off(
    source_fixture, monkeypatch
):
    from swingset.project.materialization import output_rows
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    drain_project(f)
    event = WorkUnit("project", "event", "event-a")
    source_event = WorkUnit("project", "source_event", "eepro:test")
    kept = derivations.selected_generation(f.conn, event)
    generations = count(f.conn, "derivation_generations")
    change_code(f, monkeypatch)
    now = f.corpus.clock.now()
    with f.db.transaction(), derivations.group(f.conn):
        first = derivations.capture(f.conn, event, now=now)
        assert first.generation_id != kept
        second = derivations.capture(f.conn, source_event, now=now)
        # The source event's selection names the event's provisional id.
        assert first.generation_id in json.dumps(second.dependency_sets)
        # The event's own output is unchanged, so only the pin keeps it from
        # cutting off to the kept id the sibling does not name.
        rows = list(output_rows(f.conn, event))
        assert derivations.complete(f.conn, first, rows=rows, now=now, run_id=f.corpus.run) == (
            first.generation_id
        )
        derivations.complete(f.conn, second, rows=[], now=now, run_id=f.corpus.run)
    assert derivations.selected_generation(f.conn, event) == first.generation_id
    assert derivations.current(f.conn, event) and derivations.current(f.conn, source_event)
    assert count(f.conn, "derivation_generations") == generations + 2
    assert f.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_a_sibling_captured_after_a_member_cut_off_names_the_kept_id(source_fixture, monkeypatch):
    from swingset.project.materialization import output_rows
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    drain_project(f)
    event = WorkUnit("project", "event", "event-a")
    source_event = WorkUnit("project", "source_event", "eepro:test")
    kept = derivations.selected_generation(f.conn, event)
    change_code(f, monkeypatch)
    now = f.corpus.clock.now()
    with f.db.transaction(), derivations.group(f.conn):
        first = derivations.capture(f.conn, event, now=now)
        assert first.generation_id != kept
        # No sibling names the provisional id yet, so the event cuts off.
        rows = list(output_rows(f.conn, event))
        assert derivations.complete(f.conn, first, rows=rows, now=now, run_id=f.corpus.run) == kept
        assert derivations.selected_generation(f.conn, event) == kept
        second = derivations.capture(f.conn, source_event, now=now)
        # The sibling names the kept id, never the provisional one.
        named = json.dumps(second.dependency_sets)
        assert kept in named and first.generation_id not in named
        derivations.complete(
            f.conn,
            second,
            rows=list(output_rows(f.conn, source_event)),
            now=now,
            run_id=f.corpus.run,
        )
    assert derivations.selected_generation(f.conn, event) == kept
    assert derivations.current(f.conn, event) and derivations.current(f.conn, source_event)
    assert f.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_a_scheduled_source_event_with_a_nested_reused_event_cuts_off(source_fixture, monkeypatch):
    from swingset.project.process import process_unit
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    drain_project(f)
    event = WorkUnit("project", "event", "event-a")
    source_event = WorkUnit("project", "source_event", "eepro:test")
    kept = {unit: derivations.selected_generation(f.conn, unit) for unit in (event, source_event)}
    generations = count(f.conn, "derivation_generations")
    change_code(f, monkeypatch)
    # The scheduler runs the event first, then the source event with a
    # selection captured in its own admission transaction (finding F3).
    process_unit(f.db, event, f.bundle, f.corpus.clock, f.corpus.run)
    assert derivations.current(f.conn, event)
    with f.db.transaction():
        selection = derivations.capture(f.conn, source_event, now=f.corpus.clock.now())
    process_unit(f.db, source_event, f.bundle, f.corpus.clock, f.corpus.run, selection=selection)
    assert {unit: derivations.selected_generation(f.conn, unit) for unit in kept} == kept
    assert derivations.current(f.conn, source_event)
    assert count(f.conn, "derivation_generations") == generations
    assert {
        tuple(row)
        for row in f.conn.execute("SELECT unit_kind,generation_id FROM derivation_equivalences")
    } >= {("event", kept[event]), ("source_event", kept[source_event])}


def test_a_calendar_cutoff_that_rewrote_enrichment_moves_the_replay_epoch(source_fixture):
    """Calendar-only recipe change after registry enrichment (finding F2)."""
    from swingset.project.materialization import materializing
    from swingset.project.process import _replace_calendar, process_unit
    from swingset.state import derivations
    from swingset.state.recipes import recipe_inputs
    from swingset.state.work import WorkUnit

    f = source_fixture
    add_calendar(f)
    calendar = WorkUnit("project", "calendar", "2026")
    process_unit(f.db, calendar, f.bundle, f.corpus.clock, f.corpus.run)
    add_dancer(f)
    drain_project(f)

    def event_state():
        return dict(
            f.conn.execute("SELECT * FROM events WHERE name='Registry Joined Weekend'").fetchone()
        )

    def replay_calendar(marker):
        now = f.corpus.clock.now().isoformat()
        recipe = {**recipe_inputs(f.conn, "project", "calendar"), "helper/calendar-only": marker}
        with f.db.transaction() as conn:
            with materializing(
                conn, calendar, now=now, run_id=f.corpus.run, recipe=recipe
            ) as captured:
                _replace_calendar(conn, "2026", now, f.corpus.run, selection=captured)

    # The first replay after enrichment joins the registry identity through its
    # continuity support, so its output really differs and it mints.
    first = derivations.selected_generation(f.conn, calendar)
    replay_calendar("first")
    assert derivations.selected_generation(f.conn, calendar) != first
    drain_project(f)
    enriched = event_state()
    assert enriched["series_id"] == "wsdc-999" and enriched["held"] == "held"
    assert "registry" in json.loads(enriched["history_source"])
    kept = pointers(f.conn)
    generations = count(f.conn, "derivation_generations")
    replayed = epochs(f.conn, "replayed")
    inventory = WorkUnit("project", "inventory", "all")
    assert derivations.current(f.conn, inventory)

    # A calendar-only recipe change: same output, but the replay rewrites the
    # enrichment inventory added to the live rows.
    replay_calendar("second")
    # The replay kept the calendar's id but overwrote inventory's enrichment.
    assert pointers(f.conn) == kept
    assert "registry" not in json.loads(event_state()["history_source"])
    assert epochs(f.conn, "replayed")['["project","calendar"]'] == (
        replayed.get('["project","calendar"]', 0) + 1
    )
    assert not derivations.current(f.conn, inventory)
    rerun = drain_project(f)
    assert inventory in rerun
    restored = event_state()
    assert restored["series_id"] == "wsdc-999" and restored["held"] == "held"
    assert json.loads(restored["history_source"]) == json.loads(enriched["history_source"])
    processing = ("first_seen_at", "last_seen_at", "run_id")
    assert {k: v for k, v in restored.items() if k not in processing} == {
        k: v for k, v in enriched.items() if k not in processing
    }
    assert pointers(f.conn) == kept
    assert count(f.conn, "derivation_generations") == generations
    assert drain_project(f) == []

    # The inventory re-ran at a new fingerprint and cut off. Its continuity
    # support is the one recorded with that equivalence, not the kept
    # generation's own, which names another predecessor.
    selection = derivations.desired(f.conn, inventory)
    recorded = f.conn.execute(
        "SELECT r.recipe_json FROM derivation_equivalences e JOIN derivation_recipes r "
        "USING(recipe_sha256) WHERE e.unit_kind='inventory' AND e.input_fingerprint=?",
        (selection.fingerprint,),
    ).fetchone()[0]
    own = f.conn.execute(
        "SELECT recipe_json FROM derivation_generations WHERE generation_id=?",
        (selection.generation_id,),
    ).fetchone()[0]
    assert selection.continuity == json.loads(recorded)["continuity"]
    assert json.loads(own)["continuity"] != selection.continuity

    # A real calendar change after the cutoff mints a generation whose
    # predecessor is the kept id.
    from swingset.model.observations import decode_payload, encode_payload

    payload = f.conn.execute(
        "SELECT payload_json FROM observations WHERE observation_id='calendar-research'"
    ).fetchone()[0]
    moved = replace(decode_payload("calendar_row", payload), location_raw="Boulder, USA")
    with f.db.transaction():
        f.conn.execute(
            "UPDATE observations SET payload_json=? WHERE observation_id='calendar-research'",
            (encode_payload(moved),),
        )
    process_unit(f.db, calendar, f.bundle, f.corpus.clock, f.corpus.run)
    newer = derivations.selected_generation(f.conn, calendar)
    assert newer != kept[("project", "calendar", "2026")]
    assert (
        f.conn.execute(
            "SELECT previous_generation_id FROM derivation_generations WHERE generation_id=?",
            (newer,),
        ).fetchone()[0]
        == kept[("project", "calendar", "2026")]
    )


def test_crash_between_the_comparison_and_the_equivalence_rolls_back(source_fixture, monkeypatch):
    from swingset.project.materialization import output_rows
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    f = source_fixture
    unit = WorkUnit("project", "event", "event-a")
    drain_project(f)
    kept = derivations.selected_generation(f.conn, unit)
    change_code(f, monkeypatch)
    state = output_state(f.conn)
    scopes = [
        tuple(row) for row in f.conn.execute("SELECT * FROM derivation_scopes ORDER BY 1,2,3")
    ]
    generations = count(f.conn, "derivation_generations")
    compared = []
    original = derivations._fingerprint

    def fingerprint(rows):
        compared.append(True)
        return original(rows)

    def crash(*args, **kwargs):
        assert compared, "the crash must come after pass 1"
        raise KeyboardInterrupt("crash between pass 1 and the equivalence insert")

    monkeypatch.setattr(derivations, "_fingerprint", fingerprint)
    monkeypatch.setattr(derivations, "_record_equivalence", crash)
    with pytest.raises(KeyboardInterrupt):
        with f.db.transaction():
            selection = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
            write_round_projection(f)
            derivations.complete(
                f.conn,
                selection,
                rows=lambda: output_rows(f.conn, unit),
                now=f.corpus.clock.now(),
                run_id=f.corpus.run,
            )
    assert output_state(f.conn) == state
    assert [
        tuple(row) for row in f.conn.execute("SELECT * FROM derivation_scopes ORDER BY 1,2,3")
    ] == scopes
    assert count(f.conn, "derivation_equivalences") == 0
    assert count(f.conn, "derivation_recipes") == 0
    assert count(f.conn, "derivation_generations") == generations
    assert not derivations.current(f.conn, unit)
    monkeypatch.undo()
    change_code(f, monkeypatch)
    assert drain_project(f)
    assert derivations.selected_generation(f.conn, unit) == kept
    assert count(f.conn, "derivation_generations") == generations


def test_only_closure_groups_carry_the_leaves_closure_and_retention_follow(
    source_fixture, monkeypatch
):
    """Guard for the closure-stable rule: every followed leaf is under a CLOSURE key."""
    from swingset.state import derivations
    from swingset.state.derivation_dependencies import dependencies, scopes

    f = source_fixture
    add_calendar(f)
    add_dancer(f)
    drain_project(f)
    link_all(f)
    followed = {"derivation", "observation", "source_selection"}
    kinds = set()
    for stage in ("project", "link"):
        for unit in scopes(f.conn, stage):
            kinds.add((unit.stage, unit.unit_kind))
            manifest, sets = dependencies(f.conn, unit, derivations.selected_dependency)
            for entry in manifest:
                if entry["kind"] != "dependency_set":
                    assert entry["kind"] not in followed - {"derivation"}, (unit, entry)
                    continue
                leaves = {member.get("kind") for member in sets[entry["fingerprint"]]}
                if leaves & followed:
                    assert derivations._in_closure(entry), (unit, entry["key"])
    assert kinds >= {
        ("project", kind)
        for kind in ("calendar", "dancer", "inventory", "map", "event", "source_event", "history")
    } | {("link", "event")}
