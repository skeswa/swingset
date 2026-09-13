"""One durable operator control plane for requests and bounded repair actions."""

from __future__ import annotations

import contextlib
import sqlite3
import time
import uuid
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from swingset.clock import Clock

from .control_lock import ControlTimeout, control_lock
from .db import SCHEMA_VERSION, Database

BUILTIN_KINDS = frozenset(
    {
        "round_observations",
        "source_event_mapping",
        "source_id_checked",
        "registry_event_association",
        "first_point_reconsideration",
        "archive_artifact",
        "work_attempt",
        "admission_blocked",
        "parse_failure",
    }
)


@dataclass(frozen=True)
class Selector:
    scope_kind: str
    scope_id: str


@dataclass(frozen=True)
class ActionScope:
    sources: frozenset[str] = frozenset()
    kinds: frozenset[str] = frozenset()
    host: str | None = None
    all_sources: bool = False
    all_kinds: bool = False


class ControlPaused(RuntimeError):
    def __init__(self, pauses: list[dict[str, Any]]) -> None:
        self.pauses = pauses
        super().__init__(
            "operator control holds this action: " + ", ".join(str(p["pause_id"]) for p in pauses)
        )


def _at(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("control timestamps require a timezone")
    return now.astimezone(UTC).isoformat()


def _supported(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='control_state'").fetchone()
        is not None
    )


def registered_selectors(
    conn: sqlite3.Connection, *, sources: Collection[str] = (), hosts: Collection[str] = ()
) -> dict[str, list[str]]:
    from swingset.sources import sources as registered_sources

    known_sources = {source.name for source in registered_sources()} | set(sources)
    known_sources.update(str(row[0]) for row in conn.execute("SELECT DISTINCT source FROM watches"))
    known_hosts = set(hosts) | {str(row[0]) for row in conn.execute("SELECT host FROM hosts")}
    for row in conn.execute("SELECT DISTINCT url FROM watches"):
        if hostname := urlparse(str(row[0])).hostname:
            known_hosts.add(hostname)
    kinds = set(BUILTIN_KINDS) | {
        str(row[0]) for row in conn.execute("SELECT DISTINCT kind FROM findings")
    }
    # Existing controls must remain resumable after a configured source retires.
    values = {"source": known_sources, "host": known_hosts, "kind": kinds, "all": {"all"}}
    for kind, identifier in conn.execute("SELECT scope_kind,scope_id FROM operator_pauses"):
        if kind in values:
            values[kind].add(identifier)
    return {key: sorted(value) for key, value in values.items()}


def _matches(pause: dict[str, Any], scope: ActionScope) -> bool:
    kind, identifier = pause["scope_kind"], pause["scope_id"]
    return (
        kind == "all"
        or kind == "host"
        and scope.host == identifier
        or kind == "source"
        and (scope.all_sources or identifier in scope.sources)
        or kind == "kind"
        and (scope.all_kinds or identifier in scope.kinds)
    )


def matching_pauses(
    conn: sqlite3.Connection, scope: ActionScope | None, *, now: datetime
) -> list[dict[str, Any]]:
    result = []
    for row in conn.execute("SELECT * FROM operator_pauses ORDER BY scope_kind,scope_id"):
        pause = dict(row)
        if pause["until_at"] is not None and datetime.fromisoformat(pause["until_at"]) <= now:
            continue
        if scope is None or _matches(pause, scope):
            pause["resume_selector"] = f"--{pause['scope_kind']}" + (
                "" if pause["scope_kind"] == "all" else f" {pause['scope_id']}"
            )
            pause["paused_duration_seconds"] = (
                max(0.0, (now - datetime.fromisoformat(pause["created_at"])).total_seconds())
                if pause.get("created_at")
                else None
            )
            result.append(pause)
    return result


def _event(
    conn: sqlite3.Connection,
    pause: dict[str, Any],
    action: str,
    actor: str,
    reason: str,
    now: datetime,
) -> int:
    conn.execute("UPDATE control_state SET revision=revision+1 WHERE singleton=1")
    revision = int(conn.execute("SELECT revision FROM control_state").fetchone()[0])
    conn.execute(
        "INSERT INTO control_events(control_revision,pause_id,scope_kind,scope_id,action,actor,reason,until_at,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            revision,
            pause["pause_id"],
            pause["scope_kind"],
            pause["scope_id"],
            action,
            actor,
            reason,
            pause["until_at"],
            _at(now),
        ),
    )
    return revision


def expire(conn: sqlite3.Connection, *, now: datetime) -> None:
    """Record expiry at a mutation boundary; read-only status never erases history."""
    if not conn.in_transaction:
        raise ValueError("control expiry requires a transaction")
    for row in conn.execute("SELECT * FROM operator_pauses WHERE until_at IS NOT NULL").fetchall():
        if datetime.fromisoformat(row["until_at"]) <= now:
            _event(conn, dict(row), "expire", "system:clock", "timed pause expired", now)
            conn.execute("DELETE FROM operator_pauses WHERE pause_id=?", (row["pause_id"],))


def change_control(
    state_path: str | Path,
    *,
    selector: Selector,
    paused: bool,
    actor: str,
    reason: str,
    now: datetime,
    until: datetime | None = None,
    timeout: float = 60,
    sources: Collection[str] = (),
    hosts: Collection[str] = (),
) -> dict[str, Any]:
    """Persist a control without acquiring the whole-command data writer lock.

    The connection never migrates, creates state, or changes repair evidence.
    Its authorizer allows writes only to the existing control tables.
    """
    _at(now)
    if not actor.strip() or not reason.strip():
        raise ValueError("control changes require an actor and reason")
    if until is not None and (not paused or datetime.fromisoformat(_at(until)) <= now):
        raise ValueError("timed pauses require a future expiry")
    requested = Path(state_path)
    database_path = (
        requested if requested.suffix in {".sqlite", ".db"} else requested / "state.sqlite"
    )
    state_dir = database_path.parent
    if (state_dir / "checkpoint.json").exists():
        raise ValueError("checkpoint state is immutable")
    started = time.monotonic()
    with control_lock(state_dir, timeout=timeout):
        if (state_dir / "RESTORE_PENDING").exists():
            raise RuntimeError(f"restore verification is pending: {state_dir}")
        if (state_dir / "checkpoint.json").exists():
            raise ValueError("checkpoint state is immutable")
        remaining = max(0, timeout - (time.monotonic() - started))
        conn = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=rw",
            uri=True,
            isolation_level=None,
            timeout=remaining,
        )
        conn.row_factory = sqlite3.Row
        try:
            if not 12 <= int(conn.execute("PRAGMA user_version").fetchone()[0]) <= SCHEMA_VERSION:
                raise RuntimeError(
                    "controls require a supported schema at least 12; this command does not migrate state"
                )
            conn.execute("PRAGMA synchronous=FULL")
            writable = {"operator_pauses", "control_events", "control_state"}

            def authorize(
                action: int,
                table: str | None,
                column: str | None,
                database: str | None,
                trigger: str | None,
            ) -> int:
                if (
                    action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
                    and table not in writable
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(authorize)
            conn.execute("BEGIN IMMEDIATE")
            allowed = registered_selectors(conn, sources=sources, hosts=hosts)
            if selector.scope_id not in allowed.get(selector.scope_kind, []):
                raise ValueError(
                    f"unknown control selector: {selector.scope_kind} {selector.scope_id}"
                )
            expire(conn, now=now)
            old = conn.execute(
                "SELECT * FROM operator_pauses WHERE scope_kind=? AND scope_id=?",
                (selector.scope_kind, selector.scope_id),
            ).fetchone()
            identifier = old["pause_id"] if old else None
            changed = False
            if paused:
                expiry = _at(until) if until else None
                if old is None or (old["reason"], old["actor"], old["until_at"]) != (
                    reason,
                    actor,
                    expiry,
                ):
                    identifier = identifier or "pause_" + uuid.uuid4().hex
                    pause = {
                        "pause_id": identifier,
                        "scope_kind": selector.scope_kind,
                        "scope_id": selector.scope_id,
                        "until_at": expiry,
                    }
                    revision = _event(conn, pause, "pause", actor, reason, now)
                    conn.execute(
                        "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason,pause_id,actor,control_revision,created_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET until_at=excluded.until_at,reason=excluded.reason,actor=excluded.actor,control_revision=excluded.control_revision",
                        (
                            selector.scope_kind,
                            selector.scope_id,
                            expiry,
                            reason,
                            identifier,
                            actor,
                            revision,
                            _at(now),
                        ),
                    )
                    changed = True
            elif old is not None:
                _event(conn, dict(old), "resume", actor, reason, now)
                conn.execute("DELETE FROM operator_pauses WHERE pause_id=?", (identifier,))
                changed = True
            result = status(conn, now=now, selector=selector)
            result.update(
                action="pause" if paused else "resume",
                persisted=True,
                changed=changed,
                pause_id=identifier,
            )
            conn.commit()
            return result
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if "locked" in str(exc) or "busy" in str(exc):
                raise ControlTimeout(
                    "control servicing timed out; no change was persisted"
                ) from exc
            raise
        finally:
            conn.close()


@contextlib.contextmanager
def admission(
    database: Database,
    *,
    action_id: str,
    action_kind: str,
    scope: ActionScope,
    now: datetime,
    run_id: str | None = None,
    work_attempt_id: int | None = None,
    candidate_id: str | None = None,
    timeout: float = 60,
    validate: Callable[[sqlite3.Connection], None] | None = None,
) -> Iterator[sqlite3.Connection]:
    """Serialize the pause check, attempt registration and caller's issuance writes."""
    if database.connection.in_transaction:
        raise ValueError("admission must precede the unit's write transaction")
    blocked: list[dict[str, Any]] = []
    with control_lock(database.state_dir, timeout=timeout):
        with database.transaction() as conn:
            if validate is not None:
                validate(conn)
            expire(conn, now=now)
            blocked = matching_pauses(conn, scope, now=now)
            if not blocked:
                revision = conn.execute("SELECT revision FROM control_state").fetchone()[0]
                conn.execute(
                    "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,run_id,work_attempt_id,candidate_id,host,all_sources,all_kinds,state) VALUES (?,?,?,?,?,?,?,?,?,?,'active')",
                    (
                        action_id,
                        action_kind,
                        _at(now),
                        revision,
                        run_id,
                        work_attempt_id,
                        candidate_id,
                        scope.host,
                        int(scope.all_sources),
                        int(scope.all_kinds),
                    ),
                )
                conn.executemany(
                    "INSERT INTO execution_dependencies VALUES (?,?,?)",
                    [
                        (action_id, kind, value)
                        for kind, values in (("source", scope.sources), ("kind", scope.kinds))
                        for value in sorted(values)
                    ],
                )
                yield conn
        if blocked:
            raise ControlPaused(blocked)


def settle(
    conn: sqlite3.Connection,
    action_id: str,
    *,
    now: datetime,
    outcome: str,
    uncertain: bool = False,
) -> None:
    """Join the output or reconciled receipt transaction; never acquire control.lock."""
    if not conn.in_transaction:
        raise ValueError("admission settlement requires the output transaction")
    cursor = conn.execute(
        "UPDATE execution_admissions SET state=?,settled_at=?,outcome=? WHERE action_id=? AND state<>'settled'",
        (
            "uncertain" if uncertain else "settled",
            None if uncertain else _at(now),
            outcome,
            action_id,
        ),
    )
    if (
        cursor.rowcount == 0
        and not conn.execute(
            "SELECT 1 FROM execution_admissions WHERE action_id=?", (action_id,)
        ).fetchone()
    ):
        raise ValueError("unknown execution admission")


def bind_work_attempt(conn: sqlite3.Connection, action_id: str, attempt_id: int) -> None:
    """Link the H12 attempt allocated inside the admission transaction."""
    if not conn.in_transaction:
        raise ValueError("attempt binding requires the admission transaction")
    cursor = conn.execute(
        "UPDATE execution_admissions SET work_attempt_id=? WHERE action_id=? AND state='active' AND work_attempt_id IS NULL",
        (attempt_id, action_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("admission is not active or already owns an attempt")


@contextlib.contextmanager
def operation(
    database: Database,
    *,
    action_id: str,
    action_kind: str,
    scope: ActionScope,
    clock: Clock,
    run_id: str | None = None,
    candidate_id: str | None = None,
) -> Iterator[None]:
    """Track a local/request lifecycle whose body owns its bounded transactions.

    This wrapper does not make multiple body commits atomic. Publication uses
    explicit receipt settlement so a lost response remains uncertain.
    """
    if action_kind in {"publish", "publication"}:
        raise ValueError("publication requires explicit receipt settlement")
    with admission(
        database,
        action_id=action_id,
        action_kind=action_kind,
        scope=scope,
        now=clock.now(),
        run_id=run_id,
        candidate_id=candidate_id,
    ):
        pass
    try:
        yield
    except (Exception, KeyboardInterrupt) as exc:
        with database.transaction() as conn:
            settle(
                conn,
                action_id,
                now=clock.now(),
                outcome="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            )
        raise
    else:
        with database.transaction() as conn:
            settle(conn, action_id, now=clock.now(), outcome="completed")


def recover_admissions(conn: sqlite3.Connection, *, now: datetime) -> int:
    """After exclusive writer ownership, settle abandoned local/request work.

    A publication may have committed remotely before process death. It remains
    uncertain until its particular candidate receipt is reconciled.
    """
    if not conn.in_transaction:
        raise ValueError("admission recovery requires a transaction")
    rows = conn.execute(
        "SELECT a.*,w.outcome AS work_outcome FROM execution_admissions a LEFT JOIN work_attempts w ON w.attempt_id=a.work_attempt_id WHERE a.state='active'"
    ).fetchall()
    for row in rows:
        publication = row["action_kind"] in {"publish", "publication"}
        outcome = (
            row["work_outcome"]
            if row["work_outcome"] not in {None, "running"}
            else "process_interrupted"
        )
        settle(
            conn,
            row["action_id"],
            now=now,
            outcome="receipt_reconciliation_required" if publication else outcome,
            uncertain=publication,
        )
    return len(rows)


def _action_scope(conn: sqlite3.Connection, row: sqlite3.Row) -> ActionScope:
    dependencies = list(
        conn.execute(
            "SELECT scope_kind,scope_id FROM execution_dependencies WHERE action_id=?",
            (row["action_id"],),
        )
    )
    return ActionScope(
        frozenset(r[1] for r in dependencies if r[0] == "source"),
        frozenset(r[1] for r in dependencies if r[0] == "kind"),
        row["host"],
        bool(row["all_sources"]),
        bool(row["all_kinds"]),
    )


def _drain_age(row: sqlite3.Row, pauses: list[dict[str, Any]], now: datetime) -> float:
    admitted = datetime.fromisoformat(row["admitted_at"])
    starts = [
        max(datetime.fromisoformat(pause["created_at"]), admitted)
        for pause in pauses
        if pause.get("created_at")
    ]
    return max(0.0, (now - min(starts, default=admitted)).total_seconds())


def _selected_action(scope: ActionScope, selector: Selector | None) -> bool:
    if selector is None or selector.scope_kind == "all":
        return True
    return _matches({"scope_kind": selector.scope_kind, "scope_id": selector.scope_id}, scope)


def status(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    scope: ActionScope | None = None,
    selector: Selector | None = None,
    drain_bound_seconds: float = 60,
) -> dict[str, Any]:
    """A consistent read-only view; expiry remains visible without mutation."""
    if not _supported(conn):
        return {
            "supported": False,
            "state": "running",
            "control_revision": 0,
            "pauses": [],
            "draining_attempts": [],
            "stuck_drain": False,
            "snapshot_at": _at(now),
        }
    selected_scope = scope
    if selector is not None:
        selected_scope = ActionScope(
            sources=frozenset({selector.scope_id})
            if selector.scope_kind == "source"
            else frozenset(),
            kinds=frozenset({selector.scope_id}) if selector.scope_kind == "kind" else frozenset(),
            host=selector.scope_id if selector.scope_kind == "host" else None,
            all_sources=selector.scope_kind in {"all", "kind"},
            all_kinds=selector.scope_kind in {"all", "source"},
        )
        if selector.scope_kind == "all":
            selected_scope = None
    pauses = matching_pauses(conn, selected_scope, now=now)
    draining = []
    active = 0
    for row in conn.execute(
        "SELECT * FROM execution_admissions WHERE state<>'settled' ORDER BY admitted_at,action_id"
    ):
        action_scope = _action_scope(conn, row)
        if not _selected_action(action_scope, selector):
            continue
        active += 1
        matches = [pause["pause_id"] for pause in pauses if _matches(pause, action_scope)]
        if matches:
            age = max(0.0, (now - datetime.fromisoformat(row["admitted_at"])).total_seconds())
            drain_age = _drain_age(
                row, [pause for pause in pauses if pause["pause_id"] in matches], now
            )
            draining.append(
                dict(row)
                | {
                    "pause_ids": matches,
                    "age_seconds": age,
                    "drain_age_seconds": drain_age,
                    "stuck": drain_age > drain_bound_seconds,
                    "sources": sorted(action_scope.sources),
                    "kinds": sorted(action_scope.kinds),
                }
            )
    expired = [
        dict(row)
        for row in conn.execute("SELECT * FROM operator_pauses WHERE until_at IS NOT NULL")
        if datetime.fromisoformat(row["until_at"]) <= now
    ]
    return {
        "supported": True,
        "state": "pausing" if draining else "paused" if pauses else "running",
        "control_revision": int(conn.execute("SELECT revision FROM control_state").fetchone()[0]),
        "pauses": pauses,
        "draining_attempts": draining,
        "active_attempts": active,
        "stuck_drain": any(row["stuck"] for row in draining),
        "expired_pauses": expired,
        "snapshot_at": _at(now),
    }
