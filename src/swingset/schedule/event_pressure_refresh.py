"""Checkpoint bounded verifier batches; only matching observations may reduce pressure."""

from __future__ import annotations

import json
import math
import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from swingset.config import Config
from swingset.fetch.archive import Archive
from swingset.state.db import Database

from . import event_pressure as state
from .event_pressure_probe import Limits, probe


def _merge(scan: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    result = dict(scan)
    for key in ("checked", "acquired", "interpreted", "unknown"):
        result[key] += batch[key]
    result["parent_valid"] = (
        False
        if False in (scan["parent_valid"], batch["parent_valid"])
        else None
        if None in (scan["parent_valid"], batch["parent_valid"])
        else True
    )
    result["reasons"] = dict(scan["reasons"])
    for reason, count in batch["reasons"].items():
        result["reasons"][reason] = result["reasons"].get(reason, 0) + count
    result["next_cursor"] = batch["next_cursor"]
    result["earliest_checked_at"] = min(
        scan["earliest_checked_at"], batch["earliest_checked_at"], key=datetime.fromisoformat
    )
    return result


def refresh(
    database: Database,
    archive: Archive,
    config: Config,
    *,
    now: datetime,
    max_events: int | None = None,
    wall_seconds: float = 5.0,
) -> dict[str, Any]:
    """Caller owns operator admission; this operation never authorizes requests."""
    if archive.recovery is not None:
        raise ValueError("pressure refresh requires an Archive without recovery")
    if not math.isfinite(wall_seconds) or wall_seconds < 0:
        raise ValueError("pressure refresh wall_seconds must be finite and nonnegative")
    bound = config.scheduler.event_pressure_refresh_events if max_events is None else max_events
    if (
        isinstance(bound, bool)
        or not isinstance(bound, int)
        or not 1 <= bound <= config.scheduler.event_pressure_refresh_events
    ):
        raise ValueError("pressure refresh exceeds configured event bound")
    conn = database.connection
    if not state.available(conn):
        return {"supported": False, "refreshed": 0}
    if conn.in_transaction:
        raise ValueError("pressure refresh must own its separate read/write transactions")
    deadline = time.monotonic() + wall_seconds
    captured = state.policy(config)
    subjects = list(
        conn.execute(
            "SELECT source,source_ref FROM event_pressure_subjects ORDER BY checked_sequence,source,source_ref LIMIT ?",
            (bound,),
        )
    )
    refreshed = discarded = partial = 0
    for source, source_ref in subjects:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        with database.transaction(immediate=False) as reader:
            fence = state.token(reader, source, source_ref, captured["digest"])
            row = reader.execute(
                "SELECT scan_json FROM event_pressure_subjects WHERE source=? AND source_ref=?",
                (source, source_ref),
            ).fetchone()
            scan = json.loads(row[0]) if row[0] else None
            if scan is None or scan["token"] != fence:
                scan = {
                    "token": fence,
                    "checked": 0,
                    "acquired": 0,
                    "interpreted": 0,
                    "unknown": 0,
                    "parent_valid": True,
                    "reasons": {},
                    "next_cursor": None,
                    "earliest_checked_at": now.isoformat(),
                }
            if fence["enumeration_id"] is None:
                batch = {
                    "enumeration_id": None,
                    "checked": 0,
                    "acquired": 0,
                    "interpreted": 0,
                    "unknown": 1,
                    "parent_valid": None,
                    "reasons": {"enumeration_unavailable": 1},
                    "next_cursor": None,
                    "end_of_enumeration": True,
                    "earliest_checked_at": now.isoformat(),
                }
            else:
                limits = Limits(members=config.scheduler.event_pressure_probe_pages)
                limits = replace(limits, seconds=min(limits.seconds, remaining))
                batch = probe(
                    reader,
                    archive,
                    enumeration_id=fence["enumeration_id"],
                    after_request_id=scan["next_cursor"],
                    limits=limits,
                    now=now,
                )
            combined = _merge(scan, batch)
        with database.transaction() as writer:
            # Preserve a closure caused by expiry before new observations reduce
            # pressure into the hysteresis interval.
            state.persist_latches(writer, config, now=now)
            writer.execute("UPDATE event_pressure_state SET sequence=sequence+1")
            sequence = writer.execute("SELECT sequence FROM event_pressure_state").fetchone()[0]
            if (
                state.token(writer, source, source_ref, captured["digest"]) != fence
                or batch["enumeration_id"] != fence["enumeration_id"]
            ):
                writer.execute(
                    "UPDATE event_pressure_subjects SET scan_json=NULL,observation_json=NULL,checked_sequence=? WHERE source=? AND source_ref=?",
                    (sequence, source, source_ref),
                )
                discarded += 1
            else:
                observation = None
                if batch["end_of_enumeration"]:
                    observation = {
                        **combined,
                        "valid_until": (
                            datetime.fromisoformat(combined["earliest_checked_at"])
                            + timedelta(seconds=config.scheduler.event_pressure_max_age_seconds)
                        ).isoformat(),
                    }
                else:
                    partial += 1
                writer.execute(
                    "UPDATE event_pressure_subjects SET scan_json=?,observation_json=?,checked_sequence=? WHERE source=? AND source_ref=?",
                    (
                        None if observation else state._json(combined),
                        state._json(observation) if observation else None,
                        sequence,
                        source,
                        source_ref,
                    ),
                )
                refreshed += 1
            state.persist_latches(writer, config, now=now)
    return {
        "supported": True,
        "refreshed": refreshed,
        "discarded": discarded,
        "partial": partial,
        "budget_exhausted": time.monotonic() >= deadline,
    }
