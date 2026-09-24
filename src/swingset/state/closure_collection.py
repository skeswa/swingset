"""Remove release-closure proofs that nothing names (D-0200).

A build retains its private release closure, a JSON manifest of about 235 MB,
in `derivation_dependency_sets` so a candidate's public manifest can be checked
against it later. Until candidate 018 a build retained that proof before its
files were written, and every proof carries its own cutoff, so each failed
attempt stored a new copy that no candidate and no generation names. By
2026-09-24 that left 146 such copies, 33.9 GB of a 64 GB file.

A proof is kept when any of these names it:

- a derivation generation's own `dependency_set_id`;
- a build generation's dependency set, through its `release_closure` member;
- a build generation's recipe context, by `digest` or `private_digest`;
- any directory under `candidates/`, rejected or not, including the baseline.

Everything else that starts like a release closure and says it is one is
collectable. Only proofs are ever considered; no other dependency set, row or
file is touched. `plan` writes nothing. `collect` takes a plan digest, recomputes
the plan under both locks, stops if it moved, and removes one proof per
transaction, lifting the table's no-delete trigger inside that transaction only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from swingset.build.closure_manifest import digest
from swingset.build.files import canonical_json, durable_write

from .control_lock import control_lock
from .db import Database

PLAN_FORMAT = "closure-proof-plan-v1"
RECEIPT_FORMAT = "closure-proof-collection-v1"
_PROOF_PREFIX = '{"baseline":'
_PROOF_FORMAT = '"format":"release-closure-v1"'
_NO_DELETE = "derivation_dependency_no_delete"


class ClosureCollectionError(RuntimeError):
    pass


def _proofs(conn: sqlite3.Connection) -> dict[str, int]:
    # octet_length reads the record header, not the 235 MB value.
    return {
        str(row[0]): int(row[1])
        for row in conn.execute(
            "SELECT dependency_set_id, octet_length(manifest_json) FROM derivation_dependency_sets "
            "WHERE substr(manifest_json,1,?)=?",
            (len(_PROOF_PREFIX), _PROOF_PREFIX),
        )
    }


def _candidate_names(state_dir: Path) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    directory = state_dir / "candidates"
    paths = sorted(directory.iterdir()) if directory.is_dir() else []
    # The baseline is named explicitly, wherever its link points.
    baseline = state_dir / "baseline"
    if baseline.exists():
        paths.append(baseline.resolve())
    for path in paths:
        manifest = path / "_meta" / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            closure = (json.loads(manifest.read_bytes()).get("release_policy") or {}).get(
                "closure"
            ) or {}
        except ValueError as error:
            # An unreadable candidate might name any proof: keep them all.
            raise ClosureCollectionError(f"unreadable candidate manifest {manifest}") from error
        for key in ("private_digest", "digest"):
            if closure.get(key):
                names.setdefault(str(closure[key]), set()).add(f"candidate:{path.name}")
    return names


def plan(conn: sqlite3.Connection, state_dir: Path) -> dict[str, Any]:
    """What would be removed and why everything else stays. Writes nothing."""
    proofs = _proofs(conn)
    kept: dict[str, set[str]] = {}

    def keep(identifier: str, reason: str) -> None:
        if identifier in proofs:
            kept.setdefault(identifier, set()).add(reason)

    for (identifier,) in conn.execute(
        "SELECT DISTINCT dependency_set_id FROM derivation_generations"
    ):
        keep(str(identifier), "generation_dependency_set")
    for generation_id, set_id, named, private in conn.execute(
        "SELECT generation_id, dependency_set_id, "
        "json_extract(recipe_json,'$.context.release_closure.digest'), "
        "json_extract(recipe_json,'$.context.release_closure.private_digest') "
        "FROM derivation_generations WHERE stage='build'"
    ):
        for value in (named, private):
            if value:
                keep(str(value), f"build_recipe:{generation_id}")
        row = conn.execute(
            "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
            (set_id,),
        ).fetchone()
        members = json.loads(row[0]) if row is not None else []
        for member in members if isinstance(members, list) else ():
            if isinstance(member, dict) and member.get("kind") == "release_closure":
                keep(str(member.get("fingerprint")), f"build_dependency:{generation_id}")
    for identifier, reasons in _candidate_names(state_dir).items():
        for reason in reasons:
            keep(identifier, reason)
    collectable = sorted(set(proofs) - set(kept))
    content = {
        "format": PLAN_FORMAT,
        "proofs": len(proofs),
        "proof_bytes": sum(proofs.values()),
        "kept": {identifier: sorted(kept[identifier]) for identifier in sorted(kept)},
        "kept_bytes": sum(proofs[identifier] for identifier in kept),
        "collectable": [
            {"id": identifier, "bytes": proofs[identifier]} for identifier in collectable
        ],
        "collectable_bytes": sum(proofs[identifier] for identifier in collectable),
    }
    return {**content, "digest": digest(content)}


def _usage(conn: sqlite3.Connection) -> dict[str, int]:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    return {
        "page_size": page_size,
        "page_count": int(conn.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(conn.execute("PRAGMA freelist_count").fetchone()[0]),
    }


def collect(
    database: Database, *, plan_digest: str, now: datetime, timeout: float = 60
) -> dict[str, Any]:
    """Remove the proofs the plan with this digest names, one per transaction."""
    conn = database.connection
    with control_lock(database.state_dir, timeout=timeout):
        current = plan(conn, database.state_dir)
        if current["digest"] != plan_digest:
            raise ClosureCollectionError(
                f"the plan moved: {plan_digest} is no longer current ({current['digest']}); "
                "read the new plan before collecting"
            )
        trigger = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (_NO_DELETE,)
        ).fetchone()
        if trigger is None:
            raise ClosureCollectionError(f"the {_NO_DELETE} trigger is missing")
        before = _usage(conn)
        removed: list[dict[str, Any]] = []
        error: str | None = None
        try:
            for item in current["collectable"]:
                with database.transaction() as write:
                    # The immutability trigger is lifted for this one statement
                    # and restored before commit; a failure rolls both back.
                    if not write.execute(
                        "SELECT instr(manifest_json,?)>0 FROM derivation_dependency_sets "
                        "WHERE dependency_set_id=?",
                        (_PROOF_FORMAT, item["id"]),
                    ).fetchone()[0]:
                        raise ClosureCollectionError(f"{item['id']} is not a release closure")
                    write.execute(f"DROP TRIGGER {_NO_DELETE}")
                    deleted = write.execute(
                        "DELETE FROM derivation_dependency_sets WHERE dependency_set_id=?",
                        (item["id"],),
                    ).rowcount
                    write.execute(trigger[0])
                    if deleted != 1:
                        raise ClosureCollectionError(f"{item['id']} was not removed exactly once")
                removed.append(item)
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # Every committed removal is on record, even when a later one
            # stopped the run.
            receipt = {
                "format": RECEIPT_FORMAT,
                "collected_at": now.isoformat(),
                "plan_digest": plan_digest,
                "planned": len(current["collectable"]),
                "removed": len(removed),
                "removed_bytes": sum(item["bytes"] for item in removed),
                "removed_ids": [item["id"] for item in removed],
                "kept": len(current["kept"]),
                "kept_bytes": current["kept_bytes"],
                "before": before,
                "after": _usage(conn),
                "error": error,
                "next": "gc --reclaim rewrites the file so the freed pages leave it",
            }
            path = (
                database.state_dir
                / "gc"
                / "receipts"
                / now.strftime("closure-proofs-%Y%m%dT%H%M%SZ.json")
            )
            durable_write(path, canonical_json(receipt))
        return {**receipt, "receipt": str(path)}
