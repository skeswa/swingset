"""The explicit Postgres schema-37 migration and its SQLite parity contract.

The SQL is a reviewed, static migration. SQLite remains a read-only import
format; the pipeline never translates or executes SQLite statements.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

SOURCE_SCHEMA_VERSION = 37
SCHEMA_VERSION = 41
STATEMENT_HISTORY_TRIGGERS = frozenset(
    {
        "history_timing_watches_update",
        "history_timing_work_generations_update",
        "history_timing_pending_work_insert",
        "history_timing_pending_work_update",
    }
)
STATEMENT_WORK_TRIGGERS = frozenset({"work_enqueued", "work_invalidated"})
SEARCH_PATH = 'pg_catalog,"fetch",plan,evidence,derive,project,release,ops,pg_temp'
ROOT = Path(__file__).with_name("postgres_migrations")
TABLE_NAMESPACES: dict[str, str] = {
    "hosts": "fetch",
    "host_budget": "fetch",
    "host_request_spacing": "fetch",
    "host_request_spacing_baselines": "fetch",
    "watches": "fetch",
    "snapshots": "fetch",
    "archive_queries": "fetch",
    "archive_captures": "fetch",
    "cursors": "fetch",
    "acquisition_plan": "fetch",
    "scheduler_requests": "fetch",
    "scheduler_watch_state": "fetch",
    "scheduler_parent_links": "fetch",
    "scheduler_event_policies": "fetch",
    "scheduler_event_requests": "fetch",
    "scheduler_event_turns": "fetch",
    "scheduler_capacity_policies": "fetch",
    "scheduler_capacity_requests": "fetch",
    "scheduler_capacity_service": "fetch",
    "history_origin_intents": "fetch",
    "history_origin_requests": "fetch",
    "history_origin_operator_refs": "fetch",
    "history_dispatch_fence": "fetch",
    "source_event_enumerations": "plan",
    "source_event_enumeration_members": "plan",
    "source_event_member_watches": "plan",
    "source_event_inventory": "plan",
    "event_enumeration_inputs": "plan",
    "source_event_retirement_proofs": "plan",
    "source_event_retirement_receipts": "plan",
    "event_retirement_observations": "plan",
    "event_retirement_receipts": "plan",
    "event_accounting_receipts": "plan",
    "event_accounting_support_observations": "plan",
    "event_gap_observations": "plan",
    "event_gap_revisions": "plan",
    "event_pressure_state": "plan",
    "event_pressure_subjects": "plan",
    "event_pressure_watches": "plan",
    "event_pressure_parents": "plan",
    "event_pressure_hosts": "plan",
    "event_pressure_latches": "plan",
    "event_pressure_dirty": "plan",
    "event_progress_policies": "plan",
    "event_progress_observations": "plan",
    "event_progress_receipts": "plan",
    "event_progress_scans": "plan",
    "event_progress_cursor": "plan",
    "event_stage_operations": "plan",
    "event_blocker_policies": "plan",
    "event_blocker_observations": "plan",
    "event_blocker_latest": "plan",
    "event_blocker_refreshes": "plan",
    "event_blocker_cursor": "plan",
    "event_timing": "plan",
    "event_timing_history": "plan",
    "event_timing_cursor": "plan",
    "source_units": "evidence",
    "source_generations": "evidence",
    "source_generation_packs": "evidence",
    "source_equivalences": "evidence",
    "admission_decisions": "evidence",
    "admission_policies": "evidence",
    "admission_reviews": "evidence",
    "observations": "evidence",
    "findings": "evidence",
    "finding_support": "evidence",
    "finding_support_references": "evidence",
    "requirement_scan": "evidence",
    "registry_verifications": "evidence",
    "derivation_scopes": "derive",
    "derivation_generations": "derive",
    "derivation_building": "derive",
    "derivation_equivalences": "derive",
    "derivation_recipes": "derive",
    "derivation_dependency_sets": "derive",
    "derivation_payloads": "derive",
    "derivation_row_refs": "derive",
    "derivation_rows": "derive",
    "derivation_evicted": "derive",
    "label_body_packs": "derive",
    "derivation_input_versions": "derive",
    "derivation_reads": "derive",
    "derivation_payload_removal_authority": "derive",
    "derivation_payload_removal_grant": "derive",
    "work_queue": "derive",
    "work_attempts": "derive",
    "work_generations": "derive",
    "pending_work": "derive",
    "accepted_inputs": "derive",
    "scheduler_offline_service": "derive",
    "events": "project",
    "contests": "project",
    "rounds": "project",
    "entries": "project",
    "heats": "project",
    "judges": "project",
    "callback_marks": "project",
    "callbacks": "project",
    "final_marks": "project",
    "placements": "project",
    "dancers": "project",
    "registry_placements": "project",
    "coverage": "project",
    "series": "project",
    "canonical_scope_rows": "project",
    "source_events": "project",
    "source_event_map": "project",
    "source_event_scope_rows": "project",
    "source_event_proposals": "project",
    "link_candidates": "project",
    "identity_links": "project",
    "identity_link_history": "project",
    "identity_link_resolutions": "project",
    "identity_source_refs": "project",
    "identity_reference_bindings": "project",
    "identity_reference_migrations": "project",
    "identity_decisions": "project",
    "identity_journal_acceptances": "project",
    "history_acceptance": "project",
    "release_partitions": "release",
    "release_manifests": "release",
    "release_artifacts": "release",
    "runs": "ops",
    "loop_state": "ops",
    "guard_reasons": "ops",
    "meta": "ops",
    "revisions": "ops",
    "control_state": "ops",
    "control_events": "ops",
    "operator_pauses": "ops",
    "execution_admissions": "ops",
    "execution_dependencies": "ops",
    "objects": "ops",
    "blob_classes": "ops",
    "storage_applies": "ops",
    "storage_eviction_authority": "ops",
    "storage_eviction_grant": "ops",
    "retention_applies": "ops",
    "backup_sets": "ops",
    "backup_objects": "ops",
    "backup_uploads": "ops",
}

ROLE_NAMES = ("swingset_owner", "swingset_worker", "swingset_control", "swingset_reader")
ROLE_SQL = """
DO $roles$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='swingset_owner') THEN CREATE ROLE swingset_owner NOLOGIN; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='swingset_worker') THEN CREATE ROLE swingset_worker NOLOGIN; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='swingset_control') THEN CREATE ROLE swingset_control NOLOGIN; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='swingset_reader') THEN CREATE ROLE swingset_reader NOLOGIN; END IF;
END $roles$;
""" + "\n".join(
    f"ALTER ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n"
    f"ALTER ROLE {role} SET timezone='UTC';\n"
    f"ALTER ROLE {role} SET log_statement='none';\n"
    f"ALTER ROLE {role} SET log_min_error_statement='panic';\n"
    f"ALTER ROLE {role} SET log_parameter_max_length=0;\n"
    f"ALTER ROLE {role} SET log_parameter_max_length_on_error=0;\n"
    f"ALTER ROLE {role} SET idle_in_transaction_session_timeout=0;"
    for role in ROLE_NAMES
)

ROWID_TABLES = frozenset(
    {
        "derivation_generations",
        "derivation_equivalences",
        "derivation_dependency_sets",
        "snapshots",
        "watches",
        "source_event_inventory",
        "source_event_enumerations",
        "source_event_enumeration_members",
        "scheduler_event_requests",
        "event_pressure_dirty",
    }
)


@lru_cache(maxsize=1)
def reference_manifest() -> dict[str, Any]:
    """Frozen SQLite catalog, including ordered columns and index members."""
    return cast(dict[str, Any], json.loads((ROOT / "sqlite37_manifest.json").read_text()))


def migration_files() -> tuple[Path, ...]:
    import re

    files = tuple(
        sorted(
            path
            for path in ROOT.glob("*.sql")
            if re.fullmatch(r"[0-9]{4}_.+[.]sql", path.name)
            and int(path.name[:4]) <= SCHEMA_VERSION
        )
    )
    if not files or int(files[0].name[:4]) != 37 or int(files[-1].name[:4]) != SCHEMA_VERSION:
        raise RuntimeError("Postgres migration files do not match the supported schema")
    return files


def migration_sql() -> str:
    return "\n".join(path.read_text() for path in migration_files())


def migration_checksum() -> str:
    return _migration_checksum(migration_files())


def _migration_checksum(files: tuple[Path, ...]) -> str:
    checksum = hashlib.sha256()
    for path in files:
        checksum.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    checksum.update(_GRANTS_FINGERPRINT)
    return checksum.hexdigest()


def roles_checksum() -> str:
    return hashlib.sha256(ROLE_SQL.encode()).hexdigest()


def namespace(table: str) -> str:
    return "ops" if table == "environment_identity" else TABLE_NAMESPACES[table]


def qualified(table: str) -> str:
    return f'"{namespace(table)}"."{table}"'


def install_roles(conn: Any) -> None:
    """Create capability roles once; existing cluster roles are validation-only."""
    rows = conn.execute(
        "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolconfig FROM pg_roles WHERE rolname=ANY(%s)",
        (list(ROLE_NAMES),),
    ).fetchall()
    if not rows:
        conn.execute(ROLE_SQL, prepare=False)
        return
    expected = {
        "timezone": "UTC",
        "log_statement": "none",
        "log_min_error_statement": "panic",
        "log_parameter_max_length": "0",
        "log_parameter_max_length_on_error": "0",
        "idle_in_transaction_session_timeout": "0",
    }
    if len(rows) != len(ROLE_NAMES):
        raise RuntimeError("Postgres capability role definitions are incomplete")
    if (
        conn.execute(
            "SELECT 1 FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member WHERE member.rolname=ANY(%s) LIMIT 1",
            (list(ROLE_NAMES),),
        ).fetchone()
        is not None
    ):
        raise RuntimeError("Postgres capability roles have unexpected inherited privileges")
    for row in rows:
        settings = {
            key.casefold(): value
            for item in (row[-1] or ())
            for key, value in [str(item).split("=", 1)]
        }
        if any(row[1:7]) or settings != expected:
            raise RuntimeError(
                "Postgres capability role definitions differ from the pinned configuration"
            )


def assign_owners(conn: Any) -> None:
    """Transfer administrative ownership while new tables are still empty.

    Import/restore call this before COPY, so final grants never take an
    AccessExclusive table lock against a newly started autovacuum worker.
    Existing correct owners require no DDL.
    """
    owner = conn.execute("SELECT oid FROM pg_roles WHERE rolname='swingset_owner'").fetchone()[0]
    statements = []
    names = sorted(set(TABLE_NAMESPACES.values()))
    for name, current in conn.execute(
        "SELECT nspname,nspowner FROM pg_namespace WHERE nspname=ANY(%s)", (names,)
    ).fetchall():
        if current != owner:
            statements.append(
                'ALTER SCHEMA "' + str(name).replace('"', '""') + '" OWNER TO swingset_owner'
            )
    for namespace_name, name, kind, current in conn.execute(
        "SELECT n.nspname,c.relname,c.relkind,c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','v')",
        (names,),
    ).fetchall():
        if current != owner:
            qualified_name = (
                '"'
                + str(namespace_name).replace('"', '""')
                + '"."'
                + str(name).replace('"', '""')
                + '"'
            )
            statements.append(
                "ALTER "
                + ("VIEW" if kind == "v" else "TABLE")
                + " "
                + qualified_name
                + " OWNER TO swingset_owner"
            )
    for identity, current in conn.execute(
        "SELECT p.oid::regprocedure::text,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY(%s)",
        (names,),
    ).fetchall():
        if current != owner:
            statements.append("ALTER FUNCTION " + str(identity) + " OWNER TO swingset_owner")
    if statements:
        conn.execute(";\n".join(statements), prepare=False)


def grant_roles(conn: Any) -> None:
    """Workers can use data but cannot change schemas, gates or environment identity."""
    assign_owners(conn)
    statements = []
    for schema in sorted(set(TABLE_NAMESPACES.values())):
        quoted = f'"{schema}"'
        statements.append(f"REVOKE ALL ON SCHEMA {quoted} FROM PUBLIC")
        statements.append(f"GRANT USAGE ON SCHEMA {quoted} TO swingset_worker,swingset_control")
        statements.append(f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted} FROM PUBLIC")
        statements.append(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quoted} FROM PUBLIC")
        statements.append(
            f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {quoted} TO swingset_worker,swingset_owner"
        )
        statements.append(
            f"GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {quoted} TO swingset_worker"
        )
    tables = conn.execute(
        "SELECT n.nspname,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','v')",
        (sorted(set(TABLE_NAMESPACES.values())),),
    ).fetchall()
    for schema_name, table, relation_kind in tables:
        if table == "environment_identity":
            continue
        name = (
            '"' + str(schema_name).replace('"', '""') + '"."' + str(table).replace('"', '""') + '"'
        )
        kind = "VIEW" if relation_kind == "v" else "TABLE"
        actions = "SELECT" if kind == "VIEW" else "SELECT,INSERT,UPDATE,DELETE,MAINTAIN"
        statements.append(f"GRANT {actions} ON {name} TO swingset_worker")
        statements.append(f"GRANT SELECT ON {name} TO swingset_control")
    statements.append("GRANT EXECUTE ON FUNCTION pg_catalog.pg_ls_waldir() TO swingset_owner")
    statements.append(
        "GRANT EXECUTE ON FUNCTION ops.schema_identity() TO swingset_control,swingset_reader"
    )
    statements.append(
        "GRANT EXECUTE ON FUNCTION ops.try_timestamp(text),ops.compact_json(text),ops.compact_json(json),ops.compact_json(jsonb),ops.text_bytes(text,integer,integer) TO swingset_control"
    )
    statements.append("GRANT USAGE ON SCHEMA ops TO swingset_control,swingset_reader")
    statements.append(
        "GRANT SELECT ON ops.environment_identity TO swingset_worker,swingset_control,swingset_reader"
    )
    statements.append(
        "GRANT SELECT,INSERT,UPDATE,DELETE ON ops.operator_pauses,ops.control_state,ops.control_events TO swingset_control"
    )
    statements.append(
        "GRANT SELECT ON ops.execution_admissions,ops.execution_dependencies TO swingset_control"
    )
    statements.append("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA ops TO swingset_control")
    conn.execute(";\n".join(statements), prepare=False)


def _grant_fingerprint() -> bytes:
    import ast
    import inspect
    import textwrap

    trees = [
        ast.parse(textwrap.dedent(inspect.getsource(function)))
        for function in (assign_owners, grant_roles)
    ]
    return "\n".join(ast.dump(tree, include_attributes=False) for tree in trees).encode()


# Freeze while code and source line numbers agree. Re-reading inspect source
# after another editor changes preceding functions fingerprints the wrong AST.
_GRANTS_FINGERPRINT = _grant_fingerprint()


def migration_phases() -> dict[str, str]:
    """Loading installs tables first and never exposes an unfenced worker database."""
    import re

    files = migration_files()
    chunks = re.split(r"(?m)^-- swingset-phase: (\w+)\s*$", files[0].read_text())
    phases = {chunks[i]: chunks[i + 1] for i in range(1, len(chunks), 2)}
    phases["forward"] = "\n".join(path.read_text() for path in files[1:])
    return phases


def seed_environment(conn: Any, environment: str = "dev", database_id: str | None = None) -> str:
    """Assign the destination identity as an administrator, never the worker."""
    if environment not in {"dev", "dryrun", "production"}:
        raise ValueError("environment must be dev, dryrun or production")
    identifier = database_id or str(uuid.uuid4())
    uuid.UUID(identifier)
    conn.execute(
        "INSERT INTO ops.environment_identity(singleton,environment,database_id,created_at) VALUES(1,%s,%s,%s) ON CONFLICT(singleton) DO UPDATE SET environment=EXCLUDED.environment,database_id=EXCLUDED.database_id,created_at=EXCLUDED.created_at",
        (environment, identifier, datetime.now(UTC).isoformat()),
    )
    return identifier


def validate_schema(conn: Any, *, full: bool = False) -> dict[str, Any]:
    """Ordinary opening checks version/checksum only; parity is an explicit job."""
    try:
        found = dict(conn.execute("SELECT key,value FROM ops.schema_identity()"))
    except Exception as exc:
        raise RuntimeError("Postgres schema is not initialized; run the migration job") from exc
    if (
        found.get("schema_version") != str(SCHEMA_VERSION)
        or found.get("source_schema_version", found.get("schema_version"))
        != str(SOURCE_SCHEMA_VERSION)
        or found.get("postgres_migration_checksum") != migration_checksum()
    ):
        raise RuntimeError("Postgres schema version or migration checksum differs from this code")
    return (
        verify(conn)
        if full
        else {
            "schema_version": SCHEMA_VERSION,
            "migration_checksum": migration_checksum(),
            "full": False,
        }
    )


def validate_checkpoint_schema(conn: Any) -> dict[str, Any]:
    """Read one known row-compatible checkpoint; never migrate or admit a worker."""
    if (
        conn.execute("SHOW default_transaction_read_only").fetchone()[0] != "on"
        or conn.execute("SHOW transaction_read_only").fetchone()[0] != "on"
    ):
        raise ValueError("checkpoint compatibility requires a read-only connection")
    found = dict(conn.execute("SELECT key,value FROM ops.schema_identity()"))
    if found.get("schema_version") == str(SCHEMA_VERSION):
        return validate_schema(conn)
    # Migrations 39/40 change only triggers; 41 adds nonunique indexes. Their
    # source rows retain the native-38 shape. Future versions need a separate
    # compatibility choice, and ordinary workers still require the current schema.
    if SCHEMA_VERSION not in {39, 40, 41}:
        raise RuntimeError("checkpoint schema version or migration checksum differs")
    files = tuple(path for path in migration_files() if int(path.name[:4]) <= 38)
    checksum = _migration_checksum(files)
    if (
        found.get("schema_version") != "38"
        or found.get("source_schema_version") != str(SOURCE_SCHEMA_VERSION)
        or found.get("postgres_migration_checksum") != checksum
    ):
        raise RuntimeError("checkpoint schema version or migration checksum differs")
    return {"schema_version": 38, "migration_checksum": checksum, "full": False}


def migrate(conn: Any, *, environment: str | None = None) -> dict[str, Any]:
    """Apply checked-in forward scripts explicitly; ordinary opening never calls this.

    The caller owns the state/control/write lock envelope and settles stale
    admissions first. Every applied script is immutable, including on restart.
    Existing target identities survive a forward migration unchanged.
    """
    from . import postgres_bootstrap

    prepared = postgres_bootstrap.adopt(conn)
    existing = (
        not prepared and conn.execute("SELECT to_regclass('ops.meta')").fetchone()[0] is not None
    )
    current = 0
    applied: dict[str, str] = {}
    if existing:
        version = conn.execute("SELECT value FROM ops.meta WHERE key='schema_version'").fetchone()
        source_version = int(version[0]) if version else 0
        comment = conn.execute(
            "SELECT obj_description('ops'::regnamespace,'pg_namespace')"
        ).fetchone()[0]
        metadata = json.loads(comment) if comment else {}
        current = int(metadata.get("schema_version", source_version))
        applied = dict(metadata.get("scripts", {}))
        if current > SCHEMA_VERSION or source_version > SOURCE_SCHEMA_VERSION:
            raise RuntimeError("Postgres migration refuses a schema downgrade")
        if source_version != SOURCE_SCHEMA_VERSION:
            raise RuntimeError("Postgres retained source schema version differs")
        if not applied:
            raise RuntimeError("applied Postgres migration checksums are missing")
        if environment is not None:
            found_environment = conn.execute(
                "SELECT environment FROM ops.environment_identity WHERE singleton=1"
            ).fetchone()
            if found_environment is None or found_environment[0] != environment:
                raise RuntimeError("migration cannot change an existing database environment")
    files = migration_files()
    expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    if any(
        name not in expected or expected[name] != checksum for name, checksum in applied.items()
    ):
        raise RuntimeError("an applied Postgres migration checksum differs")
    if any(path.name not in applied for path in files if int(path.name[:4]) <= current):
        raise RuntimeError("applied Postgres migration history is incomplete")
    # A fresh destination receives its immutable identity only after all DDL
    # accepts that environment. An interrupted existing upgrade preserves it.
    fresh = not existing
    with conn.transaction():
        for path in files:
            number = int(path.name[:4])
            if number <= current:
                continue
            if current and number != current + 1:
                raise RuntimeError("Postgres migration sequence has a gap")
            install_roles(conn)
            if prepared and number == SOURCE_SCHEMA_VERSION:
                phases = migration_phases()
                for phase in ("constraints", "triggers", "seed"):
                    conn.execute(phases[phase], prepare=False)
            else:
                conn.execute(path.read_text(), prepare=False)
            assign_owners(conn)
            if number > 37:
                install_publication_fences(conn)
            applied[path.name] = expected[path.name]
            stamp_schema(conn, scripts=applied, native_version=number)
            existing, current = True, number
        if fresh:
            seed_environment(conn, environment or "dev")
        grant_roles(conn)
        stamp_schema(conn, scripts=applied)
    return validate_schema(conn)


def _default(value: Any) -> Any:
    """Compare literal defaults across PostgreSQL's harmless cast decoration."""
    import re

    if value is None:
        return None
    text = re.sub(r"::(?:text|bigint|integer|double precision)", "", str(value)).strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1].replace("''", "'")
        # PostgreSQL writes numeric defaults with a cast from a quoted literal.
        if not re.search(r"::(?:bigint|integer|double precision)", str(value)):
            return text
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _short_identifier(name: str) -> str:
    return (
        name
        if len(name) <= 63
        else name[:52] + "_" + hashlib.sha256(name.encode()).hexdigest()[:10]
    )


def verify(conn: Any) -> dict[str, Any]:
    """Compare every source column, ordered key, index and FK action, plus trigger code.

    The source manifest is frozen independently from the destination migration.
    Extra physical identity columns and the destination environment identity are
    explicitly named exceptions; no other extra/missing object is accepted.
    """
    import re

    expected = reference_manifest()
    errors: list[str] = []
    actual_tables = {
        (str(schema), str(table))
        for schema, table in conn.execute(
            "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relkind='r' AND n.nspname=ANY(%s)",
            (sorted(set(TABLE_NAMESPACES.values())),),
        )
    }
    wanted_tables = {(namespace(n), n) for n in expected["tables"]} | {
        ("ops", "environment_identity")
    }
    if actual_tables != wanted_tables:
        errors.append(
            f"table names differ: missing={sorted(wanted_tables - actual_tables)} extra={sorted(actual_tables - wanted_tables)}"
        )
    types = {"TEXT": "text", "INTEGER": "int8", "REAL": "float8"}

    def catalog_rows(statement: str) -> dict[tuple[str, str], list[tuple[Any, ...]]]:
        grouped: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
        for row in conn.execute(statement, (sorted(set(TABLE_NAMESPACES.values())),)):
            grouped.setdefault((str(row[0]), str(row[1])), []).append(tuple(row[2:]))
        return grouped

    # Read the same catalog facts for every application table in five queries.
    # Keep each comparison below independent of the destination DDL generator.
    columns_by_table = catalog_rows(
        "SELECT n.nspname,c.relname,a.attname,t.typname,a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid=c.oid JOIN pg_type t ON t.oid=a.atttypid "
        "LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE n.nspname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY n.nspname,c.relname,a.attnum"
    )
    primary_by_table = catalog_rows(
        "SELECT n.nspname,c.relname,ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY x(attnum,ord) "
        "JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.attnum ORDER BY x.ord) "
        "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=ANY(%s) AND k.contype='p'"
    )
    foreign_by_table = catalog_rows(
        "SELECT n.nspname,c.relname,target.relname AS referenced_table,ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY x(attnum,ord) JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.attnum ORDER BY x.ord) AS local_cols,"
        "ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY x(attnum,ord) JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.attnum ORDER BY x.ord) AS foreign_cols,"
        "k.confupdtype,k.confdeltype,k.condeferred FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_class target ON target.oid=k.confrelid "
        "WHERE n.nspname=ANY(%s) AND k.contype='f'"
    )
    checks_by_table = catalog_rows(
        "SELECT n.nspname,c.relname,pg_get_expr(k.conbin,k.conrelid) FROM pg_constraint k "
        "JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=ANY(%s) AND k.contype='c'"
    )
    indexes_by_table = catalog_rows(
        "SELECT n.nspname,c.relname,i.relname AS index_name,x.indisunique,x.indisprimary,pg_get_expr(x.indpred,x.indrelid),"
        "ARRAY(SELECT CASE WHEN k.attnum=0 THEN NULL ELSE a.attname END FROM unnest(x.indkey::smallint[]) WITH ORDINALITY k(attnum,ord) LEFT JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum ORDER BY k.ord),"
        "x.indoption::smallint[] FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_class i ON i.oid=x.indexrelid "
        "WHERE n.nspname=ANY(%s)"
    )
    for table, specification in sorted(expected["tables"].items()):
        table_key = (namespace(table), table)
        found_columns = columns_by_table.get(table_key, [])
        source_columns = specification["columns"]
        names = [str(row[0]) for row in found_columns]
        wanted = [str(col["name"]) for col in source_columns] + (
            ["rowid"] if table in ROWID_TABLES else []
        )
        if names != wanted:
            errors.append(f"{table}: ordered column names differ")
            continue
        for column, actual in zip(source_columns, found_columns, strict=False):
            name, kind, required, default, identity = actual
            # PostgreSQL PKs are intrinsically NOT NULL; import rejects NULL keys.
            expected_required = bool(column["notnull"] or column["pk"])
            if str(kind) != types[column["type"]] or bool(required) != expected_required:
                errors.append(f"{table}.{name}: type or nullability differs")
            if _default(default) != _default(column["dflt_value"]):
                errors.append(f"{table}.{name}: default differs")
            integer_key = (
                column["type"] == "INTEGER"
                and column["pk"]
                and len([c for c in source_columns if c["pk"]]) == 1
            )
            if bool(identity) != bool(integer_key):
                errors.append(f"{table}.{name}: identity behavior differs")
        primary_rows = primary_by_table.get(table_key, [])
        primary = primary_rows[0] if primary_rows else None
        wanted_key = [c["name"] for c in sorted(source_columns, key=lambda c: c["pk"]) if c["pk"]]
        if (list(primary[0]) if primary else []) != wanted_key:
            errors.append(f"{table}: primary key order differs")
        foreign = set()
        for referenced, local_cols, foreign_cols, update, delete, deferred in foreign_by_table.get(
            table_key, []
        ):
            foreign.add(
                (
                    str(referenced),
                    tuple(local_cols),
                    tuple(foreign_cols),
                    str(update),
                    str(delete),
                    bool(deferred),
                )
            )
        actions = {
            "NO ACTION": "a",
            "RESTRICT": "r",
            "CASCADE": "c",
            "SET NULL": "n",
            "SET DEFAULT": "d",
        }
        source_foreign: dict[int, list[dict[str, Any]]] = {}
        for fk in specification["foreign_keys"]:
            source_foreign.setdefault(int(fk["id"]), []).append(fk)
        wanted_foreign = set()
        for group in source_foreign.values():
            group.sort(key=lambda fk: fk["seq"])
            first = group[0]
            local = tuple(f["from"] for f in group)
            # SQLite PRAGMA omits deferred metadata, retained in its source DDL.
            deferred = bool(
                re.search(
                    r"(?:"
                    + "|".join(re.escape(c) for c in local)
                    + r")[^,]*?REFERENCES[^,]*?DEFERRABLE INITIALLY DEFERRED",
                    specification["sql"],
                    re.I,
                )
            )
            wanted_foreign.add(
                (
                    first["table"],
                    local,
                    tuple(f["to"] for f in group),
                    actions[first["on_update"]],
                    actions[first["on_delete"]],
                    deferred,
                )
            )
        if foreign != wanted_foreign:
            errors.append(f"{table}: foreign-key columns/actions/deferred behavior differ")
        from collections import Counter

        actual_checks = Counter(
            _predicate(str(row[0])) for row in checks_by_table.get(table_key, [])
        )
        expected_checks = Counter(_predicate(value) for value in _checks(specification["sql"]))
        if actual_checks != expected_checks:
            errors.append(f"{table}: check constraints differ")
        # Indexes have byte-order collation by database construction. Their
        # ordered fields and descending bits must survive independently of names.
        indexes = indexes_by_table.get(table_key, [])
        for item in specification["indexes"]:
            if item["origin"] == "pk":
                continue
            wanted_fields = tuple(c["name"] for c in item["columns"] if c["key"])
            candidates = [
                row
                for row in indexes
                if not row[2]
                and bool(row[1]) == bool(item["unique"])
                and tuple(row[4]) == wanted_fields
            ]
            if item["origin"] == "c":
                candidates = [row for row in candidates if row[0] == item["name"]]
            if not candidates or bool(candidates[0][3]) != bool(item["partial"]):
                errors.append(f"{table}.{item['name']}: index uniqueness/fields/predicate differs")
                continue
            if item["partial"]:
                source_sql = next(
                    obj["sql"]
                    for obj in expected["objects"]
                    if obj["name"] == item["name"] and obj["type"] == "index"
                )
                source_predicate = re.split(r"\bWHERE\b", source_sql, flags=re.I)[1]
                if _predicate(str(candidates[0][3])) != _predicate(source_predicate):
                    errors.append(f"{table}.{item['name']}: partial-index predicate differs")
            descending = tuple(bool(int(bit) & 1) for bit in candidates[0][5])
            expected_descending = tuple(bool(c["desc"]) for c in item["columns"] if c["key"])
            if descending != expected_descending:
                errors.append(f"{table}.{item['name']}: index ordering differs")
    trigger_rows = list(
        conn.execute(
            "SELECT t.tgname,c.relname,n.nspname,p.proname,p.prosrc,t.tgtype,t.tgattr::smallint[],t.tgnewtable,t.tgoldtable FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid "
            "WHERE NOT t.tgisinternal AND n.nspname=ANY(%s)",
            (sorted(set(TABLE_NAMESPACES.values())),),
        )
    )
    expected_triggers = [obj for obj in expected["objects"] if obj["type"] == "trigger"]
    by_name = {str(row[0]): row for row in trigger_rows}
    wanted_trigger_names = {
        _short_identifier(("a_" + t["name"]) if t["name"].startswith("publication_") else t["name"])
        for t in expected_triggers
    }
    if set(by_name) != wanted_trigger_names:
        errors.append("trigger names/coverage differ")
    function_bodies = {
        match[0]: match[1]
        for match in re.findall(
            r'CREATE(?: OR REPLACE)? FUNCTION "[^"]+"\."([^"]+)"\(\) RETURNS trigger.*?AS \$fn\$(.*?)\$fn\$;',
            migration_sql(),
            re.S,
        )
    }
    for trigger in expected_triggers:
        name = _short_identifier(
            ("a_" + trigger["name"])
            if trigger["name"].startswith("publication_")
            else trigger["name"]
        )
        actual_trigger = by_name.get(name)
        if actual_trigger is None:
            continue
        function_name = _short_identifier("tr_" + trigger["name"])
        if (
            actual_trigger[1] != trigger["tbl_name"]
            or actual_trigger[2] != namespace(trigger["tbl_name"])
            or actual_trigger[3] != function_name
            or str(actual_trigger[4]).strip() != function_bodies[function_name].strip()
        ):
            errors.append(f"{trigger['name']}: trigger owner/function body differs")
        transition = None
        if SCHEMA_VERSION >= 39 and trigger["name"] in STATEMENT_HISTORY_TRIGGERS:
            transition = "swingset_changed_rows"
        elif SCHEMA_VERSION >= 40 and trigger["name"] in STATEMENT_WORK_TRIGGERS:
            transition = "swingset_pending_rows"
        statement = transition is not None
        if bool(int(actual_trigger[5]) & 1) != (not statement) or tuple(actual_trigger[7:]) != (
            transition,
            None,
        ):
            errors.append(f"{trigger['name']}: trigger level or transition tables differ")
        if statement:
            event = re.search(
                r"\b(AFTER|BEFORE|INSTEAD\s+OF)\s+(INSERT|UPDATE|DELETE)\b",
                trigger["sql"],
                re.I,
            )
            if event is None:
                raise RuntimeError("frozen statement-trigger event is missing")
            timing = {"AFTER": 0, "BEFORE": 2, "INSTEAD OF": 64}[event[1].upper()]
            kind = {"INSERT": 4, "DELETE": 8, "UPDATE": 16}[event[2].upper()]
            if int(actual_trigger[5]) != timing | kind:
                errors.append(f"{trigger['name']}: trigger event or timing differs")
    views = list(
        conn.execute(
            "SELECT schemaname,viewname FROM pg_views WHERE schemaname=ANY(%s)",
            (sorted(set(TABLE_NAMESPACES.values())),),
        )
    )
    if set(map(tuple, views)) != {("derive", "derivation_rows")}:
        errors.append("view names differ")
    result = {
        "schema_version": SCHEMA_VERSION,
        "source_tables": len(expected["tables"]),
        "tables": len(actual_tables),
        "triggers": len(trigger_rows),
        "views": len(views),
        "migration_checksum": migration_checksum(),
        "errors": errors,
        "equal": not errors,
    }
    if errors:
        raise RuntimeError("Postgres schema parity failed: " + "; ".join(errors[:10]))
    return result


def _predicate(expression: str) -> str:
    import re

    value = re.sub(
        r"'(-?[0-9]+(?:[.][0-9]+)?)'::(?:bigint|integer|double precision)", r"\1", expression
    )
    literals: dict[str, str] = {}

    def preserve(match: re.Match[str]) -> str:
        key = f"__literal_{len(literals)}__"
        literals[key] = match[0]
        return key

    value = re.sub(r"'(?:[^']|'')*'", preserve, value)
    value = re.sub(r"::(?:text|bigint|integer|double precision)", "", value)
    value = re.sub(
        r"(\w+)\s+BETWEEN\s+(-?[0-9]+)\s+AND\s+(-?[0-9]+)", r"\1>=\2 AND \1<=\3", value, flags=re.I
    )
    value = re.sub(r"<>\s*ALL\s*\(\s*ARRAY\[([^\]]*)\]\s*\)", r"NOT IN(\1)", value, flags=re.I)
    value = re.sub(r"=\s*ANY\s*\(\s*ARRAY\[([^\]]*)\]\s*\)", r"IN(\1)", value, flags=re.I)
    value = value.replace("!=", "<>")
    value = re.sub(r"[\s()\"]", "", value).casefold()
    for key, literal in literals.items():
        value = value.replace(key, literal)
    return value


def stamp_schema(
    conn: Any, *, scripts: dict[str, str] | None = None, native_version: int | None = None
) -> None:
    from psycopg import sql

    metadata = json.dumps(
        {
            "schema_version": SCHEMA_VERSION if native_version is None else native_version,
            "migration_checksum": migration_checksum(),
            "scripts": scripts
            if scripts is not None
            else {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in migration_files()
            },
        },
        separators=(",", ":"),
    )
    conn.execute(sql.SQL("COMMENT ON SCHEMA ops IS {}").format(sql.Literal(metadata)))


def semantic_columns(table: str) -> tuple[str, ...]:
    """Original SQLite fields, without destination physical identity columns."""
    if table == "derivation_rows":
        return ("generation_id", "ordinal", "table_name", "record_key", "payload_json")
    if table == "environment_identity":
        return ("singleton", "environment", "database_id", "created_at")
    return tuple(str(column["name"]) for column in reference_manifest()["tables"][table]["columns"])


def _checks(statement: str) -> list[str]:
    import re

    result = []
    for match in re.finditer(r"\bCHECK\s*\(", statement, re.I):
        start = match.end()
        depth, quoted, index = 1, False, start
        while depth and index < len(statement):
            value = statement[index]
            if value == "'":
                if quoted and index + 1 < len(statement) and statement[index + 1] == "'":
                    index += 1
                else:
                    quoted = not quoted
            elif not quoted:
                depth += (value == "(") - (value == ")")
            index += 1
        if depth:
            raise RuntimeError("unbalanced source check constraint")
        result.append(statement[start : index - 1])
    return result


def install_publication_fences(conn: Any) -> dict[str, int]:
    """Explicit migration-time fence refresh, including newly added tables.

    Known row-narrowing rules are native SQL in the initial migration. New
    application tables receive the broad publication fence until a later
    reviewed migration supplies a narrower rule. The destination environment
    identity is administrative metadata protected by grants, never worker DML.
    """
    import re

    from psycopg import sql

    existing = {
        (str(a), str(b))
        for a, b in conn.execute(
            "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname=ANY(%s)",
            (sorted(set(TABLE_NAMESPACES.values())),),
        )
    }
    statements: list[str] = []
    source = migration_files()[0].read_text()
    for match in re.finditer(
        r'CREATE FUNCTION "([^"]+)"\."(tr_publication_[^"]+)"\(\) RETURNS trigger.*?\$fn\$;',
        source,
        re.S,
    ):
        statements.append(match[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    known = 0
    for match in re.finditer(
        r'CREATE TRIGGER "([^"]+)".*? ON "([^"]+)"\."([^"]+)" FOR EACH ROW EXECUTE FUNCTION [^;]+;',
        source,
    ):
        if not match[1].startswith("a_publication_") or (match[2], match[3]) not in existing:
            continue
        statements.append(
            sql.SQL("DROP TRIGGER IF EXISTS {} ON {}")
            .format(sql.Identifier(match[1]), sql.Identifier(match[2], match[3]))
            .as_string(conn)
        )
        statements.append(match[0])
        known += 1
    statements.append(f"""CREATE OR REPLACE FUNCTION ops.tr_publication_new_table() RETURNS trigger
LANGUAGE plpgsql SET search_path={SEARCH_PATH} AS $fn$
BEGIN
 IF EXISTS(SELECT 1 FROM ops.execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) THEN
  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='active publication fences semantic writes';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $fn$""")
    new = 0
    exempt = {
        "operator_pauses",
        "control_state",
        "control_events",
        "execution_admissions",
        "execution_dependencies",
        "backup_sets",
        "backup_objects",
        "environment_identity",
    }
    for schema_name, table in sorted(existing):
        if table in TABLE_NAMESPACES or table in exempt:
            continue
        for action in ("INSERT", "UPDATE", "DELETE"):
            name = _short_identifier(f"a_publication_fence_{table}_{action.lower()}")
            target = sql.Identifier(schema_name, table)
            statements.append(
                sql.SQL("DROP TRIGGER IF EXISTS {} ON {}")
                .format(sql.Identifier(name), target)
                .as_string(conn)
            )
            statements.append(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE "
                    + action
                    + " ON {} FOR EACH ROW EXECUTE FUNCTION ops.tr_publication_new_table()"
                )
                .format(sql.Identifier(name), target)
                .as_string(conn)
            )
            new += 1
    conn.execute(";\n".join(statements) + ";", prepare=False)
    return {"known_fences": known, "new_fences": new}
