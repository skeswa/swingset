"""Approved real-body DCN controls remain isolated from acquisition and admission."""

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from swingset.model.observations import decode_payload, encode_payload
from swingset.sources.base import ExtractError, ParseContext, ParseError
from swingset.sources.dcn import SOURCE, IndexPage, LegacyEventPage
from swingset.sources.dcn.nuxt import evaluate_nuxt

FIXTURES = Path(__file__).parent / "fixtures/sources/dcn"
PROVENANCE = json.loads((FIXTURES / "real-provenance-20260917.json").read_bytes())
LEGACY, INDEX = (row["file"] for row in PROVENANCE)


def body(name):
    value = (FIXTURES / name).read_bytes()
    record = next((record for record in PROVENANCE if record["file"] == name), None)
    if record:
        assert hashlib.sha256(value).hexdigest() == record["body_sha256"]
    return value


def context(page):
    record = PROVENANCE[0 if isinstance(page, LegacyEventPage) else 1]
    return ParseContext(
        "approved-fixture",
        "offline-only",
        record["original_url"],
        "dcn",
        page.kind,
        "dcn:1546230" if isinstance(page, LegacyEventPage) else None,
        record["captured_at"],
    )


def test_complete_real_nuxt_inventory_matches_all_rendered_event_locators():
    page = IndexPage()
    raw = body(INDEX)
    extract = page.extract(raw)
    result = page.parse(extract, context(page))
    sheet = result.observations[0].payload
    rendered = {
        "https://danceconvention.net" + node.attributes["href"]
        for node in HTMLParser(raw).css("a[href]")
        if node.attributes["href"].startswith("/eventdirector/en/eventpage/")
    }
    assert len(sheet.events) == len(rendered) == 50
    assert {event.event_url for event in sheet.events} == rendered
    assert len({event.source_event_ref for event in sheet.events}) == 50
    assert sum("WSDC" in event.affiliations for event in sheet.events) == 27
    assert sum(event.results for event in sheet.events) == 36
    assert sheet.available_years == tuple(range(2025, 2012, -1))
    assert sheet.enumeration_status == "listed_only"
    assert sheet.events[0].name_raw == "WeAsOne Indonesia"
    assert sheet.events[0].source_event_ref == "dcn:300873831"
    assert sheet.events[0].start_date_raw == "11/7/25"
    assert sheet.events[0].location_raw == "Jakarta, Indonesia"
    assert any(event.banner_url_raw is None for event in sheet.events)
    assert any("Казань" == event.location_raw for event in sheet.events)
    assert not result.watches and SOURCE.seed_watches({}, {}) == []
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet
    assert {warning.code for warning in result.warnings} == {"dcn_canonical_admission_pending"}


def test_real_legacy_metadata_preserves_locator_without_claiming_results_or_pdf():
    page = LegacyEventPage()
    raw = body(LEGACY)
    result = page.parse(page.extract(raw), context(page))
    sheet = result.observations[0].payload
    assert sheet.source_event_ref == "dcn:1546230" and sheet.name_raw == "Riga Summer Swing"
    assert (sheet.start_date_raw, sheet.end_date_raw, sheet.location_raw) == (
        "8/9/18",
        "8/13/18",
        "Riga, Latvia",
    )
    assert (
        sheet.results_url
        == "https://danceconvention.net/eventdirector/en/eventpage/1546230-riga-summer-swing/results"
    )
    assert sheet.results_availability == "unknown" and not result.watches
    assert b".pdf" not in raw.lower() and b"roundscores" not in raw
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet
    assert {warning.code for warning in result.warnings} == {
        "dcn_canonical_admission_pending",
        "dcn_results_unassessed",
    }


@pytest.mark.parametrize(("key", "page"), [("index", IndexPage()), ("legacy", LegacyEventPage())])
def test_changed_real_body_derivatives_change_consumed_fields(key, page):
    change = json.loads((FIXTURES / "changed-controls.json").read_bytes())[key]
    original = body(change["base_file"])
    changed = original.replace(change["old"].encode(), change["new"].encode())
    assert changed != original
    before, after = page.extract(original), page.extract(changed)
    assert before != after
    result = page.parse(after, context(page))
    sheet = result.observations[0].payload
    assert (sheet.events[0].name_raw if key == "index" else sheet.name_raw) == change["new"]


@pytest.mark.parametrize("page", [IndexPage(), LegacyEventPage()])
@pytest.mark.parametrize("name", ["empty.html", "malformed-nuxt.html"])
def test_empty_malformed_and_wrong_kind_bodies_fail(page, name):
    with pytest.raises(ExtractError):
        page.extract(body(name))
    with pytest.raises(ExtractError):
        page.extract(body(INDEX if isinstance(page, LegacyEventPage) else LEGACY))


@pytest.mark.parametrize("page", [IndexPage(), LegacyEventPage()])
@pytest.mark.parametrize("field", ["source", "kind", "url", "source_ref"])
def test_exact_source_kind_and_original_ownership_are_required(page, field):
    original = body(LEGACY if isinstance(page, LegacyEventPage) else INDEX)
    value = {
        "source": "other",
        "kind": "dcn.round_pdf",
        "url": "https://example.test/unreviewed",
        "source_ref": "dcn:999",
    }[field]
    with pytest.raises(ParseError):
        page.parse(page.extract(original), replace(context(page), **{field: value}))


@pytest.mark.parametrize(
    "mutation",
    ["empty", "id", "bool", "fields", "duplicates", "years", "copies", "route", "malformed_url"],
)
def test_changed_index_payload_shapes_fail_closed(monkeypatch, mutation):
    import swingset.sources.dcn.adapter as adapter

    payload = evaluate_nuxt(body(INDEX))
    render = payload["state"]["common"]["currentPageRenderData"]
    row = render["lastYearEvents"][0]
    if mutation == "empty":
        render["lastYearEvents"] = []
    elif mutation == "id":
        row["eventId"] = 123
    elif mutation == "bool":
        row["results"] = "true"
    elif mutation == "fields":
        row["unknownCriticalField"] = "unreviewed"
    elif mutation == "duplicates":
        render["lastYearEvents"].append(row.copy())
    elif mutation == "years":
        render["availableYears"] = [True]
    elif mutation == "copies":
        payload["data"][0]["renderData"] = {}
    elif mutation == "malformed_url":
        row["eventPage"] = "https://[bad/eventdirector/en/eventpage/300873831"
    else:
        payload["routePath"] = "/en/upcoming"
    if mutation != "copies":
        payload["data"][0]["renderData"] = render
    monkeypatch.setattr(adapter, "evaluate_nuxt", lambda _: payload)
    with pytest.raises(ExtractError):
        IndexPage().extract(b"offline controlled decoder result")


def test_legacy_mobile_mismatch_foreign_results_and_invalid_dates_reject():
    page = LegacyEventPage()
    original = body(LEGACY)
    changes = [
        (b"8/9/18 - 8/13/18", b"unknown dates"),
        (b"1546230-riga-summer-swing/results", b"999-other/results"),
    ]
    for old, new in changes:
        changed = original.replace(old, new)
        with pytest.raises((ExtractError, ParseError)):
            page.parse(page.extract(changed), context(page))
    changed = original.replace(
        b"Riga Summer Swing\n<br/><small>", b"Different name\n<br/><small>", 1
    )
    assert changed != original
    with pytest.raises(ExtractError, match="disagree"):
        page.extract(changed)


def test_unrelated_scripts_and_decoration_do_not_change_index_fingerprint():
    page = IndexPage()
    original = body(INDEX)
    changed = (
        b"<!-- synthetic unrelated decoration --><script>throw new Error('not executed')</script>"
        + original
    )
    assert page.extract(original) == page.extract(changed)


@pytest.mark.parametrize("page", [IndexPage(), LegacyEventPage()])
@pytest.mark.parametrize(
    "url",
    [
        "https://[bad/eventdirector/en/eventpage/1",
        "https://danceconvention.net\n/eventdirector/en/eventsarchive",
        "\x00https://danceconvention.net/eventdirector/en/eventpage/1546230",
    ],
)
def test_malformed_context_urls_are_controlled_parse_failures(page, url):
    original = body(LEGACY if isinstance(page, LegacyEventPage) else INDEX)
    with pytest.raises(ParseError):
        page.parse(page.extract(original), replace(context(page), url=url))


def test_fresh_process_decodes_both_kinds_without_first_encoding_them():
    payloads = []
    for page, name in [(IndexPage(), INDEX), (LegacyEventPage(), LEGACY)]:
        payload = page.parse(page.extract(body(name)), context(page)).observations[0].payload
        payloads.append([payload.kind, encode_payload(payload)])
    # A new interpreter has not learned encode_payload's dynamic kind aliases.
    program = """
import json, sys
import swingset.sources.dcn
from swingset.model.observations import decode_payload
values = [decode_payload(kind, raw) for kind, raw in json.load(sys.stdin)]
assert type(values[0]).__name__ == 'DcnIndexSheet' and len(values[0].events) == 50
assert type(values[1]).__name__ == 'DcnEventMetadata' and values[1].source_event_ref == 'dcn:1546230'
"""
    subprocess.run(
        [sys.executable, "-c", program],
        input=json.dumps(payloads),
        text=True,
        check=True,
        capture_output=True,
    )
