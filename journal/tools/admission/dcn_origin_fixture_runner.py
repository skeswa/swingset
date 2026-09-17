"""One-event DCN origin PDF fixture capture; no watches or interpretations."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import http.cookiejar
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tomllib
import zlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from protego import Protego

from swingset.clock import Clock, SystemClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.classify import Outcome, classify
from swingset.fetch.client import USER_AGENT
from swingset.fetch.politeness import Gate, Grant, Paused, Wait
from swingset.state import db as db_module
from swingset.state.controls import (
    ActionScope,
    ControlPaused,
    admission,
    matching_pauses,
    operation,
    settle,
)
from swingset.state.db import Database

HOST = "danceconvention.net"
EVENT_ID = "dcn:1546230"
MAX_RESPONSE = 2 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_REQUESTS = 4
CHUNK = 64 * 1024
INITIAL_ROBOTS = f"https://{HOST}/robots.txt"
REDIRECT_ROBOTS = f"https://{HOST}/eventdirector/robots.txt"
PDF_URLS = (
    f"https://{HOST}/eventdirector/en/roundscores/3451330.pdf",
    f"https://{HOST}/eventdirector/en/roundscores/3451331.pdf",
)
DEPLOYED_SYSTEM = (
    "/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8"
)
HELD_UNITS = tuple(
    f"swingset-{name}.{suffix}"
    for name in ("cycle", "backup", "summary")
    for suffix in ("service", "timer")
)
PUBLISHED_BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
ACKNOWLEDGED_CANDIDATE = "cand_8f31cad7226643ae"


class OriginStopped(RuntimeError):
    """A sealed fixture prerequisite or hard transport boundary failed."""


class _RejectCookies(http.cookiejar.DefaultCookiePolicy):
    """Prevent httpx from storing response cookies or returning them later."""

    def set_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False

    def return_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def verify_packet(packet: Path, expected_closure_sha256: str) -> dict[str, Any]:
    """Verify the reviewed packet's complete immutable file closure."""
    if packet.is_symlink():
        raise OriginStopped("packet root cannot be a symlink")
    packet = packet.resolve(strict=True)
    closure_body = (packet / "closure.json").read_bytes()
    if sha256(closure_body) != expected_closure_sha256:
        raise OriginStopped("packet closure digest differs from coordinator gate")
    closure = json.loads(closure_body)
    if closure.get("format") != "dcn-origin-fixture-closure-v1" or not isinstance(
        closure.get("files"), dict
    ):
        raise OriginStopped("packet closure is malformed")
    paths = {item.relative_to(packet).as_posix() for item in packet.rglob("*") if item.is_file()}
    if paths != set(closure["files"]) | {"closure.json", "build-receipt.json"} or any(
        item.is_symlink() for item in packet.rglob("*")
    ):
        raise OriginStopped("packet contains undeclared, missing or linked files")
    for name, expected in closure["files"].items():
        if sha256((packet / name).read_bytes()) != expected:
            raise OriginStopped("packet member differs: " + name)
    receipt = json.loads((packet / "build-receipt.json").read_bytes())
    if (
        receipt.get("format") != "dcn-origin-fixture-build-v1"
        or receipt.get("closure_sha256") != expected_closure_sha256
        or receipt.get("files") != closure["files"]
        or receipt.get("runner_sha256") != closure["files"].get(Path(__file__).name)
    ):
        raise OriginStopped("packet build receipt differs")
    if sha256(Path(__file__).read_bytes()) != closure["files"].get(Path(__file__).name):
        raise OriginStopped("executing runner bytes differ from reviewed packet closure")
    return json.loads((packet / "manifest.json").read_bytes())


def validate_execution_gate(
    gate: dict[str, Any],
    manifest: dict[str, Any],
    *,
    source: Path,
    state: Path,
    quarantine: Path,
    ordinary_config_sha256: str,
    authorization_sha256: str,
    control_revision: int,
) -> None:
    """Bind the coordinator gate to exact runtime, operation and live control state."""
    expected_source = manifest["reference_runtime"]["source"]
    expected_receipt = manifest["reference_runtime"]["source_receipt_sha256"]
    if (
        gate.get("format") != "dcn-origin-fixture-execution-gate-v1"
        or gate.get("source") != str(source.resolve())
        or str(source.resolve()) != expected_source
        or gate.get("source_receipt_sha256") != expected_receipt
        or gate.get("schema_version") != 28
        or manifest["reference_runtime"].get("schema") != 28
        or gate.get("ordinary_config_sha256") != ordinary_config_sha256
        or manifest.get("ordinary_config_sha256") != ordinary_config_sha256
        or gate.get("state") != str(state.resolve())
        or gate.get("quarantine") != str(quarantine.resolve())
        or gate.get("authorization_sha256") != authorization_sha256
        or gate.get("source_event_id") != EVENT_ID
        or gate.get("pdf_urls") != list(PDF_URLS)
        or gate.get("production_facts_or_watches_created") != 0
        or gate.get("control_revision") != control_revision
        or gate.get("system") != DEPLOYED_SYSTEM
        or gate.get("inactive_units") != list(HELD_UNITS)
        or gate.get("published_commit") != PUBLISHED_BASELINE
        or gate.get("acknowledged_candidate") != ACKNOWLEDGED_CANDIDATE
        or manifest["reference_runtime"].get("published_commit") != PUBLISHED_BASELINE
        or manifest["reference_runtime"].get("acknowledged_candidate") != ACKNOWLEDGED_CANDIDATE
    ):
        raise OriginStopped(
            "execution gate does not bind exact source, schema, config, controls and paths"
        )


def origin_redirect(initial: str, proposed: str, *, purpose: str, redirects: int) -> bool:
    """Permit only the single reviewed robots redirect; PDF redirects always fail."""
    target = urlsplit(proposed)
    if (
        target.scheme != "https"
        or target.hostname != HOST
        or target.port not in (None, 443)
        or target.username
        or target.password
        or target.query
        or target.fragment
    ):
        return False
    return (
        purpose == "robots"
        and redirects == 0
        and initial == INITIAL_ROBOTS
        and proposed == REDIRECT_ROBOTS
    )


def check_ordinary_source_policy(path: Path) -> tuple[bytes, bool]:
    """Missing DCN stays missing; an explicit false entry remains a kill switch."""
    body = path.read_bytes()
    try:
        raw = tomllib.loads(body.decode("utf-8"))
        entry = raw.get("sources", {}).get("dcn")
        if entry is not None and entry.get("enabled", False) is False:
            raise OriginStopped("ordinary DCN source is explicitly disabled")
        if entry is not None and not isinstance(entry.get("enabled", False), bool):
            raise OriginStopped("ordinary DCN source enablement is malformed")
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, AttributeError) as exc:
        raise OriginStopped("ordinary source configuration is unreadable") from exc
    return body, entry is None


def redirect_target(response: httpx.Response, url: str, *, purpose: str, count: int) -> str:
    location = response.headers.get("location")
    if not location:
        raise OriginStopped("redirect response omitted Location")
    proposed = urljoin(url, location)
    if not origin_redirect(url, proposed, purpose=purpose, redirects=count):
        raise OriginStopped("redirect outside the exact origin fixture scope")
    return proposed


def decoded_chunks(response: httpx.Response, *, capacity: int) -> tuple[Iterator[bytes], Any]:
    """Yield decoded data in bounded chunks; the completion flag avoids an EOF probe at cap."""
    encoding = response.headers.get("content-encoding", "identity").lower()
    if encoding not in {"identity", "gzip"}:
        raise OriginStopped("unexpected content encoding")
    # Do not ask httpx to coalesce raw compressed chunks to CHUNK: that
    # wrapper can read one more transport chunk to fill its requested size.
    raw_chunks = iter(response.iter_raw())

    def chunks() -> Iterator[bytes]:
        used = 0
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        raw_pending = b""
        while used < capacity:
            if not raw_pending:
                try:
                    raw_pending = next(raw_chunks)
                except StopIteration:
                    break
            room = min(CHUNK, capacity - used)
            if decoder is None:
                output = raw_pending[:room]
                raw_pending = raw_pending[len(output) :]
            else:
                output = decoder.decompress(raw_pending, room)
                raw_pending = decoder.unconsumed_tail
                if decoder.unused_data:
                    raise OriginStopped("gzip response has trailing or concatenated data")
            if output:
                used += len(output)
                yield output
            elif raw_pending and decoder is not None:
                # The decoder may consume gzip headers without producing output.
                continue
            elif not raw_pending:
                continue
        complete = bool(decoder.eof and not decoder.unused_data) if decoder else False
        if used < capacity and decoder is None:
            complete = not raw_pending
        if used < capacity and decoder is not None and not decoder.eof:
            raise OriginStopped("gzip stream ended before its trailer")
        yield _Completion(complete)  # private sentinel; never response-body data

    return chunks(), _Completion


class _Completion:
    def __init__(self, complete: bool) -> None:
        self.complete = complete


def retained_headers(headers: httpx.Headers) -> dict[str, str]:
    """Keep response metadata while never persisting cookie values."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"set-cookie", "set-cookie2", "cookie"}
    }


def read_cached_robots(
    conn: sqlite3.Connection, archive: Archive, clock: Clock, state: Path, output: Path
) -> tuple[int, bytes, dict[str, Any]] | None:
    """Use only cache rows whose sidecar proves the exact retained URL and body."""
    row = conn.execute(
        "SELECT robots_sha256,robots_fetched_at,robots_status FROM hosts WHERE host=?", (HOST,)
    ).fetchone()
    if row is None or not row[0] or not row[1] or row[2] is None:
        return None
    try:
        fetched = datetime.fromisoformat(str(row[1]))
        now = clock.now().astimezone(UTC)
        if fetched.tzinfo is None:
            raise OriginStopped("robots cache timestamp is not timezone aware")
        if fetched > now:
            raise OriginStopped("robots cache timestamp is in the future")
        if fetched + timedelta(hours=24) <= now:
            return None
        proof_path = state / "dcn-origin-robots-cache.json"
        if not proof_path.is_file() or proof_path.is_symlink():
            raise OriginStopped("fresh robots cache lacks exact URL provenance; refresh prohibited")
        proof = json.loads(proof_path.read_bytes())
        if (
            not isinstance(proof, dict)
            or proof.get("format") != "dcn-origin-robots-cache-v1"
            or proof.get("host") != HOST
            or proof.get("requested_url") != INITIAL_ROBOTS
            or proof.get("final_url") not in {INITIAL_ROBOTS, REDIRECT_ROBOTS}
            or proof.get("status") != int(row[2])
            or proof.get("body_sha256") != str(row[0])
            or proof.get("fetched_at") != fetched.isoformat()
        ):
            raise OriginStopped("fresh robots cache provenance differs; refresh prohibited")
        body = archive.read_body(str(row[0]))
        if sha256(body) != str(row[0]) or len(body) > MAX_RESPONSE:
            raise OriginStopped("fresh robots cache body proof differs; refresh prohibited")
    except OriginStopped:
        raise
    except (OSError, ValueError, EOFError):
        raise OriginStopped("fresh robots cache cannot be verified; refresh prohibited") from None
    durable_write(output / "bodies" / sha256(body), body)
    proof = {
        "requested_url": INITIAL_ROBOTS,
        "final_url": proof["final_url"],
        "host": HOST,
        "status": int(row[2]),
        "body_sha256": sha256(body),
        "fetched_at": fetched.isoformat(),
        "format": "dcn-origin-robots-cache-v1",
        "cache_age_seconds": (now - fetched.astimezone(UTC)).total_seconds(),
    }
    return int(row[2]), body, proof


def _reserve_event_day(
    state: Path, *, day: str, run_id: str, sequence: int, url: str, purpose: str
) -> None:
    """Claim one ordered request before dispatch; a crash cannot replay its slot."""
    path = state / "dcn-origin-event-days.json"
    if path.is_symlink():
        raise OriginStopped("event-day ledger cannot be a symlink")
    try:
        record = (
            json.loads(path.read_bytes())
            if path.exists()
            else {"format": "dcn-event-day-v2", "days": {}}
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise OriginStopped("event-day ledger is unreadable") from exc
    if record.get("format") != "dcn-event-day-v2" or not isinstance(record.get("days"), dict):
        raise OriginStopped("event-day ledger has unknown format")
    prior_requests: list[dict[str, Any]] = []
    for item in record["days"].values():
        if not isinstance(item, dict) or not isinstance(item.get("requests"), list):
            raise OriginStopped("event-day ledger entry is malformed")
        if item.get("run_id") == run_id:
            prior_requests.extend(item["requests"])
    existing = record["days"].get(day)
    if existing is None:
        existing = {"event_id": EVENT_ID, "run_id": run_id, "requests": []}
        record["days"][day] = existing
    if existing.get("event_id") != EVENT_ID or existing.get("run_id") != run_id:
        raise OriginStopped("DCN one-event-per-UTC-day limit already claimed")
    requests = existing.get("requests")
    if not isinstance(requests, list) or len(prior_requests) >= MAX_REQUESTS:
        raise OriginStopped("durable four-request operation ceiling reached")
    if sequence != len(prior_requests) + 1:
        raise OriginStopped("durable request sequence prevents a retry or resume")
    if url not in {INITIAL_ROBOTS, REDIRECT_ROBOTS, *PDF_URLS}:
        raise OriginStopped("durable event ledger request URL outside exact scope")
    if purpose not in {"robots", "pdf"}:
        raise OriginStopped("durable event ledger request purpose is unknown")
    requests.append({"sequence": sequence, "url": url, "purpose": purpose})
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    with temp.open("xb") as stream:
        stream.write(canonical(record) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextlib.contextmanager
def accounting_connection(state: Path) -> Iterator[sqlite3.Connection]:
    """Open schema 28 in read/write mode without migration or domain-table writes."""
    if (state / "RESTORE_PENDING").exists():
        raise OriginStopped("restore verification is pending")
    with (state / "state.lock").open("r+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OriginStopped("production writer lock is held") from exc
        if (state / "RESTORE_PENDING").exists():
            raise OriginStopped("restore verification became pending")
        conn = sqlite3.connect(
            (state / "state.sqlite").resolve().as_uri() + "?mode=rw",
            uri=True,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            for table in (
                "execution_admissions",
                "execution_dependencies",
                "control_state",
                "control_events",
                "operator_pauses",
            ):
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone():
                    raise OriginStopped("H13 control schema missing")

            def only_accounting(
                action: int, table: str | None, column: str | None, *args: Any
            ) -> int:
                controls = {
                    "execution_admissions",
                    "execution_dependencies",
                    "control_state",
                    "control_events",
                }
                if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                    if table in controls or table == "operator_pauses":
                        return sqlite3.SQLITE_OK
                    if table in {"host_budget", "host_request_spacing"}:
                        return sqlite3.SQLITE_OK
                    if table == "hosts" and (
                        action == sqlite3.SQLITE_INSERT
                        or column
                        in {
                            "next_allowed_at",
                            "paused_until",
                            "pause_reason",
                            "pause_streak",
                            "robots_sha256",
                            "robots_fetched_at",
                            "robots_status",
                        }
                    ):
                        return sqlite3.SQLITE_OK
                    return sqlite3.SQLITE_DENY
                if action in {
                    sqlite3.SQLITE_DELETE,
                    sqlite3.SQLITE_CREATE_TABLE,
                    sqlite3.SQLITE_DROP_TABLE,
                    sqlite3.SQLITE_ALTER_TABLE,
                    sqlite3.SQLITE_ATTACH,
                    sqlite3.SQLITE_DETACH,
                }:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(only_accounting)
            yield conn
        finally:
            conn.close()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class OriginFixtureRunner:
    """Capture the exact two PDFs through shared host gates and a sealed quarantine."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        config: Config,
        clock: Clock,
        *,
        state: Path,
        output: Path,
        source_config: Path,
        source_config_sha256: str,
        authorization: dict[str, Any],
        transport: httpx.BaseTransport | None = None,
        monotonic: Any | None = None,
        production_guard: Callable[[], None] | None = None,
    ) -> None:
        if output.exists() or output.is_symlink():
            raise OriginStopped("quarantine is single-use and must be new")
        if not (state / "operator-hold").is_file():
            raise OriginStopped("scheduled-service hold must remain present")
        if (state / "RESTORE_PENDING").exists():
            raise OriginStopped("restore verification is pending")
        schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if (
            schema is None
            or db_module.SCHEMA_VERSION != 28
            or int(schema[0]) != 28
            or int(conn.execute("PRAGMA user_version").fetchone()[0]) != 28
        ):
            raise OriginStopped("exact schema 28 required")
        body, missing = check_ordinary_source_policy(source_config)
        if sha256(body) != source_config_sha256:
            raise OriginStopped("ordinary source configuration binding changed")
        self.source_config, self.source_config_sha256 = source_config, source_config_sha256
        self.ordinary_dcn_missing = missing
        self.conn, self.clock, self.state, self.output = conn, clock, state, output
        self.authorization = authorization
        self.transport = transport
        self.monotonic = monotonic or clock.monotonic
        self.production_guard = production_guard
        if isinstance(clock, SystemClock) and production_guard is None:
            raise OriginStopped("production system/unit guard is required")
        self.run_id = str(authorization.get("operation_id", ""))
        if not self.run_id:
            raise OriginStopped("authorization lacks a stable operation id")
        if authorization.get("event_id") != EVENT_ID or authorization.get("pdf_urls") != list(
            PDF_URLS
        ):
            raise OriginStopped("authorization differs from exact source-event scope")
        if (
            authorization.get("state") != str(state.resolve())
            or authorization.get("quarantine") != str(output.resolve())
            or authorization.get("owner_decision_reference")
            != "journal/decisions/0087-authorize-remaining-v2-acquisition-and-operations.md"
        ):
            raise OriginStopped(
                "authorization does not bind the exact state, quarantine and D-0087"
            )
        try:
            approved = datetime.fromisoformat(authorization["approved_at"])
            window = authorization["execution_window"]
            starts = datetime.fromisoformat(window["starts_at"])
            expires = datetime.fromisoformat(window["expires_at"])
            valid_window = (
                approved <= starts <= clock.now() < expires <= starts + timedelta(hours=24)
            )
        except (KeyError, TypeError, ValueError):
            valid_window = False
        if not valid_window:
            raise OriginStopped("authorization execution window is absent, stale or over 24 hours")
        current_policy = config.host(HOST)
        origin_policy = HostConfig(
            min_gap_seconds=max(10, current_policy.min_gap_seconds),
            daily_request_budget=min(200, current_policy.daily_request_budget),
            daily_byte_budget=min(300_000_000, current_policy.daily_byte_budget)
            if current_policy.daily_byte_budget is not None
            else 300_000_000,
            challenge_pause=current_policy.challenge_pause,
        )
        self.operation_config = Config(
            hosts={**config.hosts, HOST: origin_policy},
            sources={**config.sources, "dcn": SourceConfig(enabled=True)},
            history_start=config.history_start,
            scheduler=config.scheduler,
        )
        self.gate = Gate(conn, self.operation_config, clock)
        self.database = Database(state, conn, None)
        self.scope = ActionScope(
            sources=frozenset({"dcn"}),
            kinds=frozenset({"round_observations"}),
            host=HOST,
        )
        self.deadline = clock.now() + timedelta(seconds=900)
        self.receipt: dict[str, Any] = {
            "format": "dcn-origin-fixture-receipt-v1",
            "operation_id": self.run_id,
            "event_id": EVENT_ID,
            "ordinary_dcn_config": "missing" if missing else "explicitly-enabled",
            "started_at": clock.now().isoformat(),
            "requests": [],
            "targets": [],
            "received_bytes": 0,
            "production_facts_or_watches_created": 0,
            "status": "running",
        }
        self.rules: Protego | None = None
        self.crawl_delay = 0.0

    def _save(self) -> None:
        durable_write(self.output / "receipt.json", canonical(self.receipt))

    def _interlocks(self) -> None:
        if self.production_guard is not None:
            self.production_guard()
        if self.clock.now() >= self.deadline:
            raise OriginStopped("15-minute operation window expired")
        if not (self.state / "operator-hold").is_file():
            raise OriginStopped("scheduled-service hold was removed")
        if (self.state / "RESTORE_PENDING").exists():
            raise OriginStopped("restore verification became pending")
        body, missing = check_ordinary_source_policy(self.source_config)
        if sha256(body) != self.source_config_sha256:
            raise OriginStopped("ordinary source configuration binding changed")
        if missing != self.ordinary_dcn_missing:
            raise OriginStopped("ordinary source registration state changed")
        if (
            self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            != "28"
        ):
            raise OriginStopped("schema changed during operation")
        if (
            db_module.SCHEMA_VERSION != 28
            or int(self.conn.execute("PRAGMA user_version").fetchone()[0]) != 28
        ):
            raise OriginStopped("runtime or database schema binding changed")
        paused = matching_pauses(self.conn, self.scope, now=self.clock.now())
        if paused:
            raise OriginStopped("H13 operator control pauses fixture purpose")

    def _admit(self, url: str, purpose: str) -> tuple[Grant, str]:
        self._interlocks()
        if len(self.receipt["requests"]) >= MAX_REQUESTS:
            raise OriginStopped("four-request ceiling")
        action_id = f"dcn_origin_{self.run_id}_{len(self.receipt['requests']) + 1}"
        reservation_error: OriginStopped | None = None
        while True:
            self._interlocks()
            try:
                with admission(
                    Database(self.state, self.conn, None),
                    action_id=action_id,
                    action_kind="request",
                    scope=self.scope,
                    now=self.clock.now(),
                ) as conn:
                    grant = self.gate.acquire(HOST, source="dcn", crawl_delay=self.crawl_delay)
                    if isinstance(grant, Grant):
                        if grant.debited_at is None:
                            from swingset.fetch.classify import Classification

                            self.gate.release(
                                HOST,
                                Classification(Outcome.INVALID),
                                request_day=self.clock.now().astimezone(UTC).date().isoformat(),
                            )
                            settle(conn, action_id, now=self.clock.now(), outcome="not_issued")
                            reservation_error = OriginStopped(
                                "host grant omitted durable UTC debit time"
                            )
                        else:
                            day = grant.debited_at.astimezone(UTC).date().isoformat()
                            try:
                                if self.conn.execute(
                                    "SELECT 1 FROM history_origin_requests WHERE source='dcn' AND day=? AND event_id<>? LIMIT 1",
                                    (day, EVENT_ID),
                                ).fetchone():
                                    raise OriginStopped(
                                        "another DCN event already used the UTC origin-day allowance"
                                    )
                                # Claim before admission/Gate commit so a crash
                                # cannot separate event cadence from its debit.
                                _reserve_event_day(
                                    self.state,
                                    day=day,
                                    run_id=self.run_id,
                                    sequence=len(self.receipt["requests"]) + 1,
                                    url=url,
                                    purpose=purpose,
                                )
                            except (OriginStopped, OSError, ValueError) as exc:
                                from swingset.fetch.classify import Classification

                                self.gate.release(
                                    HOST,
                                    Classification(Outcome.INVALID),
                                    request_day=day,
                                )
                                settle(conn, action_id, now=self.clock.now(), outcome="not_issued")
                                reservation_error = (
                                    exc
                                    if isinstance(exc, OriginStopped)
                                    else OriginStopped(f"durable event-day claim failed: {exc}")
                                )
                    else:
                        raise _Deferred(grant)
            except _Deferred as exc:
                grant = exc.grant
                if isinstance(grant, Paused):
                    raise OriginStopped("shared host gate: " + grant.reason) from exc
                if not isinstance(grant, Wait):
                    raise OriginStopped("unknown host gate result") from exc
                self.clock.sleep(
                    min(grant.seconds, (self.deadline - self.clock.now()).total_seconds())
                )
                continue
            except ControlPaused as exc:
                raise OriginStopped("H13 operator control pauses fixture purpose") from exc
            break
        if reservation_error is not None:
            raise reservation_error
        return grant, action_id

    def _reserve(self, day: str) -> int:
        row = self.conn.execute(
            "SELECT bytes FROM host_budget WHERE host=? AND day=?", (HOST, day)
        ).fetchone()
        used = int(row[0]) if row else 0
        shared = self.operation_config.host(HOST).daily_byte_budget or 300_000_000
        available = min(MAX_RESPONSE, MAX_TOTAL - self.receipt["received_bytes"], shared - used)
        available -= available % CHUNK
        if available <= 0:
            raise OriginStopped("shared or fixture response byte budget exhausted")
        self.conn.execute(
            "UPDATE host_budget SET bytes=bytes+? WHERE host=? AND day=?", (available, HOST, day)
        )
        self.conn.commit()
        return available

    def _exchange(
        self, client: httpx.Client, url: str, purpose: str
    ) -> tuple[httpx.Response, bytes, dict[str, Any]]:
        grant, action_id = self._admit(url, purpose)
        day = grant.debited_at.astimezone(UTC).date().isoformat()
        reserved = 0
        request: dict[str, Any] = {
            "url": url,
            "purpose": purpose,
            "request_day": day,
            "paid_admission": True,
            "admitted_at": grant.debited_at.isoformat(),
            "socket_dispatch_attempted": False,
            "reserved_bytes": 0,
            "complete": False,
        }
        response_obj: httpx.Response | None = None
        body = bytearray()
        dispatched = False
        try:
            self.receipt["requests"].append(request)
            self._save()
            reserved = self._reserve(day)
            request["reserved_bytes"] = reserved
            headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
            # Validators are sent only when a retained, exact-URL body and its
            # validator are supplied by a reviewed manifest. This exact scope
            # has none; an unsolicited 304 is rejected below.
            validator = self.authorization.get("validators", {}).get(url)
            if validator:
                if not validator.get("body_sha256") or not validator.get("body_path"):
                    raise OriginStopped("conditional validator lacks its exact retained body")
                retained = Path(validator["body_path"]).read_bytes()
                if sha256(retained) != validator["body_sha256"]:
                    raise OriginStopped("conditional representation body proof differs")
                if validator.get("etag"):
                    headers["If-None-Match"] = validator["etag"]
                if validator.get("last_modified"):
                    headers["If-Modified-Since"] = validator["last_modified"]
            client.cookies.clear()
            self._interlocks()
            dispatched = True
            request["socket_dispatch_attempted"] = True
            request["dispatched_at"] = self.clock.now().isoformat()
            request["dispatched_monotonic"] = self.monotonic()
            with client.stream(
                "GET",
                url,
                headers=headers,
                timeout=min(30, (self.deadline - self.clock.now()).total_seconds()),
            ) as streamed:
                response_obj = streamed
                request["http_status"] = streamed.status_code
                request["headers"] = retained_headers(streamed.headers)
                request["final_url"] = str(streamed.url)
                accepted_status = (
                    streamed.status_code == 200
                    or (
                        purpose == "robots"
                        and (streamed.is_redirect or 400 <= streamed.status_code < 600)
                    )
                    or (streamed.status_code == 304 and validator is not None)
                )
                if streamed.status_code == 304 and validator:
                    request["complete"] = True
                    request["conditional_body_sha256"] = validator["body_sha256"]
                    request["body_bytes"] = 0
                    body.extend(Path(validator["body_path"]).read_bytes())
                    self.conn.execute(
                        "UPDATE host_budget SET bytes=bytes-? WHERE host=? AND day=?",
                        (reserved, HOST, day),
                    )
                else:
                    iterator, _ = decoded_chunks(streamed, capacity=reserved)
                    for chunk in iterator:
                        if isinstance(chunk, _Completion):
                            request["complete"] = chunk.complete
                            break
                        if self.clock.now() >= self.deadline:
                            body.extend(chunk)
                            self.receipt["received_bytes"] += len(chunk)
                            raise OriginStopped("deadline reached during response")
                        body.extend(chunk)
                        self.receipt["received_bytes"] += len(chunk)
                    request["body_bytes"] = len(body)
                    if request["complete"]:
                        charge = len(body)
                        self.conn.execute(
                            "UPDATE host_budget SET bytes=bytes-?+? WHERE host=? AND day=?",
                            (reserved, charge, HOST, day),
                        )
                    elif len(body) == reserved:
                        raise OriginStopped(
                            "decoded body reached reservation cap; EOF was not probed"
                        )
                    else:
                        raise OriginStopped("response body is incomplete")
                    if not accepted_status:
                        raise OriginStopped(
                            f"unexpected HTTP status {streamed.status_code}; no retry"
                        )
            request["body_sha256"] = sha256(bytes(body)) if body else None
            request["completed_at"] = self.clock.now().isoformat()
            request["completed_monotonic"] = self.monotonic()
            request["exchange_completed_elapsed_seconds"] = (
                request["completed_monotonic"] - request["dispatched_monotonic"]
            )
            self._save()
            return response_obj, bytes(body), request
        except (httpx.HTTPError, OSError, OriginStopped, ValueError, zlib.error) as exc:
            request["body_bytes"] = len(body)
            request["body_sha256"] = sha256(bytes(body)) if body else None
            request["complete"] = False
            request["stop_reason"] = f"{purpose} {url}: {exc}"
            request["completed_at"] = self.clock.now().isoformat()
            with contextlib.suppress(OSError):
                self._save()
            raise OriginStopped(f"{purpose} {url}: {exc}") from exc
        finally:
            client.cookies.clear()
            if response_obj is not None:
                classification_headers = {
                    key: value
                    for key, value in retained_headers(response_obj.headers).items()
                    if key.lower() != "content-encoding"
                }
                classified = httpx.Response(
                    response_obj.status_code,
                    headers=classification_headers,
                    content=bytes(body),
                    request=response_obj.request,
                )
                outcome = classify(classified, _PAGE_KIND, None, self.clock.now())
            else:
                from swingset.fetch.classify import Classification

                outcome = Classification(Outcome.SERVER_ERROR if dispatched else Outcome.INVALID)
            release_error: BaseException | None = None
            try:
                self.gate.release(HOST, outcome, request_day=day)
            except BaseException as exc:
                release_error = exc
                self.gate.discard(HOST)
            try:
                request["classified_outcome"] = outcome.outcome.value
                request["retry_after_seconds"] = outcome.retry_after
                host = self.conn.execute(
                    "SELECT paused_until,pause_reason,pause_streak,next_allowed_at FROM hosts WHERE host=?",
                    (HOST,),
                ).fetchone()
                if host is not None:
                    request["host_gate_after_response"] = {
                        "paused_until": host[0],
                        "pause_reason": host[1],
                        "pause_streak": host[2],
                        "next_allowed_at": host[3],
                    }
                with Database(self.state, self.conn, None).transaction() as conn:
                    settle(
                        conn,
                        action_id,
                        now=self.clock.now(),
                        outcome=outcome.outcome.value if dispatched else "not_issued",
                    )
                with contextlib.suppress(OSError):
                    self._save()
            except BaseException as exc:
                if release_error is None:
                    release_error = exc
            if release_error is not None:
                raise OriginStopped(
                    f"request accounting finalization failed: {release_error}"
                ) from release_error

    def _request(
        self, client: httpx.Client, url: str, purpose: str
    ) -> tuple[httpx.Response, bytes]:
        target, count = url, 0
        while True:
            if target not in {INITIAL_ROBOTS, REDIRECT_ROBOTS, *PDF_URLS}:
                raise OriginStopped("request outside the exact URL allowlist")
            self._check_robots(target, purpose, client)
            try:
                with operation(
                    self.database,
                    action_id=f"dcn_origin_fetch_{self.run_id}_{len(self.receipt['requests']) + 1}",
                    action_kind="fetch",
                    scope=self.scope,
                    clock=self.clock,
                ):
                    response_obj, body, request = self._exchange(client, target, purpose)
            except ControlPaused as exc:
                raise OriginStopped("H13 operator control pauses fixture purpose") from exc
            if response_obj.is_redirect:
                target = redirect_target(response_obj, target, purpose=purpose, count=count)
                count += 1
                continue
            request["redirect_count"] = count
            return response_obj, body

    def _check_robots(self, url: str, purpose: str, client: httpx.Client) -> None:
        if purpose == "robots" or (self.rules is not None and url == INITIAL_ROBOTS):
            return
        if not getattr(self, "robots_ready", False):
            cached = read_cached_robots(
                self.conn, Archive(self.state), self.clock, self.state, self.output
            )
            if cached:
                status, body, proof = cached
                self.receipt["cached_robots"] = proof
            else:
                response_obj, body = self._request(client, INITIAL_ROBOTS, "robots")
                status = response_obj.status_code
                fetched_at = self.clock.now().isoformat()
                self.conn.execute("INSERT OR IGNORE INTO hosts(host) VALUES (?)", (HOST,))
                self.conn.execute(
                    "UPDATE hosts SET robots_sha256=?,robots_fetched_at=?,robots_status=? WHERE host=?",
                    (sha256(body), fetched_at, status, HOST),
                )
                Archive(self.state).store_body(body)
                durable_write(
                    self.state / "dcn-origin-robots-cache.json",
                    canonical(
                        {
                            "format": "dcn-origin-robots-cache-v1",
                            "host": HOST,
                            "requested_url": INITIAL_ROBOTS,
                            "final_url": str(response_obj.url),
                            "status": status,
                            "body_sha256": sha256(body),
                            "fetched_at": fetched_at,
                        }
                    )
                    + b"\n",
                )
            if status == 200:
                try:
                    self.rules = Protego.parse(body.decode("utf-8"))
                except UnicodeDecodeError as exc:
                    raise OriginStopped("robots body is not valid UTF-8") from exc
                self.crawl_delay = float(self.rules.crawl_delay("swingset") or 0)
                if self.crawl_delay:
                    if self.receipt["requests"]:
                        completed = float(self.receipt["requests"][-1]["completed_monotonic"])
                        elapsed = self.monotonic() - completed
                    else:
                        # Cache proof time is written after the response, so
                        # this slightly over-waits while remaining safe.
                        elapsed = float(proof.get("cache_age_seconds", 0.0))
                    remaining = max(0.0, self.crawl_delay - elapsed)
                    if self.clock.now() + timedelta(seconds=remaining) >= self.deadline:
                        raise OriginStopped("robots crawl delay exceeds operation window")
                    if remaining:
                        self.clock.sleep(remaining)
            elif status not in (404, 410):
                raise OriginStopped("robots policy unavailable; fail closed")
            self.robots_ready = True
        if self.rules and not self.rules.can_fetch(url, "swingset"):
            raise OriginStopped("robots disallows exact PDF URL")
        self._interlocks()

    def run(self) -> dict[str, Any]:
        self.output.mkdir(parents=True, exist_ok=False)
        durable_write(self.output / "authorization.json", canonical(self.authorization))
        self.receipt["network_requests_authorized"] = MAX_REQUESTS
        self._save()
        try:
            with httpx.Client(
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            ) as client:
                client.cookies.jar.set_policy(_RejectCookies())
                for url in PDF_URLS:
                    self._interlocks()
                    response_obj, body = self._request(client, url, "pdf")
                    entry = {
                        "requested_url": url,
                        "final_url": str(response_obj.url),
                        "http_status": response_obj.status_code,
                        "headers": retained_headers(response_obj.headers),
                        "fetched_at": self.receipt["requests"][-1]["completed_at"],
                        "captured_at": self.receipt["requests"][-1]["completed_at"],
                        "observed_at": self.receipt["requests"][-1]["completed_at"],
                        "via": "origin",
                        "body_sha256": sha256(body),
                        "body_bytes": len(body),
                        "interpretation": "unreviewed_quarantine_body",
                        "content_finding": "pdf"
                        if body.startswith(b"%PDF-")
                        else "unexpected_body",
                    }
                    durable_write(self.output / "bodies" / entry["body_sha256"], body)
                    self.receipt["targets"].append(entry)
                    self._save()
                    if entry["content_finding"] != "pdf":
                        raise OriginStopped(
                            "origin response was not a PDF; retained as a quarantine finding"
                        )
            self.receipt["status"] = "captured_pending_independent_review"
        except (OriginStopped, httpx.HTTPError, OSError, ValueError) as exc:
            self.receipt["status"] = "stopped_incomplete"
            self.receipt["stop_reason"] = str(exc)
        finally:
            self.receipt["finished_at"] = self.clock.now().isoformat()
            self._save()
        return self.receipt


class _Deferred(Exception):
    def __init__(self, grant: Wait | Paused) -> None:
        self.grant = grant


class _OriginPageKind:
    kind = "fixture_quarantine"

    @staticmethod
    def expected_statuses(_: Any) -> frozenset[int]:
        return frozenset()


_PAGE_KIND = _OriginPageKind()


def _verify_runtime_source(source: Path, receipt_sha256: str) -> Path:
    source = source.resolve(strict=True)
    expected = Path("/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source")
    if source != expected:
        raise OriginStopped("runtime must use deployed source 003")
    receipt_path = source / "extension-source.json"
    receipt_body = receipt_path.read_bytes()
    if sha256(receipt_body) != receipt_sha256 or receipt_sha256 != (
        "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6"
    ):
        raise OriginStopped("source 003 receipt differs")
    inventory = json.loads(receipt_body).get("files")
    actual = {
        item.relative_to(source).as_posix()
        for item in source.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts
    }
    actual.discard("extension-source.json")
    if not isinstance(inventory, dict) or actual != set(inventory):
        raise OriginStopped("source 003 file inventory differs")
    for name, value in inventory.items():
        path = source / name
        expected_hash = value["sha256"] if isinstance(value, dict) else value
        if path.is_symlink() or sha256(path.read_bytes()) != expected_hash:
            raise OriginStopped("source 003 file differs: " + name)
    for name, module in sys.modules.items():
        if name == "swingset" or name.startswith("swingset."):
            origin = getattr(module, "__file__", None)
            if origin is not None and not Path(origin).resolve().is_relative_to(source):
                raise OriginStopped("loaded application module is outside source 003")
    if db_module.SCHEMA_VERSION != 28:
        raise OriginStopped("loaded source is not bound to schema 28")
    return source


def verify_live_production_state(
    *, active_system: str, persistent_system: str, unit_state: Callable[[str], str]
) -> None:
    """Require the pinned active/persistent generation and six inactive units."""
    if active_system != DEPLOYED_SYSTEM or persistent_system != DEPLOYED_SYSTEM:
        raise OriginStopped("active or persistent system differs from deployed source-003 system")
    for unit in HELD_UNITS:
        if unit_state(unit) != "inactive":
            raise OriginStopped(f"scheduled unit is not inactive: {unit}")


def production_runtime_guard(state: Path) -> None:
    """Fail closed unless both system generations and all scheduled units match the hold."""
    if not (state / "operator-hold").is_file():
        raise OriginStopped("operator hold is absent")
    resolved: list[str] = []
    for link in (Path("/run/current-system"), Path("/nix/var/nix/profiles/system")):
        try:
            resolved.append(str(link.resolve(strict=True)))
        except OSError as exc:
            raise OriginStopped(f"cannot resolve system binding {link}") from exc

    def unit_state(unit: str) -> str:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OriginStopped(f"cannot check held service {unit}") from exc
        return result.stdout.strip()

    verify_live_production_state(
        active_system=resolved[0], persistent_system=resolved[1], unit_state=unit_state
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    parser.add_argument(
        "--execute", action="store_true", help="run the reviewed exact-scope operation"
    )
    args = parser.parse_args(argv)
    try:
        production_runtime_guard(args.state)
        gate = json.loads(args.gate.read_bytes())
        if gate.get("format") != "dcn-origin-fixture-execution-gate-v1":
            raise OriginStopped("execution gate format differs")
        manifest = verify_packet(args.packet, gate["packet_closure_sha256"])
        source = _verify_runtime_source(
            args.source, manifest["reference_runtime"]["source_receipt_sha256"]
        )
        ordinary = source / "config/sources.toml"
        authorization_body = args.authorization.read_bytes()
        with accounting_connection(args.state) as conn:
            schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema != 28 or db_module.SCHEMA_VERSION != 28:
                raise OriginStopped("live database or runner schema is not 28")
            control_revision = int(
                conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()[0]
            )
            scope = ActionScope(
                sources=frozenset({"dcn"}),
                kinds=frozenset({"round_observations"}),
                host=HOST,
            )
            if matching_pauses(conn, scope, now=datetime.now(UTC)):
                raise OriginStopped("live H13 control pause holds the origin operation")
        validate_execution_gate(
            gate,
            manifest,
            source=source,
            state=args.state,
            quarantine=args.quarantine,
            ordinary_config_sha256=sha256(ordinary.read_bytes()),
            authorization_sha256=sha256(authorization_body),
            control_revision=control_revision,
        )
        if not args.execute:
            print(
                json.dumps(
                    {
                        "status": "preflight_passed_no_execution",
                        "closure_sha256": gate["packet_closure_sha256"],
                    }
                )
            )
            return 0
        if gate.get("execution_authorized") is not True:
            raise OriginStopped("execution gate is not explicitly authorized")
        from swingset.clock import SystemClock
        from swingset.config import load_config

        with accounting_connection(args.state) as conn:
            runner = OriginFixtureRunner(
                conn,
                load_config(source / "config"),
                SystemClock(),
                state=args.state,
                output=args.quarantine,
                source_config=ordinary,
                source_config_sha256=sha256(ordinary.read_bytes()),
                authorization=json.loads(authorization_body),
                production_guard=lambda: production_runtime_guard(args.state),
            )
            result = runner.run()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "captured_pending_independent_review" else 2
    except (OSError, ValueError, KeyError, OriginStopped) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
