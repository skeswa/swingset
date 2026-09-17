"""Check a complete dancer cohort using the same immutable currentness proof."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Sequence

from . import derivation_query
from .derivation_signatures import base_certificate, materialized_proof
from .recipes import recipe_inputs
from .work import WorkUnit


def all_dancers_current(conn: sqlite3.Connection) -> bool:
    """Check the database-defined shared link cohort once per owned snapshot.

    The key describes a database query, not a caller-supplied cohort. Guarded
    snapshot lifetime binds both membership and proofs, avoiding repeated
    construction and hashing of every dancer for each blocked link event.
    """
    from .derivation_dependencies import dancer_prerequisites
    from .derivations import current

    return derivation_query.currentness(
        conn,
        ("all_link_dancer_prerequisites", current),
        lambda: dancers_current(conn, dancer_prerequisites(conn), current=current),
    )


def dancers_current(
    conn: sqlite3.Connection,
    units: Sequence[WorkUnit],
    *,
    current: Callable[[sqlite3.Connection, WorkUnit], bool],
) -> bool:
    """Bulk fast certificates; missing or mismatched proof uses full current().

    The caller owns one read snapshot or a bounded write transaction. Only the
    ordinary database currentness callback can share a complete cohort answer
    within an owned read-only selection snapshot. Mutable worker transactions
    and custom callbacks always recompute; nothing survives the snapshot.
    Physical unregistered scopes remain in ``units`` and take the slow path.
    """
    if not conn.in_transaction:
        raise ValueError("dancer currentness requires one caller snapshot")
    if not units:
        return True
    if any(unit.stage != "project" or unit.unit_kind != "dancer" for unit in units):
        raise ValueError("only project/dancer prerequisites can use bulk currentness")
    from .derivations import current as ordinary_current

    cohort = tuple(units)
    if current is not ordinary_current:
        return _dancers_current(conn, cohort, current=current)
    return derivation_query.currentness(
        conn,
        ("dancer_cohort_readiness", cohort, current),
        lambda: _dancers_current(conn, cohort, current=current),
    )


def _dancers_current(
    conn: sqlite3.Connection,
    units: Sequence[WorkUnit],
    *,
    current: Callable[[sqlite3.Connection, WorkUnit], bool],
) -> bool:
    """Verify the full cohort from immutable certificates or ordinary fallback."""
    pointers = {
        str(row[0]): tuple(row[1:])
        for row in conn.execute(
            "SELECT s.unit_id,s.materialized_generation_id,s.materialized_signature,p.manifest_json "
            "FROM derivation_scopes s LEFT JOIN derivation_dependency_sets p "
            "ON p.dependency_set_id=s.materialized_signature "
            "WHERE s.stage='project' AND s.unit_kind='dancer'"
        )
    }
    raw_versions = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            "SELECT input_key,version FROM derivation_input_versions WHERE namespace='raw'"
        )
    }
    watches: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT DISTINCT o.scope_id,o.watch_id,coalesce(v.version,0) FROM observations o "
        "LEFT JOIN derivation_input_versions v ON v.namespace='watch' AND v.input_key=o.watch_id "
        "WHERE o.scope_kind='dancer' ORDER BY o.scope_id,o.watch_id"
    ):
        watches[str(row[0])].append((str(row[1]), int(row[2])))
    source = conn.execute(
        "SELECT version FROM derivation_input_versions WHERE namespace='watch_sources' AND input_key='all'"
    ).fetchone()
    source_version = int(source[0]) if source else 0
    recipe = recipe_inputs(conn, "project")
    for unit in units:
        pointer = pointers.get(unit.unit_id)
        if pointer is not None and pointer[0] is not None and pointer[1] is not None:
            raw_key = json.dumps(
                ("dancer", unit.unit_id), separators=(",", ":"), ensure_ascii=False
            )
            signature = base_certificate(
                unit,
                recipe,
                {},
                raw_version=raw_versions.get(raw_key, 0),
                watch_versions=watches[unit.unit_id],
                watch_sources_version=source_version,
            )
            proof, identifier = materialized_proof(pointer[0], signature)
            if pointer[1] == identifier and pointer[2] == proof:
                continue
        if not current(conn, unit):
            return False
    return True
