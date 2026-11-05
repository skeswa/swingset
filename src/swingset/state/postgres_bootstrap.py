"""A trusted host creates the frozen table phase; app owners finish loading it.

Schema 37 includes one PostgreSQL internal function that only a cluster
administrator can define. The administrator's credential is used only here,
never retained by an application or migration container. A database comment
binds the prepared, still-empty catalog to this exact code and destination.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import postgres_schema as schema

KEY = "swingset_prepared_tables"

_CATALOG = (
    "SELECT nspname AS namespace,pg_get_userbyid(nspowner) AS owner_name,nspacl::text AS privileges FROM pg_namespace WHERE nspname=ANY(%s) ORDER BY nspname",
    "SELECT n.nspname AS namespace,c.relname AS relation_name,c.relkind AS relation_kind,pg_get_userbyid(c.relowner) AS owner_name,c.relacl::text AS privileges,c.relpersistence AS persistence,c.relrowsecurity AS row_security,c.relforcerowsecurity AS forced_row_security FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) ORDER BY n.nspname,c.relname",
    "SELECT n.nspname AS namespace,c.relname AS relation_name,a.attnum AS position,a.attname AS column_name,format_type(a.atttypid,a.atttypmod) AS data_type,a.attnotnull AS required,pg_get_expr(d.adbin,d.adrelid) AS default_expression,a.attidentity AS identity_kind,a.attgenerated AS generated_kind,a.attstorage AS storage_kind,a.attcollation::regcollation::text AS collation_name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY n.nspname,c.relname,a.attnum",
    "SELECT n.nspname AS namespace,c.relname AS relation_name,k.conname AS constraint_name,k.contype AS constraint_kind,pg_get_constraintdef(k.oid) AS definition,k.convalidated AS validated FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) ORDER BY n.nspname,c.relname,k.conname",
    "SELECT n.nspname AS namespace,c.relname AS table_name,i.relname AS index_name,pg_get_indexdef(i.oid) AS definition FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) ORDER BY n.nspname,c.relname,i.relname",
    "SELECT n.nspname AS namespace,p.proname AS function_name,pg_get_function_identity_arguments(p.oid) AS arguments,pg_get_functiondef(p.oid) AS definition,pg_get_userbyid(p.proowner) AS owner_name,p.proacl::text AS privileges FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY(%s) ORDER BY n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)",
    "SELECT n.nspname AS namespace,c.relname AS relation_name,t.tgname AS trigger_name,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) AND NOT t.tgisinternal ORDER BY n.nspname,c.relname,t.tgname",
)


def _names() -> list[str]:
    return sorted(set(schema.TABLE_NAMESPACES.values()))


def _comment(connection: Any) -> dict[str, Any]:
    value = connection.execute(
        "SELECT obj_description(oid,'pg_namespace') FROM pg_namespace WHERE nspname='ops'"
    ).fetchone()
    if value is None or value[0] is None:
        return {}
    try:
        result = json.loads(value[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("prepared database metadata is invalid") from exc
    if not isinstance(result, dict):
        raise RuntimeError("prepared database metadata is invalid")
    return result


def prepared(connection: Any) -> bool:
    return KEY in _comment(connection)


def _binding(connection: Any) -> dict[str, Any]:
    oid, name, owner = connection.execute(
        "SELECT oid,datname,pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
    ).fetchone()
    execute, grant_option = connection.execute(
        "SELECT has_function_privilege('swingset_owner','pg_catalog.pg_ls_waldir()','EXECUTE') AS wal_execute,has_function_privilege('swingset_owner','pg_catalog.pg_ls_waldir()','EXECUTE WITH GRANT OPTION') AS wal_grant_option"
    ).fetchone()
    if execute is not True or grant_option is not False:
        raise RuntimeError(
            "prepared database owner WAL privilege must be execute without grant authority"
        )
    return {
        "format": 1,
        "database": {"oid": oid, "name": name, "owner": owner},
        "tables_phase_sha256": hashlib.sha256(
            schema.migration_phases()["tables"].encode()
        ).hexdigest(),
        "migration_sha256": schema.migration_checksum(),
        "roles_sha256": schema.roles_checksum(),
        "owner_wal_execute": execute,
        "owner_wal_grant_option": grant_option,
    }


def _catalog(connection: Any) -> str:
    with connection.transaction():
        connection.execute("SET LOCAL search_path=pg_catalog")
        rows = [
            [tuple(row) for row in connection.execute(query, (_names(),))] for query in _CATALOG
        ]
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _empty(connection: Any) -> None:
    actual = {
        (str(namespace), str(name))
        for namespace, name in connection.execute(
            "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) AND c.relkind='r'",
            (_names(),),
        )
    }
    expected = {(schema.namespace(name), name) for name in schema.reference_manifest()["tables"]}
    expected.add(("ops", "environment_identity"))
    if actual != expected:
        raise RuntimeError("prepared database table inventory differs")
    for name in (*schema.reference_manifest()["tables"], "environment_identity"):
        if connection.execute("SELECT 1 FROM " + schema.qualified(name) + " LIMIT 1").fetchone():
            raise RuntimeError("prepared database is not empty")


def adopt(connection: Any) -> bool:
    """Validate an explicit prepared phase; never accept an arbitrary existing DB."""
    metadata = _comment(connection)
    if KEY not in metadata:
        return False
    if not connection.execute(
        "SELECT pg_has_role(current_user,'swingset_owner','USAGE')"
    ).fetchone()[0]:
        raise RuntimeError("only the application owner may adopt prepared tables")
    expected = _binding(connection)
    recorded = metadata[KEY]
    if not isinstance(recorded, dict) or set(recorded) != {*expected, "catalog_sha256"}:
        raise RuntimeError("prepared database binding differs")
    if any(recorded[key] != value for key, value in expected.items()):
        raise RuntimeError("prepared database binding differs")
    schema.install_roles(connection)  # Existing role definitions are validation-only.
    if _catalog(connection) != recorded["catalog_sha256"]:
        raise RuntimeError("prepared database catalog differs")
    _empty(connection)
    connection.execute("SET search_path=" + schema.SEARCH_PATH)
    return True


def prepare(connection: Any) -> dict[str, Any]:
    """Cluster-admin-only table bootstrap, atomic and idempotent while still empty."""
    from psycopg import sql

    if not connection.execute(
        "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
    ).fetchone()[0]:
        raise RuntimeError("initial table bootstrap requires a trusted cluster administrator")
    if adopt(connection):
        return _comment(connection)[KEY]  # type: ignore[no-any-return]
    if connection.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname=ANY(%s) LIMIT 1", (_names(),)
    ).fetchone():
        raise RuntimeError("initial table bootstrap requires an empty application database")
    with connection.transaction():
        schema.install_roles(connection)
        connection.execute("GRANT EXECUTE ON FUNCTION pg_catalog.pg_ls_waldir() TO swingset_owner")
        _binding(connection)  # Refuse unexpected pre-existing grant authority before DDL.
        connection.execute(schema.migration_phases()["tables"], prepare=False)
        schema.assign_owners(connection)
        _empty(connection)
        marker = {**_binding(connection), "catalog_sha256": _catalog(connection)}
        connection.execute(
            sql.SQL("COMMENT ON SCHEMA ops IS {}").format(
                sql.Literal(json.dumps({KEY: marker}, sort_keys=True, separators=(",", ":")))
            )
        )
    return marker


def initialize_tables(connection: Any) -> None:
    """Start a loader, accepting fresh privileged fixtures or checked host bootstrap."""
    if adopt(connection):
        return
    prepare(connection)
