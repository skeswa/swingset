"""Read-only union of matching operator pause intervals for diagnostic clocks."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any

from .controls import ActionScope


def _aware(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if value else None
        return parsed if parsed is not None and parsed.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def _matches(selector: tuple[str, str], scope: ActionScope) -> bool:
    kind, identifier = selector
    return (
        kind == "all"
        or kind == "source"
        and (scope.all_sources or identifier in scope.sources)
        or kind == "kind"
        and (scope.all_kinds or identifier in scope.kinds)
        or kind == "host"
        and identifier == scope.host
    )


class ControlTime:
    """Read the event ledger once; merge overlapping intervals once per scope.

    These clocks exclude operator holds, not host budgets or other eligibility
    blockers. Current dependency ownership is explicit; old unknown pause starts
    produce an unavailable duration rather than a fabricated zero.
    """

    def __init__(self, conn: sqlite3.Connection, now: datetime):
        if now.utcoffset() is None:
            raise ValueError("control report time requires an explicit timezone")
        self.now = now
        self.invalid_timestamps: set[tuple[str, str]] = set()
        self.invalid_cache: dict[ActionScope, bool] = {}
        self.intervals: dict[tuple[str, str], list[tuple[datetime | None, datetime]]] = defaultdict(
            list
        )
        self.cache: dict[ActionScope, list[tuple[datetime | None, datetime]]] = {}
        active: dict[str, tuple[tuple[str, str], datetime | None, datetime | None]] = {}
        observed = set()
        has_events = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='control_events'"
        ).fetchone()

        def close(identifier: str, at: datetime) -> None:
            prior = active.pop(identifier, None)
            if prior:
                selector, start, expiry = prior
                end = min(at, expiry) if expiry else at
                if start is None or start < end:
                    self.intervals[selector].append((start, end))

        if has_events:
            for row in conn.execute("SELECT * FROM control_events ORDER BY event_id"):
                selector = (row["scope_kind"], row["scope_id"])
                at = _aware(row["occurred_at"])
                expiry = _aware(row["until_at"])
                if at is None or (row["until_at"] is not None and expiry is None):
                    self.invalid_timestamps.add(selector)
                    continue
                if at > now:
                    continue
                identifier = str(row["pause_id"])
                observed.add(identifier)
                close(identifier, at)
                if row["action"] in {"pause", "legacy_import"}:
                    if row["action"] == "legacy_import":
                        self.intervals[(row["scope_kind"], row["scope_id"])].append(
                            (None, min(at, expiry) if expiry else at)
                        )
                    active[identifier] = (
                        (row["scope_kind"], row["scope_id"]),
                        at,
                        expiry,
                    )
        for row in conn.execute("SELECT * FROM operator_pauses"):
            value = dict(row)
            selector = (row["scope_kind"], row["scope_id"])
            beginning = _aware(value.get("created_at"))
            expiry = _aware(row["until_at"])
            if (value.get("created_at") is not None and beginning is None) or (
                row["until_at"] is not None and expiry is None
            ):
                self.invalid_timestamps.add(selector)
                continue
            identifier = str(value.get("pause_id"))
            if identifier not in observed:
                # Old schemas and imported controls have no trustworthy start.
                active[identifier + str((row["scope_kind"], row["scope_id"]))] = (
                    (row["scope_kind"], row["scope_id"]),
                    beginning,
                    expiry,
                )
        for identifier in list(active):
            close(identifier, now)
        self.has_history = bool(self.intervals or self.invalid_timestamps)

    def _matching(self, scope: ActionScope) -> list[tuple[datetime | None, datetime]]:
        if scope not in self.cache:
            self.invalid_cache[scope] = any(
                _matches(selector, scope) for selector in self.invalid_timestamps
            )
            spans = [
                interval
                for (kind, identifier), intervals in self.intervals.items()
                if _matches((kind, identifier), scope)
                for interval in intervals
            ]
            unknown_end = max((end for start, end in spans if start is None), default=None)
            self.invalid_cache[scope] = any(
                _matches(selector, scope) for selector in self.invalid_timestamps
            )
            spans = [(start, end) for start, end in spans if start is not None]
            spans.sort(key=lambda item: item[0] or item[1])
            merged: list[tuple[datetime | None, datetime]] = []
            for start, end in spans:
                if merged and (start is None or start <= merged[-1][1]):
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            self.cache[scope] = ([(None, unknown_end)] if unknown_end is not None else []) + merged
        return self.cache[scope]

    def measure(self, scope: ActionScope | None, since: str | None) -> dict[str, Any]:
        start = _aware(since)
        if start is None:
            return {
                "unpaused_seconds": None,
                "operator_paused_seconds": None,
                "time_basis": "unknown_start",
            }
        wall = max(0.0, (self.now - start).total_seconds())
        paused = 0.0
        intervals = self._matching(scope) if scope is not None else ()
        if scope is not None and self.invalid_cache[scope]:
            return {
                "unpaused_seconds": None,
                "operator_paused_seconds": None,
                "time_basis": "control_timestamp_unknown",
            }
        for beginning, end in intervals:
            if end <= start:
                continue
            if beginning is None:
                return {
                    "unpaused_seconds": None,
                    "operator_paused_seconds": None,
                    "time_basis": "legacy_pause_start_unknown",
                }
            paused += max(0.0, (min(end, self.now) - max(beginning, start)).total_seconds())
        return {
            "unpaused_seconds": max(0.0, wall - paused),
            "operator_paused_seconds": paused,
            "time_basis": "current_dependencies_excluding_recorded_operator_pauses",
        }
