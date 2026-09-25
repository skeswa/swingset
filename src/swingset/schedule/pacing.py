"""Pacing of the two global projections (D-0199, D-0210).

`project/history` waits `history_interval_seconds` after it last
materialized. `project/inventory` waits `inventory_interval_seconds` too,
because each inventory generation re-mints the map, every event and source
event, history and every link. A due history lifts the inventory's wait only
once history has consumed the inventory it has: history needs a current
inventory, and re-running one whose cascade history has not reached yet
starts that cascade over. An inventory made at or after history came due
started a cascade that a dancer change has since interrupted; from then on
the inventory runs whenever it is stale until history lands, so a dancer
change in every cascade cannot starve history. A cutoff counts as a
materialization (D-0205). A calendar replay the inventory has not read, or a
calendar generation newer than the inventory's output, lifts the wait: either
rewrote live rows the inventory enriched (F2).

The cycle names its scheduler for the run (`configured`). Readers outside
scheduling then ask `holds` whether the gate holds a unit on purpose: the
phase-two acquisition gate reads the committed event list instead of waiting
for an inventory the gate holds (I5). Outside a configured run nothing is
held, so those readers wait for every pending inventory, as before D-0210.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta

from swingset.schedule.fair_policy import SchedulerConfig
from swingset.state.work import WorkUnit

HISTORY = WorkUnit("project", "history", "all")
INVENTORY = WorkUnit("project", "inventory", "all")

_ACTIVE: ContextVar[tuple[SchedulerConfig, Callable[[], datetime]] | None] = ContextVar(
    "swingset_pacing", default=None
)


def _at(conn: sqlite3.Connection, unit: WorkUnit) -> datetime | None:
    from swingset.state.derivations import materialized_at

    stamp = materialized_at(conn, unit)
    return datetime.fromisoformat(stamp) if stamp is not None else None


def deferred(
    conn: sqlite3.Connection, scheduler: SchedulerConfig, unit: WorkUnit, *, now: datetime
) -> bool:
    """Whether a paced global projection waits: it materialized within its interval.

    The inventory waits unless history is due and has consumed it (history
    materialized at or after it). While history has not, a dancer change
    during the cascade does not start the cascade over: the inventory keeps
    its interval, and history runs after the next one. A history whose latest
    attempt failed is not due here, so one that keeps failing cannot unpace
    the inventory; `[unit-failing]` names it.
    """
    if unit.stage != "project" or unit.unit_kind not in {"history", "inventory"}:
        return False

    def recent(target: WorkUnit, interval: float | None) -> bool:
        if interval is None:
            return False
        at = _at(conn, target)
        return at is not None and (now - at).total_seconds() < interval

    if unit.unit_kind == "history":
        return recent(unit, scheduler.history_interval_seconds)
    if not recent(unit, scheduler.inventory_interval_seconds):
        return False
    if calendar_replayed(conn, unit) or calendar_regenerated(conn, unit):
        return False
    if scheduler.history_interval_seconds is None or recent(
        HISTORY, scheduler.history_interval_seconds
    ):
        return True
    from swingset.state.attempts import latest_attempt

    tried = latest_attempt(conn, HISTORY)
    if tried is not None and tried["outcome"] == "transient":
        return True
    consumed, made = _at(conn, HISTORY), _at(conn, unit)
    if consumed is None or made is None:
        return True
    if consumed >= made:
        return False
    return made < consumed + timedelta(seconds=scheduler.history_interval_seconds)


def calendar_replayed(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    """Whether the calendar's replay epoch moved past the one `unit` last read.

    A calendar replay rewrites live `events` columns the inventory enriched
    (finding F2). Pacing must not hold that inventory: until it runs, the
    phase-two gate and `accept-year`, which read the committed event list
    while the gate holds it, would read calendar-only values (D-0210).
    The epoch read is the highest any run of the current output recorded:
    its generation and every equivalence to it.
    """
    from swingset.state.derivations import canonical

    try:
        now = conn.execute(
            "SELECT version FROM derivation_input_versions "
            "WHERE namespace='replayed' AND input_key=?",
            (canonical(("project", "calendar")),),
        ).fetchone()
        if now is None:
            return False
        rows = conn.execute(
            "SELECT d.manifest_json FROM derivation_scopes s "
            "JOIN derivation_generations g ON g.generation_id=s.materialized_generation_id "
            "JOIN derivation_dependency_sets d ON d.dependency_set_id=g.dependency_set_id "
            "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=? "
            "UNION ALL SELECT d.manifest_json FROM derivation_scopes s "
            "JOIN derivation_equivalences e ON e.stage=s.stage AND e.unit_kind=s.unit_kind "
            "AND e.unit_id=s.unit_id AND e.generation_id=s.materialized_generation_id "
            "JOIN derivation_dependency_sets d ON d.dependency_set_id=e.dependency_set_id "
            "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=?",
            (unit.stage, unit.unit_kind, unit.unit_id) * 2,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False
        raise
    import json

    read = 0
    for (manifest,) in rows:
        for entry in json.loads(manifest):
            if (
                isinstance(entry, dict)
                and entry.get("kind") == "replay"
                and entry.get("key") == ["project", "calendar"]
            ):
                read = max(read, int(entry.get("version", 0)))
    return int(now[0]) > read


def calendar_regenerated(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    """Whether a calendar minted a generation after `unit`'s output was made.

    A new calendar generation rewrites its live `events` rows through
    `replace_scope`, dropping the inventory's enrichment just as a replay
    does, so pacing must not hold the inventory either. `wsdc-history`
    writes no rows (project/process._replace_calendar).
    """
    made = _at(conn, unit)
    if made is None:
        return False
    for (stamp,) in conn.execute(
        "SELECT g.created_at FROM derivation_scopes s JOIN derivation_generations g "
        "ON g.generation_id=s.materialized_generation_id "
        "WHERE s.stage='project' AND s.unit_kind='calendar' AND s.unit_id!='wsdc-history'"
    ):
        if datetime.fromisoformat(str(stamp)) > made:
            return True
    return False


@contextmanager
def configured(scheduler: SchedulerConfig, now: Callable[[], datetime]) -> Iterator[None]:
    """Name the scheduler and clock whose gates `holds` answers for, for the block."""
    token = _ACTIVE.set((scheduler, now))
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def holds(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    """Whether the configured run's gate holds `unit`; False outside one.

    Only a unit with work to do is held: one that is not current. A current
    unit made within its interval holds nothing back, so the units after it
    can run now and a reader must wait for them. A unit whose recipe cannot
    be formed counts as not current, as in `derivations.pending_units`.
    """
    active = _ACTIVE.get()
    if active is None:
        return False
    scheduler, now = active
    if not deferred(conn, scheduler, unit, now=now()):
        return False
    from swingset.state.derivations import current
    from swingset.state.recipes import RecipeScopeMissing

    try:
        return not current(conn, unit)
    except RecipeScopeMissing:
        return True
