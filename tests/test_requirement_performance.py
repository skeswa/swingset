"""Guard the actual inventory SQL against retained-scale repeated table scans."""

import json

from swingset.state.db import open_database
from swingset.state.requirement_scopes import scope_page, scope_present


def plan_nodes(conn, statement, parameters=None):
    """Require a feasible indexed plan even on the deliberately empty fixture."""
    # Empty tables legitimately favor bitmap/aggregate/sort paths. These local
    # EXPLAIN settings prove that the production SQL can seek and preserve index
    # order; they do not prescribe the cost-based plan used on retained data.
    conn.execute("SET enable_seqscan=off")
    conn.execute("SET enable_hashagg=off")
    conn.execute("SET enable_bitmapscan=off")
    conn.execute("SET enable_sort=off")
    try:
        plan = conn.execute("EXPLAIN (FORMAT JSON) " + statement, parameters).fetchone()[0][0][
            "Plan"
        ]
    finally:
        conn.execute("RESET enable_seqscan")
        conn.execute("RESET enable_hashagg")
        conn.execute("RESET enable_bitmapscan")
        conn.execute("RESET enable_sort")

    def walk(node):
        yield node
        for child in node.get("Plans", ()):
            yield from walk(child)

    return list(walk(plan))


def test_scope_pages_use_native_indexes_and_unambiguous_restart_keys(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        # Empty tables are sufficient for EXPLAIN: this guards the production SQL,
        # including cursor seeks and correlated finalist existence checks.
        statements = []
        with conn.observing(lambda sql, params: statements.append((str(sql), params))):
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
        plans = [plan_nodes(conn, sql, params) for sql, params in statements]
        nodes = [node for plan in plans for node in plan]
        assert nodes
        assert not any(node["Node Type"] == "Seq Scan" for node in nodes), nodes
        assert not any(node["Node Type"] == "Sort" for node in nodes), nodes
        indexes = {node.get("Index Name") for node in nodes}
        assert "snapshots_body_idx" in indexes
        assert "placements_leader_entry_idx" in indexes
        assert "placements_follower_entry_idx" in indexes


def test_retirement_presence_uses_indexed_columns(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        statements = []
        with conn.observing(lambda sql, params: statements.append((str(sql), params))):
            for kind, subject in (
                ("source_event_mapping", json.dumps(["source:colon", "ref:colon"])),
                ("registry_event_association", json.dumps(["series:colon", "2020-01"])),
                ("source_id_checked", "29"),
                ("archive_artifact", "a" * 64),
            ):
                assert not scope_present(conn, kind, subject)
        for sql, params in statements:
            nodes = plan_nodes(conn, sql, params)
            assert any("Index" in node["Node Type"] for node in nodes), nodes
            assert not any(node["Node Type"] == "Seq Scan" for node in nodes), nodes


def test_occurrence_event_lookup_is_indexed(tmp_path):
    with open_database(tmp_path) as db:
        for query, values, index in (
            (
                "SELECT 1 FROM canonical_scope_rows WHERE table_name = %s AND record_key = %s LIMIT 1",
                ("callback_marks", "mark"),
                "canonical_scope_rows_record_idx",
            ),
            (
                "SELECT event_id FROM events WHERE series_id = %s AND event_month = %s",
                ("series", "2020-01"),
                "events_series_month_idx",
            ),
        ):
            nodes = plan_nodes(db.connection, query, values)
            assert any(node.get("Index Name") == index for node in nodes), nodes


def test_scope_pagination_preserves_composite_keys_with_delimiters(tmp_path):
    with open_database(tmp_path) as db:
        pairs = [("a", "b:c"), ("a:b", "c"), ("a:b", "d")]
        for source, reference in pairs:
            db.connection.execute(
                (
                    "INSERT INTO source_events (source, source_ref, url, snapshot_id, parser_version, "
                    "first_seen_at, last_seen_at, run_id) VALUES (%s, %s, 'url', 'snapshot', '1', 'now', 'now', "
                    "'run')"
                ),
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


def test_v6_index_migration_preserves_findings_and_restarts_old_cursor(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_requirement_performance.py",
        "test_v6_index_migration_preserves_findings_and_restarts_old_cursor",
    )


def test_every_foreign_key_child_lookup_uses_an_index(tmp_path):
    """Every application FK has an indexed child-column prefix for parent deletion."""
    with open_database(tmp_path) as db:
        conn = db.connection
        constraints = conn.execute("""
            SELECT c.conrelid::regclass::text AS child, c.conkey AS columns,
                   EXISTS(SELECT 1 FROM pg_index i
                          WHERE i.indrelid=c.conrelid AND i.indisvalid
                            AND i.indpred IS NULL
                            AND (i.indkey::smallint[])[0:cardinality(c.conkey)-1]=c.conkey) AS indexed
            FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
            WHERE c.contype='f' AND n.nspname IN ('fetch','plan','evidence','derive','project','release','ops')
        """).fetchall()
        assert len(constraints) > 20
        assert all(row["indexed"] for row in constraints), [
            tuple(row) for row in constraints if not row["indexed"]
        ]


def test_reopening_current_schema_preserves_indexes_and_partial_scan_cursor(tmp_path):
    with open_database(tmp_path) as db:
        cursor = 'g:["' + "a" * 64 + '"]'
        db.connection.execute("UPDATE requirement_scan SET cursor = %s, scanned = 42", (cursor,))
        indices = [
            tuple(row)
            for row in db.connection.execute(
                "SELECT schemaname,indexname,indexdef FROM pg_indexes WHERE schemaname IN ('fetch','plan','evidence','derive','project','release','ops') ORDER BY schemaname,indexname"
            )
        ]
    with open_database(tmp_path) as db:
        assert [
            tuple(row)
            for row in db.connection.execute(
                "SELECT schemaname,indexname,indexdef FROM pg_indexes WHERE schemaname IN ('fetch','plan','evidence','derive','project','release','ops') ORDER BY schemaname,indexname"
            )
        ] == indices
        assert tuple(
            db.connection.execute("SELECT cursor, scanned FROM requirement_scan").fetchone()
        ) == (cursor, 42)
