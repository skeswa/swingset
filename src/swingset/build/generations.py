"""Complete build generations only after candidate files are durable and verified."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from swingset.fetch.archive import Archive
from swingset.publish.safety import reject_candidate, verify_candidate_files
from swingset.state import derivations
from swingset.state.attempts import SupersededWorkError
from swingset.state.db import Database
from swingset.state.work import WorkUnit
from swingset.state.write_deadline import WriteDeadlineExceeded

from .builder import BuildError, BuildResult
from .closure import ClosureError, ReleaseClosure, hydrate, retain
from .closure_validation import validation_scope
from .event_artifacts import artifact_source
from .identity_policy import correction_token


def baseline_commit(database: Database) -> str | None:
    path = database.state_dir / "baseline"
    return json.loads((path / "PUBLISHED").read_bytes())["commit"] if path.is_symlink() else None


def select(
    database: Database,
    *,
    bundle_digest: str,
    correction_only: bool,
    closure: ReleaseClosure | Mapping[str, Any] | None = None,
) -> derivations.Selection:
    """Call in the same read snapshot that supplies the build's actual rows."""
    pinned = (
        closure.manifest()
        if isinstance(closure, ReleaseClosure)
        else dict(closure)
        if closure is not None
        else None
    )
    if correction_only and pinned is not None:
        raise BuildError("correction-only build cannot select new closure generations")
    if pinned is not None:
        pinned = hydrate(database.connection, pinned)
    context: dict[str, Any] = {
        "baseline_commit": baseline_commit(database),
        "input_bundle_hash": bundle_digest,
        "mode": "correction_only"
        if correction_only
        else "closure"
        if pinned is not None
        else "settled",
    }
    if pinned is not None:
        from .closure import public_manifest

        # The recipe names the private proof by digest. Embedding it stored a
        # second copy of the whole closure in every build generation (D-0199).
        context["release_closure"] = public_manifest(pinned)
        context["correction_token"] = correction_token(database.connection, closure=pinned)
    return derivations.desired(
        database.connection,
        WorkUnit("build", "correction" if correction_only else "release", "all"),
        context=context,
        closure=pinned,
    )


def complete(
    database: Database,
    selection: derivations.Selection,
    result: BuildResult,
    *,
    now: datetime,
    run_id: str,
    closure: Mapping[str, Any] | None = None,
) -> None:
    """``closure`` is the build's private proof when it is already in memory."""
    # Hash potentially large files outside the short SQLite write transaction.
    verify_candidate_files(result.path)
    pinned = selection.context.get("release_closure")
    from contextlib import nullcontext

    from .closure import known_proof

    try:
        # Resolve the named proof once, before the bounded write, instead of at
        # each of its boundaries (D-0199).
        # A second decoded copy of the 235 MB proof pushed the build near the
        # worker's 8 GB memory limit, so reuse the caller's when it has one.
        in_memory = (
            closure
            if closure is not None
            and closure.get("format") == "release-closure-v1"
            and pinned is not None
            and closure.get("digest") == pinned.get("private_digest")
            else None
        )
        proof = (
            known_proof(
                in_memory if in_memory is not None else hydrate(database.connection, pinned)
            )
            if pinned is not None
            else nullcontext()
        )
        with (
            proof,
            validation_scope(database.connection),
            artifact_source(database.connection, Archive(database.state_dir)),
        ):
            if pinned is not None:
                # Prove the closure in an unbounded read snapshot first. The
                # write below rechecks that proof's exact evidence at each of
                # its three boundaries, so evidence changed after this point
                # still rejects and rolls back; only the full graph walk moves
                # out of the 45-second write bound (D-0179).
                from .closure import validate

                with database.transaction(immediate=False) as conn:
                    validate(conn, pinned)
            with database.transaction() as conn:
                _complete_in_transaction(conn, database, selection, result, now=now, run_id=run_id)
    except (SupersededWorkError, ClosureError, WriteDeadlineExceeded) as error:
        # An overrun is contained like any other failed completion; it used to
        # escape as a non-build error and abort the whole cycle (D-0199).
        # The immutable files can survive a stale worker. They cannot become a
        # selected generation or be reused for publication after rejection.
        if not (result.path / "PUBLISHED").exists():
            reject_candidate(result.path, str(error))
        raise BuildError(str(error)) from error


def _complete_in_transaction(
    conn: sqlite3.Connection,
    database: Database,
    selection: derivations.Selection,
    result: BuildResult,
    *,
    now: datetime,
    run_id: str,
) -> None:
    if baseline_commit(database) != selection.context["baseline_commit"]:
        raise SupersededWorkError("build baseline changed before completion")
    accepted = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    if accepted is None or accepted[0] != selection.context["input_bundle_hash"]:
        raise SupersededWorkError("build input bundle changed before completion")
    pinned = selection.context.get("release_closure")
    if pinned is not None:
        # complete() below rechecks the closure proof before writing output,
        # after rows, and again when certifying, and falls back to the full
        # validation when the proof's evidence changed. Proof retention shares
        # that transaction and rolls back on failure.
        retain(conn, pinned)
        if correction_token(conn, closure=pinned) != selection.context.get("correction_token"):
            raise SupersededWorkError("correction policy changed before build completion")
    derivations.complete(
        conn,
        selection,
        rows=(
            derivations.OutputRow(
                "artifact",
                result.candidate_id,
                {
                    "candidate_id": result.candidate_id,
                    "path": str(result.path.resolve()),
                    "manifest_hash": result.manifest_hash,
                    "content_hash": result.content_hash,
                },
            ),
        ),
        now=now,
        run_id=run_id,
    )


def completed(conn: sqlite3.Connection, candidate_id: str, manifest_hash: str) -> bool:
    """Any immutable completed build receipt may support its still-valid candidate.

    Input proof retention alone is insufficient. Newer materialized pointers do
    not erase a completed older build's artifact receipt.
    """
    return (
        conn.execute(
            "SELECT 1 FROM derivation_generations g JOIN derivation_rows r ON r.generation_id=g.generation_id "
            "WHERE g.stage='build' AND r.table_name='artifact' AND r.record_key=? "
            "AND json_extract(r.payload_json,'$.candidate_id')=? AND json_extract(r.payload_json,'$.manifest_hash')=? LIMIT 1",
            (candidate_id, candidate_id, manifest_hash),
        ).fetchone()
        is not None
    )
