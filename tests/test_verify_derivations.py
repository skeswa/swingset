"""The weekly verify sample finds a read-set miss and is quiet otherwise (plan section 10).

The fixture is test_link_read_sets's: twenty events whose links read a small
registry, with real dancer, event, year and link units.
"""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import test_link_read_sets as linked
from test_event_enumerations import event as event

from swingset.clock import FakeClock
from swingset.loops import maintain
from swingset.loops.runtime import tick
from swingset.project.process import process_unit
from swingset.state import derivations, read_index, verify
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

NOW = linked.NOW


@pytest.fixture
def world(tmp_path):
    with open_database(tmp_path / "state", lock=False) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs (run_id, started_at, dry_run) VALUES ('run', %s, 1)",
            (NOW.isoformat(),),
        )
        linked._events(conn)
        w = linked.World(db=db, conn=conn, clock=FakeClock(NOW), people={}, pages=0)
        for wsdc_id, (first, last) in linked.REGISTRY.items():
            w.registry(wsdc_id, first=first, last=last, pro=wsdc_id == 12)
        for unit in (
            *(WorkUnit("project", "event", event) for event in linked.EVENTS),
            WorkUnit("project", "year", "2026"),
        ):
            process_unit(db, unit, linked.Bundle(), w.clock, "run")
        w.link_all()
        yield w


def _state(conn):
    """Everything a verify must leave as it found it."""
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "derivation_generations",
            "derivation_equivalences",
            "derivation_reads",
            "pending_work",
            "identity_links",
            "link_candidates",
            "runs",
        )
    } | {
        "pointers": conn.execute(
            "SELECT STRING_AGG(materialized_generation_id, ',') FROM (SELECT materialized_generation_id "
            "FROM derivation_scopes ORDER BY stage NULLS FIRST, unit_kind NULLS FIRST, unit_id NULLS "
            "FIRST)"
        ).fetchone()[0],
        "links": conn.execute(
            "SELECT STRING_AGG(subject_id || ':' || COALESCE(wsdc_id::text, ''), ',') FROM (SELECT subject_id, "
            "wsdc_id FROM identity_links ORDER BY subject_id NULLS FIRST)"
        ).fetchone()[0],
    }


def test_a_clean_sample_is_quiet_and_leaves_nothing_behind(world, tmp_path):
    w = world
    before = _state(w.conn)
    summary = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    assert summary["mismatches"] == [] and summary["failures"] == []
    assert summary["kinds"]["link"]["verified"] == len(linked.EVENTS)
    assert summary["kinds"]["link"]["matched"] == len(linked.EVENTS)
    assert summary["kinds"]["year"] == {
        "units": 1,
        "target": 1,
        "verified": 1,
        "matched": 1,
        "not_current": 0,
        "superseded": 0,
        "failed": 0,
    }
    assert _state(w.conn) == before
    assert not (w.db.state_dir / "ALERTS.log").exists()
    assert w.stale() == set()


def test_a_dancer_row_changed_without_a_generation_is_a_read_set_miss(world):
    w = world
    # A dancer whose name some link's candidates matched, renamed in place:
    # no dancer generation moves, so every link still reads as current.
    wsdc_id, event = w.conn.execute(
        "SELECT c.wsdc_id, e.event_id FROM link_candidates AS c JOIN entries AS e ON e.entry_id = "
        "c.subject_id ORDER BY c.wsdc_id NULLS FIRST, e.event_id NULLS FIRST LIMIT 1"
    ).fetchone()
    w.conn.execute(
        "UPDATE dancers SET first_name = 'Zebulon', last_name = 'Quixote' WHERE wsdc_id = %s",
        (wsdc_id,),
    )
    assert w.stale() == set()
    summary = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    missed = {item["unit"] for item in summary["mismatches"]}
    assert f"link/event/{event}" in missed
    item = next(i for i in summary["mismatches"] if i["unit"] == f"link/event/{event}")
    assert item["live"] == item["expected"] != item["found"]
    assert summary["alerted"] is True
    lines = (w.db.state_dir / "ALERTS.log").read_text().splitlines()
    assert any(f"[read-set-miss] link/event/{event}: pointer " in line for line in lines)
    assert lines[0][:20] == NOW.strftime("%Y-%m-%dT%H:%M:%SZ")[:20]
    # Rolled back: the rename is still the only change.
    assert w.stale() == set()


def test_the_sample_is_deterministic_by_week_and_verifies_only_current_units(world):
    w = world
    seed = verify.week(NOW)
    assert seed == "2026-W09"
    order = verify.ranked(w.conn, "link", "event", seed=seed)
    assert order == verify.ranked(w.conn, "link", "event", seed=seed)
    assert order != verify.ranked(w.conn, "link", "event", seed="2026-W10")
    assert verify.target(2934, 0.01) == 30
    assert verify.target(21, 0.01) == 1
    assert verify.target(0, 0.01) == 0
    # The first unit of the week is stale: the sample skips it and takes the next.
    first = order[0]
    dancer = next(
        key for key in sorted(read_index.keys(w.conn, first)) if key.startswith("project/dancer/")
    )
    w.registry(int(dancer.rsplit("/", 1)[1]), recent=2020)
    assert not derivations.current(w.conn, first)
    summary = verify.run(
        w.db, linked.Bundle(), w.clock, "verify_run", fraction=0.05, kinds=["link"]
    )
    counts = summary["kinds"]["link"]
    assert counts["target"] == 1 and counts["verified"] == 1
    assert counts["not_current"] >= 1
    assert summary["mismatches"] == []


def test_kinds_are_checked():
    assert verify.parse_kinds("link, year,link") == ("link", "year")
    with pytest.raises(ValueError):
        verify.parse_kinds("map")
    with pytest.raises(ValueError):
        verify.parse_kinds("")


def test_maintain_verifies_once_a_week(tmp_path):
    config = tmp_path / "config"
    shutil.copytree("config", config)
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    clock = FakeClock(datetime(2026, 9, 29, 3, 30, tzinfo=UTC))

    def once():
        with tick(
            "maintain",
            state=state,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            budget=60,
        ) as current:
            maintain.run(current)
        assert current.record is not None and not current.record["failed"]
        return current.record

    first = once()
    assert first["verify"]["week"] == "2026-W40"
    assert first["verify"]["mismatches"] == []
    clock.current += timedelta(days=1)
    assert "verify" not in once()
    clock.current += timedelta(days=6)
    assert "verify" in once()
    with open_database(state, lock=False) as db:
        rows = db.connection.execute(
            "SELECT COUNT(*) FROM runs WHERE loop = 'maintain' AND NOT "
            "JSON_EXTRACT_PATH_TEXT(CAST(summary_json AS JSON), 'verify', 'week') IS NULL"
        ).fetchone()[0]
    assert rows == 2


def test_the_cli_prints_the_summary(tmp_path, capsys):
    from swingset.cli import main

    state = tmp_path / "state"
    with open_database(state):
        pass
    assert main(["verify-derivations", "--state", str(state), "--sample", "0.5"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert set(summary["kinds"]) == {"link", "year"}
    assert main(["verify-derivations", "--state", str(state), "--kinds", "map"]) == 1


def test_partitions_withheld_counts_by_reason(tmp_path, capsys):
    from swingset.cli import main
    from swingset.state import release_store

    state = tmp_path / "state"
    with open_database(state) as db, db.transaction() as conn:
        for name, reason in (("event/a", "source_revoked"), ("event/b", "dangling_reference")):
            release_store.upsert(
                conn,
                release_store.PartitionRow(
                    name,
                    "d",
                    "withheld",
                    NOW.isoformat(),
                    "run",
                    reason=reason,
                    detail_json='{"events":["a"]}',
                ),
            )
        release_store.upsert(
            conn,
            release_store.PartitionRow(
                "event/c", "d", "built", NOW.isoformat(), "run", artifact_digest="x"
            ),
        )
        conn.execute(
            (
                "INSERT INTO release_partitions (partition, inputs_digest, artifact_digest, state, reason, "
                "detail_json, built_at, run_id) VALUES ('event/d', 'd', NULL, 'withheld', 'source_revoked', "
                "NULL, %s, 'run')"
            ),
            (NOW.isoformat(),),
        )
    assert main(["partitions", "--state", str(state), "--withheld"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["withheld"] == 3
    assert report["by_reason"] == {"source_revoked": 2, "dangling_reference": 1}
    assert [item["partition"] for item in report["partitions"]] == ["event/a", "event/b", "event/d"]
    assert report["partitions"][0]["detail"] == {"events": ["a"]}
    assert main(["partitions", "--state", str(state)]) == 1
    assert main(["partitions", "list", "--state", str(state), "--withheld"]) == 1


def test_a_rerun_that_raises_nondeterministic_output_is_a_read_set_miss(world, monkeypatch):
    """F29: on real data the seeded miss raised NondeterministicOutputError,
    which the sample counted as a failure, so no alert fired. The unit is
    current, so its inputs are the recorded ones: other output is a miss."""
    import swingset.link as link

    w = world
    victim = linked.EVENTS[3]
    original = link.link_event

    def raising(database, event_id, *args, **kwargs):
        if event_id == victim:
            raise derivations.NondeterministicOutputError("derivation output is not deterministic")
        return original(database, event_id, *args, **kwargs)

    monkeypatch.setattr(link, "link_event", raising)
    logged = []
    monkeypatch.setattr(verify, "log", lambda event, **fields: logged.append((event, fields)))
    summary = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    assert summary["failures"] == []
    item = next(i for i in summary["mismatches"] if i["unit"] == f"link/event/{victim}")
    assert item["reason"] == "nondeterministic_output"
    assert item["found"] is None and item["expected"] == item["live"]
    assert summary["kinds"]["link"]["verified"] == len(linked.EVENTS)
    assert ("read_set_miss", item) in logged
    lines = (w.db.state_dir / "ALERTS.log").read_text().splitlines()
    assert any(
        f"[read-set-miss] link/event/{victim}: pointer " in line
        and "nondeterministic_output" in line
        for line in lines
    )


def test_only_a_complete_sample_makes_the_next_one_wait(world, monkeypatch):
    w = world
    calls = iter(range(1000))
    summary = verify.run(
        w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0, stop=lambda: next(calls) > 3
    )
    assert summary["stopped"] is True and summary["complete"] is False
    assert summary["peak_rss_bytes"] > 0 and summary["longest_unit_seconds"] > 0
    full = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    assert full["complete"] is True

    def record(run_id, value):
        w.conn.execute(
            (
                "INSERT INTO runs (run_id, started_at, finished_at, dry_run, loop, summary_json) VALUES (%s, "
                "%s, %s, 1, 'maintain', %s)"
            ),
            (run_id, NOW.isoformat(), NOW.isoformat(), json.dumps({"verify": value})),
        )

    later = NOW + timedelta(hours=1)
    assert verify.due(w.conn, now=later)
    record("partial", summary)
    assert verify.due(w.conn, now=later)
    # Not stopped, but short of the target: failed units verify nothing.
    with monkeypatch.context() as patch:
        patch.setattr(
            verify, "verify_unit", lambda *a, **k: {"unit": "u", "state": "failed", "error": "E"}
        )
        short = verify.run(w.db, linked.Bundle(), w.clock, "verify_run", fraction=1.0)
    assert short["stopped"] is False and short["failures"]
    assert any(counts["verified"] < counts["target"] for counts in short["kinds"].values())
    assert short["complete"] is False
    record("short", short)
    assert verify.due(w.conn, now=later)
    record("whole", full)
    assert not verify.due(w.conn, now=later)
    assert verify.due(w.conn, now=NOW + verify.INTERVAL + timedelta(seconds=1))


def test_graph_receipt_requires_exact_complete_coverage():
    from swingset.fetch.archive import canonical, digest
    from swingset.state import verify_graph

    names = ["project/year/2026", "link/event/a"]
    good = {
        "format": verify_graph.FORMAT,
        "expected_units": names,
        "unit_set_digest": digest(canonical(names)),
        "results": [{"unit": n, "state": "match", "generation_id": "g"} for n in names],
        "audit": {"recipe/env": "new"},
        "versions": {},
        "inputs": {},
        "checkpoint": "frozen",
        "complete": True,
        "clean": True,
        "errors": [],
    }
    assert verify_graph.validate_receipt(good, audit=good["audit"], checkpoint="frozen")
    assert not verify_graph.validate_receipt(good, audit={"recipe/env": "old"})
    for results in (
        good["results"][:-1],
        good["results"] * 2,
        [*good["results"][:-1], {"unit": names[-1], "state": "failed"}],
        [*good["results"][:-1], {"unit": names[-1], "state": "superseded"}],
    ):
        assert not verify_graph.validate_receipt(good | {"results": results})
    assert not verify_graph.validate_receipt(good | {"errors": ["interrupted"]})
    different = [good["results"][0], good["results"][1] | {"state": "mismatch"}]
    assert not verify_graph.validate_receipt(good | {"results": different})
    assert verify_graph.validate_receipt(good | {"results": different, "clean": False})


def test_graph_verification_runs_on_copies_and_binds_dependencies(world, tmp_path):
    from swingset.state import verify_graph

    before = _state(world.conn)
    receipt = verify_graph.run(
        world.db.state_dir, linked.Bundle(), scopes=["link"], receipt_path=tmp_path / "receipt.json"
    )
    assert receipt["complete"], receipt
    assert receipt["clean"], receipt
    assert verify_graph.validate_receipt(receipt)
    assert len(receipt["results"]) == len(linked.EVENTS)
    assert all(
        result["expected_dependencies"] in result["found_dependencies"]
        for result in receipt["results"]
    )
    assert _state(world.conn) == before
    assert json.loads((tmp_path / "receipt.json").read_text()) == receipt


def test_graph_source_replay_compares_extract_and_parse_and_shards(tmp_path):
    from test_admission import Corpus

    from swingset.state import verify_graph

    with open_database(tmp_path / "source", lock=False) as db:
        corpus = Corpus(db)
        generation, _ = corpus.stage(corpus.snapshot("one"))
        corpus.review(generation)
        assert corpus.admit(generation) == "accepted"
        receipt = verify_graph.run(
            db.state_dir, linked.Bundle(), scopes=["parse", "extract"], processes=2
        )
        assert receipt["complete"] and receipt["clean"], receipt
        assert len(receipt["results"]) == 2
        assert all(r["generation_id"] == generation for r in receipt["results"])
        assert verify_graph.validate_receipt(receipt)


def test_postgres_frozen_copy_pins_all_tables_and_preserves_sequence_floors(tmp_path, monkeypatch):
    import psycopg

    from swingset.dev.services import ensure_service
    from swingset.state.postgres_copy import Copies

    service = ensure_service()
    with open_database(tmp_path / "source", lock=False) as source:
        source.connection.execute(
            "INSERT INTO ops.meta(key,value) VALUES('copy-left','old'),('copy-right','old')"
        )
        source.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES('copy-sequence','2026-01-01T00:00:00+00:00',1)"
        )
        floor = source.connection.execute(
            "SELECT nextval(pg_get_serial_sequence('fetch.watches','rowid')) AS value"
        ).fetchone()[0]
        source_id = source.connection.execute(
            "SELECT database_id FROM ops.environment_identity"
        ).fetchone()[0]
        with service.connect(source.connection.info.dbname) as admin:
            admin.execute("UPDATE ops.environment_identity SET environment='production'")
        original = psycopg.Cursor.copy
        changed = False

        def concurrent_change(cursor, statement, *args, **kwargs):
            nonlocal changed
            if cursor.connection is source.connection and not changed:
                # The frozen read snapshot already exists, but no table's
                # binary stream has been copied yet.
                with service.connect(source.connection.info.dbname) as writer:
                    writer.execute(
                        "UPDATE ops.meta SET value='new' WHERE key IN('copy-left','copy-right')"
                    )
                changed = True
            return original(cursor, statement, *args, **kwargs)

        monkeypatch.setattr(psycopg.Cursor, "copy", concurrent_change)
        with Copies(source.state_dir) as copies:
            state = tmp_path / "frozen"
            copies.freeze(source.connection, state)
            with open_database(state, lock=False) as frozen:
                assert frozen.environment == "dev"
                assert (
                    frozen.connection.execute(
                        "SELECT database_id FROM ops.environment_identity"
                    ).fetchone()[0]
                    != source_id
                )
                assert [
                    row[0]
                    for row in frozen.connection.execute(
                        "SELECT value FROM ops.meta WHERE key IN('copy-left','copy-right') ORDER BY key"
                    )
                ] == ["old", "old"]
                assert (
                    frozen.connection.execute(
                        "SELECT nextval(pg_get_serial_sequence('fetch.watches','rowid')) AS value"
                    ).fetchone()[0]
                    > floor
                )
        assert changed
        assert [
            row[0]
            for row in source.connection.execute(
                "SELECT value FROM ops.meta WHERE key IN('copy-left','copy-right') ORDER BY key"
            )
        ] == ["new", "new"]


def test_failed_postgres_copy_removes_only_its_owned_database(tmp_path, monkeypatch):
    import psycopg

    from swingset.dev.services import ensure_service
    from swingset.state.postgres_copy import Copies

    service = ensure_service()
    with open_database(tmp_path / "source", lock=False) as source:
        identifier = source.connection.info.dbname

        def fail(*args, **kwargs):
            raise RuntimeError("interrupted binary stream")

        monkeypatch.setattr(psycopg.Cursor, "copy", fail)
        with pytest.raises(RuntimeError, match="interrupted binary stream"):
            with Copies(source.state_dir) as copies:
                copies.freeze(source.connection, tmp_path / "failed-copy")
        with service.connect() as admin:
            names = {row[0] for row in admin.execute("SELECT datname FROM pg_database")}
        assert identifier in names
        assert not set(copies.record["databases"]) & names


def test_graph_new_dependency_is_difference_even_with_identical_output(world, monkeypatch):
    from swingset.state import derivation_dependencies, verify_graph

    original = derivation_dependencies.dependencies

    def extra(conn, unit, selected, **kwargs):
        dependencies, groups = original(conn, unit, selected, **kwargs)
        if unit.stage == "link":
            dependencies.append(selected(conn, WorkUnit("project", "dancer", "never-present")))
        return dependencies, groups

    monkeypatch.setattr(derivation_dependencies, "dependencies", extra)
    name = f"link/event/{linked.EVENTS[0]}"
    result = verify_graph._derive(world.db, name, linked.Bundle(), world.clock)
    assert result["state"] == "mismatch", result
    assert result["reason"] == "dependencies_changed"
    assert result["found"] == result["expected"]


def test_graph_entire_projection_and_link_fixture(world, tmp_path, monkeypatch):
    from swingset.state import verify_graph

    spill = tmp_path / "writable-spill"
    spill.mkdir()
    monkeypatch.setenv("TMPDIR", str(spill))
    replay = verify_graph._shard
    scratch = []

    def private_replay(clone, *arguments):
        root = Path(clone).parent
        assert root.parent == spill
        scratch.append(root)
        return replay(clone, *arguments)

    monkeypatch.setattr(verify_graph, "_shard", private_replay)
    before = _state(world.conn)
    receipt = verify_graph.run(world.db.state_dir, linked.Bundle())
    assert receipt["complete"] and receipt["clean"], receipt
    stages = {r["unit"].split("/")[0] for r in receipt["results"]}
    assert stages == {"project", "link"}
    kinds = {r["unit"].split("/")[1] for r in receipt["results"]}
    assert {"dancer", "event", "year"} <= kinds
    assert scratch and all(not path.exists() for path in scratch)
    assert not list(spill.iterdir())
    assert _state(world.conn) == before


def test_graph_replays_the_33_page_event_cohort(event):
    from test_event_enumerations import admit_parent, child, finish_bootstrap

    from swingset.state import verify_graph

    names = [f"round-{number:02}.htm" for number in range(33)]
    admit_parent(event, names)
    finish_bootstrap(event)
    for name in names:
        child(event, name)
    receipt = verify_graph.run(event.db.state_dir, linked.Bundle(), processes=2)
    assert receipt["complete"] and receipt["clean"], receipt
    assert len(receipt["results"]) == 68  # parent + 33 rounds; extract and parse
    assert verify_graph.validate_receipt(receipt)


def test_graph_ignores_retired_scopes_with_retained_pointers(world):
    from swingset.state import verify_graph

    generation = world.conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes WHERE stage = 'project' LIMIT 1"
    ).fetchone()[0]
    for kind, identifier in (("history", "all"), ("map", "all"), ("year", "all")):
        world.conn.execute(
            (
                "INSERT INTO derivation_scopes (stage, unit_kind, unit_id, materialized_generation_id, "
                "registered_at) VALUES ('project', %s, %s, %s, '2026-01-01T00:00:00+00:00')"
            ),
            (kind, identifier, generation),
        )
    names = verify_graph.enumerate_units(world.conn)
    assert all(
        name not in names for name in ("project/history/all", "project/map/all", "project/year/all")
    )


def test_graph_clone_fallback_removes_partial_copy_and_never_hardlinks(tmp_path, monkeypatch):
    import subprocess

    from swingset.state import verify_graph

    source, destination = tmp_path / "source.sqlite", tmp_path / "clone.sqlite"
    source.write_bytes(b"frozen checkpoint")

    def unsupported(*args, **kwargs):
        destination.write_bytes(b"partial failed clone")
        return subprocess.CompletedProcess(args[0], 1)

    monkeypatch.setattr(verify_graph.subprocess, "run", unsupported)
    assert verify_graph._clone_checkpoint(source, destination) == "full_copy"
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_ino != source.stat().st_ino
    destination.write_bytes(b"scratch writes")
    assert source.read_bytes() == b"frozen checkpoint"
