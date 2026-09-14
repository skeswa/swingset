"""Compact receipts preserve admission rules and never retain parse payloads."""

import json
import sqlite3
from contextlib import closing

import pytest

from swingset.build import closure_support as support
from swingset.build.closure_manifest import ClosureError, canonical, digest


@pytest.fixture
def evidence():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE source_generations(generation_id TEXT PRIMARY KEY,state TEXT,input_fingerprint TEXT,contract_version TEXT,page_kind TEXT,created_at TEXT,recipe_json TEXT,report_json TEXT,result_json TEXT);"
            "CREATE TABLE admission_policies(page_kind TEXT PRIMARY KEY,contract_version TEXT);"
            "CREATE TABLE admission_decisions(generation_id TEXT,state TEXT);"
            "CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,body_sha256 TEXT);"
        )
        recipe = {
            "context": {"snapshot_id": "snapshot"},
            "parser_version": "1",
            "extract_version": "1",
        }
        observations = [
            {
                "scope": {"kind": "event", "ref": "event"},
                "kind": "round_sheet",
                "payload": {"name": name},
            }
            for name in ("First", "Second")
        ]
        conn.execute(
            "INSERT INTO source_generations VALUES ('generation','accepted','fingerprint','contract','page','2026-01-01T00:00:00+00:00',?,?,?)",
            (
                canonical(recipe),
                canonical({"failures": []}),
                canonical({"observations": observations}),
            ),
        )
        conn.execute("INSERT INTO admission_policies VALUES ('page','contract')")
        conn.execute("INSERT INTO admission_decisions VALUES ('generation','accepted')")
        conn.execute("INSERT INTO snapshots VALUES ('snapshot','body')")
        selected = [
            {
                "key": str(index),
                "snapshot_id": "snapshot",
                "scope": ["event", "event"],
                "observation_kind": "round_sheet",
                "seq": index,
                "payload_sha256": digest(row["payload"]),
                "parser_version": "1",
                "extract_version": "1",
                "body_sha256": "body",
            }
            for index, row in enumerate(observations)
        ]
        yield conn, recipe, selected


def legacy_accepted(conn):
    row = conn.execute(
        "SELECT * FROM source_generations WHERE generation_id='generation'"
    ).fetchone()
    if row is None or row["state"] == "revoked":
        return False
    accepted = conn.execute(
        "SELECT 1 FROM admission_decisions WHERE generation_id='generation' AND state='accepted' LIMIT 1"
    ).fetchone()
    policy = conn.execute(
        "SELECT contract_version FROM admission_policies WHERE page_kind=?", (row["page_kind"],)
    ).fetchone()
    return bool(
        accepted is not None
        and policy is not None
        and policy[0] == row["contract_version"]
        and not json.loads(row["report_json"]).get("failures")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "DELETE FROM source_generations",
        "UPDATE source_generations SET state='revoked'",
        "UPDATE source_generations SET state='superseded'",
        "DELETE FROM admission_decisions",
        "INSERT INTO admission_decisions VALUES ('generation','superseded')",
        "DELETE FROM admission_policies",
        "UPDATE admission_policies SET contract_version='other'",
        'UPDATE source_generations SET report_json=\'{"failures":["missing_cell"]}\'',
    ],
)
def test_one_query_preserves_original_admission_predicate(evidence, mutation):
    conn, _, _ = evidence
    if mutation:
        conn.execute(mutation)
    expected = legacy_accepted(conn)
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        result = support._accepted(conn, "generation", include_result=False)
    finally:
        conn.set_trace_callback(None)
    assert (result is not None) == expected
    assert len(statements) == 1
    assert "result_json" not in statements[0]


def test_receipt_validation_cannot_read_result_payload_and_rechecks_later_mutation(evidence):
    conn, _, selected = evidence
    pinned = support.support(conn, selected, ["generation"], cutoff="2026-02-01T00:00:00+00:00")

    def authorizer(action, table, column, database, trigger):
        return (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ
            and table == "source_generations"
            and column == "result_json"
            else sqlite3.SQLITE_OK
        )

    conn.set_authorizer(authorizer)
    try:
        support.validate_support(conn, pinned)
        conn.execute("UPDATE source_generations SET state='revoked'")
        with pytest.raises(ClosureError, match="revoked"):
            support.validate_support(conn, pinned)
    finally:
        conn.set_authorizer(None)


def test_support_decodes_recipe_once_and_keeps_exact_witnesses(evidence, monkeypatch):
    conn, recipe, selected = evidence
    recipe_json = canonical(recipe)
    loads = support.json.loads
    recipes = []

    def count(value, *args, **kwargs):
        if value == recipe_json:
            recipes.append(value)
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(support.json, "loads", count)
    result = support.support(conn, selected, ["generation"], cutoff="2026-02-01T00:00:00+00:00")
    expected_receipt = {
        "generation_id": "generation",
        "input_fingerprint": "fingerprint",
        "contract_version": "contract",
        "page_kind": "page",
        "recipe": recipe,
    }
    assert result == tuple(
        {
            **row,
            "observation_id": row["key"],
            "state": "accepted",
            "source_generations": [expected_receipt],
        }
        for row in selected
    )
    assert len(recipes) == 1
    assert "result_json" not in canonical(result)
    # No memo survives a call, even on the same connection.
    conn.execute("UPDATE snapshots SET body_sha256='changed'")
    with pytest.raises(ClosureError, match="artifact_changed"):
        support.validate_support(conn, result)
