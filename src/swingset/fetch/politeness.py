"""Durable per-host request accounting and independently classified pauses."""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.classify import Classification, Outcome


@dataclass(frozen=True)
class Grant:
    host: str


@dataclass(frozen=True)
class Wait:
    seconds: float


@dataclass(frozen=True)
class Paused:
    reason: str


class Gate:
    def __init__(self, connection: sqlite3.Connection, config: Config, clock: Clock) -> None:
        self.connection = connection
        self.config = config
        self.clock = clock
        self.inflight: set[str] = set()
        self._mutex = threading.Lock()

    def acquire(
        self, host: str, *, source: str = "", crawl_delay: float = 0, sweep: bool = False
    ) -> Grant | Wait | Paused:
        with self._mutex:
            now = self.clock.now()
            conn = self.connection
            for pause in conn.execute("SELECT scope_kind,scope_id,until_at FROM operator_pauses"):
                if pause[2] is not None and datetime.fromisoformat(pause[2]) <= now:
                    continue
                if pause[0] == "all" or (pause[0], pause[1]) in (
                    ("host", host),
                    ("source", source),
                ):
                    return Paused("operator")
            conn.execute("INSERT OR IGNORE INTO hosts(host) VALUES (?)", (host,))
            row = conn.execute(
                "SELECT next_allowed_at,paused_until,pause_reason FROM hosts WHERE host=?", (host,)
            ).fetchone()
            assert row is not None
            if row[1] and datetime.fromisoformat(row[1]) > now:
                return Paused(str(row[2]))
            if host in self.inflight:
                return Wait(1)
            if row[0] and datetime.fromisoformat(row[0]) > now:
                return Wait((datetime.fromisoformat(row[0]) - now).total_seconds())
            policy = self.config.host(host)
            day = now.date().isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO host_budget(host,day,requests,bytes) VALUES (?,?,0,0)",
                (host, day),
            )
            budget = conn.execute(
                "SELECT requests,bytes FROM host_budget WHERE host=? AND day=?", (host, day)
            ).fetchone()
            request_limit = (
                policy.sweep_daily_request_budget
                if sweep and host == "points.worldsdc.com"
                else policy.daily_request_budget
            )
            if budget[0] >= request_limit:
                return Paused("request budget")
            if policy.daily_byte_budget is not None and budget[1] >= policy.daily_byte_budget:
                return Paused("byte budget")
            gap = max(
                policy.min_gap_seconds,
                crawl_delay,
                2 if sweep and host == "points.worldsdc.com" else 5,
            )
            outer_transaction = conn.in_transaction
            if not outer_transaction:
                conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE hosts SET next_allowed_at=? WHERE host=?",
                    ((now + timedelta(seconds=gap)).isoformat(), host),
                )
                conn.execute(
                    "UPDATE host_budget SET requests=requests+1 WHERE host=? AND day=?", (host, day)
                )
                if not outer_transaction:
                    conn.commit()  # Issued requests are never refunded after a crash.
            except BaseException:
                if not outer_transaction:
                    conn.rollback()
                raise
            self.inflight.add(host)
            return Grant(host)

    def discard(self, host: str) -> None:
        """Release a process-local claim without refunding durable accounting."""
        with self._mutex:
            self.inflight.discard(host)

    def release(
        self,
        host: str,
        classification: Classification,
        *,
        body_bytes: int = 0,
        request_day: str | None = None,
    ) -> None:
        with self._mutex:
            try:
                conn = self.connection
                outer_transaction = conn.in_transaction
                now = self.clock.now()
                conn.execute(
                    "UPDATE host_budget SET bytes=bytes+? WHERE host=? AND day=?",
                    (body_bytes, host, request_day or now.date().isoformat()),
                )
                if classification.outcome in (
                    Outcome.THROTTLED,
                    Outcome.BLOCKED,
                    Outcome.SERVER_ERROR,
                ):
                    row = conn.execute(
                        "SELECT pause_streak FROM hosts WHERE host=?", (host,)
                    ).fetchone()
                    streak = int(row[0] or 0) + 1
                    seconds = (
                        self.config.host(host).challenge_pause
                        if classification.outcome == Outcome.BLOCKED
                        else classification.retry_after or min(86400, 900 * 2 ** min(streak - 1, 7))
                    )
                    conn.execute(
                        "UPDATE hosts SET paused_until=?,pause_reason=?,pause_streak=? WHERE host=?",
                        (
                            (now + timedelta(seconds=seconds)).isoformat(),
                            classification.outcome.value,
                            streak,
                            host,
                        ),
                    )
                elif classification.outcome in (Outcome.OK, Outcome.NOT_MODIFIED):
                    conn.execute(
                        "UPDATE hosts SET paused_until=NULL,pause_reason=NULL,pause_streak=0 WHERE host=?",
                        (host,),
                    )
                if not outer_transaction:
                    conn.commit()
            finally:
                self.inflight.discard(host)
