"""Serialize accepted corrections with the single remote commit boundary."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, durable_write, fsync_dir, sha256_file


class StaleCandidateError(RuntimeError):
    pass


class PublicationHeldError(RuntimeError):
    pass


def _check_files(candidate: Path, built: dict[str, Any], manifest: dict[str, Any]) -> None:
    if (candidate / "REJECTED").exists():
        raise StaleCandidateError("candidate was rejected; rebuild")
    actual = {
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*")
        if path.is_file() and path.name not in {"BUILT", "PUBLISHING", "PUBLISHED", "REJECTED"}
    }
    if actual != set(manifest["files"]) | {"_meta/manifest.json"}:
        raise StaleCandidateError("candidate file closure changed after build")
    if sha256_file(candidate / "_meta/manifest.json") != built["manifest_hash"]:
        raise StaleCandidateError("candidate manifest changed after build")
    for name, expected in manifest["files"].items():
        path = (candidate / name).resolve()
        if (
            not path.is_relative_to(candidate.resolve())
            or not path.is_file()
            or sha256_file(path) != expected
        ):
            raise StaleCandidateError(f"candidate file changed after build: {name}")


def verify_candidate_files(candidate: Path) -> None:
    """Verify a retained candidate's complete immutable artifact closure."""
    built = json.loads((candidate / "BUILT").read_bytes())
    manifest = json.loads((candidate / "_meta/manifest.json").read_bytes())
    _check_files(candidate, built, manifest)


def _validate_candidate(
    conn: sqlite3.Connection | None,
    state_dir: Path,
    candidate: Path,
    built: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    _check_files(candidate, built, manifest)
    baseline = state_dir / "baseline"
    baseline_commit = (
        json.loads((baseline / "PUBLISHED").read_bytes())["commit"]
        if baseline.is_symlink()
        else None
    )
    if built.get("baseline_commit") != baseline_commit:
        raise StaleCandidateError("candidate baseline changed before submission")
    policy = manifest.get("release_policy")
    has_journal = (
        conn is not None
        and conn.execute("SELECT 1 FROM sqlite_master WHERE name='identity_decisions'").fetchone()
        is not None
    )
    if has_journal and not policy:
        raise StaleCandidateError("candidate predates correction input sealing; rebuild")
    if policy:
        if conn is None or not built.get("input_paths"):
            raise StaleCandidateError(
                "sealed publication requires its state and current input paths"
            )
        from swingset.build.identity_policy import correction_token
        from swingset.schedule.cycle import versions
        from swingset.state.inputs import capture

        selected_closure = policy.get("closure") if policy.get("mode") == "closure" else None
        if policy.get("mode") == "closure":
            from swingset.build.closure import ClosureError, validate

            if selected_closure is None:
                raise StaleCandidateError("selected release closure is missing")
            from swingset.build.generations import completed

            if not completed(conn, candidate.name, built["manifest_hash"]):
                raise PublicationHeldError(
                    "candidate files await durable build completion; retry build"
                )
            try:
                from swingset.build.event_artifacts import artifact_source
                from swingset.fetch.archive import Archive

                with artifact_source(conn, Archive(state_dir)):
                    validate(conn, selected_closure)
            except ClosureError as exc:
                raise StaleCandidateError(f"selected release closure changed: {exc}") from exc
        if policy["token"] != correction_token(conn, closure=selected_closure):
            raise StaleCandidateError("identity journal or acceptance policy changed; rebuild")
        try:
            bundle = capture(
                Path(built["input_paths"]["config"]),
                Path(built["input_paths"]["overrides"]),
                state_dir,
                versions(),
            )
        except (OSError, ValueError) as exc:
            raise StaleCandidateError(
                "current correction inputs are invalid; repair and rebuild"
            ) from exc
        selected = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
        if (
            bundle.digest != policy["input_bundle_hash"]
            or selected is None
            or selected[0] != bundle.digest
        ):
            raise StaleCandidateError("captured correction inputs changed; accept and rebuild")
        if policy["mode"] == "settled":
            if conn.execute("SELECT 1 FROM pending_work LIMIT 1").fetchone():
                raise StaleCandidateError("new derivation work arrived after build")
            revisions = {
                str(row[0]): int(row[1]) for row in conn.execute("SELECT name,value FROM revisions")
            }
            if revisions != policy["revisions"]:
                raise StaleCandidateError("selected derivations changed after build")


def _connection(state_dir: Path) -> sqlite3.Connection | None:
    path = state_dir / "state.sqlite"
    if not path.is_file():
        return None
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _has_controls(conn: sqlite3.Connection | None) -> bool:
    return (
        conn is not None
        and conn.execute("SELECT 1 FROM sqlite_master WHERE name='execution_admissions'").fetchone()
        is not None
    )


@contextmanager
def publication_boundary(state_dir: Path, candidate: Path) -> Iterator[None]:
    """Reserve validated semantics while control writes remain available.

    The caller owns state.lock. On schema12 the short admission transaction
    installs an active reservation protected by semantic-write triggers. No
    SQLite or control mutex remains held during the network request. A lost
    response releases that fence but retains an uncertain draining action.
    Legacy databases retain their original write-transaction boundary.
    """
    built = json.loads((candidate / "BUILT").read_bytes())
    if (state_dir / "RESTORE_PENDING").exists():
        raise PublicationHeldError("publication is disabled while restore verification is pending")
    manifest = json.loads((candidate / "_meta/manifest.json").read_bytes())
    conn = _connection(state_dir)
    try:
        if not _has_controls(conn):
            if conn is not None:
                conn.execute("BEGIN IMMEDIATE")
            _validate_candidate(conn, state_dir, candidate, built, manifest)
            yield
            return
        assert conn is not None
        from swingset.state.controls import ActionScope, admission, settle
        from swingset.state.db import Database

        database = Database(state_dir, conn, None)
        action_id = "publication_" + uuid.uuid4().hex
        with admission(
            database,
            action_id=action_id,
            action_kind="publication",
            scope=ActionScope(all_sources=True, all_kinds=True),
            now=datetime.now(UTC),
            candidate_id=candidate.name,
            validate=lambda current: _validate_candidate(
                current, state_dir, candidate, built, manifest
            ),
        ):
            pass
        try:
            yield
        except BaseException:
            with database.transaction():
                settle(
                    conn,
                    action_id,
                    now=datetime.now(UTC),
                    outcome="receipt_reconciliation_required",
                    uncertain=True,
                )
            raise
        else:
            with database.transaction():
                settle(conn, action_id, now=datetime.now(UTC), outcome="published")
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()


def publication_in_flight(state_dir: Path, candidate: Path) -> bool:
    """An unrecovered active request cannot be called unlanded from head alone."""
    conn = _connection(state_dir)
    try:
        return bool(
            _has_controls(conn)
            and conn is not None
            and conn.execute(
                "SELECT 1 FROM execution_admissions WHERE candidate_id=? "
                "AND action_kind IN ('publish','publication') AND state='active' LIMIT 1",
                (candidate.name,),
            ).fetchone()
        )
    finally:
        if conn is not None:
            conn.close()


def settle_publication(state_dir: Path, candidate: Path, *, outcome: str) -> None:
    """A verified receipt, or reconciled unlanded outcome, closes its drain."""
    conn = _connection(state_dir)
    try:
        if not _has_controls(conn):
            return
        assert conn is not None
        from swingset.state.controls import settle
        from swingset.state.db import Database

        with Database(state_dir, conn, None).transaction():
            actions = conn.execute(
                "SELECT action_id FROM execution_admissions WHERE candidate_id=? "
                "AND action_kind IN ('publish','publication') AND state<>'settled'",
                (candidate.name,),
            ).fetchall()
            for row in actions:
                settle(conn, row[0], now=datetime.now(UTC), outcome=outcome)
    finally:
        if conn is not None:
            conn.close()


def reject_candidate(candidate: Path, reason: str) -> None:
    durable_write(
        candidate / "REJECTED",
        canonical_json({"reason": reason, "rejected_at": datetime.now(UTC).isoformat()}),
    )
    (candidate / "PUBLISHING").unlink(missing_ok=True)
    fsync_dir(candidate)


def publication_receipt(candidate: Path, commit: str, *, recovered: bool = False) -> dict[str, Any]:
    now = datetime.now(UTC)
    policy = (
        json.loads((candidate / "_meta/manifest.json").read_bytes()).get("release_policy") or {}
    )
    detected = policy.get("detected_at")
    latency = (
        max(0.0, (now - datetime.fromisoformat(detected.replace("Z", "+00:00"))).total_seconds())
        if detected
        else None
    )
    return {
        "commit": commit,
        "verified_at": now.isoformat(),
        "candidate_id": candidate.name,
        "closure_digest": (policy.get("closure") or {}).get("digest"),
        "evidence_cutoff": (policy.get("closure") or {}).get("cutoff"),
        "correction_detected_at": detected,
        "correction_age_basis": policy.get("correction_age_basis", "legacy_detection_clock"),
        "correction_latency_seconds": latency,
        "latency_is_upper_bound": recovered,
    }
