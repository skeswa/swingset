"""Exact source identity and a read-only prediction of code-input acceptance.

Projection never edits source rows. It permits only the known watch invalidation
and NULL-scope registration effects; every other checkpoint field is hashed as-is.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from swingset.fetch.archive import canonical, digest
from swingset.state import engine


class UnsupportedCheckpointTransition(ValueError):
    """The proposed inputs need a different, explicitly checked projection."""


def _queries(conn: engine.Connection) -> dict[str, str]:
    from .read_sets import CANONICAL

    queries = {
        "derivations": (
            "SELECT stage, unit_kind, unit_id, materialized_generation_id FROM derivation_scopes ORDER "
            "BY stage NULLS FIRST, unit_kind NULLS FIRST, unit_id NULLS FIRST"
        ),
        "sources": (
            "SELECT unit_key, watch_id, accepted_generation_id, legacy_snapshot_id FROM source_units "
            "ORDER BY unit_key NULLS FIRST"
        ),
    }
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT relname AS name FROM pg_catalog.pg_class WHERE relkind = 'r' AND (PG_TABLE_IS_VISIBLE(oid) AND relnamespace NOT IN ('pg_catalog'::regnamespace,'information_schema'::regnamespace))"
        )
    }
    for table in sorted(
        (CANONICAL | {"watches", "admission_policies"}) - {"identity_journal_acceptances"}
    ):
        if table in tables:
            # ORDER BY all columns avoids dependence on row insertion order.
            from .schema_catalog import columns as column_names

            columns = list(column_names(conn, table))
            # The same immutable decision bundle can be accepted independently
            # on the proof copy and import at different instants. Its hashes
            # bind content; ingestion timestamps do not define another input.
            if table in {"identity_decisions", "identity_source_refs"}:
                columns = [name for name in columns if name not in {"accepted_at", "created_at"}]
            projection = ",".join(f'"{column}"' for column in columns)
            queries[table] = f'SELECT {projection} FROM "{table}" ORDER BY ' + ",".join(
                str(i + 1) for i in range(len(columns))
            )
    return queries


def _identities(
    conn: engine.Connection, overrides: Mapping[str, tuple[str, Any]]
) -> dict[str, Any]:
    source, candidate = hashlib.sha256(), hashlib.sha256()
    components = {}
    for name, query in sorted(_queries(conn).items()):
        label = canonical(name)
        source.update(label)
        candidate.update(label)
        before = hashlib.sha256(label)
        count = 0
        for row in conn.execute(query):
            body = canonical(tuple(row))
            source.update(body)
            before.update(body)
            if name not in overrides:
                candidate.update(body)
            count += 1
        original = {"sha256": before.hexdigest(), "rows": count}
        if name in overrides:
            projected = hashlib.sha256(label)
            count = 0
            sql, params = overrides[name]
            for row in conn.execute(sql, params):
                body = canonical(tuple(row))
                candidate.update(body)
                projected.update(body)
                count += 1
            after = {"sha256": projected.hexdigest(), "rows": count}
        else:
            after = original
        components[name] = {"source": original, "candidate": after}
    return {
        "source_checkpoint": source.hexdigest(),
        "checkpoint": candidate.hexdigest(),
        "components": components,
    }


def checkpoint_identity(conn: engine.Connection) -> str:
    """Keep the original canonical tables, pointers, ordering and hash bytes."""
    checksum = hashlib.sha256()
    for name, query in sorted(_queries(conn).items()):
        checksum.update(canonical(name))
        for row in conn.execute(query):
            checksum.update(canonical(tuple(row)))
    return checksum.hexdigest()


def input_acceptance_binding(conn: engine.Connection, bundle: Any, clock: Any) -> dict[str, Any]:
    """Predict code-only acceptance in an owned read snapshot, without writes.

    A caller must still compare the ordinary post-acceptance identity and context
    with this prediction. Unsupported changes refuse this proof, never widen it.
    """
    if not conn.read_only_snapshot:
        raise ValueError("checkpoint projection requires a read-only snapshot")
    from . import derivations, identity_journal
    from .recipes import captured_recipe_inputs
    from .schema_catalog import columns

    target = {
        name: value
        for name, value in bundle.file_hashes.items()
        if not name.startswith(("runtime/", "recipes/"))
    }
    target.update(
        captured_recipe_inputs(bundle.files, history_start=bundle.config.history_start.isoformat())
    )
    target["policy/inventory_year"] = digest(canonical(clock.now().year))
    declared = json.loads(bundle.files["versions.json"])
    target.pop("versions.json", None)
    target.update({"version/" + name: value for name, value in declared.items()})
    source_inputs = dict(
        conn.execute("SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline'")
    )
    for name, value in source_inputs.items():
        if name not in target:
            target[name] = value if name.startswith("version/") else digest(b"")
    target.pop("recipe/env", None)
    changed = sorted(
        name
        for name in source_inputs.keys() | target.keys()
        if source_inputs.get(name) != target.get(name)
    )
    prefixes = (
        "recipe/code/",
        "version/extract/",
        "version/parser/",
        "extract_version:",
        "parser_version:",
    )
    allowed = {"recipe/env", "recipe/runtime", "version/repository"}
    if any(name not in allowed and not name.startswith(prefixes) for name in changed):
        raise UnsupportedCheckpointTransition(
            "checkpoint projection supports only code-input changes"
        )
    journal = bundle.files.get("overrides/identity_overrides.csv", b"")
    if identity_journal.token(conn).digest != digest(journal):
        raise UnsupportedCheckpointTransition(
            "checkpoint projection requires the current identity journal"
        )
    kinds = sorted(
        {
            name.removeprefix("recipe/code/extract/")
            if name.startswith("recipe/code/extract/")
            else name.rsplit(":" if ":" in name else "/", 1)[1]
            for name in changed
            if name.startswith(("recipe/code/extract/", "version/extract/", "extract_version:"))
        }
    )
    clear_all = "recipe/runtime" in changed
    overrides: dict[str, tuple[str, Any]] = {}
    if kinds or clear_all:
        predicates = []
        if clear_all:
            predicates.append(
                "NOT (source='crosscheck' AND kind='registry_dump' AND method='MANUAL' AND parser='registry_crosscheck')"
            )
        if kinds:
            predicates.append("(parser=ANY(%s) OR kind=ANY(%s))")
        predicate = "(" + " OR ".join(predicates) + ")"
        params = (kinds, kinds) if kinds else ()
        names = list(columns(conn, "watches"))
        projection = ",".join(
            f"CASE WHEN {predicate} THEN NULL ELSE extract_version END AS extract_version"
            if name == "extract_version"
            else '"' + name + '"'
            for name in names
        )
        overrides["watches"] = (
            "SELECT "
            + projection
            + ' FROM "watches" ORDER BY '
            + ",".join(str(i + 1) for i in range(len(names))),
            params,
        )
    existing = {
        tuple(row) for row in conn.execute("SELECT stage,unit_kind,unit_id FROM derivation_scopes")
    }
    known = {
        (unit.stage, unit.unit_kind, unit.unit_id)
        for stage in ("project", "link")
        for unit in derivations.known_units(conn, stage)
    }
    # refresh registers project first. Its new event scopes are visible to the
    # subsequent link selection even when no event output exists yet.
    known.update(
        ("link", "event", identifier)
        for stage, kind, identifier in list(known)
        if stage == "project" and kind == "event"
    )
    missing = sorted(known - existing)
    if missing:
        overrides["derivations"] = (
            "SELECT stage,unit_kind,unit_id,materialized_generation_id FROM derivation_scopes UNION ALL SELECT stage,unit_kind,unit_id,NULL::text FROM unnest(%s::text[],%s::text[],%s::text[]) AS added(stage,unit_kind,unit_id) ORDER BY 1 NULLS FIRST,2 NULLS FIRST,3 NULLS FIRST",
            tuple([row[i] for row in missing] for i in range(3)),
        )
    return {
        "format": "swingset-input-checkpoint-binding-v1",
        **_identities(conn, overrides),
        "inputs": target,
        "changed_inputs": changed,
        "watch_extract_kinds": kinds,
        "watch_extract_all_except_manual_crosscheck": clear_all,
        "registered_null_scopes": [list(row) for row in missing],
        "registered_null_scopes_sha256": digest(canonical(missing)),
    }
