"""Exact H16 SQLite scratch replay research contracts run against frozen source.

Current Postgres replay is covered by test_dev_seed.py.
"""

import pytest


def test_bounded_cohorts_resume_real_generations_without_parse_or_fairness(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_replay_derivations.py",
        "test_bounded_cohorts_resume_real_generations_without_parse_or_fairness",
    )


def test_crash_receipt_and_resume_keep_previous_atomic_completion(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_replay_derivations.py",
        "test_crash_receipt_and_resume_keep_previous_atomic_completion",
    )


@pytest.mark.parametrize(
    "problem",
    [
        "no_marker",
        "hardlink",
        "symlink",
        "wrong_marker",
        "output_symlink",
        "existing_output",
        "checkpoint_target",
        "production_target",
    ],
)
def test_refuses_unbound_or_linked_scratch_and_unsafe_receipts(legacy_sqlite, problem):
    legacy_sqlite.assert_case(
        "test_replay_derivations.py",
        f"test_refuses_unbound_or_linked_scratch_and_unsafe_receipts[{problem}]",
    )


def test_worker_crash_rolls_back_then_uses_standard_interruption_cooldown(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_replay_derivations.py",
        "test_worker_crash_rolls_back_then_uses_standard_interruption_cooldown",
    )


def test_wallclock_reserves_atomic_worker_bound_without_starting_next_unit(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_replay_derivations.py",
        "test_wallclock_reserves_atomic_worker_bound_without_starting_next_unit",
    )


def test_new_scope_discovered_after_earlier_cohort_is_revisited(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_replay_derivations.py", "test_new_scope_discovered_after_earlier_cohort_is_revisited"
    )
