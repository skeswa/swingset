"""Generation payloads preserve source facts and isolate identity-owned columns."""

from test_link_service import entry, seed

from swingset.project.materialization import output_rows
from swingset.state.db import open_database
from swingset.state.work import WorkUnit


def test_event_and_link_generations_own_separate_columns(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        seed(conn)
        identifier = "event/c1/L-7"
        entry(conn, identifier, "c1", "7")
        conn.execute(
            "INSERT INTO canonical_scope_rows VALUES ('event','event','entries',json_array(?))",
            (identifier,),
        )
        conn.execute(
            "UPDATE entries SET wsdc_id=1,link_status='confirmed',link_confidence=1 WHERE entry_id=?",
            (identifier,),
        )
        projected = list(output_rows(conn, WorkUnit("project", "event", "event")))
        linked = list(output_rows(conn, WorkUnit("link", "event", "event")))
        assert len(projected) == len(linked) == 1
        assert projected[0].payload["name_raw"] == "Alex Lee"
        assert projected[0].payload["snapshot_id"] == "snap"
        assert (
            not {"wsdc_id", "link_status", "link_confidence", "run_id", "last_seen_at"}
            & projected[0].payload.keys()
        )
        assert dict(linked[0].payload) == {
            "entry_id": identifier,
            "wsdc_id": 1,
            "link_status": "confirmed",
            "link_confidence": 1.0,
        }
        assert projected[0].key == linked[0].key
        conn.execute("UPDATE entries SET wsdc_id=NULL WHERE entry_id=?", (identifier,))
        assert (
            list(output_rows(conn, WorkUnit("link", "event", "event")))[0].payload["wsdc_id"]
            is None
        )


def test_inventory_does_not_claim_unenriched_map_placeholders(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        seed(conn)
        assert not any(
            row.table == "events"
            for row in output_rows(conn, WorkUnit("project", "inventory", "all"))
        )
        conn.execute("UPDATE events SET history_source='[\"registry\"]' WHERE event_id='event'")
        inventory = [
            row
            for row in output_rows(conn, WorkUnit("project", "inventory", "all"))
            if row.table == "events"
        ]
        assert len(inventory) == 1
        assert inventory[0].payload["history_source"] == '["registry"]'
        assert "coverage_tier" not in inventory[0].payload
        coverage = [
            row
            for row in output_rows(conn, WorkUnit("project", "history", "all"))
            if row.table == "events"
        ]
        assert len(coverage) == 1
        assert set(coverage[0].payload) == {"event_id", "coverage_tier"}


def acceptance_fixture(db):
    from materialized_fixture import materialize_seeded_outputs

    now = "2026-01-05T00:00:00Z"
    db.connection.execute("INSERT INTO runs VALUES ('run',?,NULL,1,NULL)", (now,))
    seed(db.connection)
    materialize_seeded_outputs(db, now=now, run_id="run")
    return now


def test_year_acceptance_materializes_coverage_with_actual_operation_provenance(tmp_path):
    import json

    from swingset.project.history import accept_year, pending_inventory_projection
    from swingset.state import derivations

    with open_database(tmp_path) as db:
        now = acceptance_fixture(db)
        unit = WorkUnit("project", "history", "all")
        before = derivations.selected_generation(db.connection, unit)
        accept_year(db.connection, 2026, accepted_by="Offline owner", accepted_at=now)
        after = derivations.selected_generation(db.connection, unit)
        assert after != before
        assert derivations.current(db.connection, unit)
        assert not pending_inventory_projection(db.connection)
        assert all(
            row[0] == 1 for row in db.connection.execute("SELECT events_accepted FROM coverage")
        )
        operation = db.connection.execute(
            "SELECT r.* FROM runs r JOIN derivation_generations g USING(run_id) WHERE g.generation_id=?",
            (after,),
        ).fetchone()
        assert operation["finished_at"] == now
        assert json.loads(operation["summary_json"]) == {
            "action": "accept_year",
            "year": 2026,
            "accepted_by": "Offline owner",
        }


def test_year_acceptance_rolls_back_output_run_and_generation_on_failure(tmp_path, monkeypatch):
    import pytest

    from swingset.project import history
    from swingset.state import derivations
    from swingset.state.attempts import SupersededWorkError

    with open_database(tmp_path) as db:
        now = acceptance_fixture(db)
        tables = (
            "history_acceptance",
            "coverage",
            "runs",
            "derivation_generations",
            "derivation_scopes",
            "revisions",
        )

        def state():
            return {
                table: [tuple(row) for row in db.connection.execute(f"SELECT * FROM {table}")]
                for table in tables
            }

        baseline = state()
        complete = derivations.complete

        def crash_after_output(*args, **kwargs):
            complete(*args, **kwargs)
            raise RuntimeError("offline crash after receipt")

        with monkeypatch.context() as patch:
            patch.setattr(derivations, "complete", crash_after_output)
            with pytest.raises(RuntimeError, match="offline crash"):
                history.accept_year(
                    db.connection, 2026, accepted_by="Offline owner", accepted_at=now
                )
        assert state() == baseline

        rebuild = history.rebuild_coverage

        def changed_acceptance(conn, *, now):
            rebuild(conn, now=now)
            conn.execute("UPDATE history_acceptance SET accepted_by='changed during output'")

        with monkeypatch.context() as patch:
            patch.setattr(history, "rebuild_coverage", changed_acceptance)
            with pytest.raises(SupersededWorkError):
                history.accept_year(
                    db.connection, 2026, accepted_by="Offline owner", accepted_at=now
                )
        assert state() == baseline
        from contextlib import contextmanager

        from swingset.state import write_deadline

        @contextmanager
        def expired_on_exit(_conn):
            yield
            raise write_deadline.WriteDeadlineExceeded("offline deadline after output")

        with monkeypatch.context() as patch:
            patch.setattr(write_deadline, "bounded_write", expired_on_exit)
            with pytest.raises(write_deadline.WriteDeadlineExceeded):
                history.accept_year(
                    db.connection, 2026, accepted_by="Offline owner", accepted_at=now
                )
        assert state() == baseline
        assert not db.connection.in_transaction
