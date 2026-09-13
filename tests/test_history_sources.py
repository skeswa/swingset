import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from swingset.sources.base import ParseContext
from swingset.sources.swingdancecouncil.adapter import EventsPage as CouncilPage
from swingset.sources.wsdc_calendar.adapter import EventsPage as CalendarPage
from swingset.sources.wsdc_calendar.adapter import _dates
from swingset.sources.wsdc_newsletter.adapter import EventsPage as NewsletterPage


def context(page, url="https://example.test/"):
    return ParseContext(
        "snapshot",
        "watch",
        url,
        page.kind.split(".")[0],
        page.kind,
        None,
        "2016-01-01T00:00:00+00:00",
    )


def test_old_council_formats_and_trial_default():
    body = b"""<html><h1>Member Registry Events Page</h1><table>
    <tr><td>Aug. 23 - 26*, 2012</td><td><a href="https://example.test">Swing Weekend</a></td><td>Framingham, MA</td></tr>
    <tr><td>Nov. 29-Dec. 2, 2012</td><td>Other Weekend</td><td>Warsaw, Poland</td></tr>
    <tr><td>July TBD, 2013</td><td>Undated Weekend</td><td>London, UK</td></tr>
    </table></html>"""
    page = CouncilPage()
    result = page.parse(
        page.extract(body),
        context(page, "https://swingdancecouncil.com/ActiveServerPages/NonRegUpcomingEvents.asp"),
    )
    rows = [o.payload for o in result.observations]
    assert [(r.start_date_raw, r.end_date_raw) for r in rows] == [
        ("2012-08-23", "2012-08-26"),
        ("2012-11-29", "2012-12-02"),
        ("2013-07", "2013-07"),
    ]
    assert all(r.event_type_raw == "trial" for r in rows)


def test_calendar_2016_and_known_empty_widget():
    assert _dates("Dec 29, 2022 - Jan 1, 2023") == ("2022-12-29", "2023-01-01")
    assert _dates("10th November, 2016 To 13th November, 2016") == ("2016-11-10", "2016-11-13")
    page = CalendarPage()
    result = page.parse(
        page.extract(b'<html><div class="event-map">Events Calendar</div></html>'), context(page)
    )
    assert result.legitimate_empty and not result.observations


def newsletter_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 50 750 Td (Upcoming Registry Events) Tj 0 -20 Td (Example Swing) Tj 0 -20 Td (July 27 - 29, 2018) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


def test_newsletter_pdf_reads_explicit_dates_and_flags_approval_boxes():
    page = NewsletterPage()
    result = page.parse(page.extract(newsletter_pdf()), context(page))
    assert len(result.observations) == 1
    assert result.observations[0].payload.name_raw == "Example Swing"
    assert result.observations[0].payload.end_date_raw == "2018-07-29"
    approval = page.parse(["New Registry Events\nExample Weekend - Q3 2018"], context(page))
    assert not approval.observations and approval.warnings[0].code == "approval_notice_review"


def test_recorded_council_capture_includes_all_97_rows():
    from pathlib import Path

    page = CouncilPage()
    result = page.parse(
        page.extract(
            Path(
                "src/swingset/sources/swingdancecouncil/fixtures/council-20120825.html"
            ).read_bytes()
        ),
        context(page),
    )
    assert len(result.observations) == 97
    rows = [o.payload for o in result.observations]
    assert sum(len(row.end_date_raw) == 7 for row in rows) == 10
    assert all(row.start_date_raw <= row.end_date_raw for row in rows)
    assert any(
        row.start_date_raw == "2012-12-27" and row.end_date_raw == "2013-01-01" for row in rows
    )


def test_recorded_newsletter_columns_and_wrapped_names():
    from pathlib import Path

    page = NewsletterPage()
    result = page.parse(
        page.extract(
            Path("src/swingset/sources/wsdc_newsletter/fixtures/newsletter-vol6.pdf").read_bytes()
        ),
        context(page),
    )
    assert len(result.observations) == 41
    assert [warning.code for warning in result.warnings] == ["newsletter_colour_unverified"]
    assert result.warnings[0].evidence["colour_recovered"] is False
    rows = {o.payload.name_raw: o.payload for o in result.observations}
    assert rows["AISA WEST COAST SWING OPEN"].row_classes == (
        "newsletter_status_colour_unverified",
    )
    assert rows["Ukrainian Open"].row_classes == ()
    assert rows["AISA WEST COAST SWING OPEN"].end_date_raw == "2018-04-22"
    assert rows["Ukrainian Open"].end_date_raw == "2018-09-16"
    assert rows["NEVERLAND SWING"].end_date_raw == "2018-07-01"
    assert rows["FRENCH OPEN WEST COAST SWING **"].end_date_raw == "2018-05-21"
    assert not any("Judge" in name or "Director" in name for name in rows)


def test_newsletter_unknown_layout_is_not_authoritative_empty():
    page = NewsletterPage()
    result = page.parse(["Board meeting notes\n• Board retreat\n  June 1 - 3, 2018"], context(page))
    assert not result.observations
    assert not result.legitimate_empty
    assert result.warnings[0].code == "newsletter_sidebar_unparsed"


def test_newsletter_does_not_take_dated_article_bullets_from_another_column():
    page = NewsletterPage()
    result = page.parse(
        [
            "Registry Events\n"
            "• Actual Swing Event" + " " * 50 + "• Board retreat\n"
            "  June 1 - 3, 2018" + " " * 53 + "  July 1 - 3, 2018"
        ],
        context(page),
    )
    assert [o.payload.name_raw for o in result.observations] == ["Actual Swing Event"]


def test_newsletter_partial_approval_quarter_stays_visible_beside_dated_rows():
    page = NewsletterPage()
    result = page.parse(
        [
            "Upcoming Registry Events\nExisting Swing\nJuly 1 - 3, 2018\n"
            "New Events\nOther Swing in London, UK\nQ3 2018"
        ],
        context(page),
    )
    assert len(result.observations) == 1
    assert any(
        w.code == "approval_notice_review" and w.evidence["notice"] == "Q3 2018"
        for w in result.warnings
    )


def test_newsletter_invalid_printed_date_fails_with_stable_reason():
    import pytest

    from swingset.sources.base import ParseError

    page = NewsletterPage()
    with pytest.raises(ParseError, match="newsletter_invalid_event_date"):
        page.parse(["Registry Events\n• Broken Swing\n  June 31 - 32, 2018"], context(page))


def test_recorded_2016_calendar_uses_all_cards_instead_of_first_20_table_rows():
    from pathlib import Path

    page = CalendarPage()
    body = Path("src/swingset/sources/wsdc_calendar/fixtures/calendar-20161113.html").read_bytes()
    result = page.parse(page.extract(body), context(page))
    assert len(result.observations) == 127
    assert not result.warnings
    first = result.observations[0].payload
    assert (first.name_raw, first.start_date_raw, first.end_date_raw) == (
        "Westie Angels",
        "2016-11-10",
        "2016-11-13",
    )
    assert any(row.payload.end_date_raw.startswith("2018-") for row in result.observations)


def test_council_invalid_source_dates_warn_without_discarding_valid_rows():
    page = CouncilPage()
    body = b"""<h1>Member Registry Events</h1><table>
    <tr><td>Jun 30 - Jul 4th, 2016</td><td>Valid Weekend</td><td>London</td></tr>
    <tr><td>Feb 12 - 14, 215</td><td>Bad Year</td><td>Paris</td></tr>
    </table>"""
    result = page.parse(page.extract(body), context(page))
    assert len(result.observations) == 1
    assert result.observations[0].payload.end_date_raw == "2016-07-04"
    assert result.warnings[0].code == "unrecognized_listing_date"
    assert result.warnings[0].evidence == {
        "date": "Feb 12 - 14, 215",
        "name": "Bad Year",
        "location": "Paris",
        "website": None,
    }


def test_newsletter_trial_colour_legend_remains_explicitly_unverified():
    page = NewsletterPage()
    result = page.parse(
        [
            "Upcoming Registry Events\nWSDC Trial Events are shown in PURPLE\n• Example Swing\n  June 1 - 3, 2024"
        ],
        context(page),
    )
    assert len(result.observations) == 1
    assert result.observations[0].payload.row_classes == ("newsletter_status_colour_unverified",)
    assert result.observations[0].payload.event_type_raw == "registry"
    assert result.warnings[0].code == "newsletter_colour_unverified"
    assert result.warnings[0].evidence["colour_recovered"] is False


def test_recorded_fullcalendar_inline_data_has_500_explicit_events():
    from pathlib import Path

    page = CalendarPage()
    result = page.parse(
        page.extract(
            Path("src/swingset/sources/wsdc_calendar/fixtures/calendar-20210415.html").read_bytes()
        ),
        context(page),
    )
    assert len(result.observations) == 500
    assert not result.warnings
    first = result.observations[0].payload
    assert (first.name_raw, first.start_date_raw, first.end_date_raw) == (
        "Bavarian Open",
        "2023-09-07",
        "2023-09-10",
    )
    assert sum(row.payload.event_type_raw == "Trial Event" for row in result.observations) == 13


def test_retained_literal_calendar_and_map_preserve_rows_without_running_scripts():
    from pathlib import Path

    page = CalendarPage()
    for filename, expected in [
        ("calendar-2016-literals.html", 67),
        ("calendar-2019-map.html", 105),
    ]:
        result = page.parse(
            page.extract(
                (Path("src/swingset/sources/wsdc_calendar/fixtures") / filename).read_bytes()
            ),
            context(page),
        )
        assert len(result.observations) == expected
        assert not result.warnings


def test_retained_newsletter_wrapping_footer_columns_and_explicit_years():
    from pathlib import Path

    page = NewsletterPage()
    results = {}
    for volume, count in ((1, 39), (2, 38), (5, 39), (25, 28)):
        body = Path(
            f"src/swingset/sources/wsdc_newsletter/fixtures/newsletter-vol{volume}.pdf"
        ).read_bytes()
        result = page.parse(page.extract(body), context(page))
        assert len(result.observations) == count, volume
        assert not result.legitimate_empty
        assert any(w.code == "newsletter_undated_listing" for w in result.warnings)
        results[volume] = {row.payload.name_raw: row.payload for row in result.observations}
    assert (
        results[1]["NEW YEAR’S SWING FLING"].start_date_raw,
        results[1]["NEW YEAR’S SWING FLING"].end_date_raw,
    ) == ("2016-12-29", "2017-01-02")
    assert results[1]["CITY OF ANGELS"].end_date_raw == "2017-04-02"
    assert results[1]["BOSTON TEA PARTY"].start_date_raw == "2017-03-23"
    assert results[2]["D-TOWNSWING"].start_date_raw == "2017-06-15"
    assert results[5]["SWEETHEART SWING CLASSIC"].start_date_raw == "2018-02-16"
    assert results[5]["HIGH DESERT DANCE CLASSIC"].end_date_raw == "2018-03-11"
    assert results[5]["Westie in Pink City"].location_raw == "France"
    assert "Westie" not in results[5]
    # Preserve the printed year, including source typos; do not invent a correction.
    assert results[5]["SWINGCOUVER"].start_date_raw == "2017-01-18"
    assert results[5]["TULSA SPRING SWING"].end_date_raw == "2017-04-01"
    assert results[25]["UCWDC World Championships"].start_date_raw == "2023-01-01"
    assert results[25]["UCWDC World Championships"].row_classes == (
        "newsletter_status_colour_unverified",
    )


def test_exact_reviewed_policy_newsletters_are_empty_event_listings():
    from pathlib import Path

    page = NewsletterPage()
    for volume in range(14, 22):
        body = Path(
            f"src/swingset/sources/wsdc_newsletter/fixtures/newsletter-vol{volume}.pdf"
        ).read_bytes()
        result = page.parse(page.extract(body), context(page))
        assert not result.observations and result.legitimate_empty
        assert not result.warnings


def test_changed_policy_body_or_extract_does_not_inherit_empty_review():
    from pathlib import Path

    page = NewsletterPage()
    body = Path("src/swingset/sources/wsdc_newsletter/fixtures/newsletter-vol14.pdf").read_bytes()
    changed = page.parse(page.extract(body + b"\n% changed document\n"), context(page))
    assert not changed.legitimate_empty
    assert changed.warnings
    extracted = page.extract(body)
    extracted["pages"][0] += "\nAn unreviewed new section"
    changed = page.parse(extracted, context(page))
    assert not changed.legitimate_empty
    assert changed.warnings


def test_retained_map_single_dates_are_explicit_one_day_without_losing_hiatus():
    from pathlib import Path

    page = CalendarPage()
    body = Path(
        "src/swingset/sources/wsdc_calendar/fixtures/calendar-2017-single-dates.html"
    ).read_bytes()
    result = page.parse(page.extract(body), context(page))
    rows = {row.payload.name_raw: row.payload for row in result.observations}
    cmj = rows["CMJ NSW West Coast Swing Dance Championships"]
    assert (cmj.start_date_raw, cmj.end_date_raw) == ("2017-06-08", "2017-06-08")
    assert "The Chicago Classic (NASDE) - HIATUS" in rows
    assert not result.warnings


def test_retained_newsletter_date_footnotes_spacing_and_column_boundaries():
    from pathlib import Path

    page = NewsletterPage()
    results = {}
    for volume, count in [(3, 45), (7, 31), (13, 37)]:
        body = Path(
            f"src/swingset/sources/wsdc_newsletter/fixtures/newsletter-vol{volume}.pdf"
        ).read_bytes()
        result = page.parse(page.extract(body), context(page))
        assert len(result.observations) == count
        results[volume] = {row.payload.name_raw: row.payload for row in result.observations}
        assert any(
            warning.code == "newsletter_undated_listing" and "Hiatus" in str(warning.evidence)
            for warning in result.warnings
        )
    assert results[3]["ROCKY MOUNTAIN FIVE DANCE (RM5)"].start_date_raw == "2017-09-15"
    assert results[3]["BRIDGE TOWN SWING"].start_date_raw == "2017-09-21"
    assert results[7]["NEW ORLEANS DANCE MARDI GRAS"].end_date_raw == "2018-07-22"
    assert results[13]["WINTER COAST SWING"].start_date_raw == "2020-02-21"


def test_newsletter_ambiguous_discontinuous_and_malformed_dates_stay_unparsed():
    from swingset.sources.wsdc_newsletter.adapter import _dated_range

    for printed in [
        "Dec 31, 2019, Jan 3-5, 2020",
        "Dec 29, 2022, Jan 2, 2023",
        "May 24 – 26, 20189",
        "October 19-22, 201717",
        "On Hiatus",
    ]:
        assert _dated_range(printed) is None
