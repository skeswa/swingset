"""Cheap change certificates for exact immutable dependency manifests.

A certificate is recorded only after comparing the full selected manifest. SQL
triggers maintain input tokens in the input transaction. A changed certificate
forces the full comparison; neither queues nor cached desired hashes prove work.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from .work import WorkUnit


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _basis(unit: WorkUnit, recipe: Mapping[str, str], context: Mapping[str, Any]) -> list[Any]:
    return [
        "certificate-v1",
        (unit.stage, unit.unit_kind, unit.unit_id),
        dict(recipe),
        dict(context),
    ]


def _digest(basis: list[Any]) -> str:
    return hashlib.sha256(_json(basis).encode()).hexdigest()


def materialized_proof(generation: str, signature: str) -> tuple[str, str]:
    payload = _json({"generation_id": generation, "certificate": signature})
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def base_certificate(
    unit: WorkUnit,
    recipe: Mapping[str, str],
    context: Mapping[str, Any],
    *,
    raw_version: int,
    watch_versions: Iterable[tuple[str, int]],
    watch_sources_version: int,
) -> str:
    """Exact calendar/dancer certificate from a single retained input snapshot."""
    basis = _basis(unit, recipe, context)
    basis.append(("raw", _json((unit.unit_kind, unit.unit_id)), raw_version))
    basis.extend(("watch", watch_id, version) for watch_id, version in watch_versions)
    basis.append(("watch_sources", "all", watch_sources_version))
    return _digest(basis)


def certificate(
    conn: sqlite3.Connection, unit: WorkUnit, recipe: Mapping[str, str], context: Mapping[str, Any]
) -> str:
    from .derivations import selected_generation

    basis = _basis(unit, recipe, context)

    def token(namespace: str, key: str) -> None:
        row = conn.execute(
            "SELECT version FROM derivation_input_versions WHERE namespace=? AND input_key=?",
            (namespace, key),
        ).fetchone()
        basis.append((namespace, key, int(row[0]) if row else 0))

    def selected(kind: str, identifier: str = "all") -> None:
        basis.append(
            (
                "selected",
                kind,
                identifier,
                selected_generation(conn, WorkUnit("project", kind, identifier)),
            )
        )

    def epoch(kind: str, stage: str = "project") -> None:
        token("selected", _json((stage, kind)))

    def raw(kind: str, identifier: str) -> None:
        token("raw", _json((kind, identifier)))
        for row in conn.execute(
            "SELECT DISTINCT watch_id FROM observations WHERE scope_kind=? AND scope_id=? ORDER BY watch_id",
            (kind, identifier),
        ):
            token("watch", row[0])

    def event(identifier: str) -> None:
        raw("event", identifier)
        token("mapping", identifier)
        token("watch_sources", "all")
        for row in conn.execute(
            "SELECT DISTINCT source_ref FROM source_event_map WHERE event_id=? ORDER BY source_ref",
            (identifier,),
        ):
            raw("source_event", row[0])

    if unit.stage == "project":
        kind = unit.unit_kind
        if kind == "source_index":
            for name in ("index_raw", "snapshot_basis", "watch_sources", "source_selections"):
                token(name, "all")
            # Any accepted source selection can replace shared reference evidence.
            for row in conn.execute(
                "SELECT DISTINCT o.watch_id FROM observations o WHERE o.scope_kind='source_index' ORDER BY o.watch_id"
            ):
                token("watch", row[0])
        elif kind in {"calendar", "dancer"}:
            raw_row = conn.execute(
                "SELECT version FROM derivation_input_versions WHERE namespace='raw' AND input_key=?",
                (_json((kind, unit.unit_id)),),
            ).fetchone()
            source_row = conn.execute(
                "SELECT version FROM derivation_input_versions WHERE namespace='watch_sources' AND input_key='all'"
            ).fetchone()
            watches = (
                (str(row[0]), int(row[1]))
                for row in conn.execute(
                    "SELECT DISTINCT o.watch_id,coalesce(v.version,0) FROM observations o "
                    "LEFT JOIN derivation_input_versions v ON v.namespace='watch' AND v.input_key=o.watch_id "
                    "WHERE o.scope_kind=? AND o.scope_id=? ORDER BY o.watch_id",
                    (kind, unit.unit_id),
                )
            )
            return base_certificate(
                unit,
                recipe,
                context,
                raw_version=int(raw_row[0]) if raw_row else 0,
                watch_versions=watches,
                watch_sources_version=int(source_row[0]) if source_row else 0,
            )
        elif kind == "inventory":
            for name in (
                "calendar_raw",
                "inventory_registry",
                "inventory_sources",
                "snapshot_basis",
                "watch_sources",
                "source_selections",
            ):
                token(name, "all")
            for name in ("calendar", "source_index", "dancer"):
                epoch(name)
        elif kind == "map":
            selected("inventory")
        elif kind == "event":
            selected("map")
            event(unit.unit_id)
        elif kind == "source_event":
            selected("map")
            raw(kind, unit.unit_id)
            token("source_mapping", unit.unit_id)
            for row in conn.execute(
                "SELECT DISTINCT event_id FROM source_event_map WHERE source_ref=? ORDER BY event_id",
                (unit.unit_id,),
            ):
                selected("event", row[0])
        elif kind == "history":
            for name in ("history_acceptance", "history_findings", "snapshot_basis"):
                token(name, "all")
            for name in (
                "calendar",
                "source_index",
                "dancer",
                "inventory",
                "map",
                "event",
                "source_event",
            ):
                epoch(name)
    elif unit.stage == "link":
        token("reference_migrations", "all")
        selected("history")
        selected("event", unit.unit_id)
        epoch("dancer")
        event(unit.unit_id)
        token("canonical", unit.unit_id)
        for row in conn.execute(
            "SELECT round_id FROM rounds WHERE contest_id IN (SELECT contest_id FROM contests WHERE event_id=?) ORDER BY round_id",
            (unit.unit_id,),
        ):
            token("missing_identity", row[0])
        basis.extend(
            tuple(row)
            for row in conn.execute(
                "SELECT name,value FROM revisions WHERE name IN ('dancers','identity_decisions') ORDER BY name"
            )
        )
        basis.extend(
            tuple(row)
            for row in conn.execute(
                "SELECT key,value FROM meta WHERE key='identity_journal_digest'"
            )
        )
    else:
        # Build validity includes filesystem evidence, so it deliberately has no
        # fast certificate path. Return a stable value for receipt bookkeeping.
        basis.append("full-build-comparison-required")
    return _digest(basis)
