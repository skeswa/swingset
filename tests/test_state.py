from datetime import UTC, datetime

import pytest
from test_postgres_schema import fresh_database as fresh_database

from swingset.state import engine
from swingset.state.db import SCHEMA_VERSION, open_database
from swingset.state.work import WorkUnit, accept_input


def test_fresh_database_has_complete_schema(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        assert database.schema_version == SCHEMA_VERSION
        tables = {
            row[0]
            for row in database.connection.execute(
                "SELECT relname AS name FROM pg_catalog.pg_class WHERE relkind = 'r' AND (PG_TABLE_IS_VISIBLE(oid) AND relnamespace NOT IN ('pg_catalog'::regnamespace,'information_schema'::regnamespace))"
            )
        }
        assert {
            "watches",
            "snapshots",
            "observations",
            "pending_work",
            "events",
            "placements",
            "identity_links",
        } <= tables
        assert database.connection.execute("SHOW server_version_num").fetchone()[0].startswith("17")
        assert (
            database.connection.execute(
                "SELECT count(*) FROM pg_constraint WHERE connamespace IN ('fetch'::regnamespace,'plan'::regnamespace,'evidence'::regnamespace,'derive'::regnamespace,'project'::regnamespace,'release'::regnamespace,'ops'::regnamespace) AND NOT convalidated"
            ).fetchone()[0]
            == 0
        )


def test_input_digest_and_work_cannot_commit_separately(tmp_path) -> None:
    database = open_database(tmp_path, lock=False)
    unit = WorkUnit("link", "event", "2026-08-summer-hummer")
    assert accept_input(
        database, "link", "weights", "one", (unit,), accepted_at="2026-09-08T00:00:00Z"
    )
    database.close()
    with open_database(tmp_path, lock=False) as restarted:
        assert (
            restarted.connection.execute("SELECT digest FROM accepted_inputs").fetchone()[0]
            == "one"
        )
        assert (
            restarted.connection.execute("SELECT unit_id FROM pending_work").fetchone()[0]
            == unit.unit_id
        )


def test_failed_acceptance_rolls_back_digest_and_queue(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        with pytest.raises(engine.IntegrityError):
            accept_input(
                database,
                "link",
                "weights",
                "one",
                (WorkUnit("link", None, "x"),),
                accepted_at="2026-09-08T00:00:00Z",
            )  # type: ignore[arg-type]
        assert (
            database.connection.execute("SELECT COUNT(*) FROM accepted_inputs").fetchone()[0] == 0
        )
        assert database.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0


def test_runs_started_in_same_second_get_stable_suffix(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        instant = datetime(2026, 9, 8, tzinfo=UTC)
        assert database.start_run(instant) == "run_20260908T000000Z"
        assert database.start_run(instant) == "run_20260908T000000Z-2"


def test_explicit_owner_migration_settles_stale_admissions_without_changing_identity(
    tmp_path, fresh_database
):
    from swingset.state import engine, postgres_schema
    from swingset.state.migration_job import run

    service, name, owner = fresh_database
    initial = run(tmp_path, service.dsn(name), environment="dev")
    assert initial["schema_before"] == 0
    engine.write_settings(
        tmp_path, service.dsn(name, role="worker"), control_dsn=service.dsn(name, role="control")
    )
    identity = owner.execute(
        "SELECT environment,database_id FROM ops.environment_identity"
    ).fetchone()
    owner.execute(
        "INSERT INTO ops.execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state) VALUES('request','request','2026-01-01',0,0,0,'active'),('publication','publication','2026-01-01',0,0,0,'active')"
    )
    resumed = run(tmp_path, service.dsn(name))
    assert resumed["schema_before"] == resumed["schema_after"] == postgres_schema.SCHEMA_VERSION
    assert (
        owner.execute("SELECT environment,database_id FROM ops.environment_identity").fetchone()
        == identity
    )
    assert owner.execute(
        "SELECT action_id,state FROM ops.execution_admissions ORDER BY action_id"
    ).fetchall() == [("publication", "uncertain"), ("request", "settled")]


def test_explicit_migration_refuses_a_downgrade_before_recovery(tmp_path, fresh_database):
    from swingset.state import postgres_schema
    from swingset.state.migration_job import run

    service, name, owner = fresh_database
    postgres_schema.migrate(owner)
    owner.execute("UPDATE ops.meta SET value='999' WHERE key='schema_version'")
    owner.execute(
        "INSERT INTO ops.execution_admissions(action_id,action_kind,admitted_at,control_revision,all_sources,all_kinds,state) VALUES('request','request','2026-01-01',0,0,0,'active')"
    )
    with pytest.raises(RuntimeError, match="downgrade"):
        run(tmp_path, service.dsn(name))
    assert owner.execute("SELECT state FROM ops.execution_admissions").fetchone() == ("active",)
