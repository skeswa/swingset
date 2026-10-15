"""Operator view and switch for one page kind's admission policy (`swingset contract`).

Contract 5 was activated by a one-off script: preflight counts, a reviewed
cohort recorded with `policy.record_review`, `policy.activate_contract`, and
the same counts after. This module is that script. It lives outside every
recipe scope: only `cli.py` imports it.

A review names a cohort of generations the reviewer read. Activation refuses
a kind whose code has no reviewed contract version (`unassessed`) and a
cohort staged under any other version than the code's current one, so a
policy can only enforce the contract the running code applies.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from swingset.state import body_packs
from swingset.state.db import Database

from .kinds import contract_version
from .policy import activate_contract, pause_contract, record_review


def status(conn: sqlite3.Connection, page_kind: str) -> dict[str, Any]:
    """The kind's code and policy versions and its parse results by contract and decision."""
    policy = conn.execute(
        "SELECT contract_version,mode,policy_revision,reviewed_by,reviewed_at "
        "FROM admission_policies WHERE page_kind=?",
        (page_kind,),
    ).fetchone()
    generations: dict[str, int] = {}
    failures: dict[str, int] = {}
    code = contract_version(page_kind)
    for version, state, report in conn.execute(
        "SELECT contract_version,state,report_json FROM source_generations WHERE page_kind=?",
        (page_kind,),
    ):
        key = f"{version}:{state}"
        generations[key] = generations.get(key, 0) + 1
        if str(version) != code:
            continue
        if body_packs.is_stub(report):
            failures["(packed)"] = failures.get("(packed)", 0) + 1
            continue
        for failure in json.loads(report).get("failures") or ["(none)"]:
            failures[failure] = failures.get(failure, 0) + 1
    accepted: dict[str, int] = {}
    for version, count in conn.execute(
        "SELECT g.contract_version,COUNT(*) FROM source_units u JOIN source_generations g "
        "ON g.generation_id=u.accepted_generation_id WHERE u.page_kind=? GROUP BY 1",
        (page_kind,),
    ):
        accepted[str(version)] = int(count)
    units = conn.execute(
        "SELECT COUNT(*) FROM source_units WHERE page_kind=?", (page_kind,)
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM pending_work p JOIN snapshots s ON s.snapshot_id=p.unit_id "
        "JOIN watches w ON w.watch_id=s.watch_id "
        "WHERE p.stage='parse' AND p.unit_kind='snapshot' AND w.parser=?",
        (page_kind,),
    ).fetchone()[0]
    reviews = [
        {"digest": row[0], "contract_version": row[1], "reviewer": row[2], "reviewed_at": row[3]}
        for row in conn.execute(
            "SELECT report_digest,contract_version,reviewer,reviewed_at FROM admission_reviews "
            "WHERE page_kind=? ORDER BY reviewed_at",
            (page_kind,),
        )
    ]
    return {
        "page_kind": page_kind,
        "code_contract_version": code,
        "policy": None
        if policy is None
        else {
            "contract_version": policy[0],
            "mode": policy[1],
            "policy_revision": policy[2],
            "reviewed_by": policy[3],
            "reviewed_at": policy[4],
        },
        "mode": "shadow" if policy is None else policy[1],
        "generations": dict(sorted(generations.items())),
        "failures_under_code_version": dict(sorted(failures.items())),
        "units": int(units),
        "accepted_units": dict(sorted(accepted.items())),
        "pending_parse": int(pending),
        "reviews": reviews,
    }


def activate(
    database: Database,
    page_kind: str,
    cohort: Sequence[str],
    *,
    by: str,
    evidence: str,
    now: str,
) -> dict[str, Any]:
    """Record the reviewed cohort and enforce the kind's contract, in one transaction."""
    if not by.strip() or not evidence.strip():
        raise ValueError("activation needs --by and --evidence")
    version = contract_version(page_kind)
    if version == "unassessed":
        raise ValueError(f"{page_kind} has no reviewed contract version")
    if not cohort:
        raise ValueError("activation needs the reviewed cohort's generations")
    with database.transaction() as conn:
        for generation in cohort:
            row = conn.execute(
                "SELECT page_kind,contract_version FROM source_generations WHERE generation_id=?",
                (generation,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown generation: {generation}")
            if (row[0], str(row[1])) != (page_kind, version):
                raise ValueError(
                    f"{generation} is {row[0]} contract {row[1]}, not {page_kind} contract "
                    f"{version}"
                )
        before = status(conn, page_kind)
        digest = record_review(
            conn, tuple(cohort), reviewer=by.strip(), reviewed_at=now, evidence=evidence.strip()
        )
        activate_contract(conn, page_kind, digest)
        after = status(conn, page_kind)
    return {
        "command": "contract activate",
        "page_kind": page_kind,
        "contract_version": version,
        "by": by.strip(),
        "evidence": evidence.strip(),
        "at": now,
        "cohort": list(cohort),
        "review_digest": digest,
        "before": before,
        "after": after,
        "queued_parse": after["pending_parse"] - before["pending_parse"],
    }


def pause(database: Database, page_kind: str, *, by: str, now: str) -> dict[str, Any]:
    """Stop promoting new generations of the kind. It withdraws nothing.

    Generations already accepted stay accepted and keep publishing; undoing a
    bad activation needs the pre-activation backup or explicit revocation
    (`select.revoke_generation`).
    """
    if not by.strip():
        raise ValueError("pause needs --by")
    with database.transaction() as conn:
        before = status(conn, page_kind)
        if before["policy"] is None:
            raise ValueError(f"{page_kind} has no policy to pause")
        pause_contract(conn, page_kind)
        after = status(conn, page_kind)
    return {
        "command": "contract pause",
        "page_kind": page_kind,
        "effect": "new generations of this kind are no longer promoted",
        "not_undone": "accepted generations stay accepted and keep publishing; restore the "
        "pre-activation backup or revoke named generations to withdraw them",
        "by": by.strip(),
        "at": now,
        "before": before,
        "after": after,
    }
