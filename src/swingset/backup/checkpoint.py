from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, durable_write, fsync_dir, sha256_file
from swingset.clock import Clock
from swingset.publish.service import Hub, RemoteCommit, pending_candidates
from swingset.state.retention import RetentionError
from swingset.state.retention import artifact_closure as _artifact_closure
from swingset.state.retention import referenced_candidates as _referenced_candidates


@dataclass(frozen=True)
class Checkpoint:
    path: Path
    manifest_hash: str
    files: dict[str, dict[str, int | str]]


# The file closure moved to state/retention.py, where the one retention walk
# lives, so a checkpoint and a removal plan cannot disagree about which files are
# declared. One closure raises one error: this name is that error, so every
# existing caller and message is unchanged.
CheckpointError = RetentionError

EXCLUDED_TOP_LEVEL = {".cache", "venv", "uv-cache", "checkpoints", "gc"}
EXCLUDED_NAMES = {
    "state.lock",
    "control.lock",
    "state.sqlite-wal",
    "state.sqlite-shm",
    "RESTORE_PENDING",
}


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file)
        output_file.flush()
        os.fsync(output_file.fileno())


def _candidate_allowed(path: Path, included: set[Path]) -> bool:
    parts = path.parts
    if "candidates" not in parts:
        return True
    index = parts.index("candidates")
    return Path(*parts[: index + 2]).resolve() in included


def create_checkpoint(
    state_dir: Path,
    connection: sqlite3.Connection,
    destination: Path,
    *,
    schema_version: int,
    versions: dict[str, str],
    input_bundle_hash: str | None,
    control_timeout: float = 60,
) -> Checkpoint:
    """Copy the database and every file its closure declares.

    The file closure and the copy run under the control lock, unless the caller
    already holds it for a wider operation. A hold is one file written under that
    lock after its contents are checked, so without it a hold could be committed
    between this closure and this copy, and the checkpoint would carry the hold
    but not the file it holds. Files a hold names are filtered by `is_file()`, so
    nothing would report the gap. The documented order is state.lock (which the
    backup command already owns), then control.lock, then SQLite, and never while
    a SQLite write transaction is open; the database copy below happens before
    the lock for that reason.
    """
    from swingset.state.control_lock import control_lock, holds_control_lock

    _check_database_schema(connection, schema_version)
    if destination.exists():
        raise CheckpointError(f"checkpoint destination exists: {destination}")
    # An operator tool that already fenced a whole operation with the control
    # lock keeps its own fence; the mutex is not re-entrant.
    fence: contextlib.AbstractContextManager[None] = (
        contextlib.nullcontext()
        if holds_control_lock(state_dir)
        else control_lock(state_dir, timeout=control_timeout)
    )
    temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
    temporary.mkdir(parents=True)
    try:
        database = temporary / "state.sqlite"
        copied = sqlite3.connect(database)
        try:
            connection.backup(copied)
            copied.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            copied.close()
        with fence:
            included_candidates = _referenced_candidates(state_dir)
            # Controls may append while the one data writer is checkpointing.
            # Resolve every database reference from the copied point-in-time
            # database.
            snapshot = sqlite3.connect(
                f"{database.resolve().as_uri()}?mode=ro&immutable=1", uri=True
            )
            try:
                included_artifacts = _artifact_closure(state_dir, snapshot, included_candidates)
            finally:
                snapshot.close()
            for source in state_dir.rglob("*"):
                if not source.is_file() or source.name in EXCLUDED_NAMES:
                    continue
                relative = source.relative_to(state_dir)
                if relative == Path("state.sqlite") or relative.parts[0] in EXCLUDED_TOP_LEVEL:
                    continue
                if relative.parts[0] == "candidates" and not _candidate_allowed(
                    source, included_candidates
                ):
                    continue
                if (
                    relative.parts[0] in {"blobs", "extracts", "inputs"}
                    and source.resolve() not in included_artifacts
                ):
                    continue
                if any(part.startswith(".") and ".tmp-" in part for part in relative.parts):
                    continue
                _copy_file(source, temporary / relative)
        files: dict[str, dict[str, int | str]] = {}
        for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
            relative_name = path.relative_to(temporary).as_posix()
            files[relative_name] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
        baseline = state_dir / "baseline"
        baseline_target = baseline.resolve().name if baseline.is_symlink() else None
        pending = [
            path.name
            for path in included_candidates
            if (path / "PUBLISHING").exists() and path.name != baseline_target
        ]
        manifest: dict[str, Any] = {
            "format": 1,
            "files": files,
            "baseline_candidate": baseline_target,
            "pending_candidate": pending[0] if pending else None,
            "schema_version": schema_version,
            "versions": versions,
            "input_bundle_hash": input_bundle_hash,
        }
        durable_write(temporary / "checkpoint.json", canonical_json(manifest))
        manifest_hash = sha256_file(temporary / "checkpoint.json")
        fsync_dir(temporary)
        os.replace(temporary, destination)
        fsync_dir(destination.parent)
        return Checkpoint(destination, manifest_hash, files)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def verify_checkpoint(checkpoint: Path, *, maximum_schema_version: int) -> dict[str, Any]:
    manifest_path = checkpoint / "checkpoint.json"
    if not manifest_path.is_file():
        raise CheckpointError("checkpoint manifest is missing")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())
    if int(manifest["schema_version"]) > maximum_schema_version:
        raise CheckpointError("checkpoint schema is newer than this program")
    expected = dict(manifest["files"])
    actual = {
        path.relative_to(checkpoint).as_posix()
        for path in checkpoint.rglob("*")
        if path.is_file() and path != checkpoint / "checkpoint.json"
    }
    if actual != set(expected):
        missing = set(expected) - actual
        extra = actual - set(expected)
        raise CheckpointError(
            f"checkpoint closure differs: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    for relative, record in expected.items():
        path = checkpoint / relative
        if path.stat().st_size != int(record["size"]) or sha256_file(path) != record["sha256"]:
            raise CheckpointError(f"checkpoint file failed verification: {relative}")
    connection = sqlite3.connect(
        (checkpoint / "state.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        _check_database_schema(connection, int(manifest["schema_version"]))
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity is None or integrity[0] != "ok" or foreign_keys:
            raise CheckpointError("checkpoint database failed integrity checks")
        candidates = {
            checkpoint / "candidates" / str(name)
            for name in (manifest.get("baseline_candidate"), manifest.get("pending_candidate"))
            if name is not None
        }
        _artifact_closure(checkpoint, connection, candidates)
    finally:
        connection.close()
    for field in ("baseline_candidate", "pending_candidate"):
        candidate = manifest.get(field)
        if (
            candidate is not None
            and not (checkpoint / "candidates" / candidate / "BUILT").is_file()
        ):
            raise CheckpointError(f"{field} directory is missing or incomplete")
    return manifest


def _check_database_schema(connection: sqlite3.Connection, declared: int) -> None:
    """A manifest cannot disguise a newer state database as an older schema."""
    actual = int(connection.execute("PRAGMA user_version").fetchone()[0])
    has_meta = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    metadata = (
        connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if has_meta
        else None
    )
    if actual and actual != declared:
        raise CheckpointError(
            f"declared checkpoint schema {declared} differs from database schema {actual}"
        )
    if metadata is not None and int(metadata[0]) != declared:
        raise CheckpointError("checkpoint schema differs from database metadata")


def restore_checkpoint(checkpoint: Path, state_dir: Path, *, maximum_schema_version: int) -> None:
    from swingset.state.control_lock import control_lock

    if state_dir.exists() and any(state_dir.iterdir()):
        raise CheckpointError("restore target must be empty")
    state_dir.mkdir(parents=True, exist_ok=True)
    with control_lock(state_dir):
        durable_write(state_dir / "RESTORE_PENDING", b"verification pending\n")
        _install_checkpoint(checkpoint, state_dir, maximum_schema_version=maximum_schema_version)


def _install_checkpoint(checkpoint: Path, state_dir: Path, *, maximum_schema_version: int) -> None:
    manifest = verify_checkpoint(checkpoint, maximum_schema_version=maximum_schema_version)
    for source in checkpoint.rglob("*"):
        if source.is_file() and source != checkpoint / "checkpoint.json":
            _copy_file(source, state_dir / source.relative_to(checkpoint))
    baseline = manifest.get("baseline_candidate")
    if baseline is not None:
        os.symlink(Path("candidates") / str(baseline), state_dir / "baseline")
    # Public-head reconciliation must complete before activation. Keeping the marker
    # makes an interrupted or locally verified restore safe by default.
    fsync_dir(state_dir)


def restore_from_checkpoint(
    checkpoint: Path,
    state_dir: Path,
    hub: Hub,
    clock: Clock,
    *,
    lock_timeout: float,
    maximum_schema_version: int | None = None,
) -> None:
    """Install, remotely verify, and activate a checkpoint under the writer lock."""
    from swingset.state.control_lock import control_lock

    if maximum_schema_version is None:
        from swingset.state.db import SCHEMA_VERSION

        maximum_schema_version = SCHEMA_VERSION
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "state.lock"
    lock_file = lock_path.open("a+b")
    started = clock.now()
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if (clock.now() - started).total_seconds() >= lock_timeout:
                    raise CheckpointError(
                        f"state writer lock timed out after {lock_timeout:g} seconds"
                    ) from error
                clock.sleep(0.1)
        with control_lock(state_dir, timeout=lock_timeout):
            marker = state_dir / "RESTORE_PENDING"
            existing = [
                path
                for path in state_dir.iterdir()
                if path.name not in {"state.lock", "control.lock"}
            ]
            if existing and not marker.is_file():
                raise CheckpointError("restore target contains an active or unmarked state")
            if marker.is_file():
                for path in existing:
                    if path == marker:
                        continue
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            else:
                durable_write(marker, b"verification pending\n")
            _install_checkpoint(
                checkpoint, state_dir, maximum_schema_version=maximum_schema_version
            )
            verify_restored_public(state_dir, hub)
            activate_restored_state(state_dir)
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def activate_restored_state(state_dir: Path) -> None:
    """Activate verified state while the caller owns the exclusive restore locks."""
    marker = state_dir / "RESTORE_PENDING"
    if not marker.is_file():
        raise CheckpointError("restore marker is missing")
    # Restoring bytes is not a new check of their scheduling observations.
    # Fence these disposable hints before removing the restore barrier. A crash
    # between commit and unlink may increment twice on retry; both are safe.
    connection = sqlite3.connect(
        (state_dir / "state.sqlite").resolve().as_uri() + "?mode=rw", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_pressure_state'"
            ).fetchone():
                from swingset.state.controls import recover_admissions

                connection.execute("BEGIN IMMEDIATE")
                # Restored workers cannot still own these admissions. Preserve
                # publication uncertainty while releasing the stale write fence.
                recover_admissions(connection, now=datetime.now(UTC))
                connection.execute("UPDATE event_pressure_state SET epoch=epoch+1")
    finally:
        connection.close()
    marker.unlink()
    fsync_dir(state_dir)


def _verify_candidate_remote(candidate: Path, remote: RemoteCommit) -> None:
    built = json.loads((candidate / "BUILT").read_text())
    manifest = json.loads((candidate / "_meta" / "manifest.json").read_text())
    if (
        remote.candidate_id != candidate.name
        or remote.manifest_hash != built["manifest_hash"]
        or remote.parent != built.get("expected_parent")
    ):
        raise CheckpointError("public commit does not match restored candidate metadata")
    for name, digest in manifest["files"].items():
        if remote.files.get(name) != digest:
            raise CheckpointError(f"public commit file differs from restored candidate: {name}")


def verify_restored_public(state_dir: Path, hub: Hub) -> None:
    """Reconcile saved publication state and activate only against a stable public head."""
    marker = state_dir / "RESTORE_PENDING"
    if not marker.is_file():
        raise CheckpointError("restore marker is missing")
    baseline_link = state_dir / "baseline"
    baseline_path = baseline_link.resolve() if baseline_link.is_symlink() else None
    pending = pending_candidates(state_dir)
    if len(pending) > 1:
        raise CheckpointError("restored checkpoint has multiple pending candidates")
    baseline = baseline_path
    baseline_commit = (
        str(json.loads((baseline / "PUBLISHED").read_text())["commit"])
        if baseline is not None
        else None
    )
    first_head = hub.head()
    if pending:
        candidate = pending[0]
        built = json.loads((candidate / "BUILT").read_text())
        receipt = candidate / "PUBLISHED"
        if receipt.is_file():
            commit = str(json.loads(receipt.read_text())["commit"])
            if first_head != commit:
                raise CheckpointError(
                    f"public head differs from pending receipt: expected {commit!r}, actual {first_head!r}"
                )
            _verify_candidate_remote(candidate, hub.inspect(commit))
            _restore_promote(state_dir, candidate)
        elif first_head == built.get("expected_parent"):
            pass  # Intent did not land; the next real cycle may retry it.
        elif first_head is not None:
            remote = hub.inspect(first_head)
            _verify_candidate_remote(candidate, remote)
            durable_write(receipt, canonical_json({"commit": first_head}))
            _restore_promote(state_dir, candidate)
        else:
            raise CheckpointError("public head is absent but restored intent expected a commit")
    elif first_head != baseline_commit:
        raise CheckpointError(
            f"public head differs from restored baseline: expected {baseline_commit!r}, actual {first_head!r}"
        )
    if hub.head() != first_head:
        raise CheckpointError("public head changed during restore verification")
    # The caller removes RESTORE_PENDING only after this succeeds while holding
    # the writer lock. Keeping activation separate also makes interruption safe.


def _restore_promote(state_dir: Path, candidate: Path) -> None:
    temporary = state_dir / f".baseline.restore-{uuid.uuid4().hex}"
    os.symlink(Path("candidates") / candidate.name, temporary)
    os.replace(temporary, state_dir / "baseline")
    fsync_dir(state_dir)
    try:
        (candidate / "PUBLISHING").unlink()
    except FileNotFoundError:
        pass
    fsync_dir(candidate)
