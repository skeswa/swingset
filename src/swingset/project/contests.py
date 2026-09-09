"""Projection of round-sheet evidence into complete event-scoped rows."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import TypedDict

from swingset.model.canonical import (
    Callback,
    CallbackMark,
    Contest,
    Entry,
    FinalMark,
    Judge,
    Placement,
    Round,
)
from swingset.model.ids import entry_id, judge_id, placement_id, round_id, slug, unique_slugs
from swingset.model.observations import decode_payload
from swingset.normalize.divisions import classify_contest
from swingset.normalize.names import normalize_name
from swingset.sources.records import Cell, ResultTable, RoundSheet
from swingset.state.findings import Finding

from .writer import Projection


@dataclass(frozen=True, slots=True)
class Evidence:
    sheet: RoundSheet
    source: str
    snapshot_id: str
    parser_version: str
    fetched_at: str
    page_kind: str

    @property
    def precedence(self) -> tuple[int, str, str]:
        specificity = 3 if "round" in self.page_kind else 2 if "event" in self.page_kind else 1
        return specificity, self.fetched_at, self.snapshot_id


@dataclass(slots=True)
class EntryFacts:
    entry_id: str
    contest_id: str
    event_id: str
    role: str
    bib: str | None
    name_raw: str | None
    evidence: Evidence
    rounds: set[str] = field(default_factory=set)
    partner_name_raw: str | None = None
    partner_entry_id: str | None = None


class ProvenanceValues(TypedDict):
    source: str
    snapshot_id: str
    parser_version: str
    first_seen_at: str
    last_seen_at: str
    run_id: str


def project_event(conn: sqlite3.Connection, event: str, now: str, run_id: str) -> Projection:
    evidence = _evidence(conn, event)
    groups: dict[str, list[Evidence]] = {}
    for item in evidence:
        groups.setdefault(_contest_slug(item.sheet.contest_name_raw), []).append(item)
    contest_slugs = dict(zip(groups, unique_slugs(groups), strict=True))
    output: list[
        Contest | Round | Entry | Judge | CallbackMark | Callback | FinalMark | Placement
    ] = []
    findings: list[Finding] = []
    entries: dict[str, EntryFacts] = {}
    judges: dict[str, tuple[Judge, Evidence]] = {}
    marks: dict[tuple[str, str, str], CallbackMark] = {}
    final_marks: dict[tuple[str, str, str], FinalMark] = {}
    callbacks: dict[tuple[str, str], Callback] = {}
    placements: dict[str, Placement] = {}
    round_types: dict[str, str] = {}

    for contest_key, sheets in groups.items():
        cid = f"{event}/{contest_slugs[contest_key]}"
        winner = max(sheets, key=lambda item: item.precedence)
        contest_name = winner.sheet.contest_name_raw
        for candidate in sheets:
            if candidate.sheet.contest_name_raw != contest_name:
                findings.append(
                    Finding(
                        kind="conflict",
                        subject_kind="contest",
                        subject_id=cid,
                        severity="warning",
                        summary=f"Conflicting contest name for {cid}",
                        evidence={
                            "field": "name_raw",
                            "old": candidate.sheet.contest_name_raw,
                            "new": contest_name,
                            "snapshots": [candidate.snapshot_id, winner.snapshot_id],
                        },
                    )
                )
        vocabulary = classify_contest(contest_name)
        output.append(
            Contest(
                contest_id=cid,
                event_id=event,
                name_raw=contest_name,
                division=vocabulary.division,
                age_division=vocabulary.age_division,
                contest_type=vocabulary.contest_type,
                partner_mode=vocabulary.partner_mode,
                dance_style=vocabulary.dance_style,
                wsdc_points_eligible=vocabulary.contest_type == "jack_and_jill"
                and vocabulary.division not in {"none", "open", "invitational"},
                combined_from=vocabulary.combined_from,
                parse_status="parsed"
                if any(item.sheet.tables for item in sheets)
                else "unsupported",
                source_contest_ref=winner.sheet.source_round_ref,
                **_provenance(winner, now, run_id),
            )
        )
        round_groups: dict[tuple[str, int], list[Evidence]] = {}
        for item in sheets:
            kind = _round_type(item.sheet.round_name_raw)
            round_groups.setdefault((kind, _round_number(item.sheet.round_name_raw)), []).append(
                item
            )
        ordered_rounds = sorted(
            round_groups.items(),
            key=lambda item: (
                {"prelim": 1, "quarterfinal": 2, "semifinal": 3, "final": 4}[item[0][0]],
                item[0][1],
            ),
        )
        for round_index, ((round_type, occurrence), candidates) in enumerate(ordered_rounds, 1):
            rid = round_id(cid, round_type, occurrence)
            round_types[rid] = round_type
            selected = max(candidates, key=lambda item: item.precedence)
            tables = tuple(table for item in candidates for table in item.sheet.tables)
            judge_count = len(
                {
                    _judge_token(cell, index)
                    for table in tables
                    for index, cell in enumerate(table.headers)
                    if _is_judge(cell)
                }
            )
            danced = max((len(table.rows) for table in selected.sheet.tables), default=0)
            promoted = _promoted_count(selected.sheet.tables) if round_type != "final" else None
            output.append(
                Round(
                    round_id=rid,
                    contest_id=cid,
                    round_type=round_type,
                    round_index=round_index,
                    name_raw=selected.sheet.round_name_raw,
                    scoring_method="relative_placement" if round_type == "final" else "callback",
                    callback_legend=_legend(tables),
                    judge_count=judge_count,
                    chief_judge_id=None,
                    entry_count=danced,
                    promoted_count=promoted,
                    source_round_ref=selected.sheet.source_round_ref,
                    score_sheet_url=None,
                    **_provenance(selected, now, run_id),
                )
            )
            for table in selected.sheet.tables:
                _project_table(
                    event,
                    cid,
                    rid,
                    round_type,
                    selected,
                    table,
                    now,
                    run_id,
                    entries,
                    judges,
                    marks,
                    callbacks,
                    final_marks,
                    placements,
                    findings,
                )
            for item in candidates:
                if item is selected:
                    continue
                for table in item.sheet.tables:
                    _record_conflicts(cid, item, table, entries, findings)

    order = {"prelim": 1, "quarterfinal": 2, "semifinal": 3, "final": 4}
    for facts in entries.values():
        rounds = tuple(sorted({round_types[rid] for rid in facts.rounds}, key=order.__getitem__))
        output.append(
            Entry(
                entry_id=facts.entry_id,
                contest_id=facts.contest_id,
                event_id=facts.event_id,
                role=facts.role,
                bib=facts.bib,
                name_raw=facts.name_raw,
                name_norm=normalize_name(facts.name_raw or "").value or None,
                partner_name_raw=facts.partner_name_raw,
                partner_entry_id=facts.partner_entry_id,
                wsdc_id=None,
                link_status="unmatched",
                link_confidence=0.0,
                rounds_danced=rounds,
                best_round=max(rounds, key=order.__getitem__) if rounds else None,
                **_provenance(facts.evidence, now, run_id),
            )
        )
    output.extend(value[0] for value in judges.values())
    output.extend(marks.values())
    output.extend(callbacks.values())
    output.extend(placements.values())
    output.extend(final_marks.values())
    return Projection(tuple(output), tuple(findings))


def _project_table(
    event: str,
    contest: str,
    round_: str,
    round_type: str,
    evidence: Evidence,
    table: ResultTable,
    now: str,
    run_id: str,
    entries: dict[str, EntryFacts],
    judges: dict[str, tuple[Judge, Evidence]],
    marks: dict[tuple[str, str, str], CallbackMark],
    callbacks: dict[tuple[str, str], Callback],
    final_marks: dict[tuple[str, str, str], FinalMark],
    placements: dict[str, Placement],
    findings: list[Finding],
) -> None:
    headers = [_text(cell).casefold() for cell in table.headers]
    judge_columns: dict[int, str] = {}
    for index, cell in enumerate(table.headers):
        if not _is_judge(cell):
            continue
        token, name, anonymous = _judge_token(cell, index)
        jid = (
            judge_id(event, name=name)
            if not anonymous
            else judge_id(event, anonymous_number=int(token.removeprefix("anon-")))
        )
        judge_columns[index] = jid
        record = Judge(
            judge_id=jid,
            event_id=event,
            name_raw=name,
            initials=_text(cell) or None,
            anonymous=anonymous,
            wsdc_id=None,
            **_provenance(evidence, now, run_id),
        )
        prior = judges.get(jid)
        if prior is None or evidence.precedence > prior[1].precedence:
            judges[jid] = record, evidence
    competitor_columns = [
        index
        for index, header in enumerate(headers)
        if any(word in header for word in ("competitor", "leader", "follower", "couple", "dancer"))
    ]
    competitor_columns = sorted(
        set(competitor_columns)
        | {
            index
            for row in table.rows
            for index, cell in enumerate(row.cells)
            if "data-wsdc" in _attrs(cell)
        }
    )
    bib_columns = [index for index, header in enumerate(headers) if "bib" in header]
    if not bib_columns and competitor_columns and competitor_columns[0] > 0:
        bib_columns = [competitor_columns[0] - 1]
    place_column = next(
        (
            index
            for index, (header, cell) in enumerate(zip(headers, table.headers, strict=True))
            if header in {"place", "placement", "result", "final"} or _attrs(cell).get("t") == "6"
        ),
        None,
    )
    if place_column is None and round_type == "final":
        place_column = next(
            (
                index
                for index in range(len(headers))
                if any(_ordinal(_cell(row.cells, index)) is not None for row in table.rows)
            ),
            None,
        )
    for row_number, result in enumerate(table.rows, 1):
        cells = result.cells
        row_entries: dict[str, str] = {}
        row_names: dict[str, str] = {}
        for column in competitor_columns:
            name = _cell(cells, column)
            if not name or name in {"***", "-"}:
                continue
            role = _role(
                headers[column],
                table.heading_raw,
                len(competitor_columns),
                competitor_columns.index(column),
            )
            bib = _bib_for_role(cells, headers, bib_columns, role)
            identifier = entry_id(contest, role, bib, name)
            row_entries[role] = identifier
            row_names[role] = name
            candidate = EntryFacts(identifier, contest, event, role, bib, name, evidence, {round_})
            previous = entries.get(identifier)
            if previous is None:
                entries[identifier] = candidate
            else:
                previous.rounds.add(round_)
                if previous.name_raw != name and evidence.precedence > previous.evidence.precedence:
                    findings.append(
                        _conflict(
                            identifier,
                            "name_raw",
                            previous.name_raw,
                            name,
                            previous.evidence,
                            evidence,
                        )
                    )
                    previous.name_raw, previous.evidence = name, evidence
        if "leader" in row_entries and "follower" in row_entries:
            leader = entries[row_entries["leader"]]
            follower = entries[row_entries["follower"]]
            leader.partner_entry_id, leader.partner_name_raw = follower.entry_id, follower.name_raw
            follower.partner_entry_id, follower.partner_name_raw = leader.entry_id, leader.name_raw
        for entry in row_entries.values():
            raw_marks: list[str] = []
            for column, jid in judge_columns.items():
                raw = _cell(cells, column)
                if raw is None:
                    continue
                raw_marks.append(raw)
                if round_type == "final" and _integer(raw) is not None:
                    place = (
                        _place(_cell(cells, place_column))
                        if place_column is not None
                        else row_number
                    )
                    pid = placement_id(round_, place or row_number)
                    final_marks[(round_, pid, jid)] = FinalMark(
                        round_id=round_,
                        placement_id=pid,
                        judge_id=jid,
                        rank=_integer(raw) or 0,
                        **_provenance(evidence, now, run_id),
                    )
                elif round_type != "final":
                    normalized, value = _mark(raw)
                    if not _known_mark(raw):
                        findings.append(
                            Finding(
                                kind="unknown_enum",
                                subject_kind="mark",
                                subject_id=f"{round_}:{entry}:{jid}",
                                severity="warning",
                                summary=f"Unknown callback mark {raw!r}",
                                evidence={"snapshot_id": evidence.snapshot_id, "mark_raw": raw},
                            )
                        )
                    marks[(round_, entry, jid)] = CallbackMark(
                        round_id=round_,
                        entry_id=entry,
                        judge_id=jid,
                        mark=normalized,
                        mark_raw=raw,
                        mark_value=value,
                        **_provenance(evidence, now, run_id),
                    )
            if round_type != "final" and raw_marks:
                normalized_marks = [_mark(raw)[0] for raw in raw_marks]
                callbacks[(round_, entry)] = Callback(
                    round_id=round_,
                    entry_id=entry,
                    score_sum=sum(_mark(raw)[1] for raw in raw_marks),
                    yes_count=normalized_marks.count("yes"),
                    alt_count=sum(value.startswith("alt") for value in normalized_marks),
                    no_count=normalized_marks.count("no"),
                    outcome=_outcome(cells, headers),
                    tie_break_applied=None,
                    heat_number=None,
                    **_provenance(evidence, now, run_id),
                )
        if round_type == "final" and row_entries:
            place = _place(_cell(cells, place_column)) if place_column is not None else row_number
            pid = placement_id(round_, place or row_number)
            placements[pid] = Placement(
                placement_id=pid,
                round_id=round_,
                contest_id=contest,
                event_id=event,
                place=place or row_number,
                leader_entry_id=row_entries.get("leader"),
                follower_entry_id=row_entries.get("follower"),
                couple_entry_id=row_entries.get("couple"),
                marks_sorted="-".join(
                    sorted(
                        raw
                        for raw in (_cell(cells, column) for column in judge_columns)
                        if raw is not None
                    )
                ),
                tally=_tally(cells, table.headers),
                registry_confirmed=False,
                **_provenance(evidence, now, run_id),
            )


def _record_conflicts(
    contest: str,
    evidence: Evidence,
    table: ResultTable,
    entries: dict[str, EntryFacts],
    findings: list[Finding],
) -> None:
    headers = [_text(cell).casefold() for cell in table.headers]
    competitor_columns = [
        index
        for index, header in enumerate(headers)
        if any(word in header for word in ("competitor", "leader", "follower", "couple", "dancer"))
    ]
    bib_columns = [index for index, header in enumerate(headers) if "bib" in header]
    for row in table.rows:
        for column in competitor_columns:
            name = _cell(row.cells, column)
            if not name:
                continue
            role = _role(headers[column], table.heading_raw, len(competitor_columns))
            bib = _bib_for_role(row.cells, headers, bib_columns, role)
            identifier = entry_id(contest, role, bib, name)
            selected = entries.get(identifier)
            if selected is not None and selected.name_raw != name:
                findings.append(
                    _conflict(
                        identifier, "name_raw", name, selected.name_raw, evidence, selected.evidence
                    )
                )


def _evidence(conn: sqlite3.Connection, event: str) -> list[Evidence]:
    result: list[Evidence] = []
    for row in conn.execute(
        """SELECT o.kind,o.payload_json,o.snapshot_id,o.parser_version,w.source,s.fetched_at,w.kind
        FROM observations o JOIN watches w USING(watch_id) JOIN snapshots s USING(snapshot_id)
        JOIN source_event_map m ON m.source=w.source AND m.source_ref=o.scope_id
        WHERE o.scope_kind='source_event' AND m.event_id=? ORDER BY s.fetched_at,s.snapshot_id,o.seq""",
        (event,),
    ):
        payload = decode_payload(str(row[0]), str(row[1]))
        if isinstance(payload, RoundSheet):
            result.append(
                Evidence(payload, str(row[4]), str(row[2]), str(row[3]), str(row[5]), str(row[6]))
            )
    return result


def _provenance(evidence: Evidence, now: str, run_id: str) -> ProvenanceValues:
    return {
        "source": evidence.source,
        "snapshot_id": evidence.snapshot_id,
        "parser_version": evidence.parser_version,
        "first_seen_at": now,
        "last_seen_at": now,
        "run_id": run_id,
    }


def _text(cell: Cell) -> str:
    return (cell.text or "").strip()


def _cell(cells: tuple[Cell, ...], index: int | None) -> str | None:
    if index is None or index >= len(cells):
        return None
    return _text(cells[index]) or None


def _attrs(cell: Cell) -> dict[str, str]:
    return dict(cell.attributes)


def _is_judge(cell: Cell) -> bool:
    text, attrs = _text(cell).casefold(), _attrs(cell)
    return (
        attrs.get("t") == "9"
        or "judge" in text
        or "title" in attrs
        or (len(text) <= 3 and text.startswith("j") and text[1:].isdigit())
    )


def _judge_token(cell: Cell, index: int) -> tuple[str, str | None, bool]:
    attrs, text = _attrs(cell), _text(cell)
    name = attrs.get("title")
    if attrs.get("t") == "9" and text:
        return slug(text), text, False
    if name and not name.casefold().startswith("judge "):
        return slug(name), name, False
    number = int("".join(char for char in text if char.isdigit()) or index + 1)
    return f"anon-{number}", name or text or f"Judge {number}", True


def _round_type(value: str) -> str:
    value = value.casefold()
    return (
        "final"
        if "final" in value and "semi" not in value and "quarter" not in value
        else "semifinal"
        if "semi" in value
        else "quarterfinal"
        if "quarter" in value
        else "prelim"
    )


def _round_number(value: str) -> int:
    match = re.search(r"\b(?:round\s*)?(\d+)\b", value, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def _contest_slug(name: str) -> str:
    vocabulary = classify_contest(name)
    kind = {
        "jack_and_jill": "jj",
        "strictly": "strictly",
        "classic": "classic",
        "showcase": "showcase",
        "pro_am": "pro-am",
        "rising_star": "rising-star",
    }.get(vocabulary.contest_type, slug(name))
    parts = (
        vocabulary.age_division if vocabulary.age_division != "none" else "",
        vocabulary.division if vocabulary.division != "none" else "",
        kind,
    )
    return "-".join(value for value in parts if value)


def _role(header: str, heading: str, count: int, position: int = 0) -> str:
    value = f"{header} {heading}".casefold()
    if "follower" in value:
        return "follower"
    if "leader" in value:
        return "leader"
    if not header and count == 2:
        return "leader" if position == 0 else "follower"
    return (
        "couple"
        if count == 1 and any(word in value for word in ("strictly", "classic", "showcase"))
        else "leader"
    )


def _bib_for_role(
    cells: tuple[Cell, ...], headers: list[str], columns: list[int], role: str
) -> str | None:
    specific = next((index for index in columns if role in headers[index]), None)
    return _cell(cells, specific if specific is not None else (columns[0] if columns else None))


def _mark(raw: str) -> tuple[str, float]:
    value = raw.strip().casefold()
    mapping = {
        "y": ("yes", 10.0),
        "yes": ("yes", 10.0),
        "10": ("yes", 10.0),
        "1": ("yes", 10.0),
        "a1": ("alt1", 4.5),
        "4.5": ("alt1", 4.5),
        "2.1": ("alt1", 4.5),
        "a2": ("alt2", 4.3),
        "4.3": ("alt2", 4.3),
        "2.2": ("alt2", 4.3),
        "a3": ("alt3", 4.2),
        "4.2": ("alt3", 4.2),
        "2.3": ("alt3", 4.2),
        "n": ("no", 0.0),
        "no": ("no", 0.0),
        "0": ("no", 0.0),
        "3": ("no", 0.0),
    }
    if value in mapping:
        return mapping[value]
    try:
        number = float(value)
    except ValueError:
        return "no", 0.0
    numeric = {
        10.0: ("yes", 10.0),
        1.0: ("yes", 10.0),
        4.5: ("alt1", 4.5),
        2.1: ("alt1", 4.5),
        4.3: ("alt2", 4.3),
        2.2: ("alt2", 4.3),
        4.2: ("alt3", 4.2),
        2.3: ("alt3", 4.2),
        0.0: ("no", 0.0),
        3.0: ("no", 0.0),
    }
    return numeric.get(number, ("no", 0.0))


def _known_mark(raw: str) -> bool:
    value = raw.strip().casefold()
    if value in {
        "y",
        "yes",
        "10",
        "a1",
        "4.5",
        "2.1",
        "a2",
        "4.3",
        "2.2",
        "a3",
        "4.2",
        "2.3",
        "n",
        "no",
        "0",
        "1",
        "3",
    }:
        return True
    try:
        return float(value) in {0, 1, 2.1, 2.2, 2.3, 3, 4.2, 4.3, 4.5, 10}
    except ValueError:
        return False


def _legend(tables: tuple[ResultTable, ...]) -> str:
    values = {_text(cell) for table in tables for row in table.rows for cell in row.cells}
    return (
        "wsdc_10"
        if values & {"10", "4.5", "4.3", "4.2"}
        else "legacy_3"
        if values & {"1", "2.1", "2.2", "2.3", "3"}
        else "unknown"
    )


def _integer(value: str | None) -> int | None:
    try:
        return int(value or "")
    except ValueError:
        return None


def _ordinal(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", value.strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _place(value: str | None) -> int | None:
    return _integer(value) or _ordinal(value)


def _outcome(cells: tuple[Cell, ...], headers: list[str]) -> str:
    for index, header in enumerate(headers):
        value = (_cell(cells, index) or "").casefold()
        if "promot" in header and value not in {"", "0", "no"}:
            return "promoted"
        if "alt" in header and value:
            return "alternate_1"
    return "eliminated"


def _promoted_count(tables: tuple[ResultTable, ...]) -> int:
    promoted: set[tuple[int, int]] = set()
    for table_number, table in enumerate(tables):
        headers = [_text(cell).casefold() for cell in table.headers]
        for row_number, row in enumerate(table.rows):
            if _outcome(row.cells, headers) == "promoted":
                promoted.add((table_number, row_number))
    return len(promoted)


def _tally(cells: tuple[Cell, ...], headers: tuple[Cell, ...]) -> tuple[int, ...]:
    for index, header in enumerate(headers):
        if _attrs(header).get("t") == "12" or "tally" in _text(header).casefold():
            raw = _cell(cells, index) or ""
            return tuple(int(value) for value in raw.split("-") if value.isdigit())
    return ()


def _conflict(
    subject: str,
    field_name: str,
    old: object,
    new: object,
    old_evidence: Evidence,
    new_evidence: Evidence,
) -> Finding:
    return Finding(
        kind="conflict",
        subject_kind="entry",
        subject_id=subject,
        severity="warning",
        summary=f"Conflicting {field_name} for {subject}",
        evidence={
            "field": field_name,
            "old": old,
            "new": new,
            "snapshots": [old_evidence.snapshot_id, new_evidence.snapshot_id],
        },
    )
