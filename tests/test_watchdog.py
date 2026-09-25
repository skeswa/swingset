import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest

from swingset.backup.pruning import TIMER_NAME
from swingset.cli import HOLD_OVERDUE_SECONDS
from swingset.config import load_config
from swingset.state.db import open_database
from swingset.state.retention import SIZE_CAP_REASON

SCRIPT = Path(__file__).resolve().parents[1] / "nix" / "watchdog.py"
NOW = "2026-09-25T16:10:00+00:00"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("swingset_watchdog", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watchdog = _load()
# The retry after a failed database read would only slow the tests down.
watchdog.RETRY_SECONDS = 0


def _run(
    state: Path,
    *,
    timer: int = 1,
    disk: str = "",
    quiet: int = 7200,
    stale_fetch: int = 7200,
    stale_progress: int = 7200,
    repeat: int = 7200,
    min_free: int = 100_000_000_000,
    now: str = NOW,
    backup: list[str] | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    result: list[str] = watchdog.main(
        [
            *(backup or []),
            *(extra or []),
            "--state",
            str(state),
            "--quiet",
            str(quiet),
            "--stale-fetch",
            str(stale_fetch),
            "--stale-progress",
            str(stale_progress),
            "--repeat",
            str(repeat),
            "--disk",
            disk,
            "--disk-min-free",
            str(min_free),
            "--timer-active",
            str(timer),
            "--now",
            now,
        ]
    )
    return result


def _keys(lines: list[str]) -> list[str]:
    return [line.split("]", 1)[0].lstrip("[") for line in lines]


def _write_runs(state: Path, *records: dict[str, object]) -> None:
    runs = state / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    base = datetime.fromisoformat(NOW).timestamp() - 3600
    for index, record in enumerate(records):
        path = runs / f"run_{index:04d}.json"
        path.write_text(json.dumps(record))
        # The watchdog orders runs by mtime, not by name.
        os.utime(path, (base + index, base + index))


#: When synthetic pending units were queued: before every other stamp here.
QUEUED = "2026-09-24T00:00:00+00:00"

SCHEMA = """
CREATE TABLE snapshots (fetched_at TEXT);
CREATE TABLE watches (watch_id TEXT PRIMARY KEY, last_checked_at TEXT);
CREATE TABLE scheduler_requests (action_id TEXT PRIMARY KEY, issued_at TEXT NOT NULL);
CREATE TABLE derivation_generations (generation_id TEXT PRIMARY KEY, stage TEXT NOT NULL,
    unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE derivation_scopes (stage TEXT, unit_kind TEXT, unit_id TEXT,
    materialized_generation_id TEXT, PRIMARY KEY(stage, unit_kind, unit_id));
CREATE TABLE work_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    retry_generation INTEGER NOT NULL DEFAULT 0, finished_at TEXT,
    outcome TEXT NOT NULL, retry_at TEXT);
CREATE TABLE pending_work (stage TEXT, unit_kind TEXT, unit_id TEXT, enqueued_at TEXT NOT NULL,
    PRIMARY KEY(stage, unit_kind, unit_id));
CREATE TABLE work_generations (stage TEXT, unit_kind TEXT, unit_id TEXT,
    retry_generation INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(stage, unit_kind, unit_id));
CREATE TABLE operator_pauses (scope_kind TEXT, scope_id TEXT, until_at TEXT, reason TEXT,
    created_at TEXT, PRIMARY KEY(scope_kind, scope_id));
CREATE TABLE host_request_spacing (host TEXT PRIMARY KEY, gap_seconds REAL);
CREATE TABLE host_budget (host TEXT, day TEXT, requests INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(host, day));
CREATE TABLE hosts (host TEXT PRIMARY KEY, paused_until TEXT, pause_reason TEXT,
    pause_streak INTEGER NOT NULL DEFAULT 0);
"""


class State:
    """A synthetic state database holding only the columns the watchdog reads.

    test_every_query_runs_against_the_real_schema keeps these names honest.
    """

    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path / "state.sqlite")
        self.db.executescript(SCHEMA)
        self._ids = 0

    def _id(self) -> str:
        self._ids += 1
        return f"id{self._ids}"

    def sql(self, statement: str, *params: object) -> "State":
        self.db.execute(statement, params)
        self.db.commit()
        return self

    def fetched(self, at: str) -> "State":
        """A request that stored a page: a snapshot and a checked watch."""
        self.sql("INSERT INTO snapshots VALUES (?)", at)
        return self.requested(at)

    def requested(self, at: str) -> "State":
        """A request that stored nothing, such as a 304."""
        return self.sql("INSERT INTO watches VALUES (?,?)", self._id(), at)

    def issued(self, at: str) -> "State":
        return self.sql("INSERT INTO scheduler_requests VALUES (?,?)", self._id(), at)

    def generation(self, at: str, *, stage: str = "link", kind: str = "event") -> "State":
        return self.sql(
            "INSERT INTO derivation_generations VALUES (?,?,?,'u',?)", self._id(), stage, kind, at
        )

    def history(self, materialized: str | None, *, earlier: tuple[str, ...] = ()) -> "State":
        """A pending project/history unit whose current output was made then.

        `earlier` are older history outputs still stored.
        """
        for at in earlier:
            self.generation(at, stage="project", kind="history")
        generation = None
        if materialized is not None:
            generation = self._id()
            self.sql(
                "INSERT INTO derivation_generations VALUES (?,'project','history','all',?)",
                generation,
                materialized,
            )
        self.sql("INSERT INTO derivation_scopes VALUES ('project','history','all',?)", generation)
        return self.pending("all", stage="project", kind="history")

    def pause(
        self,
        reason: str,
        *,
        until: str | None = None,
        created: str | None = "2026-09-25T12:00:00+00:00",
        kind: str = "all",
        scope: str = "all",
    ) -> "State":
        return self.sql(
            "INSERT INTO operator_pauses VALUES (?,?,?,?,?)", kind, scope, until, reason, created
        )

    def budget(
        self,
        host: str,
        requests: int,
        *,
        day: str = "2026-09-25",
        spaced: bool = True,
        size: int = 0,
    ) -> "State":
        """Requests and bytes spent on a day; `spaced` also records the host's spacing."""
        if spaced:
            self.sql("INSERT OR IGNORE INTO host_request_spacing VALUES (?, 5.0)", host)
        return self.sql("INSERT INTO host_budget VALUES (?,?,?,?)", host, day, requests, size)

    def attempt(
        self,
        unit: str,
        outcome: str,
        *,
        stage: str = "link",
        kind: str = "event",
        finished: str | None = "2026-09-25T10:00:00+00:00",
        retry_at: str | None = None,
        retry_generation: int = 0,
    ) -> "State":
        return self.sql(
            "INSERT INTO work_attempts(stage,unit_kind,unit_id,retry_generation,finished_at,"
            "outcome,retry_at) VALUES (?,?,?,?,?,?,?)",
            stage,
            kind,
            unit,
            retry_generation,
            finished,
            outcome,
            retry_at,
        )

    def pending(
        self, unit: str, *, stage: str = "link", kind: str = "event", enqueued: str = QUEUED
    ) -> "State":
        self.sql("INSERT INTO pending_work VALUES (?,?,?,?)", stage, kind, unit, enqueued)
        return self.sql(
            "INSERT INTO work_generations VALUES (?,?,?,0) ON CONFLICT DO NOTHING",
            stage,
            kind,
            unit,
        )

    def close(self) -> None:
        self.db.close()


def _write_db(state: Path, *fetched_at: str) -> None:
    """Pages fetched at these times and nothing else."""
    db = State(state)
    for at in fetched_at:
        db.fetched(at)
    db.close()


def _healthy(state: Path) -> State:
    _write_runs(
        state,
        {"run_id": "r1", "finished_at": "2026-09-25T15:40:00+00:00"},
        {"run_id": "r2", "finished_at": "2026-09-25T15:55:00+00:00"},
        {"run_id": "r3", "finished_at": "2026-09-25T16:05:00+00:00"},
    )
    return (
        State(state)
        .fetched("2026-09-25T15:00:00+00:00")
        .generation("2026-09-25T15:30:00+00:00")
        .pending("e1")
        .sql("INSERT INTO host_request_spacing VALUES ('scoring.dance', 5.0)")
        .sql("INSERT INTO hosts VALUES ('scoring.dance', NULL, NULL, 0)")
    )


def _stalled(state: Path) -> State:
    """Fetching and derive both stopped three hours ago with one ready unit."""
    return (
        State(state)
        .fetched("2026-09-25T13:10:00+00:00")
        .generation("2026-09-25T13:10:00+00:00")
        .pending("e1")
    )


def _log(state: Path, *lines: str) -> None:
    with open(state / "ALERTS.log", "a") as handle:
        for line in lines:
            handle.write(line + "\n")


def test_the_script_copies_the_size_cap_reason() -> None:
    assert watchdog.SIZE_CAP_REASON == SIZE_CAP_REASON


def test_the_script_copies_the_package_constants() -> None:
    # FAILED_PREFIX is checked with the package code that writes it, in
    # tests/backup/test_backup_prune_wiring.py.
    assert watchdog.TIMER_NAME.pattern == TIMER_NAME.pattern
    assert watchdog.HOLD_OVERDUE_SECONDS == HOLD_OVERDUE_SECONDS


def test_every_query_runs_against_the_real_schema(tmp_path: Path) -> None:
    """A migration that renames what the watchdog reads fails here, not silently."""
    with open_database(tmp_path, lock=False):
        pass
    db = sqlite3.connect(f"file:{tmp_path / 'state.sqlite'}?mode=ro&immutable=1", uri=True)
    try:
        for sql in watchdog.QUERIES.values():
            assert isinstance(
                db.execute(sql, {"stage": "link", "day": "2026-09-25", "streak": 4}).fetchall(),
                list,
            )
    finally:
        db.close()
    assert _run(tmp_path, extra=["--config", "config"]) == []


def test_the_script_reads_the_shipped_config_as_the_package_does() -> None:
    config = load_config(Path("config"))
    limits = watchdog._config("config")
    assert limits.history_interval == config.scheduler.history_interval_seconds == 43200
    assert limits.requests == {
        host: config.host(host).daily_request_budget for host in config.hosts
    }
    assert limits.bytes == {
        host: budget
        for host in config.hosts
        if (budget := config.host(host).daily_byte_budget) is not None
    }
    assert limits.bytes == {"danceconvention.net": 300_000_000}


def test_a_healthy_state_prints_nothing(tmp_path: Path) -> None:
    _healthy(tmp_path).close()
    assert _run(tmp_path, disk="100000000000 300000000000 2026-09-25T16:00:00Z") == []


def test_three_failed_cycles_alert_with_the_latest_error(tmp_path: Path) -> None:
    _write_runs(
        tmp_path,
        {"run_id": "r1", "failed": True, "finished_at": "2026-09-25T16:00:00+00:00"},
        {"run_id": "r2", "error": "x", "finished_at": "2026-09-25T16:01:00+00:00"},
        {
            "run_id": "r3",
            "failed": True,
            "error": "boom " * 60,
            "finished_at": "2026-09-25T16:02:00+00:00",
        },
    )
    assert _run(tmp_path) == [
        "[cycles-failing] three consecutive cycles failed; latest r3: " + ("boom " * 60)[:200]
    ]


def test_three_failed_cycles_without_an_error_name_unit_failures(
    tmp_path: Path,
) -> None:
    _write_runs(
        tmp_path,
        *[{"run_id": f"r{i}", "failed": True, "finished_at": NOW} for i in range(1, 4)],
    )
    assert _run(tmp_path) == [
        "[cycles-failing] three consecutive cycles failed; latest r3: unit failures"
    ]


def test_failure_alert_ignores_a_hold_and_an_inactive_timer(tmp_path: Path) -> None:
    _write_runs(tmp_path, *[{"run_id": f"r{i}", "failed": True} for i in range(1, 4)])
    (tmp_path / "operator-hold").touch()
    assert _run(tmp_path, timer=0) == [
        "[cycles-failing] three consecutive cycles failed; latest r3: unit failures"
    ]


def test_other_commands_do_not_reset_the_failure_count(tmp_path: Path) -> None:
    # A backup or gc between cycles writes a run record with a command.
    _write_runs(
        tmp_path,
        {"run_id": "r1", "failed": True, "finished_at": NOW},
        {"run_id": "r2", "failed": True, "finished_at": NOW},
        {"run_id": "b1", "command": "backup", "failed": False, "finished_at": NOW},
        {"run_id": "r3", "failed": True, "finished_at": NOW},
        {"run_id": "g1", "command": "gc", "failed": False, "finished_at": NOW},
    )
    assert _run(tmp_path) == [
        "[cycles-failing] three consecutive cycles failed; latest r3: unit failures"
    ]


def test_other_commands_do_not_count_as_a_finished_cycle(tmp_path: Path) -> None:
    _write_runs(
        tmp_path,
        {"run_id": "r1", "finished_at": "2026-09-25T13:00:00+00:00"},
        {"run_id": "b1", "command": "backup", "finished_at": "2026-09-25T16:00:00+00:00"},
    )
    assert _run(tmp_path) == ["[cycles-quiet] no cycle has finished for 190 minutes while unheld"]


def test_two_failures_after_a_success_do_not_alert(tmp_path: Path) -> None:
    _write_runs(
        tmp_path,
        {"run_id": "r0", "failed": True, "finished_at": NOW},
        {"run_id": "r1", "finished_at": NOW},
        {"run_id": "r2", "failed": True, "finished_at": NOW},
        {"run_id": "r3", "failed": True, "finished_at": NOW},
    )
    assert _run(tmp_path) == []


def test_no_finished_cycle_for_the_quiet_window_alerts(tmp_path: Path) -> None:
    _write_runs(
        tmp_path,
        {"run_id": "r1", "finished_at": "2026-09-25T13:00:00+00:00"},
        {"run_id": "r2", "finished_at": "2026-09-25T13:39:30+00:00"},
    )
    assert _run(tmp_path) == ["[cycles-quiet] no cycle has finished for 150 minutes while unheld"]


def test_the_quiet_window_boundary_is_exclusive(tmp_path: Path) -> None:
    _write_runs(tmp_path, {"run_id": "r1", "finished_at": "2026-09-25T14:10:00+00:00"})
    assert _run(tmp_path) == []


def test_a_stale_fetch_alerts_in_whole_hours(tmp_path: Path) -> None:
    _write_db(tmp_path, "2026-09-20T00:00:00+00:00", "2026-09-23T13:40:00+00:00")
    assert _run(tmp_path) == [
        "[stale-fetch] no request has been made for 50 hours while unheld;"
        " newest stored page 50 hours old"
    ]


@pytest.mark.parametrize(
    "fetched, alerts",
    [("2026-09-25T14:10:00+00:00", 0), ("2026-09-25T14:09:59+00:00", 1)],
)
def test_the_stale_fetch_boundary_is_two_hours_exclusive(
    tmp_path: Path, fetched: str, alerts: int
) -> None:
    _write_db(tmp_path, fetched)
    assert len(_run(tmp_path)) == alerts


def test_requests_that_store_no_page_count_as_fetching(tmp_path: Path) -> None:
    # 304s and unchanged 200s store no snapshot but are requests (the
    # 2026-09-24 budget day had 265 requests in a 17 h snapshot gap).
    state = State(tmp_path).fetched("2026-09-24T06:57:00+00:00")
    assert _keys(_run(tmp_path)) == ["stale-fetch"]
    state.requested("2026-09-25T15:30:00+00:00").close()
    assert _run(tmp_path) == []


def test_a_scheduler_request_counts_as_fetching(tmp_path: Path) -> None:
    State(tmp_path).fetched("2026-09-24T06:57:00+00:00").issued("2026-09-25T16:00:00").close()
    assert _run(tmp_path) == []


def _config(tmp_path: Path, *, history: int | None = 43200) -> list[str]:
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "hosts.toml").write_text(
        '[hosts."scoring.dance"]\ndaily_request_budget = 800\n'
        '[hosts."points.worldsdc.com"]\ndaily_request_budget = 1500\n'
        '[hosts."danceconvention.net"]\ndaily_request_budget = 200\n'
        'daily_byte_budget = "300MB"\n'
    )
    scheduling = f"[scheduling]\nhistory_interval_seconds = {history}\n" if history else ""
    (directory / "sources.toml").write_text(scheduling)
    return ["--config", str(directory)]


def test_all_budgets_spent_replaces_the_stale_fetch_line(tmp_path: Path) -> None:
    state = State(tmp_path).fetched("2026-09-25T07:00:00+00:00")
    state.budget("scoring.dance", 800).budget("points.worldsdc.com", 1500)
    state.budget("scoring.dance", 20, day="2026-09-24").close()
    config = _config(tmp_path)
    assert _run(tmp_path, extra=config) == [
        "[budgets-spent] every host that fetched today has spent its daily budget"
        " (points.worldsdc.com, scoring.dance); no request for 9 hours"
    ]
    # Informational: at most once a day.
    _log(tmp_path, "2026-09-25T00:00:00Z [budgets-spent] old")
    assert _run(tmp_path, extra=config) == []


def test_a_host_with_budget_left_keeps_the_stale_fetch_line(tmp_path: Path) -> None:
    state = State(tmp_path).fetched("2026-09-25T07:00:00+00:00")
    state.budget("scoring.dance", 800).budget("points.worldsdc.com", 1499).close()
    assert _run(tmp_path, extra=_config(tmp_path)) == [
        "[stale-fetch] no request has been made for 9 hours while unheld;"
        " newest stored page 9 hours old; spent today: scoring.dance"
    ]


@pytest.mark.parametrize("size, spent", [(300_000_000, True), (299_999_999, False)])
def test_a_spent_byte_budget_counts_as_spent(tmp_path: Path, size: int, spent: bool) -> None:
    # danceconvention.net stops on its 300 MB byte budget long before its
    # 200 requests.
    state = State(tmp_path).fetched("2026-09-25T07:00:00+00:00")
    state.budget("scoring.dance", 800).budget("danceconvention.net", 40, size=size).close()
    assert _keys(_run(tmp_path, extra=_config(tmp_path))) == (
        ["budgets-spent"] if spent else ["stale-fetch"]
    )


def test_without_config_budgets_never_silence_the_stale_fetch_line(tmp_path: Path) -> None:
    State(tmp_path).fetched("2026-09-25T07:00:00+00:00").budget("scoring.dance", 800).close()
    assert _keys(_run(tmp_path)) == ["stale-fetch"]


def test_a_stale_fetch_names_the_ready_parse_backlog(tmp_path: Path) -> None:
    (
        State(tmp_path)
        .fetched("2026-09-25T12:10:00+00:00")
        .generation("2026-09-25T16:00:00+00:00", stage="parse")
        .pending("s1", stage="parse", kind="snapshot")
        .pending("s2", stage="parse", kind="snapshot")
        .pending("s3", stage="parse", kind="snapshot")
        .attempt("s3", "blocked", stage="parse", kind="snapshot")
        .close()
    )
    assert _run(tmp_path) == [
        "[stale-fetch] no request has been made for 4 hours while unheld;"
        " newest stored page 4 hours old;"
        " 2 parse units wait, backpressure may be holding requests"
    ]


def test_an_empty_database_is_quiet(tmp_path: Path) -> None:
    _write_db(tmp_path)
    assert _run(tmp_path) == []


def test_stale_progress_alerts_per_stage(tmp_path: Path) -> None:
    state = _stalled(tmp_path)
    state.fetched("2026-09-25T16:00:00+00:00").pending("e2")
    state.pending("s1", stage="parse", kind="snapshot")
    state.attempt("s0", "succeeded", stage="parse", kind="snapshot", finished="2026-09-25T11:40:00")
    state.close()
    assert _run(tmp_path) == [
        "[stale-progress] no progress while unheld: link 3 h 0 m with 2 ready;"
        " parse 4 h 30 m with 1 ready"
    ]


def test_a_stage_that_progresses_does_not_hide_a_stalled_one(tmp_path: Path) -> None:
    # The 16:10 copy at 13:30: parse and project kept succeeding while link
    # had not progressed since 01:32 with 2,932 units queued.
    state = State(tmp_path).fetched("2026-09-25T13:25:00+00:00")
    state.attempt("done", "succeeded", finished="2026-09-25T01:32:17+00:00")
    state.attempt("s", "succeeded", stage="parse", kind="snapshot", finished="2026-09-25T12:46:27")
    state.pending("e1").pending("e2").pending("s1", stage="parse", kind="snapshot").close()
    assert _run(tmp_path, now="2026-09-25T13:30:00+00:00") == [
        "[stale-progress] no progress while unheld: link 11 h 57 m with 2 ready"
    ]


@pytest.mark.parametrize(
    "generation, alerts",
    [("2026-09-25T14:10:00+00:00", 0), ("2026-09-25T14:09:59+00:00", 1)],
)
def test_the_stale_progress_boundary_is_two_hours_exclusive(
    tmp_path: Path, generation: str, alerts: int
) -> None:
    State(tmp_path).fetched(NOW).generation(generation).pending("e1").close()
    assert len(_run(tmp_path)) == alerts


def test_a_recent_succeeded_attempt_counts_as_progress(tmp_path: Path) -> None:
    state = _stalled(tmp_path).fetched(NOW)
    assert _keys(_run(tmp_path)) == ["stale-progress"]
    state.attempt("e9", "succeeded", finished="2026-09-25T15:00:00+00:00").close()
    assert _run(tmp_path) == []


def test_progress_in_another_stage_does_not_count(tmp_path: Path) -> None:
    state = _stalled(tmp_path).fetched(NOW)
    state.attempt("s9", "succeeded", stage="parse", finished="2026-09-25T16:00:00+00:00")
    state.generation("2026-09-25T16:00:00+00:00", stage="project").close()
    assert _keys(_run(tmp_path)) == ["stale-progress"]


def test_the_newest_generation_is_read_by_rowid(tmp_path: Path) -> None:
    # A later row with an earlier stamp still is the newest write.
    state = State(tmp_path).fetched(NOW).pending("e1")
    state.generation("2026-09-25T15:00:00+00:00").generation("2026-09-25T13:00:00+00:00").close()
    assert _keys(_run(tmp_path)) == ["stale-progress"]


def test_old_progress_without_ready_work_is_quiet(tmp_path: Path) -> None:
    State(tmp_path).fetched(NOW).generation("2026-09-20T00:00:00+00:00").close()
    assert _run(tmp_path) == []


def test_a_stage_that_never_progressed_is_clocked_from_its_oldest_ready_unit(
    tmp_path: Path,
) -> None:
    # A new stage, or a fresh or restored state: no attempt, no generation.
    state = State(tmp_path).fetched(NOW)
    state.pending("s1", stage="parse", kind="snapshot", enqueued="2026-09-25T14:10:00+00:00")
    assert _run(tmp_path) == []
    state.pending("s2", stage="parse", kind="snapshot", enqueued="2026-09-25T14:09:59+00:00")
    state.close()
    assert _run(tmp_path) == [
        "[stale-progress] no progress while unheld: parse 2 h 0 m with 2 ready"
    ]


def test_an_idle_stage_is_clocked_from_when_new_work_arrived(tmp_path: Path) -> None:
    # Parse last ran yesterday; a page stored ten minutes ago waits for the
    # offline phase. That is not a stall.
    state = State(tmp_path).fetched(NOW)
    state.attempt("s0", "succeeded", stage="parse", kind="snapshot", finished="2026-09-24T10:00")
    state.pending("s1", stage="parse", kind="snapshot", enqueued="2026-09-25T16:00:00+00:00")
    state.close()
    assert _run(tmp_path) == []
    assert _keys(_run(tmp_path, now="2026-09-25T18:00:01+00:00")) == ["stale-progress"]


PAST = "2026-09-25T15:00:00+00:00"
FUTURE = "2026-09-25T17:00:00+00:00"


@pytest.mark.parametrize(
    "outcome, retry_at, retry_generation, ready",
    [
        (None, None, 0, True),
        ("succeeded", None, 0, True),
        ("blocked", None, 0, False),
        ("blocked", None, 1, True),
        ("superseded", None, 0, False),
        ("unavailable", None, 0, False),
        ("running", None, 0, False),
        ("transient", FUTURE, 0, False),
        ("transient", PAST, 0, True),
        ("transient", None, 0, False),
        ("interrupted", PAST, 0, True),
        ("interrupted", FUTURE, 0, False),
    ],
)
def test_the_ready_rule_follows_the_latest_attempt(
    tmp_path: Path,
    outcome: str | None,
    retry_at: str | None,
    retry_generation: int,
    ready: bool,
) -> None:
    state = State(tmp_path).fetched(NOW).generation("2026-09-25T10:00:00+00:00").pending("e1")
    if outcome is not None:
        # An older attempt of the opposite kind must not decide the unit.
        state.attempt("e1", "blocked" if ready else "succeeded", finished="2026-09-25T09:00:00")
        state.attempt("e1", outcome, retry_at=retry_at, finished="2026-09-25T09:00:00")
    if retry_generation:
        state.sql("UPDATE work_generations SET retry_generation=1")
    state.close()
    assert _keys(_run(tmp_path)) == (["stale-progress"] if ready else [])


def test_project_history_within_its_interval_is_quiet(tmp_path: Path) -> None:
    # Output six hours old: history is not due until 22:10.
    State(tmp_path).fetched(NOW).history("2026-09-25T10:10:00+00:00").close()
    assert _run(tmp_path, extra=_config(tmp_path)) == []


def test_history_that_came_due_and_never_ran_alarms_without_links(tmp_path: Path) -> None:
    # Publication input stops when history never runs, whether or not link
    # units wait for it (the 2026-09-25 history, due 13:11, never ran).
    State(tmp_path).fetched(NOW).history("2026-09-24T10:10:00+00:00").close()
    assert _run(tmp_path, extra=_config(tmp_path)) == [
        "[stale-progress] no progress while unheld:"
        " project/history 18 h 0 m with 1 ready (due at 2026-09-24 22:10 UTC)"
    ]


@pytest.mark.parametrize(
    "finished, alerts",
    [
        # A success after it came due, even one that wrote no new output.
        ("2026-09-25T15:00:00+00:00", []),
        # A success before it came due does not count.
        ("2026-09-24T20:00:00+00:00", ["stale-progress"]),
    ],
)
def test_a_history_success_after_it_came_due_counts_as_progress(
    tmp_path: Path, finished: str, alerts: list[str]
) -> None:
    state = State(tmp_path).fetched(NOW).history("2026-09-24T10:10:00+00:00")
    state.attempt("all", "succeeded", stage="project", kind="history", finished=finished)
    state.close()
    assert _keys(_run(tmp_path, extra=_config(tmp_path))) == alerts


def test_progress_elsewhere_in_project_does_not_count_for_history(tmp_path: Path) -> None:
    state = State(tmp_path).fetched(NOW).history("2026-09-24T10:10:00+00:00")
    state.attempt("e1", "succeeded", stage="project", finished="2026-09-25T16:00:00+00:00")
    state.generation("2026-09-25T16:00:00+00:00", stage="project", kind="map").close()
    assert _keys(_run(tmp_path, extra=_config(tmp_path))) == ["stale-progress"]


@pytest.mark.parametrize(
    "enqueued, alerts",
    [("2026-09-25T14:10:00+00:00", []), ("2026-09-25T14:09:59+00:00", ["stale-progress"])],
)
def test_a_history_that_never_materialized_is_clocked_from_its_queue_time(
    tmp_path: Path, enqueued: str, alerts: list[str]
) -> None:
    state = State(tmp_path).fetched(NOW)
    state.sql("INSERT INTO derivation_scopes VALUES ('project','history','all',NULL)")
    state.pending("all", stage="project", kind="history", enqueued=enqueued).close()
    assert _keys(_run(tmp_path, extra=_config(tmp_path))) == alerts


def test_a_blocked_history_is_not_ready(tmp_path: Path) -> None:
    # Like any unit whose latest attempt is blocked: silent until phase 2.
    state = State(tmp_path).fetched(NOW).history("2026-09-24T10:10:00+00:00")
    state.attempt("all", "blocked", stage="project", kind="history").close()
    assert _run(tmp_path, extra=_config(tmp_path)) == []


def test_a_re_queue_restarts_a_stage_clock(tmp_path: Path) -> None:
    # Known silence (D-0202): enqueue moves enqueued_at on conflict, so a
    # stage whose ready units upstream churn keeps re-queuing never alarms,
    # even though it has not progressed in days.
    state = State(tmp_path).fetched(NOW)
    state.attempt("e0", "succeeded", stage="project", finished="2026-09-20T00:00:00+00:00")
    state.pending("e1", stage="project", enqueued="2026-09-25T16:00:00+00:00").close()
    assert _run(tmp_path) == []


#: The 2026-09-25 history output (production, 16:10 UTC copy) and when it
#: came due under the 12-hour interval.
MADE = "2026-09-25T01:11:01+00:00"
DUE = "2026-09-25T13:11:01+00:00"


def _links(tmp_path: Path, *, link: str | None, count: int = 1) -> State:
    """`count` ready link units, queued when history last materialized."""
    state = State(tmp_path).fetched(NOW)
    if link is not None:
        state.attempt("done", "succeeded", finished=link)
    for index in range(count):
        state.pending(f"e{index}", enqueued=MADE)
    return state


@pytest.mark.parametrize(
    "now, alerts",
    [
        # Links progressed after history's output: they wait by design until
        # history is due again, then for staleProgressSeconds after that.
        ("2026-09-25T13:00:00+00:00", []),
        ("2026-09-25T13:12:01+00:00", []),
        ("2026-09-25T13:30:00+00:00", []),
        ("2026-09-25T15:11:01+00:00", []),
        ("2026-09-25T15:11:02+00:00", ["stale-progress"]),
    ],
)
def test_links_waiting_for_paced_history_alarm_only_past_its_due_time(
    tmp_path: Path, now: str, alerts: list[str]
) -> None:
    _links(tmp_path, link="2026-09-25T01:32:17+00:00").history(MADE).close()
    assert _keys(_run(tmp_path, extra=_config(tmp_path), now=now)) == alerts


def test_links_that_miss_every_history_window_alarm(tmp_path: Path) -> None:
    # History re-materializes every 12 hours, so its newest output is always
    # young; links that never progress must still alarm (09-23 to 09-25).
    state = _links(tmp_path, link="2026-09-24T20:10:00+00:00", count=100)
    state.history("2026-09-25T15:10:00+00:00").close()
    assert _run(tmp_path, extra=_config(tmp_path)) == [
        "[stale-progress] no progress while unheld: link 8 h 0 m with 100 ready"
        " (runnable since 2026-09-25 08:10 UTC,"
        " project/history output at 2026-09-25 15:10 UTC)"
    ]


@pytest.mark.parametrize(
    "earlier, runnable",
    [
        # History came due at 01:00 and ran late at 02:00: the chain has not
        # progressed since 01:00.
        (("2026-09-24T13:00:00+00:00", "2026-09-25T02:00:00+00:00"), "2026-09-25 01:00 UTC"),
        # History ran early enough; the links had its 00:30 output to use.
        (("2026-09-24T13:00:00+00:00", "2026-09-25T00:30:00+00:00"), "2026-09-25 00:30 UTC"),
    ],
)
def test_links_that_missed_the_previous_history_output_alarm(
    tmp_path: Path, earlier: tuple[str, ...], runnable: str
) -> None:
    state = _links(tmp_path, link="2026-09-24T14:00:00+00:00")
    state.history("2026-09-25T15:30:00+00:00", earlier=earlier).close()
    lines = _run(tmp_path, extra=_config(tmp_path))
    assert _keys(lines) == ["stale-progress"]
    assert f"(runnable since {runnable}," in lines[0]


def test_links_get_their_window_after_a_new_history_output(tmp_path: Path) -> None:
    # Steady state: links ran after the previous output, history came due at
    # 15:30 and ran at 15:45. The links run next; nothing is late.
    state = _links(tmp_path, link="2026-09-25T03:40:00+00:00")
    state.history("2026-09-25T15:45:00+00:00", earlier=("2026-09-25T03:30:00+00:00",)).close()
    config = _config(tmp_path)
    assert _run(tmp_path, extra=config) == []
    assert _run(tmp_path, extra=config, now="2026-09-25T17:30:00+00:00") == []
    assert _keys(_run(tmp_path, extra=config, now="2026-09-25T17:30:01+00:00")) == [
        "stale-progress"
    ]


@pytest.mark.parametrize(
    "materialized, interval, alerts",
    [
        # A history unit that never materialized holds nothing.
        (None, 43200, ["stale-progress"]),
        # With no interval, history is not paced: links count once it is pending.
        ("2026-09-25T09:00:00+00:00", None, ["stale-progress"]),
    ],
)
def test_links_count_when_history_is_not_paced(
    tmp_path: Path, materialized: str | None, interval: int | None, alerts: list[str]
) -> None:
    state = _links(tmp_path, link="2026-09-25T10:00:00+00:00").history(materialized)
    state.close()
    assert _keys(_run(tmp_path, extra=_config(tmp_path, history=interval))) == alerts


def test_waiting_links_are_named_when_another_stage_stalls(tmp_path: Path) -> None:
    state = State(tmp_path).fetched(NOW).pending("e1").pending("e2")
    state.attempt("l0", "succeeded", finished="2026-09-25T15:30:00+00:00")
    state.attempt("s0", "succeeded", stage="parse", kind="snapshot", finished="2026-09-25T10:00")
    state.pending("s1", stage="parse", kind="snapshot")
    state.history("2026-09-25T15:00:00+00:00").close()
    assert _run(tmp_path, extra=_config(tmp_path)) == [
        "[stale-progress] no progress while unheld: parse 6 h 10 m with 1 ready;"
        " 2 link units wait for the paced history"
    ]


def _stall_everything(state: Path) -> State:
    _write_runs(state, {"run_id": "r1", "finished_at": "2026-09-24T00:00:00+00:00"})
    return _stalled(state)


def test_a_hold_silences_the_stall_alerts(tmp_path: Path) -> None:
    _stall_everything(tmp_path).close()
    assert _keys(_run(tmp_path)) == ["cycles-quiet", "stale-fetch", "stale-progress"]
    (tmp_path / "operator-hold").touch()
    assert _run(tmp_path) == []
    assert _run(tmp_path, timer=0) == []


def test_an_inactive_timer_while_unheld_alerts(tmp_path: Path) -> None:
    _stall_everything(tmp_path).close()
    assert _run(tmp_path, timer=0) == [
        "[timer-inactive] swingset-cycle.timer is not active and nothing holds the worker;"
        " start the timer or write operator-hold"
    ]


def test_an_all_scope_operator_pause_counts_as_held(tmp_path: Path) -> None:
    _stall_everything(tmp_path).pause("review").close()
    assert _run(tmp_path) == []


@pytest.mark.parametrize(
    "until, kind, scope",
    [("2026-09-25T16:00:00+00:00", "all", "all"), (None, "host", "scoring.dance")],
)
def test_an_expired_or_narrower_pause_does_not_hold(
    tmp_path: Path, until: str | None, kind: str, scope: str
) -> None:
    _stall_everything(tmp_path).pause("review", until=until, kind=kind, scope=scope).close()
    assert _keys(_run(tmp_path)) == ["cycles-quiet", "stale-fetch", "stale-progress"]


def test_a_future_pause_expiry_still_holds(tmp_path: Path) -> None:
    _stall_everything(tmp_path).pause("review", until=FUTURE).close()
    assert _run(tmp_path) == []


def test_a_hold_file_older_than_48_hours_is_overdue(tmp_path: Path) -> None:
    hold = tmp_path / "operator-hold"
    hold.touch()
    stamp = datetime.fromisoformat(NOW).timestamp() - 48 * 3600
    os.utime(hold, (stamp, stamp))
    assert _run(tmp_path, timer=0) == []
    os.utime(hold, (stamp - 1, stamp - 1))
    assert _run(tmp_path, timer=0) == [
        "[hold-overdue] operator-hold file is 48 h old, over the 48 h limit;"
        " review it or record why it stays (docs/guides/operation.md, holds)"
    ]


def test_an_all_scope_pause_older_than_48_hours_is_overdue(tmp_path: Path) -> None:
    state = _stall_everything(tmp_path).pause("review", created="2026-09-22T16:00:00+00:00")
    state.close()
    assert _run(tmp_path) == [
        "[hold-overdue] all-scope pause 'review' is 72 h old, over the 48 h limit;"
        " review it or record why it stays (docs/guides/operation.md, holds)"
    ]
    # Repeated daily, not every two hours.
    _log(tmp_path, "2026-09-25T00:00:00Z [hold-overdue] old")
    assert _run(tmp_path) == []


def test_an_all_scope_pause_without_a_creation_time_is_overdue(tmp_path: Path) -> None:
    # created_at came with migration 0012 and is nullable; such a pause
    # would otherwise silence the stall checks with no 48-hour clock.
    _stall_everything(tmp_path).pause("legacy", created=None).close()
    assert _run(tmp_path) == [
        "[hold-overdue] all-scope pause 'legacy' has no created_at, age unknown,"
        " over the 48 h limit; review it or record why it stays (docs/guides/operation.md, holds)"
    ]


def test_the_size_cap_pause_alerts_and_does_not_count_as_held(tmp_path: Path) -> None:
    state = _stall_everything(tmp_path)
    state.pause(SIZE_CAP_REASON, created="2026-09-01T00:00:00+00:00").close()
    lines = _run(tmp_path)
    assert _keys(lines) == ["size-cap-pause", "cycles-quiet", "stale-fetch", "stale-progress"]
    assert lines[0] == (
        f"[size-cap-pause] all work is paused: {SIZE_CAP_REASON};"
        " see docs/guides/operation.md#retention-plans-and-holds"
    )


def test_the_size_cap_pause_alerts_under_a_hold(tmp_path: Path) -> None:
    _stall_everything(tmp_path).pause(SIZE_CAP_REASON).close()
    (tmp_path / "operator-hold").touch()
    assert _keys(_run(tmp_path, timer=0)) == ["size-cap-pause"]


def test_legacy_spacing_alerts_even_when_held(tmp_path: Path) -> None:
    state = _healthy(tmp_path)
    state.sql("INSERT INTO host_request_spacing VALUES ('eepro.com', NULL)")
    state.sql("INSERT INTO host_request_spacing VALUES ('worldsdc.com', NULL)")
    # Spent budget before and no spacing row: politeness pauses it too.
    state.budget("web.archive.org", 3, day="2026-09-20", spaced=False)
    state.budget("scoring.dance", 9).close()
    (tmp_path / "operator-hold").touch()
    assert _run(tmp_path, timer=0) == [
        "[gate-legacy-spacing] request spacing unknown, so the fetch gate skips every request"
        " with no expiry, for eepro.com, web.archive.org, worldsdc.com (D-0190)"
    ]


@pytest.mark.parametrize(
    "until, reason, alerts",
    [
        ("2026-09-26T04:00:00+00:00", "Blocked", 1),
        ("2026-09-25T16:10:00+00:00", "Blocked", 0),
        ("2026-09-26T04:00:00+00:00", "RateLimited", 0),
    ],
)
def test_a_blocked_host_pause_alerts_until_it_expires(
    tmp_path: Path, until: str, reason: str, alerts: int
) -> None:
    state = _healthy(tmp_path)
    state.sql("UPDATE hosts SET paused_until=?, pause_reason=?", until, reason).close()
    lines = _run(tmp_path)
    assert len(lines) == alerts
    if alerts:
        assert lines == [
            "[gate-host-blocked] hosts paused as Blocked: scoring.dance until 2026-09-26 04:00 UTC"
        ]


def test_a_new_blocked_host_repeats_at_once(tmp_path: Path) -> None:
    state = _healthy(tmp_path)
    state.sql("UPDATE hosts SET paused_until=?, pause_reason='Blocked'", FUTURE)
    first = _run(tmp_path)
    _log(tmp_path, *(f"2026-09-25T16:00:00Z {line}" for line in first))
    assert _run(tmp_path) == []
    state.sql("INSERT INTO hosts VALUES ('eepro.com', ?, 'Blocked', 1)", FUTURE).close()
    assert _run(tmp_path) == [
        "[gate-host-blocked] hosts paused as Blocked: eepro.com until 2026-09-25 17:00 UTC,"
        " scoring.dance until 2026-09-25 17:00 UTC"
    ]


@pytest.mark.parametrize(
    "reason, streak, alerts",
    [
        ("ServerError", 4, 1),
        ("Throttled", 5, 1),
        ("ServerError", 3, 0),
        ("Blocked", 4, 0),
    ],
)
def test_a_host_answering_only_errors_alerts_even_when_held(
    tmp_path: Path, reason: str, streak: int, alerts: int
) -> None:
    # One dead host among healthy ones keeps the fetch clock fresh.
    state = _healthy(tmp_path)
    state.sql(
        "INSERT INTO hosts VALUES ('eepro.com', '2026-09-25T15:00:00+00:00', ?, ?)",
        reason,
        streak,
    ).close()
    (tmp_path / "operator-hold").touch()
    lines = _run(tmp_path, timer=0)
    if reason == "Blocked":
        # A Blocked pause has its own line; it expired at 15:00.
        assert lines == []
        return
    assert lines == (
        [
            f"[host-failing] hosts answering only 429 or 5xx: eepro.com {reason} x{streak}"
            " until 2026-09-25 15:00 UTC"
        ]
        if alerts
        else []
    )


def test_a_longer_failing_streak_repeats_at_once(tmp_path: Path) -> None:
    state = _healthy(tmp_path)
    state.sql("INSERT INTO hosts VALUES ('eepro.com', ?, 'ServerError', 4)", FUTURE)
    first = _run(tmp_path)
    _log(tmp_path, *(f"2026-09-25T16:00:00Z {line}" for line in first))
    assert _run(tmp_path) == []
    state.sql("UPDATE hosts SET pause_streak = 5 WHERE host = 'eepro.com'").close()
    assert _keys(_run(tmp_path)) == ["host-failing"]


def test_an_alert_logged_inside_the_repeat_window_is_not_repeated(tmp_path: Path) -> None:
    _stalled(tmp_path).close()
    _log(
        tmp_path,
        "2026-09-25T14:40:00Z [stale-fetch] no page has been fetched for 2 hours while unheld",
        "an unstamped line from before keys: [stale-progress]",
    )
    assert _keys(_run(tmp_path)) == ["stale-progress"]


@pytest.mark.parametrize("stamp", ["2026-09-25T14:10:00Z", "2026-09-25T13:00:00Z"])
def test_an_alert_logged_at_or_before_the_window_edge_repeats(tmp_path: Path, stamp: str) -> None:
    _stalled(tmp_path).close()
    _log(tmp_path, f"{stamp} [stale-fetch] old", f"{stamp} [stale-progress] old")
    assert _keys(_run(tmp_path)) == ["stale-fetch", "stale-progress"]


def test_de_duplication_reads_only_the_end_of_the_log(tmp_path: Path) -> None:
    _stalled(tmp_path).close()
    _log(tmp_path, "2026-09-25T16:00:00Z [stale-fetch] recent", "x" * watchdog.LOG_TAIL_BYTES)
    assert _keys(_run(tmp_path)) == ["stale-fetch", "stale-progress"]


def test_the_2026_09_25_stall_fires_both_progress_alarms(tmp_path: Path) -> None:
    # Production at 16:10 UTC: newest request 12:46:06, link last succeeded
    # 01:32:17, parse 12:46:27, project 13:22:33; history materialized 01:11,
    # came due 13:11 and did not run; 2,932 link units ready, cycles finishing.
    _write_runs(tmp_path, {"run_id": "r1", "finished_at": "2026-09-25T16:05:00+00:00"})
    state = State(tmp_path).fetched("2026-09-25T12:45:54.478880+00:00")
    state.issued("2026-09-25T12:46:06.042704+00:00")
    state.attempt("l", "succeeded", finished="2026-09-25T01:32:17.841080+00:00")
    state.attempt("s", "succeeded", stage="parse", finished="2026-09-25T12:46:27.452179+00:00")
    state.attempt("p", "succeeded", stage="project", finished="2026-09-25T13:22:33.001307+00:00")
    for index in range(2932):
        state.db.execute(
            "INSERT INTO pending_work VALUES ('link','event',?,?)",
            (f"e{index}", "2026-09-25T01:11:01.703824+00:00"),
        )
    state.pending("s1", stage="parse", kind="snapshot")
    state.attempt("s1", "blocked", stage="parse", kind="snapshot")
    state.history("2026-09-25T01:11:01.703824+00:00").close()
    config = _config(tmp_path)
    assert _run(tmp_path, extra=config) == [
        "[stale-fetch] no request has been made for 3 hours while unheld;"
        " newest stored page 3 hours old",
        "[stale-progress] no progress while unheld: link 2 h 58 m with 2932 ready"
        " (project/history due at 2026-09-25 13:11 UTC has not run);"
        " project/history 2 h 58 m with 1 ready (due at 2026-09-25 13:11 UTC)",
    ]
    # Before history was due, and within two hours after, the links waited
    # for it by design.
    for now in (
        "2026-09-25T13:00:00+00:00",
        "2026-09-25T13:15:00+00:00",
        "2026-09-25T13:30:00+00:00",
    ):
        assert _run(tmp_path, extra=config, now=now) == []


def test_an_unreadable_database_makes_the_watchdog_say_it_is_blind(tmp_path: Path) -> None:
    (tmp_path / "state.sqlite").write_bytes(b"not a database" * 100)
    lines = _run(tmp_path)
    assert _keys(lines) == ["watchdog-blind"]
    assert lines[0].startswith("[watchdog-blind] checks failed: pauses, requests, snapshots,")


def test_a_missing_table_names_its_check(tmp_path: Path) -> None:
    _stall_everything(tmp_path).sql("DROP TABLE hosts").close()
    lines = _run(tmp_path)
    assert _keys(lines) == ["watchdog-blind", "cycles-quiet", "stale-fetch", "stale-progress"]
    assert lines[0] == (
        "[watchdog-blind] checks failed: blocked, failing: OperationalError: no such table: hosts"
    )


def test_a_bad_stamp_loses_only_its_own_check(tmp_path: Path) -> None:
    # One malformed until_at used to drop every later database check.
    state = _stall_everything(tmp_path).pause("review", until="")
    state.sql("INSERT INTO host_request_spacing VALUES ('eepro.com', NULL)").close()
    lines = _run(tmp_path)
    assert _keys(lines) == [
        "watchdog-blind",
        "cycles-quiet",
        "stale-fetch",
        "stale-progress",
        "gate-legacy-spacing",
    ]
    assert lines[0].startswith("[watchdog-blind] checks failed: pauses: ValueError")


def test_a_failed_database_read_is_tried_once_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An immutable read of a live database can meet a page mid-checkpoint.
    _stalled(tmp_path).close()
    real = watchdog._read_facts
    calls: list[int] = []

    def flaky(db: sqlite3.Connection, *args: object) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.DatabaseError("database disk image is malformed")
        real(db, *args)

    def checked(db: sqlite3.Connection, now: float, checks: object, *rest: object) -> None:
        checks.run("pauses", lambda: flaky(db, now, checks, *rest), None)  # type: ignore[attr-defined]

    monkeypatch.setattr(watchdog, "_read_facts", checked)
    assert _keys(_run(tmp_path)) == ["stale-fetch", "stale-progress"]
    assert len(calls) == 2


def test_a_database_read_that_fails_twice_is_blind(tmp_path: Path) -> None:
    _stall_everything(tmp_path).sql("DROP TABLE hosts").close()
    lines = _run(tmp_path)
    # Named once, not once per try.
    assert lines[0] == (
        "[watchdog-blind] checks failed: blocked, failing: OperationalError: no such table: hosts"
    )


def test_a_run_record_that_is_not_an_object_is_skipped(tmp_path: Path) -> None:
    _write_runs(tmp_path, {"run_id": "r1", "finished_at": NOW})
    (tmp_path / "runs" / "run_9999.json").write_text("[1, 2]")
    assert _run(tmp_path) == []


def test_a_bad_run_stamp_is_named_not_fatal(tmp_path: Path) -> None:
    _write_runs(tmp_path, {"run_id": "r1", "finished_at": "yesterday"})
    lines = _run(tmp_path, disk="210000000000 256000000000")
    assert _keys(lines) == ["watchdog-blind", "disk-low"]
    assert lines[0].startswith("[watchdog-blind] checks failed: cycles-quiet: ValueError")


def test_a_crash_outside_every_check_still_prints_a_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(watchdog, "_last_logged", broken)
    assert watchdog.run(["--state", str(tmp_path), *ARGS]) == [
        "[watchdog-error] RuntimeError: boom"
    ]


ARGS = [
    "--quiet",
    "7200",
    "--stale-fetch",
    "7200",
    "--stale-progress",
    "7200",
    "--repeat",
    "7200",
    "--disk-min-free",
    "1",
    "--timer-active",
    "1",
]


def test_the_database_read_creates_no_side_files(tmp_path: Path) -> None:
    _stall_everything(tmp_path).close()
    before = sorted(p.name for p in tmp_path.iterdir())
    assert _keys(_run(tmp_path)) == ["cycles-quiet", "stale-fetch", "stale-progress"]
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_low_disk_quota_alerts(tmp_path: Path) -> None:
    disk = "210000000000 256000000000 2026-09-25T16:05:00Z"
    assert _run(tmp_path, disk=disk) == [
        "[disk-low] disk quota has 46.0 GB free of 256.0 GB; df does not show this"
    ]


def test_low_disk_alerts_even_when_held(tmp_path: Path) -> None:
    (tmp_path / "operator-hold").touch()
    assert _run(tmp_path, disk="210000000000 256000000000", timer=0) == [
        "[disk-low] disk quota has 46.0 GB free of 256.0 GB; df does not show this"
    ]


@pytest.mark.parametrize(
    "disk, alerts",
    [
        # A backup's second checkpoint: expected, since its room check kept
        # 20 GB of headroom.
        ("210000000000 256000000000", []),
        ("236000000001 256000000000", ["disk-low"]),
    ],
)
def test_low_disk_during_a_backup_alerts_only_under_its_headroom(
    tmp_path: Path, disk: str, alerts: list[str]
) -> None:
    lines = _run(tmp_path, disk=disk, extra=["--backup-active", "1"])
    assert _keys(lines) == alerts
    if alerts:
        assert lines[0].endswith("; df does not show this; a backup is running")


@pytest.mark.parametrize("disk", ["", "123", "100000000000 200000000000"])
def test_no_quota_or_enough_room_is_quiet(tmp_path: Path, disk: str) -> None:
    assert _run(tmp_path, disk=disk) == []


def test_alerts_come_out_in_check_order(tmp_path: Path) -> None:
    _write_runs(
        tmp_path,
        *[
            {"run_id": f"r{i}", "failed": True, "finished_at": "2026-09-25T10:00:00+00:00"}
            for i in range(1, 4)
        ],
    )
    state = _stalled(tmp_path).sql("DROP TABLE derivation_scopes")
    state.pause(SIZE_CAP_REASON)
    state.sql("INSERT INTO host_request_spacing VALUES ('eepro.com', NULL)")
    state.sql("INSERT INTO hosts VALUES ('eepro.com', ?, 'Blocked', 1)", FUTURE).close()
    _checkpoint(tmp_path, "failed-run_20260925T040000Z", NOW)
    assert _keys(_run(tmp_path, disk="210000000000 256000000000", backup=LOCAL)) == [
        "watchdog-blind",
        "cycles-failing",
        "size-cap-pause",
        "cycles-quiet",
        "stale-fetch",
        "stale-progress",
        "gate-legacy-spacing",
        "gate-host-blocked",
        "disk-low",
        "backup-failed",
        "backup-stale",
        "backup-upload-paused",
    ]


def test_the_script_prints_one_alert_per_line(tmp_path: Path) -> None:
    _write_db(tmp_path, "2026-09-20T16:10:00+00:00")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--state",
            str(tmp_path),
            "--quiet",
            "7200",
            "--stale-fetch",
            "7200",
            "--stale-progress",
            "7200",
            "--repeat",
            "7200",
            "--disk",
            "210000000000 256000000000 2026-09-25T16:05:00Z",
            "--disk-min-free",
            "100000000000",
            "--timer-active",
            "1",
            "--now",
            NOW,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "[stale-fetch] no request has been made for 120 hours while unheld;"
        " newest stored page 120 hours old",
        "[disk-low] disk quota has 46.0 GB free of 256.0 GB; df does not show this",
    ]


LOCAL = ["--backup-upload", "0", "--backup-max-age", "129600"]


def _checkpoint(state: Path, name: str, written: str, *, complete: bool = True) -> None:
    path = state / "checkpoints" / name
    path.mkdir(parents=True)
    if complete:
        (path / "checkpoint.json").write_text("{}")
        stamp = datetime.fromisoformat(written).timestamp()
        os.utime(path / "checkpoint.json", (stamp, stamp))


def test_backup_checks_are_silent_while_upload_is_on(tmp_path: Path) -> None:
    assert _run(tmp_path) == []
    assert _run(tmp_path, backup=["--backup-upload", "1", "--backup-max-age", "1"]) == []


def test_local_mode_without_a_checkpoint_alerts_stale_and_paused(tmp_path: Path) -> None:
    _checkpoint(tmp_path, "run_20260925T040000Z", NOW, complete=False)
    _checkpoint(tmp_path, "hold-before-switch", NOW)
    assert _run(tmp_path, backup=LOCAL) == [
        "[backup-stale] no complete run_* checkpoint in checkpoints/",
        "[backup-upload-paused] remote backup upload is paused"
        " (services.swingset.backupUpload = false); there is no complete local checkpoint;"
        " keep the Mac byte copy current weekly",
    ]


@pytest.mark.parametrize(
    "written, stale",
    [("2026-09-24T04:10:00+00:00", False), ("2026-09-24T04:09:59+00:00", True)],
)
def test_the_newest_local_checkpoint_is_stale_after_36_hours(
    tmp_path: Path, written: str, stale: bool
) -> None:
    _checkpoint(tmp_path, "run_20260920T040000Z", "2026-09-20T04:30:00+00:00")
    _checkpoint(tmp_path, "run_20260924T040000Z", written)
    lines = _run(tmp_path, backup=LOCAL)
    paused = (
        "[backup-upload-paused] remote backup upload is paused"
        " (services.swingset.backupUpload = false); newest local checkpoint"
        " run_20260924T040000Z is 36 h old; keep the Mac byte copy current weekly"
    )
    if stale:
        assert lines == [
            "[backup-stale] newest local checkpoint run_20260924T040000Z is 36 h old,"
            " over the 36 h limit",
            paused,
        ]
    else:
        assert lines == [paused]


def test_backup_alerts_ignore_a_hold(tmp_path: Path) -> None:
    (tmp_path / "operator-hold").touch()
    assert _keys(_run(tmp_path, timer=0, backup=LOCAL)) == ["backup-stale", "backup-upload-paused"]


@pytest.mark.parametrize(
    "stamp, repeated",
    [("2026-09-24T16:10:01Z", False), ("2026-09-24T16:10:00Z", True)],
)
def test_the_upload_paused_line_repeats_at_most_daily(
    tmp_path: Path, stamp: str, repeated: bool
) -> None:
    _checkpoint(tmp_path, "run_20260925T040000Z", "2026-09-25T04:40:00+00:00")
    _log(tmp_path, f"{stamp} [backup-upload-paused] old")
    assert _keys(_run(tmp_path, backup=LOCAL)) == (["backup-upload-paused"] if repeated else [])
    # A repeat window longer than a day wins.
    assert _run(tmp_path, backup=LOCAL, repeat=2 * 86400) == []


def test_a_stale_backup_repeats_on_the_ordinary_window(tmp_path: Path) -> None:
    _log(
        tmp_path,
        "2026-09-25T13:00:00Z [backup-stale] old",
        "2026-09-25T13:00:00Z [backup-upload-paused] old",
    )
    assert _keys(_run(tmp_path, backup=LOCAL)) == ["backup-stale"]


def test_a_failed_backup_unit_alerts_under_a_hold(tmp_path: Path) -> None:
    (tmp_path / "operator-hold").touch()
    assert _run(tmp_path, timer=0, extra=["--backup-failed", "1"]) == [
        "[backup-failed] swingset-backup.service failed; read journalctl -u swingset-backup"
    ]


def test_a_copy_that_failed_its_read_back_alerts_and_is_not_the_newest(tmp_path: Path) -> None:
    # The bad copy is newer than the last good one but must not mask it.
    _checkpoint(tmp_path, "run_20260923T040000Z", "2026-09-23T04:30:00+00:00")
    _checkpoint(tmp_path, "failed-run_20260925T040000Z", "2026-09-25T04:30:00+00:00")
    assert _run(tmp_path, backup=LOCAL, extra=["--backup-failed", "1"]) == [
        "[backup-failed] swingset-backup.service failed; read journalctl -u swingset-backup;"
        " checkpoint failed-run_20260925T040000Z failed its read-back; inspect it, then run"
        " swingset backup --remove-checkpoint failed-run_20260925T040000Z",
        "[backup-stale] newest local checkpoint run_20260923T040000Z is 59 h old,"
        " over the 36 h limit",
        "[backup-upload-paused] remote backup upload is paused"
        " (services.swingset.backupUpload = false); newest local checkpoint"
        " run_20260923T040000Z is 59 h old; keep the Mac byte copy current weekly",
    ]
