"""HTTP requests, redirects, extraction and snapshot acceptance through one gate."""

import json
import random
import sqlite3
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx

from swingset import __version__
from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.archive import Archive, canonical, digest
from swingset.fetch.classify import Classification, Outcome, classify
from swingset.fetch.controls import issue, release
from swingset.fetch.limits import RequestContext
from swingset.fetch.politeness import Gate, Grant, Paused, Wait
from swingset.fetch.robots import Robots
from swingset.fetch.wayback import captured_at
from swingset.model.ids import snapshot_id as make_snapshot_id
from swingset.sources.base import (
    ExtractError,
    PageKind,
)
from swingset.state.control_scopes import for_watch
from swingset.state.controls import ControlPaused, operation
from swingset.state.db import Database
from swingset.state.verification import record_check

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
        database_file = next(
            row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"
        )
        state_dir = Path(database_file).parent if database_file else archive.state_dir
        self.database = Database(state_dir, connection, None)
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

    def _history_gate(self, watch: object, *, request_url: str | None = None) -> str | None:
        archive_url = getattr(watch, "archive_url", None)
        if not archive_url:
            from swingset.history.origin_dispatch import request_gate

            return request_gate(
                self.connection,
                watch,
                now=self.clock.now(),
                history_start=self.config.history_start,
                request_url=request_url,
            )
        from swingset.history.acquisition import archive_watch_gate

        return archive_watch_gate(
            self.connection,
            source=str(getattr(watch, "source", "")),
            source_ref=getattr(watch, "source_ref", None),
            page_kind=str(getattr(watch, "parser", "")),
            watch_kind=str(getattr(watch, "kind", "")),
            archive_url=str(archive_url),
        )

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
        timeout: float = 30,
        robots: bool = False,
        deadline: datetime | None = None,
        sweep: bool = False,
        context: RequestContext | None = None,
    ) -> tuple[httpx.Response | None, Classification, str | None]:
        if context and (reason := context.check(self.connection, url, self.clock.now())):
            return None, Classification(Outcome.INVALID), reason
        archive_request = urlsplit(url).hostname == "web.archive.org"
        for hop in range(4):
            if context and (reason := context.check(self.connection, url, self.clock.now())):
                return None, Classification(Outcome.INVALID), reason
            if reason := self._history_gate(watch, request_url=url):
                return None, Classification(Outcome.INVALID), reason
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
                        context=context,
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
                    if reason := self._history_gate(watch, request_url=url):
                        return None, outcome, reason
                    if self.should_stop() or (
                        deadline is not None and self.clock.now() >= deadline
                    ):
                        return None, outcome, "budget or stop"
                    grant, action_id = issue(
                        self.database,
                        self.gate,
                        self.clock,
                        host=host,
                        source=source,
                        watch=watch,
                        page_kind=page_kind.kind,
                        crawl_delay=crawl_delay,
                        sweep=sweep,
                        request_url=url,
                        context=context,
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
                    assert action_id is not None
                    break
                day = self.clock.now().date().isoformat()
                response, body_bytes = None, 0
                outcome = Classification(Outcome.INVALID)
                retrying = False
                try:
                    try:
                        # No request uses cookies from a previous response.
                        self.http.cookies.clear()
                        if context is None:
                            response = self.http.request(
                                method, url, headers=headers, data=form, timeout=timeout
                            )
                            body_bytes = len(response.content)
                        else:
                            bounded_timeout = max(
                                0.001,
                                min(
                                    timeout,
                                    (context.deadline - self.clock.now()).total_seconds(),
                                    context.monotonic_deadline - time.monotonic(),
                                ),
                            )
                            before_wire, before_decoded = context.wire_bytes, context.decoded_bytes
                            try:
                                response, body_bytes = context.read(
                                    self.http,
                                    method,
                                    url,
                                    headers=headers,
                                    form=form,
                                    timeout=bounded_timeout,
                                )
                            finally:
                                body_bytes = max(
                                    context.wire_bytes - before_wire,
                                    context.decoded_bytes - before_decoded,
                                )
                        outcome = (
                            Classification(Outcome.INVALID)
                            if response.extensions.get("swingset_limit")
                            else classify(response, page_kind, watch, self.clock.now())
                        )
                        if (
                            robots
                            and not response.extensions.get("swingset_limit")
                            and 400 <= response.status_code < 500
                            and not any(
                                marker in response.content.lower()
                                for marker in (b"cf-chl", b"just a moment", b"challenge-platform")
                            )
                        ):
                            outcome = Classification(Outcome.EXPECTED_UNAVAILABLE)
                    except httpx.HTTPError as exc:
                        outcome = classify(exc, page_kind, watch, self.clock.now())
                    if response is not None and context and context.received and not robots:
                        context.received(response, outcome.outcome.value)
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
                except BaseException:
                    outcome = Classification(Outcome.INVALID)
                    raise
                finally:
                    try:
                        self.http.cookies.clear()
                    finally:
                        release(
                            self.database,
                            self.gate,
                            self.clock,
                            action_id,
                            host,
                            Classification(Outcome.INVALID) if retrying else outcome,
                            body_bytes=body_bytes,
                            request_day=day,
                        )
                if not retry_candidate or not retrying:
                    break
                self.clock.sleep(delay)
            if outcome.outcome != Outcome.REDIRECT or response is None:
                limited = (
                    response.extensions.get("swingset_limit") if response is not None else None
                )
                return response, outcome, str(limited) if limited else None
            location = response.headers.get("location")
            if not location or hop == 3:
                return (
                    response,
                    Classification(Outcome.INVALID),
                    "redirect limit or missing Location",
                )
            if context:
                try:
                    next_url = urljoin(url, location)
                    reason = context.check(self.connection, next_url, self.clock.now())
                except ValueError:
                    reason = "invalid redirect URL"
                if reason:
                    return response, Classification(Outcome.INVALID), reason
                url = next_url
            else:
                url = urljoin(url, location)
            if archive_request and urlsplit(url).hostname != "web.archive.org":
                return (
                    response,
                    Classification(Outcome.INVALID),
                    "archive redirect leaves archive host",
                )
            headers = {}  # Validators belong to the prior resource.
            if response.status_code == 303 or (
                response.status_code in (301, 302) and method == "POST"
            ):
                method, form = "GET", None
        raise AssertionError("unreachable redirect loop")

    def index_archive(
        self, *, source: str, prefix: str, year: int, deadline: datetime | None = None
    ) -> int:
        if not self.config.enabled(source):
            return 0
        scope = for_watch(
            self.connection, source=source, watch_kind="index", host="web.archive.org"
        )
        try:
            with operation(
                self.database,
                action_id="cdx_" + uuid4().hex,
                action_kind="acquisition",
                scope=scope,
                clock=self.clock,
            ):
                return self._index_archive(
                    source=source, prefix=prefix, year=year, deadline=deadline
                )
        except ControlPaused:
            return 0

    def _index_archive(
        self, *, source: str, prefix: str, year: int, deadline: datetime | None = None
    ) -> int:
        from swingset.fetch.cdx import index_archive

        return index_archive(self, source=source, prefix=prefix, year=year, deadline=deadline)

    def _cdx_receipt(self, query_id: str, page: str, response: httpx.Response) -> None:
        from swingset.fetch.cdx import retain_response

        retain_response(self, query_id, page, response)

    def fetch(
        self,
        watch_id: str,
        page_kind: PageKind,
        run_id: str,
        *,
        deadline: datetime | None = None,
        sweep: bool = False,
    ) -> FetchResult:
        row = self.connection.execute(
            "SELECT * FROM watches WHERE watch_id=?", (watch_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown watch {watch_id}")
        if not self.config.enabled(row["source"]):
            return FetchResult(Classification(Outcome.INVALID), skipped="source disabled")
        if row["state"] == "sealed":
            return FetchResult(Classification(Outcome.INVALID), skipped="sealed")
        host = urlsplit(row["archive_url"] or row["url"]).hostname
        scope = for_watch(self.connection, source=row["source"], watch_id=watch_id, host=host)
        try:
            with operation(
                self.database,
                action_id="fetch_" + uuid4().hex,
                action_kind="fetch",
                scope=scope,
                clock=self.clock,
                run_id=run_id,
            ):
                return self._fetch(watch_id, page_kind, run_id, deadline=deadline, sweep=sweep)
        except ControlPaused:
            return FetchResult(Classification(Outcome.INVALID), skipped="operator")

    def _fetch(
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
        watch = SimpleNamespace(**dict(row), _origin_run_id=run_id)
        if not self.config.enabled(watch.source):
            return FetchResult(Classification(Outcome.INVALID), skipped="source disabled")
        if watch.state == "sealed":
            return FetchResult(Classification(Outcome.INVALID), skipped="sealed")
        via_archive = bool(watch.archive_url)
        if watch.source == "steprightsolutions" and not via_archive:
            return FetchResult(Classification(Outcome.INVALID), skipped="archive required")
        if reason := self._history_gate(watch):
            return FetchResult(Classification(Outcome.INVALID), skipped=reason)
        if via_archive:
            parsed_url = urlsplit(watch.archive_url)
            if parsed_url.scheme != "https" or parsed_url.hostname != "web.archive.org":
                raise ValueError("archive replay URL must use https://web.archive.org")
            if reason := self._history_gate(watch):
                return FetchResult(Classification(Outcome.INVALID), skipped=reason)
            prior = conn.execute(
                "SELECT 1 FROM snapshots WHERE url=? AND (archive_url=? OR requested_archive_url=?) AND http_status=200 AND classification='Ok'",
                (watch.url, watch.archive_url, watch.archive_url),
            ).fetchone()
            if prior:
                return FetchResult(
                    Classification(Outcome.INVALID), skipped="capture already archived"
                )
        registry = watch.source == "wsdc_registry"
        headers: dict[str, str] = {}
        if watch.etag:
            headers["If-None-Match"] = watch.etag
        if watch.last_modified:
            headers["If-Modified-Since"] = watch.last_modified
        elif watch.source == "scoringdance":
            headers["If-Modified-Since"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        if via_archive:
            headers = {}
        form = json.loads(watch.form) if watch.form else None
        response, outcome, skipped = self._request(
            watch.method,
            watch.archive_url or watch.url,
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
            if registry:
                with self.database.transaction():
                    conn.execute(
                        "UPDATE watches SET last_checked_at=? WHERE watch_id=?", (now, watch_id)
                    )
                    record_check(
                        conn,
                        self.archive,
                        watch_id=watch_id,
                        checked_at=now,
                        http_status=None,
                        body_sha256=None,
                        successful=False,
                        failure_reason="transport_failed",
                    )
            return FetchResult(outcome)
        capture_time = (
            captured_at(dict(response.headers), str(response.url)) if via_archive else now
        )
        etag = response.headers.get("x-archive-orig-etag" if via_archive else "etag")
        last_modified = response.headers.get(
            "x-archive-orig-last-modified" if via_archive else "last-modified"
        )
        body = response.content
        failed_cache = False
        cache_reason = "missing_content_reference"
        if registry and outcome.outcome == Outcome.NOT_MODIFIED:
            try:
                if watch.body_sha256 is None:
                    raise ValueError("probe 304 has no cached representation")
                body = self.archive.read_body(str(watch.body_sha256))
            except (EOFError, FileNotFoundError, OSError, ValueError, zlib.error) as exc:
                # Keep the required digest and damaged evidence for diagnosis.
                failed_cache = True
                cache_reason = (
                    "missing_artifact"
                    if isinstance(exc, FileNotFoundError)
                    else "corrupt_artifact"
                    if watch.body_sha256 is not None
                    else "missing_content_reference"
                )
                outcome = Classification(Outcome.INVALID)
        body_sha = digest(body)
        extract_sha: str | None = None
        extract_status = "pending"
        changed = outcome.outcome == Outcome.OK
        nonce_unchanged = False
        if (
            changed
            and not via_archive
            and body_sha == watch.body_sha256
            and str(watch.extract_version) == str(page_kind.EXTRACT_VERSION)
        ):
            changed = False
        elif changed:
            try:
                extracted = page_kind.extract(body)
                fingerprint = digest(canonical(extracted))
                if (
                    not via_archive
                    and page_kind.change_mode == "extract"
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
        archive_response = (
            changed or outcome.outcome not in (Outcome.OK, Outcome.NOT_MODIFIED)
        ) and not failed_cache
        queue_parse = changed or (
            registry and outcome.outcome == Outcome.INVALID and not failed_cache
        )
        snapshot_id: str | None = None
        if archive_response:
            self.archive.store_body(body)
            snapshot_id = make_snapshot_id(fetched_at, body_sha, watch_key=watch_id)
        with self.database.transaction():
            conn.execute(
                "UPDATE watches SET last_checked_at=?,ever_ok=CASE WHEN ? THEN 1 ELSE ever_ok END WHERE watch_id=?",
                (now, response.status_code == 200, watch_id),
            )
            if outcome.outcome == Outcome.OK:
                conn.execute(
                    "UPDATE watches SET etag=?,last_modified=? WHERE watch_id=?",
                    (etag, last_modified, watch_id),
                )
            if failed_cache:
                conn.execute(
                    "UPDATE watches SET body_sha256=NULL,fingerprint=NULL,etag=NULL,last_modified=NULL WHERE watch_id=?",
                    (watch_id,),
                )
            if snapshot_id:
                conn.execute(
                    """INSERT INTO snapshots(snapshot_id,watch_id,method,url,form,fetched_at,http_status,
                    etag,last_modified,content_type,body_sha256,body_bytes,content_changed,run_id,via,headers_json,
                    classification,extract_status,extract_sha256,parse_status,extract_version,parser_version,captured_at,observed_at,archive_url,requested_archive_url)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot_id,
                        watch_id,
                        watch.method,
                        watch.url,
                        watch.form,
                        now,
                        response.status_code,
                        etag,
                        last_modified,
                        response.headers.get("content-type"),
                        body_sha,
                        len(response.content),
                        changed,
                        run_id,
                        "wayback" if via_archive else "origin",
                        json.dumps(dict(response.headers)),
                        outcome.outcome.value,
                        extract_status,
                        extract_sha,
                        "pending" if queue_parse else "failed",
                        str(page_kind.EXTRACT_VERSION),
                        str(page_kind.PARSER_VERSION),
                        capture_time,
                        capture_time,
                        str(response.url) if via_archive else None,
                        watch.archive_url if via_archive else None,
                    ),
                )
                conn.execute("UPDATE revisions SET value=value+1 WHERE name='snapshots'")
                if outcome.outcome in (Outcome.OK, Outcome.NOT_MODIFIED):
                    from swingset.state.event_progress import acquired

                    acquired(
                        conn, snapshot_id=snapshot_id, source=watch.source, parser=watch.parser
                    )
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
            if registry:
                record_check(
                    conn,
                    self.archive,
                    watch_id=watch_id,
                    checked_at=now,
                    http_status=response.status_code,
                    body_sha256=watch.body_sha256 if failed_cache else body_sha,
                    successful=outcome.outcome in (Outcome.OK, Outcome.NOT_MODIFIED),
                    snapshot_id=snapshot_id,
                    failure_reason=cache_reason if failed_cache else "content_check_failed",
                )
        return FetchResult(outcome, snapshot_id, changed, len(response.content), nonce_unchanged)
