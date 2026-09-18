"""One walk, two lists, one plan: the retention planner of bounded-state step 3.

Each test is named after a row of the plan's section 10 table.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from swingset.state import derivations, retention
from swingset.state.db import open_database
from swingset.state.retention import (
    RetentionError,
    add_hold,
    enforce_size_cap,
    holds,
    plan,
    report,
    write_plan,
)

NOW = datetime(2026, 9, 18, tzinfo=UTC)
KNOBS = {"max_database_bytes": 1 << 40, "recent_window": 1, "collect_older_than": 86400.0}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dependency_set(conn: sqlite3.Connection, members: list[dict[str, Any]]) -> str:
    """One interned manifest, addressed exactly as the shared reader checks it."""
    text = canonical(members)
    identifier = hashlib.sha256(text.encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)", (identifier, text)
    )
    return identifier


def generation(
    conn: sqlite3.Connection,
    run: str,
    generation_id: str,
    scope: tuple[str, str, str],
    *,
    depends_on: tuple[tuple[str, tuple[str, str, str]], ...] = (),
    rows: tuple[tuple[str, str, dict[str, Any]], ...] = (),
    created_at: datetime = NOW,
    nested: bool = False,
) -> str:
    members = [
        {"kind": "derivation", "key": list(key), "generation_id": identifier}
        for identifier, key in depends_on
    ]
    if nested and members:
        inner = dependency_set(conn, members)
        members = [{"kind": "dependency_set", "key": "selected", "fingerprint": inner}]
    set_id = dependency_set(conn, members)
    output = hashlib.sha256()
    # Interning is the last migration, so this fixture writes whichever shape
    # the database it was given actually has.
    shared = derivations.interned(conn)
    for ordinal, (table, key, payload) in enumerate(rows):
        stored = canonical(payload)
        if shared:
            digest = hashlib.sha256(stored.encode()).hexdigest()
            conn.execute("INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (digest, stored))
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
                (generation_id, ordinal, table, key, digest),
            )
        else:
            conn.execute(
                "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
                (generation_id, ordinal, table, key, stored),
            )
        output.update(canonical((table, key, stored)).encode() + b"\n")
    conn.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
        "VALUES (?,?,?,?)",
        (*scope, NOW.isoformat()),
    )
    conn.execute(
        "INSERT INTO derivation_generations VALUES (?,?,?,?,?,'{}',?,NULL,?,?,?,?)",
        (
            generation_id,
            *scope,
            "fingerprint-" + generation_id,
            set_id,
            output.hexdigest(),
            len(rows),
            created_at.isoformat(),
            run,
        ),
    )
    return generation_id


def point_at(conn: sqlite3.Connection, scope: tuple[str, str, str], generation_id: str) -> None:
    conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id=? "
        "WHERE stage=? AND unit_kind=? AND unit_id=?",
        (generation_id, *scope),
    )


PROJECT_A = ("project", "event", "event-a")
PROJECT_B = ("project", "event", "event-b")
LINK_A = ("link", "event", "event-a")


def history(database: Any) -> None:
    """Two project generations per scope, and a link built on the older one."""
    conn = database.connection
    run = database.start_run(NOW)
    with database.transaction():
        generation(conn, run, "dg_a1", PROJECT_A, rows=(("entries", "a-1", {"n": 1}),))
        generation(
            conn,
            run,
            "dg_a2",
            PROJECT_A,
            rows=(("entries", "a-1", {"n": 2}),),
            created_at=NOW + timedelta(hours=1),
        )
        generation(conn, run, "dg_b1", PROJECT_B, rows=(("entries", "b-1", {"n": 3}),))
        generation(
            conn,
            run,
            "dg_b2",
            PROJECT_B,
            rows=(("entries", "b-1", {"n": 4}),),
            created_at=NOW + timedelta(hours=1),
        )
        # The link was built from the older project generation, which the
        # project scope has since moved past.
        generation(
            conn,
            run,
            "dg_link_a",
            LINK_A,
            depends_on=(("dg_a1", PROJECT_A),),
            rows=(("identity_links", "a-1", {"wsdc_id": 7}),),
            created_at=NOW + timedelta(hours=2),
            nested=True,
        )
        point_at(conn, PROJECT_A, "dg_a2")
        point_at(conn, PROJECT_B, "dg_b2")
        point_at(conn, LINK_A, "dg_link_a")


def listed(content: dict[str, Any], name: str) -> set[str]:
    return {row["generation_id"] for row in content["generations"] if row["list"] == name}


def reasons(content: dict[str, Any], generation_id: str) -> list[str]:
    for row in content["generations"]:
        if row["generation_id"] == generation_id:
            return list(row["why"])
    raise AssertionError(f"no plan row for {generation_id}")


def planned(database: Any, **overrides: Any) -> dict[str, Any]:
    knobs = {**KNOBS, **overrides}
    return plan(database.connection, database.state_dir, **knobs).content


def test_the_plan_is_recomputed_on_unchanged_state(tmp_path: Path) -> None:
    """Same file, same fingerprint."""
    with open_database(tmp_path / "state", lock=False) as database:
        history(database)
        first = plan(database.connection, database.state_dir, **KNOBS)
        second = plan(database.connection, database.state_dir, **KNOBS)
        assert first.digest == second.digest
        assert first.bytes() == second.bytes()
        path = write_plan(database.state_dir, first)
        assert path == write_plan(database.state_dir, second)
        assert path.read_bytes() == first.bytes()
        # The digest is of the plan without itself, so it can be rechecked.
        content = json.loads(path.read_text())
        without = {key: value for key, value in content.items() if key != "digest"}
        assert retention._digest(without) == first.digest
        assert "created_at" not in content and "at" not in content


def test_the_window_is_2_and_one_scope_has_two_recent_generations(tmp_path: Path) -> None:
    """The walk keeps both; release validation is unchanged."""
    with open_database(tmp_path / "state", lock=False) as database:
        history(database)
        one = planned(database, recent_window=1)
        two = planned(database, recent_window=2)
        assert "dg_b1" not in listed(one, "local")
        assert {"dg_b1", "dg_b2"} <= listed(two, "local")
        assert reasons(two, "dg_b1") == ["window"]
        # The release selector still refuses two generations for one scope, so
        # the window cannot be served by it and does not change it.
        from swingset.build.closure import ClosureError, _Selection

        selection = _Selection(database.connection, (NOW + timedelta(days=1)).isoformat())
        selection.add("dg_b2")
        with pytest.raises(ClosureError, match="mixed_dependency_generations"):
            selection.selected[PROJECT_B] = {"generation_id": "dg_b2"}
            selection.add("dg_b1")


def test_a_superseded_generation_leaves_the_window_with_no_dependents(tmp_path: Path) -> None:
    """It can be archived; it is not "unknown"."""
    with open_database(tmp_path / "state", lock=False) as database:
        history(database)
        content = planned(database, recent_window=1)
        assert "dg_b1" in listed(content, "archivable")
        assert reasons(content, "dg_b1") == []
        assert content["unknown"]["orphan_payloads"] == []
        assert content["unknown"]["orphan_row_references"] == []
        assert "dg_b1" not in content["unknown"]["missing_root_generations"]
        # Durable is every labelled generation, so it is still accounted for.
        assert {row["generation_id"] for row in content["generations"]} == {
            "dg_a1",
            "dg_a2",
            "dg_b1",
            "dg_b2",
            "dg_link_a",
        }


def test_a_recent_link_generation_depends_on_an_old_project_generation(tmp_path: Path) -> None:
    """The project generation stays local because the walk reached it."""
    with open_database(tmp_path / "state", lock=False) as database:
        history(database)
        content = planned(database, recent_window=1)
        assert "dg_a1" in listed(content, "local")
        # Not because it is recent: its scope has moved on and the window is 1.
        assert reasons(content, "dg_a1") == ["pointer", "window"]
        assert reasons(content, "dg_b1") == []


def test_removing_each_kind_of_root_frees_only_what_depended_on_it_alone(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        history(database)
        run = database.start_run(NOW + timedelta(minutes=1))
        with database.transaction():
            # One generation reached only through a hold, one only through an
            # open finding, in a scope whose pointer is elsewhere.
            generation(conn, run, "dg_held", ("project", "event", "event-h"))
            generation(
                conn,
                run,
                "dg_current_h",
                ("project", "event", "event-h"),
                created_at=NOW + timedelta(hours=3),
            )
            point_at(conn, ("project", "event", "event-h"), "dg_current_h")
            generation(conn, run, "dg_found", ("project", "event", "event-f"))
            generation(
                conn,
                run,
                "dg_current_f",
                ("project", "event", "event-f"),
                created_at=NOW + timedelta(hours=3),
            )
            point_at(conn, ("project", "event", "event-f"), "dg_current_f")
            conn.execute(
                "INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,subject_id,"
                "severity,summary,evidence_json,opened_at,run_id) "
                "VALUES ('f1','review','r1','missing_identity','event','event-f','warning',"
                "'needs a generation','{}',?,?)",
                (NOW.isoformat(), run),
            )
            conn.execute(
                "INSERT INTO finding_support_references VALUES ('f1','generation','dg_found')"
            )
        record = add_hold(
            database.state_dir,
            conn,
            who="operator",
            why="investigating event-h",
            now=NOW,
            generations=("dg_held",),
        )
        with_roots = planned(database, recent_window=1)
        assert {"dg_held", "dg_found"} <= listed(with_roots, "local")
        assert reasons(with_roots, "dg_held") == ["hold"]
        assert reasons(with_roots, "dg_found") == ["finding"]

        retention.remove_hold(database.state_dir, record["hold_id"])
        without_hold = planned(database, recent_window=1)
        assert "dg_held" in listed(without_hold, "archivable")
        assert listed(with_roots, "local") - listed(without_hold, "local") == {"dg_held"}

        with database.transaction():
            conn.execute(
                "UPDATE findings SET closed_at=? WHERE finding_id='f1'", (NOW.isoformat(),)
            )
        without_finding = planned(database, recent_window=1)
        assert listed(without_hold, "local") - listed(without_finding, "local") == {"dg_found"}

        # With no window the pointers are the only roots left, so clearing them
        # frees exactly what they reached and nothing else is disturbed.
        with_pointers = planned(database, recent_window=0)
        assert "dg_a1" in listed(with_pointers, "local")
        with database.transaction():
            conn.execute("UPDATE derivation_scopes SET materialized_generation_id=NULL")
        without_pointers = planned(database, recent_window=0)
        assert listed(without_pointers, "local") == set()
        assert "dg_a1" in listed(without_pointers, "archivable")


def test_an_unexplained_piece_of_row_data_or_reference_appears(tmp_path: Path) -> None:
    """Doctor reports it; it is never eligible."""
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
        with database.transaction():
            database.connection.execute(
                "INSERT INTO derivation_payloads VALUES (?,?)",
                (hashlib.sha256(b'{"orphan":1}').hexdigest(), '{"orphan":1}'),
            )
    # A reference whose label is missing can only be written with the deferred
    # foreign key switched off, which is exactly what makes it unexplained.
    raw = sqlite3.connect(state / "state.sqlite")
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute(
        "INSERT INTO derivation_row_refs VALUES ('dg_ghost',0,'entries','x',?)",
        (hashlib.sha256(b'{"orphan":1}').hexdigest(),),
    )
    raw.commit()
    raw.close()
    with open_database(state, lock=False) as database:
        content = planned(database, recent_window=1)
        assert content["unknown"]["orphan_row_references"] == ["dg_ghost"]
        assert content["unknown"]["orphan_payloads"] == []
        assert "dg_ghost" not in listed(content, "archivable")
        assert "dg_ghost" not in listed(content, "local")
        # An undeclared file is unknown too, and stays out of both lists.
        blob = state / "blobs" / "sha256" / "ab" / "cd" / ("ab" + "cd" * 31)
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"nobody declares this")
        content = planned(database, recent_window=1)
        assert content["unknown"]["undeclared_files"] == [
            blob.relative_to(state).as_posix(),
        ]
        assert content["totals"]["unknown_file_bytes"] == len(b"nobody declares this")
        assert all(row["list"] != "removable" for row in content["files"])


def test_a_hold_is_requested_for_a_generation_whose_data_is_archived(tmp_path: Path) -> None:
    """Refused with the generation ids; nothing written."""
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        history(database)
        # Step 4 will archive payload bytes through the permission row. Until it
        # exists, removing them under that same gate is how the refusal is
        # exercised: the label and references stay, the bytes do not.
        with database.transaction():
            conn.execute(
                "INSERT INTO derivation_payload_removal_authority VALUES (1,'test',?)",
                (NOW.isoformat(),),
            )
            conn.execute(
                "DELETE FROM derivation_payloads WHERE payload_sha256 IN "
                "(SELECT payload_sha256 FROM derivation_row_refs WHERE generation_id='dg_a1')"
            )
            conn.execute("DELETE FROM derivation_payload_removal_authority")
        with pytest.raises(RetentionError, match="dg_a1") as error:
            add_hold(
                database.state_dir,
                conn,
                who="operator",
                why="needs the old project rows",
                now=NOW,
                generations=("dg_link_a",),
            )
        assert "gc --restore" in str(error.value)
        assert holds(database.state_dir) == []
        assert not (database.state_dir / "holds").exists()
        # The generation whose bytes are still local is accepted.
        record = add_hold(
            database.state_dir,
            conn,
            who="operator",
            why="keeping event-b",
            now=NOW,
            generations=("dg_b1",),
        )
        assert [item["hold_id"] for item in holds(database.state_dir)] == [record["hold_id"]]
        # A hold on a file that is not there is refused for the same reason.
        with pytest.raises(RetentionError, match="blobs/ or extracts/"):
            add_hold(
                database.state_dir,
                conn,
                who="operator",
                why="keeping a page",
                now=NOW,
                artifacts=(hashlib.sha256(b"never stored").hexdigest(),),
            )
        assert len(holds(database.state_dir)) == 1


def test_naming_a_generation_in_a_finding_gets_the_same_check(tmp_path: Path) -> None:
    from swingset.state.findings import Finding, Reference, replace_findings

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        history(database)
        with database.transaction():
            conn.execute(
                "INSERT INTO derivation_payload_removal_authority VALUES (1,'test',?)",
                (NOW.isoformat(),),
            )
            conn.execute(
                "DELETE FROM derivation_payloads WHERE payload_sha256 IN "
                "(SELECT payload_sha256 FROM derivation_row_refs WHERE generation_id='dg_a1')"
            )
            conn.execute("DELETE FROM derivation_payload_removal_authority")
        run = database.start_run(NOW + timedelta(minutes=2))
        finding = Finding(
            "missing_identity",
            "event",
            "event-a",
            "warning",
            "needs the link generation",
            {},
            references=(Reference("generation", "dg_link_a"),),
        )
        with pytest.raises(RetentionError, match="dg_a1"):
            with database.transaction():
                replace_findings(
                    conn,
                    owner_kind="review",
                    owner_id="event-a",
                    findings=(finding,),
                    opened_at=NOW.isoformat(),
                    run_id=run,
                )
        assert conn.execute("SELECT count(*) FROM findings").fetchone()[0] == 0
        with database.transaction():
            assert replace_findings(
                conn,
                owner_kind="review",
                owner_id="event-b",
                findings=(
                    Finding(
                        "missing_identity",
                        "event",
                        "event-b",
                        "warning",
                        "needs the project generation",
                        {},
                        references=(Reference("generation", "dg_b1"),),
                    ),
                ),
                opened_at=NOW.isoformat(),
                run_id=run,
            )
        assert [
            tuple(row) for row in conn.execute("SELECT kind,sha256 FROM finding_support_references")
        ] == [("generation", "dg_b1")]


def test_the_database_file_reaches_the_size_cap(tmp_path: Path) -> None:
    """An operator pause is set; controls and recovery still work."""
    from swingset.state.controls import Selector, change_control, status

    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
    result = enforce_size_cap(state, max_database_bytes=1, now=NOW)
    assert result["over_cap"] and result["paused"]
    # Nothing in this step shrinks the file, so the result names the one thing
    # an operator can actually do about it.
    assert result["remedy"] == retention.SIZE_CAP_REMEDY
    with open_database(state, lock=False, read_only=True) as database:
        pauses = database.connection.execute(
            "SELECT scope_kind,scope_id,reason FROM operator_pauses"
        ).fetchall()
        assert [tuple(row) for row in pauses] == [("all", "all", retention.SIZE_CAP_REASON)]
        # Reading keeps working while the pause stands.
        assert (
            database.connection.execute("SELECT count(*) FROM derivation_generations").fetchone()[0]
            == 5
        )
        assert status(database.connection, now=NOW)["state"] != "running"
    # Repeating it does not rewrite the pause.
    again = enforce_size_cap(state, max_database_bytes=1, now=NOW + timedelta(hours=1))
    assert not again["paused"]
    assert again["existing_pause_reason"] == retention.SIZE_CAP_REASON
    # Controls still work: the operator can resume.
    change_control(
        state,
        selector=Selector("all", "all"),
        paused=False,
        actor="operator",
        reason="cleared after gc",
        now=NOW + timedelta(hours=2),
    )
    with open_database(state, lock=False, read_only=True) as database:
        assert (
            database.connection.execute("SELECT count(*) FROM operator_pauses").fetchone()[0] == 0
        )
    # Resuming alone is not a way out: the file is still over the cap, so the
    # next cycle pauses again. Raising the knob is, and that is what the remedy
    # and both operator documents say.
    again_paused = enforce_size_cap(
        state, max_database_bytes=1, now=NOW + timedelta(hours=2, minutes=1)
    )
    assert again_paused["paused"]
    change_control(
        state,
        selector=Selector("all", "all"),
        paused=False,
        actor="operator",
        reason="raised the cap",
        now=NOW + timedelta(hours=2, minutes=2),
    )
    raised = enforce_size_cap(
        state, max_database_bytes=1 << 40, now=NOW + timedelta(hours=2, minutes=3)
    )
    assert not raised["over_cap"] and not raised["paused"]
    with open_database(state, lock=False, read_only=True) as database:
        assert (
            database.connection.execute("SELECT count(*) FROM operator_pauses").fetchone()[0] == 0
        )
    # Recovery still works: a restore marker stops the cap writing anything.
    (state / "RESTORE_PENDING").write_bytes(b"verification pending\n")
    during_restore = enforce_size_cap(state, max_database_bytes=1, now=NOW + timedelta(hours=3))
    assert during_restore["over_cap"] and not during_restore["paused"]
    (state / "RESTORE_PENDING").unlink()
    # Under the cap nothing is paused at all.
    assert not enforce_size_cap(state, max_database_bytes=1 << 40, now=NOW)["over_cap"]


def test_doctor_reports_local_archivable_and_unknown(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        history(database)
        measured = report(database.connection, database.state_dir, **KNOBS)
        assert measured["usage"]["file_bytes"] > 0
        assert measured["usage"]["bytes_in_use"] is not None
        assert measured["usage"]["free_list_bytes"] >= 0
        assert measured["usage"]["wal_bytes"] >= 0
        assert measured["over_size_cap"] is False
        totals = measured["totals"]
        assert totals["local_generations"] + totals["archivable_generations"] == 5
        assert totals["local_payload_bytes"] > 0
        assert totals["archivable_payload_bytes"] >= 0
        assert set(totals["local_generations_by_root"]) <= set(retention.ROOT_KINDS)
        assert measured["local_generations"][0]["why"]


def test_doctor_names_the_unknown_items_and_not_only_how_many(tmp_path: Path) -> None:
    """Nothing removes an unknown item, so somebody has to go and look at it.

    A count says there is something to work out. The digest or the path says
    what, and an operator should not have to write a plan file out to read it.
    """
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
        orphan = hashlib.sha256(b'{"orphan":1}').hexdigest()
        with database.transaction():
            database.connection.execute(
                "INSERT INTO derivation_payloads VALUES (?,?)", (orphan, '{"orphan":1}')
            )
        blob = state / "blobs" / "sha256" / "ab" / "cd" / ("ab" + "cd" * 31)
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"nobody declares this")
        measured = report(database.connection, database.state_dir, **KNOBS)
        assert measured["unknown"]["orphan_payloads"] == 1
        assert measured["unknown_items"]["orphan_payloads"] == [orphan]
        assert measured["unknown_items"]["undeclared_files"] == [blob.relative_to(state).as_posix()]
        # Only the kinds that have something are listed, and a long list is cut
        # to the report's own limit while the count above stays whole.
        assert set(measured["unknown_items"]) == {"orphan_payloads", "undeclared_files"}
        short = report(database.connection, database.state_dir, **KNOBS, detail_limit=0)
        assert short["unknown_items"]["orphan_payloads"] == []
        assert short["unknown"]["orphan_payloads"] == 1


def test_checkpoint_tests_still_pass_with_the_moved_closure(tmp_path: Path) -> None:
    """The checkpoint uses the one closure, and its error name still means it."""
    from swingset.backup import checkpoint as checkpoint_module

    assert checkpoint_module._artifact_closure is retention.artifact_closure
    assert checkpoint_module.CheckpointError is RetentionError
    # The direct collector that used to be re-exported here is gone. Nothing
    # removes a file except `gc --apply`, from a written plan, under both locks.
    assert not hasattr(checkpoint_module, "garbage_collect")
    assert not hasattr(retention, "garbage_collect")


def test_declared_references_replace_the_evidence_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 30 backfills what the closure used to guess from evidence."""
    from swingset.state import db as db_module

    state = tmp_path / "state"
    body = hashlib.sha256(b"evidence").hexdigest()
    absent = hashlib.sha256(b"no such file").hexdigest()
    with monkeypatch.context() as patch:
        # The last schema with no declarations in it, so the backfill has
        # something to find.
        patch.setattr(db_module, "SCHEMA_VERSION", 29)
        with open_database(state, lock=False) as database:
            blob = state / "blobs" / "sha256" / body[:2] / body[2:4] / body
            blob.parent.mkdir(parents=True)
            blob.write_bytes(b"evidence")
            run = database.start_run(NOW)
            with database.transaction():
                database.connection.execute(
                    "INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,"
                    "subject_id,severity,summary,evidence_json,opened_at,run_id) "
                    "VALUES ('f1','crosscheck','registry','registry_diff','dancer','7','warning',"
                    "'differs',?,?,?)",
                    (
                        json.dumps({"body_sha256": body, "other": absent}),
                        NOW.isoformat(),
                        run,
                    ),
                )
    with open_database(state, lock=False) as database:
        conn = database.connection
        assert database.schema_version == db_module.SCHEMA_VERSION
        assert [
            tuple(row)
            for row in conn.execute("SELECT finding_id,kind,sha256 FROM finding_support_references")
        ] == [("f1", "body", body)]
        # The closure now keeps that file because the finding declares it.
        assert (state / "blobs" / "sha256" / body[:2] / body[2:4] / body).resolve() in (
            retention.artifact_closure(state, conn, set())
        )


def test_a_backup_carries_holds_and_leaves_plans_out(tmp_path: Path) -> None:
    from swingset.backup.checkpoint import create_checkpoint

    state = tmp_path / "state"
    body = hashlib.sha256(b"held page").hexdigest()
    with open_database(state, lock=False) as database:
        history(database)
        blob = state / "blobs" / "sha256" / body[:2] / body[2:4] / body
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"held page")
        record = add_hold(
            state,
            database.connection,
            who="operator",
            why="keeping event-b",
            now=NOW,
            generations=("dg_b1",),
            artifacts=(body,),
        )
        # A held file is on the local list and in the checkpoint closure, even
        # though no snapshot, finding or candidate declares it.
        content = planned(database, recent_window=1)
        assert [row for row in content["files"] if row["why"] == "hold"] == [
            {
                "path": blob.relative_to(state).as_posix(),
                "list": "local",
                "why": "hold",
                "bytes": len(b"held page"),
            }
        ]
        assert content["unknown"]["undeclared_files"] == []
        write_plan(state, plan(database.connection, state, **KNOBS))
        checkpoint = create_checkpoint(
            state,
            database.connection,
            tmp_path / "checkpoint",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
    assert f"holds/{record['hold_id']}.json" in checkpoint.files
    assert blob.relative_to(state).as_posix() in checkpoint.files
    assert not [name for name in checkpoint.files if name.startswith("gc/")]


def test_the_hold_command_reads_and_writes_holds(tmp_path: Path) -> None:
    from swingset.cli import main

    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
    assert (
        main(
            [
                "hold",
                "add",
                "--state",
                str(state),
                "--generation",
                "dg_b1",
                "--who",
                "operator",
                "--why",
                "investigation",
            ]
        )
        == 0
    )
    listed_holds = holds(state)
    assert [item["generation_ids"] for item in listed_holds] == [["dg_b1"]]
    assert main(["hold", "list", "--state", str(state)]) == 0
    assert main(["hold", "remove", "--state", str(state), "--hold-id", "hold_missing"]) == 1
    assert (
        main(["hold", "remove", "--state", str(state), "--hold-id", listed_holds[0]["hold_id"]])
        == 0
    )
    assert holds(state) == []
    # A hold that names nothing, or gives no reason, is refused.
    assert main(["hold", "add", "--state", str(state), "--who", "o", "--why", "w"]) == 1
    assert main(["hold", "add", "--state", str(state), "--generation", "dg_b1"]) == 1


def candidate_tree(
    state: Path,
    name: str,
    *,
    selected: tuple[str, ...] | None = (),
    built: bool = True,
    publishing: bool = False,
    published: bool = True,
    commit: str = "commit-1",
) -> Path:
    """One candidate directory shaped the way a release build leaves it."""
    path = state / "candidates" / name
    (path / "_meta").mkdir(parents=True)
    policy: dict[str, Any] = (
        {"mode": "closure", "closure": {"selected_generations": list(selected)}}
        if selected is not None
        else {"mode": "correction_only"}
    )
    (path / "_meta" / "manifest.json").write_text(json.dumps({"release_policy": policy}))
    if built:
        (path / "BUILT").write_text(json.dumps({"expected_parent": commit}))
    if built and published:
        (path / "PUBLISHED").write_text(json.dumps({"commit": commit}))
    if publishing:
        (path / "PUBLISHING").write_text("{}")
    return path


def test_removing_the_baseline_or_a_pending_candidate_frees_what_it_alone_reached(
    tmp_path: Path,
) -> None:
    """The release roots are the ones whose inputs the database cannot re-derive."""
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
        with database.transaction():
            database.connection.execute(
                "UPDATE derivation_scopes SET materialized_generation_id=NULL"
            )
        # No pointers and no window, so a release is the only root there is.
        assert planned(database, recent_window=0)["totals"]["local_generations"] == 0

        baseline = candidate_tree(state, "cand_baseline", selected=("dg_b1",))
        (state / "baseline").symlink_to(baseline)
        with_baseline = planned(database, recent_window=0)
        assert listed(with_baseline, "local") == {"dg_b1"}
        assert reasons(with_baseline, "dg_b1") == ["baseline"]

        # A pending candidate pins its own selection, and the walk follows it:
        # dg_link_a was built from dg_a1, so both stay.
        candidate_tree(
            state, "cand_pending", selected=("dg_link_a",), publishing=True, published=False
        )
        with_pending = planned(database, recent_window=0)
        assert listed(with_pending, "local") == {"dg_b1", "dg_link_a", "dg_a1"}
        assert reasons(with_pending, "dg_a1") == ["pending_candidate"]

        # Removing the pending candidate frees exactly what it alone reached.
        shutil.rmtree(state / "candidates" / "cand_pending")
        assert listed(planned(database, recent_window=0), "local") == {"dg_b1"}

        # A release that is not a closure release pins nothing, and says so
        # rather than silently pinning nothing through a missing key.
        (state / "baseline").unlink()
        plain = candidate_tree(state, "cand_plain", selected=None)
        (state / "baseline").symlink_to(plain)
        assert listed(planned(database, recent_window=0), "local") == set()
        (plain / "_meta" / "manifest.json").write_text(
            json.dumps({"release_policy": {"mode": "closure"}})
        )
        with pytest.raises(RetentionError, match="release closure is missing"):
            planned(database, recent_window=0)
        # Doctor still reports the rest of the state when the planner stops.
        measured = report(database.connection, state, **KNOBS)
        assert "release closure is missing" in measured["error"]
        assert measured["usage"]["file_bytes"] > 0


def test_the_recovery_markers_pin_the_current_pointers_and_nothing_more(
    tmp_path: Path,
) -> None:
    """D-0138: a marker claims the current pointers and pending candidates only."""
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
        plain = planned(database, recent_window=0)
        for marker in ("RESTORE_PENDING", "operator-hold"):
            (state / marker).write_bytes(b"\n")
            content = planned(database, recent_window=0)
            # Same generations as the pointers alone reach, with the marker
            # named as another reason each one stays.
            assert listed(content, "local") == listed(plain, "local")
            assert {root["kind"] for root in content["roots"]} > {"pointer"}
            (state / marker).unlink()
        assert [root for root in planned(database, recent_window=0)["roots"]] == plain["roots"]


def test_an_in_progress_build_is_never_named_for_removal_without_its_age_floor(
    tmp_path: Path,
) -> None:
    """A candidate with no BUILT marker is a build in flight, not a superseded release."""
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
        inflight = candidate_tree(state, "cand_inflight", built=False)
        content = planned(database, recent_window=1)
        row = next(row for row in content["files"] if row["path"].endswith("cand_inflight"))
        assert row["why"] == "unbuilt_candidate"
        # The plan carries the collector's floor, so apply cannot delete a
        # directory the collector itself would leave alone.
        assert row["eligible_after"] == inflight.stat().st_mtime + KNOBS["collect_older_than"]
        assert row["eligible_after"] > NOW.timestamp()
        # Apply compares that floor against its own clock, so an apply running
        # now leaves the directory alone.
        from swingset.state.retention_apply import eligible_files

        assert eligible_files(plan(database.connection, state, **KNOBS), now=NOW) == ()
        # And the floor comes from the directory, not the clock, so the plan is
        # still the same bytes on unchanged state.
        assert plan(database.connection, state, **KNOBS).bytes() == (
            plan(database.connection, state, **KNOBS).bytes()
        )


def test_payload_bytes_a_missing_label_owns_are_unknown_and_never_eligible(
    tmp_path: Path,
) -> None:
    """A reference whose label is missing makes its bytes unknown, not archivable."""
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
        before = planned(database, recent_window=1)["totals"]
    raw = sqlite3.connect(state / "state.sqlite")
    raw.execute("PRAGMA foreign_keys=OFF")
    payload = '{"orphan":1}'
    digest = hashlib.sha256(payload.encode()).hexdigest()
    raw.execute("INSERT INTO derivation_payloads VALUES (?,?)", (digest, payload))
    raw.execute("INSERT INTO derivation_row_refs VALUES ('dg_ghost',0,'entries','x',?)", (digest,))
    raw.commit()
    raw.close()
    with open_database(state, lock=False) as database:
        content = planned(database, recent_window=1)
        totals = content["totals"]
        assert content["unknown"]["unowned_payloads"] == [digest]
        assert content["unknown"]["orphan_payloads"] == []
        assert content["unknown"]["orphan_row_references"] == ["dg_ghost"]
        # The bytes moved into unknown, not into what archiving would free.
        assert totals["unknown_payload_bytes"] == len(payload)
        assert totals["archivable_payload_bytes"] == before["archivable_payload_bytes"]
        assert totals["local_payload_bytes"] == before["local_payload_bytes"]


def test_payload_bytes_are_counted_in_bytes_and_not_in_characters(tmp_path: Path) -> None:
    """A name with accents costs the bytes it costs; the cap is read against these."""
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        conn = database.connection
        run = database.start_run(NOW)
        with database.transaction():
            generation(
                conn,
                run,
                "dg_accents",
                ("project", "event", "event-accents"),
                rows=(("entries", "a", {"name": "Renée Zoë"}),),
            )
            point_at(conn, ("project", "event", "event-accents"), "dg_accents")
        stored = canonical({"name": "Renée Zoë"})
        assert len(stored.encode()) > len(stored)
        content = planned(database, recent_window=1)
        row = next(row for row in content["generations"] if row["generation_id"] == "dg_accents")
        assert row["payload_bytes"] == len(stored.encode())
        assert content["totals"]["local_payload_bytes"] == len(stored.encode())
        assert retention.residency(conn, ["dg_accents"])[0].payload_bytes == len(stored.encode())


def test_a_finding_that_declares_a_file_which_is_not_there_is_reported_not_raised(
    tmp_path: Path,
) -> None:
    """One wrong declaration must not stop every backup."""
    from swingset.backup.checkpoint import create_checkpoint

    state = tmp_path / "state"
    absent = hashlib.sha256(b"never stored").hexdigest()
    with open_database(state, lock=False) as database:
        conn = database.connection
        history(database)
        run = database.start_run(NOW)
        with database.transaction():
            conn.execute(
                "INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,subject_id,"
                "severity,summary,evidence_json,opened_at,run_id) "
                "VALUES ('f1','review','r1','missing_identity','event','event-a','warning',"
                "'names a page','{}',?,?)",
                (NOW.isoformat(), run),
            )
            conn.execute("INSERT INTO finding_support_references VALUES ('f1','body',?)", (absent,))
        content = planned(database, recent_window=1)
        assert content["unknown"]["missing_declared_references"] == [f"f1/body/{absent}"]
        assert report(conn, state, **KNOBS)["unknown"]["missing_declared_references"] == 1
        # The backup still writes: the closure keeps what is there.
        checkpoint = create_checkpoint(
            state,
            conn,
            tmp_path / "checkpoint",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
        assert checkpoint.files


def test_an_unreadable_hold_file_stops_the_planner_and_not_the_operators_notes(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
        (state / "holds").mkdir()
        # An operator's own note is not a hold and is not read as one.
        (state / "holds" / "notes.json").write_text("not json at all")
        assert holds(state) == []
        assert planned(database, recent_window=1)["roots"]
        retention.artifact_closure(state, database.connection, set())
        # A file named like a hold that cannot be read is an error with a
        # remedy, not a raw decoding failure, and doctor reports the rest.
        (state / "holds" / "hold_broken.json").write_text('{"format": "retention-hold-v1", "hold_')
        with pytest.raises(RetentionError, match="repair or remove it"):
            holds(state)
        measured = report(database.connection, state, **KNOBS)
        assert "hold_broken.json" in measured["error"]
        assert measured["usage"]["file_bytes"] > 0


def test_written_plans_do_not_grow_without_bound(tmp_path: Path) -> None:
    """A plan names every file, so the plans directory keeps only the newest few."""
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
        written = []
        for index in range(retention.KEPT_PLANS + 3):
            blob = state / "blobs" / "sha256" / "ab" / "cd" / f"{index:064x}"
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(b"x" * (index + 1))
            path = write_plan(state, plan(database.connection, state, **KNOBS))
            os.utime(path, (index, index))
            written.append(path)
        kept = sorted((state / "gc" / "plans").glob("*.json"))
        assert len(kept) == retention.KEPT_PLANS
        assert written[-1].is_file() and not written[0].is_file()


def test_the_gc_command_plans_and_collects(tmp_path: Path) -> None:
    from swingset.cli import main

    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
    assert main(["gc", "--plan", "--state", str(state)]) == 0
    assert len(list((state / "gc" / "plans").glob("*.json"))) == 1
    # The plain collector still runs and removes nothing it should not.
    assert main(["gc", "--state", str(state)]) == 0
    assert len(list((state / "gc" / "plans").glob("*.json"))) == 1


def test_an_expired_whole_pipeline_pause_does_not_stand_in_for_a_live_one(
    tmp_path: Path,
) -> None:
    """A timed pause that has run out holds nothing, so the cap must still pause.

    `matching_pauses` skips a row whose `until_at` has passed, and only a
    change_control or admission boundary deletes it. Reading the row itself as
    an existing pause left the pipeline running over the cap with nothing
    paused.
    """
    from swingset.state.controls import Selector, change_control, matching_pauses

    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        history(database)
    change_control(
        state,
        selector=Selector("all", "all"),
        paused=True,
        actor="operator",
        reason="overnight maintenance",
        now=NOW,
        until=NOW + timedelta(hours=1),
    )
    # While it stands, the cap leaves the operator's own words alone.
    standing = enforce_size_cap(state, max_database_bytes=1, now=NOW + timedelta(minutes=30))
    assert not standing["paused"]
    assert standing["existing_pause_reason"] == "overnight maintenance"

    after = NOW + timedelta(hours=2)
    with open_database(state, lock=False, read_only=True) as database:
        # The row is still there, and controls already treat it as gone.
        assert (
            database.connection.execute("SELECT count(*) FROM operator_pauses").fetchone()[0] == 1
        )
        assert matching_pauses(database.connection, None, now=after) == []
    expired = enforce_size_cap(state, max_database_bytes=1, now=after)
    assert expired["paused"]
    assert expired["reason"] == retention.SIZE_CAP_REASON
    with open_database(state, lock=False, read_only=True) as database:
        assert [
            tuple(row)
            for row in database.connection.execute(
                "SELECT scope_kind,scope_id,reason FROM operator_pauses"
            )
        ] == [("all", "all", retention.SIZE_CAP_REASON)]


def test_the_residency_check_covers_only_newly_declared_generations(tmp_path: Path) -> None:
    """A root a finding already declared is already a root; only the delta is new.

    Once step 4 archives payloads, checking every declaration in the batch would
    let one already-archived generation named by one unchanged finding refuse
    every later finding for that owner. Plan section 7 scopes the check to the
    new root.
    """
    from swingset.state.findings import Finding, Reference, replace_findings

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        history(database)
        run = database.start_run(NOW + timedelta(minutes=2))
        recorded = Finding(
            "missing_identity",
            "event",
            "event-b",
            "warning",
            "needs the project generation",
            {},
            references=(Reference("generation", "dg_b1"),),
        )
        with database.transaction():
            assert replace_findings(
                conn,
                owner_kind="review",
                owner_id="reviewer",
                findings=(recorded,),
                opened_at=NOW.isoformat(),
                run_id=run,
            )
        # Step 4 will archive these bytes. Removing them under the same
        # permission row is how that state is reached today.
        with database.transaction():
            conn.execute(
                "INSERT INTO derivation_payload_removal_authority VALUES (1,'test',?)",
                (NOW.isoformat(),),
            )
            conn.execute(
                "DELETE FROM derivation_payloads WHERE payload_sha256 IN "
                "(SELECT payload_sha256 FROM derivation_row_refs WHERE generation_id='dg_b1')"
            )
            conn.execute("DELETE FROM derivation_payload_removal_authority")
        with pytest.raises(RetentionError, match="dg_b1"):
            retention.require_local(conn, ["dg_b1"])

        # An unrelated new finding for the same owner still commits, and the
        # unchanged one keeps declaring what it declared.
        fresh = Finding(
            "missing_identity",
            "event",
            "event-a",
            "warning",
            "nothing archived is claimed here",
            {},
        )
        with database.transaction():
            assert replace_findings(
                conn,
                owner_kind="review",
                owner_id="reviewer",
                findings=(recorded, fresh),
                opened_at=NOW.isoformat(),
                run_id=run,
            )
        assert (
            conn.execute(
                "SELECT count(*) FROM findings WHERE owner_id='reviewer' AND closed_at IS NULL"
            ).fetchone()[0]
            == 2
        )
        assert [
            tuple(row) for row in conn.execute("SELECT kind,sha256 FROM finding_support_references")
        ] == [("generation", "dg_b1")]
        # A newly declared archived generation is still refused.
        with pytest.raises(RetentionError, match="dg_b1"):
            with database.transaction():
                replace_findings(
                    conn,
                    owner_kind="review",
                    owner_id="reviewer",
                    findings=(
                        recorded,
                        Finding(
                            "missing_identity",
                            "event",
                            "event-c",
                            "warning",
                            "claims the archived generation too",
                            {},
                            references=(Reference("generation", "dg_b1"),),
                        ),
                    ),
                    opened_at=NOW.isoformat(),
                    run_id=run,
                )


def test_a_caller_that_declares_nothing_keeps_what_the_finding_declared(
    tmp_path: Path,
) -> None:
    """An empty declaration says nothing; it never deletes recorded support.

    Migration 31 backfilled what the evidence scan used to pin. A caller that
    rewrites the same finding without declaring anything must not unpin that.
    """
    from swingset.state.findings import Finding, replace_findings

    state = tmp_path / "state"
    body = hashlib.sha256(b"backfilled page").hexdigest()
    with open_database(state, lock=False) as database:
        conn = database.connection
        history(database)
        blob = state / "blobs" / "sha256" / body[:2] / body[2:4] / body
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"backfilled page")
        run = database.start_run(NOW)
        finding = Finding(
            "registry_diff", "dancer", "7", "warning", "differs", {"body_sha256": body}
        )
        with database.transaction():
            replace_findings(
                conn,
                owner_kind="crosscheck",
                owner_id="registry",
                findings=(finding,),
                opened_at=NOW.isoformat(),
                run_id=run,
            )
        from swingset.state.finding_reference_migration import backfill_finding_references

        with database.transaction():
            assert backfill_finding_references(conn, state) == 1
        # The same finding, written again with changed evidence and no
        # declaration: the backfilled row stays and the file stays pinned.
        with database.transaction():
            assert replace_findings(
                conn,
                owner_kind="crosscheck",
                owner_id="registry",
                findings=(
                    Finding(
                        "registry_diff",
                        "dancer",
                        "7",
                        "warning",
                        "differs",
                        {"body_sha256": body, "seen_again": True},
                    ),
                ),
                opened_at=NOW.isoformat(),
                run_id=run,
            )
        assert [
            tuple(row) for row in conn.execute("SELECT kind,sha256 FROM finding_support_references")
        ] == [("body", body)]
        assert blob.resolve() in retention.artifact_closure(state, conn, set())


def test_a_requirement_finding_relies_on_the_snapshot_pin_and_not_a_declaration(
    tmp_path: Path,
) -> None:
    """Requirements write the findings row themselves and declare nothing.

    An `archive_artifact` requirement is about a digest `snapshots.body_sha256`
    already pins, so the file is in the closure whether or not the requirement
    says anything about it.
    """
    from swingset.state.requirements import Requirement, reconcile_requirement

    state = tmp_path / "state"
    body = hashlib.sha256(b"a retained page").hexdigest()
    with open_database(state, lock=False) as database:
        conn = database.connection
        history(database)
        blob = state / "blobs" / "sha256" / body[:2] / body[2:4] / body
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"a retained page")
        run = database.start_run(NOW)
        with database.transaction():
            conn.execute(
                "INSERT INTO watches(watch_id,source,kind,url,method,parser,state,priority) "
                "VALUES ('w1','wsdc_registry','round','https://example.test/1','GET',"
                "'generic.html_table','active',5)"
            )
            conn.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
                "body_sha256,body_bytes,content_changed,run_id,via,classification) "
                "VALUES ('snap_1','w1','GET','https://example.test/1',?,200,?,15,1,?,'live','Ok')",
                (NOW.isoformat(), body, run),
            )
            reconcile_requirement(
                conn,
                Requirement(
                    "archive_artifact",
                    body,
                    "wsdc_registry",
                    "satisfied",
                    "restore verified artifact by digest",
                    {"snapshot_ids": ["snap_1"], "sources": ["wsdc_registry"], "body_sha256": body},
                ),
                NOW,
                run,
            )
        assert (
            conn.execute("SELECT count(*) FROM findings WHERE owner_kind='requirement'").fetchone()[
                0
            ]
            == 1
        )
        assert conn.execute("SELECT count(*) FROM finding_support_references").fetchone()[0] == 0
        # The snapshot is what keeps the file, and the plan says so.
        assert blob.resolve() in retention.artifact_closure(state, conn, set())
        assert planned(database, recent_window=1)["unknown"]["undeclared_files"] == []
