"""Shared CDX request/receipt transport and the existing platform query workflow."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx

from swingset.fetch.archive import canonical, durable_write
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.limits import RequestContext
from swingset.sources.base import ChangeMode, ExtractError, JsonValue, ParseContext, ParseResult

if TYPE_CHECKING:
    from swingset.fetch.client import FetchClient


def request_page(
    client: FetchClient,
    *,
    source: str,
    url: str,
    deadline: datetime | None = None,
    context: RequestContext | None = None,
) -> tuple[httpx.Response | None, Classification, str | None]:
    return client._request(
        "GET",
        url,
        source,
        CDXPage(),
        SimpleNamespace(ever_ok=False, source=source, kind="index"),
        {},
        None,
        deadline=deadline,
        timeout=120,
        context=context,
    )


def response_receipt(
    client: FetchClient, query_id: str, page: str, response: httpx.Response
) -> bytes:
    body_sha = client.archive.store_body(response.content)
    receipt = {
        "query_id": query_id,
        "page": page,
        "url": str(response.request.url),
        "fetched_at": client.clock.now().isoformat(),
        "http_status": response.status_code,
        "headers": response.extensions.get("original_headers", dict(response.headers)),
        "body_sha256": body_sha,
    }
    if response.extensions.get("swingset_limit"):
        receipt["complete"] = False
        receipt["failure_reason"] = response.extensions["swingset_limit"]
    return canonical(receipt)


def retain_response(
    client: FetchClient, query_id: str, page: str, response: httpx.Response
) -> None:
    durable_write(
        client.archive.state_dir / "archive-cdx" / query_id / (page + ".json"),
        response_receipt(client, query_id, page, response),
    )


def index_archive(
    client: FetchClient, *, source: str, prefix: str, year: int, deadline: datetime | None = None
) -> int:
    """Resume one bounded CDX query through the same robots and host gate."""
    from hashlib import sha256

    from swingset.fetch.wayback import cdx_url, query_due, store_cdx_page

    if source == "event_sites":
        raise ValueError("event_sites requires the filtered recipe executor")
    if not client.config.enabled(source):
        return 0
    conn = client.connection
    current = conn.execute(
        "SELECT * FROM archive_queries WHERE source=? AND prefix=? AND year=? ORDER BY started_at DESC,query_id DESC LIMIT 1",
        (source, prefix, year),
    ).fetchone()
    query_id = str(current["query_id"]) if current is not None else ""
    if current is not None and not query_due(year, current["completed_at"], client.clock.now()):
        return 0
    if current is None or current["completed_at"] is not None:
        query_id = sha256(
            f"{source}\0{prefix}\0{year}\0{client.clock.now().isoformat()}".encode()
        ).hexdigest()
        response, outcome, skipped = request_page(
            client, source=source, url=cdx_url(prefix, year), deadline=deadline
        )
        if skipped or response is None or outcome.outcome != Outcome.OK:
            return 0
        retain_response(client, query_id, "probe", response)
        try:
            value = response.json()
            pages = 0 if value == [] else int(value["pages"] if isinstance(value, dict) else value)
            if pages < 0:
                raise ValueError("negative page count")
        except (ValueError, TypeError, KeyError) as exc:
            raise ExtractError("invalid CDX page count") from exc
        conn.execute(
            "INSERT OR REPLACE INTO archive_queries(query_id,source,prefix,year,next_page,total_pages,completed_at,started_at) VALUES (?,?,?,?,0,?,?,?)",
            (
                query_id,
                source,
                prefix,
                year,
                pages,
                client.clock.now().isoformat() if pages == 0 else None,
                client.clock.now().isoformat(),
            ),
        )
        next_page = 0
    else:
        pages = int(current["total_pages"])
        next_page = int(current["next_page"])
    count = 0
    for page in range(next_page, pages):
        response, outcome, skipped = request_page(
            client, source=source, url=cdx_url(prefix, year, page=page), deadline=deadline
        )
        if skipped or response is None or outcome.outcome != Outcome.OK:
            break
        retain_response(client, query_id, str(page), response)
        with client.database.transaction():
            store_cdx_page(
                conn,
                source=source,
                prefix=prefix,
                year=year,
                page=page,
                total_pages=pages,
                body=response.content,
                queried_at=client.clock.now().isoformat(),
                query_id=query_id,
            )
        count += 1
    return count


class CDXPage:
    kind = "wayback.cdx"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode: ChangeMode = "body_hash"

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()

    def extract(self, body: bytes) -> JsonValue:
        return json.loads(body)

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        raise NotImplementedError("CDX rows use index_archive")
