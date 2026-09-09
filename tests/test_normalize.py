from datetime import date

from swingset.normalize import (
    EventCandidate,
    classify_contest,
    event_series_slug,
    matching_events,
    normalize_event_name,
    normalize_name,
)


def test_name_normalization_keeps_particles_and_splits_suffix() -> None:
    name = normalize_name("  José de la Cruz, Jr. ")
    assert name.value == "jose de la cruz"
    assert name.tokens == ("jose", "de", "la", "cruz")
    assert name.suffix == "jr"


def test_contest_classification() -> None:
    result = classify_contest("Sophisticated Novice Jack & Jill")
    assert (result.division, result.age_division, result.contest_type) == (
        "novice",
        "sophisticated",
        "jack_and_jill",
    )


def test_source_contest_vocabulary_goldens() -> None:
    cases = {
        "All-Stars Jack & Jill Prelim": ("allstar", "jack_and_jill", "random_partner"),
        "WCS Sophisticated Jack and Jill Finals": ("none", "jack_and_jill", "random_partner"),
        "Champions Strictly Swing Final": ("champion", "strictly", "open_couple"),
        "Novice Classic Routine": ("novice", "classic", "perm_couple"),
        "Rising Star Showcase": ("none", "showcase", "perm_couple"),
        "Newcomer / Novice Jack & Jill": ("newcomer", "jack_and_jill", "random_partner"),
    }
    for raw, expected in cases.items():
        result = classify_contest(raw)
        assert (result.division, result.contest_type, result.partner_mode) == expected


def test_contest_dance_style_classification() -> None:
    assert classify_contest("Hustle Strictly Swing").dance_style == "other"
    assert classify_contest("Country Strictly Swing").dance_style == "country"
    assert classify_contest("Lindy Strictly").dance_style == "lindy"
    assert classify_contest("Advanced Jack & Jill").dance_style == "wcs"


def test_event_normalization_series_and_date_matching() -> None:
    assert normalize_event_name("Montréal Westie Fest!") == "montreal westie fest"
    assert event_series_slug("The Open 2026 (On Hiatus)") == "the-open"
    candidates = [
        EventCandidate(
            "later", "Montréal Westie Fest", date(2026, 8, 7), date(2026, 8, 9)
        ),
        EventCandidate(
            "earlier", "Montréal Westie Fest", date(2026, 8, 6), date(2026, 8, 8)
        ),
        EventCandidate("wrong", "Another Event", date(2026, 8, 7), date(2026, 8, 9)),
    ]
    assert [
        item.event_id
        for item in matching_events(
            "Montreal Westie Fest", date(2026, 8, 8), date(2026, 8, 10), candidates
        )
    ] == ["earlier", "later"]
