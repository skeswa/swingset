"""Bounded streaming transport for the one reviewed fixture manifest, without watches."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx
from protego import Protego

from fixture_helpers.fixture_exception import (
    HOST,
    MANIFEST_SHA,
    FixtureStopped,
    validate_authorization,
)
from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.archive import Archive, canonical, digest, durable_write
from swingset.fetch.classify import Classification, Outcome, classify
from swingset.fetch.client import USER_AGENT
from swingset.fetch.politeness import Gate, Paused, Wait
from swingset.fetch.wayback import captured_at
from swingset.sources.base import PageKind

HTTP_CONTROL = cast(
    PageKind,
    SimpleNamespace(
        kind="fixture_quarantine",
        expected_statuses=lambda _: frozenset(),
    ),
)


def archive_redirect(initial: str, proposed: str) -> bool:
    """Archive redirects may change capture time, never the authorized original resource."""
    target, origin = urlsplit(proposed), urlsplit(initial)
    if (
        target.scheme != "https"
        or target.hostname != HOST
        or target.port not in (None, 443)
        or target.username
        or target.password
        or target.fragment
    ):
        return False
    if origin.path in {"/robots.txt", "/cdx/search/cdx"}:
        return target.path == origin.path and sorted(parse_qsl(target.query)) == sorted(
            parse_qsl(origin.query)
        )
    pattern = r"^/web/\d{14}id_/(.+)$"
    before, after = re.match(pattern, origin.path), re.match(pattern, target.path)
    return bool(before and after and before[1] == after[1] and origin.query == target.query)


class DispatchReceiptTransport(httpx.BaseTransport):
    """Timestamp handoff to the underlying transport, not an inferred wire time."""

    def __init__(self, runner: Any, inner: httpx.BaseTransport) -> None:
        self.runner, self.inner = runner, inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        current = self.runner.receipt["requests"][-1]
        current["transport_dispatched_at"] = self.runner.clock.now().isoformat()
        current["transport_dispatched_elapsed_seconds"] = self.runner.monotonic() - self.runner.monotonic_origin
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


class FixtureRunner:
    def __init__(
        self,
        connection: sqlite3.Connection,
        config: Config,
        clock: Clock,
        *,
        state: Path,
        output: Path,
        manifest: dict[str, Any],
        authorization: dict[str, Any],
        transport: httpx.BaseTransport | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if digest(canonical(manifest)) != MANIFEST_SHA:
            raise FixtureStopped("unknown fixture manifest")
        validate_authorization(authorization, state=state, output=output, now=clock.now())
        policy = config.host(HOST)
        policy = replace(
            policy,
            min_gap_seconds=max(10, policy.min_gap_seconds),
            daily_request_budget=min(200, policy.daily_request_budget),
        )
        self.config = replace(config, hosts={**config.hosts, HOST: policy})
        self.conn, self.clock, self.state, self.output = connection, clock, state, output
        self.monotonic = monotonic or time.monotonic
        self.monotonic_origin = self.monotonic()
        self.next_dispatch_monotonic = self.monotonic_origin + policy.min_gap_seconds
        self.manifest, self.authorization, self.transport = manifest, authorization, transport
        self.allowed_urls = frozenset(
            [target["replay_url"] for target in manifest["targets"]] + [f"https://{HOST}/robots.txt"]
        )
        self.limits = manifest["limits"]
        self.gate = Gate(connection, self.config, clock)
        self.deadline = min(
            clock.now() + timedelta(seconds=self.limits["max_elapsed_seconds"]),
            datetime.fromisoformat(authorization["execution_window"]["expires_at"]),
        )
        self.receipt: dict[str, Any] = {
            "manifest_canonical_sha256": MANIFEST_SHA,
            "authorization_sha256": digest(canonical(authorization)),
            "started_at": clock.now().isoformat(),
            "status": "running",
            "requests": [],
            "targets": [],
            "received_bytes": 0,
            "production_facts_or_watches_created": 0,
        }
        self.rules: Protego | None = None
        self.robots_ready = False
        self.crawl_delay = 0.0
        self.source = ""

    def _save(self) -> None:
        durable_write(self.output / "receipt.json", canonical(self.receipt))

    def _check(self) -> None:
        if self.clock.now() >= self.deadline:
            raise FixtureStopped("elapsed-time ceiling or execution window expired")
        if (
            len(self.receipt["requests"])
            >= self.limits["total_http_requests_including_redirects_and_robots"]
        ):
            raise FixtureStopped("fixture request ceiling")
        if self.receipt["received_bytes"] >= self.limits["max_total_received_bytes"]:
            raise FixtureStopped("fixture byte ceiling")

    def _reservation(self, day: str) -> int:
        budget = self.conn.execute(
            "SELECT bytes FROM host_budget WHERE host=? AND day=?", (HOST, day)
        ).fetchone()
        capacity = min(
            self.limits["max_received_bytes_per_response"],
            self.limits["max_total_received_bytes"] - self.receipt["received_bytes"],
        )
        daily = self.config.host(HOST).daily_byte_budget
        if daily is not None:
            capacity = min(capacity, daily - int(budget[0]))
        if capacity <= 0:
            raise FixtureStopped("shared byte budget exhausted")
        # Reserve the maximum before issuing I/O: a process crash may overcharge, never refund
        # an issued request or lose received bytes. A completed read refunds its unused reserve.
        self.conn.execute(
            "UPDATE host_budget SET bytes=bytes+? WHERE host=? AND day=?",
            (capacity, HOST, day),
        )
        self.conn.commit()
        return int(capacity)

    def _anchor_completion(self, request: dict[str, Any]) -> None:
        # The stream has closed (or failed) before this conservative boundary.
        # Persist before artifact writes and admission settlement: subsequent
        # operations inherit the floor even if receipt retention then fails.
        completed = self.clock.now()
        elapsed = self.monotonic()
        request["exchange_completed_at"] = completed.isoformat()
        spacing = max(10, self.crawl_delay, self.config.host(HOST).min_gap_seconds)
        self.next_dispatch_monotonic = elapsed + spacing
        request["exchange_completed_elapsed_seconds"] = elapsed - self.monotonic_origin
        request["next_dispatch_not_before_elapsed_seconds"] = self.next_dispatch_monotonic - self.monotonic_origin
        until = completed + timedelta(seconds=spacing)
        row = self.conn.execute("SELECT next_allowed_at FROM hosts WHERE host=?", (HOST,)).fetchone()
        if row and row[0]:
            until = max(until, datetime.fromisoformat(row[0]))
        self.conn.execute("UPDATE hosts SET next_allowed_at=? WHERE host=?", (until.isoformat(), HOST))
        self.conn.commit()
        request["next_dispatch_not_before"] = until.isoformat()
        request["spacing_basis"] = "closed_exchange_completion_floor"

    def _wait_completion_floor(self) -> None:
        # UTC remains the shared durable gate. A process-local monotonic floor
        # independently prevents forward wall-clock adjustments shortening gaps.
        while (remaining := self.next_dispatch_monotonic - self.monotonic()) > 0:
            self._check()
            self.clock.sleep(min(1, remaining, (self.deadline - self.clock.now()).total_seconds()))

    def _exchange(self, client: httpx.Client, url: str, purpose: str) -> httpx.Response:
        self._check()
        self._wait_completion_floor()
        while True:
            self._check()
            grant = self.gate.acquire(HOST, source=self.source, crawl_delay=self.crawl_delay)
            if isinstance(grant, Paused):
                raise FixtureStopped(f"shared gate: {grant.reason}")
            if not isinstance(grant, Wait):
                break
            self.clock.sleep(
                min(1, grant.seconds, (self.deadline - self.clock.now()).total_seconds())
            )
        day = self.clock.now().date().isoformat()
        reserved = self._reservation(day)
        request: dict[str, Any] = {
            "url": url,
            "purpose": purpose,
            "issued_at": self.clock.now().isoformat(),
            "request_day": day,
            "reserved_bytes": reserved,
            "complete": False,
        }
        self.receipt["requests"].append(request)
        self._save()
        body = bytearray()
        outcome = Classification(Outcome.INVALID)
        limited = False
        try:
            client.cookies.clear()
            timeout = min(
                120 if purpose == "cdx" else 30, (self.deadline - self.clock.now()).total_seconds()
            )
            with client.stream("GET", url, timeout=timeout) as streamed:
                request["http_status"] = streamed.status_code
                request["headers"] = dict(streamed.headers)
                if streamed.headers.get("content-encoding", "identity").lower() != "identity":
                    raise FixtureStopped(
                        "unexpected content encoding; bounded raw fixture required"
                    )
                for chunk in streamed.iter_raw(chunk_size=min(65536, reserved)):
                    if self.clock.now() >= self.deadline:
                        raise FixtureStopped("elapsed-time ceiling or execution window expired")
                    remaining = reserved - len(body)
                    body.extend(chunk[:remaining])
                    if len(body) >= reserved:
                        limited = True
                        break
                request["complete"] = not limited
            response = httpx.Response(
                request["http_status"],
                headers=request["headers"],
                content=bytes(body),
                request=httpx.Request("GET", url),
            )
            outcome = classify(response, HTTP_CONTROL, None, self.clock.now())
            if limited:
                raise FixtureStopped(
                    "response or total byte ceiling; retained prefix is incomplete"
                )
            return response
        except httpx.HTTPError as exc:
            outcome = classify(exc, HTTP_CONTROL, None, self.clock.now())
            raise FixtureStopped(f"transport failure: {type(exc).__name__}; no retry") from exc
        finally:
            self._anchor_completion(request)
            sha = digest(bytes(body))
            durable_write(self.output / "bodies" / sha, bytes(body))
            request.update(
                {
                    "body_sha256": sha,
                    "body_bytes": len(body),
                    "classification": outcome.outcome.value,
                }
            )
            self.receipt["received_bytes"] += len(body)
            self.gate.release(HOST, outcome, body_bytes=len(body) - reserved, request_day=day)
            client.cookies.clear()
            self._save()

    def _request(self, client: httpx.Client, initial: str, purpose: str) -> httpx.Response:
        if initial not in self.allowed_urls:
            raise FixtureStopped("URL is absent from the exact fixture allowlist")
        url = initial
        for hop in range(self.limits["max_archive_redirects_per_body"] + 1):
            if not archive_redirect(initial, url):
                raise FixtureStopped("URL outside exact archive resource allowlist")
            if purpose != "robots":
                self._robots(client, url)
            response = self._exchange(client, url, purpose)
            outcome = classify(response, HTTP_CONTROL, None, self.clock.now()).outcome
            if outcome == Outcome.REDIRECT:
                next_url = urljoin(url, response.headers.get("location", ""))
                if (
                    not response.headers.get("location")
                    or hop >= self.limits["max_archive_redirects_per_body"]
                    or not archive_redirect(initial, next_url)
                ):
                    raise FixtureStopped("redirect outside authorized resource or redirect ceiling")
                url = next_url
                continue
            if purpose == "robots" and response.status_code in (404, 410):
                return response
            if outcome != Outcome.OK:
                raise FixtureStopped(f"HTTP {response.status_code}: {outcome.value}; no retry")
            return response
        raise AssertionError("unreachable")

    def _robots(self, client: httpx.Client, url: str) -> None:
        if not self.robots_ready:
            row = self.conn.execute(
                "SELECT robots_sha256,robots_fetched_at,robots_status FROM hosts WHERE host=?",
                (HOST,),
            ).fetchone()
            cached = bool(
                row
                and row[0]
                and row[1]
                and datetime.fromisoformat(row[1]) <= self.clock.now()
                and datetime.fromisoformat(row[1]) + timedelta(days=1) > self.clock.now()
            )
            if cached:
                assert row is not None
                status, body = int(row[2]), Archive(self.state).read_body(row[0])
                sha = digest(body)
                durable_write(self.output / "bodies" / sha, body)
                self.receipt["cached_robots"] = {
                    "body_sha256": sha,
                    "status": status,
                    "fetched_at": row[1],
                }
            else:
                response = self._request(client, f"https://{HOST}/robots.txt", "robots")
                status, body = response.status_code, response.content
            if status == 200:
                self.rules = Protego.parse(body.decode("utf-8", errors="replace"))
                self.crawl_delay = float(self.rules.crawl_delay("swingset") or 0)
                # The gate already charged its ordinary floor for the robots request.
                # Conservatively wait the newly learned crawl delay before the next request.
                if not cached and self.crawl_delay:
                    if self.clock.now() + timedelta(seconds=self.crawl_delay) >= self.deadline:
                        raise FixtureStopped("robots crawl delay exceeds deadline")
                    self.clock.sleep(self.crawl_delay)
            elif status not in (404, 410):
                raise FixtureStopped("robots policy unavailable")
            self.robots_ready = True
        if self.rules and not self.rules.can_fetch(url, "swingset"):
            raise FixtureStopped("robots disallow")

    def run(self) -> dict[str, Any]:
        self.output.mkdir(parents=True, exist_ok=False)
        durable_write(self.output / "authorization.json", canonical(self.authorization))
        durable_write(self.output / "manifest.json", canonical(self.manifest))
        self._save()
        try:
            with httpx.Client(
                transport=DispatchReceiptTransport(self, self.transport or httpx.HTTPTransport(trust_env=False)),
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
            ) as client:
                for target in self.manifest["targets"]:
                    self.source = target["source"]
                    response = self._request(client, target["replay_url"], target["id"])
                    self.receipt["targets"].append(
                        {
                            "id": target["id"],
                            "original_url": target["original_url"],
                            "requested_archive_url": target["replay_url"],
                            "archive_url": str(response.url),
                            "captured_at": captured_at(dict(response.headers), str(response.url)),
                            "body_sha256": digest(response.content),
                            "interpretation": "unreviewed_quarantine_body",
                        }
                    )
                    self._save()
                self.receipt["status"] = "captured_pending_independent_review"
        except (FixtureStopped, ValueError, OSError) as exc:
            self.receipt["status"] = "stopped_incomplete"
            self.receipt["stop_reason"] = str(exc)
        finally:
            self.receipt["finished_at"] = self.clock.now().isoformat()
            self._save()
        return self.receipt
