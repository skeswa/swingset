"""Bounded indexed keyset pages for the retained requirement universe."""

from __future__ import annotations

import json
from typing import Any

from swingset.state import engine

_FINALIST = (
    "c.wsdc_points_eligible=1 AND c.division IN ('newcomer','novice') "
    "AND e.role IN ('leader','follower') AND "
    "(EXISTS(SELECT 1 FROM placements p WHERE p.leader_entry_id=e.entry_id) "
    "OR EXISTS(SELECT 1 FROM placements p WHERE p.follower_entry_id=e.entry_id))"
)

# Each scope has its own natural index order. The cursor encodes the original
# column values, so delimiters in source references cannot change pagination.
_SCOPES = (
    ("a", "finding", "finding_support", "active=1", ("finding_id",)),
    ("b", "round", "watches", "kind='round'", ("watch_id",)),
    ("c", "mapping", "source_events", "TRUE", ("source", "source_ref")),
    (
        "d",
        "source_id",
        "identity_links",
        "method='source_id' AND wsdc_id IS NOT NULL",
        ("wsdc_id",),
    ),
    ("e", "occurrence", "registry_placements", "TRUE", ("series_id", "event_month")),
    (
        "f",
        "finalist",
        "entries e JOIN contests c USING(contest_id)",
        _FINALIST,
        ("e.entry_id",),
    ),
    ("g", "artifact", "snapshots", "body_sha256 IS NOT NULL", ("body_sha256",)),
    ("h", "retirement", "findings", "owner_kind='requirement'", ("finding_id",)),
)


def scope_page(conn: engine.Connection, cursor: str, limit: int) -> list[dict[str, str]]:
    """Seek only scopes at or after the durable cursor; never sort their union."""
    prefix, values = (cursor[0], json.loads(cursor[2:])) if cursor else ("", [])
    result: list[dict[str, str]] = []
    for letter, scope, table, condition, columns in _SCOPES:
        if letter < prefix:
            continue
        parameters: list[Any] = []
        if letter == prefix:
            lhs = columns[0] if len(columns) == 1 else f"({','.join(columns)})"
            rhs = "%s" if len(columns) == 1 else f"({','.join('%s' for _ in columns)})"
            condition += f" AND {lhs}>{rhs}"
            parameters.extend(values)
        selected = ",".join(columns)
        query = f"SELECT {selected} FROM {table} WHERE {condition} GROUP BY {selected} ORDER BY {selected} LIMIT %s"
        parameters.append(limit - len(result))
        for row in conn.execute(query, parameters):
            keys = list(row)
            identifier = json.dumps(keys, separators=(",", ":")) if len(keys) > 1 else str(keys[0])
            result.append(
                {
                    "key": letter + ":" + json.dumps(keys, separators=(",", ":")),
                    "scope": scope,
                    "id": identifier,
                }
            )
        if len(result) == limit:
            break
    return result


_PRESENT = {
    "round_observations": "SELECT 1 AS _value_0 FROM watches WHERE watch_id = %s AND kind = 'round'",
    "source_event_mapping": "SELECT 1 AS _value_0 FROM source_events WHERE source = %s AND source_ref = %s",
    "source_id_checked": "SELECT 1 AS _value_0 FROM identity_links WHERE wsdc_id = %s AND method = 'source_id'",
    "registry_event_association": "SELECT 1 AS _value_0 FROM registry_placements WHERE series_id = %s AND event_month = %s",
    "first_point_reconsideration": "SELECT 1 AS _value_0 FROM entries AS e JOIN contests AS c USING (contest_id) WHERE e.entry_id = %s AND "
    + _FINALIST,
    "archive_artifact": "SELECT 1 AS _value_0 FROM snapshots WHERE body_sha256 = %s",
}


def scope_present(conn: engine.Connection, kind: str, subject_id: str) -> bool:
    query = _PRESENT.get(kind)
    if query is None:
        return True
    values = (
        json.loads(subject_id)
        if kind in {"source_event_mapping", "registry_event_association"}
        else (subject_id,)
    )
    return conn.execute(query + " LIMIT 1", values).fetchone() is not None
