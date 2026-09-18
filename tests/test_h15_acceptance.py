"""Independent H15 acceptance: immutable inputs, atomic output, and derived work."""

import json
import shutil
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
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "unchanged"\n')
    implementation = package / "project.py"
    implementation.write_text("PROJECTOR_VERSION = 19\nVALUE = 'before'\n")
    schema = package / "schema.sql"
    schema.write_text("CREATE TABLE example(value TEXT);\n")
    initial = capture_runtime(package)
    first = captured_recipe_inputs(initial, history_start="2010-01-01")
    implementation.write_text("PROJECTOR_VERSION = 19\nVALUE = 'after'\n")
    changed_code = captured_recipe_inputs(capture_runtime(package), history_start="2010-01-01")
    assert changed_code["recipe/runtime"] != first["recipe/runtime"]
    assert changed_code["policy/history_start"] == first["policy/history_start"]
    schema.write_text("CREATE TABLE example(value TEXT, revision INTEGER);\n")
    changed_schema = captured_recipe_inputs(capture_runtime(package), history_start="2010-01-01")
    assert changed_schema["recipe/runtime"] != changed_code["recipe/runtime"]
    # Retained old artifacts remain sufficient to reproduce their old recipe.
    assert captured_recipe_inputs(initial, history_start="2010-01-01") == first
    corrupted = dict(initial)
    corrupted["runtime/swingset/project.py"] = b"changed without a new manifest"
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


def test_changed_captured_code_replays_scope_without_manual_version_bump(
    source_fixture, monkeypatch
):
    from swingset.fetch.archive import canonical, digest
    from swingset.state import derivations, inputs
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
    retained_runtime = {
        name: body
        for name, body in f.bundle.files.items()
        if name.startswith(("runtime/", "recipes/"))
    }
    name = "runtime/swingset/project/contests.py"
    retained_runtime[name] += b"\n# Independent fixture: changed artifact with unchanged labels.\n"
    manifest = json.loads(retained_runtime["recipes/runtime.json"])
    manifest["files"][name] = digest(retained_runtime[name])
    retained_runtime["recipes/runtime.json"] = canonical(manifest)
    monkeypatch.setattr(inputs, "capture_runtime", lambda: retained_runtime)
    unchanged_labels = json.loads(f.bundle.files["versions.json"])
    newer_bundle = inputs.capture(f.config, f.overrides, f.db.state_dir, unchanged_labels)
    assert newer_bundle.files["versions.json"] == f.bundle.files["versions.json"]
    inputs.accept(f.db, newer_bundle, f.corpus.clock)
    f.conn.execute("DELETE FROM pending_work WHERE stage IN ('project','link')")
    assert not derivations.current(f.conn, unit)
    assert unit in set(derivations.pending_units(f.conn, "project"))
    with f.db.transaction():
        newer = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        derivations.complete(
            f.conn,
            newer,
            rows=write_round_projection(f),
            now=f.corpus.clock.now(),
            run_id=f.corpus.run,
        )
    assert newer.recipe != first.recipe and newer.generation_id != first.generation_id
    assert derivations.current(f.conn, unit)
    assert output_state(f.conn) == before
    assert f.conn.execute("SELECT COUNT(*) FROM derivation_generations").fetchone()[0] == 2


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
