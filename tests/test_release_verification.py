"""Only exact, complete verification can authorize declared-version releases."""

import json

import pytest
from test_postgres_schema import fresh_database as fresh_database

from swingset.state import engine
from swingset.state.release_verification import blocked


@pytest.fixture
def accepted(fresh_database):
    service, name, _owner = fresh_database
    conn = engine.connect(service.dsn(name))
    conn.execute("SET search_path=public,pg_catalog")
    conn.execute("CREATE TABLE accepted_inputs(consumer TEXT,input_name TEXT,digest TEXT)")
    conn.execute("CREATE TABLE meta(key TEXT,value TEXT)")
    yield conn
    conn.close()


def test_legacy_state_does_not_require_graph_verification(accepted, tmp_path):
    assert blocked(accepted, tmp_path) is None


@pytest.mark.parametrize("body", [None, "{", "null", "{}"])
def test_missing_or_invalid_proof_blocks_release(accepted, tmp_path, body):
    accepted.execute("INSERT INTO accepted_inputs VALUES ('audit','recipe/env','runtime')")
    if body is not None:
        path = tmp_path / "verification" / "receipt.json"
        path.parent.mkdir()
        path.write_text(body)
    assert blocked(accepted, tmp_path) is not None


def test_release_guard_runs_before_reconciliation(monkeypatch):
    from types import SimpleNamespace

    from swingset.loops import release
    from swingset.state import release_verification

    monkeypatch.setattr(release_verification, "blocked", lambda *_: {"reason": "proof_required"})
    monkeypatch.setattr(
        release, "reconcile", lambda *_args, **_kwargs: pytest.fail("publication preceded proof")
    )
    tick = SimpleNamespace(
        skipped=None,
        database=SimpleNamespace(connection=None, state_dir=None),
        clock=None,
        summary={},
        bundle=object(),
        hub=object(),
        dry_run=False,
    )
    release.run(tick)
    assert tick.skipped == "verification"
    assert not tick.summary.get("build_attempted")


def test_scoped_proof_covers_all_changes_since_trusted_acceptance(accepted, tmp_path):
    from swingset.fetch.archive import canonical, digest
    from swingset.state.verify_graph import FORMAT

    accepted.execute("CREATE UNIQUE INDEX meta_key ON meta(key)")
    old = {"recipe/env": "runtime", "recipe/code/project/event": "old", "recipe/code/link": "old"}
    accepted.executemany("INSERT INTO accepted_inputs VALUES ('audit',%s,%s)", old.items())
    accepted.execute(
        "INSERT INTO meta VALUES ('audit_bootstrap_context',%s)",
        (json.dumps({"audit": old, "versions": {}}),),
    )
    accepted.execute(
        "UPDATE accepted_inputs SET digest='new' WHERE input_name='recipe/code/project/event'"
    )
    audit = {**old, "recipe/code/project/event": "new"}
    path = tmp_path / "verification/receipt.json"
    path.parent.mkdir()
    proof = {
        "format": FORMAT,
        "expected_units": [],
        "results": [],
        "unit_set_digest": digest(canonical([])),
        "complete": True,
        "clean": True,
        "errors": [],
        "audit": audit,
        "versions": {},
        "inputs": {},
        "checkpoint": "checkpoint",
        "started_at": "2026-11-04T12:00:00+00:00",
        "scopes": ["project/event"],
    }
    path.write_text(json.dumps(proof))
    assert blocked(accepted, tmp_path) is None
    # A later acceptance of another scope cannot reuse the previous proof.
    accepted.execute(
        "UPDATE accepted_inputs SET digest='second' WHERE input_name='recipe/code/link'"
    )
    assert blocked(accepted, tmp_path) is not None
    proof["audit"]["recipe/code/link"] = "second"
    path.write_text(json.dumps(proof))
    assert blocked(accepted, tmp_path)["reason"] == "unverified_audit_scopes"
    proof["scopes"] = ["link"]
    path.write_text(json.dumps(proof))
    assert blocked(accepted, tmp_path) is None
    accepted.execute("UPDATE accepted_inputs SET digest='runtime2' WHERE input_name='recipe/env'")
    proof["audit"]["recipe/env"] = "runtime2"
    path.write_text(json.dumps(proof))
    assert blocked(accepted, tmp_path)["reason"] == "unverified_audit_scopes"
    proof["scopes"] = None
    path.write_text(json.dumps(proof))
    assert blocked(accepted, tmp_path) is None


def test_only_proven_bumps_with_current_consumers_and_drained_work_authorize_release(
    accepted, tmp_path, monkeypatch
):
    from swingset.fetch.archive import canonical, digest
    from swingset.state import derivations, verify_graph

    accepted.execute("CREATE UNIQUE INDEX meta_key ON meta(key)")
    accepted.execute("CREATE TABLE pending_work(stage TEXT)")
    accepted.execute("INSERT INTO accepted_inputs VALUES ('audit','recipe/env','runtime')")
    accepted.execute(
        "INSERT INTO accepted_inputs VALUES ('pipeline','recipe/code/project/event','new')"
    )
    accepted.execute(
        "CREATE TABLE derivation_scopes(stage TEXT,unit_kind TEXT,unit_id TEXT,materialized_generation_id TEXT)"
    )
    accepted.execute("CREATE TABLE derivation_generations(generation_id TEXT,created_at TEXT)")
    accepted.executemany(
        "INSERT INTO derivation_scopes VALUES ('project',%s,%s,%s)",
        [("event", "producer", "g1"), ("year", "consumer", "g2")],
    )
    accepted.executemany(
        "INSERT INTO derivation_generations VALUES (%s,%s)",
        [("g1", "2026-11-04T11:00:00+00:00"), ("g2", "2026-11-04T11:00:00+00:00")],
    )
    units = ["project/event/producer", "project/year/consumer"]
    monkeypatch.setattr(verify_graph, "enumerate_units", lambda *_: units)
    stale = {"consumer"}
    monkeypatch.setattr(derivations, "current", lambda _conn, unit: unit.unit_id not in stale)
    proof = {
        "format": verify_graph.FORMAT,
        "expected_units": units,
        "results": [
            {
                "unit": units[0],
                "generation_id": "g1",
                "state": "mismatch",
                "stored_legacy_recipe": True,
                "stored_versions": {"recipe/code/project/event": "old"},
            },
            {"unit": units[1], "generation_id": "g2", "state": "match"},
        ],
        "unit_set_digest": digest(canonical(units)),
        "complete": True,
        "clean": False,
        "errors": [],
        "audit": {"recipe/env": "runtime"},
        "versions": {"recipe/code/project/event": "new"},
        "inputs": {},
        "checkpoint": "checkpoint",
        "started_at": "2026-11-04T12:00:00+00:00",
        "scopes": None,
        "bumped_transforms": ["project/event"],
    }
    path = tmp_path / "verification/receipt.json"
    path.parent.mkdir()
    path.write_text(json.dumps(proof))
    assert blocked(accepted, tmp_path) is not None  # author exclusions alone prove nothing
    proof["results"][0]["stored_legacy_recipe"] = False
    path.write_text(json.dumps(proof))
    assert blocked(accepted, tmp_path) is not None  # consumer still reads old producer
    stale.clear()
    accepted.execute("INSERT INTO pending_work VALUES ('project')")
    assert blocked(accepted, tmp_path) is not None
    accepted.execute("DELETE FROM pending_work")
    assert blocked(accepted, tmp_path) is None


def test_coverage_rejects_omitted_old_generation_and_requires_new_generation_current(
    accepted, monkeypatch
):
    from swingset.state import derivations, verify_graph
    from swingset.state.release_verification import _coverage

    accepted.execute(
        "CREATE TABLE derivation_scopes(stage TEXT,unit_kind TEXT,unit_id TEXT,materialized_generation_id TEXT)"
    )
    accepted.execute("CREATE TABLE derivation_generations(generation_id TEXT,created_at TEXT)")
    accepted.execute("INSERT INTO derivation_scopes VALUES ('project','event','event','old')")
    accepted.execute(
        "INSERT INTO derivation_generations VALUES ('old','2026-11-04T11:00:00+00:00')"
    )
    monkeypatch.setattr(verify_graph, "enumerate_units", lambda *_: ["project/event/event"])
    proof = {"started_at": "2026-11-04T12:00:00+00:00", "results": [], "scopes": None}
    assert not _coverage(accepted, proof)
    accepted.execute(
        "INSERT INTO derivation_generations VALUES ('new','2026-11-04T13:00:00+00:00')"
    )
    accepted.execute("UPDATE derivation_scopes SET materialized_generation_id='new'")
    monkeypatch.setattr(derivations, "current", lambda *_: False)
    assert not _coverage(accepted, proof)
    monkeypatch.setattr(derivations, "current", lambda *_: True)
    assert _coverage(accepted, proof)
