"""Independent candidate identity checks using synthetic retained source evidence."""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest
from test_identity_journal import prepared

from swingset.build.schema import SCHEMAS
from swingset.state.db import open_database
from swingset.state.identity_journal import token
from swingset.state.identity_references import ReferenceReader

spec = importlib.util.spec_from_file_location(
    "v4_identity_checks", Path(__file__).parents[1] / "research" / "v4_identity_checks.py"
)
assert spec is not None and spec.loader is not None
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)
audit_identity = audit_module.audit_identity


@pytest.fixture
def candidate(tmp_path):
    with open_database(tmp_path) as db, duckdb.connect() as conn:
        state = db.connection
        identifier, _ = prepared(state)
        record = state.execute(
            "SELECT observation_id,payload_json FROM observations WHERE kind='round_sheet'"
        ).fetchone()
        payload = json.loads(record[1])
        payload["tables"][0]["rows"][0]["cells"][1]["attributes"] = [["data-wsdc", "100"]]
        state.execute(
            "UPDATE observations SET payload_json=? WHERE observation_id=?",
            (json.dumps(payload), record[0]),
        )
        binding = ReferenceReader(state).for_subject("entry", identifier)[0]
        journal = token(state)
        policy = {
            "baseline_commit": "published-v3",
            "accuracy_expansions": "withheld_pending_H17",
            "token": {
                "decision_policy": "identity-decisions-v1",
                "journal_digest": journal.digest,
                "journal_generation": journal.generation,
            },
        }
        entry = dict(
            state.execute("SELECT * FROM entries WHERE entry_id=?", (identifier,)).fetchone()
        )
        entry.update(wsdc_id=100, link_status="confirmed")
        data = {
            "entries": [entry],
            "judges": [dict(row) for row in state.execute("SELECT * FROM judges")],
            "contests": [dict(row) for row in state.execute("SELECT * FROM contests")],
            "placements": [],
            "dancers": [{"wsdc_id": 100, "first_name": "Alex", "last_name": "Lee"}],
            "registry_placements": [],
            "identity_links": [
                {
                    "link_id": "assertion",
                    "subject_kind": "entry",
                    "subject_id": identifier,
                    "wsdc_id": 100,
                    "method": "source_id",
                    "status": "confirmed",
                    "source_ref_ids": [binding.reference.ref_id],
                    "decision_ids": [],
                    "acceptance_policy": "identity-decisions-v1",
                    "acceptance_state": "accepted",
                    "journal_digest": journal.digest,
                    "journal_generation": journal.generation,
                }
            ],
        }
        for table, records in data.items():
            schema = SCHEMAS[table]
            values = []
            for row in records:
                converted = {}
                for field in schema:
                    value = row.get(field.name)
                    if pa.types.is_timestamp(field.type) and isinstance(value, str):
                        value = datetime.fromisoformat(value)
                    if pa.types.is_list(field.type) and isinstance(value, str):
                        value = json.loads(value)
                    if pa.types.is_boolean(field.type) and value is not None:
                        value = bool(value)
                    converted[field.name] = value
                values.append(converted)
            conn.register("input_rows", pa.Table.from_pylist(values, schema=schema))
            for prefix in ("old", "new"):
                conn.execute(f"CREATE TABLE {prefix}_{table} AS SELECT * FROM input_rows")
            conn.unregister("input_rows")
        yield conn, state, policy, identifier


def audit(candidate):
    conn, state, policy, _ = candidate
    return audit_identity(conn, state=state, release_policy=policy, baseline_commit="published-v3")


def test_retained_owned_source_id_and_named_judge_without_id_pass(candidate):
    report = audit(candidate)
    assert all(report["checks"].values()), report
    assert report["details"]["accepted_identities_match_retained_support"]["checked"] == 1
    assert (
        report["details"]["named_null_id_judges_preserved_where_source_persists"]["retained"] == 1
    )


@pytest.mark.parametrize("old_id", [None, 200])
def test_new_or_replaced_default_is_detected(candidate, old_id):
    candidate[0].execute("UPDATE old_entries SET wsdc_id=?", [old_id])
    assert not audit(candidate)["checks"]["entries_no_new_or_replaced_default_ids"]


@pytest.mark.parametrize(
    "statement,check",
    [
        (
            "UPDATE new_identity_links SET journal_generation=999",
            "public_assertion_acceptance_fields_match_release",
        ),
        ("UPDATE new_identity_links SET wsdc_id=200", "default_ids_have_accepted_assertions"),
        (
            "UPDATE new_identity_links SET source_ref_ids=['wrong-reference']",
            "accepted_identities_match_retained_support",
        ),
        (
            "UPDATE new_identity_links SET method='manual'",
            "accepted_identities_match_retained_support",
        ),
        (
            "UPDATE new_identity_links SET method='name_unique'",
            "accepted_assertions_have_confirmation_method_and_references",
        ),
        ("UPDATE new_entries SET wsdc_id=NULL", "accepted_assertions_have_default_ids"),
        ("DELETE FROM new_judges", "named_null_id_judges_preserved_where_source_persists"),
    ],
)
def test_unsafe_public_fields_and_lost_named_judge_are_detected(candidate, statement, check):
    candidate[0].execute(statement)
    assert not audit(candidate)["checks"][check]


def test_reparse_printed_id_change_invalidates_old_source_assertion(candidate):
    state = candidate[1]
    record = state.execute(
        "SELECT observation_id,payload_json FROM observations WHERE kind='round_sheet'"
    ).fetchone()
    payload = json.loads(record[1])
    payload["tables"][0]["rows"][0]["cells"][1]["attributes"] = [["data-wsdc", "200"]]
    state.execute(
        "UPDATE observations SET payload_json=? WHERE observation_id=?",
        (json.dumps(payload), record[0]),
    )
    assert not audit(candidate)["checks"]["accepted_identities_match_retained_support"]


def test_probable_evidence_can_remain_without_becoming_accepted(candidate):
    candidate[0].execute("UPDATE new_entries SET wsdc_id=NULL,link_status='probable'")
    candidate[0].execute(
        "UPDATE new_identity_links SET status='probable',method='name_scored',acceptance_state='unresolved'"
    )
    report = audit(candidate)
    assert all(report["checks"].values()), report


def test_unresolved_assertion_cannot_fabricate_decision_metadata(candidate):
    candidate[0].execute("UPDATE new_entries SET wsdc_id=NULL,link_status='probable'")
    candidate[0].execute(
        "UPDATE new_identity_links SET status='probable',method='name_scored',acceptance_state='unresolved',decision_ids=['fabricated-review']"
    )
    report = audit(candidate)
    assert not report["checks"]["public_identity_assertions_respect_decisions"]


def test_placement_cannot_retain_identity_dependent_points_without_entry_join(candidate):
    conn, _, _, identifier = candidate
    conn.execute(
        "INSERT INTO new_placements(placement_id,leader_entry_id,leader_wsdc_id,registry_points_leader,registry_confirmed,points_matches_expected) VALUES ('stale',?,NULL,3,true,true)",
        [identifier],
    )
    report = audit(candidate)
    assert not report["checks"]["placement_leader_identity_and_points_supported"]
    assert not report["checks"]["placement_identity_flags_supported"]
