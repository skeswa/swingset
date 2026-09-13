"""Eligible demand shares request debits; offline work rotates independently.

Selections are read-only hints. The HTTP gate repeats the pressure check and
records service in the same transaction as its existing durable host debit.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Collection, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from swingset.config import Config
from swingset.history.acquisition import is_phase_one_index
from swingset.state.control_scopes import for_watch
from swingset.state.controls import ActionScope, matching_pauses
from swingset.state.work import WorkUnit, next_work

from .fair_policy import shares


@dataclass(frozen=True)
class WatchChoice:
    key: str
    watch_id: str | None
    host: str
    category: str
    scope: ActionScope
    due_at: datetime
    repair: bool = False
    archive: bool = False
    history: bool = False


@dataclass(frozen=True)
class _Service:
    choice: WatchChoice
    run_id: str


_service: ContextVar[_Service | None] = ContextVar("scheduler_service", default=None)


@contextmanager
def servicing(choice: WatchChoice, *, run_id: str) -> Iterator[None]:
    token = _service.set(_Service(choice, run_id))
    try:
        yield
    finally:
        _service.reset(token)


def backpressure(conn: sqlite3.Connection, config: Config) -> dict[str, Any]:
    counts = dict(conn.execute("SELECT stage,count(*) FROM pending_work GROUP BY stage"))
    size = conn.execute(
        "SELECT coalesce(sum(size),0) FROM (SELECT s.body_sha256,max(s.body_bytes) AS size "
        "FROM pending_work p JOIN snapshots s ON p.unit_id=s.snapshot_id "
        "WHERE p.stage='parse' GROUP BY s.body_sha256)"
    ).fetchone()[0]
    reasons = []
    if size >= config.scheduler.pending_parse_bytes:
        reasons.append("pending_parse_bytes")
    if counts.get("parse", 0) >= config.scheduler.pending_parse_items:
        reasons.append("pending_parse_items")
    if sum(counts.values()) >= config.scheduler.pending_work_items:
        reasons.append("pending_work_items")
    return {
        "active": bool(reasons),
        "reasons": reasons,
        "pending": counts,
        "pending_parse_bytes": size,
        "repair_requests_per_cycle": config.scheduler.repair_requests_per_cycle,
    }


def report(conn: sqlite3.Connection, config: Config, *, now: datetime) -> dict[str, Any]:
    """Separate initial objectives from observed debits; never migrate a doctor read."""
    supported = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='scheduler_requests'").fetchone()
        is not None
    )
    service = []
    if supported:
        service = [
            {
                "host": host,
                "category": category,
                "requests_today": requests,
                "body_bytes_today": size,
                "last_issued_at": last,
                "last_issued_wall_age_seconds": max(
                    0.0, (now - datetime.fromisoformat(last)).total_seconds()
                ),
            }
            for host, category, requests, size, last in conn.execute(
                "SELECT host,category,sum(day=?),sum(CASE WHEN day=? THEN body_bytes ELSE 0 END),max(issued_at) "
                "FROM scheduler_requests GROUP BY host,category",
                (now.date().isoformat(), now.date().isoformat()),
            )
        ]
    return {
        "supported": supported,
        "initial_objectives": {
            **asdict(config.scheduler),
            "host_class_percentages": {host: shares(host) for host in sorted(config.hosts)},
        },
        "observed_host_usage": [
            dict(row)
            for row in conn.execute(
                "SELECT host,day,requests,bytes FROM host_budget WHERE day=? ORDER BY host",
                (now.date().isoformat(),),
            )
        ],
        "observed_attributed_service": service if supported else None,
        "observed_offline_attempts": [
            dict(row)
            for row in conn.execute(
                "SELECT stage,unit_kind,attempts,last_served_at FROM scheduler_offline_service ORDER BY stage,unit_kind"
            )
        ]
        if supported
        else None,
        "recent_repair_reservation_runs": [
            dict(row)
            for row in conn.execute(
                "SELECT run_id,count(*) AS issued_requests,max(issued_at) AS last_issued_at FROM scheduler_requests "
                "WHERE pressure_reserved=1 GROUP BY run_id ORDER BY last_issued_at DESC LIMIT 20"
            )
        ]
        if supported
        else None,
        "attribution_note": "Issued HTTP only; pre-policy debits remain charged but unattributed. Wall ages include pauses and do not establish eligible service-gap performance.",
        "backpressure": backpressure(conn, config),
    }


def request_permitted(conn: sqlite3.Connection, config: Config) -> bool:
    """Called before the durable host debit, including for robots and redirects."""
    if not backpressure(conn, config)["active"]:
        return True
    service = _service.get()
    if service is None or not service.choice.repair:
        return False
    return _repair_capacity(conn, config, service.run_id)


def _repair_capacity(conn: sqlite3.Connection, config: Config, run_id: str) -> bool:
    used = conn.execute(
        "SELECT count(*) FROM scheduler_requests WHERE run_id=? AND pressure_reserved=1",
        (run_id,),
    ).fetchone()[0]
    return bool(used < config.scheduler.repair_requests_per_cycle)


def record_request(
    conn: sqlite3.Connection,
    config: Config,
    *,
    action_id: str,
    host: str,
    watch_id: str | None,
    source: str,
    now: datetime,
) -> None:
    if not conn.in_transaction:
        raise RuntimeError("scheduler request accounting requires the host debit transaction")
    service = _service.get()
    conn.execute(
        "INSERT INTO scheduler_requests(action_id,host,day,category,work_key,run_id,repair,pressure_reserved,issued_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            action_id,
            host,
            now.date().isoformat(),
            service.choice.category
            if service
            else ("identity" if source == "wsdc_registry" else "current"),
            service.choice.key if service else watch_id or source,
            service.run_id if service else None,
            bool(service and service.choice.repair),
            bool(service and service.choice.repair and backpressure(conn, config)["active"]),
            now.isoformat(),
        ),
    )


def _choices(conn: sqlite3.Connection, config: Config, now: datetime) -> Iterator[WatchChoice]:
    # Archive captures require the dispatcher's year/parent/capture offer. They
    # must not bypass that gate merely because a watch already exists.
    from swingset.history.origin_dispatch import available

    managed_origin = (
        {row[0] for row in conn.execute("SELECT watch_id FROM history_origin_intents")}
        if available(conn)
        else set()
    )
    rows = conn.execute(
        "SELECT w.*, EXISTS(SELECT 1 FROM snapshots s WHERE s.watch_id=w.watch_id) AS retained "
        "FROM watches w WHERE state NOT IN ('sealed','retired') AND next_check_at IS NOT NULL "
        "ORDER BY priority,next_check_at,watch_id",
    )
    for row in rows:
        if row["watch_id"] in managed_origin:
            continue
        if not config.enabled(row["source"]):
            continue
        if datetime.fromisoformat(row["next_check_at"]) > now:
            continue
        if row["archive_url"] and not is_phase_one_index(row["source"], row["parser"], row["kind"]):
            continue
        if row["paused_until"] and datetime.fromisoformat(row["paused_until"]) > now:
            continue
        host = urlsplit(row["archive_url"] or row["url"]).hostname
        if not host:
            continue
        if row["source"] == "wsdc_registry":
            category = (
                "old"
                if row["notes"] == "sweep"
                else "new"
                if row["notes"] == "probe"
                else "identity"
            )
        elif not row["retained"] and row["last_checked_at"] is None:
            category = "new"
        elif row["state"] in {"live", "upcoming", "cooling"}:
            category = "current"
        else:
            category = "old"
        # Ordinary newly discovered rounds are not repair capacity. Only a
        # retained, explicitly ready identity dependency receives this reserve.
        repair = (
            row["source"] == "wsdc_registry"
            and conn.execute(
                "SELECT 1 FROM findings WHERE closed_at IS NULL AND kind='source_id_checked' "
                "AND state='ready' AND (watch_id=? OR subject_id=?) LIMIT 1",
                (row["watch_id"], str(row["source_ref"] or "").removeprefix("wsdc:")),
            ).fetchone()
            is not None
        )
        yield WatchChoice(
            str(row["watch_id"]),
            str(row["watch_id"]),
            host,
            category,
            for_watch(conn, source=row["source"], watch_id=row["watch_id"], host=host),
            datetime.fromisoformat(row["next_check_at"]),
            repair=repair,
        )


def _delay(
    conn: sqlite3.Connection, config: Config, choice: WatchChoice, now: datetime, pressure: bool
) -> float | None:
    if choice.category not in shares(choice.host):
        raise ValueError(f"unknown scheduling category: {choice.category}")
    if any(not config.enabled(source) for source in choice.scope.sources):
        return None
    if matching_pauses(conn, choice.scope, now=now) or (pressure and not choice.repair):
        return None
    budget = conn.execute(
        "SELECT requests,bytes FROM host_budget WHERE host=? AND day=?",
        (choice.host, now.date().isoformat()),
    ).fetchone()
    policy = config.host(choice.host)
    # Fair scheduling never activates the separately authorized bootstrap sweep.
    if budget and (
        budget[0] >= policy.daily_request_budget
        or (policy.daily_byte_budget is not None and budget[1] >= policy.daily_byte_budget)
    ):
        return None
    delay = max(0.0, (choice.due_at - now).total_seconds())
    host = conn.execute(
        "SELECT next_allowed_at,paused_until FROM hosts WHERE host=?", (choice.host,)
    ).fetchone()
    if host:
        if host[1] and datetime.fromisoformat(host[1]) > now:
            return None
        if host[0]:
            delay = max(delay, (datetime.fromisoformat(host[0]) - now).total_seconds())
    return delay


def _eligible(
    conn: sqlite3.Connection,
    config: Config,
    now: datetime,
    exclude: Collection[str],
    extra_choices: Iterable[WatchChoice],
    run_id: str | None,
) -> Iterator[tuple[WatchChoice, float]]:
    pressure = bool(backpressure(conn, config)["active"])
    if pressure and run_id is not None and not _repair_capacity(conn, config, run_id):
        return
    seen = set(exclude)
    for choice in (*extra_choices, *_choices(conn, config, now)):
        if choice.key in seen:
            continue
        seen.add(choice.key)
        delay = _delay(conn, config, choice, now, pressure)
        if delay is not None:
            yield choice, delay


def next_watch(
    conn: sqlite3.Connection,
    config: Config,
    *,
    now: datetime,
    exclude: Collection[str] = (),
    extra_choices: Iterable[WatchChoice] = (),
    run_id: str | None = None,
) -> WatchChoice | None:
    candidates = [
        choice
        for choice, delay in _eligible(conn, config, now, exclude, extra_choices, run_id)
        if delay <= 0
    ]
    if not candidates:
        return None
    counts = {
        (str(host), str(category)): int(count)
        for host, category, count in conn.execute(
            "SELECT host,category,count(*) FROM scheduler_requests WHERE day=? GROUP BY host,category",
            (now.date().isoformat(),),
        )
    }
    services = {
        (str(host), str(category)): datetime.fromisoformat(at)
        for host, category, at in conn.execute(
            "SELECT host,category,max(issued_at) FROM scheduler_requests GROUP BY host,category"
        )
    }
    host_latest = {
        host: max(at for (h, _), at in services.items() if h == host) for host, _ in services
    }

    def rank(choice: WatchChoice) -> tuple[float, float, float, datetime, str]:
        last = services.get((choice.host, choice.category), choice.due_at)
        age = max(0.0, (now - last).total_seconds())
        overdue = age >= config.scheduler.service_gap_seconds
        weighted = counts.get((choice.host, choice.category), 0) / max(
            1, shares(choice.host)[choice.category]
        )
        host_age = (now - host_latest.get(choice.host, choice.due_at)).total_seconds()
        return (-age if overdue else 0, -host_age, weighted, choice.due_at, choice.key)

    return min(candidates, key=rank)


def next_delay(
    conn: sqlite3.Connection,
    config: Config,
    *,
    now: datetime,
    exclude: Collection[str] = (),
    extra_choices: Iterable[WatchChoice] = (),
    run_id: str | None = None,
) -> float | None:
    return min(
        (delay for _, delay in _eligible(conn, config, now, exclude, extra_choices, run_id)),
        default=None,
    )


def next_offline(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    allowed: Callable[[WorkUnit], bool] | None = None,
    exclude: Collection[WorkUnit] = (),
) -> WorkUnit | None:
    from swingset.state.derivation_query import query

    # All stage/kind candidates share one unchanged read snapshot. The cache
    # still invalidates on writes and is disabled in mutable transactions.
    with query(conn):
        return _next_offline(conn, now=now, allowed=allowed, exclude=exclude)


def _next_offline(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    allowed: Callable[[WorkUnit], bool] | None = None,
    exclude: Collection[WorkUnit] = (),
) -> WorkUnit | None:
    from swingset.state.derivations import available, known_units

    if available(conn):
        kinds = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT DISTINCT stage,unit_kind FROM pending_work WHERE stage='parse'"
            )
        }
        kinds.update(
            (unit.stage, unit.unit_kind)
            for stage in ("project", "link")
            for unit in known_units(conn, stage)
        )
        service = {
            (row[0], row[1]): row[2]
            for row in conn.execute(
                "SELECT stage,unit_kind,sequence FROM scheduler_offline_service"
            )
        }
        stage_order = {"parse": 0, "project": 1, "link": 2}
        kind_order = {
            "calendar": 0,
            "source_index": 0,
            "dancer": 0,
            "inventory": 1,
            "map": 2,
            "event": 3,
            "source_event": 4,
            "history": 5,
        }
        groups = [
            (stage, kind, service.get((stage, kind), 0))
            for stage, kind in sorted(
                kinds,
                key=lambda key: (
                    service.get(key, 0),
                    stage_order[key[0]],
                    kind_order.get(key[1], 0),
                    key[1],
                ),
            )
        ]
    else:
        groups = conn.execute(
            "SELECT DISTINCT p.stage,p.unit_kind,coalesce(s.sequence,0) AS seq FROM pending_work p "
            "LEFT JOIN scheduler_offline_service s ON p.stage=s.stage AND p.unit_kind=s.unit_kind "
            "WHERE p.stage IN ('parse','project','link') ORDER BY seq,"
            "CASE p.stage WHEN 'parse' THEN 0 WHEN 'project' THEN 1 ELSE 2 END,"
            "CASE p.unit_kind WHEN 'map' THEN 0 WHEN 'history' THEN 2 ELSE 1 END,p.unit_kind"
        ).fetchall()
    for stage, kind, _ in groups:

        def group_allowed(unit: WorkUnit, kind: str = kind) -> bool:
            return bool(unit.unit_kind == kind and (allowed is None or allowed(unit)))

        unit = next_work(
            conn, stage, now=now, exclude=exclude, allowed=group_allowed, unit_kind=kind
        )
        if unit:
            return unit
    return None


def record_offline_service(conn: sqlite3.Connection, unit: WorkUnit, *, now: datetime) -> None:
    conn.execute(
        "INSERT INTO scheduler_offline_service(stage,unit_kind,attempts,last_served_at,sequence) "
        "VALUES (?,?,1,?,(SELECT coalesce(max(sequence),0)+1 FROM scheduler_offline_service)) "
        "ON CONFLICT(stage,unit_kind) DO UPDATE SET attempts=attempts+1,last_served_at=excluded.last_served_at,sequence=excluded.sequence",
        (unit.stage, unit.unit_kind, now.isoformat()),
    )
