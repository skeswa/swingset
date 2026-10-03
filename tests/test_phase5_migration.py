"""Migration 37: requirement history goes, unit failures leave the review queue,
and leased units lose their admissions.

Part 1 (D-0237) closes every open `work_attempt` requirement as out of scope,
stops its retained support from restoring it, bumps the `findings` revision
once, and drops the four requirement history tables. Part 3 (D-0236) closes
the open `history_event_site_review` findings the same way. The requirement scan
stays (docs/plans/architecture.md, section 9, phase 5). Part 2 (D-0238)
deletes every unit admission (`work_` action ids) and its scope rows.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from swingset.state import db as db_module
from swingset.state.db import open_database
from swingset.state.findings import Finding, replace_findings
from swingset.state.requirements import Requirement, reconcile_requirement, scan

NOW = datetime(2026, 10, 1, tzinfo=UTC)
DROPPED = (
    "requirement_cohort_members",
    "requirement_cohorts",
    "requirement_transitions",
    "requirement_attempts",
)


def unit_requirement(unit_id: str, state: str) -> Requirement:
    return Requirement(
        "work_attempt",
        json.dumps(("parse", "snapshot", unit_id), separators=(",", ":")),
        "eepro",
        state,
        "retry",
        {"stage": "parse", "unit_kind": "snapshot", "unit_id": unit_id},
        None if state == "satisfied" else "parse_failed",
    )


def support(conn, finding_id: str, active: int) -> None:
    row = conn.execute("SELECT * FROM findings WHERE finding_id=?", (finding_id,)).fetchone()
    payload = {key: row[key] for key in row.keys() if key != "finding_id"}
    conn.execute(
        "INSERT INTO finding_support VALUES (?,?,?)",
        (finding_id, json.dumps(payload, sort_keys=True), active),
    )


def seed(conn) -> dict[str, str]:
    conn.execute(
        "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run',?,0)", (NOW.isoformat(),)
    )
    open_unit = reconcile_requirement(conn, unit_requirement("open", "unavailable"), NOW, "run")
    support(conn, open_unit, 1)
    closed_unit = reconcile_requirement(conn, unit_requirement("closed", "satisfied"), NOW, "run")
    support(conn, closed_unit, 0)
    # A unit whose findings row was lost: only its support would restore it.
    lost_unit = reconcile_requirement(conn, unit_requirement("lost", "retry_wait"), NOW, "run")
    support(conn, lost_unit, 1)
    conn.execute("DELETE FROM findings WHERE finding_id=?", (lost_unit,))
    scan_finding = reconcile_requirement(
        conn, Requirement("round_observations", "w1", "eepro", "ready", "fetch", {}), NOW, "run"
    )
    replace_findings(
        conn,
        owner_kind="event",
        owner_id="historical",
        findings=(Finding("unrecognized", "event", "historical", "warning", "Review", {}),),
        opened_at=NOW.isoformat(),
        run_id="run",
    )
    review = conn.execute("SELECT finding_id FROM findings WHERE owner_kind='event'").fetchone()[0]
    # An event-site review the retired stage opened (D-0236): it has no writer left.
    replace_findings(
        conn,
        owner_kind="event_site_backfill",
        owner_id="2010-01-example",
        findings=(
            Finding(
                "history_event_site_review", "event", "2010-01-example", "warning", "Review", {}
            ),
        ),
        opened_at=NOW.isoformat(),
        run_id="run",
    )
    site = conn.execute(
        "SELECT finding_id FROM findings WHERE owner_kind='event_site_backfill'"
    ).fetchone()[0]
    assert (
        conn.execute("SELECT active FROM finding_support WHERE finding_id=?", (site,)).fetchone()[0]
        == 1
    )
    conn.execute(
        "INSERT INTO requirement_attempts VALUES ('work:1',?,?,'unavailable')",
        (open_unit, NOW.isoformat()),
    )
    conn.execute(
        "INSERT INTO requirement_cohorts(cohort_id,created_at,policy_version,source,kind,bounded) "
        "VALUES ('c',?,'1',NULL,NULL,1)",
        (NOW.isoformat(),),
    )
    conn.execute("INSERT INTO requirement_cohort_members VALUES ('c',?)", (open_unit,))
    return {
        "open": open_unit,
        "closed": closed_unit,
        "lost": lost_unit,
        "scan": scan_finding,
        "review": review,
        "site": site,
    }


def rows(conn, table: str, key: str) -> dict[str, dict]:
    return {row[key]: dict(row) for row in conn.execute(f"SELECT * FROM {table}")}


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A phase 4 (schema 36) state with requirement history and unit findings."""
    state = tmp_path / "state"
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 36)
        with open_database(state, lock=False) as database:
            assert database.schema_version == 36
            with database.transaction() as conn:
                ids = seed(conn)
            conn = database.connection
            for table in DROPPED:
                assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0, table
            before = {
                "findings": rows(conn, "findings", "finding_id"),
                "support": rows(conn, "finding_support", "finding_id"),
                "revision": conn.execute(
                    "SELECT value FROM revisions WHERE name='findings'"
                ).fetchone()[0],
            }
    return state, ids, before


def test_migration_from_36_closes_unit_findings_and_drops_history(state) -> None:
    path, ids, before = state
    with open_database(path, lock=False) as migrated:
        conn = migrated.connection
        assert migrated.schema_version == 37
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 37
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in DROPPED:
            assert not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
            ).fetchone(), table
        leftover = conn.execute(
            "SELECT name FROM sqlite_master WHERE sql LIKE '%requirement_transitions%' "
            "OR sql LIKE '%requirement_attempts%' OR sql LIKE '%requirement_cohort%'"
        ).fetchall()
        assert leftover == []
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='requirement_scan'").fetchone()
        after = rows(conn, "findings", "finding_id")
        closed = after[ids["open"]]
        assert closed["state"] == "out_of_scope"
        assert closed["next_action"] == "unit failures live in the work queue"
        assert closed["closed_at"] is not None and closed["status_at"] == closed["closed_at"]
        datetime.fromisoformat(closed["closed_at"])
        # Everything else is untouched, including the already closed unit.
        for key in ("closed", "scan", "review"):
            assert after[ids[key]] == before["findings"][ids[key]], key
        assert set(after) == set(before["findings"])
        site = after[ids["site"]]
        assert (site["state"], site["closed_at"]) == ("out_of_scope", closed["closed_at"])
        assert site["next_action"].startswith("journal/tools/collection/event_site_review.py")
        support = rows(conn, "finding_support", "finding_id")
        assert support[ids["site"]]["active"] == 0
        assert support[ids["open"]]["active"] == 0
        assert support[ids["lost"]]["active"] == 0
        assert support[ids["review"]] == before["support"][ids["review"]]
        assert support[ids["review"]]["active"] == 1
        assert (
            conn.execute("SELECT value FROM revisions WHERE name='findings'").fetchone()[0]
            == before["revision"] + 1
        )
        # A full scan neither restores the lost unit nor reopens a closed one.
        while scan(migrated, NOW, "run", limit=2) == 2:
            pass
        assert ids["lost"] not in rows(conn, "findings", "finding_id")
        assert not conn.execute(
            "SELECT 1 FROM findings WHERE kind='work_attempt' AND closed_at IS NULL"
        ).fetchone()
        # Writing a finding no longer needs the dropped history tables.
        with migrated.transaction() as write:
            reconcile_requirement(
                write,
                Requirement("round_observations", "w2", "eepro", "ready", "fetch", {}),
                NOW,
                "run",
            )


def test_migration_without_open_unit_findings_keeps_the_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state"
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 36)
        with open_database(path, lock=False) as database:
            revision = database.connection.execute(
                "SELECT value FROM revisions WHERE name='findings'"
            ).fetchone()[0]
    with open_database(path, lock=False) as database:
        assert database.schema_version == 37
        assert (
            database.connection.execute(
                "SELECT value FROM revisions WHERE name='findings'"
            ).fetchone()[0]
            == revision
        )


def test_schema_36_code_refuses_a_schema_37_file(state, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _, _ = state
    with open_database(path, lock=False) as database:
        assert database.schema_version == 37
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 36)
    with pytest.raises(RuntimeError, match="schema 37 is newer than supported 36"):
        open_database(path, lock=False)


def _admission(conn, action_id: str, kind: str, state: str, *, run: str | None = None) -> None:
    conn.execute(
        "INSERT INTO execution_admissions(action_id,action_kind,admitted_at,control_revision,"
        "run_id,all_sources,all_kinds,state,settled_at,outcome) VALUES (?,?,?,0,?,0,0,?,?,?)",
        (
            action_id,
            kind,
            NOW.isoformat(),
            run,
            state,
            NOW.isoformat() if state == "settled" else None,
            "completed" if state == "settled" else None,
        ),
    )
    for scope_kind, scope_id in (("source", "eepro"), ("kind", "work_attempt")):
        conn.execute(
            "INSERT INTO execution_dependencies VALUES (?,?,?)", (action_id, scope_kind, scope_id)
        )


def test_migration_from_36_deletes_unit_admissions_and_their_scope_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state"
    kept = {
        "request_1": ("request", "settled"),
        "fetch_1": ("fetch", "active"),
        "event_timing_1": ("project", "settled"),
        "build_1": ("build", "settled"),
        "publication_1": ("publication", "active"),
    }
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 36)
        with open_database(path, lock=False) as database:
            with database.transaction() as conn:
                conn.execute(
                    "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run',?,0)",
                    (NOW.isoformat(),),
                )
                # The publication goes last: once active it fences semantic writes.
                for action_id, (kind, state) in kept.items():
                    _admission(conn, action_id, kind, state, run="run")
                    if action_id == "request_1":
                        conn.execute(
                            "INSERT INTO scheduler_requests(action_id,host,day,category,"
                            "work_key,run_id,issued_at) "
                            "VALUES ('request_1','example.test','d','c','k','run',?)",
                            (NOW.isoformat(),),
                        )
                for number, (kind, state) in enumerate(
                    (("parse", "settled"), ("project", "active"), ("link", "settled"))
                ):
                    _admission(conn, f"work_{number}", kind, state, run="run")
            indexes = {
                row[0]
                for row in database.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name IN ('execution_admissions','execution_dependencies')"
                )
            }
    with open_database(path, lock=False) as migrated:
        conn = migrated.connection
        assert migrated.schema_version == 37
        admissions = {
            row["action_id"]: (row["action_kind"], row["state"])
            for row in conn.execute("SELECT * FROM execution_admissions")
        }
        # The exclusive open settled the active rows first (a publication stays uncertain).
        assert admissions == {
            "request_1": ("request", "settled"),
            "fetch_1": ("fetch", "settled"),
            "event_timing_1": ("project", "settled"),
            "build_1": ("build", "settled"),
            "publication_1": ("publication", "uncertain"),
        }
        dependencies = {
            row[0] for row in conn.execute("SELECT action_id FROM execution_dependencies")
        }
        assert dependencies == set(kept)
        assert conn.execute("SELECT count(*) FROM execution_dependencies").fetchone()[0] == 10
        assert {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name IN ('execution_admissions','execution_dependencies')"
            )
        } == indexes
        assert "execution_dependencies_scope" in indexes
        plan = " ".join(
            str(row[3])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT action_id FROM execution_dependencies "
                "WHERE scope_kind='source' AND scope_id='eepro'"
            )
        )
        assert "execution_dependencies_scope" in plan
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='execution_dependencies_kept'"
        ).fetchone()
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_the_migrating_connection_keeps_its_secure_delete_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 37 turns `secure_delete` off for its deletes; the runner gives
    the connection its own setting back, so a caller's later deletes zero."""
    path = tmp_path / "state"
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 36)
        with open_database(path, lock=False):
            pass
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 36)
        with open_database(path, lock=False) as database:
            patch.setattr(db_module, "SCHEMA_VERSION", 37)
            conn = database.connection
            conn.execute("PRAGMA secure_delete=ON")
            db_module._migrate(database)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 37
            assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
