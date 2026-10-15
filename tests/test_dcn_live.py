"""The registered danceconvention.net archive and results kinds (D-0255)."""

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from helpers.loop_ticks import run_round

from swingset.admission import dcn_contract
from swingset.admission.contracts import contract_version, declare, inspect
from swingset.clock import FakeClock
from swingset.fetch.archive import canonical, digest
from swingset.model.observations import decode_payload, encode_payload
from swingset.sources import get_page_kind
from swingset.sources.base import ExtractError, ParseContext, ParseError
from swingset.sources.dcn import live
from swingset.sources.dcn.live import ArchivePage, EventPage, date_range, final_table
from swingset.sources.records import EventSheet, RoundSheet, SourceEventRow
from swingset.state.db import open_database

FIXTURES = Path(__file__).parent / "fixtures/sources/dcn"
PROVENANCE = json.loads((FIXTURES / "live-provenance-20261015.json").read_bytes())
ARCHIVE = "live-eventsarchive-20261015.nuxt.html"
AUSTIN = "live-results-301367300-20261015.nuxt.html"
AMERICANO = "live-results-301108262-20261015.nuxt.html"


def body(name: str) -> bytes:
    """A reduced fixture: the exact payload script of the captured body (see provenance)."""
    value = (FIXTURES / name).read_bytes()
    record = next(item for item in PROVENANCE["bodies"] if item["file"] == name)
    assert hashlib.sha256(value).hexdigest() == record["excerpt_sha256"]
    return value


@pytest.mark.parametrize("name", [ARCHIVE, AUSTIN, AMERICANO])
def test_each_excerpt_extracts_exactly_as_its_full_capture_did(name):
    page = ArchivePage() if name == ARCHIVE else EventPage()
    record = next(item for item in PROVENANCE["bodies"] if item["file"] == name)
    extract = page.extract(body(name))
    assert digest(canonical(extract)) == record["original_extract_sha256"]


def url(name: str) -> str:
    return next(item for item in PROVENANCE["bodies"] if item["file"] == name)["original_url"]


def context(page, name: str, ref: str | None = None) -> ParseContext:
    return ParseContext("snap", "watch", url(name), "dcn", page.kind, ref, "2026-10-15T17:25:00Z")


def parse(page, extract, ctx):
    """Parse as admission does: with the adapter's declaration attached."""
    return declare(page, ctx, extract, page.parse(extract, ctx))


@pytest.fixture(scope="module")
def archive():
    page = ArchivePage()
    extract = page.extract(body(ARCHIVE))
    return extract, parse(page, extract, context(page, ARCHIVE))


@pytest.fixture(scope="module")
def austin():
    page = EventPage()
    extract = page.extract(body(AUSTIN))
    return extract, parse(page, extract, context(page, AUSTIN, "dcn:301367300"))


def test_archive_follows_published_wsdc_events_with_results(archive):
    extract, result = archive
    assert len(extract["events"]) == 44 and extract["unreviewed_fields"] == []
    rows = [o.payload for o in result.observations]
    assert all(isinstance(row, SourceEventRow) for row in rows)
    assert {o.scope.ref for o in result.observations} == {"dcn"}
    assert len(rows) == len(result.watches) == 14
    assert [row.url for row in rows] == [watch.url for watch in result.watches]
    names = {row.name_raw: row for row in rows}
    assert names["Swing & Snow 2026"].date_raw == "Feb 5 - 8, 2026"
    assert names["Southern Lights Swing"].date_raw == "Feb 26 - Mar 2, 2026"
    assert names["HONEY FEST 2026"].source_ref == "dcn:301107621"
    assert "Swingesota Summer Spectacular 2026" not in names  # WSDC, but no results yet
    assert "BRAZUKA SUMMER CUP 2026" not in names  # zouk
    watch = result.watches[0]
    assert (watch.source, watch.kind, watch.parser, watch.method) == (
        "dcn",
        "event",
        "dcn.event",
        "GET",
    )
    assert watch.url == "https://danceconvention.net/eventdirector/en/eventpage/301368170/results"
    assert watch.source_ref == "dcn:301368170"
    report = inspect(context(ArchivePage(), ARCHIVE), body(ARCHIVE), extract, result)
    assert report.failures == () and report.contract_version == "1"
    assert report.coverage.source_count == report.coverage.interpreted_count == 14


def test_results_tab_becomes_listing_event_sheet_and_final_round_sheets(austin):
    extract, result = austin
    assert extract["unreviewed_fields"] == [] and extract["publish_results"] is True
    kinds = [o.kind for o in result.observations]
    assert kinds[:2] == ["source_event_row", "event_sheet"]
    listing = result.observations[0]
    assert listing.scope.kind == "source_index" and listing.scope.ref == "dcn:301367300"
    assert listing.payload.date_raw == "Sep 18 - 20, 2026"
    sheet = result.observations[1].payload
    assert isinstance(sheet, EventSheet) and len(sheet.round_links) == 29
    finals = [o.payload for o in result.observations if isinstance(o.payload, RoundSheet)]
    assert len(finals) == 16 and all(
        o.scope.kind == "source_event" for o in result.observations[1:]
    )
    allstar = next(item for item in finals if item.contest_name_raw == "Allstar J&J")
    table = allstar.tables[0]
    assert [cell.text for cell in table.headers] == ["Place", "Leader", "Follower"]
    assert [cell.text for cell in table.rows[0].cells] == [
        "1",
        "William McGregor",
        "Taylor Linahan",
    ]
    assert len(table.rows) == 11 and allstar.source_round_ref == "30190099"
    # A masked partner stays empty; the named competitor stays.
    novice = next(item for item in finals if item.contest_name_raw == "Novice J&J")
    assert [cell.text for cell in novice.tables[0].rows[0].cells] == ["1", "Braxton White", None]
    # A follower competitor goes in the Follower column, the pro in Leader.
    follows = next(item for item in finals if item.contest_name_raw.endswith("| FOLLOWS"))
    assert [cell.text for cell in follows.tables[0].rows[-1].cells] == [
        "19",
        "KP Rutland",
        "Jocelyn McDowell",
    ]
    codes = {warning.code: warning for warning in result.warnings}
    assert set(codes) == {"dcn_nonfinal_rounds_withheld", "dcn_names_masked_at_source"}
    assert len(codes["dcn_nonfinal_rounds_withheld"].evidence["round_ids"]) == 13
    for observation in result.observations:
        assert decode_payload(observation.kind, encode_payload(observation.payload)) == (
            observation.payload
        )
    report = inspect(context(EventPage(), AUSTIN, "dcn:301367300"), body(AUSTIN), extract, result)
    assert report.failures == () and report.proposed_removal == "none"
    assert (
        report.coverage.source_count
        == report.coverage.interpreted_count
        == sum(len(item.tables[0].rows) for item in finals)
    )


def test_the_extract_keeps_no_city_or_country(austin):
    extract, _ = austin
    text = json.dumps(extract)
    assert "Overland Park" not in text and "CityAndState" not in text and "Country" not in text


def test_role_split_finals_are_withheld_not_merged():
    page = EventPage()
    extract = page.extract(body(AMERICANO))
    result = parse(page, extract, context(page, AMERICANO, "dcn:301108262"))
    codes = {warning.code: warning for warning in result.warnings}
    assert len(codes["dcn_role_split_final_withheld"].evidence["round_ids"]) == 2
    finals = [o.payload for o in result.observations if isinstance(o.payload, RoundSheet)]
    assert len(finals) == 6 and not any("ProAm" in item.contest_name_raw for item in finals)
    assert inspect(context(page, AMERICANO), body(AMERICANO), extract, result).failures == ()


def test_unpublished_results_yield_only_the_listing(austin):
    extract, _ = austin
    hidden = {**extract, "publish_results": False}
    ctx = context(EventPage(), AUSTIN, "dcn:301367300")
    result = parse(EventPage(), hidden, ctx)
    assert [o.kind for o in result.observations] == ["source_event_row"]
    assert [warning.code for warning in result.warnings] == ["dcn_results_unpublished"]
    report = inspect(ctx, b"", hidden, result)
    # Nothing is admitted from an unpublished tab, and it retires nothing.
    assert "coverage_no_terminal" in report.failures and report.proposed_removal == "none"


def test_an_unreviewed_source_field_holds_admission(austin, monkeypatch):
    payload = live.evaluate_nuxt(body(AUSTIN))
    render = payload["state"]["common"]["currentPageRenderData"]
    render["results"][0]["rounds"][0]["rankings"][0]["disqualified"] = True
    monkeypatch.setattr(live, "evaluate_nuxt", lambda _: payload)
    page = EventPage()
    extract = page.extract(b"decoded above")
    assert extract["unreviewed_fields"] == ["$.contests[*].rounds[*].rankings[*].disqualified"]
    ctx = context(page, AUSTIN, "dcn:301367300")
    assert "critical_unknown" in inspect(ctx, b"", extract, parse(page, extract, ctx)).failures


@pytest.mark.parametrize(
    "mutation", ["route", "event_id", "rank", "role", "pdf_path", "duplicate_round"]
)
def test_changed_results_shapes_fail_closed(monkeypatch, mutation):
    payload = live.evaluate_nuxt(body(AUSTIN))
    render = payload["state"]["common"]["currentPageRenderData"]
    first = render["results"][0]["rounds"][0]
    if mutation == "route":
        payload["routePath"] = "/en/eventpage/301367300/signups"
    elif mutation == "event_id":
        render["danceEvent"]["eventId"] = 1
    elif mutation == "rank":
        first["rankings"][0]["rank"] = "1"
    elif mutation == "role":
        first["rankings"][0]["competitorRole"] = "COUPLE"
    elif mutation == "pdf_path":
        first["scoresLink"] = "https://example.test/x.pdf"
    else:
        render["results"][1]["rounds"].append(first)
    monkeypatch.setattr(live, "evaluate_nuxt", lambda _: payload)
    with pytest.raises(ExtractError):
        EventPage().extract(b"decoded above")


def test_page_decoration_does_not_change_the_extract():
    original = body(AUSTIN)
    decorated = b"<!-- sentry-trace 0123 --><script>window.x = 1</script>" + original
    assert EventPage().extract(original) == EventPage().extract(decorated)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "other"),
        ("kind", "dcn.archive"),
        ("url", "https://danceconvention.net/eventdirector/en/eventpage/999/results"),
        ("url", "https://example.test/eventdirector/en/eventpage/301367300/results"),
        ("url", "https://danceconvention.net/eventdirector/en/eventpage/301367300/signups"),
    ],
)
def test_results_parse_requires_its_own_event_context(austin, field, value):
    extract, _ = austin
    ctx = replace(context(EventPage(), AUSTIN, "dcn:301367300"), **{field: value})
    with pytest.raises(ParseError):
        EventPage().parse(extract, ctx)


def test_date_range_forms():
    assert date_range("12/30/26", "1/2/27") == "Dec 30 - Jan 2, 2027"
    for start, end in (("9/20/26", "9/18/26"), ("2026-09-18", "9/20/26"), ("2/30/26", "3/1/26")):
        with pytest.raises(ExtractError):
            date_range(start, end)


def test_the_contract_repeats_the_adapter_rules(austin):
    extract, _ = austin
    assert dcn_contract.KNOWN_BOUNDED_WARNINGS == live.BOUNDED_WARNINGS
    assert dcn_contract.KINDS == set(live.SOURCE.page_kinds)
    for contest in extract["contests"]:
        for item in contest["rounds"]:
            single = dcn_contract.single_table_final(item["rankings"])
            assert single == (final_table(contest["name"], item) is not None)
    assert contract_version("dcn.event") == contract_version("dcn.archive") == "1"


def test_the_registry_resolves_both_kinds_and_seeds_the_archive():
    assert get_page_kind("dcn.archive") is live.SOURCE.page_kinds["dcn.archive"]
    assert get_page_kind("dcn.event") is live.SOURCE.page_kinds["dcn.event"]
    (seed,) = live.SOURCE.seed_watches({}, {})
    assert (seed.url, seed.parser, seed.kind) == (live.ARCHIVE_URL, "dcn.archive", "index")


def test_archived_dcn_results_run_end_to_end(tmp_path: Path) -> None:
    pages = {
        "/eventdirector/en/eventsarchive": body(ARCHIVE),
        "/eventdirector/en/eventpage/301367300/results": body(AUSTIN),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page = pages.get(request.url.path)
        return httpx.Response(200, content=page) if page is not None else httpx.Response(404)

    config = tmp_path / "config"
    config.mkdir()
    shutil.copy("config/hosts.toml", config / "hosts.toml")
    (config / "sources.toml").write_text(
        "[sources.dcn]\nenabled=true\nindex_urls=[]\n", encoding="utf-8"
    )
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    clock = FakeClock()
    with open_database(tmp_path / "state") as database:
        conn = database.connection
        for _ in range(30):
            run_round(
                database,
                config_dir=config,
                overrides_dir=overrides,
                clock=clock,
                transport=httpx.MockTransport(handler),
            )
            if conn.execute("SELECT count(*) FROM placements").fetchone()[0]:
                break
        assert (
            conn.execute("SELECT count(*) FROM source_events WHERE source='dcn'").fetchone()[0]
            == 14
        )
        assert (
            conn.execute("SELECT count(*) FROM watches WHERE parser='dcn.event'").fetchone()[0]
            == 14
        )
        events = {
            row[0]
            for row in conn.execute(
                "SELECT event_id FROM source_event_map WHERE source='dcn' AND source_ref=?",
                ("dcn:301367300",),
            )
        }
        assert len(events) == 1
        (event,) = events
        assert (
            conn.execute("SELECT count(*) FROM contests WHERE event_id=?", (event,)).fetchone()[0]
            == 16
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM rounds r JOIN contests c USING(contest_id) "
                "WHERE c.event_id=? AND r.round_type='final'",
                (event,),
            ).fetchone()[0]
            == 16
        )
        places = conn.execute(
            "SELECT p.place,l.name_raw,f.name_raw FROM placements p "
            "JOIN contests c USING(contest_id) "
            "LEFT JOIN entries l ON l.entry_id=p.leader_entry_id "
            "LEFT JOIN entries f ON f.entry_id=p.follower_entry_id "
            "WHERE c.name_raw='Allstar J&J' ORDER BY p.place"
        ).fetchall()
        assert len(places) == 11
        assert tuple(places[0]) == (1, "William McGregor", "Taylor Linahan")
        assert not conn.execute(
            "SELECT 1 FROM entries WHERE event_id=? AND name_raw LIKE '%*%'", (event,)
        ).fetchone()


def _with_contests(extract, contests):
    return {**extract, "contests": contests}


def test_a_final_without_rankings_is_withheld(austin):
    extract, _ = austin
    contests = [dict(contest) for contest in extract["contests"]]
    final = next(item for item in contests[1]["rounds"] if item["final"])
    contests[1] = {
        **contests[1],
        "rounds": [
            {**item, "rankings": []} if item is final else item for item in contests[1]["rounds"]
        ],
    }
    changed = _with_contests(extract, contests)
    ctx = context(EventPage(), AUSTIN, "dcn:301367300")
    result = parse(EventPage(), changed, ctx)
    codes = {warning.code: warning for warning in result.warnings}
    assert codes["dcn_empty_final_withheld"].evidence == {"round_ids": [str(final["id"])]}
    refs = [o.payload.source_round_ref for o in result.observations if o.kind == "round_sheet"]
    assert str(final["id"]) not in refs and len(refs) == 15
    assert final_table(contests[1]["name"], {**final, "rankings": []}) is None
    assert not dcn_contract.single_table_final([])
    assert inspect(ctx, b"", changed, result).failures == ()


def test_an_empty_published_payload_is_held_and_never_removes(austin):
    extract, _ = austin
    ctx = context(EventPage(), AUSTIN, "dcn:301367300")
    empty = _with_contests(extract, [])
    report = inspect(ctx, b"", empty, parse(EventPage(), empty, ctx))
    assert "dcn_published_without_results" in report.failures
    assert report.state == "needs_review" and report.proposed_removal == "none"


def test_a_shrunken_payload_has_no_removal_authority(austin):
    extract, _ = austin
    ctx = context(EventPage(), AUSTIN, "dcn:301367300")
    shrunk = _with_contests(extract, extract["contests"][:2])
    result = parse(EventPage(), shrunk, ctx)
    report = inspect(ctx, b"", shrunk, result)
    assert report.failures == () and report.proposed_removal == "none"
    assert sum(o.kind == "round_sheet" for o in result.observations) == 2


@pytest.mark.parametrize("name", ["******* *******", "J**** S****", "Jane *", " "])
def test_any_mask_character_hides_the_whole_name(name):
    item = {
        "name": "Finals",
        "rankings": [
            {"role": "LEADER", "name": "Named Leader", "partner": name, "rank": 1, "interval": None}
        ],
    }
    table = final_table("Novice J&J", item)
    assert [cell.text for cell in table.rows[0].cells] == ["1", "Named Leader", None]
