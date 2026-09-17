"""Exact admission contracts for the retained Step Right source bodies."""

import copy
import json
from pathlib import Path

import pytest

from swingset.admission.contracts import contract_version, inspect
from swingset.sources.base import ParseContext, ParseError
from swingset.sources.steprightsolutions import EventPage, IndexPage, RoundPage

FIXTURES = Path(__file__).parent / "fixtures/sources/steprightsolutions"
PROVENANCE = {
    row["id"]: row for row in json.loads((FIXTURES / "real-provenance-20260917.json").read_bytes())
}


def parsed(identifier, page):
    record = PROVENANCE[identifier]
    body = (FIXTURES / record["file"]).read_bytes()
    context = ParseContext(
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
    extract = page.extract(body)
    result = page.parse(extract, context)
    return body, context, extract, result


@pytest.mark.parametrize(
    ("identifier", "page", "count"),
    [
        ("srs-index", IndexPage(), 22),
        ("srs-event", EventPage(), 1),
        ("srs-event2015", EventPage(), 12),
        ("srs-round507", RoundPage(), 25),
        ("srs-round508", RoundPage(), 8),
    ],
)
def test_real_step_right_contracts_stage_without_removal_authority(identifier, page, count):
    body, context, extract, result = parsed(identifier, page)
    declared = result.interpretation
    assert declared is not None
    assert declared.contract_version == contract_version(page.kind) == "1"
    assert declared.state == "staged" and declared.failures == ()
    assert declared.proposed_removal == "none"
    assert (declared.coverage.source_count, declared.coverage.interpreted_count) == (count, count)

    corroborated = inspect(context, body, extract, result)
    assert corroborated.state == "staged"
    assert corroborated.proposed_removal == "none"


def test_index_contract_accounts_for_owned_rows_and_external_event_sites():
    _, _, extract, result = parsed("srs-index", IndexPage())
    witness = extract["contract_witness"]
    assert witness["event_block_count"] == 10
    assert witness["date_anchor_count"] == 32
    assert len(extract["rows"]) == 22
    assert len(witness["external_event_sites"]) == 10
    assert {row["label"] for row in witness["external_event_sites"]} == {"Event Site"}
    report = result.interpretation
    assert report is not None
    exclusions = [field for field in report.fields if field.disposition == "excluded"]
    assert len([field for field in exclusions if "external_event_sites" in field.path]) == 10


def test_index_contract_blocks_a_changed_raw_anchor_witness():
    _, context, extract, _ = parsed("srs-index", IndexPage())
    changed = copy.deepcopy(extract)
    changed["contract_witness"]["date_anchor_count"] = 31
    result = IndexPage().parse(changed, context)
    assert result.interpretation is not None
    assert "stepright_index_anchor_count" in result.interpretation.failures


def test_metadata_only_event_has_no_enumeration_or_removal_claim():
    _, _, extract, result = parsed("srs-event", EventPage())
    assert extract["links"] == []
    assert extract["contract_witness"] == {
        "main_panel_count": 0,
        "contest_count": 0,
        "main_round_link_count": 0,
        "main_unique_round_link_count": 0,
        "sidebar_duplicate_count": 0,
        "sidebar_round_links_match_main": True,
        "unique_round_links_outside_main": [],
    }
    report = result.interpretation
    assert report is not None
    assert report.coverage.listed_children == report.coverage.interpreted_children == ()
    assert any(
        field.path == "round_enumeration" and field.disposition == "excluded"
        for field in report.fields
    )


def test_2015_event_contract_excludes_sidebar_duplicates_and_blocks_new_outside_link():
    body, context, extract, result = parsed("srs-event2015", EventPage())
    witness = extract["contract_witness"]
    assert witness == {
        "main_panel_count": 1,
        "contest_count": 6,
        "main_round_link_count": 12,
        "main_unique_round_link_count": 12,
        "sidebar_duplicate_count": 12,
        "sidebar_round_links_match_main": True,
        "unique_round_links_outside_main": [],
    }
    assert result.interpretation is not None and result.interpretation.state == "staged"

    changed = body.replace(
        b"/events/asianopen2015/round/1126",
        b"/events/asianopen2015/round/9999",
        1,
    )
    changed_extract = EventPage().extract(changed)
    changed_result = EventPage().parse(changed_extract, context)
    assert changed_result.interpretation is not None
    assert "stepright_event_outside_main" in changed_result.interpretation.failures


def test_round_contract_blocks_missing_bib_row_and_unknown_callback_subvalue():
    _, context, extract, _ = parsed("srs-round507", RoundPage())
    missing = copy.deepcopy(extract)
    missing["tables"][0]["rows"].pop()
    missing_result = RoundPage().parse(missing, context)
    assert missing_result.interpretation is not None
    assert "stepright_round_bib_count" in missing_result.interpretation.failures

    unknown = copy.deepcopy(extract)
    unknown["tables"][0]["rows"][1][2]["text"] = "2.1"
    unknown_result = RoundPage().parse(unknown, context)
    assert unknown_result.interpretation is not None
    assert "stepright_round_callback_values" in unknown_result.interpretation.failures
    assert "critical_unknown" in unknown_result.interpretation.failures


def test_final_contract_requires_exact_placement_sequence_and_group_layout():
    _, context, extract, _ = parsed("srs-round508", RoundPage())
    changed = copy.deepcopy(extract)
    changed["tables"][0]["rows"][1][-1]["text"] = "9th"
    result = RoundPage().parse(changed, context)
    assert result.interpretation is not None
    assert "stepright_round_final_placement" in result.interpretation.failures

    changed = copy.deepcopy(extract)
    changed["tables"][0]["rows"][0][3]["attributes"]["colspan"] = "4"
    with pytest.raises(ParseError, match="merged or uneven"):
        RoundPage().parse(changed, context)
