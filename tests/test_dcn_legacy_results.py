"""The audited legacy capture is one selected contest, never a full event claim."""

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from swingset.model.observations import decode_payload, encode_payload
from swingset.sources import get_page_kind
from swingset.sources.base import ExtractError, ParseContext, ParseError
from swingset.sources.dcn import SOURCE, LegacyResultsPage
from swingset.sources.dcn.nuxt import MAX_BODY_BYTES

FIXTURES = Path(__file__).parent / "fixtures/sources/dcn"
PROVENANCE = json.loads((FIXTURES / "results-provenance-20260917.json").read_bytes())
BODY = (FIXTURES / PROVENANCE["file"]).read_bytes()
PAGE = LegacyResultsPage()
CONTEXT = ParseContext(
    "real-approved-results",
    "offline",
    PROVENANCE["original_url"],
    "dcn",
    PAGE.kind,
    "dcn:1546230",
    PROVENANCE["captured_at"],
)


def parse(body=BODY):
    return PAGE.parse(PAGE.extract(body), CONTEXT)


def test_real_capture_provenance_and_entire_printed_inventory():
    assert hashlib.sha256(BODY).hexdigest() == PROVENANCE["body_sha256"]
    for prefix in ("receipt", "independent_review"):
        assert (
            hashlib.sha256(Path(PROVENANCE[prefix + "_path"]).read_bytes()).hexdigest()
            == PROVENANCE[prefix + "_sha256"]
        )
    result = parse()
    sheet = result.observations[0].payload
    assert (
        sheet.source_event_ref,
        sheet.event_name_raw,
        sheet.contest_id,
        sheet.contest_name_raw,
    ) == ("dcn:1546230", "Riga Summer Swing 2018", "2196606", "Jack'n'Jill Newcomer")
    assert [len(table.rows) for table in sheet.tables] == [9, 0, 8]
    assert [(table.label_raw, table.role) for table in sheet.tables] == [
        ("Finals", None),
        ("Prelims - leaders", "leader"),
        ("Prelims - followers", "follower"),
    ]
    assert sheet.tables[0].rows[0].member_names_raw == ("Erwin Dirckx", "Arnita Veidemane")
    assert sheet.tables[0].rows[-1].member_names_raw == ("Evaldas Miliauskas", "Chloe  Baize")
    assert sheet.tables[2].rows[0].member_names_raw == ("Kamila Oczkowska-Luczkos",)
    assert [(row.placement_low, row.placement_high) for row in sheet.tables[2].rows] == [
        (10, 10),
        (11, 14),
        (11, 14),
        (11, 14),
        (11, 14),
        (15, 16),
        (15, 16),
        (17, 17),
    ]
    tree = HTMLParser(BODY)
    # Exact table-by-table crosscheck prevents omitting populated follower rows
    # after the empty leader table or accidentally merging their role ownership.
    for source_table, parsed in zip(tree.css("table"), sheet.tables, strict=True):
        rows = [
            [cell.text().strip() for cell in row.css("td")] for row in source_table.css("tr")[1:]
        ]
        assert [(row.bib_raw, row.names_raw, row.placement_raw) for row in parsed.rows] == [
            tuple(row) for row in rows
        ]
        assert parsed.population_completeness == "unknown"
        assert all(row.bib_ownership == "row" and row.promotion == "unknown" for row in parsed.rows)
    assert (
        sheet.event_results_completeness == "unknown"
        and sheet.score_pdf_interpretation == "unassessed"
    )
    assert len(sheet.contest_locators) == 8
    assert {row.url for row in sheet.contest_locators} == {
        node.attributes["href"] for node in tree.css('a[data-update-zone="tabsZone"]')
    }
    assert {table.score_pdf_url for table in sheet.tables} == {
        "https://danceconvention.net" + node.attributes["href"]
        for node in tree.css("a[href]")
        if "/roundscores/" in node.attributes["href"]
    }
    assert sheet.tables[1].score_pdf_url == sheet.tables[2].score_pdf_url
    assert not result.watches and SOURCE.seed_watches({}, {}) == []
    assert {w.code for w in result.warnings} == {
        "dcn_results_population_unknown",
        "dcn_score_locators_unassessed",
        "dcn_canonical_admission_pending",
    }
    with pytest.raises(KeyError):
        get_page_kind(PAGE.kind)
    assert decode_payload(sheet.kind, encode_payload(sheet)) == sheet


def test_cold_process_codec_does_not_need_prior_encoding():
    sheet = parse().observations[0].payload
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import swingset.sources.dcn; from swingset.model.observations import decode_payload; p=decode_payload('dcn_legacy_results',sys.stdin.read()); assert p.contest_id=='2196606' and len(p.tables[0].rows)==9 and p.tables[1].rows==()",
        ],
        input=encode_payload(sheet),
        text=True,
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "name",
    [
        "empty.html",
        "malformed-results.html",
        "real-legacy-event-20180815191517.html",
        "real-eventsarchive-20251112105828.html",
    ],
)
def test_empty_malformed_and_other_kind_bodies_reject(name):
    with pytest.raises(ExtractError):
        PAGE.extract((FIXTURES / name).read_bytes())


@pytest.mark.parametrize("field", ["source", "kind", "source_ref", "url"])
def test_exact_context_kind_and_event_ownership(field):
    value = dict(
        source="other",
        kind="dcn.event_results",
        source_ref="dcn:999",
        url="https://danceconvention.net/eventdirector/en/eventpage/999/results",
    )[field]
    with pytest.raises(ParseError):
        PAGE.parse(PAGE.extract(BODY), replace(CONTEXT, **{field: value}))


@pytest.mark.parametrize(
    "url",
    [
        "https://[bad/results",
        "https://danceconvention.net/eventdirector/en/eventpage/1546230/info",
        "https://web.archive.org/web/20190719204919id_/https://danceconvention.net/eventdirector/en/eventpage/1546230/results",
    ],
)
def test_malformed_non_results_and_replay_contexts_reject(url):
    with pytest.raises(ParseError):
        PAGE.parse(PAGE.extract(BODY), replace(CONTEXT, url=url))


@pytest.mark.parametrize(
    "change",
    json.loads((FIXTURES / "results-changed-controls.json").read_bytes())["changes"],
    ids=lambda change: change["field"],
)
def test_changed_consumed_field_changes_extract_and_observation(change):
    changed = BODY.replace(change["old"].encode(), change["new"].encode())
    assert changed != BODY and PAGE.extract(changed) != PAGE.extract(BODY)
    assert parse(changed).observations != parse().observations
    assert not parse(changed).watches


@pytest.mark.parametrize(
    "old,new",
    [
        (b"<legend>Prelims - leaders</legend>", b"<legend>Prelims - unknown</legend>"),
        (b"<legend>Prelims - followers</legend>", b"<legend>Prelims - leaders</legend>"),
        (b"<legend>Finals</legend>", b"<legend>Finals</legend><legend>Other</legend>"),
        (b">Placement</th>", b">Score</th>"),
        (b'<th class="col-md-1">Bib', b'<th colspan="2" class="col-md-1">Bib'),
        (b">391</div>", b">485</div>"),
        (b"<strong>11-14</strong>", b"<strong>14-11</strong>"),
        (b"<strong>11-14</strong>", b"<strong>promoted</strong>"),
        (b"\n- Arnita Veidemane", b" / Arnita Veidemane"),
        (b"Kamila Oczkowska-Luczkos\n", b"Kamila Oczkowska-Luczkos\n- Unreviewed partner\n"),
        (b"roundscores/3451331.pdf", b"roundscores/3451330.pdf"),
        (b"roundscores/3451330.pdf", b"roundscores/3451330.pdf?next=1"),
        (
            b'href="/eventdirector/en/roundscores/3451330.pdf"',
            b'href="https://evil.test/3451330.pdf"',
        ),
        (b"t:ac=1546230-riga-summer-swing-2018/results", b"t:ac=999-other/results"),
        (b"selectresultscontestrow/2196607", b"selectresultscontestrow/2196606"),
        (
            b"<h3 style=\"margin: 0px;\">Jack'n'Jill Newcomer",
            b'<h3 style="margin: 0px;">Unlisted contest',
        ),
        (
            b'content="https://danceconvention.net/eventdirector/en/eventpage/1546230" property="og:url"',
            b'content="https://danceconvention.net/eventdirector/en/eventpage/999" property="og:url"',
        ),
        (b"</body>", b"<table><tr><td>unowned results</td></tr></table></body>"),
    ],
)
def test_changed_structure_and_ownership_fail_closed(old, new):
    changed = BODY.replace(old, new)
    assert changed != BODY
    with pytest.raises(ExtractError):
        PAGE.extract(changed)


def test_empty_observed_tables_keep_unknown_population_instead_of_no_event():
    tree = HTMLParser(BODY)
    for table in tree.css("table"):
        for row in table.css("tr")[1:]:
            row.decompose()
    sheet = parse(tree.html.encode()).observations[0].payload
    assert [len(table.rows) for table in sheet.tables] == [0, 0, 0]
    assert sheet.event_results_completeness == "unknown" and len(sheet.contest_locators) == 8


def test_reparented_legend_is_owned_by_its_exact_sibling_container():
    tree = HTMLParser(BODY)
    for table in tree.css("table"):
        assert not table.css("legend") and len(table.parent.css("legend")) == 1
    assert len(PAGE.extract(tree.html.encode())["tables"]) == 3
    altered = tree.html.replace("<legend>Finals</legend>", "").replace(
        '<div id="fb-root">', '<legend>Finals</legend><div id="fb-root">'
    )
    with pytest.raises(ExtractError):
        PAGE.extract(altered.encode())


def test_unrelated_scripts_and_decoration_are_not_executed_or_consumed():
    assert PAGE.extract(BODY) == PAGE.extract(
        b"<!-- decoration --><script>throw new Error('not executed')</script>" + BODY
    )


def test_body_and_row_bounds_and_invalid_encoding():
    for body in (b"x" * (MAX_BODY_BYTES + 1), b"\xff"):
        with pytest.raises(ExtractError):
            PAGE.extract(body)
    tree = HTMLParser(BODY)
    row = tree.css("table")[0].css("tr")[1].html
    assert row is not None
    changed = BODY.replace(row.encode(), row.encode() * 2001)
    # Parsing/serialization may alter exact bytes; replace through the DOM when
    # the original row rendering does not occur byte-for-byte in source HTML.
    if changed == BODY:
        changed = tree.html.replace(row, row * 2001).encode()
    with pytest.raises(ExtractError, match="row count"):
        PAGE.extract(changed)
