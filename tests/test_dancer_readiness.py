"""Bulk dancer readiness agrees with individual immutable-proof currentness."""

import hashlib
from pathlib import Path

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset.project.process import process_unit
from swingset.schedule.watches import upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE
from swingset.state import derivations
from swingset.state.derivation_dependencies import prerequisites
from swingset.state.derivation_query import query
from swingset.state.derivation_readiness import dancers_current
from swingset.state.derivation_signatures import certificate, materialized_proof
from swingset.state.recipes import recipe_inputs
from swingset.state.work import WorkUnit


@pytest.fixture
def registry_fixture(source_fixture):
    f = source_fixture
    spec = SOURCE.watch(1)
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
    context = f.corpus.snapshot("registry-real", body, spec=spec)
    f.conn.execute(
        "UPDATE snapshots SET method=? WHERE snapshot_id=?", (spec.method, context.snapshot_id)
    )
    generation, report = f.corpus.stage(context, body=body)
    assert not report.failures
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    process_unit(f.db, WorkUnit("project", "dancer", "1"), f.bundle, f.corpus.clock, f.corpus.run)
    return f


def compare(f):
    units = tuple(
        unit
        for unit in prerequisites(f.conn, WorkUnit("link", "event", "event-a"))
        if unit.unit_kind == "dancer"
    )
    with f.db.transaction():
        ordinary = all(derivations.current(f.conn, unit) for unit in units)
        bulk = dancers_current(f.conn, units, current=derivations.current)
        assert bulk == ordinary
    return bulk


@pytest.mark.parametrize(
    "mutation", ["payload", "delete", "snapshot", "selection", "watch_source", "raw_token"]
)
def test_differential_retained_input_mutations(registry_fixture, mutation):
    f = registry_fixture
    assert compare(f)
    row = f.conn.execute(
        "SELECT observation_id,watch_id,snapshot_id FROM observations WHERE scope_kind='dancer'"
    ).fetchone()
    if mutation == "payload":
        f.conn.execute(
            "UPDATE observations SET payload_json=replace(payload_json,'Diane','Different') WHERE observation_id=?",
            (row[0],),
        )
    elif mutation == "delete":
        f.conn.execute("DELETE FROM observations WHERE observation_id=?", (row[0],))
    elif mutation == "snapshot":
        f.conn.execute("UPDATE snapshots SET via='wayback' WHERE snapshot_id=?", (row[2],))
    elif mutation == "selection":
        f.conn.execute(
            "UPDATE source_units SET accepted_generation_id=NULL WHERE watch_id=?", (row[1],)
        )
    elif mutation == "watch_source":
        f.conn.execute("UPDATE watches SET source='changed-source' WHERE watch_id=?", (row[1],))
    else:
        f.conn.execute(
            "UPDATE derivation_input_versions SET version=version+1 WHERE namespace='raw' AND input_key='[\"dancer\",\"1\"]'"
        )
    assert compare(f) == (mutation == "raw_token")


@pytest.mark.parametrize("proof_state", ["missing", "forged"])
def test_missing_or_forged_current_input_proof_never_hides_changed_facts(
    registry_fixture, proof_state
):
    f = registry_fixture
    unit = WorkUnit("project", "dancer", "1")
    f.conn.execute(
        "UPDATE observations SET payload_json=replace(payload_json,'Diane','Changed') WHERE scope_kind='dancer'"
    )
    signature = certificate(f.conn, unit, recipe_inputs(f.conn, "project"), {})
    proof, key = materialized_proof(derivations.selected_generation(f.conn, unit), signature)
    f.conn.execute(
        "UPDATE derivation_scopes SET materialized_signature=? WHERE stage='project' AND unit_kind='dancer' AND unit_id='1'",
        (key,),
    )
    if proof_state == "forged":
        f.conn.execute("INSERT INTO derivation_dependency_sets VALUES (?,?)", (key, proof + " "))
    assert not compare(f)


def test_invalid_fast_hint_falls_back_to_full_current_proof(registry_fixture):
    f = registry_fixture
    unit = WorkUnit("project", "dancer", "1")
    f.conn.execute(
        "UPDATE derivation_scopes SET materialized_signature='missing' WHERE stage='project' AND unit_kind='dancer' AND unit_id='1'"
    )
    called = []

    def current(conn, item):
        called.append(item)
        return derivations.current(conn, item)

    with f.db.transaction():
        assert dancers_current(f.conn, (unit,), current=current)
    assert called == [unit]


def test_bulk_result_does_not_survive_write_and_rollback(registry_fixture):
    f = registry_fixture
    unit = WorkUnit("project", "dancer", "1")
    with query(f.conn):
        assert compare(f)
        with pytest.raises(RuntimeError, match="rollback"):
            with f.db.transaction():
                f.conn.execute(
                    "UPDATE observations SET payload_json=replace(payload_json,'Diane','Changed') WHERE scope_kind='dancer'"
                )
                assert not dancers_current(f.conn, (unit,), current=derivations.current)
                raise RuntimeError("rollback")
        assert compare(f)


def test_unregistered_physical_dancer_remains_required_after_scope_deletion(registry_fixture):
    f = registry_fixture
    f.conn.execute(
        "INSERT INTO canonical_scope_rows VALUES ('dancer','unmaterialized','dancers','[999]')"
    )
    f.conn.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project','dancer','unmaterialized',?)",
        (f.corpus.clock.now().isoformat(),),
    )
    assert (
        f.conn.execute(
            "DELETE FROM derivation_scopes WHERE stage='project' AND unit_kind='dancer' AND unit_id='unmaterialized'"
        ).rowcount
        == 1
    )
    assert not compare(f)
    assert not derivations.ready(f.conn, WorkUnit("link", "event", "event-a"))
    assert not f.conn.in_transaction


def test_healthy_bulk_proofs_use_bounded_queries_and_no_fallback(registry_fixture):
    f = registry_fixture
    units = [WorkUnit("project", "dancer", "1")]
    with f.db.transaction():
        for number in range(2, 52):
            unit = WorkUnit("project", "dancer", str(number))
            selected = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
            derivations.complete(
                f.conn, selected, rows=(), now=f.corpus.clock.now(), run_id=f.corpus.run
            )
            units.append(unit)
    statements = []

    def forbidden(*args):
        raise AssertionError("valid immutable certificate unexpectedly fell back")

    with f.db.transaction():
        f.conn.set_trace_callback(statements.append)
        try:
            assert dancers_current(f.conn, units, current=forbidden)
        finally:
            f.conn.set_trace_callback(None)
    assert len(statements) <= 6
    assert len(units) == 51


def test_materialized_proof_encoding_is_byte_identical():
    payload, key = materialized_proof("génération", "signature")
    expected = derivations.canonical({"generation_id": "génération", "certificate": "signature"})
    assert payload == expected and key == hashlib.sha256(expected.encode()).hexdigest()


def test_changed_accepted_recipe_invalidates_bulk_and_individual_proofs(registry_fixture):
    f = registry_fixture
    assert compare(f)
    f.conn.execute(
        "UPDATE accepted_inputs SET digest='new-reviewed-runtime' WHERE consumer='pipeline' AND input_name='recipe/runtime'"
    )
    assert not compare(f)


def test_active_consistency_group_preserves_individual_readiness_path(
    registry_fixture, monkeypatch
):
    from swingset.state import derivation_readiness

    f = registry_fixture
    unit = WorkUnit("link", "event", "event-a")

    def forbidden(*args, **kwargs):
        raise AssertionError("active consistency group used bulk currentness")

    monkeypatch.setattr(derivation_readiness, "dancers_current", forbidden)
    with f.db.transaction():
        with derivations.group(f.conn):
            assert derivations.ready(f.conn, unit) == all(
                derivations.current(f.conn, item) for item in prerequisites(f.conn, unit)
            )


def test_ready_reuses_caller_transaction_without_committing_or_rolling_it_back(registry_fixture):
    f = registry_fixture
    with f.db.transaction():
        f.conn.execute("INSERT INTO meta VALUES ('readiness-caller-marker','retained')")
        derivations.ready(f.conn, WorkUnit("link", "event", "event-a"))
        assert f.conn.in_transaction
        assert (
            f.conn.execute("SELECT value FROM meta WHERE key='readiness-caller-marker'").fetchone()[
                0
            ]
            == "retained"
        )
    assert (
        f.conn.execute("SELECT value FROM meta WHERE key='readiness-caller-marker'").fetchone()[0]
        == "retained"
    )


def test_bulk_reads_one_snapshot_when_another_connection_changes_input(registry_fixture):
    import sqlite3
    from contextlib import closing

    f = registry_fixture
    unit = WorkUnit("project", "dancer", "1")
    changed = False

    def concurrent_change(statement):
        nonlocal changed
        if not changed and "SELECT input_key,version" in statement:
            changed = True
            with closing(
                sqlite3.connect(f.db.state_dir / "state.sqlite", isolation_level=None)
            ) as other:
                other.execute(
                    "UPDATE observations SET payload_json=replace(payload_json,'Diane','Concurrent') WHERE scope_kind='dancer'"
                )

    with f.db.transaction(immediate=False):
        f.conn.set_trace_callback(concurrent_change)
        try:
            assert dancers_current(f.conn, (unit,), current=derivations.current)
        finally:
            f.conn.set_trace_callback(None)
        assert changed and derivations.current(f.conn, unit)
    assert not derivations.current(f.conn, unit)
