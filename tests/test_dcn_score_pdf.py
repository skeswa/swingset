"""Real-control tests for the narrowly bound DCN Riga score PDFs."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from swingset.model.observations import decode_payload, encode_payload
from swingset.sources.base import ExtractError, ParseContext, ParseError
from swingset.sources.dcn import SOURCE, ScorePdfPage
from swingset.sources.dcn.legacy_results import LegacyResultsPage

FIXTURES = Path(__file__).parent / "fixtures/sources/dcn"
PDF = ScorePdfPage()
EVENT = "dcn:1546230"
FINALS_URL = "https://danceconvention.net/eventdirector/en/roundscores/3451330.pdf"
PRELIMS_URL = "https://danceconvention.net/eventdirector/en/roundscores/3451331.pdf"
RESULTS_URL = (
    "https://danceconvention.net/eventdirector/en/eventpage/1546230-riga-summer-swing/results"
)


def context(url: str, *, source: str = "dcn", kind: str = "dcn.round_pdf", ref: str = EVENT):
    return ParseContext("snapshot", "watch", url, source, kind, ref, "2026-09-17T00:00:00+00:00")


def pdf_result(name: str, url: str):
    body = (FIXTURES / name).read_bytes()
    extracted = PDF.extract(body)
    return body, extracted, PDF.parse(extracted, context(url))


def test_fixtures_match_retained_origin_receipt_and_provenance():
    root = Path(__file__).parents[1]
    provenance_path = FIXTURES / "score-pdf-provenance-20260917.json"
    provenance = json.loads(provenance_path.read_bytes())
    receipt = root / provenance["source_receipt"]
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == provenance["source_receipt_sha256"]
    assert provenance["source_event_ref"] == EVENT
    for key, url in (("finals", FINALS_URL), ("prelims", PRELIMS_URL)):
        details = provenance["fixtures"][key]
        body = (FIXTURES / details["file"]).read_bytes()
        assert details["url"] == url
        assert len(body) == details["body_bytes"]
        assert hashlib.sha256(body).hexdigest() == details["body_sha256"]


def test_real_finals_preserve_page_local_printed_facts_and_pair_ownership():
    body, _extracted, result = pdf_result("real-riga-finals-3451330-20260917.pdf", FINALS_URL)
    assert len(result.observations) == 1
    observation = result.observations[0]
    page = observation.payload
    assert (
        page.source_body_sha256
        == "b91e9e0a82edbd455b21b5aad50f211c590a05afd9a8bb34c81493d134e70e5e"
    )
    assert page.source_body_sha256 == __import__("hashlib").sha256(body).hexdigest()
    assert page.source_url == FINALS_URL and page.source_round_id == "3451330"
    assert page.page_number == 1 and page.heading_raw == "Jack'n'Jill Newcomer - Finals"
    assert page.event_name_raw == "Riga Summer Swing" and page.role is None
    assert [judge.code_raw for judge in page.judges] == ["OD", "SK", "DK", "OM", "MM", "MS", "HT"]
    assert [judge.name_raw for judge in page.judges] == [
        "Olivier Deprez",
        "Sergey Khakhlev",
        "Daria Komkina",
        "Olga Malafeevskaya",
        "Miquel Menendez",
        "Marcin Skalski",
        "Hanna Tuominen",
    ]
    assert page.column_headers_raw[-11:] == (
        "1-1",
        "1-2",
        "1-3",
        "1-4",
        "1-5",
        "1-6",
        "1-7",
        "1-8",
        "1-9",
        "Result",
        "Remarks",
    )
    assert len(page.rows) == 9 and {row.result_raw for row in page.rows} == set("123456789")
    row = next(row for row in page.rows if row.bib_raw == "485")
    assert row.member_names_raw == ("Lilio Montel", "Julija Losane")
    assert row.role is None and row.bib_ownership == "pair_unassigned"
    assert row.promotion == "unknown" and row.result_raw == "2" and row.remarks_raw == ""
    assert tuple(value.value_raw for value in row.judge_values) == (
        "1",
        "2",
        "4",
        "5",
        "2",
        "2",
        "3",
    )
    assert tuple(value.value_raw for value in row.numbered_values) == (
        "1",
        "4 (7)",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
    )
    assert "DanceConvention.net" in page.page_text_raw
    restored = decode_payload(observation.kind, encode_payload(page))
    assert restored == page


def test_real_prelims_keep_role_panels_legacy_marks_and_filtered_population():
    _body, _extracted, result = pdf_result("real-riga-prelims-3451331-20260917.pdf", PRELIMS_URL)
    assert len(result.observations) == 2
    leaders, followers = (observation.payload for observation in result.observations)
    assert (leaders.role, followers.role) == ("leader", "follower")
    assert (leaders.page_number, followers.page_number) == (1, 2)
    assert leaders.source_body_sha256 == followers.source_body_sha256
    assert leaders.source_round_id == followers.source_round_id == "3451331"
    assert leaders.population_completeness == followers.population_completeness == "filtered_subset"
    assert leaders.disclaimer_raw == followers.disclaimer_raw
    assert leaders.disclaimer_raw == (
        "Dancers without any Yes or Alternate marks are omitted from this list. "
        "You may check your invididual results on your danceConvention.net account page."
    )
    assert [judge.code_raw for judge in leaders.judges] == ["SK", "MM", "LT", "AV", "CHB"]
    assert [judge.code_raw for judge in followers.judges] == ["OM", "ATP", "MS", "HT", "CHB"]
    assert leaders.judges[-1].name_raw == followers.judges[-1].name_raw == "Chuck Brown"
    assert len(leaders.rows) == 9 and len(followers.rows) == 16
    assert all(row.role == "leader" and len(row.member_names_raw) == 1 for row in leaders.rows)
    assert all(row.role == "follower" and len(row.member_names_raw) == 1 for row in followers.rows)
    leader_marks = {mark.value_raw for row in leaders.rows for mark in row.judge_values}
    follower_marks = {mark.value_raw for row in followers.rows for mark in row.judge_values}
    assert leader_marks == {"1", "2.1", "2.2"}
    assert follower_marks == {"1", "2.1", "2.2", "3"}
    assert {mark.mark_kind for row in followers.rows for mark in row.judge_values} == {
        "yes",
        "alternate",
        "no",
    }
    assert {mark.alternate_rank_raw for row in followers.rows for mark in row.judge_values} == {
        None,
        "1",
        "2",
    }
    assert {row.result_raw for row in followers.rows} == {"Callback", "Alternate1", "-"}
    assert sum(row.result_raw == "Callback" for row in followers.rows) == 9
    assert sum(row.result_raw == "Alternate1" for row in followers.rows) == 1
    assert sum(row.result_raw == "-" for row in followers.rows) == 6
    for observation in result.observations:
        assert (
            decode_payload(observation.kind, encode_payload(observation.payload))
            == observation.payload
        )


def test_pdf_html_bib_485_disagreement_stays_as_two_raw_source_facts():
    _body, _extracted, pdf = pdf_result("real-riga-finals-3451330-20260917.pdf", FINALS_URL)
    page = pdf.observations[0].payload
    pdf_names = next(row.member_names_raw for row in page.rows if row.bib_raw == "485")

    html_parser = LegacyResultsPage()
    html = (FIXTURES / "real-riga-results-20190719204919.html").read_bytes()
    context_html = context(RESULTS_URL, kind="dcn.legacy_results")
    html_result = html_parser.parse(html_parser.extract(html), context_html)
    final_table = html_result.observations[0].payload.tables[0]
    html_names = next(row.member_names_raw for row in final_table.rows if row.bib_raw == "485")
    assert pdf_names == ("Lilio Montel", "Julija Losane")
    assert html_names == ("Lucien Blaise", "Julija Losane")
    assert pdf_names != html_names
    assert next(row for row in page.rows if row.bib_raw == "485").bib_ownership == "pair_unassigned"


@pytest.mark.parametrize(
    ("url", "source", "kind", "ref"),
    [
        (FINALS_URL + "?download=1", "dcn", "dcn.round_pdf", EVENT),
        (FINALS_URL.replace("danceconvention.net", "other.example"), "dcn", "dcn.round_pdf", EVENT),
        (FINALS_URL, "dcn", "dcn.round_pdf", "dcn:999"),
        (FINALS_URL, "other", "dcn.round_pdf", EVENT),
        (FINALS_URL, "dcn", "dcn.legacy_results", EVENT),
        (PRELIMS_URL, "dcn", "dcn.round_pdf", EVENT),
    ],
)
def test_pdf_parser_rejects_context_and_round_url_mismatches(url, source, kind, ref):
    body = (FIXTURES / "real-riga-finals-3451330-20260917.pdf").read_bytes()
    with pytest.raises(ParseError):
        PDF.parse(PDF.extract(body), context(url, source=source, kind=kind, ref=ref))


def test_pdf_extraction_rejects_truncation_non_pdf_and_oversized_input():
    body = (FIXTURES / "real-riga-finals-3451330-20260917.pdf").read_bytes()
    with pytest.raises(ExtractError):
        PDF.extract(b"not a PDF")
    with pytest.raises(ExtractError):
        PDF.extract(body[:128])
    with pytest.raises(ExtractError, match="byte bound"):
        PDF.extract(b"%PDF-" + b"x" * (256 * 1024))


def test_pdf_parser_fails_closed_on_changed_heading_columns_rows_and_marks():
    finals = PDF.extract((FIXTURES / "real-riga-finals-3451330-20260917.pdf").read_bytes())
    changed = copy.deepcopy(finals)
    changed["pages"][0]["text"] = changed["pages"][0]["text"].replace(
        "Jack'n'Jill Newcomer - Finals", "Other Contest - Finals", 1
    )
    with pytest.raises(ParseError, match="heading"):
        PDF.parse(changed, context(FINALS_URL))

    changed = copy.deepcopy(finals)
    changed["pages"][0]["text"] = changed["pages"][0]["text"].replace(
        "# Name OD SK DK OM MM MS HT", "# Name OD OD DK OM MM MS HT", 1
    )
    with pytest.raises(ParseError, match="columns"):
        PDF.parse(changed, context(FINALS_URL))

    changed = copy.deepcopy(finals)
    changed["pages"][0]["text"] = changed["pages"][0]["text"].replace(
        "Score legend: placement from 1 to 9",
        "Score legend: placement from 1 to 9\nUNREVIEWED NEW TABLE QUALIFICATION",
        1,
    )
    with pytest.raises(ParseError, match="between score legend"):
        PDF.parse(changed, context(FINALS_URL))

    changed = copy.deepcopy(finals)
    changed["pages"][0]["text"] = changed["pages"][0]["text"].replace(
        "485 Lilio Montel", "391 Duplicate Bib", 1
    )
    with pytest.raises(ParseError, match="repeated"):
        PDF.parse(changed, context(FINALS_URL))

    changed = copy.deepcopy(finals)
    changed["pages"][0]["text"] = changed["pages"][0]["text"].replace("Arnita Veidemane 4 5", "", 1)
    with pytest.raises(ParseError, match="second printed name"):
        PDF.parse(changed, context(FINALS_URL))

    changed = copy.deepcopy(finals)
    changed["pages"][0]["text"] = changed["pages"][0]["text"].replace(
        "Arnita Veidemane 4 5", "Arnita Veidemane 4 x", 1
    )
    with pytest.raises(ParseError):
        PDF.parse(changed, context(FINALS_URL))

    prelims = PDF.extract((FIXTURES / "real-riga-prelims-3451331-20260917.pdf").read_bytes())
    changed = copy.deepcopy(prelims)
    changed["pages"][0]["text"] = changed["pages"][0]["text"].replace(
        "Score legend: 1 = YES, 2 = ALT, 3 = NO; additional ranking may be provided for ALT",
        "Score legend: 1 = YES, 2 = ALT, 3 = NO; additional ranking may be provided for ALT"
        "\nUNREVIEWED NEW TABLE QUALIFICATION",
        1,
    )
    with pytest.raises(ParseError, match="between score legend"):
        PDF.parse(changed, context(PRELIMS_URL))

    changed = copy.deepcopy(prelims)
    changed["pages"][0]["text"] = changed["pages"][0]["text"].replace("2.1", "2.3", 1)
    with pytest.raises(ParseError, match="mark"):
        PDF.parse(changed, context(PRELIMS_URL))

    changed = copy.deepcopy(prelims)
    changed["pages"].pop()
    with pytest.raises(ParseError, match="page count"):
        PDF.parse(changed, context(PRELIMS_URL))


def test_dcn_registry_knows_pdf_parser_without_seed_watches_or_activation():
    assert "dcn.round_pdf" in SOURCE.page_kinds
    assert SOURCE.seed_watches(object(), object()) == []
    assert SOURCE.page_kinds["dcn.round_pdf"] is not None
