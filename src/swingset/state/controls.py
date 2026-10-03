"""One durable operator control plane for requests and bounded repair actions."""

from __future__ import annotations

import contextlib
import sqlite3
import time
import uuid
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from swingset.clock import Clock

from .control_lock import ControlTimeout, control_lock
from .db import SCHEMA_VERSION, Database
from .publication_fence import PUBLICATION_KINDS

#: An admission waits this long for the control lock before `ControlTimeout`.
ADMISSION_TIMEOUT_SECONDS = 90.0
#: Leased work carries no admission (D-0238): a unit's lease and running
#: attempt are its in-flight record. `project` stays a kind for the stage
#: operations (`event_*`, CDX refreshes) under their own action ids.
UNIT_KINDS = frozenset({"parse", "link"})
#: The action-id prefix of the unit admissions schema 37 deleted.
UNIT_ACTION_PREFIX = "work_"
#: Settled ledger rows older than this are pruned by maintain, except these
#: kinds: a publication is the fence's record. Of builds, the newest admitted
#: and the newest completed stay: `build_deferred_for_interval` and
#: `release_lag` read only those.
PRUNE_AGE_DAYS = 30
KEPT_KINDS = PUBLICATION_KINDS
#: Ledger rows deleted per prune transaction.
PRUNE_BATCH = 5000
#: Resolves a leased unit's dependencies (`control_scopes.for_unit`). It is
#: passed in, not imported: `control_scopes` is in no recipe scope, and this
#: module is in the project, link and build scopes.
UnitScope = Callable[[sqlite3.Connection, Any], "ActionScope"]

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
    unit_scope: UnitScope | None = None,
) -> dict[str, Any]:
    """Persist a control without acquiring the whole-command data writer lock.

    The connection never migrates, creates state, or changes repair evidence.
    Its authorizer allows writes only to the existing control tables.
    ``unit_scope`` resolves leased units for the receipt's drain (`status`).
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
            conn.commit()
        except sqlite3.OperationalError as exc:
            conn.rollback()
            conn.close()
            if "locked" in str(exc) or "busy" in str(exc):
                raise ControlTimeout(
                    "control servicing timed out; no change was persisted"
                ) from exc
            raise
        except BaseException:
            conn.rollback()
            conn.close()
            raise
        # The drain report reads after the commit, in its own read
        # transaction: resolving leased units' scopes never holds the write
        # lock, and a failed read never undoes a committed pause.
        try:
            conn.execute("BEGIN")
            result = status(conn, now=now, selector=selector, unit_scope=unit_scope)
        except (sqlite3.Error, ValueError) as exc:
            result = {"status_error": f"{type(exc).__name__}: {exc}"[:300]}
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
        result.update(
            action="pause" if paused else "resume",
            persisted=True,
            changed=changed,
            pause_id=identifier,
        )
        return result


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
    timeout: float = ADMISSION_TIMEOUT_SECONDS,
    validate: Callable[[sqlite3.Connection], None] | None = None,
) -> Iterator[sqlite3.Connection]:
    """Serialize the pause check and the caller's issuance writes.

    Another process may hold the control lock while its admitted write waits
    for SQLite, so the wait here outlasts a busy timeout (D-0215). A leased
    unit is never admitted (D-0238): `derive_one` checks its pauses in the
    transaction that begins its attempt.
    """
    if (
        action_kind in UNIT_KINDS
        or action_id.startswith(UNIT_ACTION_PREFIX)
        or work_attempt_id is not None
    ):
        raise ValueError("leased units carry no admission")
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
    forget: bool = False,
) -> None:
    """Join the output or reconciled receipt transaction; never acquire control.lock.

    ``forget`` removes the settled admission and its scope rows in the same
    transaction. Settled rows are otherwise pruned by maintain after 30 days
    (`prune`), publications and the newest builds kept (D-0238).
    """
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
    if forget and not uncertain:
        conn.execute("DELETE FROM execution_dependencies WHERE action_id=?", (action_id,))
        conn.execute("DELETE FROM execution_admissions WHERE action_id=?", (action_id,))


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

    if action_kind in PUBLICATION_KINDS:
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
    """After exclusive writer ownership, settle abandoned request and operation rows.

    The exclusive-open safety of migrate and restore (D-0238). A publication
    may have committed remotely before process death. It remains uncertain
    until its particular candidate receipt is reconciled.
    """
    if not conn.in_transaction:
        raise ValueError("admission recovery requires a transaction")
    rows = conn.execute(
        "SELECT action_id,action_kind FROM execution_admissions WHERE state='active'"
    ).fetchall()

    for row in rows:
        publication = row["action_kind"] in PUBLICATION_KINDS
        settle(
            conn,
            row["action_id"],
            now=now,
            outcome="receipt_reconciliation_required" if publication else "process_interrupted",
            uncertain=publication,
        )
    return len(rows)


def prune(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    batch: int = PRUNE_BATCH,
) -> int:
    """Delete one batch of settled ledger rows older than 30 days (D-0238).

    Publications stay, and so do the newest admitted build and the newest
    completed one, which `build_deferred_for_interval` and `release_lag`
    read. A row another table still names (`scheduler_requests`,
    `history_origin_requests`) stays: the scheduler's quota reads those.
    Inside the caller's transaction; returns rows deleted.
    """
    if not conn.in_transaction:
        raise ValueError("ledger pruning requires a transaction")
    cutoff = _at(now - timedelta(days=PRUNE_AGE_DAYS))
    kept = ",".join(f"'{kind}'" for kind in sorted(KEPT_KINDS))
    referenced = " ".join(
        f"AND NOT EXISTS(SELECT 1 FROM {table} r WHERE r.action_id=a.action_id)"
        for table in _referencing_tables(conn)
    )
    newest_builds = [
        str(row[0])
        for row in conn.execute(
            "SELECT * FROM (SELECT action_id FROM execution_admissions WHERE action_kind='build' "
            "ORDER BY admitted_at DESC,action_id DESC LIMIT 1) UNION SELECT * FROM "
            "(SELECT action_id FROM execution_admissions WHERE action_kind='build' "
            "AND outcome='completed' ORDER BY settled_at DESC,action_id DESC LIMIT 1)"
        )
    ]
    # Never `NOT IN (NULL)`: that matches no row.
    newest = (
        f"AND a.action_id NOT IN ({','.join('?' * len(newest_builds))})" if newest_builds else ""
    )
    ids = [
        str(row[0])
        for row in conn.execute(
            "SELECT a.action_id FROM execution_admissions a WHERE a.state='settled' "
            f"AND a.settled_at < ? AND a.action_kind NOT IN ({kept}) "
            f"{newest} {referenced} LIMIT ?",
            (cutoff, *newest_builds, batch),
        )
    ]
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM execution_dependencies WHERE action_id IN ({marks})", chunk)
        conn.execute(f"DELETE FROM execution_admissions WHERE action_id IN ({marks})", chunk)
    return len(ids)


def _referencing_tables(conn: sqlite3.Connection) -> list[str]:
    """Tables other than the scope rows whose foreign key names an admission."""
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name<>'execution_dependencies'"
        )
    ]
    return [
        table
        for table in tables
        if any(
            fk["table"] == "execution_admissions"
            for fk in conn.execute(f"PRAGMA foreign_key_list('{table}')")
        )
    ]


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


def _leased_units(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """Leased work units as drain rows: a unit's lease is its in-flight record (D-0238).

    `admitted_at` is the running attempt's start, else the lease's. `live`
    follows `queue.lease_dead`: a lease drains while `lease_until` is ahead
    of `now`. An expired lease belongs to a dead process; the derive loop's
    next tick takes it over, and until then it neither drains nor is stuck.
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='work_queue'").fetchone() is None:
        return []
    from .queue import at as queue_at

    stamp = queue_at(now)
    rows = conn.execute(
        "SELECT q.stage,q.unit_kind,q.unit_id,q.lease_owner,q.lease_until,q.updated_at,"
        "a.attempt_id,a.run_id,a.started_at FROM work_queue q LEFT JOIN work_attempts a "
        "ON a.attempt_id=(SELECT max(w.attempt_id) FROM work_attempts w WHERE w.stage=q.stage "
        "AND w.unit_kind=q.unit_kind AND w.unit_id=q.unit_id AND w.outcome='running') "
        "WHERE q.state='leased' ORDER BY q.stage,q.unit_kind,q.unit_id"
    ).fetchall()
    return [
        {
            "action_id": f"lease:{row['stage']}/{row['unit_kind']}/{row['unit_id']}",
            "action_kind": row["stage"],
            "state": "leased",
            "admitted_at": row["started_at"] or row["updated_at"],
            "run_id": row["run_id"],
            "work_attempt_id": row["attempt_id"],
            "stage": row["stage"],
            "unit_kind": row["unit_kind"],
            "unit_id": row["unit_id"],
            "lease_owner": row["lease_owner"],
            "lease_until": row["lease_until"],
            "live": row["lease_owner"] is not None
            and row["lease_until"] is not None
            and str(row["lease_until"]) > stamp,
        }
        for row in rows
    ]


def _drain_age(
    row: sqlite3.Row | dict[str, Any], pauses: list[dict[str, Any]], now: datetime
) -> float:
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
    unit_scope: UnitScope | None = None,
) -> dict[str, Any]:
    """A consistent read-only view; expiry remains visible without mutation.

    Draining work is the unsettled admissions and the leased units (D-0238).
    ``unit_scope`` gives a lease its dependencies; without it every lease
    counts against every pause, so a wait never ends early.
    """
    if not _supported(conn):
        return {
            "supported": False,
            "state": "running",
            "control_revision": 0,
            "pauses": [],
            "draining_attempts": [],
            "dead_leases": [],
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

    def drain(row: dict[str, Any], action_scope: ActionScope) -> None:
        nonlocal active
        if not _selected_action(action_scope, selector):
            return
        active += 1
        matches = [pause["pause_id"] for pause in pauses if _matches(pause, action_scope)]
        if not matches:
            return
        age = max(0.0, (now - datetime.fromisoformat(row["admitted_at"])).total_seconds())
        drain_age = _drain_age(
            row, [pause for pause in pauses if pause["pause_id"] in matches], now
        )
        draining.append(
            row
            | {
                "pause_ids": matches,
                "age_seconds": age,
                "drain_age_seconds": drain_age,
                "stuck": drain_age > drain_bound_seconds,
                "sources": sorted(action_scope.sources),
                "kinds": sorted(action_scope.kinds),
            }
        )

    for row in conn.execute(
        "SELECT * FROM execution_admissions WHERE state<>'settled' ORDER BY admitted_at,action_id"
    ).fetchall():
        drain(dict(row), _action_scope(conn, row))
    dead_leases = []
    for row in _leased_units(conn, now):
        from .work import WorkUnit

        if not row.pop("live"):
            dead_leases.append(
                {
                    key: row[key]
                    for key in ("action_id", "stage", "unit_kind", "unit_id", "run_id")
                    + ("lease_owner", "lease_until")
                }
            )
            continue
        unit = WorkUnit(row["stage"], row["unit_kind"], row["unit_id"])
        unit_action = ActionScope(all_sources=True, all_kinds=True)
        if unit_scope is not None:
            # One unit's dynamic scope must not break the brake (I1): a
            # resolver that raises counts the lease against every pause.
            try:
                unit_action = unit_scope(conn, unit)
            except Exception as exc:
                row["scope_error"] = f"{type(exc).__name__}: {exc}"[:300]
        drain(row, unit_action)
    draining.sort(
        key=lambda row: (datetime.fromisoformat(str(row["admitted_at"])), str(row["action_id"]))
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
        "dead_leases": dead_leases,
        "expired_pauses": expired,
        "snapshot_at": _at(now),
    }
