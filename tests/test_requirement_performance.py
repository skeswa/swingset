"""Guard the actual inventory SQL against retained-scale repeated table scans."""

import json

from swingset.state.db import open_database
from swingset.state.requirement_scopes import scope_page, scope_present


def test_scope_pages_use_native_indexes_and_unambiguous_restart_keys(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        # Empty tables are sufficient for EXPLAIN: this guards the production SQL,
        # including cursor seeks and correlated finalist existence checks.
        statements = []
        conn.set_trace_callback(statements.append)
        for prefix, values in (
            ("a", ["last"]),
            ("b", ["last"]),
            ("c", ["source:colon", "ref:colon"]),
            ("d", [29]),
            ("e", ["series:colon", "2020-01"]),
            ("f", ["last"]),
            ("g", ["a" * 64]),
            ("h", ["last"]),
        ):
            assert scope_page(conn, prefix + ":" + json.dumps(values), 100) == []
        conn.set_trace_callback(None)
        plans = [
            " ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
            for sql in statements
        ]
        assert plans
        assert all("USE TEMP B-TREE" not in plan for plan in plans)
        assert all("SCAN p" not in plan for plan in plans)
        for table in (
            "snapshots",
            "identity_links",
            "registry_placements",
            "finding_support",
            "findings",
            "watches",
        ):
            assert all(f"SCAN {table}" not in plan or "USING" in plan for plan in plans)
        assert any("snapshots_body_idx (body_sha256>?)" in plan for plan in plans)
        assert any("placements_leader_entry_idx" in plan for plan in plans)
        assert any("placements_follower_entry_idx" in plan for plan in plans)


def test_retirement_presence_uses_indexed_columns(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        statements = []
        conn.set_trace_callback(statements.append)
        for kind, subject in (
            ("source_event_mapping", json.dumps(["source:colon", "ref:colon"])),
            ("registry_event_association", json.dumps(["series:colon", "2020-01"])),
            ("source_id_checked", "29"),
            ("archive_artifact", "a" * 64),
        ):
            assert not scope_present(conn, kind, subject)
        conn.set_trace_callback(None)
        for sql in statements:
            plans = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
            assert any("SEARCH" in plan for plan in plans), plans
            assert not any(plan.startswith("SCAN") for plan in plans), plans


def test_occurrence_event_lookup_and_requirement_history_are_indexed(tmp_path):
    with open_database(tmp_path) as db:
        for query, values, index in (
            (
                "SELECT 1 FROM canonical_scope_rows WHERE table_name=? AND record_key=? LIMIT 1",
                ("callback_marks", "mark"),
                "canonical_scope_rows_record_idx",
            ),
            (
                "SELECT event_id FROM events WHERE series_id=? AND event_month=?",
                ("series", "2020-01"),
                "events_series_month_idx",
            ),
            (
                "SELECT min(at) FROM requirement_transitions WHERE requirement_id=?",
                ("finding",),
                "requirement_transitions_requirement_idx",
            ),
            (
                "SELECT count(*) FROM requirement_attempts WHERE requirement_id=?",
                ("finding",),
                "requirement_attempts_requirement_idx",
            ),
        ):
            plans = [row[3] for row in db.connection.execute("EXPLAIN QUERY PLAN " + query, values)]
            assert any("SEARCH" in plan and index in plan for plan in plans), plans


def test_scope_pagination_preserves_composite_keys_with_delimiters(tmp_path):
    with open_database(tmp_path) as db:
        pairs = [("a", "b:c"), ("a:b", "c"), ("a:b", "d")]
        for source, reference in pairs:
            db.connection.execute(
                "INSERT INTO source_events(source,source_ref,url,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?, 'url','snapshot','1','now','now','run')",
                (source, reference),
            )
        cursor = ""
        seen = []
        for _ in pairs:
            page = scope_page(db.connection, cursor, 1)
            assert len(page) == 1
            seen.append(tuple(json.loads(page[0]["id"])))
            cursor = page[0]["key"]
        assert seen == pairs
        assert scope_page(db.connection, cursor, 1) == []


def test_v6_index_migration_preserves_cohort_history_and_restarts_old_cursor(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from swingset.state import db as database_module
    from swingset.state.requirements import (
        Requirement,
        reconcile_requirement,
        record_attempt,
    )

    now = datetime(2026, 9, 13, tzinfo=UTC)
    tables = (
        "findings",
        "requirement_transitions",
        "requirement_attempts",
        "requirement_cohorts",
        "requirement_cohort_members",
    )
    with monkeypatch.context() as patch:
        patch.setattr(database_module, "SCHEMA_VERSION", 5)
        with open_database(tmp_path) as db:
            conn = db.connection
            conn.execute(
                "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run',?,1)", (now.isoformat(),)
            )
            requirement = Requirement("round_observations", "round", "eepro", "ready", "fetch", {})
            key = reconcile_requirement(conn, requirement, now, "run")
            record_attempt(conn, key, "attempt", now, "failed")
            # Retain an actual schema-5 cohort, before capture watermarks existed.
            conn.execute(
                "INSERT INTO requirement_cohorts VALUES ('cohort',?,?,NULL,NULL,1)",
                (now.isoformat(), requirement.policy_version),
            )
            conn.execute("INSERT INTO requirement_cohort_members VALUES ('cohort',?)", (key,))
            conn.execute(
                "UPDATE requirement_scan SET cursor='d:29',started_at=?,scanned=123",
                (now.isoformat(),),
            )
            before = {
                table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
                for table in tables
            }
            old_columns = {
                table: ",".join(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
                for table in tables
            }
    with open_database(tmp_path) as db:
        conn = db.connection
        assert db.schema_version == database_module.SCHEMA_VERSION
        assert before == {
            table: [tuple(row) for row in conn.execute(f"SELECT {old_columns[table]} FROM {table}")]
            for table in tables
        }
        assert tuple(
            conn.execute("SELECT cursor,started_at,scanned FROM requirement_scan").fetchone()
        ) == ("", None, 123)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {row[1] for row in conn.execute("PRAGMA index_list(findings)")} >= {
            "findings_owner_idx",
            "findings_owner_finding_idx",
        }


def test_every_foreign_key_child_lookup_uses_an_index(tmp_path):
    """SQLite checks these child columns for every parent row being deleted."""
    with open_database(tmp_path) as db:
        conn = db.connection
        tables = [
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        checked = 0
        for table in tables:
            foreign_keys = {}
            for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
                foreign_keys.setdefault(row[0], []).append((row[1], row[3]))
            for group in foreign_keys.values():
                columns = [column for _, column in sorted(group)]
                where = " AND ".join(f"{column}=?" for column in columns)
                plans = [
                    row[3]
                    for row in conn.execute(
                        f"EXPLAIN QUERY PLAN SELECT rowid FROM {table} WHERE {where}",
                        ["example"] * len(columns),
                    )
                ]
                assert all(not plan.startswith("SCAN") for plan in plans), (table, columns, plans)
                assert any("SEARCH" in plan for plan in plans), (table, columns, plans)
                checked += 1
        assert checked > 20


def test_reopening_current_schema_preserves_indexes_and_partial_scan_cursor(tmp_path):
    with open_database(tmp_path) as db:
        cursor = 'g:["' + "a" * 64 + '"]'
        db.connection.execute("UPDATE requirement_scan SET cursor=?,scanned=42", (cursor,))
        indices = [
            tuple(row)
            for row in db.connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='index' ORDER BY name"
            )
        ]
    with open_database(tmp_path) as db:
        assert [
            tuple(row)
            for row in db.connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='index' ORDER BY name"
            )
        ] == indices
        assert tuple(
            db.connection.execute("SELECT cursor,scanned FROM requirement_scan").fetchone()
        ) == (cursor, 42)
