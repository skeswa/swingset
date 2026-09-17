"""Refresh the shared Archive robots cache with one sealed request."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import http.cookiejar
import json
import math
import sqlite3
import zlib
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx

import journal.tools.admission.stepright_body_runner as shared_runner
from journal.tools.admission.stepright_body_runner import (
    AUTHORITY,
    HELD_UNITS,
    HOST,
    KIND,
    PUBLISHED_BASELINE,
    RUNNER_HELPERS,
    SOURCE,
    StepRightStopped,
    archive_prestate,
    check_ordinary_source_policy,
    decoded_chunks,
    production_runtime_guard,
    retained_headers,
    sha256,
    verify_live_publication,
    verify_loaded_swingset_modules,
    verify_source,
)
from swingset.clock import Clock, SystemClock
from swingset.config import Config, SourceConfig, load_config
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.classify import Classification, Outcome, classify
from swingset.fetch.client import USER_AGENT
from swingset.fetch.politeness import Gate, Grant, Paused
from swingset.sources.base import PageKind
from swingset.state import db as db_module
from swingset.state.controls import ActionScope, matching_pauses

ROBOTS_URL = f"https://{HOST}/robots.txt"
MAX_REQUESTS = 1
MAX_RESPONSE = 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024
MAX_ELAPSED_SECONDS = 15 * 60
MAX_PRESTATE_AGE_SECONDS = 15 * 60


class _RejectCookies(http.cookiejar.DefaultCookiePolicy):
    def set_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False

    def return_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False


class _RobotsPageKind:
    kind = "archive_robots_refresh"

    @staticmethod
    def expected_statuses(_: Any) -> frozenset[int]:
        return frozenset({404, 410})


ROBOTS_KIND = cast(PageKind, _RobotsPageKind())


def validate_authorization(
    record: dict[str, Any], *, state: Path, output: Path, now: datetime
) -> None:
    expected = {
        "format": "archive-robots-refresh-authorization-v1",
        "host": HOST,
        "url": ROBOTS_URL,
        "state": str(state.resolve()),
        "quarantine": str(output.resolve()),
        "owner_decision_reference": AUTHORITY,
        "maximum_http_requests": MAX_REQUESTS,
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise StepRightStopped("authorization differs from the exact Archive robots refresh")
    if not isinstance(record.get("operation_id"), str) or not record["operation_id"].strip():
        raise StepRightStopped("authorization lacks a stable operation id")
    try:
        approved = datetime.fromisoformat(record["approved_at"])
        starts = datetime.fromisoformat(record["execution_window"]["starts_at"])
        expires = datetime.fromisoformat(record["execution_window"]["expires_at"])
        valid = (
            approved.tzinfo is not None
            and starts.tzinfo is not None
            and expires.tzinfo is not None
            and approved <= starts <= now < expires <= starts + timedelta(hours=24)
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise StepRightStopped("authorization window is absent, stale or over 24 hours")
    resolved_state, resolved_output = state.resolve(), output.resolve()
    if (
        resolved_state == resolved_output
        or resolved_output.is_relative_to(resolved_state)
        or resolved_state.is_relative_to(resolved_output)
    ):
        raise StepRightStopped("quarantine must be separate from production state")
    if output.exists() or output.is_symlink():
        raise StepRightStopped("quarantine is single-use and must be new")


def validate_refresh_prestate(
    expected: Any, conn: sqlite3.Connection, state: Path, *, now: datetime
) -> None:
    if not isinstance(expected, dict):
        raise StepRightStopped("execution gate requires exact shared Archive prestate")
    try:
        observed = datetime.fromisoformat(expected["observed_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StepRightStopped("execution gate Archive observation time is malformed") from exc
    if observed.tzinfo is None or now.tzinfo is None:
        raise StepRightStopped("execution gate Archive times must include UTC offsets")
    current = now.astimezone(UTC)
    observed = observed.astimezone(UTC)
    age = (current - observed).total_seconds()
    usage = expected.get("usage")
    if (
        not isinstance(usage, dict)
        or age < 0
        or age > MAX_PRESTATE_AGE_SECONDS
        or usage.get("utc_day") != current.date().isoformat()
    ):
        raise StepRightStopped("execution gate Archive prestate is stale or on another UTC day")
    if archive_prestate(conn, state, observed_at=observed) != expected:
        raise StepRightStopped("shared Archive prestate differs from execution gate")
    host_state = expected.get("host_state")
    if not isinstance(host_state, dict) or not host_state.get("row_present"):
        raise StepRightStopped("Archive host row is absent")
    if host_state.get("pause_active_at_observation"):
        raise StepRightStopped("Archive host pause is active")
    if expected.get("spacing_state") == "legacy_unknown":
        raise StepRightStopped("Archive request spacing is unsafe or legacy-unknown")
    robots = expected.get("robots")
    if not isinstance(robots, dict) or not robots.get("body_sha256"):
        raise StepRightStopped("reviewed stale Archive robots cache is absent")
    robots_age = robots.get("age_seconds_at_observation")
    if (
        isinstance(robots_age, bool)
        or not isinstance(robots_age, int | float)
        or not math.isfinite(robots_age)
        or robots_age < 24 * 60 * 60
        or robots.get("fresh_at_observation") is not False
    ):
        raise StepRightStopped("Archive robots cache age is not a stale non-future value")


def validate_execution_gate(
    gate: dict[str, Any],
    *,
    conn: sqlite3.Connection,
    source: Path,
    state: Path,
    quarantine: Path,
    now: datetime,
    authorization_sha256: str,
    source_config_sha256: str,
    host_config_sha256: str,
    control_revision: int,
    runner_closure_sha256: str,
) -> None:
    expected = {
        "format": "archive-robots-refresh-execution-gate-v1",
        "source": str(source.resolve()),
        "schema_version": 29,
        "state": str(state.resolve()),
        "quarantine": str(quarantine.resolve()),
        "authorization_sha256": authorization_sha256,
        "ordinary_source_config_sha256": source_config_sha256,
        "host_config_sha256": host_config_sha256,
        "control_revision": control_revision,
        "source_name": SOURCE,
        "kind": KIND,
        "host": HOST,
        "url": ROBOTS_URL,
        "redirects": 0,
        "retries": 0,
        "maximum_http_requests": MAX_REQUESTS,
        "maximum_response_bytes": MAX_RESPONSE,
        "maximum_total_bytes": MAX_TOTAL,
        "maximum_elapsed_seconds": MAX_ELAPSED_SECONDS,
        "inactive_units": list(HELD_UNITS),
        "published_commit": PUBLISHED_BASELINE,
        "owner_decision_reference": AUTHORITY,
        "runner_closure_sha256": runner_closure_sha256,
    }
    if any(gate.get(key) != value for key, value in expected.items()):
        raise StepRightStopped("execution gate does not bind the exact Archive robots refresh")
    for key in (
        "source_receipt_sha256",
        "system",
        "published_candidate_id",
        "published_closure_digest",
        "publication_receipt_sha256",
        "baseline_path",
        "operator_hold_sha256",
    ):
        if not isinstance(gate.get(key), str) or not gate[key].strip():
            raise StepRightStopped(f"execution gate requires {key}")
    validate_refresh_prestate(gate.get("archive_prestate"), conn, state, now=now)


SOURCE_HELPERS = RUNNER_HELPERS


def verify_runner_closure(path: Path, *, source: Path, expected_sha256: str) -> str:
    if path.is_symlink():
        raise StepRightStopped("runner closure cannot be a symlink")
    body = path.read_bytes()
    if sha256(body) != expected_sha256:
        raise StepRightStopped("runner closure digest differs from execution gate")
    try:
        closure = json.loads(body)
        runner_sha = closure["runner_sha256"]
        shared_runner_sha = closure["shared_runner_sha256"]
        helpers = closure["source_helpers"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StepRightStopped("runner closure is malformed") from exc
    if (
        closure.get("format") != "archive-robots-refresh-runner-closure-v1"
        or not isinstance(runner_sha, str)
        or not isinstance(shared_runner_sha, str)
        or not isinstance(helpers, dict)
        or set(helpers) != SOURCE_HELPERS
    ):
        raise StepRightStopped("runner closure has an unknown member set")
    if sha256(Path(__file__).read_bytes()) != runner_sha:
        raise StepRightStopped("executing refresh runner differs from reviewed closure")
    if sha256(Path(shared_runner.__file__).read_bytes()) != shared_runner_sha:
        raise StepRightStopped("shared Step Right gate helper differs from reviewed closure")
    source = source.resolve(strict=True)
    for name, expected in helpers.items():
        relative = Path(name)
        member = source / relative
        if relative.is_absolute() or ".." in relative.parts or member.is_symlink():
            raise StepRightStopped("runner closure contains an unsafe helper path")
        if not isinstance(expected, str) or sha256(member.read_bytes()) != expected:
            raise StepRightStopped("reviewed runner helper differs: " + name)
    return sha256(body)


@contextlib.contextmanager
def accounting_connection(state: Path) -> Iterator[sqlite3.Connection]:
    """Open schema 29 with writes limited to Archive accounting and robots state."""
    if (state / "RESTORE_PENDING").exists():
        raise StepRightStopped("restore verification is pending")
    with (state / "state.lock").open("r+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StepRightStopped("production writer lock is held") from exc
        conn = sqlite3.connect(
            (state / "state.sqlite").resolve().as_uri() + "?mode=rw",
            uri=True,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 29:
                raise StepRightStopped("exact schema 29 is required")
            conn.execute(
                "CREATE TEMP TRIGGER archive_robots_hosts_insert_guard BEFORE INSERT ON hosts "
                "WHEN NEW.next_allowed_at IS NOT NULL OR NEW.paused_until IS NOT NULL "
                "OR NEW.pause_reason IS NOT NULL OR NEW.pause_streak != 0 "
                "OR NEW.robots_sha256 IS NOT NULL OR NEW.robots_fetched_at IS NOT NULL "
                "OR NEW.robots_status IS NOT NULL "
                "BEGIN SELECT RAISE(ABORT,'Archive robots host insert exceeds scope'); END"
            )
            writable = {"host_budget", "host_request_spacing"}
            host_columns = {
                "next_allowed_at",
                "paused_until",
                "pause_reason",
                "pause_streak",
                "robots_sha256",
                "robots_fetched_at",
                "robots_status",
            }
            schema_actions = {
                sqlite3.SQLITE_CREATE_INDEX,
                sqlite3.SQLITE_CREATE_TEMP_INDEX,
                sqlite3.SQLITE_CREATE_TEMP_TABLE,
                sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
                sqlite3.SQLITE_CREATE_TEMP_VIEW,
                sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_CREATE_TRIGGER,
                sqlite3.SQLITE_CREATE_VIEW,
                sqlite3.SQLITE_CREATE_VTABLE,
                sqlite3.SQLITE_DROP_INDEX,
                sqlite3.SQLITE_DROP_TEMP_INDEX,
                sqlite3.SQLITE_DROP_TEMP_TABLE,
                sqlite3.SQLITE_DROP_TEMP_TRIGGER,
                sqlite3.SQLITE_DROP_TEMP_VIEW,
                sqlite3.SQLITE_DROP_TABLE,
                sqlite3.SQLITE_DROP_TRIGGER,
                sqlite3.SQLITE_DROP_VIEW,
                sqlite3.SQLITE_DROP_VTABLE,
                sqlite3.SQLITE_ALTER_TABLE,
                sqlite3.SQLITE_ANALYZE,
                sqlite3.SQLITE_ATTACH,
                sqlite3.SQLITE_DETACH,
                sqlite3.SQLITE_REINDEX,
            }

            def authorize(action: int, table: str | None, column: str | None, *_: Any) -> int:
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                    allowed = table in writable or (
                        table == "hosts"
                        and (action == sqlite3.SQLITE_INSERT or column in host_columns)
                    )
                    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
                if action in schema_actions:
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_PRAGMA and (
                    table != "user_version" or column is not None
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(authorize)
            yield conn
        finally:
            conn.close()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _refresh_config(config: Config) -> Config:
    current = config.host(HOST)
    policy = replace(
        current,
        min_gap_seconds=max(10, current.min_gap_seconds),
        daily_request_budget=min(200, current.daily_request_budget),
        daily_byte_budget=min(MAX_TOTAL, current.daily_byte_budget)
        if current.daily_byte_budget is not None
        else MAX_TOTAL,
    )
    return Config(
        hosts={**config.hosts, HOST: policy},
        sources={**config.sources, SOURCE: SourceConfig(enabled=True)},
        history_start=config.history_start,
        scheduler=config.scheduler,
    )


class ArchiveRobotsRefresh:
    def __init__(
        self,
        conn: sqlite3.Connection,
        config: Config,
        clock: Clock,
        *,
        state: Path,
        output: Path,
        source_config: Path,
        host_config: Path,
        authorization: dict[str, Any],
        execution_gate: dict[str, Any],
        runner_closure_sha256: str,
        transport: httpx.BaseTransport | None = None,
        production_guard: Callable[[], None] | None = None,
    ) -> None:
        validate_authorization(authorization, state=state, output=output, now=clock.now())
        source_body, missing = check_ordinary_source_policy(source_config)
        source_hash, host_hash = sha256(source_body), sha256(host_config.read_bytes())
        revision = int(
            conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()[0]
        )
        validate_execution_gate(
            execution_gate,
            conn=conn,
            source=source_config.parent.parent,
            state=state,
            quarantine=output,
            now=clock.now(),
            authorization_sha256=sha256(canonical(authorization)),
            source_config_sha256=source_hash,
            host_config_sha256=host_hash,
            control_revision=revision,
            runner_closure_sha256=runner_closure_sha256,
        )
        if (
            db_module.SCHEMA_VERSION != 29
            or int(conn.execute("PRAGMA user_version").fetchone()[0]) != 29
        ):
            raise StepRightStopped("runtime and state must both use exact schema 29")
        if not (state / "operator-hold").is_file():
            raise StepRightStopped("operator hold is absent")
        if isinstance(clock, SystemClock) and production_guard is None:
            raise StepRightStopped("production system/unit guard is required")
        self.conn, self.clock, self.state, self.output = conn, clock, state, output
        self.source_config, self.host_config = source_config, host_config
        self.source_config_sha256, self.host_config_sha256 = source_hash, host_hash
        self.ordinary_source_missing = missing
        self.authorization, self.execution_gate = authorization, execution_gate
        self.runner_closure_sha256 = runner_closure_sha256
        self.transport, self.production_guard = transport, production_guard
        self.operation_config = _refresh_config(config)
        self.gate = Gate(conn, self.operation_config, clock)
        self.scope = ActionScope(frozenset({SOURCE}), frozenset({KIND}), HOST)
        expires = datetime.fromisoformat(authorization["execution_window"]["expires_at"])
        self.deadline = min(clock.now() + timedelta(seconds=MAX_ELAPSED_SECONDS), expires)
        self.debit_day = execution_gate["archive_prestate"]["usage"]["utc_day"]
        self.prior_robots = execution_gate["archive_prestate"]["robots"]
        self.receipt: dict[str, Any] = {
            "format": "archive-robots-refresh-receipt-v1",
            "operation_id": authorization["operation_id"],
            "host": HOST,
            "url": ROBOTS_URL,
            "runner_closure_sha256": runner_closure_sha256,
            "ordinary_source_config": "missing" if missing else "explicitly_enabled",
            "started_at": clock.now().isoformat(),
            "requests": [],
            "received_bytes": 0,
            "robots_update": None,
            "status": "running",
        }

    def _save(self) -> None:
        durable_write(self.output / "receipt.json", canonical(self.receipt) + b"\n")

    def _static_interlocks(self) -> None:
        if self.production_guard is not None:
            self.production_guard()
        if self.clock.now() >= self.deadline:
            raise StepRightStopped("elapsed-time or authorization window expired")
        if self.clock.now().astimezone(UTC).date().isoformat() != self.debit_day:
            raise StepRightStopped("UTC debit day rolled over after execution review")
        hold = self.state / "operator-hold"
        if (
            not hold.is_file()
            or sha256(hold.read_bytes()) != self.execution_gate["operator_hold_sha256"]
        ):
            raise StepRightStopped("operator hold differs from execution gate")
        if (self.state / "RESTORE_PENDING").exists():
            raise StepRightStopped("restore verification became pending")
        source_body, missing = check_ordinary_source_policy(self.source_config)
        if (
            sha256(source_body) != self.source_config_sha256
            or missing != self.ordinary_source_missing
        ):
            raise StepRightStopped("ordinary source configuration binding changed")
        if sha256(self.host_config.read_bytes()) != self.host_config_sha256:
            raise StepRightStopped("host configuration binding changed")
        if (
            db_module.SCHEMA_VERSION != 29
            or int(self.conn.execute("PRAGMA user_version").fetchone()[0]) != 29
        ):
            raise StepRightStopped("schema binding changed")
        revision = int(
            self.conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()[0]
        )
        if revision != self.execution_gate["control_revision"]:
            raise StepRightStopped("operator control revision changed after execution review")
        verify_live_publication(self.state, self.execution_gate)
        if matching_pauses(self.conn, self.scope, now=self.clock.now()):
            raise StepRightStopped("operator control pauses the Archive robots refresh")

    def _prior_robots_unchanged(self) -> None:
        row = self.conn.execute(
            "SELECT robots_sha256,robots_fetched_at,robots_status FROM hosts WHERE host=?", (HOST,)
        ).fetchone()
        actual = {
            "body_sha256": str(row[0]) if row is not None and row[0] else None,
            "fetched_at": str(row[1]) if row is not None and row[1] else None,
            "status": int(row[2]) if row is not None and row[2] is not None else None,
        }
        expected = {key: self.prior_robots[key] for key in actual}
        if actual != expected:
            raise StepRightStopped("reviewed Archive robots row changed before dispatch")
        try:
            body = Archive(self.state).read_body(str(actual["body_sha256"]))
        except (OSError, EOFError) as exc:
            raise StepRightStopped("reviewed Archive robots body became unavailable") from exc
        if sha256(body) != actual["body_sha256"]:
            raise StepRightStopped("reviewed Archive robots body changed before dispatch")

    def _admit(self) -> Grant:
        while True:
            self._static_interlocks()
            validate_refresh_prestate(
                self.execution_gate["archive_prestate"],
                self.conn,
                self.state,
                now=self.clock.now(),
            )
            result = self.gate.acquire(HOST, source=SOURCE)
            if isinstance(result, Grant):
                if result.debited_at is None:
                    self.gate.release(HOST, Classification(Outcome.INVALID))
                    raise StepRightStopped("Archive grant omitted its durable debit time")
                return result
            if isinstance(result, Paused):
                raise StepRightStopped("shared Archive gate: " + result.reason)
            self.clock.sleep(
                min(result.seconds, (self.deadline - self.clock.now()).total_seconds())
            )

    def _reserve(self) -> int:
        row = self.conn.execute(
            "SELECT bytes FROM host_budget WHERE host=? AND day=?", (HOST, self.debit_day)
        ).fetchone()
        used = int(row[0]) if row else 0
        shared = self.operation_config.host(HOST).daily_byte_budget
        assert shared is not None
        capacity = min(MAX_RESPONSE, MAX_TOTAL - int(self.receipt["received_bytes"]), shared - used)
        if capacity <= 0:
            raise StepRightStopped("shared or refresh byte budget exhausted")
        self.conn.execute(
            "UPDATE host_budget SET bytes=bytes+? WHERE host=? AND day=?",
            (capacity, HOST, self.debit_day),
        )
        self.conn.commit()
        return capacity

    def _exchange(self, client: httpx.Client) -> tuple[httpx.Response, bytes]:
        if len(self.receipt["requests"]) >= MAX_REQUESTS:
            raise StepRightStopped("one-request refresh ceiling")
        grant = self._admit()
        assert grant.debited_at is not None
        actual_day = grant.debited_at.astimezone(UTC).date().isoformat()
        request: dict[str, Any] = {
            "url": ROBOTS_URL,
            "request_day": actual_day,
            "admitted_at": grant.debited_at.isoformat(),
            "socket_dispatch_attempted": False,
            "dispatch_state": "not_attempted",
            "reserved_bytes": 0,
            "complete": False,
        }
        self.receipt["requests"].append(request)
        self._save()
        reserved, body = 0, bytearray()
        response_obj: httpx.Response | None = None
        dispatched = False
        try:
            if actual_day != self.debit_day:
                raise StepRightStopped("Archive request debit crossed the reviewed UTC day")
            reserved = self._reserve()
            request["reserved_bytes"] = reserved
            self._static_interlocks()
            self._prior_robots_unchanged()
            client.cookies.clear()
            timeout = min(30, (self.deadline - self.clock.now()).total_seconds())
            dispatched = True
            request["socket_dispatch_attempted"] = True
            request["dispatch_state"] = "dispatch_ambiguous_or_issued"
            request["dispatched_at"] = self.clock.now().isoformat()
            self._save()
            with client.stream("GET", ROBOTS_URL, timeout=timeout) as streamed:
                response_obj = streamed
                request["dispatch_state"] = "response_received"
                request["http_status"] = streamed.status_code
                request["headers"] = retained_headers(streamed.headers)
                request["final_url"] = str(streamed.url)
                for item in decoded_chunks(streamed, capacity=reserved):
                    if isinstance(item, bytes):
                        if self.clock.now() >= self.deadline:
                            raise StepRightStopped("deadline reached during response")
                        body.extend(item)
                        self.receipt["received_bytes"] += len(item)
                    else:
                        request["complete"] = item.complete
                if not request["complete"]:
                    raise StepRightStopped("response reached its byte cap or was incomplete")
            response = httpx.Response(
                response_obj.status_code,
                headers=request["headers"],
                content=bytes(body),
                request=httpx.Request("GET", ROBOTS_URL),
            )
            return response, bytes(body)
        except (httpx.HTTPError, OSError, ValueError, zlib.error, StepRightStopped) as exc:
            request["stop_reason"] = f"robots {ROBOTS_URL}: {exc}"
            raise StepRightStopped(request["stop_reason"]) from exc
        finally:
            request["body_bytes"] = len(body)
            request["body_sha256"] = sha256(bytes(body))
            request["completed_at"] = self.clock.now().isoformat()
            durable_write(self.output / "bodies" / request["body_sha256"], bytes(body))
            if response_obj is not None:
                classified = httpx.Response(
                    response_obj.status_code,
                    headers={
                        key: value
                        for key, value in retained_headers(response_obj.headers).items()
                        if key.lower() != "content-encoding"
                    },
                    content=bytes(body),
                    request=httpx.Request("GET", ROBOTS_URL),
                )
                outcome = classify(classified, ROBOTS_KIND, None, self.clock.now())
            else:
                outcome = Classification(Outcome.SERVER_ERROR if dispatched else Outcome.INVALID)
            try:
                adjustment = len(body) - reserved if request["complete"] else 0
                self.gate.release(HOST, outcome, body_bytes=adjustment, request_day=self.debit_day)
            finally:
                request["classification"] = outcome.outcome.value
                client.cookies.clear()
                self._save()

    def run(self) -> dict[str, Any]:
        self.output.mkdir(parents=True, exist_ok=False)
        durable_write(self.output / "authorization.json", canonical(self.authorization) + b"\n")
        durable_write(self.output / "execution-gate.json", canonical(self.execution_gate) + b"\n")
        self._save()
        try:
            with httpx.Client(
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            ) as client:
                client.cookies.jar.set_policy(_RejectCookies())
                response, body = self._exchange(client)
                if str(response.url) != ROBOTS_URL or response.is_redirect:
                    raise StepRightStopped("Archive robots redirect is outside the exact scope")
                if response.status_code not in {200, 404, 410}:
                    raise StepRightStopped(
                        f"Archive robots returned HTTP {response.status_code}; no retry"
                    )
                digest = Archive(self.state).store_body(body)
                fetched_at = self.clock.now().astimezone(UTC).isoformat()
                self.conn.execute(
                    "UPDATE hosts SET robots_sha256=?,robots_fetched_at=?,robots_status=? "
                    "WHERE host=?",
                    (digest, fetched_at, response.status_code, HOST),
                )
                self.conn.commit()
                self.receipt["robots_update"] = {
                    "status": response.status_code,
                    "body_sha256": digest,
                    "body_bytes": len(body),
                    "fetched_at": fetched_at,
                }
                self.receipt["status"] = "refreshed_pending_independent_review"
        except (StepRightStopped, httpx.HTTPError, OSError, ValueError) as exc:
            self.receipt["status"] = "stopped_incomplete"
            self.receipt["stop_reason"] = str(exc)
        finally:
            self.receipt["finished_at"] = self.clock.now().isoformat()
            self._save()
        return self.receipt


def preflight_report(*, gate_sha256: str, runner_closure_sha256: str) -> dict[str, Any]:
    return {
        "status": "preflight_checks_passed_not_operation_readiness",
        "requests": 0,
        "writes": 0,
        "executed": False,
        "ready": False,
        "gate_sha256": gate_sha256,
        "runner_closure_sha256": runner_closure_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, type=Path)
    parser.add_argument("--expected-gate-sha256", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--quarantine", required=True, type=Path)
    parser.add_argument("--runner-closure", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    gate_body, authorization_body = args.gate.read_bytes(), args.authorization.read_bytes()
    if sha256(gate_body) != args.expected_gate_sha256:
        raise StepRightStopped("execution gate digest differs from reviewed value")
    gate, authorization = json.loads(gate_body), json.loads(authorization_body)
    closure_sha = verify_runner_closure(
        args.runner_closure,
        source=args.source,
        expected_sha256=gate.get("runner_closure_sha256", ""),
    )
    verify_source(args.source, gate.get("source_receipt_sha256", ""))
    source_config = args.source / "config/sources.toml"
    host_config = args.source / "config/hosts.toml"
    check_ordinary_source_policy(source_config)
    with accounting_connection(args.state) as conn:
        now = SystemClock().now()
        revision = int(
            conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()[0]
        )
        validate_authorization(authorization, state=args.state, output=args.quarantine, now=now)
        validate_execution_gate(
            gate,
            conn=conn,
            source=args.source,
            state=args.state,
            quarantine=args.quarantine,
            now=now,
            authorization_sha256=sha256(canonical(authorization)),
            source_config_sha256=sha256(source_config.read_bytes()),
            host_config_sha256=sha256(host_config.read_bytes()),
            control_revision=revision,
            runner_closure_sha256=closure_sha,
        )
        scope = ActionScope(frozenset({SOURCE}), frozenset({KIND}), HOST)
        if matching_pauses(conn, scope, now=now):
            raise StepRightStopped("operator control pauses the Archive robots refresh")
        verify_live_publication(args.state, gate)
        production_runtime_guard(args.state, gate["system"])
        if not args.execute:
            print(
                json.dumps(
                    preflight_report(
                        gate_sha256=args.expected_gate_sha256,
                        runner_closure_sha256=closure_sha,
                    )
                )
            )
            return 0

        def live_guard() -> None:
            production_runtime_guard(args.state, gate["system"])
            verify_loaded_swingset_modules(args.source)
            verify_live_publication(args.state, gate)
            verify_runner_closure(
                args.runner_closure,
                source=args.source,
                expected_sha256=closure_sha,
            )

        runner = ArchiveRobotsRefresh(
            conn,
            load_config(args.source / "config"),
            SystemClock(),
            state=args.state,
            output=args.quarantine,
            source_config=source_config,
            host_config=host_config,
            authorization=authorization,
            execution_gate=gate,
            runner_closure_sha256=closure_sha,
            production_guard=live_guard,
        )
        result = runner.run()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "refreshed_pending_independent_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
