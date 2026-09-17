"""Approved complete archived bodies; no source-kind activation or network."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from swingset.model.observations import decode_payload, encode_payload
from swingset.sources.base import ExtractError, ParseContext, ParseError
from swingset.sources.steprightsolutions import EventPage, IndexPage, RoundPage

FIXTURES = Path(__file__).parent / "fixtures/sources/steprightsolutions"
PROVENANCE = {
    row["id"]: row for row in json.loads((FIXTURES / "real-provenance-20260917.json").read_bytes())
}


def body(identifier):
    record = PROVENANCE[identifier]
    value = (FIXTURES / record["file"]).read_bytes()
    assert hashlib.sha256(value).hexdigest() == record["body_sha256"]
    return value


def context(identifier, page):
    record = PROVENANCE[identifier]
    return ParseContext(
        "retained-real-control",
        "offline-only",
        record["original_url"],
        "steprightsolutions",
        page.kind,
        record.get(
            "source_ref",
            None if identifier == "srs-index" else "steprightsolutions:asianopen2013",
        ),
        record["captured_at"],
    )


def parsed(identifier, page):
    return page.parse(page.extract(body(identifier)), context(identifier, page))


def test_real_index_and_metadata_only_event_preserve_printed_scope():
    index = parsed("srs-index", IndexPage())
    assert len(index.observations) == 22
    assert any(item.payload.year_raw == "2009" for item in index.observations)
    assert not index.watches  # Retained parsing does not authorize years or requests.
    event = parsed("srs-event", EventPage())
    sheet = event.observations[0].payload
    assert sheet.name_raw == "Swingvitation Asian WCS Open"
    assert sheet.date_raw == "April 25 - 28, 2013"
    assert sheet.source_event_ref == "steprightsolutions:asianopen2013"
    assert sheet.round_links == () and sheet.round_listing_status == "no_round_links"
    assert [warning.code for warning in event.warnings] == [
        "steprightsolutions_round_listing_absent"
    ]
    assert not event.watches
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet


def test_real_2015_event_uses_visible_main_panel_without_sidebar_duplicates():
    result = parsed("srs-event2015", EventPage())
    sheet = result.observations[0].payload
    assert sheet.name_raw == "Asia West Coast Swing Open"
    assert sheet.date_raw == "April 23 - 26, 2015"
    assert sheet.source_event_ref == "steprightsolutions:asianopen2015"
    assert len(sheet.round_links) == 12
    assert [
        (link.contest_name_raw, link.round_name_raw, link.source_round_ref)
        for link in sheet.round_links
    ] == [
        ("Newcomer West Coast Swing Jack & Jill", "Finals", "1126"),
        ("Novice West Coast Swing Jack & Jill", "Prelims", "1127"),
        ("Novice West Coast Swing Jack & Jill", "Semi-Finals", "1128"),
        ("Novice West Coast Swing Jack & Jill", "Finals", "1129"),
        ("Intermediate West Coast Swing Jack & Jill", "Prelims", "1130"),
        ("Intermediate West Coast Swing Jack & Jill", "Finals", "1131"),
        ("Advanced West Coast Swing Jack & Jill", "Prelims", "1132"),
        ("Advanced West Coast Swing Jack & Jill", "Finals", "1133"),
        ("Novice West Coast Swing Strictly", "Prelims", "1134"),
        ("Novice West Coast Swing Strictly", "Finals", "1135"),
        ("Open West Coast Swing Strictly", "Prelims", "1136"),
        ("Open West Coast Swing Strictly", "Finals", "1137"),
    ]
    assert len({link.source_round_ref for link in sheet.round_links}) == 12
    assert sheet.round_listing_status == "listed_links"
    assert not result.warnings and not result.watches
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet


def test_real_2015_event_does_not_substitute_sidebar_for_empty_main_panel():
    page = EventPage()
    original = body("srs-event2015")
    before_panel, panel = original.split(b'<div class="span9">', 1)
    without_main_links = (
        before_panel
        + b'<div class="span9">'
        + panel.replace(
            b'<a href="/events/asianopen2015/round/',
            b'<a data-href="/events/asianopen2015/round/',
        )
    )
    assert without_main_links != original
    with pytest.raises(ExtractError, match="main results panel"):
        page.extract(without_main_links)


def test_real_grouped_preliminary_header_and_anonymous_roster_are_distinct():
    result = parsed("srs-round507", RoundPage())
    sheet = result.observations[0].payload
    assert sheet.contest_name_raw == "Newcomer West Coast Swing Jack & Jill"
    assert sheet.round_name_raw == "Prelims" and sheet.callback_legend == "legacy_3"
    assert [len(t.table.rows) for t in sheet.tables] == [10, 15]
    assert [t.bib_ownership for t in sheet.tables] == ["leader", "follower"]
    marks = Counter()
    for table in sheet.tables:
        assert table.anonymous_judge_columns == (2, 3, 4, 5, 6)
        assert table.anonymous_judge_ids == ("anon-1", "anon-2", "anon-3", "anon-4", "anon-5")
        assert not table.marks_attributed and table.chief_judge_raw is None
        assert table.promotion == "unknown"
        assert "Judging results" not in table.named_panel_raw[0]
        assert table.named_panel_raw == (
            "Chuck Brown , Jessica Cox , Jordan Frisbee , Tatiana Mollmann , Ben Morris",
        )
        assert table.judge_notes_raw == (
            "* Judging results are anonymous. Chief judge scores are kept private and only used in the event of a tie.",
        )
        attrs = dict(table.table.headers[2].attributes)
        assert attrs["source_group_colspan"] == "5"
        assert attrs["source_group_label"] == "Judge Scores *"
        assert attrs["title"] == "1 = Yes, 2 = Alt, 3 = No"
        marks.update(
            row.cells[index].text
            for row in table.table.rows
            for index in table.anonymous_judge_columns
        )
    assert marks == {"1": 71, "2": 13, "3": 41}
    assert sheet.tables[0].table.rows[0].cells[0].text == "032"
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet
    assert not result.watches


def test_real_final_keeps_printed_pair_and_does_not_invent_cross_page_ownership():
    result = parsed("srs-round508", RoundPage())
    sheet = result.observations[0].payload
    table = sheet.tables[0]
    assert sheet.round_name_raw == "Finals" and sheet.callback_legend == "unknown"
    assert len(table.table.rows) == 8
    assert table.anonymous_judge_columns == (3, 4, 5, 6, 7)
    assert table.bib_ownership == "unknown" and not table.marks_attributed
    assert [cell.text for cell in table.table.rows[0].cells] == [
        "018",
        "Kenneth Tan",
        "Hisaki Mino",
        "2",
        "4",
        "1",
        "1",
        "1",
        "1st",
    ]
    assert any(w.code == "steprightsolutions_bib_ownership_unknown" for w in result.warnings)
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet


def test_cross_body_controls_support_only_the_observed_promotion_and_bib_claims():
    leaders, followers = parsed("srs-round507", RoundPage()).observations[0].payload.tables
    final = parsed("srs-round508", RoundPage()).observations[0].payload.tables[0]

    def advanced(table):
        return {
            row.cells[1].text
            for row in table.table.rows
            if "adv" in dict(row.cells[0].attributes)["row-class"].split()
        }

    assert len(advanced(leaders)) == len(advanced(followers)) == 8
    assert advanced(leaders) == {row.cells[1].text for row in final.table.rows}
    assert advanced(followers) == {row.cells[2].text for row in final.table.rows}
    leader_bibs = {row.cells[1].text: row.cells[0].text for row in leaders.table.rows}
    assert all(leader_bibs[row.cells[1].text] == row.cells[0].text for row in final.table.rows)
    # The single-body parser has no verified predecessor input; these comparisons
    # are review evidence, not an implied cross-page join inside parse().
    assert final.bib_ownership == leaders.promotion == followers.promotion == "unknown"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b'colspan="5"', b'colspan="4"'),
        (b'colspan="5"', b'colspan="999999"'),
        (b'rowspan="1"', b'rowspan="2"'),
        (b"Judge Scores *", b"Unreviewed Scores *"),
        (b"1 = Yes, 2 = Alt, 3 = No", b"1 = No, 2 = Alt, 3 = Yes"),
    ],
)
def test_real_header_mutations_fail_closed(old, new):
    original = body("srs-round507")
    assert old in original
    page = RoundPage()
    with pytest.raises(ParseError):
        page.parse(page.extract(original.replace(old, new)), context("srs-round507", page))


def test_changed_mark_promotion_attribute_and_anonymity_note_change_extract():
    page = RoundPage()
    original = body("srs-round507")
    baseline = page.extract(original)
    changed_mark = original.replace(b">2<", b">2.1<", 1)
    assert changed_mark != original and page.extract(changed_mark) != baseline
    result = page.parse(page.extract(changed_mark), context("srs-round507", page))
    finding = next(w for w in result.warnings if w.code == "steprightsolutions_callback_unknown")
    assert finding.evidence["values"] == ["2.1"]
    for old, new in [
        (b"odd adv", b"odd"),
        (b"Judging results are anonymous.", b"Anonymity rule changed."),
    ]:
        assert old in original
        assert page.extract(original.replace(old, new, 1)) != baseline


@pytest.mark.parametrize("page", [IndexPage(), EventPage(), RoundPage()])
@pytest.mark.parametrize(
    "empty", [b"", b"<html></html>", b"<h2>Unknown event</h2><small>April 25 - 28, 2013</small>"]
)
def test_empty_or_unrecognized_page_is_not_metadata_only_event(page, empty):
    with pytest.raises(ExtractError):
        page.extract(empty)


def test_metadata_header_mutations_and_wrong_ownership_are_rejected():
    page = EventPage()
    original = body("srs-event")
    with pytest.raises(ExtractError):
        page.extract(original.replace(b"April 25 - 28, 2013", b"date unavailable"))
    ctx = context("srs-event", page)
    wrong = ParseContext(
        ctx.snapshot_id,
        ctx.watch_id,
        ctx.url,
        ctx.source,
        ctx.kind,
        "steprightsolutions:another-event",
        ctx.fetched_at,
    )
    with pytest.raises(ParseError, match="ownership"):
        page.parse(page.extract(original), wrong)
