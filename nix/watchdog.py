"""Checks behind the swingset-watchdog unit (D-0188, D-0202, D-0203, D-0215, D-0219).

Prints one alert per line and nothing when all is well. Each line starts
with a stable key in brackets, such as `[stale-fetch]`. A key already logged
in ALERTS.log within `--repeat` seconds is not printed again; for the keys in
`SAME_TEXT_KEYS` only the same text is held back, so a new host repeats at
once. The unit's shell wrapper reads the timer, backup and summary unit states
and the disk quota, passes them in, and appends whatever this prints to ALERTS.log
with a UTC stamp. Standard library only: the unit runs it with the system
python3, not the project environment.

With `--loops 1` the worker runs the five loop timers instead of the cycle
(services.swingset.loops.enable): the cycle checks go quiet and the loop
checks (`[loop-quiet]`, `[loop-failing]`, `[loop-idle]`, `[plan-stale]`) read
the loops' run records, `loop_state` and plan rows instead.

Each check reads on its own. A check that raises is named in one
`[watchdog-blind]` line instead of silencing the checks after it, so the
watchdog never goes quiet because it could not look.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import struct
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import Any, NamedTuple, TypeVar

T = TypeVar("T")

# Copied from swingset.state.retention.SIZE_CAP_REASON; this script cannot
# import the package. tests/test_watchdog.py checks the two stay equal.
SIZE_CAP_REASON = "state database file is over its retention size cap"
# Copied from swingset.backup.pruning.TIMER_NAME: a checkpoint the backup
# timer wrote, as opposed to one an operator named. tests/test_watchdog.py
# checks the two stay equal.
TIMER_NAME = re.compile(r"^run_\d{8}T\d{6}Z(?:-\d+)?$")
#: A local checkpoint that failed its read-back is renamed with this prefix
#: (swingset.cli.FAILED_CHECKPOINT_PREFIX; the test checks the two match).
FAILED_PREFIX = "failed-"
#: Copied from swingset.cli.HOLD_OVERDUE_SECONDS: a hold older than this
#: must be visible (AGENTS.md, holds).
HOLD_OVERDUE_SECONDS = 48 * 3600
#: How much of the end of ALERTS.log the de-duplication reads.
LOG_TAIL_BYTES = 64 * 1024
#: Standing, informational lines repeat at most this often.
DAY = 86400
#: Keys whose text names what is wrong; a changed text repeats at once.
SAME_TEXT_KEYS = frozenset(
    {
        "gate-legacy-spacing",
        "gate-host-blocked",
        "host-failing",
        "backup-failed",
        "watchdog-blind",
        "unit-quarantined",
        "write-deadline",
        "guard-expired",
        "loop-quiet",
        "loop-failing",
        "loop-idle",
    }
)
#: A host whose last this many responses were all 429 or 5xx is failing.
#: Its backoff (fetch/politeness.py) is then two hours or more.
FAILING_STREAK = 4
#: [unit-quarantined] names units quarantined this recently, and repeats a
#: line no sooner, so each quarantine logs one line (D-0216).
QUARANTINE_WINDOW = 7200
#: Up to this many quarantined units get a line each; more get one line by
#: class, so a code bug that quarantines thousands cannot flood ALERTS.log.
QUARANTINE_LINES = 5
#: A lease this far past its end belongs to no live loop (D-0216).
LEASE_STUCK_SECONDS = 600
#: [write-deadline] reads run records finished this recently, and repeats a
#: line no sooner (D-0217).
WRITE_DEADLINE_WINDOW = 7200
#: At most this many of the newest run records are read for it.
WRITE_DEADLINE_RECORDS = 500
#: [guard-expired] names guards seen past their expiry this recently: a
#: reason that stopped being seen has cleared or is no longer tried (D-0219).
GUARD_SEEN_WINDOW = 7200
#: Up to this many expired guards get a line each; more get one line by reason.
GUARD_LINES = 5
#: With --loops 1: how long each loop may go without a finished tick while
#: unheld and its timer active, about three timer intervals with slack
#: (nix/module.nix: acquire and derive every 5 minutes, plan every 30,
#: release hourly, maintain daily).
LOOP_QUIET = {
    "acquire": 1800,
    "plan": 7200,
    "derive": 1800,
    "release": 14400,
    "maintain": 93600,
}
#: A loop whose last this many ticks, skips left out, all failed alerts.
LOOP_FAILING = 3
#: With --loops 1: how long each loop may go without progress (loop_state's
#: last_progress_at) while unheld and its timer active. Skipped and idle
#: ticks do not reset it. Acquire counts only while no host or acquire guard
#: explains the silence, derive only while ready work waits; release's window
#: is its build interval plus RELEASE_IDLE_SLACK, from its newest record.
LOOP_IDLE = {"acquire": 3600, "derive": 7200, "plan": 7200}
RELEASE_IDLE_SLACK = 7200
#: The build interval when no release record names one (config/sources.toml).
BUILD_INTERVAL_SECONDS = 21600
#: At most this many of the newest run records are read for the loop checks.
LOOP_RECORDS = 5000
#: The plan loop rewrites its year rows every 30 minutes; acquire offers only
#: planned pages, so a plan older than this starves it (D-0218).
PLAN_STALE_SECONDS = 7200
#: WAL frames not yet reset past this many bytes mean checkpoints cannot keep
#: up, usually because a long read holds an old snapshot.
WAL_LARGE_BYTES = 4_000_000_000
#: A staged generation (derivation_building) not advanced for this long is
#: an orphan: nothing is finishing it (D-0217).
BUILDING_ORPHAN_SECONDS = 86400
#: Up to this many orphans are named.
BUILDING_LINES = 5
#: Partial generations with no building row (maintain's count) above this
#: are named: nothing collects them before phase 4 (D-0217).
INCOMPLETE_GENERATIONS = 3
#: WAL header magic numbers (either checksum byte order).
WAL_MAGIC = (0x377F0682, 0x377F0683)
#: The headroom swingset-backup-room in nix/module.nix keeps free beside a
#: new checkpoint. While a backup runs, [disk-low] fires only below it.
BACKUP_HEADROOM_BYTES = 20_000_000_000
#: The state without checkpoints/ above which [state-size] fires: 5 GB before
#: architecture phase 4's 55 GB trigger (docs/plans/architecture.md), which
#: leaves two to three days at the growth estimated on 2026-09-25 (D-0208).
STATE_SIZE_ALERT_BYTES = 50_000_000_000
#: Top-level directories [state-size] leaves out: the checkpoints, and the
#: service's Python environment and uv cache (nix/module.nix), which hold
#: code, not state, and do not grow with the data.
STATE_SIZE_SKIP = frozenset({"checkpoints", "venv", "uv-cache"})
#: Nothing collects derivation or source equivalences before invalidation
#: step 3; past this many rows in either table that step starts (D-0208).
#: Step 3 deletes rows, so they are counted, not read off the largest rowid.
EQUIVALENCE_ROWS = 500_000
SOURCE_EQUIVALENCE_ROWS = 500_000
#: Reports a cycle record carries; each is stored as `<key>` or, when its
#: query failed, `<key>_error` (schedule/cycle._report).
CYCLE_REPORTS = ("controls", "derivation")
#: A database read that fails is tried once more after this many seconds: an
#: immutable read of a live database can meet a page mid-checkpoint.
RETRY_SECONDS = 1.0
_LOGGED = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) \[([a-z0-9-]+)\] ?(.*)$")

# A unit is ready when its latest attempt is absent or succeeded, a retry was
# requested after it, or it is transient/interrupted and its retry time has
# come. This mirrors state/attempts.eligible without the input fingerprint
# test, which needs the package. A unit whose work-queue row waits for input,
# is quarantined, or backs off to a later time is not ready (D-0216). A
# code-scope or environment accept and a replay epoch queue hints for the
# units they make stale (state/work.affected_work, derivations.bump_replay),
# so those show here; a unit made stale only by a new upstream generation is
# still missed, so the check errs toward silence. A re-queue moves
# `enqueued_at` (state/work.enqueue), so churn that re-queues every ready unit
# of a stage restarts that stage's clock. project/history is paced by
# history_interval_seconds (D-0199), so it has a stage of its own, HISTORY,
# clocked from when it came due; the link units that wait for it are handled
# in _link_clock.
_READY_33 = """
SELECT p.stage, p.unit_kind, p.enqueued_at, a.outcome, a.retry_at,
    coalesce(g.retry_generation, 0) > a.retry_generation
FROM pending_work p
LEFT JOIN work_generations g USING (stage, unit_kind, unit_id)
LEFT JOIN work_attempts a ON a.attempt_id = (
    SELECT attempt_id FROM work_attempts w
    WHERE w.stage = p.stage AND w.unit_kind = p.unit_kind AND w.unit_id = p.unit_id
    ORDER BY w.attempt_id DESC LIMIT 1)
"""
_READY = (
    _READY_33
    + """LEFT JOIN work_queue q
    ON q.stage = p.stage AND q.unit_kind = p.unit_kind AND q.unit_id = p.unit_id
WHERE q.state IS NULL OR q.state = 'leased'
    OR (q.state = 'ready' AND (q.next_attempt_at IS NULL OR q.next_attempt_at <= :now))
"""
)
#: The stage name project/history units are counted and reported under.
HISTORY = "project/history"
#: The kinds whose new generation makes a unit of each kind re-run with a
#: real input change (state/derivation_dependencies.prerequisites). A replay
#: bump explained by one of them, or by the kind's own generation, is not a
#: loop; any other generation is unrelated work.
REPLAY_CAUSES = {
    "project/inventory": frozenset({"project/calendar", "project/dancer", "project/source_index"}),
    "project/map": frozenset({"project/inventory"}),
    "project/event": frozenset({"project/map", "project/source_event"}),
    "project/source_event": frozenset({"project/map", "project/event"}),
    "project/history": frozenset(
        {
            "project/calendar",
            "project/dancer",
            "project/source_index",
            "project/inventory",
            "project/map",
            "project/event",
            "project/source_event",
        }
    ),
    "link/event": frozenset({"project/dancer", "project/event", "project/history"}),
}
#: Project kinds that wait for a paced inventory (D-0210), the deferred
#: inventory itself included.
PACED_BY_INVENTORY = frozenset({"inventory", "map", "event", "source_event"})

#: Every query the watchdog runs. tests/test_watchdog.py runs each against a
#: database built by the real migrations, so a renamed table or column fails
#: a test instead of silencing a check.
QUERIES: dict[str, str] = {
    "pauses": "SELECT until_at, reason, created_at FROM operator_pauses "
    "WHERE scope_kind = 'all' AND scope_id = 'all'",
    "checked": "SELECT max(last_checked_at) FROM watches",
    "issued": "SELECT max(issued_at) FROM scheduler_requests",
    "stored": "SELECT max(fetched_at) FROM snapshots",
    "ready": _READY,
    # Before schema 34, which has no work queue.
    "ready_33": _READY_33,
    "queue_table": "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'work_queue'",
    # Units quarantined since :since (a queue timestamp, state/queue.at).
    "quarantined": "SELECT stage, unit_kind, unit_id, attempts, error_class, last_error "
    "FROM work_queue WHERE state = 'quarantined' AND quarantined_at > :since "
    "ORDER BY quarantined_at, stage, unit_kind, unit_id",
    # Leases that ended before :since and were never closed or taken over.
    "lease_stuck": "SELECT stage, unit_kind, unit_id, lease_owner, lease_until FROM work_queue "
    "WHERE state = 'leased' AND lease_until < :since ORDER BY lease_until",
    "guard_table": "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'guard_reasons'",
    # Guards still seen after their expiry, and seen since :since (a queue
    # timestamp). Watch rows are counted only (swingset.state.guards).
    "guard_expired": "SELECT subject, reason, since, expires_at FROM guard_reasons "
    "WHERE subject NOT LIKE 'watch:%' AND last_seen_at > expires_at AND last_seen_at > :since "
    "ORDER BY expires_at, subject, reason",
    "guard_counts": "SELECT reason, count(*) FROM guard_reasons GROUP BY reason "
    "ORDER BY count(*) DESC, reason",
    "loop_state_table": "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'loop_state'",
    # When each loop last made progress (loops/runtime.py; ISO stamps).
    "loop_progress": "SELECT loop, last_progress_at FROM loop_state",
    # Host and acquire guards seen since :since: acquisition is held on purpose.
    "guards_recent": "SELECT count(*) FROM guard_reasons WHERE last_seen_at > :since "
    "AND (subject LIKE 'host:%' OR subject = 'loop:acquire')",
    "plan_table": "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'acquisition_plan'",
    # When the plan loop last wrote a row (loops/plan.py; an ISO stamp).
    "plan_newest": "SELECT max(planned_at) FROM acquisition_plan",
    "building_table": "SELECT 1 FROM sqlite_master WHERE type = 'table' "
    "AND name = 'derivation_building'",
    # Every staged generation; the table holds a handful of rows at most.
    "building": "SELECT stage, unit_kind, unit_id, rows_stored, label_rows, updated_at "
    "FROM derivation_building ORDER BY updated_at, stage, unit_kind, unit_id",
    "stage_attempt": "SELECT finished_at FROM work_attempts "
    "WHERE stage = :stage AND outcome = 'succeeded' ORDER BY attempt_id DESC LIMIT 1",
    "stage_generation": "SELECT created_at FROM derivation_generations "
    "WHERE stage = :stage ORDER BY rowid DESC LIMIT 1",
    # A pending project/history unit, when it was queued and when its
    # current output was made: written, or kept by a later run's cutoff
    # (D-0205), whichever is newer.
    "history": "SELECT p.unit_id, p.enqueued_at, max(g.created_at, coalesce(("
    "SELECT max(e.created_at) FROM derivation_equivalences e WHERE e.stage = s.stage "
    "AND e.unit_kind = s.unit_kind AND e.unit_id = s.unit_id "
    "AND e.generation_id = s.materialized_generation_id), g.created_at)) FROM pending_work p "
    "LEFT JOIN derivation_scopes s ON s.stage = p.stage AND s.unit_kind = p.unit_kind "
    "AND s.unit_id = p.unit_id "
    "LEFT JOIN derivation_generations g ON g.generation_id = s.materialized_generation_id "
    "WHERE p.stage = 'project' AND p.unit_kind = 'history'",
    # Every project/history output still stored, or kept by a cutoff: when
    # links became runnable.
    "history_made": "SELECT created_at FROM derivation_generations "
    "WHERE stage = 'project' AND unit_kind = 'history' "
    "UNION ALL SELECT created_at FROM derivation_equivalences "
    "WHERE stage = 'project' AND unit_kind = 'history'",
    # The same two before schema 33, which has no equivalences.
    "history_32": "SELECT p.unit_id, p.enqueued_at, g.created_at FROM pending_work p "
    "LEFT JOIN derivation_scopes s ON s.stage = p.stage AND s.unit_kind = p.unit_kind "
    "AND s.unit_id = p.unit_id "
    "LEFT JOIN derivation_generations g ON g.generation_id = s.materialized_generation_id "
    "WHERE p.stage = 'project' AND p.unit_kind = 'history'",
    "history_made_32": "SELECT created_at FROM derivation_generations "
    "WHERE stage = 'project' AND unit_kind = 'history'",
    "equivalences": "SELECT 1 FROM sqlite_master WHERE type = 'table' "
    "AND name = 'derivation_equivalences'",
    # When project/inventory last materialized, cutoffs included (D-0205).
    "inventory_made": "SELECT max(g.created_at, coalesce((SELECT max(e.created_at) "
    "FROM derivation_equivalences e WHERE e.stage = s.stage AND e.unit_kind = s.unit_kind "
    "AND e.unit_id = s.unit_id AND e.generation_id = s.materialized_generation_id), "
    "g.created_at)) FROM derivation_scopes s JOIN derivation_generations g "
    "ON g.generation_id = s.materialized_generation_id "
    "WHERE s.stage = 'project' AND s.unit_kind = 'inventory'",
    "inventory_made_32": "SELECT g.created_at FROM derivation_scopes s "
    "JOIN derivation_generations g ON g.generation_id = s.materialized_generation_id "
    "WHERE s.stage = 'project' AND s.unit_kind = 'inventory'",
    # Every time project/inventory materialized, cutoffs included: when each
    # cascade started.
    "inventory_runs": "SELECT created_at FROM derivation_generations "
    "WHERE stage = 'project' AND unit_kind = 'inventory' "
    "UNION ALL SELECT created_at FROM derivation_equivalences "
    "WHERE stage = 'project' AND unit_kind = 'inventory'",
    "inventory_runs_32": "SELECT created_at FROM derivation_generations "
    "WHERE stage = 'project' AND unit_kind = 'inventory'",
    # When project/history's current output was made, cutoffs included: what
    # the inventory gate (schedule/pacing) reads as history's made-time.
    "history_current": "SELECT max(g.created_at, coalesce((SELECT max(e.created_at) "
    "FROM derivation_equivalences e WHERE e.stage = s.stage AND e.unit_kind = s.unit_kind "
    "AND e.unit_id = s.unit_id AND e.generation_id = s.materialized_generation_id), "
    "g.created_at)) FROM derivation_scopes s JOIN derivation_generations g "
    "ON g.generation_id = s.materialized_generation_id "
    "WHERE s.stage = 'project' AND s.unit_kind = 'history' AND s.unit_id = 'all'",
    "history_current_32": "SELECT g.created_at FROM derivation_scopes s "
    "JOIN derivation_generations g ON g.generation_id = s.materialized_generation_id "
    "WHERE s.stage = 'project' AND s.unit_kind = 'history' AND s.unit_id = 'all'",
    # Equivalences so far (D-0208's collector trigger).
    "equivalence_rows": "SELECT count(*) FROM derivation_equivalences",
    "source_equivalence_rows": "SELECT count(*) FROM source_equivalences",
    # Whether the calendar's replay epoch moved past the one the inventory's
    # current output read (schedule/pacing.calendar_replayed): the gate then
    # lets the inventory run. Schema 33 only.
    "calendar_replayed": "SELECT (SELECT version FROM derivation_input_versions "
    "WHERE namespace = 'replayed' AND input_key = '[\"project\",\"calendar\"]') > "
    "coalesce((SELECT max(CAST(json_extract(j.value, '$.version') AS INTEGER)) FROM ("
    "SELECT d.manifest_json AS manifest FROM derivation_scopes s "
    "JOIN derivation_generations g ON g.generation_id = s.materialized_generation_id "
    "JOIN derivation_dependency_sets d ON d.dependency_set_id = g.dependency_set_id "
    "WHERE s.stage = 'project' AND s.unit_kind = 'inventory' AND s.unit_id = 'all' "
    "UNION ALL SELECT d.manifest_json FROM derivation_scopes s "
    "JOIN derivation_equivalences e ON e.stage = s.stage AND e.unit_kind = s.unit_kind "
    "AND e.unit_id = s.unit_id AND e.generation_id = s.materialized_generation_id "
    "JOIN derivation_dependency_sets d ON d.dependency_set_id = e.dependency_set_id "
    "WHERE s.stage = 'project' AND s.unit_kind = 'inventory' AND s.unit_id = 'all'"
    ") m, json_each(m.manifest) j "
    "WHERE json_extract(j.value, '$.kind') = 'replay' "
    "AND json_extract(j.value, '$.key') = '[\"project\",\"calendar\"]'), 0)",
    # When a calendar that writes rows last minted a generation
    # (schedule/pacing.calendar_regenerated): newer than the inventory's
    # output, the gate lets the inventory run.
    "calendar_made": "SELECT max(g.created_at) FROM derivation_scopes s "
    "JOIN derivation_generations g ON g.generation_id = s.materialized_generation_id "
    "WHERE s.stage = 'project' AND s.unit_kind = 'calendar' AND s.unit_id != 'wsdc-history'",
    # Whether a changed input queued the inventory again.
    "inventory_pending": "SELECT 1 FROM pending_work "
    "WHERE stage = 'project' AND unit_kind = 'inventory'",
    # The outcome of the newest project/history attempt, finished or not.
    "history_latest": "SELECT outcome FROM work_attempts WHERE stage = 'project' "
    "AND unit_kind = 'history' AND unit_id = 'all' ORDER BY attempt_id DESC LIMIT 1",
    # When project/history last succeeded, output or not.
    "history_attempt": "SELECT finished_at FROM work_attempts WHERE stage = 'project' "
    "AND unit_kind = 'history' AND outcome = 'succeeded' ORDER BY attempt_id DESC LIMIT 1",
    "budgets": "SELECT host, requests, bytes FROM host_budget WHERE day = :day ORDER BY host",
    # fetch/politeness.py pauses a host whose spacing is unknown: a NULL gap,
    # or no spacing row although the host has spent budget before.
    "legacy": "SELECT host FROM host_request_spacing WHERE gap_seconds IS NULL "
    "UNION SELECT host FROM host_budget WHERE requests > 0 "
    "AND host NOT IN (SELECT host FROM host_request_spacing) ORDER BY host",
    "blocked": "SELECT host, paused_until FROM hosts "
    "WHERE pause_reason = 'Blocked' AND paused_until IS NOT NULL ORDER BY host",
    # A success resets the streak (fetch/politeness.py), so a long one means
    # the host has answered nothing but 429 or 5xx for hours.
    "failing": "SELECT host, pause_reason, pause_streak, paused_until FROM hosts "
    "WHERE pause_reason IN ('Throttled', 'ServerError') AND pause_streak >= :streak "
    "ORDER BY host",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, help="swingset state directory")
    parser.add_argument(
        "--config",
        help="config directory; hosts.toml gives the daily request and byte budgets and"
        " sources.toml the history interval. Overrides are not read.",
    )
    parser.add_argument(
        "--quiet",
        type=int,
        required=True,
        help="seconds without a finished cycle, while unheld, that count as a stall",
    )
    parser.add_argument(
        "--stale-fetch",
        type=int,
        required=True,
        help="seconds since the newest request, while unheld, that count as a stall",
    )
    parser.add_argument(
        "--stale-progress",
        type=int,
        required=True,
        help="seconds since a stage with ready work last progressed, while unheld,"
        " that count as a stall",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        required=True,
        help="an alert key already in ALERTS.log within this many seconds is not repeated",
    )
    parser.add_argument(
        "--disk",
        default="",
        help="'USED CAP [READ_AT]' in bytes from swingset-disk --raw; empty means no quota",
    )
    parser.add_argument(
        "--disk-min-free",
        type=int,
        required=True,
        help="alert when the quota has fewer free bytes than this",
    )
    parser.add_argument(
        "--timer-active",
        type=int,
        choices=(0, 1),
        help="1 when swingset-cycle.timer is active; required without --loops 1",
    )
    parser.add_argument(
        "--loops",
        type=int,
        choices=(0, 1),
        default=0,
        help="1 when the five loop timers run instead of the cycle (loops.enable)",
    )
    parser.add_argument(
        "--timers-inactive",
        default="",
        help="with --loops 1, comma-separated loops whose swingset-<loop>.timer is not active",
    )
    parser.add_argument(
        "--wal-large",
        type=int,
        default=WAL_LARGE_BYTES,
        help="alert when state.sqlite-wal holds more bytes of frames not yet reset",
    )
    parser.add_argument(
        "--backup-failed",
        type=int,
        choices=(0, 1),
        default=0,
        help="1 when swingset-backup.service is in the failed state",
    )
    parser.add_argument(
        "--summary-failed",
        type=int,
        choices=(0, 1),
        default=0,
        help="1 when swingset-summary.service is in the failed state",
    )
    parser.add_argument(
        "--backup-active",
        type=int,
        choices=(0, 1),
        default=0,
        help="1 while swingset-backup.service runs; [disk-low] then fires only under"
        " the backup's own headroom",
    )
    parser.add_argument(
        "--backup-upload",
        type=int,
        choices=(0, 1),
        default=1,
        help="0 when the backup timer writes local checkpoints only (D-0203)",
    )
    parser.add_argument(
        "--backup-max-age",
        type=int,
        default=129600,
        help="with --backup-upload 0, alert when the newest local checkpoint is older",
    )
    parser.add_argument(
        "--state-size-alert",
        type=int,
        default=STATE_SIZE_ALERT_BYTES,
        help="alert when the state directory without checkpoints/, venv/ and uv-cache/"
        " holds more bytes",
    )
    parser.add_argument("--now", help="ISO 8601 time to check against; default now")
    return parser


def _ts(value: str) -> float:
    """Seconds since the epoch; a stamp without a zone is UTC."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def _utc(value: str) -> str:
    return _at(_ts(value))


def _at(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _queue_at(stamp: float) -> str:
    """A work-queue timestamp (swingset.state.queue.at): text order is time order."""
    return (
        datetime.fromtimestamp(stamp, UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60} h {minutes % 60} m"


def _span(seconds: int) -> str:
    """A window: whole minutes under two hours, else whole hours."""
    return f"{seconds // 60} minutes" if seconds < 7200 else f"{seconds // 3600} h"


class Checks:
    """Runs each read on its own and remembers which ones failed."""

    def __init__(self) -> None:
        self.failed: list[tuple[str, str]] = []

    def run(self, name: str, read: Callable[[], T], default: T) -> T:
        try:
            return read()
        except Exception as exc:  # one broken read must not hide the others
            self.failed.append((name, f"{type(exc).__name__}: {exc}"))
            return default


def _one(db: sqlite3.Connection, name: str, **params: Any) -> Any:
    row = db.execute(QUERIES[name], params).fetchone()
    return row[0] if row else None


def _calendar_replayed(db: sqlite3.Connection) -> bool:
    try:
        return bool(_one(db, "calendar_replayed"))
    except sqlite3.OperationalError as exc:
        # Test databases hold only the tables each check reads.
        if "no such table" in str(exc):
            return False
        raise


def _newest(*stamps: str | None) -> float | None:
    parsed = [_ts(stamp) for stamp in stamps if stamp]
    return max(parsed) if parsed else None


class Limits(NamedTuple):
    """What the watchdog reads from config/: daily budgets and the history pace."""

    requests: dict[str, int]
    bytes: dict[str, int]
    history_interval: float | None
    #: `inventory_interval_seconds` (D-0210); None when unset.
    inventory_interval: float | None = None
    #: `[retention] max_database_bytes`, where the pipeline pauses; None
    #: without a config.
    size_cap: int | None = None


_BYTE_UNITS = {"KB": 1000, "MB": 1000000, "GB": 1000000000}


def _byte_budget(value: object) -> int:
    """A `daily_byte_budget`, parsed as swingset.config.parse_hosts does."""
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"(\d+)(KB|MB|GB)", str(value))
    if not match:
        raise ValueError(f"invalid byte budget: {value}")
    return int(match[1]) * _BYTE_UNITS[match[2]]


def _config(directory: str | None) -> Limits:
    """Daily request and byte budgets by host and the history interval.

    A sweep's larger budget for points.worldsdc.com is not read: the script
    cannot tell a sweep day, so on one that host counts as spent early.
    """
    if not directory:
        return Limits({}, {}, None)
    with open(os.path.join(directory, "hosts.toml"), "rb") as handle:
        hosts = tomllib.load(handle).get("hosts", {})
    with open(os.path.join(directory, "sources.toml"), "rb") as handle:
        scheduling = tomllib.load(handle).get("scheduling", {})
    requests = {
        host: int(table["daily_request_budget"])
        for host, table in hosts.items()
        if "daily_request_budget" in table
    }
    byte_budgets = {
        host: _byte_budget(table["daily_byte_budget"])
        for host, table in hosts.items()
        if "daily_byte_budget" in table
    }
    interval = scheduling.get("history_interval_seconds")
    inventory = scheduling.get("inventory_interval_seconds")
    with open(os.path.join(directory, "sources.toml"), "rb") as handle:
        cap = tomllib.load(handle).get("retention", {}).get("max_database_bytes", "64GB")
    return Limits(
        requests,
        byte_budgets,
        float(interval) if interval is not None else None,
        float(inventory) if inventory is not None else None,
        _byte_size(cap),
    )


def _byte_size(value: object) -> int:
    """A byte count, parsed as swingset.config.byte_size does."""
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"(\d+)(KB|MB|GB)?", str(value).strip())
    if not match:
        raise ValueError(f"invalid byte size: {value}")
    return int(match[1]) * (_BYTE_UNITS[match[2]] if match[2] else 1)


def _read_db(state: str, now: float, checks: Checks, limits: Limits) -> dict[str, Any]:
    """The database facts; a read that fails is tried once more (RETRY_SECONDS)."""
    # immutable=1 reads the main file only and mode=ro stops a missing file
    # from being created, so root never leaves files in the service's state
    # directory. It ignores the WAL, so the view may lag one checkpoint.
    path = os.path.join(state, "state.sqlite")
    if not os.path.exists(path):
        return {}
    for attempt in (1, 2):
        tried = Checks()
        facts: dict[str, Any] = {}
        db = tried.run(
            "database",
            lambda: sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True),
            None,
        )
        if db is not None:
            try:
                _read_facts(db, now, tried, limits, facts)
            finally:
                db.close()
        if not tried.failed or attempt == 2:
            break
        time.sleep(RETRY_SECONDS)
    checks.failed.extend(tried.failed)
    return facts


def _link_clock(
    link: float | None,
    oldest: float | None,
    made: list[float],
    pending: float | None,
    interval: float | None,
    now: float,
) -> tuple[float | None, str]:
    """When link units last progressed or became runnable, and why.

    Every link unit needs a current project/history output (D-0199), so
    links become runnable when history materializes. `made` holds every
    stored history output time and `pending` the output time of a pending
    history unit. Returns (None, "waiting") while links wait for paced history
    by design: they progressed after its newest output and it is not yet due.
    Otherwise the clock is the latest of the last link progress and the moment
    the links became runnable without progressing since:

    - they progressed after the newest output: when the pending history came
      due;
    - they did not: when history next materialized after their last
      progress, or, if earlier, when that history was due again, since a
      history that runs late holds the links as surely as one that fails.
    """
    if not made:
        clocks = [clock for clock in (link, oldest) if clock is not None]
        return (max(clocks) if clocks else None), ""
    newest = max(made)
    if link is not None and link >= newest:
        if pending is None:
            clocks = [clock for clock in (link, oldest) if clock is not None]
            return max(clocks), ""
        if interval is None:
            return link, f"project/history pending since its output at {_at(pending)}"
        due = pending + interval
        if now < due:
            return None, "waiting"
        return max(link, due), f"project/history due at {_at(due)} has not run"
    after = min(stamp for stamp in made if link is None or stamp > link)
    runnable = after
    if interval is not None and link is not None:
        before = [stamp for stamp in made if stamp <= link]
        runnable = min(after, (max(before) if before else link) + interval)
    clock = runnable if link is None else max(link, runnable)
    return clock, f"runnable since {_at(runnable)}, project/history output at {_at(newest)}"


def _history_clock(
    progress: float | None,
    units: list[tuple[float | None, float | None]],
    interval: float | None,
    runs: Sequence[float] = (),
) -> tuple[float | None, str]:
    """When project/history last progressed or came due, and when it was due.

    `units` holds each pending history unit's queue time and the time its
    current output was made. A unit with an output comes due `interval`
    after it (D-0199); one without, or with no interval, when it was queued.
    History needs a current inventory, so it cannot run before the cascade
    of the first inventory made at or after it came due; `runs` holds every
    inventory materialization. The clock is the latest of its due time, that
    inventory and its last success or output, so a history that comes due
    and never runs alarms whether or not link units wait for it. Later
    inventories do not move the clock: a dancer change that interrupts every
    cascade starves history, and that must alarm (D-0210).
    """
    dues = [
        made + interval if made is not None and interval is not None else enqueued
        for enqueued, made in units
    ]
    known = [due for due in dues if due is not None]
    if not known:
        return progress, ""
    due = min(known)
    note = f"due at {_at(due)}"
    since = [run for run in runs if run >= due]
    started = min(since, default=due)
    if since:
        note += f", cascade from the inventory at {_at(started)}"
        if len(since) > 1:
            note += f", interrupted since {_at(max(since))}"
    return (started if progress is None else max(progress, started)), note


def _read_facts(
    db: sqlite3.Connection,
    now: float,
    checks: Checks,
    limits: Limits,
    facts: dict[str, Any],
) -> None:
    def pauses() -> None:
        live = [
            (reason, created)
            for until, reason, created in db.execute(QUERIES["pauses"])
            if until is None or _ts(until) > now
        ]
        facts["size_cap"] = any(reason == SIZE_CAP_REASON for reason, _ in live)
        held = [(reason, created) for reason, created in live if reason != SIZE_CAP_REASON]
        facts["paused"] = bool(held)
        facts["pause_since"] = [(reason, _ts(created)) for reason, created in held if created]
        # created_at came with migration 0012 and may be NULL: such a pause
        # has no 48-hour clock, so it is overdue from the start.
        facts["pause_undated"] = [reason for reason, created in held if not created]

    checks.run("pauses", pauses, None)
    requested = checks.run(
        "requests", lambda: _newest(_one(db, "checked"), _one(db, "issued")), None
    )
    if requested is not None:
        facts["requested"] = requested
    stored = checks.run("snapshots", lambda: _newest(_one(db, "stored")), None)
    if stored is not None:
        facts["stored"] = stored

    def inventory_paced() -> bool:
        """Whether the inventory gate (D-0210) holds the kinds downstream of it.

        The gate (schedule/pacing.deferred) holds only a queued inventory.
        While one is queued and materialized within its interval, it and the
        map, event and source-event units after it wait by design, unless
        history is due and has consumed that inventory, or the inventory was
        made at or after history came due: that cascade was interrupted, and
        the gate lets the inventory run until history lands. A history whose
        latest attempt failed is not due to the gate, so the gate keeps
        holding. A current inventory that is not queued holds nothing, so
        those units count as ready. A calendar replay the inventory has not
        read, or a calendar generation newer than it, lifts the wait too. While it holds, history waits for the
        inventory's next run, so history's clock starts then. History's
        made-time is its current output's, read as the gate reads it.
        """
        if limits.inventory_interval is None or not _one(db, "inventory_pending"):
            return False
        suffix = "" if _one(db, "equivalences") else "_32"
        made = _newest(_one(db, "inventory_made" + suffix))
        if made is None or now - made >= limits.inventory_interval:
            return False
        if suffix == "" and _calendar_replayed(db):
            return False
        calendar = _newest(_one(db, "calendar_made"))
        if calendar is not None and calendar > made:
            return False
        facts["inventory_next"] = made + limits.inventory_interval
        if limits.history_interval is None:
            return True
        history = _newest(_one(db, "history_current" + suffix))
        if history is not None and now < history + limits.history_interval:
            return True
        if _one(db, "history_latest") == "transient":
            return True
        if history is None:
            return True
        return made < history + limits.history_interval and history < made

    paced = checks.run("inventory", inventory_paced, False)

    def equivalence_rows() -> tuple[int, int] | None:
        if not _one(db, "equivalences"):
            return None
        return (
            int(_one(db, "equivalence_rows") or 0),
            int(_one(db, "source_equivalence_rows") or 0),
        )

    rows = checks.run("equivalence-rows", equivalence_rows, None)
    if rows is not None:
        facts["equivalence_rows"], facts["source_equivalence_rows"] = rows

    queued = bool(checks.run("queue", lambda: _one(db, "queue_table"), None))

    def ready() -> tuple[Counter[str], dict[str, float]]:
        counted: Counter[str] = Counter()
        oldest: dict[str, float] = {}
        rows = (
            db.execute(QUERIES["ready"], {"now": _queue_at(now)})
            if queued
            else db.execute(QUERIES["ready_33"])
        )
        for stage, kind, enqueued, outcome, retry_at, retried in rows:
            if stage == "project" and kind == "history":
                stage = HISTORY
            if paced and stage == "project" and kind in PACED_BY_INVENTORY:
                facts["waiting_inventory"] = facts.get("waiting_inventory", 0) + 1
                continue
            if (
                outcome is None
                or outcome == "succeeded"
                or retried
                or (
                    outcome in ("transient", "interrupted")
                    and retry_at is not None
                    and _ts(retry_at) <= now
                )
            ):
                counted[stage] += 1
                if enqueued:
                    stamp = _ts(enqueued)
                    oldest[stage] = min(stamp, oldest.get(stage, stamp))
        return counted, oldest

    found = checks.run("ready", ready, None)
    if found is not None:
        counted, oldest = found
        facts["ready"] = counted
        clocks: dict[str, float] = {}
        notes: dict[str, str] = {}

        def stage_clock(stage: str) -> float | None:
            return _newest(
                _one(db, "stage_attempt", stage=stage), _one(db, "stage_generation", stage=stage)
            )

        def history() -> tuple[list[float], list[tuple[float | None, float | None]]]:
            # Schema 33 comes with the first cycle after the phase 1 switch.
            suffix = "" if _one(db, "equivalences") else "_32"
            made = [
                _ts(created)
                for (created,) in db.execute(QUERIES["history_made" + suffix])
                if created
            ]
            units = [
                (_ts(enqueued) if enqueued else None, _ts(created) if created else None)
                for _unit, enqueued, created in db.execute(QUERIES["history" + suffix])
            ]
            return made, units

        def inventory_runs() -> list[float]:
            suffix = "" if _one(db, "equivalences") else "_32"
            return [
                _ts(created)
                for (created,) in db.execute(QUERIES["inventory_runs" + suffix])
                if created
            ]

        def history_progress(made: list[float]) -> float | None:
            succeeded = _newest(_one(db, "history_attempt"))
            known = [stamp for stamp in (succeeded, max(made, default=None)) if stamp is not None]
            return max(known) if known else None

        no_history: tuple[list[float], list[tuple[float | None, float | None]]] = ([], [])
        made, units = (
            checks.run("history", history, no_history)
            if "link" in counted or HISTORY in counted
            else no_history
        )
        for stage in sorted(counted):
            if stage == HISTORY:
                progress = checks.run(f"progress:{stage}", partial(history_progress, made), None)
                runs = checks.run("inventory-runs", inventory_runs, [])
                clock, note = _history_clock(progress, units, limits.history_interval, runs)
                # The gate holds only an inventory made before history came
                # due (inventory_paced), so no inventory has run since.
                waits = facts.get("inventory_next") if paced else None
                if clock is not None and waits is not None and waits > clock:
                    clock = waits
                    note = f"waits for the paced inventory at {_at(waits)}"
                if note:
                    notes[stage] = note
                if clock is not None:
                    clocks[stage] = clock
                continue
            clock = checks.run(f"progress:{stage}", partial(stage_clock, stage), None)
            if stage == "link":
                pending = [created for _, created in units if created is not None]
                clock, note = _link_clock(
                    clock,
                    oldest.get(stage),
                    made,
                    max(pending) if pending else None,
                    limits.history_interval,
                    now,
                )
                if note == "waiting":
                    facts["waiting_links"] = counted.pop("link")
                    continue
                if note:
                    notes[stage] = note
            else:
                # A stage that sat idle is clocked from when its oldest ready
                # unit was queued, not from its last progress.
                clocks_known = [c for c in (clock, oldest.get(stage)) if c is not None]
                clock = max(clocks_known) if clocks_known else None
            if clock is not None:
                clocks[stage] = clock
        facts["progress"] = clocks
        facts["progress_notes"] = notes

    if queued:
        quarantined = checks.run(
            "unit-quarantined",
            lambda: [
                tuple(row)
                for row in db.execute(
                    QUERIES["quarantined"], {"since": _queue_at(now - QUARANTINE_WINDOW)}
                )
            ],
            [],
        )
        if quarantined:
            facts["quarantined"] = quarantined
        stuck = checks.run(
            "lease-stuck",
            lambda: [
                tuple(row)
                for row in db.execute(
                    QUERIES["lease_stuck"], {"since": _queue_at(now - LEASE_STUCK_SECONDS)}
                )
            ],
            [],
        )
        if stuck:
            facts["lease_stuck"] = stuck
    if checks.run("guards", lambda: _one(db, "guard_table"), None):
        expired = checks.run(
            "guard-expired",
            lambda: [
                tuple(row)
                for row in db.execute(
                    QUERIES["guard_expired"], {"since": _queue_at(now - GUARD_SEEN_WINDOW)}
                )
            ],
            [],
        )
        if expired:
            facts["guard_expired"] = expired
        counts = checks.run(
            "guard-counts",
            lambda: [
                (str(reason), int(count)) for reason, count in db.execute(QUERIES["guard_counts"])
            ],
            [],
        )
        if counts:
            facts["guard_counts"] = counts
        recent_guards = checks.run(
            "guards-recent",
            lambda: _one(db, "guards_recent", since=_queue_at(now - LOOP_IDLE["acquire"])),
            None,
        )
        if recent_guards is not None:
            facts["guards_recent"] = int(recent_guards)
    if checks.run("loop-state", lambda: _one(db, "loop_state_table"), None):
        progress = checks.run(
            "loop-progress",
            lambda: {
                str(loop): (_ts(str(stamp)) if stamp else None)
                for loop, stamp in db.execute(QUERIES["loop_progress"])
            },
            None,
        )
        if progress is not None:
            facts["loop_progress"] = progress
    if checks.run("plan", lambda: _one(db, "plan_table"), None):
        planned = checks.run("plan-stale", lambda: _newest(_one(db, "plan_newest")), None)
        if planned is not None:
            facts["planned"] = planned
    if checks.run("building", lambda: _one(db, "building_table"), None):
        orphans = checks.run(
            "building-orphans",
            lambda: [
                tuple(row)
                for row in db.execute(QUERIES["building"])
                if now - _ts(row[5]) > BUILDING_ORPHAN_SECONDS
            ],
            [],
        )
        if orphans:
            facts["building_orphans"] = orphans
    day = datetime.fromtimestamp(now, UTC).date().isoformat()
    facts["budgets"] = checks.run(
        "budgets",
        lambda: [tuple(row) for row in db.execute(QUERIES["budgets"], {"day": day})],
        None,
    )
    legacy: list[str] = checks.run(
        "legacy", lambda: [host for (host,) in db.execute(QUERIES["legacy"])], []
    )
    if legacy:
        facts["legacy"] = legacy
    blocked: list[tuple[str, str]] = checks.run(
        "blocked",
        lambda: [
            (host, until) for host, until in db.execute(QUERIES["blocked"]) if _ts(until) > now
        ],
        [],
    )
    if blocked:
        facts["blocked"] = blocked
    failing: list[tuple[str, str, int, str | None]] = checks.run(
        "failing",
        lambda: [tuple(row) for row in db.execute(QUERIES["failing"], {"streak": FAILING_STREAK})],
        [],
    )
    if failing:
        facts["failing"] = failing


def _last_logged(state: str) -> tuple[dict[str, float], dict[str, float]]:
    """When each key, and each whole line, was last logged."""
    try:
        with open(os.path.join(state, "ALERTS.log"), "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return {}, {}
    by_key: dict[str, float] = {}
    by_line: dict[str, float] = {}
    for line in tail.splitlines():
        match = _LOGGED.match(line)
        if match:
            at = _ts(match.group(1).replace("Z", "+00:00"))
            key, text = match.group(2), match.group(3)
            by_key[key] = max(at, by_key.get(key, at))
            whole = f"[{key}] {text}"
            by_line[whole] = max(at, by_line.get(whole, at))
    return by_key, by_line


def _checkpoints(state: str) -> tuple[tuple[str, float] | None, list[str]]:
    """The newest complete timer checkpoint, and the copies that failed a read-back."""
    newest: tuple[str, float] | None = None
    failed: list[str] = []
    try:
        names = os.listdir(os.path.join(state, "checkpoints"))
    except OSError:
        return None, failed
    for name in sorted(names):
        if name.startswith(FAILED_PREFIX):
            failed.append(name)
            continue
        if not TIMER_NAME.match(name):
            continue
        try:
            written = os.stat(os.path.join(state, "checkpoints", name, "checkpoint.json")).st_mtime
        except OSError:
            continue
        if newest is None or written > newest[1]:
            newest = (name, written)
    return newest, failed


def _tree_bytes(path: str, skip: frozenset[str] = frozenset()) -> int:
    """Bytes of the regular files under a directory, without following links,
    leaving out the top-level directories named in `skip`.

    The state directory holds about 80,000 files; a walk takes under a second.
    """
    total = 0
    for root, directories, files in os.walk(path):
        if root == path:
            directories[:] = [name for name in directories if name not in skip]
        for name in files:
            try:
                info = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if not os.path.islink(os.path.join(root, name)):
                total += info.st_size
    return total


def _records(state: str) -> list[dict[str, Any]]:
    """The last three cycle records, oldest first.

    A backup, gc or other command writes a record with a `command` field, and
    a loop tick one with `loop`; a cycle's summary has neither. Those records
    must neither reset `[cycles-failing]` nor hold `[cycles-quiet]` back.
    """
    runs = sorted(glob.glob(os.path.join(state, "runs", "run_*.json")), key=os.path.getmtime)
    records: list[dict[str, Any]] = []
    for path in reversed(runs):
        try:
            with open(path) as handle:
                record = json.load(handle)
        except Exception:
            continue
        if (
            isinstance(record, dict)
            and record.get("command", "cycle") == "cycle"
            and not record.get("loop")
        ):
            records.append(record)
            if len(records) == 3:
                break
    return records[::-1]


class LoopRecords(NamedTuple):
    """What the loop checks read from the newest run records."""

    #: When each loop's newest tick finished.
    newest: dict[str, float]
    #: Each loop's newest ticks that were not skipped, up to LOOP_FAILING,
    #: newest first.
    recent: dict[str, list[dict[str, Any]]]
    #: When the oldest loop tick read finished: no loop missing from `newest`
    #: ticked since. Older than the longest quiet window when loop ticks run
    #: back past it.
    earliest: float | None
    #: Each loop's newest record, skipped or not.
    latest: dict[str, dict[str, Any]] = {}


def _deferred_only(record: dict[str, Any]) -> bool:
    """A release tick that only waited for its build interval or backlog.

    It attempted no build or correction, so it neither breaks nor extends a
    run of failed builds (`[loop-failing]`).
    """
    return (
        record.get("loop") == "release"
        and bool(record.get("build_deferred"))
        and not record.get("build_attempted")
        and not record.get("failed")
        and not record.get("error")
    )


def _loop_records(state: str, now: float) -> LoopRecords:
    """The newest loop tick records, read newest first by mtime.

    Reading stops once every loop has LOOP_FAILING ticks that were not
    skipped, at the first record older than the longest quiet window, or
    after LOOP_RECORDS files. A record that cannot be read or has a bad stamp
    is skipped: its loop's own ticks decide.
    """
    horizon = now - max(LOOP_QUIET.values())
    runs = sorted(
        (
            (os.path.getmtime(path), path)
            for path in glob.glob(os.path.join(state, "runs", "run_*.json"))
        ),
        reverse=True,
    )
    newest: dict[str, float] = {}
    latest: dict[str, dict[str, Any]] = {}
    recent: dict[str, list[dict[str, Any]]] = {name: [] for name in LOOP_QUIET}
    earliest: float | None = None
    for mtime, path in runs[:LOOP_RECORDS]:
        try:
            with open(path) as handle:
                record = json.load(handle)
            loop = record.get("loop") if isinstance(record, dict) else None
            finished = _ts(record["finished_at"]) if loop in LOOP_QUIET else None
        except Exception:
            continue
        if finished is not None:
            earliest = finished if earliest is None else min(earliest, finished)
            if finished >= newest.get(loop, finished):
                latest[loop] = record
            newest[loop] = max(finished, newest.get(loop, finished))
            if (
                not record.get("skipped")
                and not _deferred_only(record)
                and len(recent[loop]) < LOOP_FAILING
            ):
                recent[loop].append(record)
        if mtime < horizon:
            break
        if len(newest) == len(LOOP_QUIET) and all(
            len(rows) == LOOP_FAILING for rows in recent.values()
        ):
            break
    return LoopRecords(newest, recent, earliest, latest)


def _wal_bytes(state: str) -> tuple[int, int] | None:
    """Bytes of WAL frames not yet reset, and the WAL file's size.

    SQLite never shrinks the WAL file here (no journal_size_limit), so its
    size is a high-water mark. After a checkpoint the next writer restarts
    the log from the top with new salts, and the frames that carry the
    header's salts are a prefix; a binary search finds its end.
    """
    path = os.path.join(state, "state.sqlite-wal")
    try:
        size = os.stat(path).st_size
    except FileNotFoundError:
        return None
    with open(path, "rb") as handle:
        header = handle.read(32)
        if len(header) < 32:
            return 0, size
        magic, _version, page_size = struct.unpack(">III", header[:12])
        if magic not in WAL_MAGIC or page_size < 512:
            return size, size
        frame = page_size + 24

        def current(index: int) -> bool:
            handle.seek(32 + index * frame + 8)
            return handle.read(8) == header[16:24]

        low, high = 0, (size - 32) // frame
        while low < high:
            middle = (low + high) // 2
            if current(middle):
                low = middle + 1
            else:
                high = middle
    return 32 + low * frame, size


def _deadline_firings(state: str, now: float) -> dict[str, list[dict[str, Any]]]:
    """Run records finished in the window whose writes hit the deadline, by loop.

    A cycle's record names no loop; a loop's names its own. The newest
    records come first; reading stops at the first one older than the window.
    """
    runs = sorted(
        glob.glob(os.path.join(state, "runs", "run_*.json")), key=os.path.getmtime, reverse=True
    )
    fired: dict[str, list[dict[str, Any]]] = {}
    for path in runs[:WRITE_DEADLINE_RECORDS]:
        try:
            with open(path) as handle:
                record = json.load(handle)
            finished = _ts(record["finished_at"])
        except Exception:
            # A bad record or stamp is [cycles-quiet]'s news, not this check's.
            continue
        if now - finished > WRITE_DEADLINE_WINDOW:
            break
        count = record.get("write_deadline_fired")
        if isinstance(count, int) and count > 0:
            fired.setdefault(str(record.get("loop") or "cycle"), []).append(record)
    return fired


def main(argv: list[str]) -> list[str]:
    parser = _parser()
    args = parser.parse_args(argv)
    loops = args.loops == 1
    inactive = [name for name in args.timers_inactive.split(",") if name]
    if not loops and args.timer_active is None:
        parser.error("--timer-active is required without --loops 1")
    if not set(inactive) <= set(LOOP_QUIET):
        parser.error(f"--timers-inactive names unknown loops: {args.timers_inactive}")
    state = args.state
    now = _ts(args.now) if args.now else time.time()
    checks = Checks()
    no_loops = LoopRecords({}, {name: [] for name in LOOP_QUIET}, None)
    ticks: LoopRecords = (
        checks.run("loop-records", lambda: _loop_records(state, now), no_loops)
        if loops
        else no_loops
    )
    # With the loops on, the cycle records are history: the cycle checks go
    # quiet, and [replay-loop] and [report-failing] read derive's ticks.
    records: list[dict[str, Any]] = [] if loops else checks.run("runs", lambda: _records(state), [])
    reported = ticks.recent["derive"][::-1] if loops else records
    noun = "derive ticks" if loops else "cycles"
    limits = checks.run("config", lambda: _config(args.config), Limits({}, {}, None))
    facts = _read_db(state, now, checks, limits)
    alerts: list[tuple[str, str]] = []
    windows = {
        "backup-upload-paused": max(args.repeat, DAY),
        "budgets-spent": max(args.repeat, DAY),
        "hold-overdue": max(args.repeat, DAY),
        "state-size": max(args.repeat, DAY),
        "size-cap-near": max(args.repeat, DAY),
        "equivalences": max(args.repeat, DAY),
        "unit-quarantined": QUARANTINE_WINDOW,
        "write-deadline": WRITE_DEADLINE_WINDOW,
        "guards": max(args.repeat, DAY),
    }

    def failing() -> None:
        if len(records) == 3 and all(r.get("failed") or r.get("error") for r in records):
            last = records[-1]
            alerts.append(
                (
                    "cycles-failing",
                    f"three consecutive cycles failed; latest {last.get('run_id')}: "
                    f"{str(last.get('error') or 'unit failures')[:200]}",
                )
            )

    checks.run("cycles-failing", failing, None)

    def replay_loop() -> None:
        # A cutoff that rewrote live rows moves its kind's replay epoch, and
        # dependents re-run and cut off in turn (D-0205). An epoch that moves
        # in three cycles running, while none of them wrote a generation of
        # that kind or of a kind it reads, is two kinds replaying each other,
        # which burns CPU while every unit succeeds. Unrelated generations,
        # such as a dancer change every cycle, do not hide it.
        if len(reported) < 3:
            return
        progress = [r.get("derivation") for r in reported]
        if not all(isinstance(item, dict) for item in progress):
            return
        written = set().union(*(set(item.get("generations") or {}) for item in progress))
        bumped = set.intersection(*(set(item.get("replay_bumps") or {}) for item in progress))
        looping = sorted(
            kind for kind in bumped if not ({kind} | REPLAY_CAUSES.get(kind, frozenset())) & written
        )
        if looping:
            alerts.append(
                (
                    "replay-loop",
                    f"replay epochs moved in each of the last three {noun} with no new"
                    f" generation that explains them: {', '.join(looping)}",
                )
            )

    checks.run("replay-loop", replay_loop, None)

    def report_failing() -> None:
        # A report query that fails is noted in the record, and a record
        # without `derivation` blinds `[replay-loop]`: health that goes dark
        # must not read as healthy (I4). Only records of the code that writes
        # these reports count; they all carry `readiness_errors`.
        broken = sorted(
            {
                key
                for record in reported
                if "readiness_errors" in record
                for key in (
                    *(f"{name}_error" for name in CYCLE_REPORTS if f"{name}_error" in record),
                    *(("derivation missing",) if "derivation" not in record else ()),
                )
            }
        )
        if broken:
            latest = next(
                (
                    str(record.get(key))
                    for record in reversed(reported)
                    for key in broken
                    if record.get(key)
                ),
                "",
            )
            alerts.append(
                (
                    "report-failing",
                    f"{noun[:-1] if loops else 'cycle'} reports failed in the last three"
                    f" {noun} ({', '.join(broken)})"
                    + (f"; latest: {latest[:200]}" if latest else "")
                    + f"; [replay-loop] cannot see those {noun}",
                )
            )

    checks.run("report-failing", report_failing, None)
    over = [
        f"{table} holds {facts[key]:,} rows, over {limit:,}"
        for table, key, limit in (
            ("derivation_equivalences", "equivalence_rows", EQUIVALENCE_ROWS),
            ("source_equivalences", "source_equivalence_rows", SOURCE_EQUIVALENCE_ROWS),
        )
        if facts.get(key, 0) > limit
    ]
    if over:
        alerts.append(
            (
                "equivalences",
                "; ".join(over) + "; nothing collects them before invalidation step 3,"
                " which now starts ahead of phase 2 (D-0208)",
            )
        )
    if facts.get("size_cap"):
        alerts.append(
            (
                "size-cap-pause",
                f"all work is paused: {SIZE_CAP_REASON};"
                " see docs/guides/operation.md#retention-plans-and-holds",
            )
        )
    hold_file = os.path.join(state, "operator-hold")
    hold_since = checks.run(
        "operator-hold",
        lambda: os.stat(hold_file).st_mtime if os.path.exists(hold_file) else None,
        None,
    )
    held = hold_since is not None or facts.get("paused", False)
    # A hold silences the stall checks, so a forgotten one must still be
    # heard: AGENTS.md requires a hold older than 48 hours to surface.
    overdue = []
    if hold_since is not None and now - hold_since > HOLD_OVERDUE_SECONDS:
        overdue.append(f"operator-hold file is {int((now - hold_since) // 3600)} h old")
    for reason, since in facts.get("pause_since", []):
        if now - since > HOLD_OVERDUE_SECONDS:
            overdue.append(f"all-scope pause '{reason[:80]}' is {int((now - since) // 3600)} h old")
    for reason in facts.get("pause_undated", []):
        overdue.append(f"all-scope pause '{reason[:80]}' has no created_at, age unknown")
    if overdue:
        alerts.append(
            (
                "hold-overdue",
                "; ".join(overdue) + f", over the {HOLD_OVERDUE_SECONDS // 3600} h limit;"
                " review it or record why it stays (docs/guides/operation.md, holds)",
            )
        )
    if not held and loops and inactive:
        timers = ", ".join(f"swingset-{name}.timer" for name in inactive)
        alerts.append(
            (
                "timer-inactive",
                f"{timers} not active and nothing holds the worker;"
                " start the timers or write operator-hold",
            )
        )
    elif not held and not loops and args.timer_active == 0:
        alerts.append(
            (
                "timer-inactive",
                "swingset-cycle.timer is not active and nothing holds the worker;"
                " start the timer or write operator-hold",
            )
        )
    watching = not held and not loops and args.timer_active == 1
    # Fetching and derive each stop with their own timer.
    fetching = watching or (not held and loops and "acquire" not in inactive)
    deriving = watching or (not held and loops and "derive" not in inactive)

    def loop_failing() -> None:
        for name in LOOP_QUIET:
            rows = ticks.recent[name]
            if len(rows) == LOOP_FAILING and all(r.get("failed") or r.get("error") for r in rows):
                reason = next(
                    (
                        str(rows[0][key])
                        for key in ("error", "build_error", "correction_error")
                        if rows[0].get(key)
                    ),
                    "no error named, see its run record",
                )
                alerts.append(
                    (
                        "loop-failing",
                        f"{name}: the last {LOOP_FAILING} ticks failed; latest: {reason[:160]}",
                    )
                )

    def loop_quiet() -> None:
        if args.backup_active:
            # A backup holds the state exclusive: every loop's tick skips
            # before writing a record until it ends; [backup-*] watch it.
            return
        for name, limit in LOOP_QUIET.items():
            if name in inactive:
                continue
            finished = ticks.newest.get(name)
            if finished is not None:
                if now - finished > limit:
                    alerts.append(
                        (
                            "loop-quiet",
                            f"{name}: no tick has finished since {_at(finished)}, over"
                            f" {_span(limit)} while unheld",
                        )
                    )
            elif ticks.earliest is not None and now - ticks.earliest > limit:
                alerts.append(
                    (
                        "loop-quiet",
                        f"{name}: no tick on record since {_at(ticks.earliest)}, over"
                        f" {_span(limit)} while unheld",
                    )
                )

    def loop_idle() -> None:
        progress: dict[str, float | None] = facts.get("loop_progress", {})
        windows = dict(LOOP_IDLE)
        released = ticks.latest.get("release") or {}
        interval = released.get("build_interval_seconds")
        windows["release"] = (
            int(interval) if isinstance(interval, int) else BUILD_INTERVAL_SECONDS
        ) + RELEASE_IDLE_SLACK
        waiting = sum(ready.values())
        for name, limit in windows.items():
            if name in inactive:
                continue
            if name == "acquire" and facts.get("guards_recent", 0):
                continue
            if name == "derive" and not waiting:
                continue
            if name == "release" and released.get("build_deferred") == "interval":
                # A build (or a reuse, which is no progress) is inside its interval.
                continue
            if name not in progress:
                # No tick has written loop_state yet: [loop-quiet] watches it.
                continue
            # A loop that ticked but never progressed is judged from the
            # oldest tick record read.
            since = progress[name] if progress[name] is not None else ticks.earliest
            if since is not None and now - since > limit:
                alerts.append(
                    (
                        "loop-idle",
                        f"{name}: no progress since {_at(since)}, over {_span(limit)} while"
                        " unheld; its ticks finish but do nothing (read its run records)",
                    )
                )

    def plan_stale() -> None:
        planned = facts.get("planned")
        if planned is not None and now - planned > PLAN_STALE_SECONDS:
            alerts.append(
                (
                    "plan-stale",
                    f"the newest acquisition_plan row was planned at {_at(planned)}, over"
                    f" {PLAN_STALE_SECONDS // 3600} h ago; acquire offers only planned pages",
                )
            )

    ready: Counter[str] = facts.get("ready", Counter())
    if loops:
        checks.run("loop-failing", loop_failing, None)
        if not held:
            checks.run("loop-quiet", loop_quiet, None)
            checks.run("loop-idle", loop_idle, None)
            if "plan" not in inactive:
                checks.run("plan-stale", plan_stale, None)

    def quiet() -> None:
        finished = records[-1].get("finished_at") if records else None
        if finished:
            age = now - _ts(finished)
            if age > args.quiet:
                alerts.append(
                    (
                        "cycles-quiet",
                        f"no cycle has finished for {int(age // 60)} minutes while unheld",
                    )
                )

    if watching:
        checks.run("cycles-quiet", quiet, None)
    if fetching and "requested" in facts:
        age = now - facts["requested"]
        if age > args.stale_fetch:
            # A host that spent its daily request or byte budget stops by
            # design until the UTC day turns. When every host that fetched
            # today has spent one, the silence is expected.
            spent = [
                host
                for host, requests, size in facts.get("budgets") or []
                if (host in limits.requests and requests >= limits.requests[host])
                or (host in limits.bytes and (size or 0) >= limits.bytes[host])
            ]
            active = [host for host, _, _ in facts.get("budgets") or []]
            if spent and len(spent) == len(active):
                alerts.append(
                    (
                        "budgets-spent",
                        f"every host that fetched today has spent its daily budget"
                        f" ({', '.join(spent)}); no request for {int(age // 3600)} hours",
                    )
                )
            else:
                text = f"no request has been made for {int(age // 3600)} hours while unheld"
                if "stored" in facts:
                    text += f"; newest stored page {int((now - facts['stored']) // 3600)} hours old"
                if spent:
                    text += f"; spent today: {', '.join(spent)}"
                if ready["parse"]:
                    text += (
                        f"; {ready['parse']} parse units wait, backpressure may be holding requests"
                    )
                alerts.append(("stale-fetch", text))
    if deriving:
        notes = facts.get("progress_notes", {})
        stalled = [
            f"{stage} {_duration(now - clock)} with {ready[stage]} ready"
            + (f" ({notes[stage]})" if stage in notes else "")
            for stage, clock in sorted(facts.get("progress", {}).items())
            if ready[stage] and now - clock > args.stale_progress
        ]
        if stalled:
            text = "no progress while unheld: " + "; ".join(stalled)
            if facts.get("waiting_links"):
                text += f"; {facts['waiting_links']} link units wait for the paced history"
            if facts.get("waiting_inventory"):
                text += f"; {facts['waiting_inventory']} project units wait for the paced inventory"
            alerts.append(("stale-progress", text))
    if facts.get("legacy"):
        alerts.append(
            (
                "gate-legacy-spacing",
                "request spacing unknown, so the fetch gate skips every request with no expiry,"
                f" for {', '.join(facts['legacy'])} (D-0190)",
            )
        )
    if facts.get("blocked"):
        hosts = ", ".join(f"{host} until {_utc(until)}" for host, until in facts["blocked"])
        alerts.append(("gate-host-blocked", f"hosts paused as Blocked: {hosts}"))
    if facts.get("failing"):
        hosts = ", ".join(
            f"{host} {reason} x{streak}" + (f" until {_utc(until)}" if until else "")
            for host, reason, streak, until in facts["failing"]
        )
        alerts.append(("host-failing", f"hosts answering only 429 or 5xx: {hosts}"))
    # One line per quarantined unit, or one line by class for a storm (F15).
    # A quarantine stops the unit's retries, so no cycle fails for it again.
    quarantined = facts.get("quarantined", [])
    if 0 < len(quarantined) <= QUARANTINE_LINES:
        for stage, kind, unit, tries, failure, error in quarantined:
            alerts.append(
                (
                    "unit-quarantined",
                    f"{stage}/{kind}/{unit} after {tries} x {failure}: {(error or '')[:120]}",
                )
            )
    elif quarantined:
        classes = Counter(str(row[4]) for row in quarantined)
        alerts.append(
            (
                "unit-quarantined",
                f"{len(quarantined)} units quarantined: "
                + ", ".join(f"{name} x {count}" for name, count in classes.most_common()),
            )
        )
    if facts.get("lease_stuck"):
        rows = facts["lease_stuck"]
        named = ", ".join(
            f"{stage}/{kind}/{unit} ({owner}, until {_utc(until)})"
            for stage, kind, unit, owner, until in rows[:5]
        )
        more = f" and {len(rows) - 5} more" if len(rows) > 5 else ""
        alerts.append(
            (
                "lease-stuck",
                f"{len(rows)} work-queue leases ended over {LEASE_STUCK_SECONDS // 60} minutes"
                f" ago and were not taken over: {named}{more}",
            )
        )

    # A guard that holds fetches past its expiry and is still being met (D-0219):
    # one line per guard, or one line by reason when many expire at once.
    expired = facts.get("guard_expired", [])
    if 0 < len(expired) <= GUARD_LINES:
        for subject, reason, since, expires in expired:
            alerts.append(
                (
                    "guard-expired",
                    f"{subject} held by {reason} since {_utc(since)},"
                    f" past its expiry at {_utc(expires)}",
                )
            )
    elif expired:
        reasons = Counter(str(row[1]) for row in expired)
        alerts.append(
            (
                "guard-expired",
                f"{len(expired)} guards past their expiry: "
                + ", ".join(f"{name} x {count}" for name, count in reasons.most_common()),
            )
        )
    if facts.get("guard_counts"):
        counts = facts["guard_counts"]
        alerts.append(
            (
                "guards",
                f"{sum(count for _, count in counts)} guard rows: "
                + ", ".join(f"{reason} x {count}" for reason, count in counts),
            )
        )

    def write_deadline() -> None:
        # Every write is small by construction (I2); the 60 s alarm firing
        # means one was not, and its unit rolled back (D-0217).
        for name, rows in sorted(_deadline_firings(state, now).items()):
            latest = rows[0]
            total = sum(int(row["write_deadline_fired"]) for row in rows)
            longest = max(float(row.get("write_seconds_max") or 0) for row in rows)
            alerts.append(
                (
                    "write-deadline",
                    f"{name}: {total} writes cut off at the deadline in {len(rows)} runs in the"
                    f" last {WRITE_DEADLINE_WINDOW // 3600} h, latest {latest.get('run_id')};"
                    f" longest write {longest:.0f} s",
                )
            )

    checks.run("write-deadline", write_deadline, None)

    def wal_large() -> None:
        found = _wal_bytes(state)
        if found is not None and found[0] > args.wal_large:
            live, size = found
            alerts.append(
                (
                    "wal-large",
                    f"state.sqlite-wal holds {live / 1e9:.1f} GB of frames not yet reset"
                    f" (file {size / 1e9:.1f} GB), over {args.wal_large / 1e9:.1f} GB:"
                    " checkpoints are not keeping up, usually behind a long read",
                )
            )

    checks.run("wal-large", wal_large, None)
    orphans = facts.get("building_orphans", [])
    if orphans and not held:
        named = ", ".join(
            f"{stage}/{kind}/{unit} ({stored} of {rows if rows is not None else '?'} rows,"
            f" last {_utc(updated)})"
            for stage, kind, unit, stored, rows, updated in orphans[:BUILDING_LINES]
        )
        more = f" and {len(orphans) - BUILDING_LINES} more" if len(orphans) > BUILDING_LINES else ""
        alerts.append(
            (
                "building-orphans",
                f"{len(orphans)} staged generations not advanced for"
                f" {BUILDING_ORPHAN_SECONDS // 3600} h: {named}{more}",
            )
        )

    incomplete = (ticks.latest.get("maintain") or {}).get("incomplete_generations") or {}
    if (
        isinstance(incomplete, dict)
        and int(incomplete.get("rows") or 0) > INCOMPLETE_GENERATIONS
        and not held
    ):
        alerts.append(
            (
                "incomplete-generations",
                f"{incomplete['rows']} partial generations ({incomplete.get('refs', 0)}"
                " references) that nothing finishes or collects; see docs/reference/state.md",
            )
        )

    def disk() -> None:
        fields = args.disk.split()
        if len(fields) >= 2:
            free = int(fields[1]) - int(fields[0])
            # A backup holds a second checkpoint until it prunes the first;
            # its room check kept the headroom free, so only less is news.
            floor = (
                min(args.disk_min_free, BACKUP_HEADROOM_BYTES)
                if args.backup_active
                else args.disk_min_free
            )
            if free < floor:
                alerts.append(
                    (
                        "disk-low",
                        f"disk quota has {free / 1e9:.1f} GB free of {int(fields[1]) / 1e9:.1f} GB;"
                        " df does not show this"
                        + ("; a backup is running" if args.backup_active else ""),
                    )
                )

    checks.run("disk", disk, None)

    def state_size() -> None:
        size = _tree_bytes(state, skip=STATE_SIZE_SKIP)
        if size > args.state_size_alert:
            alerts.append(
                (
                    "state-size",
                    f"state without checkpoints/, venv/ and uv-cache/ is {size / 1e9:.1f} GB, over"
                    f" {args.state_size_alert / 1e9:.0f} GB; architecture phase 4 starts at"
                    " about 55 GB (docs/plans/architecture.md), and the pipeline pauses at the"
                    " retention size cap",
                )
            )

    checks.run("state-size", state_size, None)

    def size_cap_near() -> None:
        # The retention cap pauses the whole pipeline (D-0208). Say so before
        # it does, while there is time to act.
        cap = limits.size_cap
        path = os.path.join(state, "state.sqlite")
        if cap is None or not os.path.exists(path):
            return
        size = os.stat(path).st_size
        if size > 0.9 * cap:
            alerts.append(
                (
                    "size-cap-near",
                    f"state.sqlite is {size / 1e9:.1f} GB, over 90% of the"
                    f" {cap / 1e9:.0f} GB retention cap where the pipeline pauses (D-0208)",
                )
            )

    checks.run("size-cap-near", size_cap_near, None)
    # Backup lines are never silenced by a hold, which also stops the backup.
    no_checkpoints: tuple[tuple[str, float] | None, list[str]] = (None, [])
    newest, failed_copies = checks.run("checkpoints", lambda: _checkpoints(state), no_checkpoints)
    problems = []
    if args.backup_failed:
        problems.append("swingset-backup.service failed; read journalctl -u swingset-backup")
    for name in failed_copies:
        problems.append(
            f"checkpoint {name} failed its read-back; inspect it, then run"
            f" swingset backup --remove-checkpoint {name}"
        )
    if problems:
        alerts.append(("backup-failed", "; ".join(problems)))
    # The daily digest runs under a 5 GB cap it nears (the publication
    # report's closure check); a killed digest must not go dark (I4).
    if args.summary_failed:
        alerts.append(
            (
                "summary-failed",
                "swingset-summary.service failed, possibly at its 5 GB memory cap;"
                " read journalctl -u swingset-summary",
            )
        )
    # Upload is paused while the transport refuses the checkpoint (D-0203):
    # the timer keeps one local copy and the Mac byte copy is the only one
    # off the machine.
    if args.backup_upload == 0:
        if newest is None:
            where = "there is no complete local checkpoint"
            alerts.append(("backup-stale", "no complete run_* checkpoint in checkpoints/"))
        else:
            name, written = newest
            age = now - written
            where = f"newest local checkpoint {name} is {int(age // 3600)} h old"
            if age > args.backup_max_age:
                alerts.append(
                    (
                        "backup-stale",
                        f"newest local checkpoint {name} is {int(age // 3600)} h old,"
                        f" over the {args.backup_max_age // 3600} h limit",
                    )
                )
        alerts.append(
            (
                "backup-upload-paused",
                "remote backup upload is paused (services.swingset.backupUpload = false);"
                f" {where}; keep the Mac byte copy current weekly",
            )
        )
    if checks.failed:
        names = ", ".join(name for name, _ in checks.failed)
        alerts.insert(0, ("watchdog-blind", f"checks failed: {names}: {checks.failed[0][1][:200]}"))
    by_key, by_line = _last_logged(state)
    lines = []
    for key, text in alerts:
        line = f"[{key}] {text}"
        last = by_line.get(line) if key in SAME_TEXT_KEYS else by_key.get(key)
        if last is None or now - last >= windows.get(key, args.repeat):
            lines.append(line)
    return lines


def run(argv: list[str]) -> list[str]:
    """`main`, with a last guard: a crash prints one line instead of nothing.

    An argument error still exits nonzero; the unit's wrapper turns any
    nonzero exit into a `[watchdog-error]` line of its own.
    """
    try:
        return main(argv)
    except Exception as exc:
        return [f"[watchdog-error] {type(exc).__name__}: {exc}"[:400]]


if __name__ == "__main__":
    print("\n".join(run(sys.argv[1:])))
