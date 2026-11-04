"""Historical SQLite upgrades run against a checksummed, test-only source fixture.

Every archived case keeps its original assertions. Contemporary runtime paths
are exercised against Postgres by the ordinary state, retention and backup tests.
"""


def test_plan_apply_and_reclaim_run_on_a_database_without_interning(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_uninterned_schema.py",
        "test_plan_apply_and_reclaim_run_on_a_database_without_interning",
    )


def test_holds_and_doctor_run_on_a_database_without_interning(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_uninterned_schema.py", "test_holds_and_doctor_run_on_a_database_without_interning"
    )


def test_a_checkpoint_of_a_database_without_interning_verifies_and_restores(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_uninterned_schema.py",
        "test_a_checkpoint_of_a_database_without_interning_verifies_and_restores",
    )
