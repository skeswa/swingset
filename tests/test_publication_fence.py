"""The narrow publication fence (plan phase 3, WP10).

A partitioned candidate's publication is admitted as `publication_narrow`.
Its fence covers only what `publish/safety` reads for such a candidate, so
the loops write through it; a closure-mode publication keeps the full fence.
"""

import json
import shutil
import sqlite3

import pytest

from swingset.build.service import build_release
from swingset.clock import FakeClock
from swingset.publish import safety
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture
from swingset.state.publication_fence import (
    NARROW,
    NARROW_KIND,
    admission_kind,
    expected_narrow,
    narrow_triggers,
)
from swingset.versions import versions

FENCED = "active publication fences"

#: The narrow fence, pinned. A change here is a change to what a partitioned
#: publication's check reads (`publish/safety._validate_partitioned`).
PINNED = {
    "admission_policies": ["delete", "insert", "update"],
    "derivation_generations": ["delete", "insert", "update"],
    "derivation_evicted": ["delete", "insert", "update"],
    "derivation_payloads": ["delete"],
    "derivation_row_refs": ["delete", "insert", "update"],
    "derivation_scopes": ["delete", "insert", "update"],
    "identity_decisions": ["delete", "insert", "update"],
    "identity_journal_acceptances": ["delete", "insert", "update"],
    "label_body_packs": ["delete", "insert", "update"],
    "meta": ["delete", "insert", "update"],
    "objects": ["delete", "insert", "update"],
    "release_artifacts": ["delete", "insert", "update"],
    "release_manifests": ["delete", "insert", "update"],
    "release_partitions": ["delete", "insert", "update"],
    "revisions": ["delete", "insert", "update"],
    "source_generation_packs": ["delete", "insert", "update"],
    "source_generations": ["delete", "insert", "update"],
}


@pytest.fixture
def partitioned(tmp_path, monkeypatch):
    """A built partitioned candidate and its state."""
    monkeypatch.setenv("SWINGSET_REVISION", "narrow-fence-fixture")
    config, overrides = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config)
    overrides.mkdir()
    (overrides / "identity_overrides.csv").write_text(
        "decision_id,source,source_event,contest,round,participant,wsdc_id,decision,evidence,reason,author,date,supersedes\n"
    )
    (overrides / "suppressions.csv").write_text("wsdc_id,name_norm,reason,date\n")
    clock = FakeClock()
    with open_database(tmp_path / "state") as database:
        bundle = capture(config, overrides, database.state_dir, versions())
        accept(database, bundle, clock)
        database.connection.execute("DELETE FROM pending_work")
        run_id = database.start_run(clock.now())
        candidate = build_release(database, bundle, clock, run_id, None)
        yield database, candidate.path, clock


def _admit(conn: sqlite3.Connection, kind: str, action_id: str = "publication_x") -> None:
    conn.execute(
        "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,"
        "all_sources,all_kinds,state) VALUES (?,?,'2026-01-01T00:00:00+00:00',0,1,1,'active')",
        (action_id, kind),
    )


def test_the_narrow_fence_is_pinned(tmp_path):
    with open_database(tmp_path / "state") as database:
        conn = database.connection
        assert narrow_triggers(conn) == PINNED == expected_narrow(conn)
        assert admission_kind(conn, narrow=True) == NARROW_KIND
        assert admission_kind(conn, narrow=False) == "publication"


def test_the_fence_covers_every_table_a_partitioned_check_reads(partitioned):
    """The list derives from the check: run it under an authorizer on a fresh
    connection (no cached statement hides a read) and compare (plan item 10)."""
    database, candidate, _clock = partitioned
    built = json.loads((candidate / "BUILT").read_bytes())
    manifest = json.loads((candidate / "_meta/manifest.json").read_bytes())
    assert manifest["release_policy"]["mode"] == "partitioned"
    read: set[str] = set()

    def authorize(action, table, _column, _db, _trigger):
        if action == sqlite3.SQLITE_READ and table:
            read.add(table)
        return sqlite3.SQLITE_OK

    conn = sqlite3.connect(database.state_dir / "state.sqlite")
    conn.row_factory = sqlite3.Row
    try:
        conn.set_authorizer(authorize)
        safety._validate_candidate(conn, database.state_dir, candidate, built, manifest)
    finally:
        conn.close()
    # `derivation_rows` is a view over row refs and payloads (migration 32).
    assert read - {"sqlite_master", "derivation_rows"} <= set(NARROW), read
    assert {"meta", "revisions", "admission_policies", "source_generations"} <= read


def _seed(conn: sqlite3.Connection) -> None:
    """Rows the statements below change, written before the publication."""
    conn.execute(
        "INSERT INTO admission_policies(page_kind,contract_version,mode,policy_revision) "
        "VALUES ('seeded','1','shadow','seeded')"
    )


FENCED_WRITES = [
    "UPDATE meta SET value='changed' WHERE key='input_bundle_hash'",
    "UPDATE meta SET value='changed' WHERE key='identity_journal_digest'",
    "DELETE FROM meta WHERE key='input_bundle_hash'",
    "UPDATE revisions SET value=value+1 WHERE name='identity_decisions'",
    "INSERT INTO identity_decisions VALUES ('d','eepro','event','contest','round','p','42',"
    "'same_person','evidence','reason','author','2026-09-13',NULL,'{}','sha','digest','now')",
    "INSERT INTO identity_journal_acceptances VALUES ('new','bundle','now',0,9)",
    "INSERT INTO admission_policies(page_kind,contract_version,mode,policy_revision) "
    "VALUES ('fixture','1','enforce','changed')",
    "UPDATE admission_policies SET mode='paused' WHERE page_kind='seeded'",
    "DELETE FROM admission_policies WHERE page_kind='seeded'",
    # A revocation, whatever its unit: the trigger runs before any key check.
    "INSERT INTO source_generations VALUES ('g','u','k','1','f','{}','{}','{}','{}',NULL,"
    "NULL,'now','r','revoked','none')",
    "UPDATE derivation_scopes SET desired_fingerprint='x' WHERE stage='build'",
    "UPDATE derivation_generations SET row_count=row_count WHERE stage='build'",
    "DELETE FROM derivation_row_refs WHERE generation_id IN "
    "(SELECT generation_id FROM derivation_generations WHERE stage='build')",
    "DELETE FROM derivation_payloads",
    "DELETE FROM release_manifests",
    "UPDATE release_manifests SET verdict_json='{}'",
    "INSERT INTO release_artifacts VALUES ('d','event/x',1,'now','now')",
    "INSERT INTO release_partitions VALUES ('event/x','i',NULL,'withheld','r',NULL,'now','run')",
]
#: Writes the loops make that a narrow publication lets through.
LOOP_WRITES = [
    "INSERT INTO pending_work VALUES ('parse','snapshot','x','2026-09-13')",
    "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_narrow','now',1)",
    "UPDATE revisions SET value=value+1 WHERE name<>'identity_decisions'",
    "INSERT INTO meta VALUES ('event_site_review_cursor','x') "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    # An unchanged acceptance: derive re-accepts the same bundle each tick.
    "INSERT INTO meta(key,value) SELECT key,value FROM meta WHERE key='input_bundle_hash' "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    "UPDATE meta SET value=value WHERE key='identity_journal_digest'",
    # Parse upserts the policy row of its kind; present, it changes nothing.
    "INSERT INTO admission_policies(page_kind,contract_version,policy_revision) "
    "VALUES ('seeded','2','shadow:2') ON CONFLICT(page_kind) DO NOTHING",
    "INSERT OR IGNORE INTO identity_journal_acceptances SELECT * FROM identity_journal_acceptances",
    "UPDATE derivation_scopes SET desired_fingerprint='x' WHERE stage<>'build'",
    "INSERT INTO derivation_payloads VALUES ('sha-narrow','{}')",
]


def _each(conn: sqlite3.Connection, statements: list[str], *, fenced: bool) -> None:
    """Run each statement in a savepoint rolled back after it."""
    for statement in statements:
        conn.execute("SAVEPOINT probe")
        try:
            if fenced:
                with pytest.raises(sqlite3.IntegrityError, match=FENCED):
                    conn.execute(statement)
            else:
                conn.execute(statement)
        except BaseException as exc:
            exc.add_note(statement)
            raise
        finally:
            conn.execute("ROLLBACK TO probe")
            conn.execute("RELEASE probe")


def test_a_narrow_publication_fences_what_its_check_reads_and_nothing_else(partitioned):
    database, _candidate, _clock = partitioned
    conn = database.connection
    _seed(conn)
    _admit(conn, NARROW_KIND)
    _each(conn, FENCED_WRITES, fenced=True)
    _each(conn, LOOP_WRITES, fenced=False)


def test_a_full_publication_still_fences_every_table(partitioned):
    database, _candidate, _clock = partitioned
    conn = database.connection
    _admit(conn, "publication")
    for statement in (
        "INSERT INTO pending_work VALUES ('parse','snapshot','x','2026-09-13')",
        "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_full','now',1)",
    ):
        with pytest.raises(sqlite3.IntegrityError, match=FENCED):
            conn.execute(statement)


def test_a_missing_narrow_trigger_takes_the_full_fence(tmp_path):
    with open_database(tmp_path / "state") as database:
        conn = database.connection
        conn.execute('DROP TRIGGER "publication_narrow_fence_meta_update"')
        assert admission_kind(conn, narrow=True) == "publication"


def test_the_boundary_admits_a_partitioned_candidate_narrow_and_one_at_a_time(partitioned):
    database, candidate, _clock = partitioned
    conn = database.connection
    with safety.publication_boundary(database.state_dir, candidate, narrow=True):
        row = conn.execute(
            "SELECT action_kind,state FROM execution_admissions WHERE candidate_id=?",
            (candidate.name,),
        ).fetchone()
        assert tuple(row) == (NARROW_KIND, "active")
        with pytest.raises(safety.PublicationHeldError, match="another publication"):
            with safety.publication_boundary(database.state_dir, candidate, narrow=True):
                pass
    assert (
        conn.execute(
            "SELECT state FROM execution_admissions WHERE candidate_id=?", (candidate.name,)
        ).fetchone()[0]
        == "settled"
    )


def test_recovery_keeps_a_narrow_publication_uncertain(partitioned):
    from datetime import UTC, datetime

    from swingset.state.controls import recover_admissions

    database, _candidate, _clock = partitioned
    with database.transaction() as conn:
        _admit(conn, NARROW_KIND)
        assert recover_admissions(conn, now=datetime.now(UTC)) == 1
    assert database.connection.execute("SELECT state,outcome FROM execution_admissions").fetchone()[
        :
    ] == ("uncertain", "receipt_reconciliation_required")


def test_no_code_replaces_a_narrow_fenced_row():
    """`INSERT OR REPLACE` on a present key fires no insert trigger (the key
    exists) and no delete trigger (recursive triggers are off), so it would
    pass the narrow fence. No writer of a fenced table uses it."""
    import re
    from pathlib import Path

    tables = "|".join(sorted(NARROW))
    pattern = re.compile(rf"REPLACE\s+INTO\s+\"?({tables})\b", re.IGNORECASE)
    found = [
        f"{path}:{match.group(0)}"
        for path in sorted(Path("src/swingset").rglob("*.py"))
        for match in pattern.finditer(path.read_text())
    ]
    assert found == []
