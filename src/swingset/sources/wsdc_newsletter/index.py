"""Newsletter issue discovery from the council's public newsletter index."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from swingset.sources.base import ExtractError, JsonValue, ParseContext, ParseResult, WatchSpec


def is_issue_url(url: str) -> bool:
    parsed = urlsplit(url)
    return bool(
        parsed.path.lower().endswith(".pdf")
        and re.search(r"newslet|vol(?:ume)?[- _]?\d", parsed.path.rsplit("/", 1)[-1], re.I)
    )


class IndexPage:
    kind = "wsdc_newsletter.index"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 2
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        document = HTMLParser(body)
        urls = sorted(
            {
                str(node.attributes["href"])
                for node in document.css("a[href]")
                if ".pdf" in str(node.attributes["href"]).lower()
            }
        )
        if not urls:
            raise ExtractError("newsletter index has no PDF issue links")
        return urls

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, list) or any(not isinstance(url, str) for url in extract):
            raise ExtractError("newsletter index links must be strings")
        watches = []
        for href in extract:
            url = urljoin(ctx.url, href)
            parsed = urlsplit(url)
            if parsed.hostname not in {
                "worldsdc.com",
                "www.worldsdc.com",
            } or not is_issue_url(url):
                continue
            watches.append(
                WatchSpec("", "wsdc_newsletter", "index", "GET", url, "wsdc_newsletter.events")
            )
        if not watches:
            raise ExtractError("newsletter index has no same-host PDF issues")
        return ParseResult(watches=tuple(watches), legitimate_empty=True)

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()
