"""Guard reasons: why a fetch was held, since when, and when it should clear.

One `guard_reasons` row per subject and reason (migration 34). A subject is
`host:<h>`, `watch:<id>` or `loop:<l>`. `rule` says which skips are guards
and what their subject is; `expires_at` gives each its clock; `flush` writes
one acquisition pass's guards in its caller's transaction (D-0219). The fetch
client collects them (fetch/client.GuardRecorder).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import astuple, dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Literal

from swingset.state.queue import at


@dataclass(frozen=True)
class GuardRow:
    subject: str
    reason: str
    loop: str
    since: str
    last_seen_at: str
    expires_at: str
    seen: int = 1


_COLUMNS = tuple(field.name for field in fields(GuardRow))
_KEY = ("subject", "reason")

HOUR = timedelta(hours=1)
#: The fetch gate cannot tell when the last request went (fetch/politeness).
SPACING = frozenset(
    {
        "legacy request spacing unknown",
        "request spacing recovery required",
        "request spacing schema unavailable",
    }
)
#: A host paused by its own answers; `hosts.paused_until` says until when.
HOST_PAUSES = frozenset({"Throttled", "ServerError", "Blocked"})
#: A host's daily budget is spent; it renews at UTC midnight.
BUDGETS = frozenset({"request budget", "byte budget"})
OPERATOR = "operator"
BACKPRESSURE = "pending work backpressure"
#: Event expansion denials (schedule/event_pressure.admission).
EXPANSION = frozenset({"event expansion pressure", "event pressure metadata pending"})
PUBLICATION = "publication_in_flight"
#: Long-standing watch holds: counted daily, never alarmed.
WATCH_REASONS = frozenset({"robots disallow", "source disabled"})
#: History gate reasons (history/acquisition, history/origin_dispatch) that
#: can also reach the client as a `Paused` grant.
HISTORY_PREFIXES = ("history_", "origin_")
HISTORY_REASONS = frozenset({"plan_mapping_changed", "parent_document_pending"})
#: A loop tick that skipped whole, and how long that may last (D-0219): plan
#: and release wait for derive to accept the inputs, which it does within a
#: tick; release waits for derive's heavy lock, three hourly ticks at most.
LOOP_SKIPS = {"inputs_not_accepted": timedelta(hours=1), "heavy_busy": timedelta(hours=3)}
#: A request that raised (a defect, not a gate): its watch waits on a clock,
#: an hour doubling, and a week from the third in a row, like a quarantined
#: unit (D-0219). Written by the acquisition pass, never by `flush`.
REQUEST_ERROR = "request_error"
REQUEST_ERROR_FIRST = timedelta(hours=1)
REQUEST_ERROR_QUARANTINE = 3
REQUEST_ERROR_HOLD = timedelta(days=7)
OPERATOR_OPEN = timedelta(hours=48)
BACKPRESSURE_CLOCK = timedelta(hours=12)
EXPANSION_CLOCK = timedelta(hours=24)
WATCH_CLOCK = timedelta(days=30)

Kind = Literal["host", "watch", "loop"]


@dataclass(frozen=True)
class Rule:
    """A guard's subject kind; `daily` rows are written once a UTC day."""

    subject: Kind
    daily: bool = False


def rule(reason: str, *, history: bool = False) -> Rule | None:
    """The guard a skip reason is, or None when it is not a guard.

    `history` marks a reason returned by the client's history gate, whatever
    its text. A wait, a deadline skip, a terminal watch state and a request
    limit are not guards.
    """
    if (
        history
        or reason in WATCH_REASONS
        or reason in HISTORY_REASONS
        or reason.startswith(HISTORY_PREFIXES)
    ):
        return Rule("watch", daily=True)
    if reason in (BACKPRESSURE, PUBLICATION) or reason in LOOP_SKIPS:
        return Rule("loop")
    if reason in SPACING | HOST_PAUSES | BUDGETS | EXPANSION or reason == OPERATOR:
        return Rule("host")
    return None


def subject(
    kind: Kind, *, host: str | None = None, watch_id: str | None = None, loop: str
) -> str | None:
    value = {"host": host, "watch": watch_id, "loop": loop}[kind]
    return f"{kind}:{value}" if value else None


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _operator_until(conn: sqlite3.Connection, host: str, now: datetime) -> str | None:
    """The end of the live `all` or host pauses on `host`; None when one is open."""
    ends: list[datetime] = []
    for until in conn.execute(
        "SELECT until_at FROM operator_pauses WHERE scope_kind='all' "
        "OR (scope_kind='host' AND scope_id=?)",
        (host,),
    ):
        if until[0] is None:
            return None
        end = _time(str(until[0]))
        if end > now:
            ends.append(end)
    return at(max(ends)) if ends else None


def expires_at(
    conn: sqlite3.Connection,
    subject: str,
    reason: str,
    since: datetime,
    *,
    now: datetime,
    until: str | None = None,
) -> datetime:
    """When the reason should have cleared, from when it began (D-0219).

    `until` is an operator pause's end when the caller knows it.
    """
    kind, _, name = subject.partition(":")
    if kind == "watch":
        return since + WATCH_CLOCK
    if reason == BACKPRESSURE:
        return since + BACKPRESSURE_CLOCK
    if reason == PUBLICATION:
        return since + HOUR
    if kind == "loop" and reason in LOOP_SKIPS:
        return since + LOOP_SKIPS[reason]
    if reason in EXPANSION:
        return since + EXPANSION_CLOCK
    if reason in BUDGETS:
        midnight = (since.astimezone(UTC) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return midnight + HOUR
    if reason in HOST_PAUSES:
        row = conn.execute("SELECT paused_until FROM hosts WHERE host=?", (name,)).fetchone()
        return _time(str(row[0])) + HOUR if row and row[0] else since + HOUR
    if reason == OPERATOR:
        end = until if until is not None else _operator_until(conn, name, now)
        return _time(end) if end is not None else since + OPERATOR_OPEN
    return since + HOUR


def flush(
    conn: sqlite3.Connection,
    *,
    loop: str,
    seen: Mapping[tuple[str, str], int],
    requested: Iterable[str],
    now: datetime,
    until: Mapping[tuple[str, str], str | None] | None = None,
) -> dict[str, int]:
    """Write one pass's guards; the caller holds the transaction.

    A subject that issued a request this pass is not held: its rows go, and
    its skips this pass are dropped. A `request_error` row stays: a request
    that went out and then raised (its response write, say) must keep its
    clock; only a served request clears it. Every other skip keeps its row's
    `since` and moves `last_seen_at`, `seen` and `expires_at`. A daily row is
    written at most once a UTC day, so its `seen` counts days.
    """
    stamp = at(now)
    issued = set(requested)
    cleared = sum(
        conn.execute(
            "DELETE FROM guard_reasons WHERE subject=? AND reason<>?", (name, REQUEST_ERROR)
        ).rowcount
        for name in sorted(issued)
    )
    recorded = 0
    for (name, reason), count in sorted(seen.items()):
        if name in issued:
            continue
        kind = rule(reason, history=name.startswith("watch:"))
        old = get(conn, name, reason)
        if kind is not None and kind.daily:
            if old is not None and old.last_seen_at[:10] == stamp[:10]:
                continue
            count = 1
        since = old.since if old is not None else stamp
        hint = (until or {}).get((name, reason))
        expiry = expires_at(conn, name, reason, _time(since), now=now, until=hint)
        upsert(
            conn,
            GuardRow(
                name,
                reason,
                loop,
                since,
                stamp,
                at(expiry),
                (old.seen if old is not None else 0) + count,
            ),
        )
        recorded += 1
    return {"recorded": recorded, "cleared": cleared}


def request_failed(
    conn: sqlite3.Connection, watch_key: str, *, loop: str, now: datetime
) -> GuardRow:
    """Hold a watch whose request raised; the caller holds the transaction.

    Each failure in a row moves the expiry: an hour, doubling, and a week
    from the `REQUEST_ERROR_QUARANTINE`-th. The pass skips the watch until
    then (`requests_held`); a request that goes through deletes the row.
    """
    name = f"watch:{watch_key}"
    old = get(conn, name, REQUEST_ERROR)
    seen = (old.seen if old is not None else 0) + 1
    hold = (
        REQUEST_ERROR_HOLD
        if seen >= REQUEST_ERROR_QUARANTINE
        else REQUEST_ERROR_FIRST * (2 ** (seen - 1))
    )
    row = GuardRow(
        name,
        REQUEST_ERROR,
        loop,
        old.since if old is not None else at(now),
        at(now),
        at(now + hold),
        seen,
    )
    upsert(conn, row)
    return row


def requests_errored(conn: sqlite3.Connection, *, now: datetime) -> dict[str, bool]:
    """Watch keys with a `request_error` row, and whether it still holds them."""
    if not available(conn):
        return {}
    stamp = at(now)
    return {
        str(subject).removeprefix("watch:"): str(expires) > stamp
        for subject, expires in conn.execute(
            "SELECT subject,expires_at FROM guard_reasons WHERE reason=?", (REQUEST_ERROR,)
        )
    }


def loop_skip(conn: sqlite3.Connection, loop: str, reason: str, *, now: datetime) -> None:
    """Record that a whole tick of ``loop`` skipped for ``reason``; caller's transaction."""
    flush(conn, loop=loop, seen={(f"loop:{loop}", reason): 1}, requested=(), now=now)


def loop_ran(conn: sqlite3.Connection, loop: str, reason: str) -> int:
    """A tick of ``loop`` got past ``reason``: its guard row goes."""
    return delete(conn, f"loop:{loop}", reason)


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='guard_reasons'").fetchone()
        is not None
    )


def get(conn: sqlite3.Connection, subject: str, reason: str) -> GuardRow | None:
    row = conn.execute(
        f"SELECT {','.join(_COLUMNS)} FROM guard_reasons WHERE subject=? AND reason=?",
        (subject, reason),
    ).fetchone()
    return None if row is None else GuardRow(*row)


def upsert(conn: sqlite3.Connection, row: GuardRow) -> None:
    updates = ",".join(f"{name}=excluded.{name}" for name in _COLUMNS if name not in _KEY)
    conn.execute(
        f"INSERT INTO guard_reasons({','.join(_COLUMNS)}) "
        f"VALUES ({','.join('?' * len(_COLUMNS))}) "
        f"ON CONFLICT(subject,reason) DO UPDATE SET {updates}",
        astuple(row),
    )


def clocked(
    conn: sqlite3.Connection,
    subject: str,
    reason: str,
    *,
    loop: str,
    now: datetime,
    clock: timedelta,
) -> GuardRow:
    """Note `reason` again on `subject`, keeping when it began; caller's transaction.

    The row expires `clock` after it began, so a reason that keeps coming
    back past that is `[guard-expired]` in the watchdog. Deleting the row
    when the reason clears restarts the clock.
    """
    stamp = at(now)
    existing = get(conn, subject, reason)
    since = existing.since if existing is not None else stamp
    begun = datetime.fromisoformat(since.replace("Z", "+00:00"))
    row = GuardRow(
        subject=subject,
        reason=reason,
        loop=loop,
        since=since,
        last_seen_at=stamp,
        expires_at=at(begun + clock),
        seen=(existing.seen + 1) if existing is not None else 1,
    )
    upsert(conn, row)
    return row


def delete(conn: sqlite3.Connection, subject: str, reason: str | None = None) -> int:
    """Delete one reason of a subject, or all of them; returns rows deleted."""
    if reason is None:
        cursor = conn.execute("DELETE FROM guard_reasons WHERE subject=?", (subject,))
    else:
        cursor = conn.execute(
            "DELETE FROM guard_reasons WHERE subject=? AND reason=?", (subject, reason)
        )
    return cursor.rowcount


def counts_by_reason(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        str(reason): int(count)
        for reason, count in conn.execute(
            "SELECT reason,count(*) FROM guard_reasons GROUP BY reason ORDER BY reason"
        )
    }


def report(conn: sqlite3.Connection, *, now: datetime) -> dict[str, object]:
    """Counts by reason, and the rows still seen after their expiry.

    Watch rows are counted only (D-0219); the watchdog reads the same rows.
    """
    if not available(conn):
        return {"supported": False}
    expired = [
        {"subject": row[0], "reason": row[1], "since": row[2], "expires_at": row[3]}
        for row in conn.execute(
            "SELECT subject,reason,since,expires_at FROM guard_reasons "
            "WHERE subject NOT LIKE 'watch:%' AND last_seen_at > expires_at "
            "ORDER BY expires_at LIMIT 20"
        )
    ]
    return {
        "supported": True,
        "by_reason": counts_by_reason(conn),
        "expired": expired,
        "snapshot_at": at(now),
    }
