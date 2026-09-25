"""Checks behind the swingset-watchdog unit (D-0188, D-0202, D-0203).

Prints one alert per line and nothing when all is well. Each line starts
with a stable key in brackets, such as `[stale-fetch]`. A key already logged
in ALERTS.log within `--repeat` seconds is not printed again; for the keys in
`SAME_TEXT_KEYS` only the same text is held back, so a new host repeats at
once. The unit's shell wrapper reads the timer, backup and summary unit states
and the disk quota, passes them in, and appends whatever this prints to ALERTS.log
with a UTC stamp. Standard library only: the unit runs it with the system
python3, not the project environment.

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
    }
)
#: A host whose last this many responses were all 429 or 5xx is failing.
#: Its backoff (fetch/politeness.py) is then two hours or more.
FAILING_STREAK = 4
#: A queued unit whose last this many finished attempts all failed with the
#: same reason fails every retry (schedule/derive.py gives each a clock).
UNIT_FAILING_ATTEMPTS = 3
#: Reasons retried by design: an I/O error retries in a minute, and a page
#: kind this runtime does not know waits a month (D-0182).
EXPECTED_RETRIES = frozenset({"unit_io_error", "unsupported_page_kind"})
#: An I/O error that repeats is no longer a passing one (a corrupt blob, a
#: permission error): it fails the unit once its last this many attempts
#: all hit it, or once the oldest of an unbroken run of at least
#: UNIT_FAILING_ATTEMPTS is older than IO_ERROR_SECONDS.
IO_ERROR_ATTEMPTS = 20
IO_ERROR_SECONDS = 3600
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
# test, which needs the package. A code-scope or environment accept and a
# replay epoch queue hints for the units they make stale (state/work.
# affected_work, derivations.bump_replay), so those show here; a unit made
# stale only by a new upstream generation is still missed, so the check errs
# toward silence. A re-queue moves `enqueued_at`
# (state/work.enqueue), so churn that re-queues every ready unit of a stage
# restarts that stage's clock. project/history is paced by
# history_interval_seconds (D-0199), so it has a stage of its own,
# HISTORY, clocked from when it came due; the link units that wait for it
# are handled in _link_clock.
_READY = """
SELECT p.stage, p.unit_kind, p.enqueued_at, a.outcome, a.retry_at,
    coalesce(g.retry_generation, 0) > a.retry_generation
FROM pending_work p
LEFT JOIN work_generations g USING (stage, unit_kind, unit_id)
LEFT JOIN work_attempts a ON a.attempt_id = (
    SELECT attempt_id FROM work_attempts w
    WHERE w.stage = p.stage AND w.unit_kind = p.unit_kind AND w.unit_id = p.unit_id
    ORDER BY w.attempt_id DESC LIMIT 1)
"""
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
    # Queued units whose newest finished attempt failed for an unexpected
    # reason, and the newest finished attempts of one unit.
    "unit_failed": "SELECT p.stage, p.unit_kind, p.unit_id FROM pending_work p "
    "JOIN work_attempts a ON a.attempt_id = (SELECT attempt_id FROM work_attempts w "
    "WHERE w.stage = p.stage AND w.unit_kind = p.unit_kind AND w.unit_id = p.unit_id "
    "AND w.finished_at IS NOT NULL ORDER BY w.attempt_id DESC LIMIT 1) "
    "WHERE a.outcome = 'transient' ORDER BY 1, 2, 3",
    "unit_attempts": "SELECT outcome, reason_code, finished_at FROM work_attempts "
    "WHERE stage = :stage AND unit_kind = :kind AND unit_id = :unit "
    "AND finished_at IS NOT NULL ORDER BY attempt_id DESC LIMIT :limit",
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
        required=True,
        help="1 when swingset-cycle.timer is active",
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


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60} h {minutes % 60} m"


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


def _failing(attempts: list[tuple[str, str | None, str | None]], now: float) -> bool:
    """Whether a unit's newest finished attempts, newest first, fail every retry.

    The last UNIT_FAILING_ATTEMPTS failed the same way, for a reason not
    retried by design. An I/O error counts once it repeats: all of the last
    IO_ERROR_ATTEMPTS, or an unbroken run whose oldest attempt is older than
    IO_ERROR_SECONDS.
    """
    run = []
    for outcome, reason, finished in attempts:
        if outcome != "transient" or reason != attempts[0][1]:
            break
        run.append(finished)
    if len(run) < UNIT_FAILING_ATTEMPTS:
        return False
    reason = attempts[0][1]
    if reason == "unit_io_error":
        oldest = run[-1]
        return len(run) >= IO_ERROR_ATTEMPTS or (
            oldest is not None and now - _ts(oldest) > IO_ERROR_SECONDS
        )
    return reason not in EXPECTED_RETRIES


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

    def ready() -> tuple[Counter[str], dict[str, float]]:
        counted: Counter[str] = Counter()
        oldest: dict[str, float] = {}
        for stage, kind, enqueued, outcome, retry_at, retried in db.execute(QUERIES["ready"]):
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

    def unit_failing() -> list[tuple[str, str]]:
        # Every retry of such a unit is `transient` with a clock, so no two
        # failing cycles are consecutive and [cycles-failing] never sees it.
        found: list[tuple[str, str]] = []
        for stage, kind, unit in db.execute(QUERIES["unit_failed"]).fetchall():
            attempts = db.execute(
                QUERIES["unit_attempts"],
                {"stage": stage, "kind": kind, "unit": unit, "limit": IO_ERROR_ATTEMPTS},
            ).fetchall()
            if _failing(attempts, now):
                found.append((f"{stage}/{kind}/{unit}", str(attempts[0][1])))
        return found

    failed_units = checks.run("unit-failing", unit_failing, [])
    if failed_units:
        facts["unit_failing"] = failed_units
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

    A backup, gc or other command writes a record with a `command` field; a
    cycle's summary has none. Those records must neither reset
    `[cycles-failing]` nor hold `[cycles-quiet]` back.
    """
    runs = sorted(glob.glob(os.path.join(state, "runs", "run_*.json")), key=os.path.getmtime)
    records: list[dict[str, Any]] = []
    for path in reversed(runs):
        try:
            with open(path) as handle:
                record = json.load(handle)
        except Exception:
            continue
        if isinstance(record, dict) and record.get("command", "cycle") == "cycle":
            records.append(record)
            if len(records) == 3:
                break
    return records[::-1]


def main(argv: list[str]) -> list[str]:
    args = _parser().parse_args(argv)
    state = args.state
    now = _ts(args.now) if args.now else time.time()
    checks = Checks()
    records: list[dict[str, Any]] = checks.run("runs", lambda: _records(state), [])
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
        if len(records) < 3:
            return
        progress = [r.get("derivation") for r in records]
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
                    f"replay epochs moved in each of the last three cycles with no new"
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
                for record in records
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
                    for record in reversed(records)
                    for key in broken
                    if record.get(key)
                ),
                "",
            )
            alerts.append(
                (
                    "report-failing",
                    f"cycle reports failed in the last three cycles ({', '.join(broken)})"
                    + (f"; latest: {latest[:200]}" if latest else "")
                    + "; [replay-loop] cannot see those cycles",
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
    if not held and args.timer_active == 0:
        alerts.append(
            (
                "timer-inactive",
                "swingset-cycle.timer is not active and nothing holds the worker;"
                " start the timer or write operator-hold",
            )
        )
    watching = not held and args.timer_active == 1

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
    ready: Counter[str] = facts.get("ready", Counter())
    if watching and "requested" in facts:
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
    if watching:
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
    if facts.get("unit_failing"):
        units = facts["unit_failing"]
        named = ", ".join(f"{unit} ({reason})" for unit, reason in units[:5])
        more = f" and {len(units) - 5} more" if len(units) > 5 else ""
        alerts.append(
            (
                "unit-failing",
                f"{len(units)} queued units failed their last {UNIT_FAILING_ATTEMPTS} attempts"
                f" for the same reason: {named}{more}",
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
