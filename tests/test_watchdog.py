import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from swingset.backup.pruning import TIMER_NAME
from swingset.cli import HOLD_OVERDUE_SECONDS
from swingset.config import load_config
from swingset.state.db import open_database
from swingset.state.retention import SIZE_CAP_REASON
from swingset.state.work import WorkUnit

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
    outcome TEXT NOT NULL, retry_at TEXT, reason_code TEXT);
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
CREATE TABLE work_queue (stage TEXT, unit_kind TEXT, unit_id TEXT, state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, error_class TEXT, last_error TEXT, lease_owner TEXT,
    lease_until TEXT, next_attempt_at TEXT, quarantined_at TEXT,
    PRIMARY KEY(stage, unit_kind, unit_id));
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

    def history_output(self, at: str) -> "State":
        """project/history's current output, made then, with nothing queued."""
        generation = self._id()
        self.sql(
            "INSERT INTO derivation_generations VALUES (?,'project','history','all',?)",
            generation,
            at,
        )
        return self.sql(
            "INSERT INTO derivation_scopes VALUES ('project','history','all',?)", generation
        )

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
        reason: str | None = None,
    ) -> "State":
        return self.sql(
            "INSERT INTO work_attempts(stage,unit_kind,unit_id,retry_generation,finished_at,"
            "outcome,retry_at,reason_code) VALUES (?,?,?,?,?,?,?,?)",
            stage,
            kind,
            unit,
            retry_generation,
            finished,
            outcome,
            retry_at,
            reason,
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


def test_the_replay_and_pacing_sets_follow_the_package() -> None:
    """REPLAY_CAUSES and PACED_BY_INVENTORY are hand copies; a drift would
    silence `[replay-loop]` or the inventory wait, or raise false alarms."""
    from swingset.state.derivation_dependencies import _REPLAYED

    read = {
        f"{stage}/{kind}": {f"project/{cause}" for cause in causes}
        for (stage, kind), causes in _REPLAYED.items()
    }
    assert set(watchdog.REPLAY_CAUSES) == set(read)
    # An event and its source events are one consistency group: each one's
    # generation is a real input change for the other (prerequisites).
    group = {"project/event": {"project/source_event"}, "project/source_event": {"project/event"}}
    for key, causes in watchdog.REPLAY_CAUSES.items():
        assert read[key] <= causes, key
        assert causes - read[key] <= group.get(key, set()), key
    # The paced inventory and every project kind that reads it, directly or
    # not, except history, which has its own pacing.
    paced = {"inventory"}
    while True:
        more = {
            kind
            for (stage, kind), causes in _REPLAYED.items()
            if stage == "project" and kind != "history" and causes & paced
        }
        if more <= paced:
            break
        paced |= more
    assert watchdog.PACED_BY_INVENTORY == paced


def test_every_query_runs_against_the_real_schema(tmp_path: Path) -> None:
    """A migration that renames what the watchdog reads fails here, not silently."""
    with open_database(tmp_path, lock=False):
        pass
    db = sqlite3.connect(f"file:{tmp_path / 'state.sqlite'}?mode=ro&immutable=1", uri=True)
    try:
        for sql in watchdog.QUERIES.values():
            assert isinstance(
                db.execute(
                    sql,
                    {
                        "stage": "link",
                        "kind": "event",
                        "unit": "u",
                        "limit": 3,
                        "day": "2026-09-25",
                        "streak": 4,
                        "now": "2026-09-25T16:10:00.000000Z",
                        "since": "2026-09-25T14:10:00.000000Z",
                    },
                ).fetchall(),
                list,
            )
    finally:
        db.close()
    assert _run(tmp_path, extra=["--config", "config"]) == []


def test_the_script_reads_the_shipped_config_as_the_package_does() -> None:
    config = load_config(Path("config"))
    limits = watchdog._config("config")
    assert limits.history_interval == config.scheduler.history_interval_seconds == 43200
    assert limits.inventory_interval == config.scheduler.inventory_interval_seconds == 43200
    assert limits.requests == {
        host: config.host(host).daily_request_budget for host in config.hosts
    }
    assert limits.bytes == {
        host: budget
        for host in config.hosts
        if (budget := config.host(host).daily_byte_budget) is not None
    }
    assert limits.bytes == {"danceconvention.net": 300_000_000}
    assert limits.size_cap == config.retention.max_database_bytes == 72_000_000_000


@pytest.mark.parametrize("near", [False, True])
def test_a_database_file_near_the_retention_cap_alerts(tmp_path: Path, near: bool) -> None:
    State(tmp_path).fetched(NOW).close()
    size = (tmp_path / "state.sqlite").stat().st_size
    # 90% of the cap is the line: just under it, then just over it.
    cap = size * 10 // 9 - 1 if near else size * 10 // 9 + 10
    config = tmp_path / "config"
    config.mkdir()
    (config / "hosts.toml").write_text("")
    (config / "sources.toml").write_text(f"[retention]\nmax_database_bytes = {cap}\n")
    alerts = ["size-cap-near"] if near else []
    assert _keys(_run(tmp_path, extra=["--config", str(config)])) == alerts


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


def _config(
    tmp_path: Path, *, history: int | None = 43200, inventory: int | None = None
) -> list[str]:
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "hosts.toml").write_text(
        '[hosts."scoring.dance"]\ndaily_request_budget = 800\n'
        '[hosts."points.worldsdc.com"]\ndaily_request_budget = 1500\n'
        '[hosts."danceconvention.net"]\ndaily_request_budget = 200\n'
        'daily_byte_budget = "300MB"\n'
    )
    scheduling = f"[scheduling]\nhistory_interval_seconds = {history}\n" if history else ""
    if inventory:
        scheduling = (scheduling or "[scheduling]\n") + (
            f"inventory_interval_seconds = {inventory}\n"
        )
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


@pytest.mark.parametrize(
    "cutoff, alerts", [(None, ["stale-progress"]), ("2026-09-25T15:00:00+00:00", [])]
)
def test_a_history_cutoff_counts_as_materializing(
    tmp_path: Path, cutoff: str | None, alerts: list[str]
) -> None:
    # After schema 33 a history re-run with identical output keeps its old
    # generation and records an equivalence; its interval starts then.
    state = State(tmp_path).fetched(NOW).history("2026-09-24T10:10:00+00:00")
    state.sql(
        "CREATE TABLE derivation_equivalences (stage TEXT, unit_kind TEXT, unit_id TEXT,"
        " generation_id TEXT, created_at TEXT)"
    )
    state.sql("CREATE TABLE source_equivalences (unit_key TEXT, input_fingerprint TEXT)")
    if cutoff is not None:
        generation = state.db.execute(
            "SELECT materialized_generation_id FROM derivation_scopes"
        ).fetchone()[0]
        state.sql(
            "INSERT INTO derivation_equivalences VALUES ('project','history','all',?,?)",
            generation,
            cutoff,
        )
    state.close()
    assert _keys(_run(tmp_path, extra=_config(tmp_path))) == alerts


@pytest.mark.parametrize(
    "generations, alerts",
    [
        ({}, ["replay-loop"]),
        ({"project/event": 1}, []),
        ({"project/map": 1}, []),
        ({"link/event": 3}, ["replay-loop"]),
        # A dancer change is unrelated work: it explains neither kind.
        ({"project/dancer": 1}, ["replay-loop"]),
    ],
)
def test_replay_epochs_moving_every_cycle_without_generations_alert(
    tmp_path: Path, generations: dict[str, int], alerts: list[str]
) -> None:
    State(tmp_path).fetched(NOW).close()
    record = {
        "finished_at": NOW,
        "derivation": {
            "generations": {},
            "equivalences": {"project/event": 2933},
            "replay_bumps": {"project/event": 1, "project/source_event": 1},
        },
    }
    last = {**record, "derivation": {**record["derivation"], "generations": generations}}
    _write_runs(tmp_path, record, record, last)
    assert _keys(_run(tmp_path)) == alerts
    # The same generations in every cycle hide the loop no more than in one.
    _write_runs(tmp_path, last, last, last)
    assert _keys(_run(tmp_path)) == alerts
    # Two such cycles are not yet a loop.
    _write_runs(tmp_path, {"finished_at": NOW}, record, last)
    assert _run(tmp_path) == []


@pytest.mark.parametrize(
    "history, interval, failed, alerts",
    [
        # History materialized an hour ago: not due, so the map waits by design.
        ("2026-09-25T15:10:00+00:00", 43200, None, []),
        # History came due at 15:00 but has not consumed the inventory made at
        # 14:00, so the gate keeps holding it and the map waits by design.
        ("2026-09-25T03:00:00+00:00", 43200, None, []),
        # History came due at 12:00 and the inventory made at 14:00 since was
        # interrupted: the gate lets it run, so the map is stalled.
        ("2026-09-25T00:00:00+00:00", 43200, None, ["stale-progress"]),
        # History is due and consumed that inventory: the gate is open, so the
        # map is stalled.
        ("2026-09-25T14:30:00+00:00", 3600, None, ["stale-progress"]),
        ("2026-09-25T14:30:00+00:00", 3600, "succeeded", ["stale-progress"]),
        # A history whose latest attempt failed does not open the gate (D-0210).
        ("2026-09-25T14:30:00+00:00", 3600, "transient", []),
    ],
)
def test_units_waiting_for_the_paced_inventory_are_not_a_stall(
    tmp_path: Path, history: str, interval: int, failed: str | None, alerts: list[str]
) -> None:
    state = State(tmp_path).fetched(NOW)
    state.history_output(history)
    if failed is not None:
        state.attempt("all", failed, stage="project", kind="history", finished=QUEUED)
    state.sql(
        "INSERT INTO derivation_generations VALUES ('inv','project','inventory','all',?)",
        "2026-09-25T14:00:00+00:00",
    )
    state.sql("INSERT INTO derivation_scopes VALUES ('project','inventory','all','inv')")
    # A dancer change queued the inventory, which the gate holds back.
    state.pending("all", stage="project", kind="inventory", enqueued="2026-09-25T10:00:00+00:00")
    state.attempt("m0", "succeeded", stage="project", kind="map", finished=QUEUED)
    state.pending("all", stage="project", kind="map", enqueued="2026-09-25T10:00:00+00:00")
    state.close()
    lines = _run(tmp_path, extra=_config(tmp_path, history=interval, inventory=43200))
    assert _keys(lines) == alerts
    # Without the inventory interval the same map is a stall either way.
    (tmp_path / "config" / "sources.toml").write_text("")
    assert _keys(_run(tmp_path, extra=["--config", str(tmp_path / "config")])) == ["stale-progress"]


def test_a_due_history_waiting_for_the_paced_inventory_is_not_a_stall(tmp_path: Path) -> None:
    """History due at 12:00 needs the inventory made at 11:00, which the gate
    holds until 23:00."""
    state = State(tmp_path).fetched(NOW)
    state.history("2026-09-25T00:00:00+00:00")
    state.sql(
        "INSERT INTO derivation_generations VALUES ('inv','project','inventory','all',?)",
        "2026-09-25T11:00:00+00:00",
    )
    state.sql("INSERT INTO derivation_scopes VALUES ('project','inventory','all','inv')")
    state.pending("all", stage="project", kind="inventory", enqueued="2026-09-25T11:30:00+00:00")
    state.close()
    assert _run(tmp_path, extra=_config(tmp_path, inventory=43200)) == []
    # Unpaced, history due four hours ago with no run is a stall.
    (tmp_path / "config" / "sources.toml").write_text(
        "[scheduling]\nhistory_interval_seconds = 43200\n"
    )
    assert _keys(_run(tmp_path, extra=["--config", str(tmp_path / "config")])) == ["stale-progress"]


@pytest.mark.parametrize("links", [False, True])
def test_a_history_starved_by_interrupted_cascades_alarms(tmp_path: Path, links: bool) -> None:
    """History came due on 09-23 and every cascade since was interrupted by a
    dancer change (phase 1 review). Its clock starts at the first inventory
    made since it came due and later ones do not move it, so the starvation
    alarms with or without a link unit queued."""
    state = State(tmp_path).fetched(NOW)
    state.history("2026-09-22T12:00:00+00:00")
    state.attempt(
        "all", "succeeded", stage="project", kind="history", finished="2026-09-22T12:00:00+00:00"
    )
    for day in ("23", "24"):
        state.generation(f"2026-09-{day}T02:00:00+00:00", stage="project", kind="inventory")
    state.sql(
        "INSERT INTO derivation_generations VALUES ('inv','project','inventory','all',?)",
        "2026-09-25T14:00:00+00:00",
    )
    state.sql("INSERT INTO derivation_scopes VALUES ('project','inventory','all','inv')")
    # A dancer change interrupted this cascade too.
    state.pending("all", stage="project", kind="inventory", enqueued="2026-09-25T15:00:00+00:00")
    if links:
        state.attempt(
            "l0", "succeeded", stage="link", kind="event", finished="2026-09-22T13:00:00+00:00"
        )
        state.pending("l1", stage="link", kind="event", enqueued="2026-09-25T15:00:00+00:00")
    state.close()
    lines = _run(tmp_path, extra=_config(tmp_path, inventory=43200))
    assert "stale-progress" in _keys(lines)
    assert "project/history" in lines[0]
    assert "cascade from the inventory at 2026-09-23 02:00 UTC" in lines[0]
    assert "interrupted since 2026-09-25 14:00 UTC" in lines[0]


def test_a_cascade_after_a_late_history_is_clocked_from_its_start(tmp_path: Path) -> None:
    """History came due at 03:00 while the inventory waited out its interval;
    the inventory ran at 15:00 and its cascade is running. History gets the
    full stall allowance from 15:00, not from 03:00."""
    state = State(tmp_path).fetched(NOW)
    state.history("2026-09-24T15:00:00+00:00")
    state.sql(
        "INSERT INTO derivation_generations VALUES ('inv','project','inventory','all',?)",
        "2026-09-25T15:00:00+00:00",
    )
    state.sql("INSERT INTO derivation_scopes VALUES ('project','inventory','all','inv')")
    state.close()
    config = _config(tmp_path, inventory=43200)
    assert _run(tmp_path, extra=config) == []
    later = _run(tmp_path, extra=config, now="2026-09-25T17:30:00+00:00")
    assert _keys(later) == ["stale-progress"]
    assert "cascade from the inventory at 2026-09-25 15:00 UTC" in later[0]


def test_the_deferred_inventory_itself_is_not_a_stall(tmp_path: Path) -> None:
    state = State(tmp_path).fetched(NOW)
    # History made an hour ago: not due.
    state.history_output("2026-09-25T15:10:00+00:00")
    # The inventory materialized at 13:00, within its 12-hour interval.
    state.sql(
        "INSERT INTO derivation_generations VALUES ('inv','project','inventory','all',?)",
        "2026-09-25T13:00:00+00:00",
    )
    state.sql("INSERT INTO derivation_scopes VALUES ('project','inventory','all','inv')")
    # A dancer succeeded at 13:30 and queued the inventory then.
    state.attempt(
        "d1", "succeeded", stage="project", kind="dancer", finished="2026-09-25T13:30:00+00:00"
    )
    state.pending("all", stage="project", kind="inventory", enqueued="2026-09-25T13:30:00+00:00")
    state.close()
    lines = _run(tmp_path, extra=_config(tmp_path, inventory=43200))
    assert lines == []


def test_a_runnable_event_stalls_while_the_inventory_is_current(tmp_path: Path) -> None:
    state = State(tmp_path).fetched(NOW)
    # History and the inventory both materialized within their intervals.
    state.generation("2026-09-25T04:30:00+00:00", stage="project", kind="history")
    state.sql(
        "INSERT INTO derivation_generations VALUES ('inv','project','inventory','all',?)",
        "2026-09-25T13:00:00+00:00",
    )
    state.sql("INSERT INTO derivation_scopes VALUES ('project','inventory','all','inv')")
    # The inventory is current and not pending, so nothing holds this event.
    state.attempt(
        "e0", "succeeded", stage="project", kind="event", finished="2026-09-25T04:00:00+00:00"
    )
    state.pending("e1", stage="project", kind="event", enqueued="2026-09-25T04:00:00+00:00")
    state.close()
    lines = _run(tmp_path, extra=_config(tmp_path, inventory=43200))
    assert _keys(lines) == ["stale-progress"]


def _failures(state: Path, unit: WorkUnit, reasons: list[str], *, minutes_ago: int = 10) -> None:
    """Record failures through the package's queue policy on the real schema."""
    from datetime import timedelta

    from swingset.state import queue

    moment = datetime.fromisoformat(NOW) - timedelta(minutes=minutes_ago)
    with open_database(state, lock=False) as database, database.transaction() as conn:
        for reason in reasons:
            name, _, exception = reason.partition(":")
            queue.fail(
                conn,
                unit,
                reason=name,
                exception=exception or None,
                fingerprint="fp",
                now=moment,
                error="boom",
            )


EVENT_A = WorkUnit("project", "event", "event-a")


@pytest.mark.parametrize(
    "reasons, alerts",
    [
        # Three failures of one class quarantine the unit: one line names it.
        (["unit_exception:ValueError"] * 3, ["unit-quarantined"]),
        (["nondeterministic_output"] * 3, ["unit-quarantined"]),
        # Two are not yet a pattern; alternating classes are not one failure.
        (["unit_exception:ValueError"] * 2, []),
        (["unit_exception:ValueError", "write_deadline_exceeded"] * 3, []),
        (["unit_exception:ValueError", "unit_exception:KeyError"] * 3, []),
        # Retried by design: an I/O error needs twenty, an unknown page kind waits.
        (["unit_io_error"] * 3, []),
        (["unsupported_page_kind"] * 3, []),
    ],
)
def test_a_quarantined_unit_alerts_once(
    tmp_path: Path, reasons: list[str], alerts: list[str]
) -> None:
    _failures(tmp_path, EVENT_A, reasons)
    lines = _run(tmp_path)
    assert _keys(lines) == alerts
    if alerts:
        assert lines[0].startswith("[unit-quarantined] project/event/event-a after 3 x ")
        assert lines[0].endswith(": boom")
        # Logged once: the same line is held back for the whole window.
        _log(tmp_path, "2026-09-25T16:10:00Z " + lines[0])
        assert _run(tmp_path, now="2026-09-25T17:30:00+00:00") == []
    # A quarantine older than the window is not news any more.
    assert _run(tmp_path, now="2026-09-25T18:05:00+00:00") == []


@pytest.mark.parametrize("tries, alerts", [(3, []), (19, []), (20, ["unit-quarantined"])])
def test_an_io_error_quarantines_at_twenty(tmp_path: Path, tries: int, alerts: list[str]) -> None:
    """An I/O error retries in a minute by design; one that fails every retry
    (a corrupt blob, a permission error) is quarantined at twenty (D-0216)."""
    _failures(tmp_path, WorkUnit("parse", "snapshot", "snap"), ["unit_io_error"] * tries)
    lines = _run(tmp_path)
    assert _keys(lines) == alerts
    if alerts:
        assert "parse/snapshot/snap after 20 x unit_io_error" in lines[0]


def test_a_quarantine_storm_is_one_line_by_class(tmp_path: Path) -> None:
    for number in range(6):
        _failures(
            tmp_path, WorkUnit("link", "event", f"e{number}"), ["unit_exception:TypeError"] * 3
        )
    _failures(tmp_path, WorkUnit("project", "event", "x"), ["write_deadline_exceeded"] * 3)
    lines = _run(tmp_path)
    assert lines == [
        "[unit-quarantined] 7 units quarantined: unit_exception:TypeError x 6,"
        " write_deadline_exceeded x 1"
    ]


def test_a_unit_that_succeeded_after_failing_does_not_alert(tmp_path: Path) -> None:
    from swingset.state import queue

    _failures(tmp_path, EVENT_A, ["unit_exception:ValueError"] * 2)
    with open_database(tmp_path, lock=False) as database, database.transaction() as conn:
        queue.succeed(conn, EVENT_A)
    assert _run(tmp_path) == []


@pytest.mark.parametrize("minutes, alerts", [(9, []), (11, ["lease-stuck"])])
def test_a_lease_long_past_its_end_alerts(tmp_path: Path, minutes: int, alerts: list[str]) -> None:
    from datetime import timedelta

    from swingset.state import queue

    until = datetime.fromisoformat(NOW) - timedelta(minutes=minutes)
    with open_database(tmp_path, lock=False) as database, database.transaction() as conn:
        queue.upsert(
            conn,
            queue.QueueRow(
                stage="project",
                unit_kind="event",
                unit_id="e1",
                partition="event/e1",
                state="leased",
                attempts=0,
                error_class=None,
                input_fingerprint=None,
                lease_owner="derive:run_1",
                lease_until=queue.at(until),
                next_attempt_at=None,
                last_reason=None,
                last_error=None,
                enqueued_at=queue.at(until),
                updated_at=queue.at(until),
                quarantined_at=None,
            ),
        )
    lines = _run(tmp_path)
    assert _keys(lines) == alerts
    if alerts:
        assert "project/event/e1 (derive:run_1, until 2026-09-25 15:59 UTC)" in lines[0]


@pytest.mark.parametrize(
    "state, next_attempt, alerts",
    [
        ("waiting_input", "2026-09-26T00:00:00.000000Z", []),
        ("quarantined", "2026-10-02T00:00:00.000000Z", []),
        ("ready", "2026-09-25T18:00:00.000000Z", []),
        ("ready", "2026-09-25T12:00:00.000000Z", ["stale-progress"]),
        ("ready", None, ["stale-progress"]),
    ],
)
def test_units_waiting_on_a_queue_clock_are_not_ready(
    tmp_path: Path, state: str, next_attempt: str | None, alerts: list[str]
) -> None:
    """A unit that waits for input, is quarantined or backs off is no stall."""
    db = _stalled(tmp_path)
    db.sql(
        "INSERT INTO work_queue(stage,unit_kind,unit_id,state,next_attempt_at)"
        " VALUES ('link','event','e1',?,?)",
        state,
        next_attempt,
    )
    db.close()
    assert _keys(_run(tmp_path, stale_fetch=86400)) == alerts


def test_ready_units_are_counted_before_schema_34(tmp_path: Path) -> None:
    db = _stalled(tmp_path)
    db.sql("DROP TABLE work_queue")
    db.close()
    assert _keys(_run(tmp_path, stale_fetch=86400)) == ["stale-progress"]


def test_state_over_its_size_alert_alerts_without_counting_checkpoints(tmp_path: Path) -> None:
    State(tmp_path).fetched(NOW).close()
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / "a").write_bytes(b"x" * 4096)
    (tmp_path / "checkpoints" / "run_20260925T000000Z").mkdir(parents=True)
    (tmp_path / "checkpoints" / "run_20260925T000000Z" / "state.sqlite").write_bytes(
        b"x" * 1_000_000
    )
    # The service's Python environment and uv cache are code, not state.
    for name in ("venv", "uv-cache"):
        (tmp_path / name / "lib").mkdir(parents=True)
        (tmp_path / name / "lib" / "big").write_bytes(b"x" * 1_000_000)
    skipped = {"checkpoints", "venv", "uv-cache"}
    size = sum(
        path.stat().st_size
        for path in tmp_path.rglob("*")
        if path.is_file() and path.relative_to(tmp_path).parts[0] not in skipped
    )
    assert _run(tmp_path, extra=["--state-size-alert", str(size)]) == []
    lines = _run(tmp_path, extra=["--state-size-alert", str(size - 1)])
    assert _keys(lines) == ["state-size"]
    assert "architecture phase 4" in lines[0]


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


def test_a_code_scope_change_that_derive_never_runs_alarms(tmp_path: Path) -> None:
    """I4 after phase 1: a code-only accept makes project units stale by
    fingerprint alone. Its queue hints let the watchdog see them, so a derive
    that never picks them up alarms after two hours."""
    from swingset.state.work import accept_inputs, affected_work

    with open_database(tmp_path, lock=False) as database:
        with database.transaction() as conn:
            conn.executemany(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
                "VALUES ('project','event',?,?)",
                (("e1", QUEUED), ("e2", QUEUED)),
            )
    # The watchdog reads the file as immutable, so each step closes it first.
    assert _run(tmp_path) == []
    with open_database(tmp_path, lock=False) as database:
        accept_inputs(
            database,
            "pipeline",
            {"recipe/code/project/event": "changed"},
            lambda name, old, new, conn: affected_work(conn, name),
            accepted_at="2026-09-25T13:00:00+00:00",
        )
    assert _run(tmp_path, now="2026-09-25T14:59:00+00:00") == []
    assert _run(tmp_path) == [
        "[stale-progress] no progress while unheld: project 3 h 10 m with 2 ready"
    ]


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


def test_a_write_deadline_firing_in_a_run_record_alerts_by_loop(tmp_path: Path) -> None:
    """The 60 s alarm should never fire (D-0217): any run that saw it is named."""
    _write_runs(
        tmp_path,
        # Older than two hours: not news any more.
        {"run_id": "old", "finished_at": "2026-09-25T13:00:00+00:00", "write_deadline_fired": 4},
        {
            "run_id": "c1",
            "finished_at": "2026-09-25T15:00:00+00:00",
            "write_deadline_fired": 1,
            "write_seconds_max": 60.4,
        },
        {"run_id": "c2", "finished_at": "2026-09-25T15:30:00+00:00", "write_deadline_fired": 0},
        {
            "run_id": "d1",
            "loop": "derive",
            "finished_at": "2026-09-25T16:00:00+00:00",
            "write_deadline_fired": 2,
            "write_seconds_max": 61.2,
        },
        {"run_id": "b1", "command": "backup", "finished_at": "2026-09-25T16:05:00+00:00"},
    )
    lines = [line for line in _run(tmp_path) if line.startswith("[write-deadline]")]
    assert lines == [
        "[write-deadline] cycle: 1 writes cut off at the deadline in 1 runs in the last 2 h,"
        " latest c1; longest write 60 s",
        "[write-deadline] derive: 2 writes cut off at the deadline in 1 runs in the last 2 h,"
        " latest d1; longest write 61 s",
    ]
    # Each line logs once for the window; a new firing changes the text.
    _log(tmp_path, *(f"2026-09-25T16:10:00Z {line}" for line in lines))
    later = [
        line
        for line in _run(tmp_path, now="2026-09-25T16:40:00+00:00")
        if line.startswith("[write-deadline]")
    ]
    assert later == []
    # Two hours after the last firing the check is quiet.
    assert not [
        line
        for line in _run(tmp_path, now="2026-09-25T18:30:00+00:00")
        if line.startswith("[write-deadline]")
    ]


def test_run_records_without_firings_do_not_alert(tmp_path: Path) -> None:
    _write_runs(
        tmp_path,
        {"run_id": "r1", "finished_at": NOW, "write_deadline_fired": 0},
        {"run_id": "r2", "finished_at": NOW},
    )
    assert _run(tmp_path) == []


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


def test_a_failed_summary_unit_alerts(tmp_path: Path) -> None:
    """The digest runs near its 5 GB cap; killed there it must not go dark."""
    assert _run(tmp_path) == []
    line = (
        "[summary-failed] swingset-summary.service failed, possibly at its 5 GB memory cap;"
        " read journalctl -u swingset-summary"
    )
    assert _run(tmp_path, extra=["--summary-failed", "1"]) == [line]
    # A hold stops the digest from starting, not the failed state.
    (tmp_path / "operator-hold").touch()
    assert _run(tmp_path, timer=0, extra=["--summary-failed", "1"]) == [line]


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


def test_equivalences_past_the_collector_trigger_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing collects equivalences before invalidation step 3; the row count
    of either table is D-0208's trigger, and this line is its clock."""
    monkeypatch.setattr(watchdog, "EQUIVALENCE_ROWS", 2)
    monkeypatch.setattr(watchdog, "SOURCE_EQUIVALENCE_ROWS", 2)
    state = State(tmp_path).fetched(NOW)
    state.sql(
        "CREATE TABLE derivation_equivalences (stage TEXT, unit_kind TEXT, unit_id TEXT,"
        " generation_id TEXT, created_at TEXT)"
    )
    state.sql("CREATE TABLE source_equivalences (unit_key TEXT, input_fingerprint TEXT)")
    for unit in ("1", "2"):
        state.sql(
            "INSERT INTO derivation_equivalences VALUES ('project','dancer',?,'g',?)", unit, NOW
        )
    assert _run(tmp_path) == []
    state.sql("INSERT INTO derivation_equivalences VALUES ('project','dancer','3','g',?)", NOW)
    lines = _run(tmp_path)
    assert _keys(lines) == ["equivalences"]
    assert "derivation_equivalences holds 3 rows, over 2" in lines[0]
    # Step 3 deletes rows: the count clears the line although the largest
    # rowid stays past the mark.
    state.sql("DELETE FROM derivation_equivalences WHERE unit_id = '1'")
    assert _run(tmp_path) == []
    # Source equivalences carry a recipe each and have their own trigger.
    for fingerprint in ("a", "b", "c"):
        state.sql("INSERT INTO source_equivalences VALUES ('u', ?)", fingerprint)
    state.close()
    lines = _run(tmp_path)
    assert _keys(lines) == ["equivalences"]
    assert "source_equivalences holds 3 rows, over 2" in lines[0]


@pytest.mark.parametrize(
    "broken, alerts",
    [
        ({}, []),
        ({"derivation_error": "OperationalError: database is locked"}, ["report-failing"]),
        ({"controls_error": "OperationalError: disk I/O error"}, ["report-failing"]),
        ({"derivation": None}, ["report-failing"]),
    ],
)
def test_a_failing_cycle_report_alerts(
    tmp_path: Path, broken: dict[str, object], alerts: list[str]
) -> None:
    # A record without `derivation` blinds [replay-loop]; that must not read
    # as healthy (I4).
    State(tmp_path).fetched(NOW).close()
    record: dict[str, object] = {
        "finished_at": NOW,
        "readiness_errors": {},
        "controls": {},
        "derivation": {"generations": {}, "equivalences": {}, "replay_bumps": {}},
    }
    last = {key: value for key, value in {**record, **broken}.items() if value is not None}
    _write_runs(tmp_path, record, record, last)
    lines = _run(tmp_path)
    assert _keys(lines) == alerts
    if "derivation_error" in broken:
        assert "database is locked" in lines[0]
    # Records from before phase 1 carry none of these reports.
    _write_runs(tmp_path, {"finished_at": NOW}, {"finished_at": NOW}, {"finished_at": NOW})
    assert _run(tmp_path) == []


def test_the_inventory_wait_lifts_on_a_calendar_replay_or_generation(tmp_path: Path) -> None:
    """The mirror of the gate's calendar rules (schedule/pacing), on the real
    schema. The inventory was made at 13:00 within its interval and history,
    made at 03:00, has not consumed it, so the map after it waits by design.
    A calendar replay epoch the inventory has not read, or a calendar
    generation newer than it, lifts the wait: the map is then a stall. A
    run that reads the new epoch, or the new calendar, paces it again."""
    from swingset.state import derivations

    config = _config(tmp_path, inventory=43200)
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        run = database.start_run(datetime.fromisoformat("2026-09-25T00:00:00+00:00"))
        conn.execute("INSERT OR IGNORE INTO derivation_dependency_sets VALUES ('empty','[]')")
        conn.execute("INSERT OR IGNORE INTO derivation_recipes VALUES ('r','{}')")

        def generation(kind: str, scope: str, generation_id: str, at: str) -> None:
            conn.execute(
                "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
                "VALUES ('project',?,?,?)",
                (kind, scope, at),
            )
            conn.execute(
                "INSERT INTO derivation_generations VALUES "
                "(?,'project',?,?,'f','{}','empty',NULL,'d',0,?,?)",
                (generation_id, kind, scope, at, run),
            )
            conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id=? "
                "WHERE stage='project' AND unit_kind=? AND unit_id=?",
                (generation_id, kind, scope),
            )

        def equivalence(epoch: int, at: str) -> None:
            read = f'[{{"key":["project","calendar"],"kind":"replay","version":{epoch}}}]'
            conn.execute(
                "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)",
                (f"read{epoch}", read),
            )
            conn.execute(
                "INSERT INTO derivation_equivalences VALUES "
                "('project','inventory','all',?,'inv','r',?,?,?)",
                (f"f{epoch}-{at}", f"read{epoch}", at, run),
            )

        generation("calendar", "wsdc", "cal0", "2026-09-25T01:00:00+00:00")
        generation("history", "all", "hist", "2026-09-25T03:00:00+00:00")
        generation("inventory", "all", "inv", "2026-09-25T13:00:00+00:00")
        conn.execute(
            "INSERT INTO work_attempts(stage,unit_kind,unit_id,queue_generation,"
            "retry_generation,work_token,input_fingerprint,run_id,started_at,finished_at,"
            "outcome,reason_code) VALUES ('project','map','all',1,0,'t','f',?,?,?,"
            "'succeeded','output_committed')",
            (run, "2026-09-25T10:00:00+00:00", "2026-09-25T10:00:00+00:00"),
        )

        def queue() -> None:
            # Only the inventory and the map it holds, whatever a replay queued.
            conn.execute("DELETE FROM pending_work")
            conn.execute(
                "INSERT INTO pending_work VALUES "
                "('project','inventory','all','2026-09-25T13:30:00+00:00'),"
                "('project','map','all','2026-09-25T10:00:00+00:00')"
            )

        def stalled() -> bool:
            # The watchdog reads the main file only (immutable=1).
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            lines = _run(tmp_path, extra=config, repeat=0)
            assert "watchdog-blind" not in _keys(lines)
            return "stale-progress" in _keys(lines)

        queue()
        assert not stalled()
        with database.transaction():
            derivations.bump_replay(conn, "project", "calendar", now=NOW)
        queue()
        assert stalled()
        # The inventory's next run read epoch 1; it was made at 13:30, so it
        # is paced again and the map waits.
        equivalence(1, "2026-09-25T13:30:00+00:00")
        assert not stalled()
        # The wsdc calendar mints a generation after it: the gate lets the
        # inventory run again.
        generation("calendar", "wsdc", "cal1", "2026-09-25T13:45:00+00:00")
        assert stalled()
        equivalence(1, "2026-09-25T13:50:00+00:00")
        assert not stalled()


def _guard(tmp_path: Path, subject: str, reason: str, *, seen: str, expires: str) -> None:
    from swingset.state import guards

    with open_database(tmp_path, lock=False) as database, database.transaction() as conn:
        guards.upsert(
            conn,
            guards.GuardRow(
                subject, reason, "cycle", "2026-09-25T10:00:00.000000Z", seen, expires, 3
            ),
        )


@pytest.mark.parametrize(
    "seen, expires, alerts",
    [
        # Seen after its expiry and within the last two hours.
        ("2026-09-25T16:00:00.000000Z", "2026-09-25T11:00:00.000000Z", ["guard-expired"]),
        # Not yet expired.
        ("2026-09-25T16:00:00.000000Z", "2026-09-25T17:00:00.000000Z", []),
        # Stale: last seen over two hours ago, so it cleared or is no longer tried.
        ("2026-09-25T14:00:00.000000Z", "2026-09-25T11:00:00.000000Z", []),
        # Expired since it was last seen: not held past its clock.
        ("2026-09-25T15:30:00.000000Z", "2026-09-25T15:45:00.000000Z", []),
    ],
)
def test_a_guard_seen_past_its_expiry_alerts(
    tmp_path: Path, seen: str, expires: str, alerts: list[str]
) -> None:
    """D-0219: a reason still met after its clock ran out."""
    _guard(tmp_path, "host:a.test", "legacy request spacing unknown", seen=seen, expires=expires)
    lines = _run(tmp_path)
    assert _keys(lines) == [*alerts, "guards"]
    if alerts:
        assert lines[0] == (
            "[guard-expired] host:a.test held by legacy request spacing unknown since"
            " 2026-09-25 10:00 UTC, past its expiry at 2026-09-25 11:00 UTC"
        )
    assert lines[-1] == "[guards] 1 guard rows: legacy request spacing unknown x 1"


def test_a_watch_guard_is_counted_never_alarmed(tmp_path: Path) -> None:
    _guard(
        tmp_path,
        "watch:w1",
        "robots disallow",
        seen="2026-09-25T16:00:00.000000Z",
        expires="2026-09-25T11:00:00.000000Z",
    )
    assert _run(tmp_path) == ["[guards] 1 guard rows: robots disallow x 1"]


def test_many_expired_guards_are_one_line_and_the_count_is_daily(tmp_path: Path) -> None:
    for index in range(6):
        _guard(
            tmp_path,
            f"host:h{index}.test",
            "request budget" if index % 2 else "Throttled",
            seen="2026-09-25T16:00:00.000000Z",
            expires="2026-09-25T11:00:00.000000Z",
        )
    lines = _run(tmp_path)
    assert lines == [
        "[guard-expired] 6 guards past their expiry: Throttled x 3, request budget x 3",
        "[guards] 6 guard rows: Throttled x 3, request budget x 3",
    ]
    _log(tmp_path, *(f"2026-09-25T10:00:00Z {line}" for line in lines))
    # Each line waits out its window: the count a day, the same text --repeat.
    assert _run(tmp_path) == [lines[0]]
    assert _run(tmp_path, repeat=86400) == []


# The loop checks (services.swingset.loops.enable, D-0215).

LOOPS = ("acquire", "plan", "derive", "release", "maintain")


def _loops(
    state: Path,
    *,
    inactive: str = "",
    now: str = NOW,
    extra: list[str] | None = None,
) -> list[str]:
    """The watchdog as the loops-on wrapper runs it: no --timer-active."""
    result: list[str] = watchdog.main(
        [
            *(extra or []),
            "--state",
            str(state),
            "--loops",
            "1",
            f"--timers-inactive={inactive}",
            *ARGS[:8],
            "--disk-min-free",
            "1",
            "--now",
            now,
        ]
    )
    return result


def _ticks(state: Path, *records: dict[str, object]) -> None:
    """Loop tick records, each file's mtime at its finish as the runtime writes it."""
    runs = state / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    for index, record in enumerate(records):
        record = {"run_id": f"run_{index:04d}", "failed": False, **record}
        path = runs / f"{record['run_id']}.json"
        path.write_text(json.dumps(record))
        stamp = datetime.fromisoformat(str(record["finished_at"])).timestamp()
        os.utime(path, (stamp, stamp))


def _fresh(**finished: str) -> list[dict[str, object]]:
    """One tick of each loop, at the times given or at 16:05."""
    return [
        {"loop": name, "finished_at": finished.get(name, "2026-09-25T16:05:00+00:00")}
        for name in LOOPS
        if finished.get(name) != "none"
    ]


def _real(state: Path) -> None:
    with open_database(state, lock=False):
        pass


def _plan(state: Path, planned_at: str) -> None:
    from swingset.state import acquisition_plan

    with open_database(state, lock=False) as database, database.transaction() as conn:
        acquisition_plan.upsert(
            conn,
            acquisition_plan.PlanRow(
                acquisition_plan.year_key(2026), "year", "eligible", planned_at, "run_1", year=2026
            ),
        )


def test_healthy_loops_on_the_real_schema_print_nothing(tmp_path: Path) -> None:
    _plan(tmp_path, "2026-09-25T15:40:00+00:00")
    _ticks(tmp_path, *_fresh())
    assert _loops(tmp_path) == []
    # With the loops off the same records are not cycles: none has finished.
    assert _run(tmp_path, quiet=60) == []


@pytest.mark.parametrize(
    "loop, finished, alerts",
    [
        ("derive", "2026-09-25T15:41:00+00:00", []),
        ("derive", "2026-09-25T15:39:00+00:00", ["loop-quiet"]),
        ("plan", "2026-09-25T14:11:00+00:00", []),
        ("plan", "2026-09-25T14:09:00+00:00", ["loop-quiet"]),
        ("release", "2026-09-25T12:09:00+00:00", ["loop-quiet"]),
        ("maintain", "2026-09-24T14:11:00+00:00", []),
    ],
)
def test_a_loop_with_no_tick_for_three_intervals_is_quiet(
    tmp_path: Path, loop: str, finished: str, alerts: list[str]
) -> None:
    _real(tmp_path)
    _ticks(tmp_path, *_fresh(**{loop: finished}))
    lines = _loops(tmp_path)
    assert _keys(lines) == alerts
    if alerts:
        assert lines[0].startswith(f"[loop-quiet] {loop}: no tick has finished since")
    # An inactive timer names itself instead; a hold silences both.
    assert _keys(_loops(tmp_path, inactive=loop)) == ["timer-inactive"]
    (tmp_path / "operator-hold").touch()
    assert _loops(tmp_path, inactive=loop) == []
    assert _loops(tmp_path) == []


def test_the_quiet_line_names_the_last_tick_and_holds_its_text(tmp_path: Path) -> None:
    _real(tmp_path)
    _ticks(tmp_path, *_fresh(derive="2026-09-25T15:00:00+00:00"))
    lines = _loops(tmp_path)
    assert lines == [
        "[loop-quiet] derive: no tick has finished since 2026-09-25 15:00 UTC,"
        " over 30 minutes while unheld"
    ]
    _log(tmp_path, f"2026-09-25T16:10:00Z {lines[0]}")
    assert _loops(tmp_path, now="2026-09-25T16:30:00+00:00") == []
    # Cycle mode never reads loop ticks for this.
    (tmp_path / "ALERTS.log").unlink()
    assert "loop-quiet" not in _keys(_run(tmp_path))


def test_a_loop_that_never_ticked_is_quiet_once_the_others_ran_past_its_window(
    tmp_path: Path,
) -> None:
    _real(tmp_path)
    # Loops switched on an hour ago: maintain has not had its first 03:30.
    _ticks(tmp_path, {"loop": "acquire", "finished_at": "2026-09-25T15:10:00+00:00"})
    _ticks(tmp_path, *_fresh(maintain="none"))
    assert _loops(tmp_path) == []
    # Ticks run back past maintain's 26 hours with no maintain tick among them.
    old = {"run_id": "run_old", "loop": "acquire", "finished_at": "2026-09-24T12:00:00+00:00"}
    _ticks(tmp_path, old)
    assert _loops(tmp_path) == [
        "[loop-quiet] maintain: no tick on record since 2026-09-24 12:00 UTC,"
        " over 26 h while unheld"
    ]


def test_three_failed_ticks_of_one_loop_alert_even_when_held(tmp_path: Path) -> None:
    _real(tmp_path)
    _ticks(
        tmp_path,
        *_fresh(release="none"),
        {"loop": "release", "finished_at": "2026-09-25T13:20:00+00:00", "failed": True},
        {
            "loop": "release",
            "finished_at": "2026-09-25T14:20:00+00:00",
            "failed": True,
            "build_error": "BuildError: closure",
        },
        # A skip is neither a failure nor a success.
        {"loop": "release", "finished_at": "2026-09-25T15:00:00+00:00", "skipped": "heavy_busy"},
        {
            "loop": "release",
            "finished_at": "2026-09-25T15:20:00+00:00",
            "failed": True,
            "build_error": "BuildError: disk full",
        },
    )
    expected = ["[loop-failing] release: the last 3 ticks failed; latest: BuildError: disk full"]
    assert _loops(tmp_path) == expected
    (tmp_path / "operator-hold").touch()
    assert _loops(tmp_path) == expected
    # Cycle mode reads cycle records only.
    assert "loop-failing" not in _keys(_run(tmp_path))


def test_a_success_among_the_last_three_ticks_does_not_alert(tmp_path: Path) -> None:
    _real(tmp_path)
    failed = {"loop": "derive", "failed": True, "error": "OperationalError: locked"}
    _ticks(
        tmp_path,
        *_fresh(),
        {**failed, "finished_at": "2026-09-25T16:06:00+00:00"},
        {"loop": "derive", "finished_at": "2026-09-25T16:07:00+00:00"},
        {**failed, "finished_at": "2026-09-25T16:08:00+00:00"},
    )
    assert _loops(tmp_path) == []


@pytest.mark.parametrize(
    "planned, alerts",
    [("2026-09-25T14:11:00+00:00", []), ("2026-09-25T14:09:00+00:00", ["plan-stale"])],
)
def test_a_plan_older_than_two_hours_alerts_with_the_loops_on(
    tmp_path: Path, planned: str, alerts: list[str]
) -> None:
    _plan(tmp_path, planned)
    _ticks(tmp_path, *_fresh())
    lines = _loops(tmp_path)
    assert _keys(lines) == alerts
    if alerts:
        assert lines[0] == (
            "[plan-stale] the newest acquisition_plan row was planned at 2026-09-25 14:09 UTC,"
            " over 2 h ago; acquire offers only planned pages"
        )
    # The cycle does not plan; a held or stopped plan is not stale.
    assert _run(tmp_path) == []
    assert _keys(_loops(tmp_path, inactive="plan")) == ["timer-inactive"]
    (tmp_path / "operator-hold").touch()
    assert _loops(tmp_path) == []


def test_inactive_loop_timers_are_named_and_stop_only_their_own_stall_check(
    tmp_path: Path,
) -> None:
    _stalled(tmp_path).close()
    _ticks(tmp_path, *_fresh())
    assert _keys(_loops(tmp_path)) == ["stale-fetch", "stale-progress"]
    lines = _loops(tmp_path, inactive="acquire,release")
    assert _keys(lines) == ["timer-inactive", "stale-progress"]
    assert lines[0] == (
        "[timer-inactive] swingset-acquire.timer, swingset-release.timer not active and nothing"
        " holds the worker; start the timers or write operator-hold"
    )
    assert _keys(_loops(tmp_path, inactive="derive")) == ["timer-inactive", "stale-fetch"]


def test_the_loop_arguments_are_checked(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        watchdog.main(["--state", str(tmp_path), *ARGS[:-2]])
    with pytest.raises(SystemExit):
        _loops(tmp_path, inactive="cycle")


def test_replay_and_report_checks_read_derive_ticks_with_the_loops_on(tmp_path: Path) -> None:
    _real(tmp_path)
    derivation = {
        "generations": {},
        "equivalences": {},
        "replay_bumps": {"project/event": 1, "project/source_event": 1},
    }
    derive = {"loop": "derive", "readiness_errors": {}, "derivation": derivation}
    _ticks(
        tmp_path,
        *_fresh(),
        *({**derive, "finished_at": f"2026-09-25T16:0{minute}:00+00:00"} for minute in (6, 7, 8)),
    )
    assert _loops(tmp_path) == [
        "[replay-loop] replay epochs moved in each of the last three derive ticks with no new"
        " generation that explains them: project/event, project/source_event"
    ]
    _ticks(
        tmp_path,
        {
            "run_id": "run_x",
            "loop": "derive",
            "finished_at": "2026-09-25T16:09:00+00:00",
            "readiness_errors": {},
            "derivation_error": "OperationalError: locked",
        },
    )
    assert _keys(_loops(tmp_path)) == ["report-failing"]
    assert _loops(tmp_path)[0].startswith(
        "[report-failing] derive tick reports failed in the last three derive ticks"
    )


def _wal(state: Path) -> sqlite3.Connection:
    """A live WAL of about 400 KB that nothing has checkpointed."""
    _real(state)
    db = sqlite3.connect(state / "state.sqlite")
    db.execute("PRAGMA wal_autocheckpoint = 0")
    db.execute("CREATE TABLE filler (x BLOB)")
    for _ in range(100):
        db.execute("INSERT INTO filler VALUES (randomblob(4000))")
        db.commit()
    return db


def test_a_large_wal_alerts_in_both_modes_and_a_reset_one_does_not(tmp_path: Path) -> None:
    db = _wal(tmp_path)
    try:
        live, size = watchdog._wal_bytes(str(tmp_path))
        assert live == size > 400_000
        limit = ["--wal-large", "300000"]
        assert _keys(_run(tmp_path, extra=limit)) == ["wal-large"]
        (tmp_path / "operator-hold").touch()
        lines = _loops(tmp_path, extra=limit)
        assert _keys(lines) == ["wal-large"]
        assert lines[0].startswith("[wal-large] state.sqlite-wal holds 0.0 GB of frames")
        # A checkpoint, then a write: the log restarts at the top and the file
        # keeps its size, which no longer counts.
        assert db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[0] == 0
        db.execute("INSERT INTO filler VALUES (1)")
        db.commit()
        live, after = watchdog._wal_bytes(str(tmp_path))
        assert after == size and live < 50_000
        assert _run(tmp_path, extra=limit) == []
    finally:
        db.close()


@pytest.mark.parametrize(
    "updated, alerts",
    [("2026-09-24T16:11:00+00:00", []), ("2026-09-24T16:09:00+00:00", ["building-orphans"])],
)
def test_a_staged_generation_idle_for_a_day_alerts_in_both_modes(
    tmp_path: Path, updated: str, alerts: list[str]
) -> None:
    _real(tmp_path)
    with open_database(tmp_path, lock=False) as database, database.transaction() as conn:
        conn.execute(
            "INSERT INTO derivation_building(generation_id,stage,unit_kind,unit_id,"
            "input_fingerprint,label_rows,rows_stored,run_id,started_at,updated_at) "
            "VALUES ('g1','project','history','all','f',12000,5000,'run_1',?,?)",
            (updated, updated),
        )
    _ticks(tmp_path, *_fresh())
    _plan(tmp_path, "2026-09-25T16:00:00+00:00")
    lines = _run(tmp_path)
    assert _keys(lines) == alerts
    assert _keys(_loops(tmp_path)) == alerts
    if alerts:
        assert lines[0] == (
            "[building-orphans] 1 staged generations not advanced for 24 h:"
            " project/history/all (5000 of 12000 rows, last 2026-09-24 16:09 UTC)"
        )
    (tmp_path / "operator-hold").touch()
    assert _run(tmp_path) == []


@pytest.mark.parametrize("rows, alerts", [(3, []), (4, ["incomplete-generations"])])
def test_partial_generations_nothing_collects_alert_above_three(
    tmp_path: Path, rows: int, alerts: list[str]
) -> None:
    _real(tmp_path)
    maintained = {
        "loop": "maintain",
        "finished_at": "2026-09-25T16:06:00+00:00",
        "incomplete_generations": {"rows": rows, "refs": 12},
    }
    _ticks(tmp_path, *_fresh(), maintained)
    _plan(tmp_path, "2026-09-25T16:00:00+00:00")
    lines = _loops(tmp_path)
    assert _keys(lines) == alerts
    if alerts:
        assert lines[0].startswith("[incomplete-generations] 4 partial generations (12 references)")
    (tmp_path / "operator-hold").touch()
    assert _loops(tmp_path) == []


def _progress(state: Path, **last: str | None) -> None:
    """loop_state rows as ticks write them: each loop's last progress time."""
    from swingset.state import loop_state

    with open_database(state, lock=False) as database, database.transaction() as conn:
        for name, stamp in last.items():
            loop_state.upsert(
                conn,
                loop_state.LoopStateRow(
                    loop=name,  # type: ignore[arg-type]
                    updated_at="2026-09-25T16:05:00+00:00",
                    run_id="run_x",
                    started_at="2026-09-25T16:04:00+00:00",
                    finished_at="2026-09-25T16:05:00+00:00",
                    failed=0,
                    error=None,
                    counters_json="{}",
                    last_progress_at=stamp,
                ),
            )


@pytest.mark.parametrize(
    "loop, last, alerts",
    [
        ("plan", "2026-09-25T14:11:00+00:00", []),
        ("plan", "2026-09-25T14:09:00+00:00", ["loop-idle"]),
        ("acquire", "2026-09-25T15:11:00+00:00", []),
        ("acquire", "2026-09-25T15:09:00+00:00", ["loop-idle"]),
        # Six hours of build interval plus two of slack.
        ("release", "2026-09-25T08:11:00+00:00", []),
        ("release", "2026-09-25T08:09:00+00:00", ["loop-idle"]),
        # Nothing is ready, so an idle derive is healthy.
        ("derive", "2026-09-25T10:00:00+00:00", []),
    ],
)
def test_a_loop_whose_ticks_finish_without_progress_is_idle(
    tmp_path: Path, loop: str, last: str, alerts: list[str]
) -> None:
    _plan(tmp_path, "2026-09-25T15:40:00+00:00")
    _ticks(tmp_path, *_fresh())
    fresh = "2026-09-25T16:05:00+00:00"
    last_progress = {name: fresh for name in LOOPS if name != "maintain"}
    _progress(tmp_path, **{**last_progress, loop: last})
    lines = _loops(tmp_path)
    assert _keys(lines) == alerts
    if alerts:
        assert lines[0].startswith(f"[loop-idle] {loop}: no progress since")
    # A hold or a stopped timer silences it.
    assert "loop-idle" not in _keys(_loops(tmp_path, inactive=loop))
    (tmp_path / "operator-hold").touch()
    assert _loops(tmp_path) == []


def test_an_idle_acquire_held_by_a_guard_and_a_release_inside_its_interval_are_quiet(
    tmp_path: Path,
) -> None:
    from swingset.state import guards, queue

    _plan(tmp_path, "2026-09-25T15:40:00+00:00")
    _ticks(
        tmp_path,
        *_fresh(release="none"),
        {
            "loop": "release",
            "finished_at": "2026-09-25T16:05:00+00:00",
            "build_deferred": "interval",
            "build_interval_seconds": 21600,
        },
    )
    old = "2026-09-25T01:00:00+00:00"
    _progress(
        tmp_path,
        acquire=old,
        release=old,
        plan="2026-09-25T16:05:00+00:00",
        derive="2026-09-25T16:05:00+00:00",
    )
    seen = queue.at(datetime(2026, 9, 25, 16, 0, tzinfo=UTC))
    with open_database(tmp_path, lock=False) as database, database.transaction() as conn:
        guards.upsert(
            conn,
            guards.GuardRow("host:eepro.com", "request budget", "acquire", seen, seen, seen),
        )
    assert "loop-idle" not in _keys(_loops(tmp_path))


def test_derive_is_idle_only_while_ready_work_waits(tmp_path: Path) -> None:
    _plan(tmp_path, "2026-09-25T15:40:00+00:00")
    with open_database(tmp_path, lock=False) as database, database.transaction() as conn:
        # One parse unit that never had an attempt: ready work.
        conn.execute(
            "INSERT INTO pending_work VALUES ('parse','snapshot','snap_ready',"
            "'2026-09-25T12:00:00+00:00')"
        )
    _ticks(tmp_path, *_fresh())
    fresh = "2026-09-25T16:05:00+00:00"
    _progress(
        tmp_path,
        acquire=fresh,
        plan=fresh,
        release=fresh,
        derive="2026-09-25T13:00:00+00:00",
    )
    lines = [line for line in _loops(tmp_path) if line.startswith("[loop-idle]")]
    assert lines == [
        "[loop-idle] derive: no progress since 2026-09-25 13:00 UTC, over 2 h while unheld;"
        " its ticks finish but do nothing (read its run records)"
    ]


def test_builds_that_keep_failing_alert_through_the_deferred_ticks_between_them(
    tmp_path: Path,
) -> None:
    """Release ticks hourly and builds every interval: the ticks that only
    waited for the interval neither break nor extend a run of failed builds."""
    _real(tmp_path)
    failed = {"loop": "release", "failed": True, "build_error": "BuildError: killed"}
    waited = {"loop": "release", "build_deferred": "interval"}
    records: list[dict[str, object]] = [*_fresh(release="none")]
    for hour in (9, 11, 13):
        stamp = f"2026-09-25T{hour:02}:00:00+00:00"
        records.append({**failed, "build_attempted": True, "finished_at": stamp})
        # Five hourly-style ticks that only waited, before the next build.
        records.extend(
            {**waited, "finished_at": f"2026-09-25T{hour:02}:{10 * n:02}:00+00:00"}
            for n in range(1, 6)
        )
    _ticks(tmp_path, *records)
    assert _keys(_loops(tmp_path)) == ["loop-failing"]


def test_loop_quiet_waits_for_a_running_backup(tmp_path: Path) -> None:
    _real(tmp_path)
    _ticks(tmp_path, *_fresh(derive="2026-09-25T15:00:00+00:00"))
    assert _keys(_loops(tmp_path)) == ["loop-quiet"]
    assert "loop-quiet" not in _keys(_loops(tmp_path, extra=["--backup-active", "1"]))
