"""Record deliberately precomputed synthetic outputs during test arrangement.

Transport, control, and publication tests often seed canonical rows directly.
Those rows have no raw projector input and must not be erased by replaying an
empty source. This helper records their exact existing outputs in topological
order; production work and tests of projector behavior use process_unit instead.
"""

from swingset.project.materialization import output_rows
from swingset.state import derivations


def materialize_seeded_outputs(database, *, now, run_id, stages=("project", "link")):
    conn = database.connection
    for _ in range(100):
        pending = [unit for stage in stages for unit in derivations.pending_units(conn, stage)]
        if not pending:
            return
        ready = [unit for unit in pending if derivations.ready(conn, unit)]
        assert ready, f"synthetic fixture has unresolved dependencies: {pending}"
        for unit in ready:
            with database.transaction():
                selection = derivations.capture(conn, unit, now=now)
                derivations.complete(
                    conn, selection, rows=output_rows(conn, unit), now=now, run_id=run_id
                )
                conn.execute(
                    "DELETE FROM pending_work WHERE stage = %s AND unit_kind = %s AND unit_id = %s",
                    (unit.stage, unit.unit_kind, unit.unit_id),
                )
    raise AssertionError("synthetic fixture materialization did not settle")


def bootstrap_runtime(database, clock):
    """Accept candidate code on an empty fixture before its synthetic rows exist.

    This is the normal empty-graph bootstrap path, rather than authorization
    of preexisting results. Populated imports still require a real verify run.
    """
    from pathlib import Path

    from swingset.state.inputs import accept, capture
    from swingset.versions import versions

    conn = database.connection
    assert (
        conn.execute(
            "SELECT 1 FROM derivation_scopes WHERE NOT materialized_generation_id IS NULL LIMIT 1"
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT 1 FROM source_units WHERE NOT accepted_generation_id IS NULL LIMIT 1"
        ).fetchone()
        is None
    )
    overrides = database.state_dir / "fixture-bootstrap-overrides"
    overrides.mkdir(exist_ok=True)
    bundle = capture(Path("config"), overrides, database.state_dir, versions())
    accept(database, bundle, clock)
    return bundle
