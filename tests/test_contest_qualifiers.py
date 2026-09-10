from pathlib import Path

import pytest
from test_project_event import EVENT, add, seed

from swingset.model.canonical import Contest, Placement
from swingset.project.contests import project_event
from swingset.sources.base import ParseContext
from swingset.sources.eepro.adapter import RoundPage
from swingset.state.db import open_database


def _parse(heading: str, path: str = "finals.html"):
    body = f"""<table><tr><th colspan="5">Division: {heading}</th></tr>
    <tr><th>Place</th><th>Competitor</th><th>J1</th><th>BIB</th><th>Marks sorted</th></tr>
    <tr><td>1</td><td>A Leader and B Follower</td><td>1</td><td>42</td><td>1</td></tr></table>""".encode()
    page = RoundPage()
    context = ParseContext(
        "s",
        "w",
        f"https://eepro.com/results/capital2026/{path}",
        "eepro",
        page.kind,
        "eepro:hummer",
        "2026-09-10",
    )
    return page.parse(page.extract(body), context).observations[0].payload


@pytest.mark.parametrize(
    "suffix", ["(WSDC)", "(CSDC)", "(Not WSDC)", "Retro", "Stage One (Vintage)"]
)
def test_round_suffix_preserves_contest_qualifier(suffix: str) -> None:
    sheet = _parse(f"Jack &amp; Jill Advanced Finals - {suffix}")
    assert sheet.contest_name_raw == f"Jack & Jill Advanced {suffix}"
    assert sheet.round_name_raw == "Finals"


def test_country_qualifier_survives_prelim_instructions() -> None:
    sheet = _parse(
        "Jack &amp; Jill Advanced Follower Prelims (CSDC) - 11 competed When marks are tied, lowest sum used as tiebreaker, Y=10"
    )
    assert sheet.contest_name_raw == "Jack & Jill Advanced Follower (CSDC)"


def test_wsdc_and_csdc_finals_both_survive_projection(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        for index, code in enumerate(("WSDC", "CSDC")):
            sheet = _parse(f"Jack &amp; Jill Advanced Finals ({code})", f"{code}.html")
            add(db.connection, f"w{index}", f"s{index}", sheet, f"2026-09-0{index + 1}")
        projection = project_event(db.connection, EVENT, "2026-09-10", "run_a")
    contests = {row.dance_style: row for row in projection.rows if isinstance(row, Contest)}
    assert set(contests) == {"wcs", "country"}
    assert contests["wcs"].wsdc_points_eligible
    assert not contests["country"].wsdc_points_eligible
    assert {row.contest_id for row in projection.rows if isinstance(row, Placement)} == {
        row.contest_id for row in contests.values()
    }
