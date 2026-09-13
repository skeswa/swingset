"""WP16 never turns missing CDX rows or a transport failure into origin permission."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from swingset.fetch.wayback import Capture
from swingset.history.captures import CaptureState
from swingset.history.origin import ArchiveSearch, propose_origin
from swingset.history.platform import KnownEvent, Page

NOW = datetime(2026, 9, 13, tzinfo=UTC)
EVENT = KnownEvent("event", "eepro", "eepro:example2020", 2020, "2020-08", date(2020, 8, 30))
PAGE = Page(
    "eepro",
    EVENT.source_ref,
    "https://eepro.com/results/example2020/finals.html",
    "eepro.round",
    "round",
)


def searches(source="eepro", prefix="eepro.com/results/*"):
    return tuple(
        ArchiveSearch(f"query-{year}", source, prefix, year, 2, 2, NOW.isoformat())
        for year in range(2010, 2027)
    )


def plan(*, event=EVENT, page=PAGE, **kwargs):
    return propose_origin(
        event,
        page,
        captures=kwargs.pop("captures", ()),
        outcomes=kwargs.pop("outcomes", {}),
        searches=kwargs.pop("searches", searches()),
        now=NOW,
        gate_reason=kwargs.pop("gate_reason", None),
        **kwargs,
    )


def test_absence_requires_complete_scoped_index_proof_for_every_capture_year():
    assert plan(searches=()).reason == "archive_search_incomplete"
    assert plan(searches=searches()[:-1]).reason == "archive_search_incomplete"
    result = plan()
    assert result.eligible and result.reason == "archive_absence_proven"
    assert len(result.archive_query_ids) == 17
    assert result.attempted_capture_urls == ()
    assert result.max_events_per_cycle == 1


@pytest.mark.parametrize(
    "change",
    [
        {"next_page": 1},
        {"total_pages": None},
        {"completed_at": None},
        {"completed_at": "2026-09-13"},
        {"completed_at": (NOW + timedelta(days=1)).isoformat()},
        {"completed_at": (NOW - timedelta(days=91)).isoformat()},
        {"source": "other"},
        {"prefix": "eepro.com/results/unrelated/*"},
        {"prefix": "eepro.com/results/example2020/"},
    ],
)
def test_partial_stale_or_unrelated_search_never_proves_absence(change):
    queries = searches()
    result = plan(searches=(*queries[:-1], replace(queries[-1], **change)))
    assert not result.eligible and result.reason == "archive_search_incomplete"


def test_old_capture_year_search_does_not_expire_but_previous_year_does():
    old = (NOW - timedelta(days=500)).isoformat()
    queries = tuple(
        replace(query, completed_at=old) if query.year < 2025 else query for query in searches()
    )
    assert plan(searches=queries).eligible
    queries = tuple(
        replace(query, completed_at=old) if query.year == 2025 else query for query in queries
    )
    assert not plan(searches=queries).eligible


def captures():
    return tuple(
        Capture(PAGE.url, f"202{index}1001000000", f"digest-{index}", "text/html", 100)
        for index in range(1, 5)
    )


def test_three_distinct_failed_interpretations_allow_documented_fallback():
    bodies = captures()
    failed = {
        capture.archive_url: CaptureState(
            "incomplete", "round_table_empty", f"snapshot-{capture.digest}"
        )
        for capture in bodies[1:]
    }
    result = plan(captures=bodies, outcomes=failed)
    assert result.eligible and result.reason == "archive_alternatives_unusable"
    assert len(result.attempted_capture_urls) == 3
    # An independently retained usable older capture always wins over origin.
    failed[bodies[0].archive_url] = CaptureState("complete", "admitted_complete_capture", "older")
    assert plan(captures=bodies, outcomes=failed).reason == "usable_archive_copy"


@pytest.mark.parametrize("state", ["unfetched", "waiting", "retryable"])
def test_unfetched_pending_or_transport_failure_does_not_exhaust_archive(state):
    body = captures()[-1]
    result = plan(captures=(body,), outcomes={body.archive_url: CaptureState(state, "diagnostic")})
    assert not result.eligible and result.reason == "archive_acquisition_or_interpretation_pending"


def test_duplicate_digest_is_one_alternative_and_zero_page_query_is_valid():
    body = captures()[-1]
    duplicate = replace(body, timestamp="20241002000000")
    failed = {
        capture.archive_url: CaptureState("incomplete", "historical_results_empty")
        for capture in (body, duplicate)
    }
    result = plan(
        captures=(body, duplicate),
        outcomes=failed,
        searches=tuple(replace(q, next_page=0, total_pages=0) for q in searches()),
    )
    assert result.eligible and len(result.attempted_capture_urls) == 1


@pytest.mark.parametrize(
    "reason",
    ["history_year_unaccepted", "history_contract_not_enforced", "history_contract_review_stale"],
)
def test_proposal_preserves_exact_acquisition_gate(reason):
    assert plan(gate_reason=reason).reason == reason
    assert not plan(gate_reason=reason).eligible


def test_operator_pre2018_slug_is_required_and_no_2009_event_proposed():
    older = replace(EVENT, year=2017, event_month="2017-08", end_date=date(2017, 8, 30))
    assert plan(event=older).reason == "eepro_operator_slug_required"
    assert plan(event=older, operator_pre2018_refs=frozenset({older.source_ref})).eligible
    excluded = replace(older, year=2009, event_month="2009-08", end_date=date(2009, 8, 30))
    assert (
        plan(event=excluded, operator_pre2018_refs=frozenset({older.source_ref})).reason
        == "history_out_of_scope"
    )


def test_dcn_and_scoringdance_use_documented_cadences_without_creating_controls():
    for source, host in (("dcn", "danceconvention.net"), ("scoringdance", "scoring.dance")):
        event = replace(EVENT, source=source, source_ref=f"{source}:123")
        page = Page(
            source, event.source_ref, f"https://{host}/known/result", f"{source}.event", "event"
        )
        result = plan(event=event, page=page, searches=searches(source, f"{host}/*"))
        assert result.eligible and result.max_events_per_cycle == 1
        assert result.max_events_per_day == (1 if source == "dcn" else None)


def test_http_spelling_of_retained_https_resource_cannot_manufacture_origin_gap():
    body = replace(captures()[-1], url=PAGE.url.replace("https://", "http://"))
    assert plan(captures=(body,)).reason == "archive_acquisition_or_interpretation_pending"
    assert (
        plan(
            captures=(body,), outcomes={body.archive_url: CaptureState("complete", "admitted")}
        ).reason
        == "usable_archive_copy"
    )


def test_incomplete_archive_query_is_not_a_negative_cache_entry_even_after_bad_body():
    body = captures()[-1]
    result = plan(
        captures=(body,),
        outcomes={body.archive_url: CaptureState("incomplete", "parse_failed")},
        searches=(),
    )
    assert not result.eligible and result.reason == "archive_search_incomplete"


@pytest.mark.parametrize("port", [":bad", ":444", ":80"])
def test_origin_locator_rejects_malformed_or_nonstandard_https_port(port):
    assert (
        plan(page=replace(PAGE, url=PAGE.url.replace("eepro.com", "eepro.com" + port))).reason
        == "origin_locator_invalid"
    )


def test_malformed_retained_capture_alias_does_not_crash_planning():
    malformed = Capture(
        PAGE.url.replace("eepro.com", "eepro.com:bad"), "20220101000000", "bad", "text/html", 1
    )
    assert plan(captures=(malformed,)).eligible


def test_malformed_origin_brackets_fail_as_invalid_locator():
    assert (
        plan(page=replace(PAGE, url="https://[broken/results/finals.html")).reason
        == "origin_locator_invalid"
    )
