"""Re-admit observations whose labels lag their accepted generation (D-0241).

Release support matches each row's observation, labels included, against an
accepted source generation (`build/partition_checks`). Before D-0241 an
admitted parse whose payload was unchanged kept the old snapshot, extract and
parser labels, and a later parse that repeats the accepted generation is held
again without writing anything (D-0212). Those observations never match an
accepted generation again, so the release can use them only where a row
equals the published baseline. On 2026-10-14 that was 479 units: 309
`scoringdance.event`, 103 `eepro.round` and 67 `eepro.autoindex`.

`drift` compares each source unit's stored observations with its accepted
generation's result. A unit is `labels` when every row is equal in kind,
scope, sequence and payload but some snapshot, extract or parser label
differs, and the watch's current snapshot is the generation's. `relabel`
stores that generation's result again through `store_parse_result`, the writer
admission uses, one unit per transaction. That rewrites the rows under the
generation's labels and queues their project scopes, which derive drains like
any other work. A unit whose payloads differ (`payload`), or whose watch is
owned by another snapshot (`not_current`), is reported and left alone: only a
parse may change what a page says.

This module is an operator command, outside every recipe scope
(`recipe_scope.EXCLUDED`), so shipping it re-runs nothing.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from swingset.model.observations import encode_payload
from swingset.sources.base import ParseContext, ParseResult
from swingset.state import body_packs
from swingset.state.db import Database
from swingset.state.observations import store_parse_result

from .generations import deserialize_result

#: What `drift` calls a unit.
CURRENT, LABELS, PAYLOAD, NOT_CURRENT = "current", "labels", "payload", "not_current"


@dataclass(frozen=True)
class Drift:
    """One source unit's stored observations against its accepted generation."""

    unit_key: str
    page_kind: str
    watch_id: str
    generation_id: str
    state: str
    #: The labels `(snapshot, extract, parser)` the stored rows carry.
    stored: tuple[tuple[str, str, str], ...]
    #: The accepted generation's labels.
    accepted: tuple[str, str, str]


def _units(conn: sqlite3.Connection, kinds: Iterable[str] | None) -> Iterator[dict[str, Any]]:
    wanted = sorted(set(kinds or ()))
    query = (
        "SELECT u.unit_key,u.page_kind,u.watch_id,g.generation_id,g.recipe_json,"
        "g.removal_authority,w.current_observation_snapshot_id "
        "FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id "
        "JOIN watches w ON w.watch_id=u.watch_id WHERE g.state='accepted'"
    )
    if wanted:
        query += f" AND u.page_kind IN ({','.join('?' for _ in wanted)})"
    query += " ORDER BY u.page_kind,u.unit_key"
    names = (
        "unit_key",
        "page_kind",
        "watch_id",
        "generation_id",
        "recipe_json",
        "removal_authority",
        "current_observation_snapshot_id",
    )
    for values in conn.execute(query, wanted).fetchall():
        yield dict(zip(names, values, strict=True))


def _result(conn: sqlite3.Connection, generation_id: str) -> ParseResult:
    body = body_packs.source_body(conn, generation_id)
    if body is None:
        raise ValueError(f"source generation {generation_id} has no row")
    return deserialize_result(body.result_json)


def _classify(conn: sqlite3.Connection, row: Mapping[str, Any]) -> tuple[Drift, Any]:
    recipe = json.loads(str(row["recipe_json"]))
    snapshot = str(recipe["context"]["snapshot_id"])
    accepted = (snapshot, str(recipe["extract_version"]), str(recipe["parser_version"]))
    # A unit that keeps history owns only its own snapshot's rows, as
    # `store_parse_result` compares them (`preserve_history`).
    by_snapshot = row["removal_authority"] == "none"
    stored = conn.execute(
        "SELECT kind,scope_kind,scope_id,seq,payload_json,snapshot_id,"
        "CAST(extract_version AS TEXT),CAST(parser_version AS TEXT) FROM observations "
        "WHERE watch_id=?" + (" AND snapshot_id=?" if by_snapshot else "") + " ORDER BY seq",
        (row["watch_id"], snapshot) if by_snapshot else (row["watch_id"],),
    ).fetchall()
    labels = tuple(sorted({(str(item[5]), str(item[6]), str(item[7])) for item in stored}))
    result = _result(conn, str(row["generation_id"]))
    expected = [
        (
            observation.kind,
            observation.scope.kind,
            observation.scope.ref,
            seq,
            encode_payload(observation.payload),
        )
        for seq, observation in enumerate(result.observations)
    ]
    same = [tuple(item[:5]) for item in stored] == expected
    if same and labels in {(accepted,), ()} and (stored or not expected):
        state = CURRENT
    elif not same or not stored:
        state = PAYLOAD
    elif row["current_observation_snapshot_id"] != snapshot:
        state = NOT_CURRENT
    else:
        state = LABELS
    drift = Drift(
        str(row["unit_key"]),
        str(row["page_kind"]),
        str(row["watch_id"]),
        str(row["generation_id"]),
        state,
        labels,
        accepted,
    )
    return drift, (recipe, result, by_snapshot)


def drift(conn: sqlite3.Connection, *, kinds: Iterable[str] | None = None) -> list[Drift]:
    """Every source unit with an accepted generation, classified (read only)."""
    return [_classify(conn, row)[0] for row in _units(conn, kinds)]


def report(drifts: Iterable[Drift]) -> dict[str, Any]:
    """Counts by page kind and state, and the units `relabel` would rewrite."""
    counts: Counter[tuple[str, str]] = Counter()
    units: list[dict[str, Any]] = []
    for item in drifts:
        counts[(item.page_kind, item.state)] += 1
        if item.state != CURRENT:
            units.append(
                {
                    "unit_key": item.unit_key,
                    "page_kind": item.page_kind,
                    "state": item.state,
                    "generation_id": item.generation_id,
                    "stored": [list(label) for label in item.stored],
                    "accepted": list(item.accepted),
                }
            )
    by_kind: dict[str, dict[str, int]] = {}
    for (kind, state), count in sorted(counts.items()):
        by_kind.setdefault(kind, {})[state] = count
    return {
        "kinds": by_kind,
        "relabel": sum(count for (_, state), count in counts.items() if state == LABELS),
        "units": units,
    }


def relabel(
    database: Database,
    *,
    now: str,
    run_id: str,
    kinds: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Store each `labels` unit's accepted result again; returns counts by kind.

    Each unit is re-checked and rewritten in its own transaction, so a unit a
    parse moved meanwhile is left alone and an interruption loses nothing.
    """
    conn = database.connection
    rewritten: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    for row in _units(conn, kinds):
        with database.transaction() as tx:
            current = tx.execute(
                "SELECT g.recipe_json,g.removal_authority,w.current_observation_snapshot_id "
                "FROM source_units u JOIN source_generations g "
                "ON g.generation_id=u.accepted_generation_id JOIN watches w ON w.watch_id=u.watch_id "
                "WHERE u.unit_key=? AND g.generation_id=? AND g.state='accepted'",
                (row["unit_key"], row["generation_id"]),
            ).fetchone()
            if current is None:
                skipped["moved"] += 1
                continue
            fresh = {
                **row,
                "recipe_json": current[0],
                "removal_authority": current[1],
                "current_observation_snapshot_id": current[2],
            }
            item, (recipe, result, by_snapshot) = _classify(tx, fresh)
            if item.state != LABELS:
                if item.state != CURRENT:
                    skipped[item.state] += 1
                continue
            changed = store_parse_result(
                tx,
                ParseContext(**recipe["context"]),
                result,
                extract_version=recipe["extract_version"],
                parser_version=recipe["parser_version"],
                parsed_at=now,
                run_id=run_id,
                # An accepted generation passed its contract, as admission
                # stores it (`admit_generation`).
                legitimate_empty=True,
                preserve_history=by_snapshot,
            )
            if changed:
                rewritten[item.page_kind] += 1
            else:
                skipped["unchanged"] += 1
    return {
        "rewritten": dict(sorted(rewritten.items())),
        "total": sum(rewritten.values()),
        "skipped": dict(sorted(skipped.items())),
    }
