"""Durable per-host request accounting and independently classified pauses."""

import math
import sqlite3
import threading
import weakref
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import uuid4

from swingset.clock import Clock, SystemClock
from swingset.config import Config
from swingset.fetch.classify import Classification, Outcome


@dataclass(frozen=True)
class Grant:
    host: str
    # Accounting admission time; not a socket-dispatch timestamp.
    debited_at: datetime | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Wait:
    seconds: float


@dataclass(frozen=True)
class Paused:
    reason: str


# One writer may construct several clients. A durable reservation also excludes
# those clients; a process-local owner tells recovery that work is still live.
_MUTEX = threading.RLock()
_OWNERS: weakref.WeakValueDictionary[str, "Gate"] = weakref.WeakValueDictionary()
_WAIT_UNTIL: dict[str, tuple[Clock, float]] = {}


class _Spacing(NamedTuple):
    reservation: str
    gap: float | None
    released_at: str | None
    reserved_at: str | None


class Gate:
    def __init__(self, connection: sqlite3.Connection, config: Config, clock: Clock) -> None:
        self.connection = connection
        self.config = config
        self.clock = clock
        self.inflight: set[str] = set()
        self._mutex = _MUTEX
        self._reservations: dict[str, str] = {}

    def _spacing(self, host: str) -> _Spacing | None:
        row = self.connection.execute(
            "SELECT reservation_id,gap_seconds,released_at,reserved_at FROM host_request_spacing WHERE host=?",
            (host,),
        ).fetchone()
        return (
            _Spacing(
                str(row[0]),
                float(row[1]) if row[1] is not None else None,
                str(row[2]) if row[2] else None,
                str(row[3]) if row[3] else None,
            )
            if row
            else None
        )

    def _spacing_schema(self) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='host_request_spacing'"
            ).fetchone()
            is not None
        )

    def _legacy_paid(self, host: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM host_budget WHERE host=? AND requests>0 LIMIT 1", (host,)
            ).fetchone()
            is not None
        )

    def establish_spacing_baseline(
        self, host: str, *, gap_seconds: float, stopped_at: datetime, evidence_ref: str
    ) -> str:
        """Record a reviewed legacy baseline under exclusive writer ownership.

        The operator must establish that the old worker has stopped and supply
        a conservative gap from effective configuration and retained robots.
        This records that evidence, not a fabricated request completion. A full
        fresh gap must still elapse, and no host deadline or budget is reduced.
        """
        with self._mutex:
            conn = self.connection
            now = self.clock.now().astimezone(UTC)
            if not conn.in_transaction:
                raise ValueError("spacing baseline requires a writer transaction")
            if not evidence_ref.strip() or stopped_at.tzinfo is None or stopped_at > now:
                raise ValueError("spacing baseline needs evidence and an observed past stop")
            if not math.isfinite(gap_seconds) or gap_seconds < max(
                5, self.config.host(host).min_gap_seconds
            ):
                raise ValueError("spacing baseline gap must cover the ordinary configured floor")
            previous = self._spacing(host)
            if previous and self._live(previous[0], host):
                raise ValueError("spacing baseline cannot replace a live reservation")
            if (previous is not None and previous[1] is not None) or (
                previous is None and not self._legacy_paid(host)
            ):
                raise ValueError("spacing baseline applies only to unknown legacy paid hosts")
            if conn.execute(
                "SELECT 1 FROM execution_admissions WHERE host=? AND action_kind='request' AND state<>'settled' LIMIT 1",
                (host,),
            ).fetchone():
                raise ValueError("spacing baseline cannot replace an unsettled request")
            reservation = "baseline_" + uuid4().hex
            conn.execute("INSERT OR IGNORE INTO hosts(host) VALUES (?)", (host,))
            conn.execute(
                "INSERT INTO host_request_spacing_baselines(reservation_id,host,gap_seconds,stopped_at,recorded_at,evidence_ref,prior_reservation_id) VALUES(?,?,?,?,?,?,?)",
                (
                    reservation,
                    host,
                    gap_seconds,
                    stopped_at.isoformat(),
                    now.isoformat(),
                    evidence_ref,
                    previous[0] if previous else None,
                ),
            )
            conn.execute(
                "INSERT INTO host_request_spacing VALUES(?,?,?,?,NULL) ON CONFLICT(host) DO UPDATE SET "
                "reservation_id=excluded.reservation_id,gap_seconds=excluded.gap_seconds,reserved_at=excluded.reserved_at,released_at=NULL",
                (host, reservation, gap_seconds, now.isoformat()),
            )
            # Admission will start the full monotonic wait after the caller's
            # commit. Rolling this transaction back establishes no authority.
            return reservation

    @staticmethod
    def _live(reservation: str, host: str) -> bool:
        owner = _OWNERS.get(reservation)
        return owner is not None and owner._reservations.get(host) == reservation

    def _remaining(self, reservation: str) -> float | None:
        wait = _WAIT_UNTIL.get(reservation)
        same_domain = wait is not None and (
            wait[0] is self.clock
            or isinstance(wait[0], SystemClock)
            and isinstance(self.clock, SystemClock)
        )
        return (
            max(0, wait[1] - self.clock.monotonic()) if wait is not None and same_domain else None
        )

    def assess(
        self, host: str, *, source: str = "", sweep: bool = False
    ) -> tuple[Grant | Wait | Paused, datetime]:
        """Read the same host gates as acquisition without creating rows or debits.

        The deadline bounds the proof; callers must reassess after mutations.
        In-flight ownership is meaningful only in this gate's writer process.
        """
        with self._mutex:
            return self._assess(host, source=source, sweep=sweep)

    def _assess(
        self, host: str, *, source: str, sweep: bool, now: datetime | None = None
    ) -> tuple[Grant | Wait | Paused, datetime]:
        now = now if now is not None else self.clock.now().astimezone(UTC)
        boundary = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        conn = self.connection
        for pause in conn.execute("SELECT scope_kind,scope_id,until_at FROM operator_pauses"):
            if pause[2] is not None and datetime.fromisoformat(pause[2]) <= now:
                continue
            if pause[0] == "all" or (pause[0], pause[1]) in (
                ("host", host),
                ("source", source),
            ):
                if pause[2]:
                    boundary = min(boundary, datetime.fromisoformat(pause[2]))
                return Paused("operator"), boundary
        row = conn.execute(
            "SELECT next_allowed_at,paused_until,pause_reason FROM hosts WHERE host=?", (host,)
        ).fetchone()
        if row and row[1] and datetime.fromisoformat(row[1]) > now:
            return Paused(str(row[2])), min(boundary, datetime.fromisoformat(row[1]))
        if host in self.inflight:
            return Wait(1), boundary
        if not self._spacing_schema():
            return Paused("request spacing schema unavailable"), boundary
        spacing = self._spacing(host)
        legacy_unknown = (spacing is not None and spacing[1] is None) or (
            spacing is None and self._legacy_paid(host)
        )
        if spacing and not legacy_unknown:
            reservation = spacing[0]
            if self._live(reservation, host):
                return Wait(1), boundary
            remaining = self._remaining(reservation)
            if remaining is None:
                # Assessment never starts recovery or assumes an old grant was
                # dispatched/completed at its accounting timestamp.
                return Paused("request spacing recovery required"), boundary
            if remaining is not None and remaining > 0:
                return Wait(remaining), min(boundary, now + timedelta(seconds=remaining))
        if row and row[0] and datetime.fromisoformat(row[0]) > now:
            due = datetime.fromisoformat(row[0])
            return Wait((due - now).total_seconds()), min(boundary, due)
        policy = self.config.host(host)
        budget = conn.execute(
            "SELECT requests,bytes FROM host_budget WHERE host=? AND day=?",
            (host, now.date().isoformat()),
        ).fetchone()
        requests, size = tuple(budget) if budget else (0, 0)
        request_limit = (
            policy.sweep_daily_request_budget
            if sweep and host == "points.worldsdc.com"
            else policy.daily_request_budget
        )
        if requests >= request_limit:
            return Paused("request budget"), boundary
        if policy.daily_byte_budget is not None and size >= policy.daily_byte_budget:
            return Paused("byte budget"), boundary
        if legacy_unknown:
            return Paused("legacy request spacing unknown"), boundary
        return Grant(host), boundary

    def acquire(
        self, host: str, *, source: str = "", crawl_delay: float = 0, sweep: bool = False
    ) -> Grant | Wait | Paused:
        with self._mutex:
            now = self.clock.now().astimezone(UTC)
            conn = self.connection
            previous = self._spacing(host) if self._spacing_schema() else None
            if previous and previous[1] is not None and not self._live(previous[0], host):
                # Exclusive database writer ownership excludes another process.
                # A fresh full wait is conservative even if the old process died
                # long after its grant. A further crash starts this wait anew.
                if self._remaining(previous[0]) is None:
                    _WAIT_UNTIL[previous[0]] = (self.clock, self.clock.monotonic() + previous[1])
            grant, _ = self._assess(host, source=source, sweep=sweep, now=now)
            # Keep acquisition's diagnostic rows even when no request is issued.
            conn.execute("INSERT OR IGNORE INTO hosts(host) VALUES (?)", (host,))
            if not isinstance(grant, Grant):
                return grant
            policy = self.config.host(host)
            day = now.date().isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO host_budget(host,day,requests,bytes) VALUES (?,?,0,0)",
                (host, day),
            )
            gap = max(
                policy.min_gap_seconds,
                crawl_delay,
                2 if sweep and host == "points.worldsdc.com" else 5,
            )
            reservation = uuid4().hex
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
                conn.execute(
                    "INSERT INTO host_request_spacing(host,reservation_id,gap_seconds,reserved_at,released_at) "
                    "VALUES(?,?,?,?,NULL) ON CONFLICT(host) DO UPDATE SET "
                    "reservation_id=excluded.reservation_id,gap_seconds=excluded.gap_seconds,"
                    "reserved_at=excluded.reserved_at,released_at=NULL",
                    (host, reservation, gap, now.isoformat()),
                )
                if not outer_transaction:
                    conn.commit()  # Issued requests are never refunded after a crash.
            except BaseException:
                if not outer_transaction:
                    conn.rollback()
                raise
            self.inflight.add(host)
            self._reservations[host] = reservation
            _OWNERS[reservation] = self
            if previous:
                _WAIT_UNTIL.pop(previous[0], None)
            return Grant(host, debited_at=now)

    def discard(self, host: str) -> None:
        """Release a process-local claim without refunding durable accounting."""
        with self._mutex:
            self.inflight.discard(host)
            reservation = self._reservations.pop(host, None)
            if reservation:
                _OWNERS.pop(reservation, None)

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
                now = self.clock.now().astimezone(UTC)
                spacing = self._spacing(host)
                if not spacing or spacing[1] is None or self._reservations.get(host) != spacing[0]:
                    raise ValueError("request spacing release requires the owning reservation")
                gap = spacing[1]
                if spacing.reserved_at is None:
                    raise ValueError("request spacing reservation has no debit timestamp")
                debit_day = (
                    datetime.fromisoformat(spacing.reserved_at).astimezone(UTC).date().isoformat()
                )
                next_allowed = now + timedelta(seconds=gap)
                prior = conn.execute(
                    "SELECT next_allowed_at FROM hosts WHERE host=?", (host,)
                ).fetchone()
                if prior and prior[0]:
                    next_allowed = max(next_allowed, datetime.fromisoformat(prior[0]))
                _WAIT_UNTIL[spacing[0]] = (self.clock, self.clock.monotonic() + gap)
                if not outer_transaction:
                    conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE hosts SET next_allowed_at=? WHERE host=?",
                    (next_allowed.isoformat(), host),
                )
                conn.execute(
                    "UPDATE host_request_spacing SET released_at=? WHERE host=? AND reservation_id=?",
                    (now.isoformat(), host, spacing[0]),
                )
                conn.execute(
                    "UPDATE host_budget SET bytes=bytes+? WHERE host=? AND day=?",
                    (body_bytes, host, debit_day),
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
            except BaseException:
                if not outer_transaction:
                    conn.rollback()
                raise
            finally:
                self.discard(host)
