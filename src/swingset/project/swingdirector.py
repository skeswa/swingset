"""SwingDirector score sheets as common round sheets (D-0258, D-0259).

`sources/swingdirector` keeps each printed sheet in source vocabulary. This
module turns one event's sheets into the `RoundSheet` tables the contest
projector already reads, and adds the one fact the prelim sheets do not
print: who advanced. That comes from the roster of the contest's next
round, and only when the roster proves it is the next round:

- The next round is the contest's next stage (prelim, quarterfinal,
  semifinal, final) on any captured sheet of either role. A stage captured
  for one role but not the other leaves the other role underived.
- Into a semifinal, a row advanced when its bib is on the same role's
  semifinal sheet. Into a final, a leader advanced when the final prints
  their bib (the final's bib is the leader's); a follower when the final
  prints their name, compared as `normalize_name` values.
- Every entrant of the next round must match exactly one row, and at most
  one row that did not advance may outscore the lowest row that did (a
  scratch). Otherwise the next round is not this round's successor, or the
  match is unsure, and the round keeps its marks without outcomes.
- An advanced row is `promoted`. A row scoring below every advanced row is
  `eliminated`. A row that did not advance but scored at least the cut (a
  tie-break or a scratch) has no outcome. Marks score Y 10, A1 4.5, A2
  4.3, A3 4.2, N 0; `A1`-`A3` are judges' alternate ranks, never outcomes.

Names printed as `(Name hidden for discretion)` hide that bib's name in
every sheet of the contest and role: prelim and semifinal rows keep their
bib without a name or marks, as WDR's redacted rows do, and a hidden side of
a final pair is left out while its partner stays.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from swingset.normalize.names import normalize_name
from swingset.sources.records import Cell, ResultRow, ResultTable, RoundSheet
from swingset.sources.swingdirector.records import DirectorRoundSheet, DirectorRow

HIDDEN = "(Name hidden for discretion)"
#: The contest projector's redaction marker for a nameless row with a bib.
REDACTED = "***"
MARK_VALUES = {"Y": 10.0, "A1": 4.5, "A2": 4.3, "A3": 4.2, "N": 0.0}
STAGES = {"prelim": 1, "quarterfinal": 2, "semifinal": 3, "final": 4}
#: Rows that did not advance yet outscore the cut; more means another round.
OUTSCORING_LIMIT = 1
#: The header the contest projector reads a derived outcome from.
OUTCOME = "Outcome"
_TALLY = re.compile(r"(\d{1,2})\(\d{1,3}\)")

type Stage = tuple[int, int]
type Group = tuple[str, str | None]


@dataclass(frozen=True, slots=True)
class Derivation:
    """Outcomes by bib for one prelim-layout sheet, or why there are none."""

    outcomes: dict[str, str | None]
    reason: str | None = None


def round_sheets(payloads: Sequence[object]) -> list[object]:
    """Replace each `DirectorRoundSheet` with its `RoundSheet`, or None.

    Other payloads pass through unchanged. `payloads` are one event's
    observations, oldest first; the newest sheet of each source round is the
    current one, and only current sheets supply next-round rosters.
    """
    sheets = [payload for payload in payloads if isinstance(payload, DirectorRoundSheet)]
    if not sheets:
        return list(payloads)
    current = {sheet.source_round_ref: sheet for sheet in sheets}
    hidden = _hidden_bibs(current.values())
    derivations = {ref: derive(sheet, current.values()) for ref, sheet in current.items()}
    result: list[object] = []
    for payload in payloads:
        if not isinstance(payload, DirectorRoundSheet):
            result.append(payload)
            continue
        derivation = derivations[payload.source_round_ref]
        if payload is not current[payload.source_round_ref]:
            # An older capture of the round shows its own rows, never outcomes
            # derived from today's rosters.
            derivation = Derivation({}, "superseded")
        result.append(round_sheet(payload, derivation, hidden))
    return result


def round_sheet(
    sheet: DirectorRoundSheet,
    derivation: Derivation | None = None,
    hidden: dict[Group, set[str]] | None = None,
) -> RoundSheet | None:
    """One sheet as a `RoundSheet`; None when its layout and round disagree."""
    stage = _stage(sheet)
    if stage is None:
        return None
    contest = _contest(sheet)
    judges = tuple(Cell(name, (("title", name),)) for name in sheet.judges_raw)
    if sheet.layout == "final":
        table = _final_table(sheet, judges, (hidden or {}).get((contest, "Leaders"), set()))
        round_name = (
            sheet.round_name_raw if _round_type(sheet.round_name_raw) == "final" else "Final"
        )
    else:
        role = "Leader" if sheet.role_raw == "Leaders" else "Follower"
        concealed = (hidden or {}).get((contest, sheet.role_raw), set())
        outcomes = derivation.outcomes if derivation and derivation.reason is None else None
        headers = (Cell("Bib"), Cell(role), *judges, *((Cell(OUTCOME),) if outcomes else ()))
        rows = tuple(
            ResultRow(
                (
                    Cell(row.bib_raw),
                    Cell(REDACTED if _hidden(row) or row.bib_raw in concealed else row.entry_raw),
                    *(Cell(mark) for mark in row.marks_raw),
                    *((Cell(outcomes.get(row.bib_raw)),) if outcomes else ()),
                )
            )
            for row in sheet.rows
        )
        table = ResultTable(sheet.title_raw, headers, rows)
        round_name = sheet.round_name_raw
    return RoundSheet(
        "round_sheet",
        sheet.source_event_ref,
        sheet.source_round_ref,
        sheet.contest_name_raw,
        round_name,
        (table,),
    )


def derive(sheet: DirectorRoundSheet, sheets: Iterable[DirectorRoundSheet]) -> Derivation:
    """Who on a prelim-layout sheet danced the contest's next round."""
    stage = _stage(sheet)
    if sheet.layout == "final" or stage is None or sheet.role_raw is None:
        return Derivation({}, "not_a_callback_round")
    contest = _contest(sheet)
    stages: dict[Stage, dict[str | None, list[DirectorRoundSheet]]] = {}
    for other in sheets:
        other_stage = _stage(other)
        if other_stage is not None and _contest(other) == contest:
            stages.setdefault(other_stage, {}).setdefault(other.role_raw, []).append(other)
    if len(stages[stage].get(sheet.role_raw, ())) != 1:
        return Derivation({}, "round_repeated")
    later = sorted(candidate for candidate in stages if candidate > stage)
    if not later:
        return Derivation({}, "no_next_round")
    following = stages[later[0]]
    final = later[0][0] == STAGES["final"]
    roster_sheets = following.get(None if final else sheet.role_raw, [])
    if len(roster_sheets) != 1:
        return Derivation({}, "next_round_missing" if not roster_sheets else "round_repeated")
    roster = roster_sheets[0]
    leaders = sheet.role_raw == "Leaders"
    if not final or leaders:
        keys = [row.bib_raw for row in roster.rows]
        rows_by_key = {row.bib_raw: row for row in sheet.rows}
    else:
        pairs = [row.entry_raw.split(" / ") for row in roster.rows]
        if any(len(pair) != 2 or HIDDEN in pair for pair in pairs):
            return Derivation({}, "next_round_unnamed")
        keys = [normalize_name(pair[1]).value for pair in pairs]
        named = [row for row in sheet.rows if not _hidden(row)]
        rows_by_key = {normalize_name(row.entry_raw).value: row for row in named}
        if len(rows_by_key) != len(named):
            return Derivation({}, "names_repeat")
    if len(set(keys)) != len(keys) or any(key not in rows_by_key for key in keys):
        return Derivation({}, "next_round_unmatched")
    advanced = {rows_by_key[key].bib_raw for key in keys}
    if not advanced:
        return Derivation({}, "next_round_empty")
    cut = min(_score(row) for row in sheet.rows if row.bib_raw in advanced)
    outscoring = [row for row in sheet.rows if row.bib_raw not in advanced and _score(row) > cut]
    if len(outscoring) > OUTSCORING_LIMIT:
        return Derivation({}, "next_round_not_successor")
    return Derivation(
        {
            row.bib_raw: "promoted"
            if row.bib_raw in advanced
            else "eliminated"
            if _score(row) < cut
            else None
            for row in sheet.rows
        }
    )


def _final_table(
    sheet: DirectorRoundSheet, judges: tuple[Cell, ...], hidden_leaders: set[str]
) -> ResultTable:
    pairs = [row.entry_raw.split(" / ") for row in sheet.rows]
    split = all(len(pair) == 2 and all(part.strip() for part in pair) for pair in pairs)
    competitors = (Cell("Leader"), Cell("Follower")) if split else (Cell("Couple"),)
    headers = (Cell("Bib"), *competitors, *judges, Cell("Tally"), Cell("Place"))
    rows = []
    for row, pair in zip(sheet.rows, pairs, strict=True):
        names: tuple[str | None, ...]
        if split:
            leader, follower = (part.strip() for part in pair)
            names = (
                None if leader == HIDDEN or row.bib_raw in hidden_leaders else leader,
                None if follower == HIDDEN else follower,
            )
        else:
            names = (None if row.entry_raw == HIDDEN else row.entry_raw,)
        rows.append(
            ResultRow(
                (
                    Cell(row.bib_raw),
                    *(Cell(name) for name in names),
                    *(Cell(mark) for mark in row.marks_raw),
                    Cell(_tally(row)),
                    Cell(row.place_raw),
                )
            )
        )
    return ResultTable(sheet.title_raw, headers, tuple(rows))


def _tally(row: DirectorRow) -> str:
    """Relative-placement counts `C1..Cn`, as the projector's `n-n-n` form."""
    counts = []
    for cell in row.tally_raw:
        match = _TALLY.fullmatch(cell or "")
        if match is None:
            return ""
        counts.append(match.group(1))
    return "-".join(counts)


def _hidden_bibs(sheets: Iterable[DirectorRoundSheet]) -> dict[Group, set[str]]:
    """Bibs a contest's sheets print hidden in any round, by contest and role."""
    hidden: dict[Group, set[str]] = {}
    for sheet in sheets:
        if sheet.layout == "final":
            for row in sheet.rows:
                if row.entry_raw.split(" / ")[0].strip() == HIDDEN:
                    hidden.setdefault((_contest(sheet), "Leaders"), set()).add(row.bib_raw)
            continue
        for row in sheet.rows:
            if _hidden(row):
                hidden.setdefault((_contest(sheet), sheet.role_raw), set()).add(row.bib_raw)
    return hidden


def _hidden(row: DirectorRow) -> bool:
    return row.entry_raw == HIDDEN


def _score(row: DirectorRow) -> float:
    return sum(MARK_VALUES.get(mark or "", 0.0) for mark in row.marks_raw)


def _contest(sheet: DirectorRoundSheet) -> str:
    return " ".join(sheet.contest_name_raw.split())


def _round_type(name: str) -> str:
    value = name.casefold()
    if "semi" in value:
        return "semifinal"
    if "quarter" in value:
        return "quarterfinal"
    if "final" in value:
        return "final"
    return "prelim"


def _stage(sheet: DirectorRoundSheet) -> Stage | None:
    """A sheet's place in its contest; None when the layout contradicts the title."""
    kind = _round_type(sheet.round_name_raw)
    if sheet.layout == "final":
        # Finals print `Scores` as their round; the layout is the evidence.
        if kind != "final" and sheet.round_name_raw.casefold() != "scores":
            return None
        return STAGES["final"], 1
    if kind == "final" or sheet.role_raw not in {"Leaders", "Followers"}:
        return None
    match = re.search(r"\b(\d+)\b", sheet.round_name_raw)
    return STAGES[kind], int(match.group(1)) if match else 1
