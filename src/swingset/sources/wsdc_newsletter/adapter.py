"""Newsletter event sidebars. Quarter-only approvals remain review findings."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from pypdf import PdfReader
from pypdf.errors import PdfReadError

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
from swingset.sources.records import CalendarRow
from swingset.sources.swingdancecouncil.adapter import historical_dates

from .colour_review import reviewed_colours
from .empty_review import reviewed_empty


class EventsPage:
    kind = "wsdc_newsletter.events"
    EXTRACT_VERSION = 2
    PARSER_VERSION = 8
    change_mode = "extract"

    def extract(self, body: bytes) -> JsonValue:
        if not body.startswith(b"%PDF-"):
            raise ExtractError("newsletter body is not a PDF")
        try:
            reader = PdfReader(io.BytesIO(body), strict=True)
            pages = [page.extract_text(extraction_mode="layout") for page in reader.pages]
        except (PdfReadError, ValueError, TypeError, KeyError) as exc:
            raise ExtractError(f"cannot extract newsletter PDF: {exc}") from exc
        if not any(text.strip() for text in pages):
            raise ExtractError("newsletter PDF has no extractable text")
        return {"body_sha256": sha256(body).hexdigest(), "pages": pages}

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        body_sha256 = None
        if isinstance(extract, dict):
            digest = extract.get("body_sha256")
            body_sha256 = digest if isinstance(digest, str) else None
            extract = extract.get("pages")
        if not isinstance(extract, list) or any(not isinstance(page, str) for page in extract):
            raise ExtractError("newsletter extract must contain page text")
        if reviewed_empty(body_sha256, extract):
            return ParseResult(legitimate_empty=True)
        colours = reviewed_colours(body_sha256, extract)
        observations = []
        warnings = []
        colour_unverified = any(
            re.search(
                r"(?:Member\s+Activities|Trial\s+Events).*?(?:purple|gr[ae]y)", page, re.I | re.S
            )
            for page in extract
        )
        if colour_unverified:
            warnings.append(
                ParseWarning(
                    "newsletter_colour_unverified",
                    "Trial or member activity colour was not recovered; reviewed rows are labelled separately, other newsletter rows use the documented registry fallback"
                    if colours
                    else "Trial or member activity colour was not recovered; newsletter registry status uses the documented fallback",
                    {
                        "snapshot_id": ctx.snapshot_id,
                        "fallback": "registry",
                        "colour_recovered": False,
                        **({"reviewed_colour_rows": len(colours)} if colours else {}),
                    },
                )
            )
        for page_number, page in enumerate(extract, 1):
            sidebar_rows, undated = _sidebar_rows(page)
            if undated:
                warnings.append(
                    ParseWarning(
                        "newsletter_undated_listing",
                        "Listed event has no safely parsed date; preserve hiatus or other notice for review",
                        {"page": page_number, "rows": undated},
                    )
                )
            approval_rows, approval_warnings = _approval_rows(page, page_number)
            warnings.extend(approval_warnings)
            rows = [(row, True) for row in sidebar_rows] + [(row, False) for row in approval_rows]
            if not rows:
                code = (
                    "approval_notice_review"
                    if re.search(r"New\s+(?:Registry\s+)?Events", page, re.I)
                    else "newsletter_sidebar_unparsed"
                )
                warnings.append(
                    ParseWarning(
                        code,
                        "Newsletter page has no safely dated event rows; review its sidebar and approval notices",
                        {"page": page_number},
                    )
                )
            for (name, start, end, location), sidebar in rows:
                label = colours.get((page_number, name, start, end)) if sidebar else None
                flags: tuple[str, ...]
                if label is not None:
                    flags = (
                        "newsletter_status_colour_reviewed",
                        "newsletter_member_activity"
                        if label == "Member Activity"
                        else "newsletter_trial_event",
                    )
                else:
                    flags = (
                        ("newsletter_status_colour_unverified",)
                        if sidebar and colour_unverified
                        else ()
                    )
                payload = CalendarRow(
                    "calendar_row",
                    name,
                    start,
                    end,
                    label or "registry",
                    location,
                    None,
                    None,
                    flags,
                )
                observations.append(
                    Observation(ObservationScope("calendar", "wsdc-history"), payload.kind, payload)
                )
        return ParseResult(tuple(observations), warnings=tuple(warnings), legitimate_empty=False)

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()


_DATE = re.compile(
    r"(?P<date>[A-Za-z]{3,9}\.?\s*,?\s*\d{1,2}\s*[-–—]\s*(?:[A-Za-z]{3,9}\.?\s+)?\d{1,2},?\s+20\d{2})"
)


_BULLET = re.compile("[•\uf0b7]")
_SINGLE_DATE = re.compile(r"([A-Za-z]{3,9})\.?\s*(\d{1,2}),?\s+(20\d{2})")


def _single_date(match: re.Match[str]) -> str:
    try:
        return (
            datetime.strptime(f"{match[1][:3]} {match[2]} {match[3]}", "%b %d %Y")
            .date()
            .isoformat()
        )
    except ValueError as exc:
        raise ParseError(f"newsletter_invalid_event_date: {match[0]}") from exc


def _date(raw: str) -> tuple[str, str]:
    cleaned = raw.replace("–", "-").replace("—", "-")
    cleaned = re.sub(r"([A-Za-z]+),\s*(?=\d)", r"\1 ", cleaned)
    if not re.search(r",\s+20\d{2}$", cleaned):
        cleaned = re.sub(r"\s+(20\d{2})$", r", \1", cleaned)
    try:
        return historical_dates(cleaned)
    except ValueError as exc:
        raise ParseError(f"newsletter_invalid_event_date: {raw}") from exc


def _dated_range(raw: str) -> tuple[str, str] | None:
    raw = re.sub(r"\s+\*+$", "", raw.strip())
    raw = re.sub(r"\s+,", ",", raw)
    if match := _DATE.fullmatch(raw):
        return _date(match["date"])
    if explicit := re.fullmatch(r"(.+?20\d{2})\s*[–—-]\s*(.+?20\d{2})", raw):
        first, last = _SINGLE_DATE.fullmatch(explicit[1]), _SINGLE_DATE.fullmatch(explicit[2])
        if first and last:
            start, end = _single_date(first), _single_date(last)
            if end < start:
                raise ParseError("newsletter_invalid_event_date: reversed explicit range")
            return start, end
    return None


def _column(line: str, start: int) -> str:
    # Blank space in this column is not permission to consume the next one.
    # A layout line can shift a date a few characters left of its bullet.
    # Recover a clipped word only; never cross a whitespace column boundary.
    while 0 < start < len(line) and not line[start].isspace() and not line[start - 1].isspace():
        start -= 1
    segment = line[start:]
    if len(segment) - len(segment.lstrip()) > 20:
        return ""
    segment = re.split(r"\s{5,}", segment.strip(), maxsplit=1)[0]
    return " ".join(segment.split())


def _sidebar_rows(page: str) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    lines = page.splitlines()
    result: list[tuple[str, str, str, str]] = []
    undated = []
    columns = []
    footer_start = len(lines)
    footer_right = 0
    contact_footer = False
    footer_columns: list[int] = []
    for index, line in enumerate(lines):
        for header in re.finditer(r"(?:Upcoming\s+)?Registry(?:\s+Events)?", line, re.I):
            before = line[: header.start()]
            if before.strip() and not re.search(r"\s{5,}$", before):
                continue
            label = _column(line, header.start()).casefold()
            continuation = next(
                (
                    part
                    for following in lines[index + 1 : index + 5]
                    if (part := _column(following, header.start()).casefold())
                ),
                "",
            )
            if label in {"registry events", "upcoming registry events"} or (
                label == "upcoming registry" and continuation == "events"
            ):
                columns.append((index, header.start()))
        if "WSDC Membership email:" in line:
            contact_footer = True
        bullets = list(_BULLET.finditer(line))
        if contact_footer and len(bullets) > 1 and bullets[0].start() <= 15:
            if not footer_columns:
                footer_columns = [bullet.start() for bullet in bullets]
            footer_start = min(footer_start, index)
            footer_right = max(footer_right, bullets[-1].start() + 15)
        footer = re.search("Events with an asterisk", line)
        if footer:
            footer_start, footer_right = index, footer.start() + 15
            while footer_start and lines[footer_start - 1].strip():
                footer_start -= 1
    for index, line in enumerate(lines):
        for bullet in _BULLET.finditer(line):
            start = bullet.start()
            matching = [
                column
                for heading, column in columns
                if index > heading
                and (abs(start - column) <= 15 or (column > 40 and start >= column))
            ]
            if not matching and not (index >= footer_start and start <= footer_right):
                continue
            anchor = (
                min(start, min(matching))
                if matching
                else min(footer_columns, key=lambda column: abs(column - start))
                if footer_columns
                else start
            )
            name_line = re.sub(r"(?<=[A-Z]) {5,7}(?=[A-Z]{2,}\b)", " ", line[start + 1 :])
            names = [_column(name_line, 0)]
            before_count = len(result)
            explicit_start = None
            for continuation in lines[index + 1 : index + 9]:
                segment = _column(continuation, anchor)
                if _BULLET.search(segment) or any(
                    abs(bullet.start() - start) <= 15 for bullet in _BULLET.finditer(continuation)
                ):
                    break
                if not _dated_range(segment):
                    alternate = _column(continuation, start + 1)
                    if _dated_range(alternate):
                        segment = alternate
                # A wrapped name can share its final line with a printed range.
                inline = re.search(
                    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d",
                    segment,
                    re.I,
                )
                if (
                    inline
                    and inline.start()
                    and not re.search(r"\d|,", re.sub(r"\([^)]*\)", "", segment[: inline.start()]))
                    and (dates := _dated_range(segment[inline.start() :]))
                ):
                    names.append(segment[: inline.start()].strip())
                    result.append((" ".join(names), *dates, ""))
                    break
                if dates := _dated_range(segment):
                    result.append((" ".join(names), *dates, ""))
                    break
                if match := _SINGLE_DATE.fullmatch(segment.rstrip("–—-")):
                    date_value = _single_date(match)
                    if explicit_start:
                        if date_value < explicit_start:
                            raise ParseError(
                                "newsletter_invalid_event_date: reversed explicit range"
                            )
                        result.append((" ".join(names), explicit_start, date_value, ""))
                        break
                    explicit_start = date_value
                    continue
                if explicit_start and segment.casefold().startswith("to "):
                    if match := _SINGLE_DATE.fullmatch(segment[3:]):
                        end = _single_date(match)
                        if end < explicit_start:
                            raise ParseError(
                                "newsletter_invalid_event_date: reversed explicit range"
                            )
                        result.append((" ".join(names), explicit_start, end, ""))
                    break
                if segment:
                    names.append(segment)
            if len(result) == before_count:
                undated.append(" ".join(names))
    # Simple one-column issues may omit bullets altogether.
    if not result and re.search(r"Upcoming\s+Registry\s+Events", page, re.I):
        for index, line in enumerate(lines):
            if dates := _dated_range(line.strip()):
                name = lines[index - 1].strip() if index else ""
                if name and not re.search(r"Registry Events", name, re.I):
                    result.append((name, *dates, ""))
    return result, undated


def _approval_rows(
    page: str, page_number: int
) -> tuple[list[tuple[str, str, str, str]], list[ParseWarning]]:
    lines = page.splitlines()
    result = []
    warnings = []
    for index, line in enumerate(lines):
        header = re.search(r"New\s+(?:Registry\s+)?Events\b", line, re.I)
        if header is None or re.search(r"applications?|\bemail:", line, re.I):
            continue
        administrative = re.search(
            r"new events are able to select locations and dates\b"
            r"|(?:managing|running)\s+new events,"
            r"|certification process for new events\."
            r"|assist new events that are applying for membership\."
            r"|events and new events\.\s+Wednesday[’']s focus is",
            line,
            re.I,
        )
        if administrative and administrative.start() <= header.start() < administrative.end():
            # These retained planning, management and posting-schedule sentences
            # name no newly approved edition. Unknown prose still needs review.
            continue
        start = header.start()
        label = _column(line, start).casefold()
        if label not in {"new events", "new registry events"}:
            warnings.append(
                ParseWarning(
                    "approval_notice_review",
                    "Prose approval notice needs a dated event interpretation",
                    {"page": page_number, "notice": line.strip()},
                )
            )
            continue
        previous = ""
        for following in lines[index + 1 : index + 35]:
            segment = _column(following, start)
            if segment.startswith("Applications for"):
                break
            if dates := _dated_range(segment):
                if location_parts := re.fullmatch(r"(.+?)\s*[–—]\s*(.+)", previous):
                    name, location = location_parts[1], location_parts[2]
                else:
                    name, separator, location = previous.partition(" in ")
                    if not separator:
                        continue
                if name:
                    result.append((name.strip(), *dates, location.strip()))
            elif segment:
                if re.search(
                    r"\bQ[1-4]\s+20\d{2}\b|\b(?:first|second|third|fourth)\s+quarter\b",
                    segment,
                    re.I,
                ):
                    warnings.append(
                        ParseWarning(
                            "approval_notice_review",
                            "Approval quarter does not establish event dates",
                            {"page": page_number, "preceding_text": previous, "notice": segment},
                        )
                    )
                previous = segment
    return result, warnings


@dataclass(frozen=True)
class NewsletterSource:
    name: str = "wsdc_newsletter"
    hosts: frozenset[str] = frozenset({"worldsdc.com"})
    from .index import IndexPage

    page_kinds = {EventsPage.kind: EventsPage(), IndexPage.kind: IndexPage()}

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return []


SOURCE = NewsletterSource()
