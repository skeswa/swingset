"""Bounded, ephemeral Archive offer proofs; never acquisition authority."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from swingset.clock import Clock
from swingset.config import Config
from swingset.sources.base import WatchSpec

MAX_WATCHES = 256
MAX_PENDING_PARSE = 64
FORMAT = "archive-offer-timing-v1"


def epoch(conn: sqlite3.Connection) -> int | None:
    if (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='history_dispatch_fence'").fetchone()
        is None
    ):
        return None
    row = conn.execute("SELECT revision FROM history_dispatch_fence WHERE singleton=1").fetchone()
    return int(row[0]) if row else None


def retry_boundary(conn: sqlite3.Connection, now: datetime, deadline: datetime) -> datetime | None:
    """Inspect at most 64 pending parse units, never their bodies or attempts' JSON.

    Any latest retry can conservatively end an offer proof. This includes unrelated
    units so a newly relevant parent cannot hide behind a narrower guessed domain.
    """
    rows = conn.execute(
        "SELECT CASE WHEN length(unit_kind)<=64 THEN unit_kind END,"
        "CASE WHEN length(unit_id)<=512 THEN unit_id END FROM pending_work "
        "WHERE stage='parse' ORDER BY unit_kind,unit_id LIMIT ?",
        (MAX_PENDING_PARSE + 1,),
    ).fetchall()
    if len(rows) > MAX_PENDING_PARSE:
        return None
    until = deadline
    for kind, identifier in rows:
        if kind is None or identifier is None:
            return None
        attempt = conn.execute(
            "SELECT outcome,CASE WHEN length(retry_at)<=64 THEN retry_at END,retry_at IS NOT NULL "
            "FROM work_attempts WHERE stage='parse' AND unit_kind=? AND unit_id=? "
            "ORDER BY attempt_id DESC LIMIT 1",
            (kind, identifier),
        ).fetchone()
        if attempt is None or attempt[0] not in {"transient", "interrupted"}:
            continue
        if not attempt[2] or attempt[1] is None:
            return None
        try:
            retry = datetime.fromisoformat(attempt[1])
            if retry.tzinfo is None:
                return None
            if retry > now:
                until = min(until, retry)
        except (ValueError, TypeError, OverflowError):
            return None
    return until


@dataclass(frozen=True)
class ArchiveOfferProof:
    connection: sqlite3.Connection = field(repr=False, compare=False)
    spec: WatchSpec
    run_id: str
    epoch: int
    control_revision: int
    observed_at: datetime
    until: datetime
    history_start: str

    def current(
        self, conn: sqlite3.Connection, config: Config, now: datetime, run_id: str | None
    ) -> bool:
        return (
            conn is self.connection
            and run_id == self.run_id
            and self.observed_at <= now < self.until
            and config.history_start.isoformat() == self.history_start
            and epoch(conn) == self.epoch
            and conn.execute("SELECT revision FROM control_state").fetchone()[0]
            == self.control_revision
        )

    def binds(self, watch: Any) -> bool:
        return (
            all(
                getattr(watch, name) == getattr(self.spec, name)
                for name in ("watch_id", "source", "source_ref", "method", "url", "parser", "kind")
            )
            and not watch.form
            and not self.spec.form
        )


class OfferTiming:
    """Bracket existing dispatcher work; additional proof work is bounded.

    The dispatcher retains responsibility for its ordinary plan and candidate
    checks. No plan or candidate is recomputed by this handoff or its consumer.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        config: Config,
        clock: Clock,
        *,
        watches: tuple[str, ...],
        run_id: str,
        deadline: datetime,
        accept: Callable[[tuple[ArchiveOfferProof, ...]], None],
    ) -> None:
        self.conn, self.config, self.clock = conn, config, clock
        self.watches, self.run_id, self.deadline, self.accept = watches, run_id, deadline, accept
        self.started: tuple[int, int, datetime, datetime, str] | None = None

    def begin(
        self, *, connection: sqlite3.Connection, config: Config, clock: Clock, run_id: str | None
    ) -> None:
        self.started = None
        if (
            connection is not self.conn
            or config is not self.config
            or clock is not self.clock
            or run_id != self.run_id
        ):
            return
        if len(self.watches) > MAX_WATCHES or any(len(watch) > 512 for watch in self.watches):
            return
        before, now = epoch(self.conn), self.clock.now()
        if before is None or not self.run_id or now >= self.deadline:
            return
        controls = self.conn.execute("SELECT revision FROM control_state").fetchone()[0]
        until = retry_boundary(self.conn, now, self.deadline)
        if until is not None and epoch(self.conn) == before:
            self.started = before, controls, now, until, self.config.history_start.isoformat()

    def finish(self, offers: Mapping[str, WatchSpec]) -> None:
        proofs = []
        if self.started is not None:
            revision, controls, observed, until, floor = self.started
            now = self.clock.now()
            if (
                revision == epoch(self.conn)
                and observed <= now < until
                and floor == self.config.history_start.isoformat()
                and self.conn.execute("SELECT revision FROM control_state").fetchone()[0]
                == controls
            ):
                for identifier in self.watches:
                    spec = offers.get(identifier)
                    if (
                        spec is not None
                        and spec.archive_url
                        and len(spec.archive_url) <= 8192
                        and not spec.form
                    ):
                        proofs.append(
                            ArchiveOfferProof(
                                self.conn,
                                spec,
                                self.run_id,
                                revision,
                                controls,
                                observed,
                                until,
                                floor,
                            )
                        )
        self.accept(tuple(proofs))
