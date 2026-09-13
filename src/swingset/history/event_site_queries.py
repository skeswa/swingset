"""Explicit, disabled-by-default filtered CDX execution; no discovered URL is fetched."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from swingset.fetch.archive import canonical, digest
from swingset.fetch.cdx import request_page, response_receipt
from swingset.fetch.classify import Outcome
from swingset.fetch.client import FetchClient
from swingset.fetch.limits import RequestContext
from swingset.fetch.wayback import query_due
from swingset.history.event_site_intake import (
    FORMAT,
    MAX_TOTAL_BYTES,
    _body,
    _file,
    _gate,
    _retain,
    ingest,
    page_captures,
    page_count,
)
from swingset.history.event_sites import MAX_RECEIPTS, Site, query_url, validate_query
from swingset.schedule.fairness import WatchChoice, servicing
from swingset.state.controls import ActionScope, ControlPaused, operation


@dataclass(frozen=True)
class Recipe:
    site: Site
    year: int
    mime: str

    def value(self) -> dict[str, object]:
        return {
            "source": "event_sites",
            "event_id": self.site.event_id,
            "event_year": self.site.year,
            "website": self.site.website,
            "probe_url": query_url(self.site, self.year, self.mime, page=None),
        }

    @property
    def key(self) -> str:
        return digest(canonical(self.value()))


@dataclass(frozen=True)
class QueryResult:
    query_id: str | None
    page_receipts: int
    complete: bool
    admitted_attempts: int
    uncertain_admissions: int
    wire_bytes: int
    decoded_bytes: int
    skipped: str | None = None
    deadline_mode: str = "cooperative; coordinator hard timeout required for real execution"


def _intent(client: FetchClient, recipe: Recipe) -> tuple[str | None, bool]:
    root = client.archive.state_dir
    directory = root / "history" / "event-site-queries" / recipe.key
    paths = sorted(islice(directory.glob("generation-*.json"), 65))
    if len(paths) > 64:
        raise ValueError("event-site query generation inventory exceeds bound")
    intents = []
    for path in paths:
        value = json.loads(_file(root, path.relative_to(root), 128 * 1024))
        identifier = str(value["query_id"])
        if (
            len(identifier) != 64
            or any(c not in "0123456789abcdef" for c in identifier)
            or path.name != "generation-" + identifier + ".json"
            or value["recipe"] != recipe.value()
        ):
            raise ValueError("query intent identity or recipe mismatch")
        started = datetime.fromisoformat(value["started_at"])
        if started.tzinfo is None or started > client.clock.now():
            raise ValueError("query intent has invalid time")
        intents.append((started, identifier))
    if not intents:
        return None, False
    identifier = max(intents)[1]
    row = client.connection.execute(
        "SELECT completed_at FROM archive_queries WHERE query_id=? AND source='event_sites'",
        (identifier,),
    ).fetchone()
    complete = row is not None and row[0] is not None
    if complete and query_due(recipe.year, row[0], client.clock.now()):
        return None, False
    return identifier, complete


def _receipt(
    client: FetchClient, recipe: Recipe, identifier: str, page: str
) -> tuple[bytes, bytes]:
    root = client.archive.state_dir
    raw = _file(root, Path("archive-cdx") / identifier / (page + ".json"), 128 * 1024)
    value = json.loads(raw)
    if (
        value["query_id"] != identifier
        or str(value["page"]) != page
        or value["http_status"] != 200
        or value.get("complete") is False
        or value.get("failure_reason")
    ):
        raise ValueError("retained query receipt is not complete successful evidence")
    mime = validate_query(
        value["url"], recipe.site, recipe.year, page=None if page == "probe" else int(page)
    )
    if mime != recipe.mime:
        raise ValueError("retained query filters do not match recipe")
    fetched = datetime.fromisoformat(value["fetched_at"])
    if fetched.tzinfo is None or fetched > client.clock.now():
        raise ValueError("retained query receipt has invalid time")
    body = _body(client.archive, value["body_sha256"])
    page_count(body) if page == "probe" else page_captures(
        body, recipe.site, recipe.year, recipe.mime, fetched
    )
    return raw, body


def execute(
    client: FetchClient,
    site: Site,
    *,
    year: int,
    mime: str,
    run_id: str,
    execute_requests: bool = False,
    max_attempts: int = 8,
    wall_seconds: float = 60,
    deadline: datetime | None = None,
) -> QueryResult:
    """One filter/year recipe per invocation, charged as priority-six old work.

    Caller owns the state lock. Intent/probe/pages survive interruption; successful
    receipts are revalidated and reused. No cycle schedules this entrypoint and
    absent event_sites configuration leaves it disabled.
    """
    if not 1 <= max_attempts <= 8 or not 0 < wall_seconds <= 60:
        raise ValueError("event-site CDX execution allows at most eight attempts and 60 seconds")
    recipe = Recipe(site, year, mime)
    recipe.value()  # Validate scope before constructing any filesystem name.
    scope = ActionScope(
        all_sources=True, kinds=frozenset({"source_event_mapping"}), host="web.archive.org"
    )
    active_page: str = "probe"

    def gate(conn: sqlite3.Connection, url: str) -> str | None:
        try:
            if not client.config.enabled("event_sites"):
                return "event_sites disabled"
            _gate(conn, site, client.clock.now(), client.config.history_start)
            parsed = urlsplit(url)
            if (
                parsed.scheme == "https"
                and parsed.hostname == "web.archive.org"
                and parsed.port in {None, 443}
                and not parsed.username
                and not parsed.password
                and parsed.path == "/robots.txt"
                and not parsed.query
                and not parsed.fragment
            ):
                return None
            selected = validate_query(
                url, site, year, page=None if active_page == "probe" else int(active_page)
            )
            if selected != mime:
                return "event-site query filter changed"
        except (ValueError, TypeError, KeyError) as exc:
            return str(exc)
        return None

    started = client.clock.now()
    until = min(
        deadline or started + timedelta(seconds=wall_seconds),
        started + timedelta(seconds=wall_seconds),
    )
    remaining_seconds = max(0.0, (until - started).total_seconds())
    context = RequestContext(
        gate, until, time.monotonic() + remaining_seconds, max_attempts=max_attempts
    )
    identifier = None
    records: list[dict[str, str]] = []
    complete = False

    def result(reason: str | None = None) -> QueryResult:
        return QueryResult(
            identifier,
            max(0, len(records) - 1),
            complete,
            context.admitted_attempts,
            context.uncertain_admissions,
            context.wire_bytes,
            context.decoded_bytes,
            reason,
        )

    if not client.config.enabled("event_sites"):
        return result("event_sites disabled")
    if not execute_requests:
        return result("dry run; no requests or intent writes")
    if client.archive.state_dir.resolve() != client.database.state_dir.resolve():
        raise ValueError("event-site query archive must belong to the locked state")
    policy = client.config.host("web.archive.org")
    if policy.min_gap_seconds < 10 or policy.daily_request_budget > 200:
        raise ValueError(
            "event-site queries require the Archive ten-second floor and <=200 daily requests"
        )
    if client.connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
        raise ValueError("query execution needs an existing coordinator run")
    if reason := context.check(
        client.connection, query_url(site, year, mime, page=None), client.clock.now()
    ):
        return result(reason)

    def diagnostic(response: httpx.Response, outcome: str) -> None:
        if outcome == Outcome.OK.value and not response.extensions.get("swingset_limit"):
            return
        assert identifier is not None
        value = json.loads(response_receipt(client, identifier, active_page, response))
        value.update(
            complete=False, failure_reason=response.extensions.get("swingset_limit") or outcome
        )
        _retain(
            client.archive.state_dir
            / "archive-cdx"
            / identifier
            / "diagnostics"
            / (uuid4().hex + ".json"),
            canonical(value),
        )
        response.extensions["swingset_diagnostic_retained"] = True

    context.received = diagnostic
    choice = WatchChoice(
        "event_sites:" + recipe.key,
        None,
        "web.archive.org",
        "old",
        scope,
        client.clock.now(),
        archive=True,
        history=True,
    )
    try:
        with (
            operation(
                client.database,
                action_id="event_site_cdx_" + uuid4().hex,
                action_kind="acquisition",
                scope=scope,
                clock=client.clock,
                run_id=run_id,
            ),
            servicing(choice, run_id=run_id),
        ):
            identifier, already_complete = _intent(client, recipe)
            if already_complete:
                # Do not report a retained query complete without its exact probe/pages.
                assert identifier is not None
            if identifier is None:
                identifier = digest(
                    canonical(
                        {
                            "recipe": recipe.value(),
                            "started_at": client.clock.now().isoformat(),
                            "nonce": uuid4().hex,
                        }
                    )
                )
                intent = {
                    "query_id": identifier,
                    "recipe": recipe.value(),
                    "started_at": client.clock.now().isoformat(),
                }
                _retain(
                    client.archive.state_dir
                    / "history"
                    / "event-site-queries"
                    / recipe.key
                    / ("generation-" + identifier + ".json"),
                    canonical(intent),
                )
            total_bytes = 0
            pages: int | None = None
            stopped = None
            for ordinal in range(MAX_RECEIPTS + 1):
                active_page = "probe" if ordinal == 0 else str(ordinal - 1)
                if pages is not None and ordinal > pages:
                    break
                path = (
                    client.archive.state_dir / "archive-cdx" / identifier / (active_page + ".json")
                )
                if not path.exists():
                    if already_complete:
                        raise ValueError("completed query lost a required retained receipt")
                    response, outcome, skipped = request_page(
                        client,
                        source="event_sites",
                        url=query_url(site, year, mime, page=None if ordinal == 0 else ordinal - 1),
                        deadline=until,
                        context=context,
                    )
                    if response is None:
                        stopped = skipped or outcome.outcome.value
                        break
                    raw = response_receipt(client, identifier, active_page, response)
                    invalid = skipped or (
                        outcome.outcome.value if outcome.outcome != Outcome.OK else None
                    )
                    if not invalid:
                        try:
                            page_count(response.content) if ordinal == 0 else page_captures(
                                response.content, site, year, mime, client.clock.now()
                            )
                        except ValueError as exc:
                            invalid = str(exc)
                    if invalid:
                        value = json.loads(raw)
                        value.update(complete=False, failure_reason=invalid)
                        if not response.extensions.get("swingset_diagnostic_retained"):
                            _retain(
                                client.archive.state_dir
                                / "archive-cdx"
                                / identifier
                                / "diagnostics"
                                / (uuid4().hex + ".json"),
                                canonical(value),
                            )
                        stopped = invalid
                        break
                    _retain(path, raw)
                raw, body = _receipt(client, recipe, identifier, active_page)
                total_bytes += len(body)
                if total_bytes > MAX_TOTAL_BYTES:
                    raise ValueError("retained query body cohort exceeds bound")
                if ordinal == 0:
                    pages = page_count(body)
                records.append({"page": active_page, "sha256": digest(raw)})
            # A retained successful page is not reacquired if a pause/deadline
            # prevented its metadata commit. Next invocation reuses these files.
            remaining = min(
                (until - client.clock.now()).total_seconds(),
                context.monotonic_deadline - time.monotonic(),
            )
            if records and remaining > 0:
                manifest = {
                    "format": FORMAT,
                    "event_id": site.event_id,
                    "year": site.year,
                    "website": site.website,
                    "queries": [{"query_id": identifier, "receipts": records}],
                }
                raw = canonical(manifest)
                relative = (
                    Path("history")
                    / "event-site-queries"
                    / recipe.key
                    / ("package-" + digest(raw) + ".json")
                )
                _retain(client.archive.state_dir / relative, raw)
                ingested = ingest(
                    client.database,
                    site,
                    directory=client.archive.state_dir,
                    manifest_sha256=digest(raw),
                    manifest_path=relative,
                    now=client.clock.now(),
                    dry_run=False,
                    history_start=client.config.history_start,
                    wall_seconds=min(60, remaining),
                )
                complete = bool(ingested.complete_queries)
            return result(stopped or ("query not due" if already_complete else None))
    except ControlPaused:
        return result("operator pause")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return result(str(exc))
