"""Administrative corruption injection only inside an owned disposable test database."""

import os
from contextlib import contextmanager

from swingset.dev.services import ensure_service
from swingset.state.postgres_schema import SEARCH_PATH


@contextmanager
def corruption_connection(worker):
    assert os.environ.get("SWINGSET_TEST_RUN") and worker.info.dbname.startswith("sst_")
    with ensure_service().connect(worker.info.dbname) as owner:
        owner.execute("SELECT set_config('search_path',%s,false)", (SEARCH_PATH,))
        owner.execute("SET session_replication_role=replica")
        yield owner


@contextmanager
def reject_inserts(worker, table, message, *, when=None):
    """Make an actual worker INSERT fail inside its native database transaction."""
    import uuid

    from psycopg import sql

    from swingset.state.postgres_schema import namespace

    name = "test_reject_" + uuid.uuid4().hex[:16]
    function = sql.Identifier("ops", name)
    relation = sql.Identifier(namespace(table), table)
    condition = sql.SQL("") if when is None else sql.SQL(" WHEN ({})").format(sql.SQL(when))
    with corruption_connection(worker) as owner:
        owner.execute(
            sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS {} ").format(
                function,
                sql.Literal(
                    "BEGIN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE="
                    + sql.Literal(message).as_string(owner)
                    + "; END;"
                ),
            )
        )
        owner.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE INSERT ON {} FOR EACH ROW{} EXECUTE FUNCTION {}()"
            ).format(sql.Identifier(name), relation, condition, function)
        )
        try:
            yield
        finally:
            owner.execute(sql.SQL("DROP TRIGGER {} ON {}").format(sql.Identifier(name), relation))
            owner.execute(sql.SQL("DROP FUNCTION {}()").format(function))
