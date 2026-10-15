"""Fail-closed accounting for SwingDirector score-sheet PDFs (D-0258, D-0259).

`swingdirector.round` reads one PDF per watch. The extract holds every page:
a sheet page (`prelim` or `final`), a page another tool printed
(`unsupported`) or a SwingDirector page the extractor could not read
(`failed`). This module counts the bib rows on the sheet pages again and
checks that each one reached a round sheet, and re-witnesses the parser's
own checks on the sheets it emitted: prelim marks from the legend, one
ordinal per judge, tallies that recompute from the ordinals, and judge
columns that rank every finalist once.

The adapter declares its interpretation through `sources/interpretation.py`,
which calls the contract without the body, so every count here comes from
the extract. This module never imports the source package (its
`__init__` loads the adapter, see `dcn_contract`); the legend it repeats is
pinned equal to `records.PRELIM_MARKS` by `tests/test_swingdirector.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from swingset.sources.base import ParseContext, ParseResult

from .accounting import account_keys
from .report import Field, Guard

KINDS = frozenset({"swingdirector.round"})

#: The prelim legend every captured sheet prints (`records.PRELIM_MARKS`).
PRELIM_MARKS = frozenset({"Y", "N", "A1", "A2", "A3"})
SHEET_KIND = "swingdirector_round_sheet"

#: Findings that leave the rest of the file admissible, with the reason each
#: is bounded. Any other warning (an unreadable SwingDirector page, a final
#: entry without a leader / follower pair) blocks the file.
KNOWN_BOUNDED_WARNINGS = {
    "swingdirector_page_unsupported": "Another tool printed these pages of a merged file; "
    "they carry no SwingDirector sheet and are not read",
    "swingdirector_callbacks_not_printed": "The sheet prints marks only; the projector takes "
    "who advanced from the next round or leaves it unknown (D-0259)",
    "swingdirector_name_hidden": "The sheet hides the name; the bib stays and the row "
    "publishes no name and no marks (D-0259)",
}

_PAGE_KEYS = {
    "prelim": {"page", "title", "layout", "headers", "judges", "rows", "footer"},
    "final": {"page", "title", "layout", "headers", "judges", "rows", "footer"},
    "unsupported": {"page", "layout"},
    "failed": {"page", "layout", "reason"},
}
_ROW_KEYS = {"bib", "heat", "entry", "marks", "tallies", "place"}
_ORDINAL = re.compile(r"\d{1,2}")


@dataclass(frozen=True)
class Accounting:
    fields: tuple[Field, ...]
    guards: tuple[Guard, ...]
    source_count: int | None
    interpreted_count: int
    terminal: str | None
    removal: str


def _ordinals(sheet: Any) -> list[list[int]] | None:
    rows = []
    for row in sheet.rows:
        if len(row.marks_raw) != len(sheet.judges_raw) or not all(
            mark is not None and _ORDINAL.fullmatch(mark) for mark in row.marks_raw
        ):
            return None
        rows.append([int(mark) for mark in row.marks_raw if mark is not None])
    return rows


def _final_witnessed(sheet: Any) -> bool:
    """Tallies recompute from the ordinals, and each judge ranks every finalist once."""
    ordinals = _ordinals(sheet)
    if ordinals is None or not sheet.rows:
        return False
    for row, marks in zip(sheet.rows, ordinals, strict=True):
        expected = []
        for column in range(1, len(sheet.tally_headers_raw) + 1):
            better = [value for value in marks if value <= column]
            expected.append(f"{len(better)}({sum(better)})")
        if list(row.tally_raw) != expected:
            return False
        if row.place_raw is None or not _ORDINAL.fullmatch(row.place_raw):
            return False
    ranking = list(range(1, len(sheet.rows) + 1))
    return all(
        sorted(marks[judge] for marks in ordinals) == ranking
        for judge in range(len(sheet.judges_raw))
    )


def _prelim_witnessed(sheet: Any) -> bool:
    return all(
        len(row.marks_raw) == len(sheet.judges_raw)
        and all(mark in PRELIM_MARKS for mark in row.marks_raw)
        and not row.tally_raw
        and row.place_raw is None
        for row in sheet.rows
    )


def accounting(ctx: ParseContext, extract: Any, result: ParseResult) -> Accounting:
    if ctx.kind not in KINDS:
        raise ValueError(f"unsupported SwingDirector contract kind: {ctx.kind}")
    fields: list[Field] = []
    account_keys(extract, {"pages"}, "$", fields)
    pages = extract.get("pages") if isinstance(extract, dict) else None
    if not isinstance(pages, list):
        fields.append(Field("$.pages", "unknown", "Expected a list of pages"))
        pages = []
    printed = 0
    for index, page in enumerate(pages):
        layout = page.get("layout") if isinstance(page, dict) else None
        if layout not in _PAGE_KEYS:
            fields.append(Field(f"$.pages[*].layout:{layout}", "unknown", "Unreviewed layout"))
            continue
        account_keys(page, _PAGE_KEYS[layout], f"$.pages[{index}]", fields)
        rows = page.get("rows") if layout in {"prelim", "final"} else []
        for number, row in enumerate(rows if isinstance(rows, list) else []):
            account_keys(row, _ROW_KEYS, f"$.pages[{index}].rows[{number}]", fields)
            printed += 1
    sheets = [o.payload for o in result.observations if o.kind == SHEET_KIND]
    for warning in result.warnings:
        reason = KNOWN_BOUNDED_WARNINGS.get(warning.code)
        fields.append(
            Field(
                f"warning:{warning.code}",
                "excluded" if reason else "unknown",
                reason or warning.message,
            )
        )
    fields.append(
        Field(
            "other_files",
            "excluded",
            "One PDF is one watch: a re-fetched file replaces only its own sheets, never "
            "another file's contests of the same event",
        )
    )
    guards = [
        Guard(
            "swingdirector_layouts_known",
            all(sheet.layout in {"prelim", "final"} for sheet in sheets),
            "Every round sheet is a prelim or a final layout",
        ),
        Guard(
            "swingdirector_prelim_marks",
            all(_prelim_witnessed(sheet) for sheet in sheets if sheet.layout == "prelim"),
            "Every prelim row prints one legend mark per judge and no final cells",
        ),
        Guard(
            "swingdirector_final_tallies",
            all(_final_witnessed(sheet) for sheet in sheets if sheet.layout == "final"),
            "Every final row's tallies recompute from its ordinals, it prints a place, and "
            "each judge ranks every finalist once",
        ),
        Guard(
            "swingdirector_unique_bibs",
            all(len({row.bib_raw for row in s.rows}) == len(s.rows) for s in sheets),
            "A sheet prints each bib once",
        ),
    ]
    interpreted = sum(len(sheet.rows) for sheet in sheets)
    return Accounting(
        tuple(fields),
        tuple(guards),
        printed,
        interpreted,
        terminal="captured_document_end" if sheets else None,
        # A round document retires only the claims its own watch owns.
        removal="watch",
    )
