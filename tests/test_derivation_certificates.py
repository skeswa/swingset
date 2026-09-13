"""Change certificates must be as conservative as full dependency comparison."""

import hashlib
import json

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset.state import derivations
from swingset.state.derivation_query import memo, query
from swingset.state.derivation_signatures import certificate
from swingset.state.recipes import recipe_inputs
from swingset.state.work import WorkUnit


def materialize(fixture):
    unit = WorkUnit("project", "event", "event-a")
    with fixture.db.transaction():
        selection = derivations.capture(fixture.conn, unit, now=fixture.corpus.clock.now())
        derivations.complete(
            fixture.conn,
            selection,
            rows=(),
            now=fixture.corpus.clock.now(),
            run_id=fixture.corpus.run,
        )
    assert derivations.current(fixture.conn, unit)
    return unit, selection


@pytest.mark.parametrize("mutation", ["update", "delete", "snapshot", "selection", "watch_source"])
def test_source_mutation_cannot_hide_behind_proven_materialization_signature(
    source_fixture, mutation
):
    f = source_fixture
    unit, selection = materialize(f)
    original = f.conn.execute(
        "SELECT observation_id,watch_id,snapshot_id FROM observations LIMIT 1"
    ).fetchone()
    with f.db.transaction():
        if mutation == "update":
            f.conn.execute(
                "UPDATE observations SET payload_json=replace(payload_json,'Alice Example','Different Name') WHERE observation_id=?",
                (original[0],),
            )
        elif mutation == "delete":
            f.conn.execute("DELETE FROM observations WHERE observation_id=?", (original[0],))
        elif mutation == "snapshot":
            f.conn.execute("UPDATE snapshots SET via='wayback' WHERE snapshot_id=?", (original[2],))
        elif mutation == "selection":
            f.conn.execute(
                "UPDATE source_units SET accepted_generation_id=NULL WHERE watch_id=?",
                (original[1],),
            )
        else:
            f.conn.execute(
                "UPDATE watches SET source='changed-source' WHERE watch_id=?", (original[1],)
            )
    assert not derivations.current(f.conn, unit)
    assert derivations.desired(f.conn, unit).fingerprint != selection.fingerprint


def test_false_mutable_cache_claim_has_no_immutable_completion_proof(source_fixture):
    f = source_fixture
    unit, _ = materialize(f)
    f.conn.execute(
        "UPDATE observations SET payload_json=replace(payload_json,'Alice Example','Changed')"
    )
    generation = derivations.selected_generation(f.conn, unit)
    signature = certificate(f.conn, unit, recipe_inputs(f.conn, "project"), {})
    proof = derivations.canonical({"generation_id": generation, "certificate": signature})
    f.conn.execute(
        "UPDATE derivation_scopes SET desired_fingerprint=?,materialized_signature=? WHERE stage=? AND unit_kind=? AND unit_id=?",
        (
            derivations.desired(f.conn, unit).fingerprint,
            hashlib.sha256(proof.encode()).hexdigest(),
            unit.stage,
            unit.unit_kind,
            unit.unit_id,
        ),
    )
    assert not derivations.current(f.conn, unit)


def test_source_tokens_rollback_with_input_and_query_memo_does_not_keep_dirty_values(
    source_fixture,
):
    f = source_fixture
    unit, before = materialize(f)
    versions = [
        tuple(row)
        for row in f.conn.execute(
            "SELECT * FROM derivation_input_versions ORDER BY namespace,input_key"
        )
    ]
    with query(f.conn):
        assert (
            memo(f.conn, "payload", lambda: derivations.desired(f.conn, unit).fingerprint)
            == before.fingerprint
        )
        with pytest.raises(RuntimeError, match="rollback"):
            with f.db.transaction():
                f.conn.execute(
                    "UPDATE observations SET payload_json=replace(payload_json,'Alice Example','Changed')"
                )
                assert not derivations.current(f.conn, unit)
                assert (
                    memo(f.conn, "payload", lambda: derivations.desired(f.conn, unit).fingerprint)
                    != before.fingerprint
                )
                raise RuntimeError("rollback")
        assert derivations.current(f.conn, unit)
        assert (
            memo(f.conn, "payload", lambda: derivations.desired(f.conn, unit).fingerprint)
            == before.fingerprint
        )
    assert [
        tuple(row)
        for row in f.conn.execute(
            "SELECT * FROM derivation_input_versions ORDER BY namespace,input_key"
        )
    ] == versions


def test_continuity_is_retained_and_selection_mutation_is_rejected(source_fixture):
    f = source_fixture
    unit = WorkUnit("project", "calendar", "legacy")
    with f.db.transaction():
        selection = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        support_id = selection.continuity["dependency_set_id"]
        support = json.loads(
            f.conn.execute(
                "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
                (support_id,),
            ).fetchone()[0]
        )
        assert any(
            row["table"] == "events" and row["row"]["event_id"] == "event-a" for row in support
        )
        selection.continuity["dependency_set_id"] = "tampered"
        with pytest.raises(derivations.SupersededWorkError, match="modified"):
            derivations.complete(
                f.conn, selection, rows=(), now=f.corpus.clock.now(), run_id=f.corpus.run
            )
    assert derivations.selected_generation(f.conn, unit) is None


def test_input_versions_cannot_be_rewound_or_renamed(source_fixture):
    import sqlite3

    f = source_fixture
    materialize(f)
    row = f.conn.execute(
        "SELECT namespace,input_key,version FROM derivation_input_versions LIMIT 1"
    ).fetchone()
    assert row
    for assignment, params in (
        ("version=?", (row[2] - 1,)),
        ("input_key=?", ("renamed",)),
        ("namespace=?", ("renamed",)),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="must advance"):
            f.conn.execute(
                "UPDATE derivation_input_versions SET "
                + assignment
                + " WHERE namespace=? AND input_key=?",
                (*params, row[0], row[1]),
            )
    f.conn.execute(
        "UPDATE derivation_input_versions SET version=version+1 WHERE namespace=? AND input_key=?",
        (row[0], row[1]),
    )


def test_row_iteration_source_change_cannot_be_certified(source_fixture):
    f = source_fixture
    unit, _ = materialize(f)
    before = derivations.selected_generation(f.conn, unit)
    with f.db.transaction():
        selection = derivations.capture(f.conn, unit, now=f.corpus.clock.now())

    def changing_rows():
        f.conn.execute(
            "UPDATE observations SET payload_json=replace(payload_json,'Alice Example','Changed')"
        )
        yield from ()

    with pytest.raises(derivations.SupersededWorkError):
        with f.db.transaction():
            derivations.complete(
                f.conn,
                selection,
                rows=changing_rows(),
                now=f.corpus.clock.now(),
                run_id=f.corpus.run,
            )
    assert derivations.selected_generation(f.conn, unit) == before
    assert derivations.current(f.conn, unit)
