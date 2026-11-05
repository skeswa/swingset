"""Resume a fenced restore without invalidating completed replay on each turn."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, fsync_dir, sha256_file
from swingset.clock import Clock, SystemClock
from swingset.state import engine
from swingset.state.db import Database, open_database
from swingset.state.rwlock import Held, exclusive


def _progress(database: Database, binding: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from .checkpoint import CheckpointError

    key = "restore_activation:" + binding["database_id"]
    digest = hashlib.sha256(canonical_json(binding)).hexdigest()
    row = database.connection.execute(
        "SELECT summary_json FROM ops.runs WHERE run_id=%s", (key,)
    ).fetchone()
    if row is None:
        return key, {
            "operation": "restore_activation",
            "format": 1,
            "binding_sha256": digest,
            "phase": "initialized",
            "needs_rebuild": False,
            "turns": 0,
        }
    try:
        result = json.loads(row[0])
        valid = (
            isinstance(result, dict)
            and result.get("operation") == "restore_activation"
            and result.get("format") == 1
            and result.get("binding_sha256") == digest
            and result.get("phase") in {"initialized", "bootstrapped", "complete"}
            and type(result.get("needs_rebuild")) is bool
            and type(result.get("turns")) is int
            and result["turns"] >= 0
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise CheckpointError("restore progress is invalid or belongs to different recovery inputs")
    return key, result


def _save(database: Database, key: str, progress: dict[str, Any], clock: Clock) -> None:
    # The phase follows its domain commits in the same database's WAL. A file
    # cursor could survive a database rollback and incorrectly skip bootstrap.
    # Each turn is a closed operational record, so stale-run recovery cannot
    # replace the resumable summary between turns. The fresh database UUID
    # prevents changing any run retained from the original backup.
    now = clock.now().isoformat()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO ops.runs(run_id,started_at,finished_at,dry_run,summary_json) "
            "VALUES(%s,%s,%s,1,%s) ON CONFLICT(run_id) DO UPDATE SET "
            "finished_at=excluded.finished_at,summary_json=excluded.summary_json",
            (key, now, now, canonical_json(progress).decode()),
        )


def activate(
    state_dir: Path,
    *,
    connection: engine.Connection | None = None,
    locks: Held | None = None,
    clock: Clock | None = None,
    seconds: float = 540,
    max_units: int = 100_000,
    allow_partial: bool = False,
    stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Run one recovery turn; a partial turn always leaves RESTORE_PENDING.

    Callers reconcile the public head before entering this function. Ordinary
    workers remain fenced between turns. Per-unit deadlines are unchanged.
    The default retains the original fail-closed single-turn API.
    """
    from swingset.state import postgres_backup, postgres_rebuild, verify
    from swingset.state.control_lock import control_lock
    from swingset.state.environment import identity
    from swingset.state.inputs import load

    from .checkpoint import CheckpointError
    from .snapshot import current_runtime_identity

    if not math.isfinite(seconds) or seconds <= 0 or type(max_units) is not int or max_units < 1:
        raise ValueError("restore activation requires positive finite turn bounds")
    clock = clock or SystemClock()
    marker = state_dir / "RESTORE_PENDING"
    if not marker.is_file():
        raise CheckpointError("restore marker is missing")
    if connection is None:
        with open_database(state_dir, allow_restore_pending=True) as database:
            return activate(
                state_dir,
                connection=database.connection,
                locks=database._locks,
                clock=clock,
                seconds=seconds,
                max_units=max_units,
                allow_partial=allow_partial,
                stop=stop,
            )
    if locks is not None and (locks.connection is not connection or locks.mode != "exclusive"):
        raise CheckpointError("restore activation requires the matching exclusive state lock")
    held = locks if locks is not None else exclusive(state_dir, connection=connection)
    try:
        database = Database(state_dir, connection, held)
        witness = state_dir / "tmp/restore-history"
        manifest = postgres_backup.verify(witness) if witness.exists() else None
        inventory = manifest["rebuild_units"] if manifest is not None else []
        selected = connection.execute(
            "SELECT value FROM ops.meta WHERE key='input_bundle_hash'"
        ).fetchone()
        bundle_hash = str(selected[0]) if selected is not None else None
        binding = {
            "database_id": identity(connection).database_id,
            "history": sha256_file(witness / postgres_backup.MANIFEST) if manifest else None,
            "input_bundle_hash": bundle_hash,
            "runtime": current_runtime_identity(),
        }
        key, progress = _progress(database, binding)
        if stop():
            return {"job": key, "complete": False, "reason": "rebuild_stopped"}
        with control_lock(state_dir, connection=connection):
            with database.transaction() as transaction:
                from swingset.state.controls import recover_admissions

                recover_admissions(transaction, now=clock.now())
                if progress["phase"] == "initialized":
                    transaction.execute("UPDATE event_pressure_state SET epoch=epoch+1")
        if progress["phase"] == "initialized":
            recovered = postgres_rebuild.bootstrap(
                database, enqueued_at=clock.now().isoformat(), inventory=inventory
            )
            progress.update(
                phase="bootstrapped",
                needs_rebuild=bool(recovered["materializations"]["queued_units"]),
            )
            _save(database, key, progress, clock)
        progress["turns"] += 1
        result: dict[str, Any] = {"job": key, "turn": progress["turns"]}
        if progress["phase"] != "complete" and progress["needs_rebuild"]:
            if bundle_hash is None:
                raise CheckpointError("restored materializations have no retained input bundle")
            options: dict[str, Any] = {
                "seconds": seconds,
                "max_units": max_units,
                "bootstrap_cache": False,
                "inventory": inventory,
                "stop": stop,
            }
            if allow_partial:
                options["resume_source"] = True
            rebuilt = postgres_rebuild.rebuild(
                database, load(state_dir, bundle_hash), clock, **options
            )
            with database.transaction() as transaction:
                transaction.execute(
                    "UPDATE ops.runs SET finished_at=%s,summary_json=%s WHERE run_id=%s "
                    "AND finished_at IS NULL",
                    (clock.now().isoformat(), canonical_json(rebuilt).decode(), rebuilt["run_id"]),
                )
            progress["last_rebuild"] = {
                name: rebuilt.get(name)
                for name in ("run_id", "reason", "processed_units", "remaining_units", "seconds")
            }
            _save(database, key, progress, clock)
            result["rebuild"] = rebuilt
            if not rebuilt["complete"]:
                if allow_partial and rebuilt["reason"] in {
                    "rebuild_budget_exhausted",
                    "rebuild_stopped",
                }:
                    return {**result, "complete": False, "reason": rebuilt["reason"]}
                raise CheckpointError(f"restore rebuild is incomplete: {rebuilt['reason']}")
            checked = verify.run(
                database, load(state_dir, bundle_hash), clock, rebuilt["run_id"], alert=False
            )
            if not checked["complete"] or checked["mismatches"] or checked["failures"]:
                raise CheckpointError("restored derivations failed verification")
        if manifest is not None:
            postgres_backup.audit(connection, witness, after_rebuild=True)
        progress.update(phase="complete", needs_rebuild=False)
        _save(database, key, progress, clock)
        marker.unlink()
        fsync_dir(state_dir)
        connection.restore_pending = False
        shutil.rmtree(witness, ignore_errors=True)
        return {**result, "complete": True, "history_equal": True}
    finally:
        if locks is None:
            held.release()
