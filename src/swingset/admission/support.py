"""Read-only evidence support and publication boundary fingerprints."""

import json
import re
import sqlite3
from typing import Any

from swingset.fetch.archive import canonical, digest


def generation_failure_codes(conn: sqlite3.Connection, generation_id: str) -> tuple[str, ...]:
    """Public diagnostics use stored guard codes, never private exception text."""
    row = conn.execute(
        "SELECT report_json FROM source_generations WHERE generation_id=?", (generation_id,)
    ).fetchone()
    reasons = [] if row is None else list(json.loads(row[0])["failures"])
    decision = conn.execute(
        "SELECT reason FROM admission_decisions WHERE generation_id=? ORDER BY decision_id DESC LIMIT 1",
        (generation_id,),
    ).fetchone()
    if decision is not None:
        # Some dynamic checks append exception details after their stable code.
        reasons.extend(str(decision[0]).split(":", 1)[0].split(","))
    return tuple(sorted({reason for reason in reasons if re.fullmatch(r"[a-z][a-z0-9_]*", reason)}))


def selection_digest(conn: sqlite3.Connection) -> str:
    """Pin policies, selected/legacy pointers, and explicit revocations."""
    return digest(
        canonical(
            {
                "policies": [
                    tuple(row)
                    for row in conn.execute("SELECT * FROM admission_policies ORDER BY page_kind")
                ],
                "units": [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT unit_key,desired_fingerprint,accepted_generation_id,legacy_snapshot_id,legacy_state FROM source_units ORDER BY unit_key"
                    )
                ],
                "revocations": [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT generation_id,input_fingerprint FROM source_generations WHERE state='revoked' ORDER BY generation_id"
                    )
                ],
            }
        )
    )


def admission_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_generations'"
        ).fetchone()
        is None
    ):
        return {"available": False, "reason": "admission migration pending"}
    return {
        "available": True,
        "policies": [
            dict(row)
            for row in conn.execute(
                "SELECT page_kind,contract_version,mode,policy_revision,reviewed_by,reviewed_at FROM admission_policies ORDER BY page_kind"
            )
        ],
        "generations": [
            dict(row)
            for row in conn.execute(
                "SELECT page_kind,state,COUNT(*) AS count,MIN(created_at) AS oldest_created_at FROM source_generations GROUP BY state,page_kind ORDER BY page_kind,state"
            )
        ],
        "legacy_unassessed": conn.execute(
            "SELECT COUNT(*) FROM source_units WHERE legacy_state='legacy_unassessed'"
        ).fetchone()[0],
        "accepted_units": conn.execute(
            "SELECT COUNT(*) FROM source_units WHERE accepted_generation_id IS NOT NULL"
        ).fetchone()[0],
    }


def interpretation_support(
    conn: sqlite3.Connection,
    snapshot_id: str,
    *,
    parser_version: str,
    extract_version: str | None = None,
) -> dict[str, Any]:
    """Report support without blessing legacy evidence or selecting new inputs.

    ``usable`` requires a selected observation with an accepted generation.
    Legacy output has its own state and remains for the caller's disclosed
    compatibility policy; migration does not make it automatically admissible.
    An explicit revocation wins over legacy or other apparent support.
    """
    snapshot = conn.execute(
        "SELECT s.watch_id,w.parser FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    if snapshot is None:
        return {"state": "missing", "usable": False, "reason": "snapshot_missing"}
    watch_id, page_kind = str(snapshot[0]), str(snapshot[1])
    policy = conn.execute(
        "SELECT mode,contract_version,policy_revision FROM admission_policies WHERE page_kind=?",
        (page_kind,),
    ).fetchone()
    units = list(conn.execute("SELECT * FROM source_units WHERE watch_id=?", (watch_id,)))
    matching = []
    for unit in units:
        for generation in conn.execute(
            "SELECT generation_id,state,contract_version,recipe_json,removal_authority FROM source_generations WHERE unit_key=? ORDER BY created_at DESC,generation_id DESC",
            (unit["unit_key"],),
        ):
            recipe = json.loads(generation["recipe_json"])
            if recipe["context"]["snapshot_id"] != snapshot_id or recipe["parser_version"] != str(
                parser_version
            ):
                continue
            if extract_version is not None and recipe["extract_version"] != str(extract_version):
                continue
            matching.append((generation, unit))
    base = {
        "snapshot_id": snapshot_id,
        "parser_version": str(parser_version),
        "policy_revision": None if policy is None else policy[2],
    }
    revoked = [g["generation_id"] for g, _ in matching if g["state"] == "revoked"]
    if revoked:
        return {
            **base,
            "state": "revoked",
            "usable": False,
            "reason": "explicit_generation_revocation",
            "generation_ids": revoked,
        }
    observation = conn.execute(
        "SELECT 1 FROM observations WHERE watch_id=? AND snapshot_id=? AND parser_version=? AND (? IS NULL OR extract_version=?) LIMIT 1",
        (watch_id, snapshot_id, str(parser_version), extract_version, extract_version),
    ).fetchone()
    for generation, unit in matching:
        selected = unit["accepted_generation_id"] == generation["generation_id"]
        retained = generation["removal_authority"] == "none" and generation["state"] == "accepted"
        version_ok = policy is not None and policy[1] == generation["contract_version"]
        if (
            observation
            and generation["state"] == "accepted"
            and (selected or retained)
            and version_ok
        ):
            return {
                **base,
                "state": "accepted",
                "usable": True,
                "reason": "selected_generation" if selected else "retained_historical_claim",
                "generation_id": generation["generation_id"],
                "removal_authority": generation["removal_authority"],
            }
    if any(
        unit["legacy_state"] == "legacy_unassessed" and unit["legacy_snapshot_id"] == snapshot_id
        for unit in units
    ):
        return {
            **base,
            "state": "legacy_unassessed",
            "usable": False,
            "reason": "migration_grants_no_admission",
            "selected_observation": observation is not None,
        }
    return {
        **base,
        "state": "superseded" if matching else "unassessed",
        "usable": False,
        "reason": "interpretation_not_selected_under_current_contract",
        "selected_observation": observation is not None,
    }
