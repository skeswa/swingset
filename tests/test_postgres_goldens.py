"""The port preserves hashes captured with SQLite code over real production rows."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from psycopg import sql
from test_postgres_schema import fresh_database  # noqa: F401

from swingset.admission import generations
from swingset.admission.policy import corpus_digest
from swingset.admission.support import selection_digest
from swingset.fetch.archive import canonical, digest
from swingset.project.history import _inventory_digest

FIXTURE = Path("tests/fixtures/postgres/production37_goldens.json")


def test_real_production_inventory_corpus_selection_policy_generation_and_payload_hashes(
    fresh_database,  # noqa: F811
):  # noqa: F811
    _, _, conn = fresh_database
    fixture = json.loads(FIXTURE.read_text())
    # These tables expose precisely the real value columns each public hash
    # reads. Schema/foreign-key/trigger parity has its own native integration
    # tests; this test detects changes to the hash queries and value bytes.
    for table, rows in fixture["rows"].items():
        names = tuple(rows[0])
        definition = sql.SQL(",").join(
            sql.SQL("{} text").format(sql.Identifier(name)) for name in names
        )
        conn.execute(sql.SQL("CREATE TEMP TABLE {}({})").format(sql.Identifier(table), definition))
        with conn.cursor().copy(
            sql.SQL("COPY {} FROM STDIN").format(sql.Identifier(table))
        ) as target:
            for row in rows:
                target.write_row(tuple(row[name] for name in names))
    assert _inventory_digest(conn) == fixture["hashes"]["inventory"]
    cohort = tuple(row["generation_id"] for row in fixture["rows"]["source_generations"])
    assert corpus_digest(conn, cohort) == fixture["hashes"]["corpus"]
    assert selection_digest(conn) == fixture["hashes"]["selection"]
    policies = list(
        conn.execute(
            "SELECT page_kind,contract_version,mode,policy_revision,reviewed_report_digest,reviewed_by,reviewed_at FROM admission_policies ORDER BY page_kind"
        )
    )
    assert digest(canonical(policies)) == fixture["hashes"]["policies"]
    for row, (identifier, fingerprint, regenerated) in zip(
        fixture["rows"]["source_generations"], fixture["hashes"]["generation_ids"], strict=True
    ):
        assert (
            generations.digest(
                generations.canonical(
                    {
                        "manifest": json.loads(row["manifest_json"]),
                        "recipe": json.loads(row["recipe_json"]),
                    }
                )
            )
            == fingerprint
            == row["input_fingerprint"]
        )
        assert (
            generations.generation_identity(
                unit_key=row["unit_key"],
                fingerprint=row["input_fingerprint"],
                report=json.loads(row["report_json"]),
                result_json=row["result_json"],
                previous=row["previous_generation_id"],
                work_token=row["work_token"],
            )
            == regenerated
            == identifier
        )
    for row, (identifier, regenerated) in zip(
        fixture["rows"]["derivation_payloads"], fixture["hashes"]["payload_ids"], strict=True
    ):
        assert hashlib.sha256(row["payload_json"].encode()).hexdigest() == regenerated == identifier
