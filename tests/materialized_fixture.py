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
                    "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                    (unit.stage, unit.unit_kind, unit.unit_id),
                )
    raise AssertionError("synthetic fixture materialization did not settle")
