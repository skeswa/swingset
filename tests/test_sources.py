import gzip
from pathlib import Path
from types import SimpleNamespace

from swingset.sources.base import ParseContext
from swingset.sources.eepro import RoundPage as EEProRound
from swingset.sources.scoringdance import EventPage as ScoringEvent
from swingset.sources.scoringdance import RecentPage, SitemapPage
from swingset.sources.scoringdance import RoundPage as ScoringRound
from swingset.sources.wdr import AwardsPage as WDRAwards
from swingset.sources.wdr import RoundsPage as WDRRounds
from swingset.sources.wsdc_calendar import EventsPage
from swingset.sources.wsdc_registry import DancerPage, is_verified_miss

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


def context(kind: str, *, source_ref: str = "example:1") -> ParseContext:
    return ParseContext(
        "snap_1",
        "watch_1",
        "https://example.test/round.html",
        "example",
        kind,
        source_ref,
        "2026-09-08T00:00:00Z",
    )


def test_calendar_extract_captures_attribute_only_changes() -> None:
    body = (FIXTURES / "synthetic_calendar.html").read_bytes()
    page = EventsPage()
    original = page.extract(body)
    changed = page.extract(body.replace(b"event-trial", b"event-canceled"))
    assert original != changed
    result = page.parse(original, context(page.kind, source_ref="wsdc"))
    assert result.observations[0].payload.row_classes == ("event-trial",)


def test_eepro_round_is_header_driven_and_keeps_raw_mark() -> None:
    page = EEProRound()
    result = page.parse(
        page.extract((FIXTURES / "synthetic_eepro_round.html").read_bytes()),
        context(page.kind, source_ref="eepro:example"),
    )
    table = result.observations[0].payload.tables[0]
    assert [cell.text for cell in table.headers] == [
        "Count",
        "Competitor",
        "J1",
        "BIB",
        "Counts",
        "Sum",
        "Promote",
        "Alt",
    ]
    assert table.rows[0].cells[2].text == "Y"


def test_scoringdance_extract_ignores_outer_nonce_but_keeps_data_attributes() -> None:
    body = (FIXTURES / "synthetic_scoringdance_round.html").read_bytes()
    page = ScoringRound()
    first = page.extract(body)
    second = page.extract(body.replace(b'nonce="one"', b'nonce="two"'))
    assert first == second
    result = page.parse(first, context(page.kind, source_ref="scoringdance:1"))
    assert ("data-wsdc", "123") in result.observations[0].payload.tables[0].rows[0].cells[
        1
    ].attributes


def test_wdr_redacted_zero_judge_mark_becomes_null() -> None:
    page = WDRRounds()
    result = page.parse(
        page.extract((FIXTURES / "synthetic_wdr_rounds.json").read_bytes()),
        context(page.kind, source_ref="wdr:00000000-0000-0000-0000-000000000000"),
    )
    table = result.observations[0].payload.tables[0]
    assert table.rows[0].cells[1].text == "***"
    assert table.rows[0].cells[2].text is None
    assert page.expected_statuses(SimpleNamespace(ever_ok=0)) == frozenset({403})
    assert page.expected_statuses(SimpleNamespace(ever_ok=1)) == frozenset()


def test_registry_nested_placements_and_safe_invalid_outcome() -> None:
    page = DancerPage()
    found = {
        "leader": {
            "type": "dancer",
            "dancer": {"id": 96, "first_name": "Bill", "last_name": "Borgida", "wscid": 100},
            "level": {"required": "ALS", "allowed": "CHMP"},
            "placements": {
                "West Coast Swing": {
                    "CHMP": {
                        "competitions": [
                            {
                                "role": "leader",
                                "points": 1,
                                "result": "F",
                                "event": {"id": 53, "name": "Summer Hummer", "date": "August 2002"},
                            }
                        ]
                    }
                }
            },
        },
        "follower": {"type": "dancer", "dancer": {"wscid": 100}, "placements": []},
        "dominate_role": "Primary Role Leader",
        "is_pro": 0,
        "recent_year": "2002",
    }
    ctx = context(page.kind, source_ref="wsdc:100")
    payload = page.parse(found, ctx).observations[0].payload
    assert payload.outcome == "found"
    assert payload.placements[0].event_id_raw == "53"
    invalid = page.parse({"error": "unknown unverified miss"}, ctx)
    assert invalid.observations[0].payload.outcome == "invalid"
    assert invalid.warnings[0].code == "invalid_response"


def test_registry_verified_real_miss_is_exact_body_match() -> None:
    fixture = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1000000.body").read_bytes()
    page = DancerPage()
    assert is_verified_miss(fixture, 404)
    assert not is_verified_miss(fixture + b" ", 404)
    result = page.parse(page.extract(fixture), context(page.kind, source_ref="wsdc:1000000"))
    assert result.observations[0].payload.outcome == "not_found"


def test_real_wdr_routeinfo_fixtures() -> None:
    root = Path("src/swingset/sources/wdr/fixtures")
    source_ref = "wdr:98011277-01cd-11f1-9a29-0aa72bbce9ea"
    rounds = WDRRounds()
    round_result = rounds.parse(
        rounds.extract((root / "rounds-2026-09-09.body").read_bytes()),
        context(rounds.kind, source_ref=source_ref),
    )
    assert len(round_result.observations) == 32
    assert {warning.code for warning in round_result.warnings} == {
        "wdr_finals_bib_unverified",
        "wdr_s_callback_unverified",
    }
    awards = WDRAwards()
    award_result = awards.parse(
        awards.extract((root / "awards-2026-09-09.body").read_bytes()),
        context(awards.kind, source_ref=source_ref),
    )
    assert len(award_result.observations) == 13


def test_real_scoringdance_event_fixture_discovers_rounds() -> None:
    page = ScoringEvent()
    fixture = Path("src/swingset/sources/scoringdance/fixtures/event-2026-09-09.body").read_bytes()
    ctx = ParseContext(
        "snap",
        "watch",
        "https://scoring.dance/enUS/events/418/results/",
        "scoringdance",
        page.kind,
        "scoringdance:418",
        "2026-09-09T04:06:51Z",
    )
    result = page.parse(page.extract(fixture), ctx)
    assert result.watches
    assert all(watch.source_ref == "scoringdance:418" for watch in result.watches)
    assert {observation.scope.kind for observation in result.observations} == {
        "source_event",
        "source_index",
    }
    event_row = next(
        observation.payload
        for observation in result.observations
        if observation.kind == "source_event_row"
    )
    assert (event_row.source_ref, event_row.name_raw, event_row.date_raw) == (
        "scoringdance:418",
        "Bristol Swing Fiesta 2026",
        "08/28/2026",
    )


def test_real_scoringdance_index_and_round_fields() -> None:
    root = Path("src/swingset/sources/scoringdance/fixtures")
    recent = RecentPage()
    recent_result = recent.parse(
        recent.extract((root / "recent-2026-09-09.body").read_bytes()),
        context(recent.kind, source_ref="scoringdance"),
    )
    first = recent_result.observations[0].payload
    assert (first.source_ref, first.name_raw, first.date_raw) == (
        "scoringdance:438",
        "Bavarian Open 2026",
        "09/10 - 09/14/2026",
    )
    sitemap = SitemapPage()
    sitemap_body = gzip.decompress((root / "sitemap-2026-09-09.body.gz").read_bytes())
    assert "418" in sitemap.extract(sitemap_body)
    for round_id, expected_round, expected_tables in (("6012", "prelim", 2), ("6014", "final", 1)):
        page = ScoringRound()
        body = (root / f"round-{round_id}-2026-09-09.body").read_bytes()
        ctx = ParseContext(
            "snap",
            "watch",
            f"https://scoring.dance/enUS/events/418/results/{round_id}.html",
            "scoringdance",
            page.kind,
            "scoringdance:418",
            "2026-09-09T00:00:00Z",
        )
        result = page.parse(page.extract(body), ctx)
        assert len(result.observations) == expected_tables
        sheet = result.observations[0].payload
        assert sheet.contest_name_raw == "Advanced Jack&Jill"
        assert sheet.round_name_raw == expected_round
        assert sheet.tables[0].headers[0].text == "Bib Number"
        assert sheet.tables[0].headers[1].text == "Leader"
        if expected_round == "prelim":
            assert result.observations[1].payload.tables[0].headers[1].text == "Follower"
        else:
            assert sheet.tables[0].headers[2].text == "Follower"
            assert sheet.tables[0].headers[-2].text == "Placement"
        name_cell = sheet.tables[0].rows[0].cells[1]
        assert ("data-wsdc", "21242") in name_cell.attributes
        assert any(
            ("title", "Peter Fradley (Chiefjudge)") in cell.attributes
            for cell in sheet.tables[0].headers
        )
        if expected_round == "prelim":
            assert ("row-data-state", "CB") in name_cell.attributes
