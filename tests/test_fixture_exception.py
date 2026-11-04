"""Historical fixed-manifest fixture-acquisition guarantees, against frozen SQLite.

The original 27 nodes retain their exact authorizer, writer-lock, crash and
shared-budget assertions. Current acquisition and role guards run natively in
test_fetch.py, test_paid_pacing.py, test_controls.py and test_postgres_engine.py.
"""

import pytest


def test_dry_run_does_not_open_state_or_request(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_authorization_and_exact_manifest_fail_closed(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_success_uses_shared_gate_and_preserves_all_production_facts(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


@pytest.mark.parametrize(
    "location",
    [
        "https://steprightsolutions.com/events",
        "http://web.archive.org/robots.txt",
        "https://web.archive.org/web/20130413022752id_/https://unapproved.test/",
        "https://web.archive.org:444/robots.txt",
        "https://user@web.archive.org/robots.txt",
    ],
)
def test_redirect_escape_stops_after_one_charged_request(legacy_sqlite, request, location):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


@pytest.mark.parametrize("status", [429, 503, 500])
def test_failure_never_retries_and_retains_host_pause(legacy_sqlite, request, status):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_shared_pause_and_exhausted_daily_budget_make_no_requests(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_operator_pause_is_not_bypassed(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_byte_ceiling_keeps_prefix_and_never_exceeds_shared_byte_capacity(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_request_ceiling_counts_redirects(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_deadline_prevents_next_read_or_request(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_fresh_robots_is_quarantined_without_dangling_production_blob_reference(
    legacy_sqlite, request
):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_redirect_may_change_capture_but_not_original_resource(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_source_pause_applies_to_fixture_purpose(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_existing_writer_lock_prevents_accounting_access(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_real_process_crash_leaves_conservative_budget_and_no_facts(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_response_ceiling_and_total_ceiling_are_independent(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_aggregate_byte_ceiling_stops_before_cdx_or_extra_body(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_execute_without_authorization_never_opens_state(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_unlisted_initial_archive_resource_is_refused_before_io(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_owner_authorization_persists_when_unstarted_execution_window_moves(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)


def test_fixture_bytes_and_receipt_keep_grant_day_across_dispatch_midnight(legacy_sqlite, request):
    legacy_sqlite.assert_case("test_fixture_exception.py", request.node.name)
