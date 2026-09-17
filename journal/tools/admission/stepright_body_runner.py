"""Acquire one exact Step Right archived event body into a sealed quarantine."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import http.cookiejar
import json
import sqlite3
import subprocess
import sys
import tomllib
import zlib
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, cast

import httpx
from protego import Protego

from swingset.clock import Clock, SystemClock
from swingset.config import Config, SourceConfig, load_config
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.classify import Classification, Outcome, classify
from swingset.fetch.client import USER_AGENT
from swingset.fetch.politeness import Gate, Grant, Paused
from swingset.sources.base import PageKind
from swingset.state import db as db_module
from swingset.state.controls import ActionScope, matching_pauses

HOST = "web.archive.org"
SOURCE = "steprightsolutions"
KIND = "steprightsolutions.event"
CAPTURE = "20150711035813"
ORIGINAL_URL = "http://steprightsolutions.com:80/events/asianopen2015"
REPLAY_URL = f"https://{HOST}/web/{CAPTURE}id_/{ORIGINAL_URL}"
ROBOTS_URL = f"https://{HOST}/robots.txt"
LOCATOR_AUDIT_SHA256 = "dd3cd1048fcad435491f67b48223d4eda60fd5c60892e69a13451db4525d2c97"
AUTHORITY = "journal/decisions/0087-authorize-remaining-v2-acquisition-and-operations.md"
PUBLISHED_BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
MAX_REQUESTS = 1  # the exact body; robots must already be fresh and retained
MAX_RESPONSE = 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024
MAX_ELAPSED_SECONDS = 15 * 60
CHUNK = 64 * 1024
HELD_UNITS = tuple(
    f"swingset-{name}.{suffix}"
    for name in ("cycle", "backup", "summary")
    for suffix in ("service", "timer")
)


class StepRightStopped(RuntimeError):
    """A sealed prerequisite or acquisition boundary failed."""


class _RejectCookies(http.cookiejar.DefaultCookiePolicy):
    def set_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False

    def return_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False


class _FixturePageKind:
    kind = "fixture_quarantine"

    @staticmethod
    def expected_statuses(_: Any) -> frozenset[int]:
        return frozenset()


HTTP_CONTROL = cast(PageKind, _FixturePageKind())


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def retained_headers(headers: httpx.Headers) -> dict[str, str]:
    """Retain response metadata without persisting cookie values."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"set-cookie", "set-cookie2", "cookie"}
    }


def check_ordinary_source_policy(path: Path) -> tuple[bytes, bool]:
    """Allow a missing entry, but preserve an explicit ordinary-source kill switch."""
    body = path.read_bytes()
    try:
        raw = tomllib.loads(body.decode("utf-8"))
        sources = raw.get("sources", {})
        if not isinstance(sources, dict):
            raise StepRightStopped("ordinary source table is malformed")
        entry = sources.get(SOURCE)
        if entry is None:
            return body, True
        if not isinstance(entry, dict) or not isinstance(entry.get("enabled"), bool):
            raise StepRightStopped("ordinary Step Right source enablement is malformed")
        if entry["enabled"] is False:
            raise StepRightStopped("ordinary Step Right source is explicitly disabled")
        return body, False
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise StepRightStopped("ordinary source configuration is unreadable") from exc


def validate_locator_audit(path: Path) -> dict[str, Any]:
    body = path.read_bytes()
    if sha256(body) != LOCATOR_AUDIT_SHA256:
        raise StepRightStopped("retained Step Right locator audit differs")
    audit = json.loads(body)
    scope = audit.get("bounded_next_body_scope", {})
    candidate = audit.get("candidate_event_page", {})
    if (
        audit.get("format") != "stepright-next-controls-retained-locator-audit-v1"
        or audit.get("network_requests") != 0
        or scope.get("only_archive_url") != REPLAY_URL
        or scope.get("maximum_archive_body_requests") != 1
        or scope.get("automatic_redirects_or_alternates") != 0
        or scope.get("child_round_requests") != 0
        or scope.get("origin_requests") != 0
        or candidate.get("archive_url") != REPLAY_URL
        or candidate.get("capture") != CAPTURE
        or candidate.get("original_url") != ORIGINAL_URL
    ):
        raise StepRightStopped("retained Step Right locator scope differs")
    return cast(dict[str, Any], audit)


def validate_authorization(
    record: dict[str, Any], *, state: Path, output: Path, now: datetime
) -> None:
    expected = {
        "source": SOURCE,
        "kind": KIND,
        "capture": CAPTURE,
        "replay_url": REPLAY_URL,
        "state": str(state.resolve()),
        "quarantine": str(output.resolve()),
        "owner_decision_reference": AUTHORITY,
        "maximum_archive_body_requests": 1,
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise StepRightStopped("authorization differs from the exact Step Right body scope")
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


def _archive_robots_policy(status: int, body: bytes) -> tuple[bool, float]:
    if status in (404, 410):
        return True, 0.0
    if status != 200:
        return False, 0.0
    try:
        rules = Protego.parse(body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise StepRightStopped("retained Archive robots body is not valid UTF-8") from exc
    return rules.can_fetch(REPLAY_URL, "swingset"), float(rules.crawl_delay("swingset") or 0)


def archive_prestate(
    conn: sqlite3.Connection, state: Path, *, observed_at: datetime
) -> dict[str, Any]:
    """Return the reviewable shared Archive state at one UTC observation time."""
    if observed_at.tzinfo is None:
        raise StepRightStopped("Archive prestate observation time must include a UTC offset")
    observed = observed_at.astimezone(UTC)
    day = observed.date().isoformat()
    budget = conn.execute(
        "SELECT requests,bytes FROM host_budget WHERE host=? AND day=?", (HOST, day)
    ).fetchone()
    all_budget: list[tuple[str, int, int]] = [
        (str(row[0]), int(row[1]), int(row[2]))
        for row in conn.execute(
            "SELECT day,requests,bytes FROM host_budget WHERE host=? ORDER BY day", (HOST,)
        )
    ]
    host = conn.execute(
        "SELECT next_allowed_at,paused_until,pause_reason,pause_streak,"
        "robots_sha256,robots_fetched_at,robots_status FROM hosts WHERE host=?",
        (HOST,),
    ).fetchone()
    spacing = conn.execute(
        "SELECT reservation_id,gap_seconds,reserved_at,released_at "
        "FROM host_request_spacing WHERE host=?",
        (HOST,),
    ).fetchone()
    spacing_record = (
        {
            "reservation_id": str(spacing[0]),
            "gap_seconds": float(spacing[1]) if spacing[1] is not None else None,
            "reserved_at": str(spacing[2]) if spacing[2] is not None else None,
            "released_at": str(spacing[3]) if spacing[3] is not None else None,
        }
        if spacing is not None
        else None
    )
    historical_debits = sum(row[1] for row in all_budget)
    if spacing_record is None:
        spacing_state = "legacy_unknown" if historical_debits else "none"
    elif spacing_record["gap_seconds"] is None:
        spacing_state = "legacy_unknown"
    elif spacing_record["released_at"] is None:
        spacing_state = "unreleased_reservation_recovery_required"
    else:
        spacing_state = "released_reservation_recovery_required"

    def active_deadline(value: Any, label: str) -> bool:
        if value is None:
            return False
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise StepRightStopped(f"retained Archive {label} is malformed") from exc
        if parsed.tzinfo is None:
            raise StepRightStopped(f"retained Archive {label} lacks a UTC offset")
        return parsed.astimezone(UTC) > observed

    robots: dict[str, Any] = {
        "body_sha256": None,
        "fetched_at": None,
        "status": None,
        "age_seconds_at_observation": None,
        "fresh_at_observation": False,
        "policy_allows_replay": False,
        "crawl_delay_seconds": 0.0,
    }
    if host is not None and host[4] and host[5] and host[6] is not None:
        digest, fetched_raw, status = str(host[4]), str(host[5]), int(host[6])
        try:
            fetched = datetime.fromisoformat(fetched_raw)
        except ValueError as exc:
            raise StepRightStopped("retained Archive robots timestamp is malformed") from exc
        if fetched.tzinfo is None:
            raise StepRightStopped("retained Archive robots timestamp lacks a UTC offset")
        try:
            body = Archive(state).read_body(digest)
        except (OSError, EOFError) as exc:
            raise StepRightStopped("retained Archive robots body is unavailable") from exc
        if sha256(body) != digest or len(body) > MAX_RESPONSE:
            raise StepRightStopped("retained Archive robots body differs from its digest")
        allows, crawl_delay = _archive_robots_policy(status, body)
        age = (observed - fetched.astimezone(UTC)).total_seconds()
        robots = {
            "body_sha256": digest,
            "fetched_at": fetched_raw,
            "status": status,
            "age_seconds_at_observation": age,
            "fresh_at_observation": 0 <= age < 24 * 60 * 60,
            "policy_allows_replay": allows,
            "crawl_delay_seconds": crawl_delay,
        }

    return {
        "format": "stepright-archive-prestate-v1",
        "host": HOST,
        "observed_at": observed.isoformat(),
        "usage": {
            "utc_day": day,
            "utc_day_row_present": budget is not None,
            "utc_day_request_debits": int(budget[0]) if budget is not None else 0,
            "utc_day_bytes": int(budget[1]) if budget is not None else 0,
            "all_time_budget_rows": len(all_budget),
            "all_time_request_debits": historical_debits,
            "all_time_bytes": sum(row[2] for row in all_budget),
            "all_budget_rows_sha256": sha256(canonical(all_budget)),
        },
        "host_state": {
            "row_present": host is not None,
            "next_allowed_at": str(host[0]) if host is not None and host[0] else None,
            "next_allowed_active_at_observation": active_deadline(
                host[0] if host is not None else None, "next-allowed timestamp"
            ),
            "paused_until": str(host[1]) if host is not None and host[1] else None,
            "pause_active_at_observation": active_deadline(
                host[1] if host is not None else None, "pause timestamp"
            ),
            "pause_reason": str(host[2]) if host is not None and host[2] else None,
            "pause_streak": int(host[3]) if host is not None else 0,
        },
        "spacing": spacing_record,
        "spacing_state": spacing_state,
        "reservation_recovery_required": spacing_state.endswith("recovery_required"),
        "robots": robots,
    }


def validate_archive_prestate(
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
    usage = expected.get("usage")
    if (
        not isinstance(usage, dict)
        or observed > current
        or usage.get("utc_day") != current.date().isoformat()
    ):
        raise StepRightStopped("execution gate Archive UTC debit day is not current")
    actual = archive_prestate(conn, state, observed_at=observed)
    if actual != expected:
        raise StepRightStopped("shared Archive prestate differs from execution gate")
    robots = actual["robots"]
    fetched_raw = robots["fetched_at"]
    if fetched_raw is None:
        raise StepRightStopped("execution gate requires retained Archive robots prestate")
    fetched = datetime.fromisoformat(fetched_raw)
    current_age = (current - fetched.astimezone(UTC)).total_seconds()
    if (
        not robots["fresh_at_observation"]
        or not robots["policy_allows_replay"]
        or current_age < 0
        or current_age >= 24 * 60 * 60
    ):
        raise StepRightStopped("execution gate Archive robots prestate is not fresh and allowing")


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
        "format": "stepright-exact-body-execution-gate-v1",
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
        "capture": CAPTURE,
        "replay_url": REPLAY_URL,
        "body_redirects": 0,
        "body_requests": 1,
        "robots_redirects": 0,
        "maximum_http_requests": MAX_REQUESTS,
        "maximum_response_bytes": MAX_RESPONSE,
        "maximum_total_bytes": MAX_TOTAL,
        "maximum_elapsed_seconds": MAX_ELAPSED_SECONDS,
        "origin_requests": 0,
        "production_facts_or_watches_created": 0,
        "inactive_units": list(HELD_UNITS),
        "locator_audit_sha256": LOCATOR_AUDIT_SHA256,
        "published_commit": PUBLISHED_BASELINE,
        "runner_closure_sha256": runner_closure_sha256,
    }
    if any(gate.get(key) != value for key, value in expected.items()):
        raise StepRightStopped("execution gate does not bind the exact Step Right operation")
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
    validate_archive_prestate(gate.get("archive_prestate"), conn, state, now=now)


def verify_loaded_swingset_modules(source: Path, modules: dict[str, Any] | None = None) -> None:
    """Require every already-loaded application module to come from the reviewed source."""
    source = source.resolve(strict=True)
    selected = modules if modules is not None else sys.modules
    for name, module in selected.items():
        if name != "swingset" and not name.startswith("swingset."):
            continue
        origin = getattr(module, "__file__", None)
        if origin is not None and not Path(origin).resolve().is_relative_to(source):
            raise StepRightStopped("loaded application module is outside reviewed source: " + name)


RUNNER_HELPERS = frozenset(
    {
        "src/swingset/clock.py",
        "src/swingset/config.py",
        "src/swingset/fetch/archive.py",
        "src/swingset/fetch/classify.py",
        "src/swingset/fetch/client.py",
        "src/swingset/fetch/politeness.py",
        "src/swingset/sources/base.py",
        "src/swingset/state/controls.py",
        "src/swingset/state/db.py",
    }
)


def verify_runner_closure(path: Path, *, source: Path, expected_sha256: str) -> str:
    """Verify the executing runner and the exact reviewed source-helper closure."""
    if path.is_symlink():
        raise StepRightStopped("runner closure cannot be a symlink")
    body = path.read_bytes()
    if sha256(body) != expected_sha256:
        raise StepRightStopped("runner closure digest differs from execution gate")
    try:
        closure = json.loads(body)
        runner_sha = closure["runner_sha256"]
        helpers = closure["source_helpers"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StepRightStopped("runner closure is malformed") from exc
    if (
        closure.get("format") != "stepright-exact-body-runner-closure-v1"
        or not isinstance(runner_sha, str)
        or not isinstance(helpers, dict)
        or set(helpers) != RUNNER_HELPERS
    ):
        raise StepRightStopped("runner closure has an unknown member set")
    if sha256(Path(__file__).read_bytes()) != runner_sha:
        raise StepRightStopped("executing runner differs from reviewed closure")
    source = source.resolve(strict=True)
    for name, expected in helpers.items():
        relative = Path(name)
        member = source / relative
        if relative.is_absolute() or ".." in relative.parts or member.is_symlink():
            raise StepRightStopped("runner closure contains an unsafe helper path")
        if not isinstance(expected, str) or sha256(member.read_bytes()) != expected:
            raise StepRightStopped("reviewed runner helper differs: " + name)
    return sha256(body)


def verify_source(source: Path, receipt_sha256: str) -> None:
    source = source.resolve(strict=True)
    receipt_path = source / "extension-source.json"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise StepRightStopped("runtime source receipt must be a regular file")
    body = receipt_path.read_bytes()
    if sha256(body) != receipt_sha256:
        raise StepRightStopped("runtime source receipt differs from execution gate")
    try:
        inventory = json.loads(body).get("files")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise StepRightStopped("runtime source receipt is malformed") from exc
    if not isinstance(inventory, dict) or not inventory:
        raise StepRightStopped("runtime source inventory is absent")
    actual = {
        item.relative_to(source).as_posix()
        for item in source.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts
    }
    actual.discard("extension-source.json")
    if actual != set(inventory):
        raise StepRightStopped("runtime source inventory differs")
    for name, value in inventory.items():
        relative = Path(name)
        path = source / relative
        expected = value.get("sha256") if isinstance(value, dict) else value
        if relative.is_absolute() or ".." in relative.parts or path.is_symlink():
            raise StepRightStopped("runtime source inventory contains an unsafe path")
        if not isinstance(expected, str) or sha256(path.read_bytes()) != expected:
            raise StepRightStopped("runtime source file differs: " + name)
    verify_loaded_swingset_modules(source)


def verify_live_publication(state: Path, gate: dict[str, Any]) -> None:
    """Bind the live baseline symlink and its retained publication receipt."""
    baseline = state / "baseline"
    if not baseline.is_symlink():
        raise StepRightStopped("live baseline is not a symlink")
    resolved = baseline.resolve(strict=True)
    if str(resolved) != gate["baseline_path"] or resolved.name != gate["published_candidate_id"]:
        raise StepRightStopped("live baseline path differs from execution gate")
    receipt_path = resolved / "PUBLISHED"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise StepRightStopped("publication receipt cannot be a symlink")
    body = receipt_path.read_bytes()
    if sha256(body) != gate["publication_receipt_sha256"]:
        raise StepRightStopped("publication receipt differs from execution gate")
    try:
        receipt = json.loads(body)
    except json.JSONDecodeError as exc:
        raise StepRightStopped("publication receipt is malformed") from exc
    if (
        receipt.get("commit") != PUBLISHED_BASELINE
        or receipt.get("candidate_id") != gate["published_candidate_id"]
        or receipt.get("closure_digest") != gate["published_closure_digest"]
    ):
        raise StepRightStopped("live publication identity differs from execution gate")


def verify_live_system(
    *,
    active_system: str,
    persistent_system: str,
    expected_system: str,
    unit_state: Callable[[str], str],
) -> None:
    if active_system != expected_system or persistent_system != expected_system:
        raise StepRightStopped("active or persistent system differs from execution gate")
    for unit in HELD_UNITS:
        if unit_state(unit) != "inactive":
            raise StepRightStopped("ordinary unit is not inactive: " + unit)


def production_runtime_guard(state: Path, expected_system: str) -> None:
    if not (state / "operator-hold").is_file():
        raise StepRightStopped("operator hold is absent")
    resolved: list[str] = []
    for link in (Path("/run/current-system"), Path("/nix/var/nix/profiles/system")):
        try:
            resolved.append(str(link.resolve(strict=True)))
        except OSError as exc:
            raise StepRightStopped("cannot resolve active or persistent system") from exc

    def unit_state(unit: str) -> str:
        result = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True, check=False
        )
        return result.stdout.strip()

    verify_live_system(
        active_system=resolved[0],
        persistent_system=resolved[1],
        expected_system=expected_system,
        unit_state=unit_state,
    )


@contextlib.contextmanager
def accounting_connection(state: Path) -> Iterator[sqlite3.Connection]:
    """Open schema 29 with writes restricted to controls and host accounting."""
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
                "CREATE TEMP TRIGGER stepright_hosts_insert_guard BEFORE INSERT ON hosts "
                "WHEN NEW.next_allowed_at IS NOT NULL OR NEW.paused_until IS NOT NULL "
                "OR NEW.pause_reason IS NOT NULL OR NEW.pause_streak != 0 "
                "OR NEW.robots_sha256 IS NOT NULL OR NEW.robots_fetched_at IS NOT NULL "
                "OR NEW.robots_status IS NOT NULL "
                "BEGIN SELECT RAISE(ABORT,'Step Right host insert exceeds accounting scope'); END"
            )
            writable = {"host_budget", "host_request_spacing"}

            def authorize(action: int, table: str | None, column: str | None, *_: Any) -> int:
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                    allowed = table in writable or (
                        table == "hosts"
                        and (
                            action == sqlite3.SQLITE_INSERT
                            or column
                            in {"next_allowed_at", "paused_until", "pause_reason", "pause_streak"}
                        )
                    )
                    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
                if action in {
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
                }:
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


class _Complete:
    def __init__(self, complete: bool) -> None:
        self.complete = complete


def decoded_chunks(response: httpx.Response, *, capacity: int) -> Iterator[bytes | _Complete]:
    """Stream to a decoded cap, then consume at most one decoded lookahead byte."""
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip"}:
        raise StepRightStopped("unexpected content encoding")
    raw_chunks = iter(response.iter_raw())
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    used, pending, exhausted = 0, b"", False
    while used < capacity:
        if not pending:
            try:
                pending = next(raw_chunks)
            except StopIteration:
                exhausted = True
                break
        room = min(CHUNK, capacity - used)
        if decoder is None:
            output, pending = pending[:room], pending[room:]
        else:
            output = decoder.decompress(pending, room)
            pending = decoder.unconsumed_tail
            if decoder.unused_data:
                raise StepRightStopped("gzip response has trailing or concatenated data")
        if output:
            used += len(output)
            yield output
    if used < capacity:
        if decoder is not None:
            if not exhausted:
                raise AssertionError("short gzip read ended without exhausting input")
            if not decoder.eof:
                raise StepRightStopped("gzip stream ended before its trailer")
            if decoder.unused_data:
                raise StepRightStopped("gzip response has trailing or concatenated data")
        yield _Complete(True)
        return

    # At the exact decoded cap, one decoded-byte lookahead distinguishes EOF
    # from overflow. Compressed framing may require more raw input to produce
    # that byte or reach the trailer, but no extra decoded byte is retained.
    if decoder is None:
        if pending:
            yield _Complete(False)
            return
        for raw in raw_chunks:
            if raw:
                yield _Complete(False)
                return
        yield _Complete(True)
        return

    while not decoder.eof:
        if not pending:
            try:
                pending = next(raw_chunks)
            except StopIteration as exc:
                raise StepRightStopped("gzip stream ended before its trailer") from exc
        lookahead = decoder.decompress(pending, 1)
        pending = decoder.unconsumed_tail
        if decoder.unused_data:
            raise StepRightStopped("gzip response has trailing or concatenated data")
        if lookahead:
            yield _Complete(False)
            return
    if decoder.unused_data or pending:
        raise StepRightStopped("gzip response has trailing or concatenated data")
    for raw in raw_chunks:
        if raw:
            raise StepRightStopped("gzip response has trailing or concatenated data")
    yield _Complete(True)


def capture_time(headers: dict[str, str]) -> datetime:
    value = next(
        (value for key, value in headers.items() if key.lower() == "memento-datetime"), None
    )
    if value is None:
        raise StepRightStopped("exact body lacks Memento-Datetime")
    try:
        parsed = parsedate_to_datetime(value)
        expected = datetime.strptime(CAPTURE, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except (TypeError, ValueError) as exc:
        raise StepRightStopped("exact body has malformed Memento-Datetime") from exc
    if parsed.tzinfo is None or parsed.astimezone(UTC) != expected:
        raise StepRightStopped("Memento-Datetime differs from the authorized capture")
    return expected


class StepRightBodyRunner:
    """Run the one-body quarantine operation through shared host and control gates."""

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
        current = config.host(HOST)
        archive_policy = replace(
            current,
            min_gap_seconds=max(10, current.min_gap_seconds),
            daily_request_budget=min(200, current.daily_request_budget),
            daily_byte_budget=min(MAX_TOTAL, current.daily_byte_budget)
            if current.daily_byte_budget is not None
            else MAX_TOTAL,
        )
        self.operation_config = Config(
            hosts={**config.hosts, HOST: archive_policy},
            sources={**config.sources, SOURCE: SourceConfig(enabled=True)},
            history_start=config.history_start,
            scheduler=config.scheduler,
        )
        self.conn, self.clock, self.state, self.output = conn, clock, state, output
        self.source_config, self.host_config = source_config, host_config
        self.source_config_sha256, self.host_config_sha256 = source_hash, host_hash
        self.ordinary_source_missing = missing
        self.authorization, self.execution_gate = authorization, execution_gate
        self.runner_closure_sha256 = runner_closure_sha256
        self.transport, self.production_guard = transport, production_guard
        self.gate = Gate(conn, self.operation_config, clock)
        self.scope = ActionScope(frozenset({SOURCE}), frozenset({KIND}), HOST)
        expires = datetime.fromisoformat(authorization["execution_window"]["expires_at"])
        self.deadline = min(clock.now() + timedelta(seconds=MAX_ELAPSED_SECONDS), expires)
        self.run_id = authorization["operation_id"]
        self.rules: Protego | None = None
        self.robots_ready = False
        self.robots_binding: dict[str, Any] | None = None
        self.crawl_delay = 0.0
        self.receipt: dict[str, Any] = {
            "format": "stepright-exact-body-receipt-v1",
            "operation_id": self.run_id,
            "source": SOURCE,
            "kind": KIND,
            "capture": CAPTURE,
            "requested_url": REPLAY_URL,
            "runner_closure_sha256": runner_closure_sha256,
            "ordinary_source_config": "missing" if missing else "explicitly_enabled",
            "started_at": clock.now().isoformat(),
            "requests": [],
            "targets": [],
            "received_bytes": 0,
            "production_facts_or_watches_created": 0,
            "status": "running",
        }

    def _save(self) -> None:
        durable_write(self.output / "receipt.json", canonical(self.receipt) + b"\n")

    def _interlocks(self) -> None:
        if self.production_guard is not None:
            self.production_guard()
        if self.clock.now() >= self.deadline:
            raise StepRightStopped("elapsed-time or authorization window expired")
        if not (self.state / "operator-hold").is_file():
            raise StepRightStopped("operator hold was removed")
        if (
            sha256((self.state / "operator-hold").read_bytes())
            != self.execution_gate["operator_hold_sha256"]
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
            raise StepRightStopped("operator control pauses the Step Right fixture")

    def _admit(self) -> Grant:
        if len(self.receipt["requests"]) >= MAX_REQUESTS:
            raise StepRightStopped("one-request operation ceiling")
        while True:
            self._interlocks()
            result = self.gate.acquire(HOST, source=SOURCE, crawl_delay=self.crawl_delay)
            if not isinstance(result, Grant):
                if isinstance(result, Paused):
                    raise StepRightStopped("shared Archive gate: " + result.reason)
                self.clock.sleep(
                    min(result.seconds, (self.deadline - self.clock.now()).total_seconds())
                )
                continue
            if result.debited_at is None:
                self.gate.release(HOST, Classification(Outcome.INVALID))
                raise StepRightStopped("Archive grant omitted its durable debit time")
            return result

    def _reserve(self, day: str) -> int:
        row = self.conn.execute(
            "SELECT bytes FROM host_budget WHERE host=? AND day=?", (HOST, day)
        ).fetchone()
        used = int(row[0]) if row else 0
        shared = self.operation_config.host(HOST).daily_byte_budget
        assert shared is not None
        received = int(self.receipt["received_bytes"])
        capacity = min(MAX_RESPONSE, MAX_TOTAL - received, shared - used)
        capacity -= capacity % CHUNK
        if capacity <= 0:
            raise StepRightStopped("shared or fixture byte budget exhausted")
        self.conn.execute(
            "UPDATE host_budget SET bytes=bytes+? WHERE host=? AND day=?", (capacity, HOST, day)
        )
        self.conn.commit()
        return capacity

    def _exchange(
        self, client: httpx.Client, url: str, purpose: str
    ) -> tuple[httpx.Response, bytes, dict[str, Any]]:
        grant = self._admit()
        assert grant.debited_at is not None
        day = grant.debited_at.astimezone(UTC).date().isoformat()
        request: dict[str, Any] = {
            "url": url,
            "purpose": purpose,
            "request_day": day,
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
            reserved = self._reserve(day)
            request["reserved_bytes"] = reserved
            self._interlocks()
            client.cookies.clear()
            timeout = min(30, (self.deadline - self.clock.now()).total_seconds())
            request["robots_revalidated"] = self._revalidate_robots()
            dispatched = True
            request["socket_dispatch_attempted"] = True
            request["dispatch_state"] = "dispatch_ambiguous_or_issued"
            request["dispatched_at"] = self.clock.now().isoformat()
            # This durable marker precedes the transport call. Process death
            # can therefore cause over-accounting, never a silent retry.
            self._save()
            with client.stream("GET", url, timeout=timeout) as streamed:
                response_obj = streamed
                request["dispatch_state"] = "response_received"
                request["http_status"] = streamed.status_code
                request["headers"] = retained_headers(streamed.headers)
                request["final_url"] = str(streamed.url)
                for item in decoded_chunks(streamed, capacity=reserved):
                    if isinstance(item, _Complete):
                        request["complete"] = item.complete
                    else:
                        if self.clock.now() >= self.deadline:
                            raise StepRightStopped("deadline reached during response")
                        body.extend(item)
                        self.receipt["received_bytes"] += len(item)
                if not request["complete"]:
                    raise StepRightStopped("response reached its byte cap or was incomplete")
            response = httpx.Response(
                response_obj.status_code,
                headers={
                    key: value
                    for key, value in request["headers"].items()
                    if key.lower() != "content-encoding"
                },
                content=bytes(body),
                request=httpx.Request("GET", url),
            )
            return response, bytes(body), request
        except (httpx.HTTPError, OSError, ValueError, zlib.error, StepRightStopped) as exc:
            request["stop_reason"] = f"{purpose} {url}: {exc}"
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
                    request=httpx.Request("GET", url),
                )
                outcome = classify(classified, HTTP_CONTROL, None, self.clock.now())
            else:
                outcome = Classification(Outcome.SERVER_ERROR if dispatched else Outcome.INVALID)
            try:
                adjustment = len(body) - reserved if request["complete"] else 0
                self.gate.release(HOST, outcome, body_bytes=adjustment, request_day=day)
            finally:
                request["classification"] = outcome.outcome.value
                client.cookies.clear()
                self._save()

    def _request(
        self, client: httpx.Client, url: str, purpose: str
    ) -> tuple[httpx.Response, bytes]:
        if purpose != "body" or url != REPLAY_URL:
            raise StepRightStopped("request URL is outside the exact allowlist")
        self._robots()
        response, body, _ = self._exchange(client, url, purpose)
        outcome = classify(response, HTTP_CONTROL, None, self.clock.now()).outcome
        if response.is_redirect:
            raise StepRightStopped("body redirect is outside the exact fixture scope")
        if outcome != Outcome.OK:
            raise StepRightStopped(f"HTTP {response.status_code}: {outcome.value}; no retry")
        return response, body

    def _robots(self) -> None:
        if self.robots_ready:
            return
        row = self.conn.execute(
            "SELECT robots_sha256,robots_fetched_at,robots_status FROM hosts WHERE host=?", (HOST,)
        ).fetchone()
        if not row or not row[0] or not row[1] or row[2] is None:
            raise StepRightStopped("fresh retained Archive robots cache is required")
        try:
            fetched = datetime.fromisoformat(str(row[1]))
        except ValueError as exc:
            raise StepRightStopped("retained Archive robots timestamp is malformed") from exc
        now = self.clock.now().astimezone(UTC)
        if fetched.tzinfo is None or fetched > now:
            raise StepRightStopped("retained Archive robots timestamp is unverifiable")
        if fetched + timedelta(hours=24) <= now:
            raise StepRightStopped("retained Archive robots cache is stale")
        try:
            body = Archive(self.state).read_body(str(row[0]))
        except (OSError, EOFError) as exc:
            raise StepRightStopped("retained Archive robots body is unavailable") from exc
        status = int(row[2])
        if sha256(body) != str(row[0]) or len(body) > MAX_RESPONSE:
            raise StepRightStopped("fresh robots cache body differs")
        durable_write(self.output / "bodies" / sha256(body), body)
        self.receipt["robots_proof"] = {
            "source": "retained_host_cache",
            "requested_url": ROBOTS_URL,
            "status": status,
            "body_sha256": sha256(body),
            "fetched_at": fetched.isoformat(),
            "cache_age_seconds": (now - fetched.astimezone(UTC)).total_seconds(),
        }
        if status == 200:
            try:
                self.rules = Protego.parse(body.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise StepRightStopped("robots body is not valid UTF-8") from exc
            self.crawl_delay = float(self.rules.crawl_delay("swingset") or 0)
            if self.clock.now() + timedelta(seconds=self.crawl_delay) >= self.deadline:
                raise StepRightStopped("robots crawl delay exceeds the operation window")
            if self.crawl_delay:
                self.clock.sleep(self.crawl_delay)
        elif status not in (404, 410):
            raise StepRightStopped("robots policy unavailable; fail closed")
        self.robots_binding = {
            "body_sha256": str(row[0]),
            "fetched_at": str(row[1]),
            "status": status,
            "crawl_delay": self.crawl_delay,
        }
        self.robots_ready = True
        if self.rules and not self.rules.can_fetch(REPLAY_URL, "swingset"):
            raise StepRightStopped("robots disallows the exact replay URL")
        self._save()

    def _revalidate_robots(self) -> dict[str, Any]:
        """Recheck the exact retained row and policy immediately before body I/O."""
        if not self.robots_ready or self.robots_binding is None:
            raise StepRightStopped("Archive robots policy was not bound before dispatch")
        row = self.conn.execute(
            "SELECT robots_sha256,robots_fetched_at,robots_status FROM hosts WHERE host=?", (HOST,)
        ).fetchone()
        if row is None or {
            "body_sha256": str(row[0]),
            "fetched_at": str(row[1]),
            "status": int(row[2]) if row[2] is not None else None,
        } != {key: self.robots_binding[key] for key in ("body_sha256", "fetched_at", "status")}:
            raise StepRightStopped("retained Archive robots binding changed before dispatch")
        try:
            fetched = datetime.fromisoformat(str(row[1]))
        except ValueError as exc:
            raise StepRightStopped("retained Archive robots timestamp became malformed") from exc
        now = self.clock.now().astimezone(UTC)
        if fetched.tzinfo is None or fetched > now:
            raise StepRightStopped("retained Archive robots timestamp became unverifiable")
        if fetched + timedelta(hours=24) <= now:
            raise StepRightStopped("retained Archive robots cache became stale before dispatch")
        try:
            body = Archive(self.state).read_body(str(row[0]))
        except (OSError, EOFError) as exc:
            raise StepRightStopped(
                "retained Archive robots body became unavailable before dispatch"
            ) from exc
        if sha256(body) != self.robots_binding["body_sha256"] or len(body) > MAX_RESPONSE:
            raise StepRightStopped("retained Archive robots body changed before dispatch")
        status = int(row[2])
        if status == 200:
            try:
                rules = Protego.parse(body.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise StepRightStopped(
                    "retained Archive robots policy became unreadable before dispatch"
                ) from exc
            crawl_delay = float(rules.crawl_delay("swingset") or 0)
            if crawl_delay != self.robots_binding["crawl_delay"] or not rules.can_fetch(
                REPLAY_URL, "swingset"
            ):
                raise StepRightStopped("retained Archive robots policy changed before dispatch")
        elif status not in (404, 410):
            raise StepRightStopped("retained Archive robots status changed before dispatch")
        return {
            "revalidated_at": now.isoformat(),
            "body_sha256": str(row[0]),
            "fetched_at": fetched.isoformat(),
            "status": status,
            "cache_age_seconds": (now - fetched.astimezone(UTC)).total_seconds(),
            "policy_allows_replay": True,
        }

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
                response, body = self._request(client, REPLAY_URL, "body")
                if str(response.url) != REPLAY_URL or response.status_code != 200:
                    raise StepRightStopped("exact body did not return directly with HTTP 200")
                request = self.receipt["requests"][-1]
                retained = cast(dict[str, str], request["headers"])
                captured = capture_time(retained)
                if not body:
                    raise StepRightStopped("exact body is empty")
                target = {
                    "requested_url": REPLAY_URL,
                    "final_url": str(response.url),
                    "original_url": ORIGINAL_URL,
                    "http_status": response.status_code,
                    "headers": retained,
                    "body_sha256": sha256(body),
                    "body_bytes": len(body),
                    "memento_datetime": captured.isoformat(),
                    "archive_receipt_metadata": {
                        key: value
                        for key, value in retained.items()
                        if key.lower().startswith("x-archive-")
                        or key.lower() in {"link", "memento-datetime"}
                    },
                    "request_day": request["request_day"],
                    "interpretation": "unreviewed_quarantine_body",
                }
                durable_write(self.output / "bodies" / target["body_sha256"], body)
                self.receipt["targets"].append(target)
                self.receipt["status"] = "captured_pending_independent_review"
        except (StepRightStopped, httpx.HTTPError, OSError, ValueError) as exc:
            self.receipt["status"] = "stopped_incomplete"
            self.receipt["stop_reason"] = str(exc)
        finally:
            self.receipt["finished_at"] = self.clock.now().isoformat()
            self._save()
        return self.receipt


def preflight_report(*, gate_sha256: str, runner_closure_sha256: str) -> dict[str, Any]:
    """Describe completed checks without turning them into operation readiness."""
    return {
        "status": "preflight_checks_passed_not_operation_readiness",
        "requests": 0,
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
    parser.add_argument("--locator-audit", required=True, type=Path)
    parser.add_argument("--runner-closure", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    gate_body, authorization_body = args.gate.read_bytes(), args.authorization.read_bytes()
    if sha256(gate_body) != args.expected_gate_sha256:
        raise StepRightStopped("execution gate digest differs from reviewed value")
    gate, authorization = json.loads(gate_body), json.loads(authorization_body)
    validate_locator_audit(args.locator_audit)
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
        revision = int(
            conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()[0]
        )
        validate_authorization(
            authorization, state=args.state, output=args.quarantine, now=SystemClock().now()
        )
        validate_execution_gate(
            gate,
            conn=conn,
            source=args.source,
            state=args.state,
            quarantine=args.quarantine,
            now=SystemClock().now(),
            authorization_sha256=sha256(canonical(authorization)),
            source_config_sha256=sha256(source_config.read_bytes()),
            host_config_sha256=sha256(host_config.read_bytes()),
            control_revision=revision,
            runner_closure_sha256=closure_sha,
        )
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

        runner = StepRightBodyRunner(
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
    return 0 if result["status"] == "captured_pending_independent_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
