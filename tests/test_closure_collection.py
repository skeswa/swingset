"""Only release-closure proofs that nothing names are ever collected (D-0200)."""

import json
import sqlite3

import pytest

from swingset.build.closure_manifest import canonical
from swingset.clock import FakeClock
from swingset.state import closure_collection
from swingset.state.db import open_database


def proof(name):
    return canonical(
        {
            "baseline": None,
            "cutoff": name,
            "dependency_sets": [],
            "format": "release-closure-v1",
            "selected": [],
        }
    )


def generation(conn, run, identifier, stage, kind, set_id, context):
    conn.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,'all','t')",
        (stage, kind),
    )
    conn.execute(
        "INSERT INTO derivation_generations VALUES (?,?,?,'all','f',?,?,NULL,'d',0,'t',?)",
        (identifier, stage, kind, canonical({"context": context}), set_id, run),
    )


@pytest.fixture
def state(tmp_path):
    with open_database(tmp_path / "state") as db:
        clock = FakeClock()
        run = db.start_run(clock.now())
        conn = db.connection
        for name in ("failed-1", "failed-2", "by-set", "by-member", "by-recipe", "by-candidate"):
            conn.execute("INSERT INTO derivation_dependency_sets VALUES (?,?)", (name, proof(name)))
        conn.execute("INSERT INTO derivation_dependency_sets VALUES ('ordinary','[]')")
        conn.execute(
            "INSERT INTO derivation_dependency_sets VALUES ('build-set',?)",
            (canonical([{"kind": "release_closure", "fingerprint": "by-member"}]),),
        )
        generation(conn, run, "dg_project", "project", "history", "by-set", {})
        generation(
            conn,
            run,
            "dg_build",
            "build",
            "release",
            "build-set",
            {
                "release_closure": {
                    "format": "release-closure-public-v1",
                    "private_digest": "by-recipe",
                }
            },
        )
        manifest = db.state_dir / "candidates" / "cand_x" / "_meta" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"release_policy": {"closure": {"private_digest": "by-candidate"}}})
        )
        yield db, clock


def remaining(conn):
    return {
        row[0] for row in conn.execute("SELECT dependency_set_id FROM derivation_dependency_sets")
    }


def test_plan_names_every_reason_to_keep_and_collects_only_unnamed_proofs(state):
    db, _ = state
    value = closure_collection.plan(db.connection, db.state_dir)
    assert [item["id"] for item in value["collectable"]] == ["failed-1", "failed-2"]
    assert value["kept"] == {
        "by-candidate": ["candidate:cand_x"],
        "by-member": ["build_dependency:dg_build"],
        "by-recipe": ["build_recipe:dg_build"],
        "by-set": ["generation_dependency_set"],
    }
    assert value["collectable_bytes"] == len(proof("failed-1")) + len(proof("failed-2"))


def test_collect_removes_only_the_planned_proofs_and_restores_immutability(state):
    db, clock = state
    before = remaining(db.connection)
    value = closure_collection.plan(db.connection, db.state_dir)
    receipt = closure_collection.collect(db, plan_digest=value["digest"], now=clock.now())
    assert receipt["removed_ids"] == ["failed-1", "failed-2"]
    assert remaining(db.connection) == before - {"failed-1", "failed-2"}
    assert json.loads(open(receipt["receipt"]).read())["plan_digest"] == value["digest"]
    with pytest.raises(sqlite3.DatabaseError):
        db.connection.execute(
            "DELETE FROM derivation_dependency_sets WHERE dependency_set_id='ordinary'"
        )
    assert closure_collection.plan(db.connection, db.state_dir)["collectable"] == []


def test_collect_refuses_a_plan_that_moved(state):
    db, clock = state
    value = closure_collection.plan(db.connection, db.state_dir)
    db.connection.execute(
        "INSERT INTO derivation_dependency_sets VALUES ('failed-3',?)", (proof("failed-3"),)
    )
    with pytest.raises(closure_collection.ClosureCollectionError, match="plan moved"):
        closure_collection.collect(db, plan_digest=value["digest"], now=clock.now())
    assert "failed-1" in remaining(db.connection)


def test_an_unreadable_candidate_keeps_everything(state):
    db, _ = state
    (db.state_dir / "candidates" / "cand_x" / "_meta" / "manifest.json").write_text("{")
    with pytest.raises(closure_collection.ClosureCollectionError, match="unreadable"):
        closure_collection.plan(db.connection, db.state_dir)


def test_a_stopped_collection_still_records_what_it_removed(state, monkeypatch):
    db, clock = state
    value = closure_collection.plan(db.connection, db.state_dir)
    original = db.transaction
    calls = []

    def failing_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("stopped")
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "transaction", failing_second)
    with pytest.raises(RuntimeError, match="stopped"):
        closure_collection.collect(db, plan_digest=value["digest"], now=clock.now())
    (receipt,) = (db.state_dir / "gc" / "receipts").glob("closure-proofs-*.json")
    recorded = json.loads(receipt.read_text())
    assert recorded["removed_ids"] == ["failed-1"] and recorded["planned"] == 2
    assert recorded["error"] == "RuntimeError: stopped"
    assert "failed-2" in remaining(db.connection)


def test_the_baseline_is_kept_wherever_it_points(state, tmp_path):
    db, _ = state
    elsewhere = tmp_path / "published" / "_meta" / "manifest.json"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text(
        json.dumps({"release_policy": {"closure": {"private_digest": "failed-2"}}})
    )
    (db.state_dir / "baseline").symlink_to(elsewhere.parent.parent)
    value = closure_collection.plan(db.connection, db.state_dir)
    assert [item["id"] for item in value["collectable"]] == ["failed-1"]
    assert value["kept"]["failed-2"] == ["candidate:published"]
