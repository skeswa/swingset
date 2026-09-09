from pathlib import Path

from swingset.sources.base import ParseContext
from swingset.sources.eepro import AutoIndexPage, IndexPage, RoundPage

FIXTURES = Path("src/swingset/sources/eepro/fixtures")


def _parse_round(name: str):
    page = RoundPage()
    body = (FIXTURES / f"round-{name}-summerhummer2026-2026-09-09.body").read_bytes()
    result = page.parse(
        page.extract(body),
        ParseContext(
            "snapshot",
            "watch",
            f"https://eepro.com/results/summerhummer2026/{name}.html",
            "eepro",
            page.kind,
            "eepro:summerhummer2026",
            "2026-09-09T00:00:00Z",
        ),
    )
    return [observation.payload for observation in result.observations]


def test_real_autoindex_reads_modified_and_size_columns() -> None:
    page = AutoIndexPage()
    rows = page.extract((FIXTURES / "autoindex-summerhummer2026-2026-09-09.body").read_bytes())
    assert rows == [
        {"name": "aa.html", "href": "aa.html", "modified": "2026-08-23 19:26", "size": "8.5K"},
        {
            "name": "jjfinals.html",
            "href": "jjfinals.html",
            "modified": "2026-08-23 19:26",
            "size": "23K",
        },
        {
            "name": "jjprelims.html",
            "href": "jjprelims.html",
            "modified": "2026-08-23 19:26",
            "size": "142K",
        },
        {
            "name": "routines.html",
            "href": "routines.html",
            "modified": "2026-08-23 19:26",
            "size": "9.8K",
        },
        {
            "name": "strictly.html",
            "href": "strictly.html",
            "modified": "2026-08-23 19:26",
            "size": "33K",
        },
    ]


def test_index_reads_labeled_title_and_date_fields() -> None:
    page = IndexPage()
    body = b"""<a class="event-card" href="?event=summerhummer2026">
      <div class="event-title">Summer Hummer</div>
      <div class="event-date">August 20-23, 2026</div>
    </a>"""
    assert page.extract(body) == [
        {
            "slug": "summerhummer2026",
            "name": "Summer Hummer",
            "date": "August 20-23, 2026",
            "url": "?event=summerhummer2026",
        }
    ]


def test_real_jack_and_jill_prelims_have_actual_headers_and_rank_counts() -> None:
    sheets = _parse_round("jjprelims")
    assert len(sheets) == 21
    advanced = next(
        sheet for sheet in sheets if sheet.contest_name_raw == "Jack & Jill Follower Advanced"
    )
    table = advanced.tables[0]
    assert advanced.round_name_raw == "Prelims"
    assert [cell.text for cell in table.headers[:3]] == [
        "Count",
        "Competitor",
        "Arjay Centeno",
    ]
    assert len(table.rows) == 28
    assert [table.rows[index].cells[0].text for index in range(4)] == ["1", "2", "2", "2"]
    assert table.rows[-1].cells[0].text == ""


def test_real_jack_and_jill_finals_decode_entities_and_keep_pair_bibs() -> None:
    sheets = _parse_round("jjfinals")
    assert len(sheets) == 9
    novice = next(sheet for sheet in sheets if sheet.contest_name_raw == "Jack & Jill Novice")
    assert novice.round_name_raw == "Finals"
    assert novice.tables[0].headers[0].text == "Place"
    assert "/" in novice.tables[0].rows[0].cells[-2].text


def test_real_couples_final_keeps_single_couple_bib_column() -> None:
    sheets = _parse_round("strictly")
    assert len(sheets) == 10
    champions = next(
        sheet for sheet in sheets if sheet.contest_name_raw == "Strictly Swing Champions"
    )
    assert champions.round_name_raw == "Finals"
    assert len(champions.tables[0].rows) == 12
    assert champions.tables[0].headers[-2].text == "BIB"


def test_real_routines_without_round_suffix_use_final_layout_headers() -> None:
    sheets = _parse_round("routines")
    assert len(sheets) == 5
    assert {sheet.round_name_raw for sheet in sheets} == {"Finals"}
    assert all(sheet.tables[0].headers[0].text == "Place" for sheet in sheets)
