"""Historical SQLite upgrades run against a checksummed, test-only source fixture.

Every archived case keeps its original assertions. Contemporary runtime paths
are exercised against Postgres by the ordinary state, retention and backup tests.
"""


def test_migration_from_36_closes_unit_findings_and_drops_history(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_phase5_migration.py", "test_migration_from_36_closes_unit_findings_and_drops_history"
    )


def test_migration_without_open_unit_findings_keeps_the_revision(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_phase5_migration.py", "test_migration_without_open_unit_findings_keeps_the_revision"
    )


def test_schema_36_code_refuses_a_schema_37_file(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_phase5_migration.py", "test_schema_36_code_refuses_a_schema_37_file"
    )


def test_migration_from_36_deletes_unit_admissions_and_their_scope_rows(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_phase5_migration.py",
        "test_migration_from_36_deletes_unit_admissions_and_their_scope_rows",
    )


def test_the_migrating_connection_keeps_its_secure_delete_setting(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_phase5_migration.py", "test_the_migrating_connection_keeps_its_secure_delete_setting"
    )
