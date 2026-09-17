"""Durable event turns within the host and class already chosen by fairness.

Each selected fetch may finish its bounded redirect/retry/robots chain. Every
issued request consumes the selected turn, including requests to another host;
the existing request ledger separately charges that actual host exactly once.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from swingset.config import Config

    from .fairness import WatchChoice

# Four normal hops, each with four attempts and a robots chain of at most
# four hops with four attempts. FetchClient.fetch adds no outer retry loop.
FETCH_CHAIN_REQUEST_LIMIT = 80


@dataclass(frozen=True)
class Selection:
    owner_key: str
    source: str | None
    source_ref: str | None
    enumeration_id: str | None
    position: int
    policy_digest: str
    reason: str


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='scheduler_event_turns'").fetchone()
        is not None
    )


def _owners(
    conn: sqlite3.Connection, choices: Sequence[WatchChoice]
) -> dict[str, list[dict[str, Any]]]:
    from .event_enumerations import memberships

    members = memberships(conn, (choice.key for choice in choices))
    result: dict[str, list[dict[str, Any]]] = {}
    for choice in choices:
        owners = {}
        for member in members.get(choice.key, []):
            key = json.dumps(
                ["event", member["source"], member["source_ref"]], separators=(",", ":")
            )
            owners[key] = {"owner_key": key, **member}
        # Unenumerated discovery and other watches participate too. Otherwise a
        # continuous stream of index requests could bypass the event rotation.
        result[choice.key] = list(owners.values()) or [
            {
                "owner_key": json.dumps(["watch", choice.key], separators=(",", ":")),
                "source": None,
                "source_ref": None,
                "enumeration_id": None,
            }
        ]
    return result


def prepare(
    conn: sqlite3.Connection, config: Config, choices: Sequence[WatchChoice], *, now: datetime
) -> None:
    """Enroll eligible demand at the tail; preserve skipped owners and usage."""
    if not available(conn):
        return
    if not conn.in_transaction:
        raise RuntimeError("event turn enrollment requires a transaction")
    body = json.dumps(
        {
            "format": "event-turn-policy-v1",
            "scheduler": asdict(config.scheduler),
            "fetch_chain_request_limit": FETCH_CHAIN_REQUEST_LIMIT,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(body.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO scheduler_event_policies VALUES (?,?)", (digest, body))
    # Rotate exhausted turns before enrolling arrivals. A restarted worker
    # cannot refund the previous fetch chain or put new work ahead of waiters.
    for row in conn.execute(
        "SELECT * FROM scheduler_event_turns WHERE used>=target_requests ORDER BY host,category,position"
    ).fetchall():
        position = conn.execute(
            "SELECT coalesce(max(position),0)+1 FROM scheduler_event_turns WHERE host=? AND category=?",
            (row["host"], row["category"]),
        ).fetchone()[0]
        conn.execute(
            "UPDATE scheduler_event_turns SET position=?,used=0,policy_digest=?,target_requests=? WHERE host=? AND category=? AND owner_key=?",
            (
                position,
                digest,
                config.scheduler.event_turn_requests,
                row["host"],
                row["category"],
                row["owner_key"],
            ),
        )
    owners = _owners(conn, choices)
    for choice in choices:
        for owner in owners[choice.key]:
            conn.execute(
                "INSERT OR IGNORE INTO scheduler_event_turns(host,category,owner_key,source,source_ref,position,policy_digest,target_requests,enrolled_at) "
                "VALUES (?,?,?,?,?,(SELECT coalesce(max(position),0)+1 FROM scheduler_event_turns WHERE host=? AND category=?),?,?,?)",
                (
                    choice.host,
                    choice.category,
                    owner["owner_key"],
                    owner["source"],
                    owner["source_ref"],
                    choice.host,
                    choice.category,
                    digest,
                    config.scheduler.event_turn_requests,
                    now.isoformat(),
                ),
            )


def choose(conn: sqlite3.Connection, choices: Sequence[WatchChoice]) -> WatchChoice:
    """Select an enrolled owner without changing its place or claiming service."""
    if not choices:
        raise ValueError("event rotation needs eligible choices")
    if not available(conn):
        return choices[0]
    owners = _owners(conn, choices)
    selected = []
    for choice in choices:
        for owner in owners[choice.key]:
            row = conn.execute(
                "SELECT * FROM scheduler_event_turns WHERE host=? AND category=? AND owner_key=?",
                (choice.host, choice.category, owner["owner_key"]),
            ).fetchone()
            if row is None:
                continue
            selection = Selection(
                owner["owner_key"],
                owner["source"],
                owner["source_ref"],
                owner["enumeration_id"],
                row["position"],
                row["policy_digest"],
                "continue_turn" if row["used"] else "queue_head",
            )
            selected.append(
                (row["position"], choice.due_at, choice.key, replace(choice, turn=selection))
            )
    # Callers without runner preparation retain the old selection contract.
    return min(selected, key=lambda item: item[:3])[3] if selected else choices[0]


def record(conn: sqlite3.Connection, choice: WatchChoice, *, action_id: str, now: datetime) -> None:
    """Charge one issued request atomically with its existing host debit."""
    selection = choice.turn
    if selection is None:
        return
    if not conn.in_transaction:
        raise RuntimeError("event usage requires the host debit transaction")
    changed = conn.execute(
        "UPDATE scheduler_event_turns SET used=used+1,last_issued_at=? WHERE host=? AND category=? AND owner_key=? AND position=? AND policy_digest=?",
        (
            now.isoformat(),
            choice.host,
            choice.category,
            selection.owner_key,
            selection.position,
            selection.policy_digest,
        ),
    )
    if changed.rowcount != 1:
        raise ValueError("selected event turn changed before request issuance")
    conn.execute(
        "INSERT INTO scheduler_event_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            action_id,
            choice.host,
            choice.category,
            selection.owner_key,
            selection.source,
            selection.source_ref,
            selection.enumeration_id,
            selection.position,
            selection.policy_digest,
            selection.reason,
        ),
    )
