"""SwingDirector and CountryDanceDirector score-sheet PDFs on event sites.

Several event sites post the PDFs this scoring tool prints: one prelim,
semifinal or final sheet per file, or several merged into one file. The
layout is read from text positions, never from line order: judge names are
printed rotated above their columns, entry names wrap onto a second line and
narrow tally cells wrap their closing parenthesis. Unknown layouts fail
closed. The adapter emits source vocabulary only; `project/swingdirector.py`
projects it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from pypdf import PdfReader

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
from swingset.sources.common import source_key
from swingset.sources.interpretation import declared

from .records import PRELIM_MARKS, DirectorRoundSheet, DirectorRow

NAME = "swingdirector"
#: Event sites whose captured PDFs match this layout (2026-10-15 review).
HOSTS = frozenset({"highdesertdanceclassic.com", "sbdf.dance", "calgarydancestampede.com"})
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_PAGES = 40
MAX_ITEMS_PER_PAGE = 4000
MAX_TEXT = 200
MAX_JUDGES = 15
#: A page's decoded content stream; captured pages use under 40 KB.
MAX_CONTENT_BYTES = 1024 * 1024
#: Wall-clock bound for one file. Captured files take under a second.
MAX_SECONDS = 20.0
_clock = time.monotonic
#: Text origins on one printed line differ by under a point; a wrapped line
#: sits 10 points lower. Column anchors are at least 22 points apart.
LINE_TOLERANCE = 2.0
ROW_LEAD = 3.0
COLUMN_TOLERANCE = 12.0
PRELIM_HEADERS = ("Heat", "Bib", "Contestant(s)")
FINAL_PREFIX = ("Bib", "Entry")
FOOTER_RE = re.compile(r"\b(?:Swing|CountryDance)Director\.com\b")
PAGE_RE = re.compile(r"Page \d{1,3}")
BIB_RE = re.compile(r"\d{1,4}")
ORDINAL_RE = re.compile(r"\d{1,2}")
TALLY_RE = re.compile(r"\d{1,2}\(\d{1,3}\)")
ROLE_RE = re.compile(r"^(?P<round>.+?)\s*\((?P<role>Leaders|Followers)\)$")


class _BoundExceeded(ExtractError):
    """A resource bound fails the whole file, never only one page."""


@dataclass(frozen=True)
class _Item:
    text: str
    x: float
    y: float
    size: float
    vertical: bool


def _items(page: Any, deadline: float) -> list[_Item]:
    contents = page.get_contents()
    if contents is not None and len(contents.get_data()) > MAX_CONTENT_BYTES:
        raise _BoundExceeded("SwingDirector page content exceeds the reviewed bound")
    rotate = int(page.get("/Rotate", 0) or 0) % 360
    box = page.mediabox
    if rotate not in (0, 90) or float(box.left) != 0 or float(box.bottom) != 0:
        raise ExtractError("SwingDirector page geometry is unsupported")
    height = float(box.height)
    found: list[_Item] = []

    def visit(text: str, cm: list[float], tm: list[float], font: Any, size: float) -> None:
        value = " ".join(text.split())
        if not value:
            return
        if len(value) > MAX_TEXT or len(found) >= MAX_ITEMS_PER_PAGE:
            raise _BoundExceeded("SwingDirector page text exceeds the reviewed bound")
        if _clock() > deadline:
            raise _BoundExceeded("SwingDirector extraction exceeded its time bound")
        tx, ty = tm[4], tm[5]
        x = cm[0] * tx + cm[2] * ty + cm[4]
        y = cm[1] * tx + cm[3] * ty + cm[5]
        dx = tm[0] * cm[0] + tm[1] * cm[2]
        dy = tm[0] * cm[1] + tm[1] * cm[3]
        # Visual coordinates: origin top left, y grows downward.
        if rotate == 0:
            vx, vy, ux, uy = x, height - y, dx, -dy
        else:
            vx, vy, ux, uy = y, x, dy, dx
        if abs(ux) > abs(uy) and ux <= 0:
            raise ExtractError("SwingDirector page has mirrored text")
        found.append(_Item(value, vx, vy, float(size), abs(uy) > abs(ux)))

    page.extract_text(visitor_text=visit)
    return found


def _one(items: list[_Item], text: str) -> _Item:
    matches = [item for item in items if item.text == text and not item.vertical]
    if len(matches) != 1:
        raise ExtractError(f"SwingDirector header {text!r} is missing or repeated")
    return matches[0]


def _join(parts: list[_Item], separator: str) -> str:
    return separator.join(item.text for item in sorted(parts, key=lambda item: (item.y, item.x)))


def _cell(cells: dict[tuple[str, int], list[_Item]], name: str, index: int) -> str | None:
    parts = cells.get((name, index))
    return _join(parts, "") if parts else None


def _page(page: Any, number: int, deadline: float) -> dict[str, JsonValue]:
    """One page; a SwingDirector page that cannot be read is kept as `failed`."""
    if _clock() > deadline:
        raise _BoundExceeded("SwingDirector extraction exceeded its time bound")
    try:
        return _sheet_page(_items(page, deadline), number)
    except _BoundExceeded:
        raise
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ExtractError) else "unreadable page text"
        return {"page": number, "layout": "failed", "reason": reason}


def _sheet_page(items: list[_Item], number: int) -> dict[str, JsonValue]:
    footer_marks = [item for item in items if FOOTER_RE.search(item.text) and not item.vertical]
    if not footer_marks:
        # A merged file can hold pages another tool printed. They stay visible
        # as unsupported pages; a file with no SwingDirector page fails below.
        return {"page": number, "layout": "unsupported"}
    if len(footer_marks) != 1:
        raise ExtractError("SwingDirector footer is repeated")
    footer_top = footer_marks[0].y - LINE_TOLERANCE
    footer = [item for item in items if item.y >= footer_top and not item.vertical]
    items = [item for item in items if item.y < footer_top]
    bib = _one(items, "Bib")
    header = sorted(
        (item for item in items if not item.vertical and abs(item.y - bib.y) <= LINE_TOLERANCE),
        key=lambda item: item.x,
    )
    labels = tuple(item.text for item in header)
    if labels == PRELIM_HEADERS:
        layout = "prelim"
        tallies: list[_Item] = []
        place: _Item | None = None
    elif (
        labels[:2] == FINAL_PREFIX
        and labels[-1] == "Place"
        and labels[2:-1] == tuple(f"C{index}" for index in range(1, len(labels) - 2))
        and len(labels) > 3
    ):
        layout = "final"
        tallies = list(header[2:-1])
        place = header[-1]
    else:
        raise ExtractError(f"SwingDirector header layout is unknown: {labels!r}")
    titles = [item for item in items if not item.vertical and item.y < bib.y - LINE_TOLERANCE]
    if len(titles) != 1:
        raise ExtractError("SwingDirector sheet title is missing or split")
    judges = sorted((item for item in items if item.vertical), key=lambda item: item.x)
    if not 1 <= len(judges) <= MAX_JUDGES:
        raise ExtractError("SwingDirector judge panel is missing or too large")
    entry_x = header[2].x if layout == "prelim" else header[1].x
    heat_x = header[0].x if layout == "prelim" else None
    body = [item for item in items if not item.vertical and item.y > bib.y + LINE_TOLERANCE]
    anchors = sorted(
        (
            item
            for item in body
            if abs(item.x - bib.x) <= LINE_TOLERANCE and BIB_RE.fullmatch(item.text)
        ),
        key=lambda item: item.y,
    )
    columns: list[tuple[str, int, float]] = [("mark", i, j.x) for i, j in enumerate(judges)]
    columns += [("tally", i, t.x) for i, t in enumerate(tallies)]
    if place is not None:
        columns.append(("place", 0, place.x))
    cells: list[dict[tuple[str, int], list[_Item]]] = [{} for _ in anchors]
    for item in body:
        if item in anchors:
            continue
        row = None
        for index, anchor in enumerate(anchors):
            if anchor.y - ROW_LEAD <= item.y:
                row = index
        if row is None:
            raise ExtractError("SwingDirector text sits above the first bib row")
        if abs(item.x - entry_x) <= LINE_TOLERANCE:
            key: tuple[str, int] = ("entry", 0)
        elif heat_x is not None and abs(item.x - heat_x) <= LINE_TOLERANCE:
            key = ("heat", 0)
        else:
            near = min(columns, key=lambda column: abs(column[2] - item.x))
            if abs(near[2] - item.x) > COLUMN_TOLERANCE:
                raise ExtractError("SwingDirector cell sits outside every column")
            key = (near[0], near[1])
        cells[row].setdefault(key, []).append(item)
    rows = []
    for anchor, row_cells in zip(anchors, cells, strict=True):
        if ("entry", 0) not in row_cells:
            raise ExtractError("SwingDirector bib row has no entry")

        rows.append(
            {
                "bib": anchor.text,
                "heat": _cell(row_cells, "heat", 0),
                "entry": _join(row_cells[("entry", 0)], " "),
                "marks": [_cell(row_cells, "mark", index) for index in range(len(judges))],
                "tallies": [_cell(row_cells, "tally", index) for index in range(len(tallies))],
                "place": _cell(row_cells, "place", 0),
            }
        )
    if not rows:
        raise ExtractError("SwingDirector sheet has no bib rows")
    if any(not titles[0].y < judge.y < anchors[0].y - ROW_LEAD for judge in judges):
        raise ExtractError("SwingDirector rotated text sits outside the header")
    return {
        "page": number,
        "title": titles[0].text,
        "layout": layout,
        "headers": list(labels),
        "judges": [judge.text for judge in judges],
        "rows": rows,
        "footer": _join([item for item in footer if not PAGE_RE.fullmatch(item.text)], " "),
    }


def _split_title(title: str) -> tuple[str, str, str | None]:
    contest, separator, round_name = title.rpartition(" - ")
    if not separator or not contest or not round_name:
        raise ParseError("SwingDirector title has no round part")
    role = None
    if match := ROLE_RE.fullmatch(round_name):
        round_name, role = match["round"], match["role"]
    return contest, round_name, role


def _check_rows(sheet: dict[str, Any], warnings: list[ParseWarning]) -> None:
    judges = len(sheet["judges"])
    for row in sheet["rows"]:
        marks = row["marks"]
        if sheet["layout"] == "prelim":
            unknown = [mark for mark in marks if mark not in PRELIM_MARKS]
            if unknown:
                raise ParseError(f"SwingDirector callback mark is not in the legend: {unknown!r}")
            if row["tallies"] or row["place"] is not None:
                raise ParseError("SwingDirector prelim row carries final cells")
        else:
            if any(mark is None or not ORDINAL_RE.fullmatch(mark) for mark in marks):
                raise ParseError("SwingDirector final ordinal is missing or not a number")
            if any(tally is None or not TALLY_RE.fullmatch(tally) for tally in row["tallies"]):
                raise ParseError("SwingDirector tally cell is missing or malformed")
            if row["place"] is None or not ORDINAL_RE.fullmatch(row["place"]):
                raise ParseError("SwingDirector placement is missing")
            if len(marks) != judges:
                raise ParseError("SwingDirector ordinal count differs from the panel")
            # Column Ck prints how many judges placed the couple k or better and
            # the sum of those ordinals. Recomputing it witnesses that the row's
            # ordinals were read in full; it cannot tell judges apart, so
            # _check_judge_columns witnesses which judge each ordinal belongs to.
            ordinals = [int(mark) for mark in marks]
            expected = []
            for column in range(1, len(row["tallies"]) + 1):
                better = [value for value in ordinals if value <= column]
                expected.append(f"{len(better)}({sum(better)})")
            if expected != row["tallies"]:
                raise ParseError("SwingDirector tally disagrees with the printed ordinals")
            if " / " not in row["entry"]:
                warnings.append(
                    ParseWarning(
                        "swingdirector_couple_unsplit",
                        "A final entry does not print a leader / follower pair.",
                        {"bib": row["bib"]},
                    )
                )


def _check_judge_columns(sheet: dict[str, Any]) -> None:
    """Each judge ranks every finalist once: a column must be exactly 1..n."""
    if sheet["layout"] != "final":
        return
    expected = list(range(1, len(sheet["rows"]) + 1))
    for index in range(len(sheet["judges"])):
        if sorted(int(row["marks"][index]) for row in sheet["rows"]) != expected:
            raise ParseError("SwingDirector judge column is not a ranking of every finalist")


class RoundPdf:
    """One PDF file; each run of pages with one title is one sheet."""

    kind = "swingdirector.round"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "validators"

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()

    def extract(self, body: bytes) -> JsonValue:
        if not body.startswith(b"%PDF-") or len(body) > MAX_BODY_BYTES:
            raise ExtractError("SwingDirector body is not a bounded PDF")
        try:
            reader = PdfReader(BytesIO(body))
            if reader.is_encrypted:
                raise ExtractError("encrypted documents are unsupported")
            pages = reader.pages
            if not 1 <= len(pages) <= MAX_PAGES:
                raise ExtractError("SwingDirector page count exceeds the reviewed bound")
            deadline = _clock() + MAX_SECONDS
            extracted = [_page(page, number, deadline) for number, page in enumerate(pages, 1)]
        except ExtractError:
            raise
        except Exception as exc:
            raise ExtractError("cannot read SwingDirector PDF text positions") from exc
        if all(page["layout"] == "unsupported" for page in extracted):
            raise ExtractError("no page carries the SwingDirector footer")
        if all(page["layout"] in {"unsupported", "failed"} for page in extracted):
            raise ExtractError("no SwingDirector page is readable")
        return {"pages": extracted}

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not ctx.source_ref or not ctx.source_ref.startswith(f"{NAME}:"):
            raise ParseError("SwingDirector sheets need an event reference from their watch")
        sheets: list[dict[str, Any]] = []
        unsupported = [page["page"] for page in extract["pages"] if page["layout"] == "unsupported"]
        failed = [
            {"page": page["page"], "reason": page["reason"]}
            for page in extract["pages"]
            if page["layout"] == "failed"
        ]
        for page in extract["pages"]:
            if page["layout"] in {"unsupported", "failed"}:
                sheets.append({"title": None})  # A foreign page ends a continuation run.
                continue
            previous = sheets[-1] if sheets else None
            if previous and previous["title"] == page["title"]:
                if (previous["judges"], previous["headers"]) != (page["judges"], page["headers"]):
                    raise ParseError("SwingDirector continuation page changes its panel")
                previous["rows"] = [*previous["rows"], *page["rows"]]
                previous["pages"].append(page["page"])
                continue
            if any(sheet["title"] == page["title"] for sheet in sheets):
                raise ParseError("SwingDirector title repeats after another sheet")
            sheets.append({**page, "pages": [page["page"]]})
        sheets = [sheet for sheet in sheets if sheet["title"] is not None]
        base = source_key(NAME, ctx.url)
        observations = []
        warnings: list[ParseWarning] = []
        if failed:
            # parsing.md: an unreadable layout stays visible; the rest is kept.
            warnings.append(
                ParseWarning(
                    "swingdirector_page_failed",
                    "SwingDirector pages whose layout could not be read; a sheet that "
                    "continued on them may be missing rows.",
                    {"pages": failed},
                )
            )
        if unsupported:
            warnings.append(
                ParseWarning(
                    "swingdirector_page_unsupported",
                    "Pages without the SwingDirector footer were printed by another tool.",
                    {"pages": unsupported},
                )
            )
        for index, sheet in enumerate(sheets, 1):
            bibs = [row["bib"] for row in sheet["rows"]]
            if len(set(bibs)) != len(bibs):
                raise ParseError("SwingDirector sheet repeats a bib")
            _check_rows(sheet, warnings)
            _check_judge_columns(sheet)
            contest, round_name, role = _split_title(sheet["title"])
            if sheet["layout"] == "prelim":
                warnings.append(
                    ParseWarning(
                        "swingdirector_callbacks_not_printed",
                        "The sheet prints marks only; who advanced is not printed.",
                        {"sheet": index},
                    )
                )
            hidden = sum(row["entry"] == "(Name hidden for discretion)" for row in sheet["rows"])
            if hidden:
                warnings.append(
                    ParseWarning(
                        "swingdirector_name_hidden",
                        "Rows print a hidden name; the bib and marks stay.",
                        {"sheet": index, "rows": hidden},
                    )
                )
            payload = DirectorRoundSheet(
                "swingdirector_round_sheet",
                ctx.source_ref,
                f"{base}:{index}",
                sheet["title"],
                contest,
                round_name,
                role,
                sheet["layout"],
                tuple(sheet["judges"]),
                tuple(sheet["headers"][2:-1]) if sheet["layout"] == "final" else (),
                tuple(
                    DirectorRow(
                        row["bib"],
                        row["heat"],
                        row["entry"],
                        tuple(row["marks"]),
                        tuple(row["tallies"]),
                        row["place"],
                    )
                    for row in sheet["rows"]
                ),
                tuple(sheet["pages"]),
                sheet["footer"],
            )
            observations.append(
                Observation(ObservationScope("source_event", ctx.source_ref), payload.kind, payload)
            )
        return ParseResult(tuple(observations), warnings=tuple(warnings))


def watches_from_overrides(rows: object) -> list[WatchSpec]:
    """One watch per reviewed PDF row; the row's event id names the source event.

    `discover` also adds a generic spec for each row, keyed by URL; the same
    watch id inserts once, so this event-level reference wins.
    """
    result: list[WatchSpec] = []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict) or row.get("source") != NAME:
            continue
        event_id = str(row.get("event_id", "")).strip()
        if not event_id:
            continue
        result.append(
            WatchSpec(
                "",
                NAME,
                str(row.get("kind") or "event"),
                "GET",
                str(row.get("url", "")),
                RoundPdf.kind,
                source_ref=f"{NAME}:{event_id}",
                notes=str(row.get("notes", "")) or None,
            )
        )
    return result


@dataclass(frozen=True)
class SwingDirectorSource:
    name: str = NAME
    hosts: frozenset[str] = HOSTS
    page_kinds = {RoundPdf.kind: RoundPdf()}

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return watches_from_overrides(overrides)


SOURCE = SwingDirectorSource()
