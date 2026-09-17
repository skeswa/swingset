"""Protect listed result requests inside the existing new-work allowance."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import TYPE_CHECKING

from .event_enumerations import memberships
from .event_request_kind import purpose as source_event_purpose
from .event_turns import FETCH_CHAIN_REQUEST_LIMIT
from .event_turns import choose as choose_event

if TYPE_CHECKING:
    from swingset.config import Config

    from .fairness import WatchChoice


@dataclass(frozen=True)
class Selection:
    lane: str
    purpose: str
    competing: bool
    listed_page_percent: int
    policy_digest: str
    policy_json: str


def purpose(parser: str | None, *, listed: bool) -> str:
    """Watch kind and membership alone do not distinguish results from indexes."""
    kind = source_event_purpose(parser or "")
    if kind == "result":
        return "listed_result" if listed else "unlisted_result"
    if kind == "event_index":
        return "event_index"
    if kind == "discovery":
        return "essential_discovery"
    return "other"


def _available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='scheduler_capacity_service'"
        ).fetchone()
        is not None
    )


def choose(conn: sqlite3.Connection, config: Config, choices: Sequence[WatchChoice]) -> WatchChoice:
    """Select a subcategory, then an event, without changing host/class ranking."""
    if not choices:
        raise ValueError("listed-page capacity requires eligible choices")
    if choices[0].category != "new" or not _available(conn):
        return choose_event(conn, choices)
    if any((choice.host, choice.category) != (choices[0].host, "new") for choice in choices):
        raise ValueError("capacity selection must stay within one host and new-work class")
    keys = [choice.key for choice in choices]
    members = memberships(conn, keys)
    parsers = dict(
        conn.execute(
            "SELECT watch_id,parser FROM watches WHERE watch_id IN (SELECT value FROM json_each(?))",
            (json.dumps(keys),),
        )
    )
    purposes = {key: purpose(parsers.get(key), listed=bool(members.get(key))) for key in keys}
    lanes: dict[str, list[WatchChoice]] = {"listed_result": [], "discovery": []}
    for choice in choices:
        lane = "listed_result" if purposes[choice.key] == "listed_result" else "discovery"
        lanes[lane].append(choice)
    row = conn.execute(
        "SELECT credit FROM scheduler_capacity_service WHERE selected_host=?", (choices[0].host,)
    ).fetchone()
    credit = int(row[0]) if row else 0
    competing = all(lanes.values())
    lane = (
        ("listed_result" if credit >= 0 else "discovery")
        if competing
        else next(key for key, group in lanes.items() if group)
    )
    selected = choose_event(conn, lanes[lane])
    policy = json.dumps(
        {
            "format": "listed-page-capacity-v1",
            "listed_page_percent": config.scheduler.listed_page_percent,
            "fetch_chain_request_limit": FETCH_CHAIN_REQUEST_LIMIT,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return replace(
        selected,
        capacity=Selection(
            lane,
            purposes[selected.key],
            competing,
            config.scheduler.listed_page_percent,
            sha256(policy.encode()).hexdigest(),
            policy,
        ),
    )


def record(conn: sqlite3.Connection, choice: WatchChoice, *, action_id: str) -> None:
    """One purpose per actual issued request; borrowing never creates share debt."""
    selected = choice.capacity
    if selected is None:
        return
    if not conn.in_transaction:
        raise RuntimeError("capacity accounting requires the host debit transaction")
    conn.execute(
        "INSERT OR IGNORE INTO scheduler_capacity_policies VALUES (?,?)",
        (selected.policy_digest, selected.policy_json),
    )
    conn.execute(
        "INSERT OR IGNORE INTO scheduler_capacity_service(selected_host) VALUES (?)", (choice.host,)
    )
    before = int(
        conn.execute(
            "SELECT credit FROM scheduler_capacity_service WHERE selected_host=?", (choice.host,)
        ).fetchone()[0]
    )
    after = before
    if selected.competing:
        after += selected.listed_page_percent - (100 if selected.lane == "listed_result" else 0)
    conn.execute(
        "UPDATE scheduler_capacity_service SET credit=? WHERE selected_host=?", (after, choice.host)
    )
    conn.execute(
        "INSERT INTO scheduler_capacity_requests VALUES (?,?,?,?,?,?,?,?)",
        (
            action_id,
            choice.host,
            selected.lane,
            selected.purpose,
            "competing" if selected.competing else "borrowed",
            selected.policy_digest,
            before,
            after,
        ),
    )
