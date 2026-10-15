"""The registered danceconvention.net source: the events archive and event results tabs.

Both pages are Nuxt server renders of about 2.9 MB. The data lives in the one
`window.__NUXT__` payload, which `nuxt.py` decodes without running JavaScript.
The rest of the page (inlined translations, a Sentry trace id) changes on every
response, so change detection compares the extract (`change_mode = "extract"`).

`dcn.archive` lists the current year's events. Each event with a `WSDC`
affiliation and a `results` flag becomes a listing row and an event watch.
`dcn.event` reads one event's results tab: every contest, round and ranking.
Only final rounds become round sheets. A preliminary or semifinal ranking lists
only the dancers that round eliminated, with their overall rank, and has no
judge marks; the shared projector would publish a promoted count of zero for
it. Bibs, marks and full round populations are only in the per-round score
PDFs, which this module does not fetch (D-0255). Names the source masks
(`******* *******`) stay unpublished: the cell is left empty.

The legacy offline kinds in `adapter.py` (`dcn.list`, `dcn.event_metadata`,
`dcn.legacy_results`, `dcn.score_pdf`) stay unregistered.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from swingset.sources.base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseError,
    ParseResult,
    ParseWarning,
    WatchSpec,
)
from swingset.sources.interpretation import declared
from swingset.sources.records import (
    Cell,
    EventSheet,
    ResultRow,
    ResultTable,
    RoundSheet,
    SourceEventRow,
)

from .nuxt import evaluate_nuxt

HOST = "danceconvention.net"
ROOT = f"https://{HOST}"
ARCHIVE_URL = f"{ROOT}/eventdirector/en/eventsarchive"
ARCHIVE_PATH = re.compile(r"/eventdirector/[a-z]{2}/eventsarchive/?")
ARCHIVE_ROUTE = re.compile(r"/[a-z]{2}/eventsarchive")
RESULTS_PATH = re.compile(
    r"/eventdirector/[a-z]{2}/eventpage/([0-9]{1,18})(?:-[^/?#;\s]*)?/results/?"
)
RESULTS_ROUTE = re.compile(r"/[a-z]{2}/eventpage/([0-9]{1,18})(?:-[^/]*)?/results")
EVENT_PAGE = re.compile(r"/eventdirector/[a-z]{2}/eventpage/([0-9]{1,18})(?:-[^/?#;\s]*)?")
DATE = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{2})")
#: Any mask character hides the whole name, including partial masks (`J**** S****`).
MASKED = re.compile(r"\*")
#: The one affiliation that makes an archive row a WCS results listing.
AFFILIATION = "WSDC"
ROLES = frozenset({"LEADER", "FOLLOWER"})
MAX_EVENTS = 1000
MAX_CONTESTS = 200
MAX_ROUNDS = 20
MAX_RANKINGS = 2000
MAX_TEXT = 512

ARCHIVE_RENDER = frozenset({"availableYears", "lastYearEvents", "loadYearUrl", "dcnetLogo"})
ARCHIVE_ROW = frozenset(
    {
        "eventId",
        "name",
        "venueName",
        "startDate",
        "endDate",
        "location",
        "online",
        "schedule",
        "bannerUrl",
        "squareImage",
        "affiliations",
        "hideAffiliationOnPublicPages",
        "results",
        "published",
        "frequent",
        "eventPage",
    }
)
EVENT_RENDER = frozenset(
    {
        "eventPageLink",
        "canonicalEventUrl",
        "eventLeadAsText",
        "eventInfo",
        "publishFlags",
        "eventWebsiteUrl",
        "currentUserSignedUp",
        "signupAllowed",
        "registrationLink",
        "danceEvent",
        "results",
    }
)
CONTEST = frozenset({"contestId", "contestName", "divisionType", "rounds"})
ROUND = frozenset({"roundId", "roundName", "isFinal", "scoresAvailable", "scoresLink", "rankings"})
# Cities and countries are read but never kept: the dataset holds no addresses.
RANKING = frozenset(
    {
        "competitorRole",
        "competitorName",
        "competitorCityAndState",
        "competitorCountry",
        "partnerName",
        "partnerCityAndState",
        "partnerCountry",
        "proName",
        "rank",
        "interval",
    }
)
DIVISION_TYPES = frozenset(
    {
        "RANDOM_PARTNER",
        "RANDOM_COUPLE",
        "OPEN_COUPLE",
        "PERM_COUPLE",
        "PROAM_MIXED",
        "PROAM_JNJ",
        "SWITCH_JNJ",
    }
)
#: Warnings the admission contract accepts as bounded, documented limits.
BOUNDED_WARNINGS = frozenset(
    {
        "dcn_nonfinal_rounds_withheld",
        "dcn_role_split_final_withheld",
        "dcn_empty_final_withheld",
        "dcn_names_masked_at_source",
        "dcn_results_unpublished",
    }
)


def _text(value: Any, field: str, *, empty: bool = False) -> str:
    """Source text with HTML entities decoded; the payload carries escaped HTML text."""
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise ExtractError(f"DCN {field} is not bounded source text")
    decoded = " ".join(html.unescape(value).split())
    if not decoded and not empty:
        raise ExtractError(f"DCN {field} is empty")
    return decoded


def _int(value: Any, field: str, *, low: int = 1, high: int = 10**18) -> int:
    if type(value) is not int or not low <= value < high:
        raise ExtractError(f"DCN {field} is not a bounded integer")
    return value


def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ExtractError(f"DCN {field} is not a boolean")
    return value


def _date(raw: str) -> date:
    match = DATE.fullmatch(raw)
    if match is None:
        raise ExtractError("DCN date is not M/D/YY")
    month, day, year = (int(part) for part in match.groups())
    try:
        return date(2000 + year, month, day)
    except ValueError as exc:
        raise ExtractError("DCN date is not a calendar date") from exc


def date_range(start_raw: str, end_raw: str) -> str:
    """The listing date in the range form `project/events.nullable_date_range` reads."""
    start, end = _date(start_raw), _date(end_raw)
    if not start <= end or (end - start).days > 31:
        raise ExtractError("DCN event dates are reversed or longer than a month")
    if start.month == end.month and start.year == end.year:
        return f"{start:%b} {start.day} - {end.day}, {end.year}"
    return f"{start:%b} {start.day} - {end:%b} {end.day}, {end.year}"


def _render(body: bytes, route: re.Pattern[str]) -> tuple[re.Match[str], dict[str, Any]]:
    payload = evaluate_nuxt(body)
    path = payload.get("routePath")
    match = route.fullmatch(path) if isinstance(path, str) else None
    if match is None:
        raise ExtractError("DCN route is not the reviewed page")
    try:
        render = payload["state"]["common"]["currentPageRenderData"]
    except (KeyError, TypeError) as exc:
        raise ExtractError("DCN render data is missing") from exc
    if not isinstance(render, dict):
        raise ExtractError("DCN render data is not an object")
    # Older captures repeat the render data under `data[0].renderData`; when
    # present the two views must agree.
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict) and "renderData" in data[0]:
        if data[0]["renderData"] != render:
            raise ExtractError("DCN duplicate render data views disagree")
    return match, render


def _unreviewed(value: dict[str, Any], known: frozenset[str], path: str, found: set[str]) -> None:
    found.update(f"{path}.{key}" for key in value if key not in known)


def results_url(event_id: int) -> str:
    return f"{ROOT}/eventdirector/en/eventpage/{event_id}/results"


def _context_ref(ctx: ParseContext, kind: str) -> str | None:
    """The event id an event context names, or None for the archive."""
    if ctx.source != "dcn" or ctx.kind != kind:
        raise ParseError("DCN parse context differs from the exact source kind")
    if any(char.isspace() or ord(char) < 32 for char in ctx.url):
        raise ParseError("DCN context URL contains whitespace or controls")
    try:
        parsed = urlsplit(ctx.url)
    except ValueError as exc:
        raise ParseError("DCN context URL is malformed") from exc
    if parsed.scheme != "https" or parsed.netloc != HOST or parsed.query or parsed.fragment:
        raise ParseError("DCN context URL is outside the reviewed host")
    if kind == ArchivePage.kind:
        if ARCHIVE_PATH.fullmatch(parsed.path) is None:
            raise ParseError("DCN archive parser requires the events archive URL")
        return None
    match = RESULTS_PATH.fullmatch(parsed.path)
    if match is None:
        raise ParseError("DCN event parser requires an event results URL")
    return match[1]


class ArchivePage:
    kind = "dcn.archive"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "extract"

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()

    def extract(self, body: bytes) -> JsonValue:
        _, render = _render(body, ARCHIVE_ROUTE)
        unreviewed: set[str] = set()
        _unreviewed(render, ARCHIVE_RENDER, "$", unreviewed)
        years = render.get("availableYears")
        if (
            not isinstance(years, list)
            or not 1 <= len(years) <= 200
            or any(type(year) is not int or not 2000 <= year <= 2100 for year in years)
        ):
            raise ExtractError("DCN available years are malformed")
        rows = render.get("lastYearEvents")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_EVENTS:
            raise ExtractError("DCN archive event list is empty or oversized")
        events = []
        seen: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ExtractError("DCN archive row is not an object")
            _unreviewed(row, ARCHIVE_ROW, "$.events[*]", unreviewed)
            identifier = _int(row.get("eventId"), "eventId")
            page = row.get("eventPage")
            match = EVENT_PAGE.fullmatch(page) if isinstance(page, str) else None
            if match is None or int(match[1]) != identifier or identifier in seen:
                raise ExtractError("DCN archive event id, page or ownership disagrees")
            seen.add(identifier)
            affiliations = row.get("affiliations")
            if not isinstance(affiliations, list) or len(affiliations) > 50:
                raise ExtractError("DCN affiliations are malformed")
            events.append(
                {
                    "id": identifier,
                    "name": _text(row.get("name"), "event name"),
                    "start": _text(row.get("startDate"), "start date"),
                    "end": _text(row.get("endDate"), "end date"),
                    "affiliations": [_text(item, "affiliation") for item in affiliations],
                    "results": _bool(row.get("results"), "results"),
                    "published": _bool(row.get("published"), "published"),
                }
            )
        return {"events": events, "unreviewed_fields": sorted(unreviewed)}

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        _context_ref(ctx, self.kind)
        observations = []
        watches = []
        for event in extract["events"]:
            if not listed(event):
                continue
            ref = f"dcn:{event['id']}"
            url = results_url(event["id"])
            try:
                when = date_range(event["start"], event["end"])
            except ExtractError as exc:
                raise ParseError(str(exc)) from exc
            payload = SourceEventRow("source_event_row", ref, event["name"], when, url)
            observations.append(
                Observation(ObservationScope("source_index", "dcn"), payload.kind, payload)
            )
            watches.append(
                WatchSpec("", "dcn", "event", "GET", url, EventPage.kind, source_ref=ref)
            )
        return ParseResult(tuple(observations), tuple(watches))


def listed(event: dict[str, Any]) -> bool:
    """An archive row we follow: a published WSDC event that reports results."""
    return AFFILIATION in event["affiliations"] and event["results"] and event["published"]


def _ranking(item: Any, unreviewed: set[str]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ExtractError("DCN ranking is not an object")
    _unreviewed(item, RANKING, "$.contests[*].rounds[*].rankings[*]", unreviewed)
    role = item.get("competitorRole")
    if role not in ROLES:
        raise ExtractError("DCN competitor role is not LEADER or FOLLOWER")
    partner = item.get("partnerName")
    interval = item.get("interval")
    return {
        "role": role,
        "name": _text(item.get("competitorName"), "competitor name"),
        "partner": None if partner is None else _text(partner, "partner name", empty=True),
        "rank": _int(item.get("rank"), "rank", high=100_000),
        "interval": None if interval is None else _int(interval, "interval", high=100_000),
    }


class EventPage:
    kind = "dcn.event"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "extract"

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()

    def extract(self, body: bytes) -> JsonValue:
        route, render = _render(body, RESULTS_ROUTE)
        unreviewed: set[str] = set()
        _unreviewed(render, EVENT_RENDER, "$", unreviewed)
        event = render.get("danceEvent")
        flags = render.get("publishFlags")
        contests = render.get("results")
        if not isinstance(event, dict) or not isinstance(flags, dict):
            raise ExtractError("DCN event or publish flags are missing")
        identifier = _int(event.get("eventId"), "eventId")
        if str(identifier) != route[1]:
            raise ExtractError("DCN route and event id disagree")
        if not isinstance(contests, list) or len(contests) > MAX_CONTESTS:
            raise ExtractError("DCN results list is missing or oversized")
        affiliations = event.get("affiliations")
        if not isinstance(affiliations, list) or len(affiliations) > 50:
            raise ExtractError("DCN affiliations are malformed")
        output = []
        contest_ids: set[int] = set()
        round_ids: set[int] = set()
        for contest in contests:
            if not isinstance(contest, dict):
                raise ExtractError("DCN contest is not an object")
            _unreviewed(contest, CONTEST, "$.contests[*]", unreviewed)
            contest_id = _int(contest.get("contestId"), "contestId")
            division = contest.get("divisionType")
            rounds = contest.get("rounds")
            if contest_id in contest_ids or not isinstance(rounds, list):
                raise ExtractError("DCN contest id repeats or its rounds are missing")
            if not 1 <= len(rounds) <= MAX_ROUNDS:
                raise ExtractError("DCN contest round count is outside its bound")
            contest_ids.add(contest_id)
            if division not in DIVISION_TYPES:
                unreviewed.add(f"$.contests[*].divisionType={division}")
            output_rounds = []
            for item in rounds:
                if not isinstance(item, dict):
                    raise ExtractError("DCN round is not an object")
                _unreviewed(item, ROUND, "$.contests[*].rounds[*]", unreviewed)
                round_id = _int(item.get("roundId"), "roundId")
                rankings = item.get("rankings")
                if (
                    round_id in round_ids
                    or not isinstance(rankings, list)
                    or len(rankings) > MAX_RANKINGS
                ):
                    raise ExtractError("DCN round id repeats or its rankings are malformed")
                round_ids.add(round_id)
                link = item.get("scoresLink")
                if link is not None and link != f"/eventdirector/en/roundscores/{round_id}.pdf":
                    raise ExtractError("DCN score sheet locator is not the reviewed path")
                output_rounds.append(
                    {
                        "id": round_id,
                        "name": _text(item.get("roundName"), "round name"),
                        "final": _bool(item.get("isFinal"), "isFinal"),
                        "scores_available": _bool(item.get("scoresAvailable"), "scoresAvailable"),
                        "scores_link": None if link is None else ROOT + link,
                        "rankings": [_ranking(ranking, unreviewed) for ranking in rankings],
                    }
                )
            output.append(
                {
                    "id": contest_id,
                    "name": _text(contest.get("contestName"), "contest name"),
                    "division_type": str(division),
                    "rounds": output_rounds,
                }
            )
        return {
            "event": {
                "id": identifier,
                "name": _text(event.get("name"), "event name"),
                "start": _text(event.get("startDate"), "start date"),
                "end": _text(event.get("endDate"), "end date"),
                "affiliations": [_text(item, "affiliation") for item in affiliations],
            },
            "publish_results": _bool(flags.get("publishCompResults"), "publishCompResults"),
            "contests": output,
            "unreviewed_fields": sorted(unreviewed),
        }

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        identifier = _context_ref(ctx, self.kind)
        event = extract["event"]
        if str(event["id"]) != identifier:
            raise ParseError("DCN results page belongs to another event")
        ref = f"dcn:{identifier}"
        try:
            when = date_range(event["start"], event["end"])
        except ExtractError as exc:
            raise ParseError(str(exc)) from exc
        listing = SourceEventRow("source_event_row", ref, event["name"], when, ctx.url)
        observations = [Observation(ObservationScope("source_index", ref), listing.kind, listing)]
        warnings: list[ParseWarning] = []
        if not extract["publish_results"]:
            warnings.append(
                ParseWarning(
                    "dcn_results_unpublished",
                    "The organizer has not published contest results; nothing is public yet.",
                )
            )
            return ParseResult(tuple(observations), warnings=tuple(warnings))
        links = tuple(
            (str(item["id"]), f"{contest['name']} - {item['name']}")
            for contest in extract["contests"]
            for item in contest["rounds"]
        )
        sheet = EventSheet("event_sheet", ref, event["name"], links)
        observations.append(Observation(ObservationScope("source_event", ref), sheet.kind, sheet))
        withheld: list[str] = []
        split: list[str] = []
        empty: list[str] = []
        masked = 0
        for contest in extract["contests"]:
            for item in contest["rounds"]:
                if not item["final"]:
                    withheld.append(str(item["id"]))
                    continue
                if not item["rankings"]:
                    empty.append(str(item["id"]))
                    continue
                table = final_table(contest["name"], item)
                if table is None:
                    split.append(str(item["id"]))
                    continue
                masked += sum(cell.text is None for row in table.rows for cell in row.cells[1:])
                payload = RoundSheet(
                    "round_sheet",
                    ref,
                    str(item["id"]),
                    contest["name"],
                    item["name"],
                    (table,),
                    event_name_raw=event["name"],
                )
                observations.append(
                    Observation(ObservationScope("source_event", ref), payload.kind, payload)
                )
        if withheld:
            warnings.append(
                ParseWarning(
                    "dcn_nonfinal_rounds_withheld",
                    "Preliminary and semifinal rankings list only eliminated dancers, without "
                    "marks or bibs; they wait for the score PDFs.",
                    {"round_ids": withheld},
                )
            )
        if split:
            warnings.append(
                ParseWarning(
                    "dcn_role_split_final_withheld",
                    "A final ranks each role separately, so one round would repeat places.",
                    {"round_ids": split},
                )
            )
        if empty:
            warnings.append(
                ParseWarning(
                    "dcn_empty_final_withheld",
                    "A final lists no placements; it yields no round sheet.",
                    {"round_ids": empty},
                )
            )
        if masked:
            warnings.append(
                ParseWarning(
                    "dcn_names_masked_at_source",
                    "The source masks these dancers' names; their cells stay empty.",
                    {"count": masked},
                )
            )
        return ParseResult(tuple(observations), warnings=tuple(warnings))


def _name(value: str | None) -> str | None:
    return None if value is None or not value.strip() or MASKED.search(value) else value


def final_table(contest: str, item: dict[str, Any]) -> ResultTable | None:
    """A final's placements as one Place / Leader / Follower table.

    The competitor goes in its role's column and the partner in the other.
    Returns None when the printed places are not exactly 1..N in order (a
    final that ranks each role separately); the projector keys a placement by
    round and place, so such a round cannot be one table.
    """
    rankings = item["rankings"]
    if not rankings:
        return None
    if [ranking["rank"] for ranking in rankings] != list(range(1, len(rankings) + 1)):
        return None
    if any(ranking["interval"] is not None for ranking in rankings):
        return None
    rows = []
    for ranking in rankings:
        competitor, partner = _name(ranking["name"]), _name(ranking["partner"])
        leader, follower = (
            (competitor, partner) if ranking["role"] == "LEADER" else (partner, competitor)
        )
        rows.append(ResultRow((Cell(str(ranking["rank"])), Cell(leader), Cell(follower))))
    return ResultTable(
        f"{contest} - {item['name']}",
        (Cell("Place"), Cell("Leader"), Cell("Follower")),
        tuple(rows),
    )


@dataclass(frozen=True)
class DCNLiveSource:
    name: str = "dcn"
    hosts: frozenset[str] = frozenset({HOST})
    page_kinds = {ArchivePage.kind: ArchivePage(), EventPage.kind: EventPage()}

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return [WatchSpec("", self.name, "index", "GET", ARCHIVE_URL, ArchivePage.kind)]


SOURCE = DCNLiveSource()
