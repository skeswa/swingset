from pathlib import Path

import pytest
from test_project_event import EVENT, add, seed

from swingset.model.canonical import Callback, CallbackMark, Entry, Placement
from swingset.project.contests import project_event
from swingset.sources.base import ParseContext
from swingset.sources.eepro.adapter import RoundPage
from swingset.sources.records import Cell, ResultRow, ResultTable, RoundSheet
from swingset.state.db import open_database


def test_semifinalists_heading_does_not_contaminate_contest_identity() -> None:
    body = b"""<table><tr><th colspan="4">Division: Jack &amp; Jill Leader Intermediate Semifinalists - 29 competed When marks are tied, lowest sum used as tiebreaker</th></tr>
    <tr><th>Count</th><th>Competitor</th><th>BIB</th><th>Promote</th></tr>
    <tr><td>1</td><td>A Dancer</td><td>42</td><td>X</td></tr></table>"""
    page = RoundPage()
    ctx = ParseContext(
        "s",
        "w",
        "https://eepro.com/results/a/prelims.html",
        "eepro",
        page.kind,
        "eepro:a",
        "2026-09-10",
    )
    result = page.parse(page.extract(body), ctx)
    sheet = result.observations[0].payload
    assert sheet.contest_name_raw == "Jack & Jill Leader Intermediate"
    assert sheet.round_name_raw == "Semifinalists"
    assert "29 competed" in sheet.tables[0].heading_raw


def _sheet(
    kind: str, name: str, bib: str | None, contest: str = "Novice Jack & Jill Leader"
) -> RoundSheet:
    headers = (Cell("Bib"), Cell("Leader"), Cell("Place" if kind == "Final" else "J1"))
    state = () if kind == "Final" else (("row-data-state", "CB"),)
    cells = (
        Cell(bib, state),
        Cell(name, state),
        Cell("1" if kind == "Final" else "Y", state),
    )
    return RoundSheet(
        "round_sheet",
        "eepro:hummer",
        kind + (bib or ""),
        contest,
        kind,
        (ResultTable(kind, headers, (ResultRow(cells),)),),
    )


def test_unique_named_final_rejoins_bib_prelim_and_rewrites_references(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "pre", "s1", _sheet("Prelim", "A Dancer", "42"), "2026-09-01")
        add(db.connection, "fin", "s2", _sheet("Final", "A Dancer", None), "2026-09-02")
        rows = project_event(db.connection, EVENT, "2026-09-10", "run_a").rows
    entries = [r for r in rows if isinstance(r, Entry)]
    assert len(entries) == 1
    assert entries[0].bib == "42"
    assert entries[0].rounds_danced == ("prelim", "final")
    assert {r.entry_id for r in rows if isinstance(r, Callback)} == {entries[0].entry_id}
    assert {r.entry_id for r in rows if isinstance(r, CallbackMark)} == {entries[0].entry_id}
    assert {r.leader_entry_id for r in rows if isinstance(r, Placement)} == {entries[0].entry_id}


def test_homonyms_with_multiple_bibs_remain_unmerged(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        for i, (kind, bib) in enumerate([("Prelim", "42"), ("Prelim", "43"), ("Final", None)]):
            add(db.connection, f"w{i}", f"s{i}", _sheet(kind, "A Dancer", bib), f"2026-09-0{i + 1}")
        projection = project_event(db.connection, EVENT, "2026-09-10", "run_a")
    assert len([r for r in projection.rows if isinstance(r, Entry)]) == 3
    assert any(f.kind == "ambiguous_entry" for f in projection.findings)


@pytest.mark.parametrize(
    "role,first_pair,second_pair,expected_name",
    [
        ("Leader", "A Leader and B Follower", "A Leader and C Follower", "A Leader"),
        ("Follower", "A Leader and B Follower", "C Leader and B Follower", "B Follower"),
    ],
)
def test_rotating_partner_prelim_scores_only_named_role(
    tmp_path: Path, role: str, first_pair: str, second_pair: str, expected_name: str
) -> None:
    table = ResultTable(
        "Prelims",
        (Cell("Count"), Cell("Competitor"), Cell("J1"), Cell("BIB")),
        (
            ResultRow((Cell("1"), Cell(first_pair), Cell("Y"), Cell("42"))),
            ResultRow((Cell("1"), Cell(second_pair), Cell("Y"), Cell("42"))),
        ),
    )
    sheet = RoundSheet(
        "round_sheet",
        "eepro:hummer",
        "pre",
        f"Jack & Jill Hi-Low (Adv AS Ch {role})",
        "Prelims",
        (table,),
    )
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "pre", "s1", sheet, "2026-09-01")
        projection = project_event(db.connection, EVENT, "2026-09-10", "run_a")
    entries = [r for r in projection.rows if isinstance(r, Entry)]
    assert [(r.role, r.name_raw, r.bib) for r in entries] == [(role.lower(), expected_name, "42")]
    assert len([r for r in projection.rows if isinstance(r, Callback)]) == 1
