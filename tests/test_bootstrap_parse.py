import importlib.util
import json
import sys
from pathlib import Path

import pytest

from swingset.admission.policy import activate_contract, record_review
from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, enqueue

MODULE = importlib.util.spec_from_file_location(
    "bootstrap_parse", Path(__file__).parents[1] / "research/bootstrap_parse.py"
)
assert MODULE is not None and MODULE.loader is not None
bootstrap_parse = importlib.util.module_from_spec(MODULE)
sys.modules[MODULE.name] = bootstrap_parse
MODULE.loader.exec_module(bootstrap_parse)


def setup_snapshot(database):
    conn = database.connection
    clock = FakeClock()
    run = database.start_run(clock.now())
    archive = Archive(database.state_dir)
    spec = WatchSpec(
        "",
        "eepro",
        "autoindex",
        "GET",
        "http://eepro.com/results/freedomswing2019/",
        "eepro.autoindex",
        source_ref="eepro:freedomswing2019",
    )
    upsert_watch(conn, spec, clock.now())
    body = Path("tests/fixtures/sources/eepro-archive/freedomswing2019.html").read_bytes()
    sha = archive.store_body(body)
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES ('s',?,'GET',?,?,200,?,?,1,?,'Ok')",
        (spec.watch_id, spec.url, clock.now().isoformat(), sha, len(body), run),
    )
    unit = WorkUnit("parse", "snapshot", "s")
    enqueue(conn, (unit,), enqueued_at=clock.now().isoformat())
    return spec, clock, run, archive, unit


def test_recovered_files_and_accepted_generation_survive_without_new_round_controls(tmp_path):
    with open_database(tmp_path) as db:
        spec, clock, run, archive, unit = setup_snapshot(db)
        parser = bootstrap_parse.BootstrapParser(db, archive, clock, run)
        first = parser.parse(unit)
        assert len(first.gated_watch_ids) == 7
        conn = db.connection
        generation = conn.execute("SELECT generation_id FROM source_generations").fetchone()[0]
        review = record_review(
            conn,
            (generation,),
            reviewer="offline-test",
            reviewed_at=clock.now().isoformat(),
            evidence="Retained index has seven full file hrefs",
        )
        activate_contract(conn, "eepro.autoindex", review)
        second = parser.parse(unit)
        assert second.gated_watch_ids == first.gated_watch_ids
        assert not second.attempt.failed
        assert conn.execute("SELECT count(*) FROM watches").fetchone()[0] == 1
        names = {
            json.loads(row[0])["name"]
            for row in conn.execute("SELECT payload_json FROM observations WHERE kind='file_row'")
        }
        assert len(names) == 7
        assert "jackandjillprelimssemis.html" in names
        selected = conn.execute(
            "SELECT g.result_json FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id"
        ).fetchone()
        assert len(json.loads(selected[0])["watches"]) == 7
        findings = conn.execute("SELECT * FROM findings WHERE kind='acquisition_gate'").fetchall()
        assert len(findings) == 7
        evidence = json.loads(findings[0]["evidence_json"])
        assert evidence["parent_watch_id"] == spec.watch_id
        assert evidence["source_event_ref"] == spec.source_ref
        assert evidence["url"].startswith(spec.url)
        assert evidence["parent_snapshot_id"] == "s"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT count(*) FROM scheduler_parent_links").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM scheduler_watch_state").fetchone()[0] == 0


def test_existing_paused_child_is_preserved_exactly(tmp_path):
    with open_database(tmp_path) as db:
        spec, clock, run, archive, unit = setup_snapshot(db)
        child = WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            spec.url + "allamericanfinals.html",
            "eepro.round",
            source_ref=spec.source_ref,
        )
        upsert_watch(db.connection, child, clock.now(), parent_watch_id=spec.watch_id)
        db.connection.execute(
            "UPDATE watches SET state='paused',next_check_at='2030-01-01',paused_until='2030-02-01',notes='operator hold',priority=17 WHERE watch_id=?",
            (child.watch_id,),
        )
        db.connection.execute(
            "INSERT INTO scheduler_watch_state VALUES (?,3,?)",
            (child.watch_id, clock.now().isoformat()),
        )
        metadata_before = {
            table: [tuple(row) for row in db.connection.execute("SELECT * FROM " + table)]
            for table in ("scheduler_parent_links", "scheduler_watch_state")
        }
        before = tuple(
            db.connection.execute(
                "SELECT * FROM watches WHERE watch_id=?", (child.watch_id,)
            ).fetchone()
        )
        result = bootstrap_parse.BootstrapParser(db, archive, clock, run).parse(unit)
        assert len(result.gated_watch_ids) == 6
        assert {
            table: [tuple(row) for row in db.connection.execute("SELECT * FROM " + table)]
            for table in metadata_before
        } == metadata_before
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            tuple(
                db.connection.execute(
                    "SELECT * FROM watches WHERE watch_id=?", (child.watch_id,)
                ).fetchone()
            )
            == before
        )


@pytest.mark.parametrize("failure", ["crash", "unexpected_index"])
def test_outer_transaction_rolls_back_selection_controls_and_findings(
    tmp_path, monkeypatch, failure
):
    with open_database(tmp_path) as db:
        spec, clock, run, archive, unit = setup_snapshot(db)
        parser = bootstrap_parse.BootstrapParser(db, archive, clock, run)
        if failure == "crash":
            parser.parse(unit)
            generation = db.connection.execute(
                "SELECT generation_id FROM source_generations"
            ).fetchone()[0]
            review = record_review(
                db.connection,
                (generation,),
                reviewer="offline-test",
                reviewed_at=clock.now().isoformat(),
                evidence="Offline crash-boundary fixture review",
            )
            activate_contract(db.connection, "eepro.autoindex", review)
        tables = (
            "watches",
            "snapshots",
            "source_units",
            "source_generations",
            "observations",
            "pending_work",
            "findings",
            "scheduler_parent_links",
            "scheduler_watch_state",
        )
        before = {
            name: [tuple(row) for row in db.connection.execute("SELECT * FROM " + name)]
            for name in tables
        }
        original = bootstrap_parse.parse_snapshot

        def interrupted(*args):
            result = original(*args)
            if failure == "crash":
                raise KeyboardInterrupt("after nested admission transaction")
            upsert_watch(
                db.connection,
                WatchSpec("", "eepro", "index", "GET", spec.url + "unexpected/", "eepro.index"),
                clock.now(),
                parent_watch_id=spec.watch_id,
            )
            return result

        monkeypatch.setattr(bootstrap_parse, "parse_snapshot", interrupted)
        with pytest.raises(KeyboardInterrupt if failure == "crash" else ValueError):
            parser.parse(unit)
        assert {
            name: [tuple(row) for row in db.connection.execute("SELECT * FROM " + name)]
            for name in tables
        } == before


@pytest.mark.parametrize("crash", [False, True])
def test_alternate_parent_does_not_reactivate_gone_paused_existing_child(
    tmp_path, monkeypatch, crash
):
    with open_database(tmp_path) as db:
        spec, clock, run, archive, unit = setup_snapshot(db)
        original_parent = WatchSpec(
            "",
            "eepro",
            "autoindex",
            "GET",
            "http://eepro.com/results/original2019/",
            "eepro.autoindex",
        )
        child = WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            spec.url + "allamericanfinals.html",
            "eepro.round",
            source_ref=spec.source_ref,
        )
        upsert_watch(db.connection, original_parent, clock.now())
        upsert_watch(db.connection, child, clock.now(), parent_watch_id=original_parent.watch_id)
        db.connection.execute(
            "UPDATE watches SET state='gone',next_check_at='2030-01-01',paused_until='2030-02-01',notes='operator hold',priority=17,consecutive_404s=3,first_404_at='2025-12-01' WHERE watch_id=?",
            (child.watch_id,),
        )
        db.connection.execute(
            "INSERT INTO scheduler_watch_state VALUES (?,3,'2025-12-01')", (child.watch_id,)
        )
        child_before = tuple(
            db.connection.execute(
                "SELECT * FROM watches WHERE watch_id=?", (child.watch_id,)
            ).fetchone()
        )
        tables = (
            "scheduler_parent_links",
            "scheduler_watch_state",
            "watches",
            "source_generations",
            "findings",
            "pending_work",
        )
        before = {
            table: [tuple(row) for row in db.connection.execute("SELECT * FROM " + table)]
            for table in tables
        }
        parser = bootstrap_parse.BootstrapParser(db, archive, clock, run)
        original = bootstrap_parse.parse_snapshot

        def interrupted(*args):
            result = original(*args)
            # The real H14 discovery hook reactivates and resets this existing
            # child before the operational bootstrap restores its old controls.
            assert (
                db.connection.execute(
                    "SELECT state FROM watches WHERE watch_id=?", (child.watch_id,)
                ).fetchone()[0]
                != "gone"
            )
            assert (
                db.connection.execute(
                    "SELECT count(*) FROM scheduler_parent_links WHERE child_watch_id=?",
                    (child.watch_id,),
                ).fetchone()[0]
                == 2
            )
            if crash:
                raise KeyboardInterrupt("after alternate-parent reactivation")
            return result

        monkeypatch.setattr(bootstrap_parse, "parse_snapshot", interrupted)
        if crash:
            with pytest.raises(KeyboardInterrupt):
                parser.parse(unit)
            assert {
                table: [tuple(row) for row in db.connection.execute("SELECT * FROM " + table)]
                for table in tables
            } == before
        else:
            result = parser.parse(unit)
            assert len(result.gated_watch_ids) == 6
            assert (
                tuple(
                    db.connection.execute(
                        "SELECT * FROM watches WHERE watch_id=?", (child.watch_id,)
                    ).fetchone()
                )
                == child_before
            )
            for table in ("scheduler_parent_links", "scheduler_watch_state"):
                assert [
                    tuple(row) for row in db.connection.execute("SELECT * FROM " + table)
                ] == before[table]
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            db.connection.execute(
                "SELECT name FROM sqlite_temp_master WHERE name LIKE 'bootstrap_%'"
            ).fetchall()
            == []
        )
