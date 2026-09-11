"""HTTP requests, redirects, extraction and snapshot acceptance through one gate."""

import json
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

import httpx

from swingset import __version__
from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.archive import Archive, canonical, digest
from swingset.fetch.classify import Classification, Outcome, classify
from swingset.fetch.politeness import Gate, Grant, Paused, Wait
from swingset.fetch.robots import Robots
from swingset.model.ids import snapshot_id as make_snapshot_id
from swingset.sources.base import ExtractError, PageKind

USER_AGENT = f"swingset/{__version__} (+https://github.com/skeswa/swingset)"


class FetchDeferred(RuntimeError):
    """No request was issued; do not turn a gate stop into cached robots failure."""


@dataclass(frozen=True)
class FetchResult:
    classification: Classification
    snapshot_id: str | None = None
    changed: bool = False
    body_bytes: int = 0
    nonce_unchanged: bool = False
    skipped: str | None = None


class FetchClient:
    def __init__(
        self,
        connection: sqlite3.Connection,
        config: Config,
        clock: Clock,
        archive: Archive,
        *,
        transport: httpx.BaseTransport | None = None,
        random_value: Callable[[], float] = random.random,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        self.connection = connection
        self.config = config
        self.clock = clock
        self.archive = archive
        self.gate = Gate(connection, config, clock)
        self.robots = Robots(connection, archive, clock)
        self.http = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=30,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )
        self.random_value = random_value
        self.should_stop = should_stop

    def close(self) -> None:
        self.http.close()

    def _request(
        self,
        method: str,
        url: str,
        source: str,
        page_kind: PageKind,
        watch: object,
        headers: dict[str, str],
        form: dict[str, str] | None,
        *,
        robots: bool = False,
        deadline: datetime | None = None,
        sweep: bool = False,
    ) -> tuple[httpx.Response | None, Classification, str | None]:
        for hop in range(4):
            host = urlsplit(url).hostname or ""
            if urlsplit(url).scheme not in ("http", "https") or not host:
                return None, Classification(Outcome.INVALID), "invalid redirect URL"
            crawl_delay = 0.0
            if not robots:

                def get_robots(robots_url: str) -> httpx.Response | None:
                    response, _, deferred = self._request(
                        "GET",
                        robots_url,
                        source,
                        page_kind,
                        watch,
                        {},
                        None,
                        robots=True,
                        deadline=deadline,
                        sweep=sweep,
                    )
                    if deferred:
                        raise FetchDeferred(deferred)
                    return response

                try:
                    rules = self.robots.policy(url, get_robots)
                except FetchDeferred as exc:
                    return None, Classification(Outcome.INVALID), str(exc)
                if not rules.allowed:
                    return None, Classification(Outcome.INVALID), "robots disallow"
                crawl_delay = rules.crawl_delay
            response: httpx.Response | None = None
            outcome = Classification(Outcome.INVALID)
            for attempt in range(4):
                while True:
                    if self.should_stop() or (
                        deadline is not None and self.clock.now() >= deadline
                    ):
                        return None, outcome, "budget or stop"
                    grant = self.gate.acquire(
                        host, source=source, crawl_delay=crawl_delay, sweep=sweep
                    )
                    if isinstance(grant, Paused):
                        return None, outcome, grant.reason
                    if isinstance(grant, Wait):
                        if (
                            deadline is not None
                            and (deadline - self.clock.now()).total_seconds() < grant.seconds
                        ):
                            return None, outcome, "budget"
                        self.clock.sleep(min(grant.seconds, 1))
                        continue
                    assert isinstance(grant, Grant)
                    break
                day = self.clock.now().date().isoformat()
                try:
                    # httpx may receive Set-Cookie, but no request uses its cookie jar.
                    self.http.cookies.clear()
                    response = self.http.request(method, url, headers=headers, data=form)
                    self.http.cookies.clear()
                    outcome = classify(response, page_kind, watch, self.clock.now())
                    if (
                        robots
                        and 400 <= response.status_code < 500
                        and not any(
                            marker in response.content.lower()
                            for marker in (b"cf-chl", b"just a moment", b"challenge-platform")
                        )
                    ):
                        outcome = Classification(Outcome.EXPECTED_UNAVAILABLE)
                except httpx.HTTPError as exc:
                    outcome = classify(exc, page_kind, watch, self.clock.now())
                body_bytes = len(response.content) if response is not None else 0
                retry_candidate = outcome.outcome == Outcome.SERVER_ERROR and attempt < 3
                delay = self.random_value() * 10 * 2**attempt if retry_candidate else 0.0
                retrying = (
                    retry_candidate
                    and not self.should_stop()
                    and not (
                        deadline is not None
                        and (deadline - self.clock.now()).total_seconds() < delay
                    )
                )
                self.gate.release(
                    host,
                    Classification(Outcome.INVALID) if retrying else outcome,
                    body_bytes=body_bytes,
                    request_day=day,
                )
                if not retry_candidate or not retrying:
                    break
                self.clock.sleep(delay)
            if outcome.outcome != Outcome.REDIRECT or response is None:
                return response, outcome, None
            location = response.headers.get("location")
            if not location or hop == 3:
                return (
                    response,
                    Classification(Outcome.INVALID),
                    "redirect limit or missing Location",
                )
            url = urljoin(url, location)
            headers = {}  # Validators belong to the prior resource.
            if response.status_code == 303 or (
                response.status_code in (301, 302) and method == "POST"
            ):
                method, form = "GET", None
        raise AssertionError("unreachable redirect loop")

    def fetch(
        self,
        watch_id: str,
        page_kind: PageKind,
        run_id: str,
        *,
        deadline: datetime | None = None,
        sweep: bool = False,
    ) -> FetchResult:
        conn = self.connection
        row = conn.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown watch {watch_id}")
        watch = SimpleNamespace(**dict(row))
        if not self.config.enabled(watch.source):
            return FetchResult(Classification(Outcome.INVALID), skipped="source disabled")
        registry_probe = watch.source == "wsdc_registry" and watch.notes == "probe"
        headers: dict[str, str] = {}
        if watch.etag:
            headers["If-None-Match"] = watch.etag
        if watch.last_modified:
            headers["If-Modified-Since"] = watch.last_modified
        elif watch.source == "scoringdance":
            headers["If-Modified-Since"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        form = json.loads(watch.form) if watch.form else None
        response, outcome, skipped = self._request(
            watch.method,
            watch.url,
            watch.source,
            page_kind,
            watch,
            headers,
            form,
            deadline=deadline,
            sweep=sweep,
        )
        if skipped:
            return FetchResult(outcome, skipped=skipped)
        fetched_at = self.clock.now()
        now = fetched_at.isoformat()
        if response is None:
            return FetchResult(outcome)
        body = response.content
        reused_probe_body = False
        failed_probe_cache = False
        if registry_probe and outcome.outcome == Outcome.NOT_MODIFIED:
            try:
                if watch.body_sha256 is None:
                    raise ValueError("probe 304 has no cached representation")
                body = self.archive.read_body(str(watch.body_sha256))
                reused_probe_body = True
            except (EOFError, FileNotFoundError, OSError, ValueError):
                # A 304 proves freshness only when its prior representation is intact.
                failed_probe_cache = True
                outcome = Classification(Outcome.INVALID)
                if watch.body_sha256 is not None:
                    self.archive.blob_path(str(watch.body_sha256)).unlink(missing_ok=True)
        body_sha = digest(body)
        extract_sha: str | None = None
        extract_status = "pending"
        changed = outcome.outcome == Outcome.OK
        nonce_unchanged = False
        if (
            changed
            and body_sha == watch.body_sha256
            and str(watch.extract_version) == str(page_kind.EXTRACT_VERSION)
        ):
            changed = False
        elif changed:
            try:
                extracted = page_kind.extract(body)
                fingerprint = digest(canonical(extracted))
                if (
                    page_kind.change_mode == "extract"
                    and fingerprint == watch.fingerprint
                    and str(watch.extract_version) == str(page_kind.EXTRACT_VERSION)
                ):
                    changed, nonce_unchanged = False, True
                else:
                    extract_sha = self.archive.store_extract(extracted)
                    extract_status = "ok"
            except ExtractError:
                extract_status = "failed"
        # Archive failures for diagnosis too, but never replace observations with HTTP error bodies.
        fresh_probe_evidence = registry_probe and (
            outcome.outcome == Outcome.OK or reused_probe_body
        )
        archive_response = (
            changed
            or fresh_probe_evidence
            or outcome.outcome not in (Outcome.OK, Outcome.NOT_MODIFIED)
        ) and not failed_probe_cache
        queue_parse = (
            changed
            or fresh_probe_evidence
            or (
                watch.source == "wsdc_registry"
                and outcome.outcome == Outcome.INVALID
                and not failed_probe_cache
            )
        )
        snapshot_id: str | None = None
        if archive_response:
            self.archive.store_body(body)
            snapshot_id = make_snapshot_id(fetched_at, body_sha)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE watches SET last_checked_at=?,ever_ok=CASE WHEN ? THEN 1 ELSE ever_ok END WHERE watch_id=?",
                (now, response.status_code == 200, watch_id),
            )
            if outcome.outcome == Outcome.OK:
                conn.execute(
                    "UPDATE watches SET etag=?,last_modified=? WHERE watch_id=?",
                    (response.headers.get("etag"), response.headers.get("last-modified"), watch_id),
                )
            if failed_probe_cache:
                conn.execute(
                    "UPDATE watches SET etag=NULL,last_modified=NULL WHERE watch_id=?", (watch_id,)
                )
            if snapshot_id:
                conn.execute(
                    """INSERT INTO snapshots(snapshot_id,watch_id,method,url,form,fetched_at,http_status,
                    etag,last_modified,content_type,body_sha256,body_bytes,content_changed,run_id,via,headers_json,
                    classification,extract_status,extract_sha256,parse_status,extract_version,parser_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot_id,
                        watch_id,
                        watch.method,
                        watch.url,
                        watch.form,
                        now,
                        response.status_code,
                        response.headers.get("etag"),
                        response.headers.get("last-modified"),
                        response.headers.get("content-type"),
                        body_sha,
                        len(response.content),
                        changed,
                        run_id,
                        "origin",
                        json.dumps(dict(response.headers)),
                        outcome.outcome.value,
                        extract_status,
                        extract_sha,
                        "pending" if queue_parse else "failed",
                        str(page_kind.EXTRACT_VERSION),
                        str(page_kind.PARSER_VERSION),
                    ),
                )
                conn.execute("UPDATE revisions SET value=value+1 WHERE name='snapshots'")
                if queue_parse:
                    conn.execute(
                        "INSERT OR IGNORE INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES ('parse','snapshot',?,?)",
                        (snapshot_id, now),
                    )
            if changed:
                conn.execute(
                    "UPDATE watches SET body_sha256=?,last_changed_at=?,unchanged_streak=0 WHERE watch_id=?",
                    (body_sha, now, watch_id),
                )
                if extract_status == "ok":
                    conn.execute(
                        "UPDATE watches SET fingerprint=?,extract_version=? WHERE watch_id=?",
                        (extract_sha, str(page_kind.EXTRACT_VERSION), watch_id),
                    )
            elif outcome.outcome in (Outcome.OK, Outcome.NOT_MODIFIED):
                conn.execute(
                    "UPDATE watches SET unchanged_streak=unchanged_streak+1 WHERE watch_id=?",
                    (watch_id,),
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return FetchResult(outcome, snapshot_id, changed, len(response.content), nonce_unchanged)
