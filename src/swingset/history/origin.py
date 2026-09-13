"""Pure WP16 origin-gap proposals; no watches, acquisition, or policy activation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit

from swingset.fetch.wayback import Capture, query_due, select_captures
from swingset.history.captures import CaptureState
from swingset.history.platform import KnownEvent, Page


@dataclass(frozen=True)
class ArchiveSearch:
    """A retained archive_queries receipt, never an inferred absent row."""

    query_id: str
    source: str
    prefix: str
    year: int
    next_page: int
    total_pages: int | None
    completed_at: str | None

    def covers(self, source: str, url: str, year: int, now: datetime) -> bool:
        if self.source != source or self.year != year or not self.query_id:
            return False
        if (
            self.total_pages is None
            or self.total_pages < 0
            or self.next_page != self.total_pages
            or not self.completed_at
        ):
            return False
        try:
            completed = datetime.fromisoformat(self.completed_at)
            if (
                completed.tzinfo is None
                or completed > now
                or query_due(year, self.completed_at, now)
            ):
                return False
        except ValueError:
            return False
        # CDX prefixes may omit the scheme. Only a trailing wildcard grants a
        # prefix match; exact queries cannot accidentally cover sibling pages.
        parsed = urlsplit(url)
        candidate = (
            url
            if "://" in self.prefix
            else parsed.netloc + parsed.path + ("?" + parsed.query if parsed.query else "")
        )
        if self.prefix.endswith("*"):
            return candidate.startswith(self.prefix[:-1])
        return candidate == self.prefix


@dataclass(frozen=True)
class OriginProposal:
    event: KnownEvent
    page: Page
    eligible: bool
    reason: str
    archive_query_ids: tuple[str, ...] = ()
    attempted_capture_urls: tuple[str, ...] = ()
    max_events_per_cycle: int = 1
    max_events_per_day: int | None = None


def _same_resource(left: str, right: str) -> bool:
    # CDX may retain the HTTP spelling of an HTTPS query. A known alternate
    # scheme is still archive evidence; never discard it to manufacture a gap.
    def key(url: str) -> tuple[str | None, int | None, str, str]:
        parsed = urlsplit(url)
        default = 80 if parsed.scheme == "http" else 443
        return (
            parsed.hostname,
            None if parsed.port == default else parsed.port,
            parsed.path,
            parsed.query,
        )

    try:
        return all(
            urlsplit(url).scheme in {"http", "https"}
            and not urlsplit(url).username
            and not urlsplit(url).password
            and not urlsplit(url).fragment
            for url in (left, right)
        ) and key(left) == key(right)
    except ValueError:
        return False


def propose_origin(
    event: KnownEvent,
    page: Page,
    *,
    captures: tuple[Capture, ...],
    outcomes: Mapping[str, CaptureState],
    searches: tuple[ArchiveSearch, ...],
    now: datetime,
    gate_reason: str | None,
    operator_pre2018_refs: frozenset[str] = frozenset(),
    history_start: date = date(2010, 1, 1),
) -> OriginProposal:
    """Assess one known locator after the caller's exact year/kind gate.

    Absence requires complete capture-year queries from the history floor to
    today. Existing successful captures always take precedence. A documented
    unusable-copy fallback requires the preferred distinct alternatives to have
    actually failed interpretation; transport errors and pending work do not count.
    The result is a proposal only: any eventual dispatcher must repeat the gate
    and normal host, control, quota, conditional-request and cadence checks.
    """

    def result(
        reason: str,
        *,
        eligible: bool = False,
        query_ids: tuple[str, ...] = (),
        attempted: tuple[str, ...] = (),
    ) -> OriginProposal:
        return OriginProposal(
            event,
            page,
            eligible,
            reason,
            query_ids,
            attempted,
            max_events_per_day=1 if page.source == "dcn" else None,
        )

    if now.tzinfo is None:
        raise ValueError("origin planning requires a timezone-aware clock")
    if (event.source, event.source_ref) != (page.source, page.source_ref) or not event.event_id:
        return result("history_event_unmapped")
    if event.last_day < history_start:
        return result("history_out_of_scope")
    if event.last_day > now.date():
        return result("event_not_ended")
    if gate_reason is not None:
        return result(gate_reason)
    expected_hosts = {
        "eepro": {"eepro.com", "www.eepro.com"},
        "scoringdance": {"scoring.dance"},
        "dcn": {"danceconvention.net"},
    }
    try:
        parsed = urlsplit(page.url)
        port = parsed.port
    except ValueError:
        return result("origin_locator_invalid")
    if page.source not in expected_hosts:
        return result("origin_fallback_not_documented")
    if (
        parsed.scheme not in {"http", "https"}
        or port not in {None, 80 if parsed.scheme == "http" else 443}
        or parsed.hostname not in expected_hosts[page.source]
        or parsed.username
        or parsed.password
        or parsed.fragment
        or page.page_kind.split(".")[0] != page.source
    ):
        return result("origin_locator_invalid")
    if (
        page.source == "eepro"
        and event.year < 2018
        and page.source_ref not in operator_pre2018_refs
    ):
        return result("eepro_operator_slug_required")
    held = tuple(
        capture
        for capture in captures
        if _same_resource(capture.url, page.url) and capture.status == 200
    )
    if any(
        outcomes.get(capture.archive_url, CaptureState("unfetched", "capture_unexamined")).state
        == "complete"
        for capture in held
    ):
        return result("usable_archive_copy")
    selected = select_captures(held, event.last_day)
    if any(
        outcomes.get(capture.archive_url, CaptureState("unfetched", "capture_unexamined")).state
        != "incomplete"
        for capture in selected
    ):
        return result("archive_acquisition_or_interpretation_pending")
    query_ids = []
    for year in range(history_start.year, now.year + 1):
        proof = next(
            (query for query in searches if query.covers(page.source, page.url, year, now)), None
        )
        if proof is None:
            return result("archive_search_incomplete")
        query_ids.append(proof.query_id)
    attempted = tuple(capture.archive_url for capture in selected)
    return result(
        "archive_alternatives_unusable" if selected else "archive_absence_proven",
        eligible=True,
        query_ids=tuple(query_ids),
        attempted=attempted,
    )
