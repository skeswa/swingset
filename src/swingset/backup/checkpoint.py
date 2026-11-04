from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, durable_write, fsync_dir, sha256_file
from swingset.clock import Clock, SystemClock
from swingset.publish.journal import Hub, RemoteCommit, pending_candidates
from swingset.state import engine
from swingset.state.retention import RetentionError
from swingset.state.rwlock import Held, is_lock_file


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
    connection: engine.Connection,
    destination: Path,
    *,
    schema_version: int,
    versions: dict[str, str],
    input_bundle_hash: str | None,
    control_timeout: float = 60,
) -> Checkpoint:
    """Export filtered Postgres history and its matching captured file closure."""
    from .snapshot import backup_lock, capture

    _check_database_schema(connection, schema_version)
    if destination.exists():
        raise CheckpointError(f"checkpoint destination exists: {destination}")
    temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
    temporary.mkdir(parents=True)
    try:
        with backup_lock(connection):
            history = capture(
                state_dir, connection, temporary / "database", timeout=control_timeout
            )
            from .check import check

            check(temporary / "database", manifest_sha256=history["manifest_sha256"])
            files: dict[str, dict[str, int | str]] = {
                path.relative_to(temporary).as_posix(): {
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in sorted(temporary.rglob("*"))
                if path.is_file()
            }
            metadata = history["metadata"]
            pending = metadata["pending_candidates"]
            manifest = {
                "format": "swingset-postgres-checkpoint-v1",
                "files": files,
                "baseline_candidate": metadata["baseline_candidate"],
                "pending_candidate": pending[0] if pending else None,
                "schema_version": schema_version,
                "versions": versions,
                "input_bundle_hash": input_bundle_hash,
                "history_manifest_sha256": history["manifest_sha256"],
                "runtime_identity": history["runtime_identity"],
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


def verify_checkpoint(
    checkpoint: Path, *, maximum_schema_version: int, quick: bool = False
) -> dict[str, Any]:
    """Check the exact file closure, typed COPY rows and retained schema.

    ``quick`` remains a caller compatibility flag. Both modes perform every
    native integrity check; a separate owner job proves fresh restoration.
    """
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
    from swingset.state import postgres_backup

    if manifest.get("format") != "swingset-postgres-checkpoint-v1":
        raise CheckpointError("legacy SQLite checkpoints must use the explicit importer")
    history = postgres_backup.verify(
        checkpoint / "database",
        runtime_identity=manifest["runtime_identity"],
        expected_manifest_sha256=manifest["history_manifest_sha256"],
    )
    if history["schema_version"] != manifest["schema_version"]:
        raise CheckpointError("checkpoint schema differs from its history manifest")
    for field in ("baseline_candidate", "pending_candidate"):
        candidate = manifest.get(field)
        if (
            candidate is not None
            and not (checkpoint / "database/files/candidates" / candidate / "BUILT").is_file()
        ):
            raise CheckpointError(f"{field} directory is missing or incomplete")
    return manifest


def _check_database_schema(connection: engine.Connection, declared: int) -> None:
    """A manifest cannot disguise a newer state database as an older schema."""
    actual = int(
        connection.execute("SELECT value FROM ops.meta WHERE key='schema_version'").fetchone()[0]
    )
    if actual != declared:
        raise CheckpointError(
            f"declared checkpoint schema {declared} differs from database schema {actual}"
        )


def restore_checkpoint(
    checkpoint: Path,
    state_dir: Path,
    *,
    maximum_schema_version: int,
    owner_dsn: str | None = None,
    environment: str = "dev",
) -> None:
    from swingset.state.control_lock import control_lock
    from swingset.state.rwlock import exclusive

    from .destination import owner

    if state_dir.exists() and any(path.name != "database.json" for path in state_dir.iterdir()):
        raise CheckpointError("restore target must be empty")
    state_dir.mkdir(parents=True, exist_ok=True)
    verify_checkpoint(checkpoint, maximum_schema_version=maximum_schema_version)
    with owner(state_dir, owner_dsn) as conn:
        held = exclusive(state_dir, connection=conn)
        try:
            with control_lock(state_dir, connection=conn):
                durable_write(state_dir / "RESTORE_PENDING", b"verification pending\n")
                _install_checkpoint(
                    checkpoint,
                    state_dir,
                    maximum_schema_version=maximum_schema_version,
                    connection=conn,
                    environment=environment,
                )
        finally:
            held.release()


def _install_checkpoint(
    checkpoint: Path,
    state_dir: Path,
    *,
    maximum_schema_version: int,
    connection: engine.Connection,
    environment: str = "dev",
) -> None:
    from swingset.state import postgres_backup

    from .snapshot import current_runtime_identity

    manifest = verify_checkpoint(checkpoint, maximum_schema_version=maximum_schema_version)
    # A retained image is the authority for decoding this backup. Native development
    # captures its complete code/runtime identity in the same field.
    image = current_runtime_identity()
    postgres_backup.restore(
        checkpoint / "database",
        connection,
        environment=environment,
        runtime_identity=image,
        files_target=state_dir,
        expected_manifest_sha256=manifest["history_manifest_sha256"],
    )
    retain_restore_history(state_dir, checkpoint / "database")


def restore_from_checkpoint(
    checkpoint: Path,
    state_dir: Path,
    hub: Hub,
    clock: Clock,
    *,
    lock_timeout: float,
    maximum_schema_version: int | None = None,
    owner_dsn: str | None = None,
    environment: str = "dev",
) -> None:
    from swingset.state.control_lock import control_lock
    from swingset.state.rwlock import exclusive

    from .destination import owner

    if maximum_schema_version is None:
        from swingset.state.db import SCHEMA_VERSION

        maximum_schema_version = SCHEMA_VERSION
    verify_checkpoint(checkpoint, maximum_schema_version=maximum_schema_version)
    state_dir.mkdir(parents=True, exist_ok=True)
    existing = [
        path
        for path in state_dir.iterdir()
        if path.name != "database.json" and not is_lock_file(path.name)
    ]
    if existing:
        raise CheckpointError(
            "restore target contains active or interrupted state; use a fresh database and volume"
        )
    with owner(state_dir, owner_dsn) as conn:
        held = exclusive(state_dir, connection=conn, timeout=lock_timeout, clock=clock)
        try:
            with control_lock(state_dir, connection=conn, timeout=lock_timeout):
                durable_write(state_dir / "RESTORE_PENDING", b"verification pending\n")
                _install_checkpoint(
                    checkpoint,
                    state_dir,
                    maximum_schema_version=maximum_schema_version,
                    connection=conn,
                    environment=environment,
                )
            verify_restored_public(state_dir, hub, connection=conn)
            activate_restored_state(state_dir, connection=conn, locks=held, clock=clock)
        finally:
            held.release()


def activate_restored_state(
    state_dir: Path,
    *,
    connection: engine.Connection | None = None,
    locks: Held | None = None,
    clock: Clock | None = None,
) -> None:
    """Activate verified state while the caller owns the exclusive restore locks."""
    clock = clock or SystemClock()
    marker = state_dir / "RESTORE_PENDING"
    if not marker.is_file():
        raise CheckpointError("restore marker is missing")
    # Restoring bytes is not a new check of their scheduling observations.
    # Fence these disposable hints before removing the restore barrier. A crash
    # between commit and unlink may increment twice on retry; both are safe.
    from swingset.state.control_lock import control_lock
    from swingset.state.db import Database, open_database

    if connection is None:
        with open_database(state_dir, allow_restore_pending=True) as database:
            return activate_restored_state(
                state_dir, connection=database.connection, locks=database._locks, clock=clock
            )
    from swingset.state.postgres_rebuild import bootstrap, rebuild
    from swingset.state.rwlock import exclusive

    if locks is not None and (locks.connection is not connection or locks.mode != "exclusive"):
        raise CheckpointError("restore activation requires the matching exclusive state lock")
    held = locks if locks is not None else exclusive(state_dir, connection=connection)
    try:
        database = Database(state_dir, connection, held)
        with control_lock(state_dir, connection=connection):
            with database.transaction() as conn:
                from swingset.state.controls import recover_admissions

                recover_admissions(conn, now=clock.now())
                conn.execute("UPDATE event_pressure_state SET epoch=epoch+1")
        witness = state_dir / "tmp/restore-history"
        inventory = []
        if witness.exists():
            from swingset.state import postgres_backup

            inventory = postgres_backup.verify(witness)["rebuild_units"]
        recovered = bootstrap(database, enqueued_at=clock.now().isoformat(), inventory=inventory)
        # Existing project/link anchors certify caches that the history backup
        # deliberately omitted. They must be rebuilt before activation, even
        # when the retained software and input fingerprints are unchanged.
        if recovered["materializations"]["queued_units"]:
            from swingset.state import verify
            from swingset.state.inputs import load

            selected = connection.execute(
                "SELECT value FROM ops.meta WHERE key='input_bundle_hash'"
            ).fetchone()
            if selected is None:
                raise CheckpointError("restored materializations have no retained input bundle")
            bundle = load(state_dir, str(selected[0]))
            rebuilt = rebuild(database, bundle, clock, bootstrap_cache=False, inventory=inventory)
            if not rebuilt["complete"]:
                raise CheckpointError(
                    f"restore rebuild is incomplete: {rebuilt['reason']}; use a fresh database and volume"
                )
            checked = verify.run(database, bundle, clock, str(rebuilt["run_id"]), alert=False)
            if not checked["complete"] or checked["mismatches"] or checked["failures"]:
                raise CheckpointError(
                    "restored derivations failed verification; use a fresh database and volume"
                )
        witness = state_dir / "tmp/restore-history"
        if witness.exists():
            from swingset.state import postgres_backup

            postgres_backup.audit(connection, witness, after_rebuild=True)
        marker.unlink()
        fsync_dir(state_dir)
        connection.restore_pending = False
        shutil.rmtree(witness, ignore_errors=True)
    finally:
        if locks is None:
            held.release()


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


def verify_restored_public(
    state_dir: Path, hub: Hub, *, connection: engine.Connection | None = None
) -> None:
    """Reconcile saved publication state and activate only against a stable public head."""
    if connection is None:
        from swingset.state.db import open_database

        with open_database(state_dir, allow_restore_pending=True) as database:
            return verify_restored_public(state_dir, hub, connection=database.connection)
    from swingset.state.environment import ensure_external_allowed

    ensure_external_allowed(connection, "restore public verification", hub)
    connection.check_alive()
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
    unlanded = None
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
            _restore_promote(state_dir, candidate, connection=connection)
        elif first_head == built.get("expected_parent"):
            unlanded = candidate
        elif first_head is not None:
            remote = hub.inspect(first_head)
            _verify_candidate_remote(candidate, remote)
            durable_write(receipt, canonical_json({"commit": first_head}))
            _restore_promote(state_dir, candidate, connection=connection)
        else:
            raise CheckpointError("public head is absent but restored intent expected a commit")
    elif first_head != baseline_commit:
        raise CheckpointError(
            f"public head differs from restored baseline: expected {baseline_commit!r}, actual {first_head!r}"
        )
    if hub.head() != first_head:
        raise CheckpointError("public head changed during restore verification")
    baseline_link = state_dir / "baseline"
    if baseline_link.is_symlink():
        baseline = baseline_link.resolve()
        commit = str(json.loads((baseline / "PUBLISHED").read_text())["commit"])
        _fetch_published_files(baseline, commit, hub, connection=connection)
    if hub.head() != first_head:
        raise CheckpointError("public head changed while downloading the restored baseline")
    if unlanded is not None:
        from swingset.publish.safety import reject_candidate, settle_publication
        from swingset.state.control_lock import control_lock

        with control_lock(state_dir, connection=connection):
            settle_publication(
                state_dir, unlanded, outcome="restore_not_landed", connection=connection
            )
            reject_candidate(
                unlanded, "restored intent did not land; rebuild its candidate from verified state"
            )
    # The caller removes RESTORE_PENDING only after this succeeds while holding
    # the writer lock. Keeping activation separate also makes interruption safe.


def _restore_promote(state_dir: Path, candidate: Path, *, connection: engine.Connection) -> None:
    from swingset.publish.service import _landed

    _landed(state_dir, candidate, connection, outcome="restore_receipt_promoted")


def _fetch_published_files(
    candidate: Path, commit: str, hub: Hub, *, connection: engine.Connection
) -> None:
    """Fill only the reconciled baseline's cache, pinned to its public commit."""
    from swingset.state.environment import ensure_external_allowed

    from .huggingface import safe_relative

    built = json.loads((candidate / "BUILT").read_bytes())
    manifest_path = candidate / "_meta/manifest.json"
    if sha256_file(manifest_path) != built["manifest_hash"]:
        raise CheckpointError("restored baseline manifest differs from its sealed hash")
    manifest = json.loads(manifest_path.read_bytes())
    # Validate the complete list before any network operation or file placement.
    files = []
    for name, digest in manifest["files"].items():
        relative = safe_relative(name)
        if relative.parts[0] not in {"data", "_meta"} and relative.as_posix() not in {
            "README.md",
            "LICENSE",
        }:
            raise CheckpointError("published manifest names a reserved restore path")
        if relative.as_posix() == "_meta/manifest.json":
            raise CheckpointError("published manifest cannot replace its own authority")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise CheckpointError("published manifest contains an invalid file hash")
        target = candidate / relative
        if target.is_symlink() or not target.resolve().is_relative_to(candidate.resolve()):
            raise CheckpointError("published manifest path escapes the restored candidate")
        files.append((name, digest, target))
    for name, digest, target in files:
        if target.is_file() and sha256_file(target) == digest:
            continue
        ensure_external_allowed(connection, "restore baseline download", hub)
        connection.check_alive()
        fetch = getattr(hub, "download", None)
        if fetch is None:
            raise CheckpointError("the public adapter cannot download the restored baseline")
        source = fetch(name, commit)
        if not source.is_file() or sha256_file(source) != digest:
            raise CheckpointError(f"public baseline file differs from its manifest: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name("." + target.name + ".restore-" + uuid.uuid4().hex)
        try:
            _copy_file(source, temporary)
            connection.check_alive()
            os.replace(temporary, target)
            fsync_dir(target.parent)
        finally:
            temporary.unlink(missing_ok=True)


def retain_restore_history(state_dir: Path, source: Path) -> None:
    """Keep the verified witness until activation's post-rebuild history audit."""
    import errno

    target = state_dir / "tmp/restore-history"

    def link_or_copy(origin: str, destination: str) -> str:
        try:
            os.link(origin, destination)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            shutil.copyfile(origin, destination)
        return destination

    shutil.copytree(source, target, copy_function=link_or_copy)
