from pathlib import Path

import pytest

from swingset.model.observations import decode_payload, encode_payload
from swingset.sources import get_page_kind
from swingset.sources.base import ExtractError, ParseContext, ParseError
from swingset.sources.records import RoundSheet
from swingset.sources.steprightsolutions import SOURCE, EventPage, IndexPage, RoundPage
from swingset.sources.steprightsolutions.records import callback_mark

FIXTURES = Path(__file__).parent / "fixtures" / "sources" / "steprightsolutions"


def context(kind: str, path: str = "/events/example2010/round/507") -> ParseContext:
    return ParseContext(
        "snap",
        "watch",
        "http://steprightsolutions.com" + path,
        "steprightsolutions",
        kind,
        "steprightsolutions:example2010",
        "2026-09-13T00:00:00Z",
    )


def test_index_keeps_series_location_year_and_deduplicates_aliases() -> None:
    page = IndexPage()
    result = page.parse(
        page.extract((FIXTURES / "synthetic-index.html").read_bytes()), context(page.kind)
    )
    assert [
        (row.payload.source_event_ref, row.payload.year_raw) for row in result.observations
    ] == [("steprightsolutions:example2010", "2010"), ("steprightsolutions:example2011", "2011")]
    assert result.observations[1].payload.url == "http://steprightsolutions.com/events/example2011"
    assert result.observations[0].payload.location_raw == "Example City, Canada"
    assert not result.watches


def test_event_keeps_contest_round_links_and_raw_date_without_creating_watches() -> None:
    page = EventPage()
    result = page.parse(
        page.extract((FIXTURES / "synthetic-event.html").read_bytes()), context(page.kind)
    )
    sheet = result.observations[0].payload
    assert sheet.date_raw == "December 2 - 5, 2010"
    assert sheet.round_links[0].contest_name_raw == "Novice West Coast Swing Jack & Jill"
    assert [link.round_name_raw for link in sheet.round_links] == [
        "Prelims",
        "Semi-FInals",
        "FInals",
        "Finals",
    ]
    assert sheet.round_links[-1].contest_name_raw == "Master's Strictly Swing"
    assert not result.watches
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet


def test_callbacks_preserve_raw_values_unknown_promotion_and_unattributed_roster() -> None:
    page = RoundPage()
    result = page.parse(
        page.extract((FIXTURES / "synthetic-prelims.html").read_bytes()), context(page.kind)
    )
    sheet = result.observations[0].payload
    assert not isinstance(sheet, RoundSheet)
    assert sheet.callback_legend == "legacy_3"
    assert len(sheet.tables) == 2
    leaders, followers = sheet.tables
    assert leaders.table.rows[0].cells[0].text == "032"
    assert leaders.table.rows[1].cells[3].text == "2.1"
    assert leaders.anonymous_judge_columns == (2, 3)
    assert leaders.anonymous_judge_ids == ("anon-1", "anon-2")
    assert leaders.named_panel_raw == ("Taylor Example, Morgan Sample",)
    assert followers.named_panel_raw == leaders.named_panel_raw
    assert leaders.chief_judge_raw == "Casey Example"
    assert not leaders.marks_attributed
    assert leaders.promotion == "unknown"  # Even a highlight class cannot establish promotion.
    assert (leaders.bib_ownership, followers.bib_ownership) == ("leader", "follower")
    assert {warning.code for warning in result.warnings} == {
        "steprightsolutions_canonical_admission_pending",
        "steprightsolutions_promotion_unknown",
        "steprightsolutions_callback_unknown",
    }
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet
    assert not result.watches


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", "yes"), ("2", "alt"), ("3", "no"), ("2.1", None), ("", None), ("Y", None)],
)
def test_legacy_callback_never_invents_an_alternate_rank(raw: str, expected: str | None) -> None:
    assert callback_mark(raw) == expected


def test_finals_keep_one_printed_bib_and_separate_partner_names() -> None:
    page = RoundPage()
    result = page.parse(
        page.extract((FIXTURES / "synthetic-finals.html").read_bytes()), context(page.kind)
    )
    sheet = result.observations[0].payload
    assert sheet.round_name_raw == "FInals"
    assert sheet.contest_name_raw == "All-Stars / Champions West Coast Swing Jack and Jill"
    table = sheet.tables[0]
    assert table.bib_ownership == "unknown"
    assert [cell.text for cell in table.table.rows[0].cells] == [
        "032",
        "Alex Example",
        "Robin Example",
        "1",
        "2",
        "1st",
    ]
    assert table.anonymous_judge_columns == (3, 4)
    assert table.chief_judge_raw is None
    assert any(
        warning.code == "steprightsolutions_bib_ownership_unknown" for warning in result.warnings
    )
    assert sheet.callback_legend == "unknown"


@pytest.mark.parametrize("page", [IndexPage(), EventPage(), RoundPage()])
def test_unrecognized_response_is_not_a_legitimate_empty(page: IndexPage) -> None:
    with pytest.raises(ExtractError):
        page.extract(b"<html><h1>Archive rate limit</h1></html>")
    assert get_page_kind(page.kind).PARSER_VERSION == page.PARSER_VERSION
    assert SOURCE.seed_watches({}, {}) == []


def test_event_rejects_foreign_event_round_links() -> None:
    page = EventPage()
    body = (
        (FIXTURES / "synthetic-event.html")
        .read_bytes()
        .replace(b"/events/example2010/round/510", b"/events/foreign/round/510")
    )
    with pytest.raises(ParseError, match="ownership"):
        page.parse(page.extract(body), context(page.kind))


def test_merged_or_uneven_table_does_not_guess_column_ownership() -> None:
    page = RoundPage()
    body = (
        (FIXTURES / "synthetic-finals.html")
        .read_bytes()
        .replace(b"<td>032", b'<td colspan="2">032')
    )
    with pytest.raises(ParseError, match="merged or uneven"):
        page.parse(page.extract(body), context(page.kind))
