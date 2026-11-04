"""Backup row classes, retained references and rebuild-mutable anchor fields.

All retained fields are audited exactly before recovery. Rebuilding may advance
only the named anchor/status fields through the existing writers; immutable
historical evidence and every original key remain present and unchanged.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import postgres_schema as schema

Class = Literal["history", "rebuilt", "bookkeeping"]
# The same source contexts are reconstructed by postgres_rebuild. Legacy
# observations outside these contexts remain original evidence, not cache.
REHYDRATABLE_SOURCE_PREDICATE = "g.state='accepted' AND (g.removal_authority='none' OR g.generation_id=u.accepted_generation_id)"

_HISTORY = frozenset(
    """
hosts host_budget host_request_spacing host_request_spacing_baselines watches snapshots
archive_queries archive_captures cursors scheduler_requests scheduler_watch_state scheduler_parent_links
scheduler_event_policies scheduler_event_requests scheduler_event_turns scheduler_capacity_policies
scheduler_capacity_requests scheduler_capacity_service history_origin_intents history_origin_requests
history_origin_operator_refs history_dispatch_fence
source_event_enumerations source_event_enumeration_members source_event_member_watches source_event_inventory
event_enumeration_inputs source_event_retirement_proofs source_event_retirement_receipts
event_retirement_observations event_retirement_receipts event_accounting_receipts event_accounting_support_observations
event_progress_policies event_progress_receipts event_stage_operations event_blocker_policies
source_units admission_decisions admission_policies admission_reviews findings finding_support finding_support_references registry_verifications
identity_link_history identity_source_refs identity_reference_bindings identity_reference_migrations identity_decisions identity_journal_acceptances history_acceptance
runs meta control_state control_events operator_pauses execution_admissions execution_dependencies accepted_inputs
blob_classes storage_applies retention_applies backup_sets backup_objects backup_uploads
release_manifests
""".split()
)
_REBUILT = frozenset(
    """
observations source_generations derivation_scopes derivation_generations derivation_dependency_sets
derivation_payloads derivation_row_refs source_generation_packs label_body_packs derivation_evicted derivation_recipes
objects release_partitions release_artifacts
events contests rounds entries heats judges callback_marks callbacks final_marks placements dancers registry_placements
coverage series canonical_scope_rows source_events source_event_map source_event_scope_rows source_event_proposals
link_candidates identity_links identity_link_resolutions
""".split()
)
_BOOKKEEPING = frozenset(
    """
acquisition_plan event_gap_observations event_gap_revisions event_pressure_state event_pressure_subjects
event_pressure_watches event_pressure_parents event_pressure_hosts event_pressure_latches event_pressure_dirty
event_progress_observations event_progress_scans event_progress_cursor event_blocker_observations
 event_blocker_latest event_blocker_refreshes event_blocker_cursor event_timing event_timing_history event_timing_cursor
requirement_scan derivation_building derivation_equivalences source_equivalences derivation_input_versions derivation_reads
derivation_payload_removal_authority derivation_payload_removal_grant work_queue work_attempts work_generations pending_work
scheduler_offline_service loop_state guard_reasons revisions storage_eviction_authority storage_eviction_grant
""".split()
)

# A table may contain rows of more than one class. The initial history rule
# names irrecoverable rows; the reference closure can only add more rows.
_INITIAL_HISTORY = {
    "source_generations": "r.state<>'superseded'",
    "derivation_generations": "r.stage='build' OR (r.stage='project' AND (r.unit_kind IN ('history','map') OR (r.unit_kind='year' AND r.unit_id !~ '^[0-9]{4}$')))",
    "derivation_scopes": "r.materialized_generation_id IS NOT NULL OR r.stage='build' OR (r.stage='project' AND (r.unit_kind IN ('history','map') OR (r.unit_kind='year' AND r.unit_id !~ '^[0-9]{4}$')))",
    "revisions": "r.name='identity_decisions'",
    "objects": "r.class IN ('body','extract','input_bundle','parse_body','label_body','release_manifest')",
}

# These fields are operational anchors/status, never immutable evidence. The
# before-worker restore audit still checks their original bytes exactly.
MUTABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "accepted_inputs": ("digest",),
    "objects": ("stored_at",),
    "release_manifests": ("candidate_id",),
    "derivation_scopes": (
        "desired_fingerprint",
        "materialized_generation_id",
        "materialized_signature",
    ),
    "source_units": ("desired_fingerprint", "accepted_generation_id", "legacy_state"),
    "source_generations": ("state",),
    "snapshots": (
        "extract_status",
        "extract_sha256",
        "parse_status",
        "parsed_at",
        "extract_version",
        "parser_version",
        "extract_recipe_sha256",
    ),
    "watches": (
        "state",
        "next_check_at",
        "body_sha256",
        "etag",
        "last_modified",
        "fingerprint",
        "extract_version",
        "current_observation_snapshot_id",
    ),
    "runs": ("finished_at", "summary_json"),
    "control_state": ("revision",),
    "history_dispatch_fence": ("revision",),
    "execution_admissions": ("state", "settled_at", "outcome"),
    "work_attempts": (
        "finished_at",
        "outcome",
        "reason_code",
        "evidence_json",
        "retry_at",
        "requirement_id",
    ),
    "findings": (
        "closed_at",
        "closed_by",
        "state",
        "next_action",
        "blocking_reason",
        "next_eligible_at",
        "attempt_count",
        "last_progress_at",
        "status_at",
        "desired_fingerprint",
    ),
}
MUTABLE_META_KEYS = frozenset(
    {
        "input_bundle_hash",
        "audit_changed_scopes",
        "audit_verified_context",
        "audit_bootstrap_context",
    }
)

# JSON encodes retained generation/dependency/decision identities. Serialized
# projection records contain identity *values*, not references to the mutable
# live projection tables: their historical dependencies name generation labels.
# These target categories are immutable identities, so a token equal to an
# existing identity is retained conservatively even in nested encoded JSON.
JSON_ID_TARGETS = (
    ("snapshots", "snapshot_id"),
    ("watches", "watch_id"),
    ("runs", "run_id"),
    ("derivation_generations", "generation_id"),
    ("source_generations", "generation_id"),
    ("derivation_dependency_sets", "dependency_set_id"),
    ("derivation_recipes", "recipe_sha256"),
    ("derivation_payloads", "payload_sha256"),
    ("identity_source_refs", "ref_id"),
    ("identity_decisions", "decision_id"),
)
_JSON_COLUMNS = frozenset(
    {
        "manifest_json",
        "recipe_json",
        "result_json",
        "report_json",
        "payload_json",
        "evidence_json",
        "assertion_json",
        "resolution_json",
        "cohort_json",
        "parent_support_json",
        "support_json",
        "token_json",
        "summary_json",
        "ref_ids_json",
        "decision_ids_json",
        "proof_json",
        "facts_json",
        "detail_json",
        "planned_payloads_json",
        "planned_files_json",
        "receipt_json",
        "record_key",
    }
)


@dataclass(frozen=True)
class RowClass:
    kind: Class
    history_rule: str
    reset_rule: str


def classification(table: str) -> RowClass:
    if table == "environment_identity":
        return RowClass(
            "bookkeeping", "FALSE", "fresh destination identity; never copy the source identity"
        )
    if table == "cursors":
        return RowClass(
            "history",
            "name <> 'event_pressure_inventory_after'",
            "preserve acquisition cursors; reset the pressure inventory scan position",
        )
    if table == "accepted_inputs":
        return RowClass(
            "history",
            "r.consumer='pipeline' AND r.input_name<>'recipe/env'",
            "preserve exact semantic input digests and absence tombstones; reset audit inputs and the excluded recipe environment",
        )
    if table in _HISTORY:
        return RowClass("history", "TRUE", "preserve every restored row and field")
    if table == "observations":
        return RowClass(
            "rebuilt",
            "NOT EXISTS(SELECT 1 FROM evidence.source_generations g JOIN evidence.source_units u USING(unit_key) WHERE "
            + REHYDRATABLE_SOURCE_PREDICATE
            + " AND u.watch_id=r.watch_id AND g.recipe_json::jsonb #>> '{context,snapshot_id}'=r.snapshot_id)",
            "preserve exact legacy observations without an accepted immutable counterpart; regenerate approved-backed contexts only",
        )
    if table in _REBUILT:
        return RowClass(
            "rebuilt",
            _INITIAL_HISTORY.get(table, "FALSE"),
            "empty except transitively retained history; regenerate by the real pipeline",
        )
    if table == "source_equivalences":
        return RowClass(
            "bookkeeping",
            "EXISTS(SELECT 1 FROM evidence.source_generations retained_source WHERE retained_source.generation_id=r.generation_id AND retained_source.state<>'superseded')",
            "preserve fingerprint mappings owned by retained source generations; reset unrelated equivalences",
        )
    if table == "pending_work":
        return RowClass(
            "bookkeeping",
            "stage='parse' AND unit_kind='snapshot' AND EXISTS(SELECT 1 FROM \"fetch\".snapshots pending_snapshot WHERE pending_snapshot.snapshot_id=unit_id AND pending_snapshot.body_sha256 IS NOT NULL)",
            "preserve identity-bearing pending parse tokens; reset all other work hints",
        )
    if table == "event_pressure_state":
        return RowClass(
            "bookkeeping",
            "FALSE",
            "preserve source epoch floor in manifest, reset sequence to zero; activation advances epoch",
        )
    if table == "event_pressure_dirty":
        return RowClass(
            "bookkeeping",
            "FALSE",
            "seed every retained watch; ordinary pressure bootstrap reconstructs subjects before admission",
        )
    if table in _BOOKKEEPING:
        return RowClass(
            "bookkeeping",
            _INITIAL_HISTORY.get(table, "FALSE"),
            "empty except transitively retained history; restore schema-37 singleton/counter defaults",
        )
    raise RuntimeError(f"table {table} has no backup class")


def checksum() -> str:
    rules = {
        table: {
            "class": classification(table).kind,
            "history": classification(table).history_rule,
            "reset": classification(table).reset_rule,
        }
        for table in (*schema.reference_manifest()["tables"], "environment_identity")
    }
    return hashlib.sha256(
        json.dumps(
            {
                "rules": rules,
                "mutable": MUTABLE_FIELDS,
                "mutable_meta_keys": sorted(MUTABLE_META_KEYS),
                "json_targets": JSON_ID_TARGETS,
                "json_columns": sorted(_JSON_COLUMNS),
                "ownership": _OWNERSHIP,
                "soft_references": _SOFT,
                "context_ids": CONTEXT_IDS,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def validate_catalog(conn: Any | None = None) -> None:
    reference = set(schema.reference_manifest()["tables"])
    assigned = _HISTORY | _REBUILT | _BOOKKEEPING
    if (
        reference != assigned
        or (_HISTORY & _REBUILT)
        or (_HISTORY & _BOOKKEEPING)
        or (_REBUILT & _BOOKKEEPING)
    ):
        raise RuntimeError(
            f"backup class list differs from schema: missing={sorted(reference - assigned)}, extra={sorted(assigned - reference)}"
        )
    if conn is not None:
        actual = {
            str(row[0])
            for row in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname=ANY(%s)",
                (sorted(set(schema.TABLE_NAMESPACES.values())),),
            )
        }
        if actual != reference | {"environment_identity"}:
            raise RuntimeError("a live table has no backup class or reset rule")


def keys(table: str) -> tuple[str, ...]:
    columns = schema.reference_manifest()["tables"][table]["columns"]
    return tuple(c["name"] for c in sorted(columns, key=lambda c: c["pk"]) if c["pk"])


def marker(table: str) -> str:
    # Table names are from the validated, closed class catalog.
    return 'pg_temp."_swingset_history_' + table + '"'


def _join(left: str, right: str, columns: tuple[str, ...]) -> str:
    return " AND ".join(f'{left}."{col}"={right}."{col}"' for col in columns)


def where(table: str, alias: str | None = None) -> str:
    target = alias or '"' + table + '"'
    return f"EXISTS(SELECT 1 FROM {marker(table)} history_key WHERE {_join('history_key', target, keys(table))})"


def prepare(conn: Any) -> None:
    """Create session-only markers before the caller pins a read-only snapshot."""
    validate_catalog(conn)
    for table, specification in schema.reference_manifest()["tables"].items():
        columns = {c["name"]: c for c in specification["columns"]}
        types = {"TEXT": "text", "INTEGER": "bigint", "REAL": "double precision"}
        definition = ",".join(
            f'"{col}" {types[columns[col]["type"]]} NOT NULL' for col in keys(table)
        )
        key_sql = ",".join(f'"{col}"' for col in keys(table))
        conn.execute(
            f'CREATE TEMP TABLE IF NOT EXISTS "_swingset_history_{table}"({definition},expanded boolean NOT NULL DEFAULT FALSE,PRIMARY KEY({key_sql})) ON COMMIT PRESERVE ROWS'
        )
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _swingset_history_tokens(token text PRIMARY KEY) ON COMMIT PRESERVE ROWS"
    )


def _insert(conn: Any, table: str, select: str) -> int:
    names = ",".join(f'"{name}"' for name in keys(table))
    return max(
        0,
        int(
            conn.execute(
                f"INSERT INTO {marker(table)}({names}) {select} ON CONFLICT DO NOTHING"
            ).rowcount
        ),
    )


def _foreign_edges(conn: Any) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    edges = []
    for source, target, local, remote in conn.execute(
        "SELECT s.relname AS source_table,t.relname AS target_table,ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY x(attnum,ord) JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.attnum ORDER BY x.ord) AS local_cols,ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY x(attnum,ord) JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.attnum ORDER BY x.ord) AS foreign_cols FROM pg_constraint k JOIN pg_class s ON s.oid=k.conrelid JOIN pg_class t ON t.oid=k.confrelid JOIN pg_namespace n ON n.oid=s.relnamespace WHERE k.contype='f' AND n.nspname=ANY(%s)",
        (sorted(set(schema.TABLE_NAMESPACES.values())),),
    ):
        edges.append((str(source), str(target), tuple(local), tuple(remote)))
    return edges


# Owning a generation also owns its immutable output/body rows. These are
# reverse ownership edges, not inferred from a table's name or FK direction.
_OWNERSHIP = (
    ("source_generations", "source_equivalences", ("generation_id",), ("generation_id",)),
    ("derivation_generations", "derivation_row_refs", ("generation_id",), ("generation_id",)),
    ("derivation_generations", "label_body_packs", ("generation_id",), ("generation_id",)),
    ("derivation_generations", "derivation_evicted", ("generation_id",), ("generation_id",)),
    ("source_generations", "source_generation_packs", ("generation_id",), ("generation_id",)),
    ("source_generations", "admission_decisions", ("generation_id",), ("generation_id",)),
    ("derivation_row_refs", "derivation_payloads", ("payload_sha256",), ("payload_sha256",)),
)


# Soft relational references use the same durable IDs as their declared FK
# counterparts. Contextual public IDs/selector values are documented below;
# they are not pointers to mutable current projection rows.
_SOFT = (
    ("observations", "snapshots", ("snapshot_id",), ("snapshot_id",)),
    ("observations", "watches", ("watch_id",), ("watch_id",)),
    ("pending_work", "snapshots", ("unit_id",), ("snapshot_id",)),
    ("archive_captures", "archive_queries", ("cdx_query_id",), ("query_id",)),
    ("admission_policies", "admission_reviews", ("reviewed_report_digest",), ("report_digest",)),
    ("finding_support_references", "derivation_generations", ("sha256",), ("generation_id",)),
    (
        "event_accounting_receipts",
        "source_event_enumerations",
        ("enumeration_id",),
        ("enumeration_id",),
    ),
    ("event_progress_receipts", "execution_admissions", ("request_id",), ("action_id",)),
    ("event_stage_operations", "execution_admissions", ("request_id",), ("action_id",)),
    ("event_stage_operations", "watches", ("watch_id",), ("watch_id",)),
    ("event_stage_operations", "snapshots", ("snapshot_id",), ("snapshot_id",)),
    ("event_stage_operations", "source_generations", ("generation_id",), ("generation_id",)),
    ("event_stage_operations", "admission_decisions", ("decision_id",), ("decision_id",)),
    ("identity_reference_bindings", "snapshots", ("snapshot_id",), ("snapshot_id",)),
    ("watches", "snapshots", ("created_by_snapshot_id",), ("snapshot_id",)),
    ("watches", "snapshots", ("current_observation_snapshot_id",), ("snapshot_id",)),
) + tuple(
    (source, target, (local,), (remote,))
    for source in ("event_retirement_receipts", "source_event_retirement_receipts")
    for local, target, remote in (
        ("enumeration_id", "source_event_enumerations", "enumeration_id"),
        ("predecessor_id", "source_event_enumerations", "enumeration_id"),
        ("generation_id", "source_generations", "generation_id"),
        ("decision_id", "admission_decisions", "decision_id"),
    )
)

CONTEXT_IDS = {
    "control_events": {
        "pause_id": "historical pause identity survives deletion of its current pause row",
        "scope_id": "selector value; source/kind/host/all scopes are not row references",
    },
    "operator_pauses": {
        "pause_id": "identity owned by this current pause, not an outgoing reference"
    },
    "execution_admissions": {
        "candidate_id": "publication identity; only active/baseline metadata names required local files"
    },
    "release_manifests": {
        "candidate_id": "publication identity; old published candidates are recoverable from the Hub"
    },
    "findings": {
        "owner_id": "context selector; durable dependencies are declared in finding_support_references",
        "subject_id": "context selector; durable dependencies are declared in finding_support_references",
    },
    "history_origin_intents": {
        "event_id": "public event identity, not a pointer to mutable live projection data"
    },
    "history_origin_requests": {
        "event_id": "public event identity, not a pointer to mutable live projection data"
    },
    "identity_decisions": {"wsdc_id": "external WSDC registry identity"},
    "identity_link_history": {
        "subject_id": "historical public subject identity; stable origin is retained by identity_source_refs"
    },
    "identity_reference_bindings": {
        "subject_id": "public subject identity; historical locator is the retained ref_id",
        "event_id": "public event identity; historical locator is the retained ref_id",
    },
    "host_request_spacing": {"reservation_id": "identity owned by this reservation row"},
    "host_request_spacing_baselines": {
        "prior_reservation_id": "prior reservation continuity token, whose expired current row need not exist"
    },
}


def immutable_fields(table: str, row: Mapping[str, Any]) -> tuple[str, ...]:
    ignored = set(MUTABLE_FIELDS.get(table, ()))
    if table == "meta" and row.get("key") in MUTABLE_META_KEYS:
        ignored.add("value")
    if table == "revisions" and row.get("name") != "identity_decisions":
        ignored.add("value")
    return tuple(name for name in schema.semantic_columns(table) if name not in ignored)


def reference_list(conn: Any) -> list[dict[str, Any]]:
    return [
        {"source": source, "target": target, "local": local, "remote": remote, "kind": kind}
        for kind, edges in (
            ("foreign_key", _foreign_edges(conn)),
            ("ownership", list(_OWNERSHIP)),
            ("soft_reference", list(_SOFT)),
        )
        for source, target, local, remote in edges
    ] + [
        {
            "source": "structured history JSON and retained publication/hold metadata",
            "target": table,
            "column": column,
            "kind": "json_identity",
        }
        for table, column in JSON_ID_TARGETS
    ]


def _token_blocks(blocks: Iterable[bytes]) -> Iterator[list[str]]:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    tail = ""
    for block in blocks:
        text = tail + decoder.decode(block)
        tokens = re.findall(r"[\w:/.-]{1,256}", text)
        if text and (text[-1].isalnum() or text[-1] in "_:/.-"):
            tail = tokens.pop() if tokens else text[-256:]
        else:
            tail = ""
        if tokens:
            yield list(set(tokens))
    final = tail + decoder.decode(b"", final=True)
    if final:
        yield list(set(re.findall(r"[\w:/.-]{1,256}", final)))


def _tokens(conn: Any, blocks: Iterable[bytes]) -> None:
    for batch in _token_blocks(blocks):
        conn.execute(
            "INSERT INTO pg_temp._swingset_history_tokens(token) SELECT unnest(%s::text[]) ON CONFLICT DO NOTHING",
            (batch,),
        )


def _json_rows(conn: Any, table: str, *, after_rebuild: bool = False) -> None:
    from .postgres_import import _postgres_text

    columns = [
        column
        for column in schema.semantic_columns(table)
        if (column in _JSON_COLUMNS or column.endswith("_json"))
        and not (after_rebuild and column in MUTABLE_FIELDS.get(table, ()))
    ]
    if not columns:
        conn.execute(f"UPDATE {marker(table)} SET expanded=TRUE WHERE NOT expanded")
        return
    columns_sql = ",".join(f'r."{name}"' for name in keys(table))
    with conn.cursor(name="history_json_" + table[:30]) as cursor:
        cursor.execute(
            f"SELECT {columns_sql} FROM {schema.qualified(table)} r JOIN {marker(table)} history_key ON {_join('r', 'history_key', keys(table))} WHERE NOT history_key.expanded"
        )
        for key in cursor:
            for column in columns:
                _tokens(conn, _postgres_text(conn, table, column, tuple(key)))
    conn.execute(f"UPDATE {marker(table)} SET expanded=TRUE WHERE NOT expanded")


def _edge_select(source: str, target: str, local: tuple[str, ...], remote: tuple[str, ...]) -> str:
    select = ",".join(f't."{name}"' for name in keys(target))
    relation = " AND ".join(f's."{a}"=t."{b}"' for a, b in zip(local, remote, strict=True))
    return f"SELECT DISTINCT {select} FROM {schema.qualified(source)} s JOIN {marker(source)} history_key ON {_join('s', 'history_key', keys(source))} JOIN {schema.qualified(target)} t ON {relation}"


def select_history(conn: Any, *, files: Mapping[str, Path] | None = None) -> dict[str, int]:
    """Close history transitively inside the caller's one pinned snapshot."""
    validate_catalog(conn)
    for table in schema.reference_manifest()["tables"]:
        conn.execute(f"DELETE FROM {marker(table)}")
        rule = classification(table).history_rule
        if rule != "FALSE":
            columns = ",".join(f'r."{name}"' for name in keys(table))
            _insert(conn, table, f"SELECT {columns} FROM {schema.qualified(table)} r WHERE {rule}")
    conn.execute("DELETE FROM pg_temp._swingset_history_tokens")
    for name, path in (files or {}).items():
        if (
            name.startswith(
                (
                    "holds/",
                    "candidates/",
                    "partitions/manifests/",
                    "objects/label_body/",
                    "objects/release_manifest/",
                )
            )
            or name.endswith("/sidecar.json")
        ) and (name.endswith(".json") or "/objects/" in "/" + name):
            import gzip

            opener = gzip.open if name.startswith("objects/label_body/") else open
            with opener(path, "rb") as stream:
                _tokens(conn, iter(lambda: stream.read(1 << 16), b""))
    edges = [*_foreign_edges(conn), *_OWNERSHIP, *_SOFT]
    while True:
        added = 0
        for source, target, local, remote in edges:
            added += _insert(conn, target, _edge_select(source, target, local, remote))
        for table in schema.reference_manifest()["tables"]:
            _json_rows(conn, table)
        for table, column in JSON_ID_TARGETS:
            added += _insert(
                conn,
                table,
                f'SELECT t."{column}" FROM {schema.qualified(table)} t JOIN pg_temp._swingset_history_tokens token ON token.token=t."{column}"',
            )
        for pack_table, kind in (
            ("source_generation_packs", "parse_body"),
            ("label_body_packs", "label_body"),
        ):
            added += _insert(
                conn,
                "objects",
                f"SELECT o.class,o.digest FROM ops.objects o JOIN {schema.qualified(pack_table)} p ON p.pack_digest=o.digest JOIN {marker(pack_table)} h USING(generation_id) WHERE o.class='{kind}'",
            )
        added += _insert(
            conn,
            "objects",
            f"SELECT o.class,o.digest FROM ops.objects o JOIN derive.derivation_generations g ON g.output_digest=o.digest JOIN {marker('derivation_evicted')} h USING(generation_id) WHERE o.class='derivation_output'",
        )
        if added == 0:
            break
    assert_closed(conn)
    return {
        table: int(conn.execute(f"SELECT count(*) FROM {marker(table)}").fetchone()[0])
        for table in schema.reference_manifest()["tables"]
    }


def assert_closed(conn: Any, *, after_rebuild: bool = False) -> None:
    """An independent anti-join fails if any declared reference leaves history."""
    for source, target, local, remote in [*_foreign_edges(conn), *_OWNERSHIP, *_SOFT]:
        if after_rebuild and (source, target) == ("source_generations", "source_equivalences"):
            # Re-key appends verified aliases to retained generations. Original
            # aliases remain key/hash audited; their forward references stay closed.
            continue
        if after_rebuild and set(local).issubset(MUTABLE_FIELDS.get(source, ())):
            continue  # Existing writers may advance this anchor to a new live row.
        query = _edge_select(source, target, local, remote)
        columns = keys(target)
        match = " AND ".join(f'needed."{name}"=kept."{name}"' for name in columns)
        if (
            conn.execute(
                f"SELECT 1 FROM ({query}) needed WHERE NOT EXISTS(SELECT 1 FROM {marker(target)} kept WHERE {match}) LIMIT 1"
            ).fetchone()
            is not None
        ):
            raise RuntimeError(f"history reference leaves the backup: {source} -> {target}")
    for table, column in JSON_ID_TARGETS:
        if (
            conn.execute(
                f'SELECT 1 FROM {schema.qualified(table)} t JOIN pg_temp._swingset_history_tokens token ON token.token=t."{column}" WHERE NOT {where(table, "t")} LIMIT 1'
            ).fetchone()
            is not None
        ):
            raise RuntimeError(f"JSON identity reference leaves history: {table}")
