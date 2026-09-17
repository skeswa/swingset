"""Offline parser for the two exact, reviewed Riga score-PDF layouts."""

from __future__ import annotations

import hashlib
import re
from io import BytesIO
from typing import NoReturn
from urllib.parse import urlsplit

from pypdf import PdfReader

from swingset.sources.base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseError,
    ParseResult,
)

from .records import DcnScorePdfJudge, DcnScorePdfPage, DcnScorePdfRow, DcnScorePdfValue

HOST = "danceconvention.net"
EVENT_REF = "dcn:1546230"
ROUND_URLS = {
    "/eventdirector/en/roundscores/3451330.pdf": "3451330",
    "/eventdirector/en/roundscores/3451331.pdf": "3451331",
}
FINALS_HEADING = "Jack'n'Jill Newcomer - Finals"
LEADERS_HEADING = "Jack'n'Jill Newcomer - Prelims - Leaders"
FOLLOWERS_HEADING = "Jack'n'Jill Newcomer - Prelims - Followers"
EVENT_NAME = "Riga Summer Swing"
PRELIMS_LEGEND = (
    "Score legend: 1 = YES, 2 = ALT, 3 = NO; additional ranking may be provided for ALT"
)
FINALS_LEGEND = "Score legend: placement from 1 to 9"
OMISSION_NOTICE = (
    "Dancers without any Yes or Alternate marks are omitted from this list. "
    "You may check your invididual results on your danceConvention.net account page."
)
MAX_BODY_BYTES = 256 * 1024
MAX_PAGES = 2
MAX_PAGE_TEXT = 16_000
MAX_LINE_TEXT = 512
MAX_ROWS_PER_PAGE = 100
MAX_NAME_TEXT = 160
FINAL_JUDGE_CODES = ("OD", "SK", "DK", "OM", "MM", "MS", "HT")
PRELIM_JUDGE_CODES = {
    "leader": ("SK", "MM", "LT", "AV", "CHB"),
    "follower": ("OM", "ATP", "MS", "HT", "CHB"),
}
FINAL_NUMBERED_HEADERS = tuple(f"1-{number}" for number in range(1, 10))
MARK_RE = re.compile(r"(?:1|2(?:\.[12])?|3)\Z")
CELL_RE = re.compile(r"(?:-|[0-9]{1,3}(?: \([0-9]{1,3}\))?)\Z")
BIB_RE = re.compile(r"[0-9]{1,3}\Z")


def _fail(message: str) -> NoReturn:
    raise ParseError("DCN score PDF " + message)


def _pdf_pages(reader: PdfReader) -> list[dict[str, JsonValue]]:
    try:
        if reader.is_encrypted:
            raise ExtractError("encrypted documents are unsupported")
        pages = reader.pages
        count = len(pages)
        if count < 1 or count > MAX_PAGES:
            raise ExtractError("page count exceeds the reviewed bound")
        extracted: list[dict[str, JsonValue]] = []
        for number, page in enumerate(pages, start=1):
            text = page.extract_text()
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_PAGE_TEXT:
                raise ExtractError("page text is empty or exceeds the reviewed bound")
            if any(len(line) > MAX_LINE_TEXT for line in text.splitlines()):
                raise ExtractError("page line exceeds the reviewed text bound")
            extracted.append(
                {
                    "page_number": number,
                    "width": float(page.mediabox.width),
                    "height": float(page.mediabox.height),
                    "text": text,
                }
            )
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError("cannot read strict PDF structure or bounded page text") from exc
    return extracted


def _heading(line: str) -> tuple[str, str | None] | None:
    values = {
        FINALS_HEADING: ("finals", None),
        LEADERS_HEADING: ("prelims", "leader"),
        FOLLOWERS_HEADING: ("prelims", "follower"),
    }
    return values.get(line)


def _nonblank(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _page_lines(text: str) -> list[str]:
    lines = _nonblank(text)
    if lines.count("DanceConvention.net") != 1:
        _fail("page footer is missing or ambiguous")
    return [line for line in lines if line != "DanceConvention.net"]


def _judges(lines: list[str], codes: tuple[str, ...]) -> tuple[DcnScorePdfJudge, ...]:
    judges: list[DcnScorePdfJudge] = []
    for line in lines:
        match = re.fullmatch(r"([A-Z]{2,3})\s+(.{1,120})", line)
        if match is None:
            _fail("judge roster layout changed")
        judges.append(DcnScorePdfJudge(match[1], match[2]))
    if tuple(judge.code_raw for judge in judges) != codes:
        _fail("judge codes are missing, duplicated, or out of order")
    if len({judge.name_raw for judge in judges}) != len(judges):
        _fail("judge names are ambiguous within the page panel")
    return tuple(judges)


def _mark(column: str, raw: str) -> DcnScorePdfValue:
    if MARK_RE.fullmatch(raw) is None:
        _fail(f"unrecognized legacy mark in {column}")
    if raw == "1":
        kind, subrank = "yes", None
    elif raw.startswith("2"):
        kind, _, subrank = raw.partition(".")
        kind, subrank = "alternate", subrank or None
    else:
        kind, subrank = "no", None
    return DcnScorePdfValue(column, raw, kind, subrank)


def _header_and_legend(lines: list[str], *, finals: bool) -> tuple[int, int]:
    expected_legend = FINALS_LEGEND if finals else PRELIMS_LEGEND
    legend_positions = [index for index, line in enumerate(lines) if line == expected_legend]
    if len(legend_positions) != 1:
        _fail("score legend is missing or ambiguous")
    legend_index = legend_positions[0]
    headers = [
        index for index in range(legend_index + 1, len(lines)) if lines[index].startswith("#")
    ]
    if len(headers) != 1:
        _fail("result table header is missing or ambiguous")
    if headers[0] != legend_index + 1:
        _fail("unaccounted text between score legend and result table header")
    return legend_index, headers[0]


def _take_tail(tokens: list[str], count: int) -> tuple[list[str], list[str]]:
    remaining = len(tokens) - 1
    reversed_cells: list[str] = []
    for _ in range(count):
        if remaining < 0:
            _fail("result row has too few cells")
        token = tokens[remaining]
        if re.fullmatch(r"\([0-9]{1,3}\)", token):
            if remaining < 1 or re.fullmatch(r"[0-9]{1,3}", tokens[remaining - 1]) is None:
                _fail("numbered result cell has an orphan parenthetical value")
            reversed_cells.append(tokens[remaining - 1] + " " + token)
            remaining -= 2
        else:
            reversed_cells.append(token)
            remaining -= 1
    return tokens[: remaining + 1], list(reversed(reversed_cells))


def _final_rows(lines: list[str], header_index: int) -> tuple[DcnScorePdfRow, ...]:
    expected_headers = (
        "#",
        "Name",
        *FINAL_JUDGE_CODES,
        *FINAL_NUMBERED_HEADERS,
        "Result",
        "Remarks",
    )
    if tuple(lines[header_index].split()) != expected_headers:
        _fail("finals columns are missing, duplicated, or changed")
    rows: list[DcnScorePdfRow] = []
    seen_bibs: set[str] = set()
    index = header_index + 1
    table_lines = lines[index:]
    cursor = 0
    while cursor < len(table_lines):
        line = table_lines[cursor]
        if re.match(r"^[0-9]{1,3}\s", line) is None:
            _fail("unexpected text or broken pair row after finals header")
        if len(rows) >= MAX_ROWS_PER_PAGE:
            _fail("row count exceeds the reviewed bound")
        leader_tokens = line.split()
        if len(leader_tokens) < 2 or BIB_RE.fullmatch(leader_tokens[0]) is None:
            _fail("finals bib or pair name is missing")
        bib, leader = leader_tokens[0], " ".join(leader_tokens[1:])
        if bib in seen_bibs or len(leader) > MAX_NAME_TEXT:
            _fail("finals bib is repeated or pair leader name is too long")
        cursor += 1
        if cursor >= len(table_lines) or re.match(r"^[0-9]{1,3}\s", table_lines[cursor]):
            _fail("finals pair is missing its second printed name")
        follower_line = table_lines[cursor]
        prefix, cells = _take_tail(follower_line.split(), 17)
        if not prefix:
            _fail("finals pair continuation name is missing")
        follower = " ".join(prefix)
        if len(follower) > MAX_NAME_TEXT:
            _fail("finals pair follower name is too long")
        if len(cells) != 17:
            _fail("finals row width changed")
        judge_cells = cells[:7]
        numbered_cells = cells[7:16]
        result = cells[16]
        if any(re.fullmatch(r"[1-9]", value) is None for value in judge_cells):
            _fail("finals judge placement is not an ordinal from 1 to 9")
        if any(CELL_RE.fullmatch(value) is None for value in numbered_cells):
            _fail("finals numbered-column value is malformed")
        if re.fullmatch(r"[1-9]", result) is None:
            _fail("finals result is not an ordinal from 1 to 9")
        seen_bibs.add(bib)
        rows.append(
            DcnScorePdfRow(
                bib,
                (leader, follower),
                None,
                tuple(
                    DcnScorePdfValue(code, value)
                    for code, value in zip(FINAL_JUDGE_CODES, judge_cells, strict=True)
                ),
                tuple(
                    DcnScorePdfValue(header, value)
                    for header, value in zip(FINAL_NUMBERED_HEADERS, numbered_cells, strict=True)
                ),
                result,
                "",
                line,
                follower_line,
                "pair_unassigned",
                "unknown",
            )
        )
        cursor += 1
    if len(rows) != 9 or {row.result_raw for row in rows} != {str(value) for value in range(1, 10)}:
        _fail("finals result rows do not contain the printed placements 1 through 9")
    return tuple(rows)


def _prelim_rows(
    lines: list[str], header_index: int, role: str, disclaimer: str
) -> tuple[tuple[str, ...], tuple[DcnScorePdfRow, ...]]:
    codes = PRELIM_JUDGE_CODES[role]
    expected_headers = ("#", "Name", *codes, "Result")
    if tuple(lines[header_index].split()) != expected_headers:
        _fail(f"{role} preliminary columns are missing, duplicated, or changed")
    notice_indices = [
        index
        for index in range(header_index + 1, len(lines))
        if lines[index].startswith("Dancers without any Yes or Alternate marks")
    ]
    if len(notice_indices) != 1:
        _fail(f"{role} omission disclaimer is missing or ambiguous")
    notice_index = notice_indices[0]
    notice_lines = [lines[notice_index]]
    if notice_index + 1 < len(lines) and lines[notice_index + 1] == "page.":
        notice_lines.append(lines[notice_index + 1])
    if " ".join(" ".join(notice_lines).split()) != disclaimer:
        _fail(f"{role} omission disclaimer changed")
    rows: list[DcnScorePdfRow] = []
    seen_bibs: set[str] = set()
    for line in lines[header_index + 1 : notice_index]:
        if line == "DanceConvention.net":
            continue
        tokens = line.split()
        if len(tokens) < 8 or BIB_RE.fullmatch(tokens[0]) is None:
            _fail(f"{role} preliminary row is malformed")
        bib = tokens[0]
        name = " ".join(tokens[1:-6])
        mark_values = tokens[-6:-1]
        result = tokens[-1]
        if bib in seen_bibs or not name or len(name) > MAX_NAME_TEXT:
            _fail(f"{role} preliminary bib is repeated or name is malformed")
        if result not in {"Callback", "Alternate1", "-"}:
            _fail(f"{role} preliminary result is unrecognized")
        seen_bibs.add(bib)
        rows.append(
            DcnScorePdfRow(
                bib,
                (name,),
                role,
                tuple(_mark(code, value) for code, value in zip(codes, mark_values, strict=True)),
                (),
                result,
                "",
                line,
                None,
                "single_person_row",
                "unknown",
            )
        )
    if not rows or len(rows) > MAX_ROWS_PER_PAGE:
        _fail(f"{role} preliminary row count is outside the reviewed bound")
    tail = lines[notice_index + len(notice_lines) :]
    if any(line != "DanceConvention.net" for line in tail):
        _fail(f"{role} preliminary page has unaccounted text after its disclaimer")
    return tuple(notice_lines), tuple(rows)


class ScorePdfPage:
    kind = "dcn.round_pdf"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "extract"

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()

    def extract(self, body: bytes) -> JsonValue:
        if len(body) > MAX_BODY_BYTES:
            raise ExtractError("body exceeds the reviewed byte bound")
        if not body.startswith(b"%PDF-"):
            raise ExtractError("body is not a PDF")
        try:
            reader = PdfReader(BytesIO(body), strict=True)
        except Exception as exc:
            raise ExtractError("cannot read strict PDF structure") from exc
        return {"body_sha256": hashlib.sha256(body).hexdigest(), "pages": _pdf_pages(reader)}

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if ctx.source != "dcn" or ctx.kind != self.kind or ctx.source_ref != EVENT_REF:
            raise ParseError("requires the reviewed DCN event and page kind")
        try:
            url = urlsplit(ctx.url)
        except ValueError as exc:
            raise ParseError("source URL is malformed") from exc
        round_id = ROUND_URLS.get(url.path)
        if (
            url.scheme != "https"
            or url.netloc != HOST
            or round_id is None
            or ctx.url != f"https://{HOST}{url.path}"
        ):
            raise ParseError("source URL is outside the reviewed Riga PDF locators")
        if not isinstance(extract, dict) or set(extract) != {"body_sha256", "pages"}:
            raise ParseError("extracted PDF manifest changed")
        sha = extract["body_sha256"]
        pages = extract["pages"]
        if (
            not isinstance(sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha) is None
            or not isinstance(pages, list)
            or len(pages) not in {1, 2}
        ):
            raise ParseError("extracted PDF page manifest is malformed")
        page_texts: list[tuple[int, float, float, str]] = []
        for expected_number, value in enumerate(pages, start=1):
            if (
                not isinstance(value, dict)
                or set(value) != {"page_number", "width", "height", "text"}
                or value["page_number"] != expected_number
                or not isinstance(value["width"], int | float)
                or not isinstance(value["height"], int | float)
                or not isinstance(value["text"], str)
            ):
                raise ParseError("extracted PDF page entry is malformed")
            page_texts.append(
                (expected_number, float(value["width"]), float(value["height"]), value["text"])
            )
        first_lines = _page_lines(page_texts[0][3])
        if not first_lines:
            _fail("first page is empty")
        document = _heading(first_lines[0])
        if document is None:
            _fail("heading is not one of the reviewed layouts")
        observations: tuple[Observation, ...]
        if document[0] == "finals":
            if len(page_texts) != 1 or round_id != "3451330":
                _fail("finals URL or page count disagrees with the printed layout")
            page = self._final_page(sha, ctx.url, round_id, *page_texts[0])
            observations = (
                Observation(ObservationScope("source_event", EVENT_REF), page.kind, page),
            )
        else:
            if len(page_texts) != 2 or round_id != "3451331":
                _fail("preliminary URL or page count disagrees with the printed layout")
            parsed = tuple(
                self._prelim_page(sha, ctx.url, round_id, *page_values)
                for page_values in page_texts
            )
            if tuple(page.role for page in parsed) != ("leader", "follower"):
                _fail("preliminary role page order or pairing changed")
            observations = tuple(
                Observation(ObservationScope("source_event", EVENT_REF), page.kind, page)
                for page in parsed
            )
        return ParseResult(observations)

    def _final_page(
        self,
        sha: str,
        source_url: str,
        round_id: str,
        page_number: int,
        width: float,
        height: float,
        text: str,
    ) -> DcnScorePdfPage:
        if abs(width - 841.889) > 2 or abs(height - 595.275) > 2:
            _fail("finals page dimensions are not the reviewed landscape sheet")
        lines = _page_lines(text)
        if len(lines) < 4 or lines[:2] != [FINALS_HEADING, EVENT_NAME]:
            _fail("finals title or event name changed")
        legend_index, header_index = _header_and_legend(lines, finals=True)
        if header_index <= legend_index or legend_index <= 2:
            _fail("finals heading, roster, and table order changed")
        judges = _judges(lines[2:legend_index], FINAL_JUDGE_CODES)
        rows = _final_rows(lines, header_index)
        headers = tuple(lines[header_index].split())
        return DcnScorePdfPage(
            "dcn_score_pdf_page",
            sha,
            source_url,
            EVENT_REF,
            round_id,
            page_number,
            lines[0],
            lines[1],
            None,
            lines[legend_index],
            judges,
            headers,
            rows,
            headers[-1],
            None,
            text,
            "unknown",
        )

    def _prelim_page(
        self,
        sha: str,
        source_url: str,
        round_id: str,
        page_number: int,
        width: float,
        height: float,
        text: str,
    ) -> DcnScorePdfPage:
        if abs(width - 595.275) > 2 or abs(height - 841.889) > 2:
            _fail("preliminary page dimensions are not the reviewed portrait sheet")
        lines = _page_lines(text)
        if len(lines) < 5 or lines[1] != EVENT_NAME:
            _fail("preliminary title or event name changed")
        document = _heading(lines[0])
        if document is None or document[0] != "prelims" or document[1] is None:
            _fail("preliminary role heading is missing")
        role = document[1]
        codes = PRELIM_JUDGE_CODES[role]
        legend_index, header_index = _header_and_legend(lines, finals=False)
        if header_index <= legend_index or legend_index <= 2:
            _fail("preliminary heading, roster, and table order changed")
        judges = _judges(lines[2:legend_index], codes)
        notice_lines, rows = _prelim_rows(lines, header_index, role, OMISSION_NOTICE)
        headers = tuple(lines[header_index].split())
        return DcnScorePdfPage(
            "dcn_score_pdf_page",
            sha,
            source_url,
            EVENT_REF,
            round_id,
            page_number,
            lines[0],
            lines[1],
            role,
            lines[legend_index],
            judges,
            headers,
            rows,
            None,
            " ".join(notice_lines),
            text,
            "filtered_subset",
        )


PAGE = ScorePdfPage()
