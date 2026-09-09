from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, durable_write, fsync_dir, sha256_file
from swingset.clock import Clock
from swingset.publish.service import Hub, RemoteCommit


@dataclass(frozen=True)
class Checkpoint:
    path: Path
    manifest_hash: str
    files: dict[str, dict[str, int | str]]


class CheckpointError(RuntimeError):
    pass


EXCLUDED_TOP_LEVEL = {"venv", "uv-cache", "checkpoints"}
EXCLUDED_NAMES = {"state.lock", "state.sqlite-wal", "state.sqlite-shm", "RESTORE_PENDING"}


def _artifact_closure(
    state_dir: Path, connection: sqlite3.Connection, candidates: set[Path]
) -> set[Path]:
    included: set[Path] = set()
    table_names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "snapshots" in table_names:
        for body, extract in connection.execute(
            "SELECT body_sha256, extract_sha256 FROM snapshots"
        ):
            if body:
                digest = str(body)
                included.add(state_dir / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest)
            if extract:
                included.add(state_dir / "extracts" / str(extract))
    if "hosts" in table_names:
        for (body,) in connection.execute(
            "SELECT robots_sha256 FROM hosts WHERE robots_sha256 IS NOT NULL"
        ):
            digest = str(body)
            included.add(
                state_dir / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest
            )
    if "findings" in table_names:
        for (evidence_json,) in connection.execute(
            "SELECT evidence_json FROM findings WHERE closed_at IS NULL"
        ):
            stack = [json.loads(str(evidence_json))]
            while stack:
                value = stack.pop()
                if isinstance(value, dict):
                    stack.extend(value.values())
                elif isinstance(value, list):
                    stack.extend(value)
                elif (
                    isinstance(value, str)
                    and len(value) == 64
                    and all(char in "0123456789abcdef" for char in value)
                ):
                    digest = value
                    possibilities = (
                        state_dir / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest,
                        state_dir / "extracts" / digest,
                    )
                    included.update(path for path in possibilities if path.is_file())
    bundle_hashes: set[str] = set()
    if "meta" in table_names:
        row = connection.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
        if row and row[0]:
            bundle_hashes.add(str(row[0]))
    for candidate in candidates:
        manifest = candidate / "_meta" / "manifest.json"
        if manifest.is_file():
            value = json.loads(manifest.read_text()).get("input_bundle_hash")
            if value:
                bundle_hashes.add(str(value))
    for bundle_hash in bundle_hashes:
        bundle = state_dir / "inputs" / bundle_hash
        if not bundle.is_dir():
            raise CheckpointError(f"referenced input bundle is missing: {bundle_hash}")
        included.update(path for path in bundle.rglob("*") if path.is_file())
    missing = [path for path in included if not path.is_file()]
    if missing:
        raise CheckpointError(
            f"referenced artifact is missing: {missing[0].relative_to(state_dir)}"
        )
    return {path.resolve() for path in included}


def _referenced_candidates(state_dir: Path) -> set[Path]:
    result: set[Path] = set()
    baseline = state_dir / "baseline"
    if baseline.is_symlink():
        result.add(baseline.resolve())
    candidates = state_dir / "candidates"
    if candidates.exists():
        result.update(
            path.resolve() for path in candidates.iterdir() if (path / "PUBLISHING").is_file()
        )
    return result


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
) -> Checkpoint:
    if destination.exists():
        raise CheckpointError(f"checkpoint destination exists: {destination}")
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
        included_candidates = _referenced_candidates(state_dir)
        included_artifacts = _artifact_closure(state_dir, connection, included_candidates)
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
        if path.is_file() and path.name != "checkpoint.json"
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
    connection = sqlite3.connect(checkpoint / "state.sqlite")
    try:
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


def restore_checkpoint(checkpoint: Path, state_dir: Path, *, maximum_schema_version: int) -> None:
    if state_dir.exists() and any(state_dir.iterdir()):
        raise CheckpointError("restore target must be empty")
    state_dir.mkdir(parents=True, exist_ok=True)
    durable_write(state_dir / "RESTORE_PENDING", b"verification pending\n")
    _install_checkpoint(checkpoint, state_dir, maximum_schema_version=maximum_schema_version)


def _install_checkpoint(checkpoint: Path, state_dir: Path, *, maximum_schema_version: int) -> None:
    manifest = verify_checkpoint(checkpoint, maximum_schema_version=maximum_schema_version)
    for source in checkpoint.rglob("*"):
        if source.is_file() and source.name != "checkpoint.json":
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
    maximum_schema_version: int = 1,
) -> None:
    """Install, remotely verify, and activate a checkpoint under the writer lock."""
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
        marker = state_dir / "RESTORE_PENDING"
        existing = [path for path in state_dir.iterdir() if path != lock_path]
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
        _install_checkpoint(checkpoint, state_dir, maximum_schema_version=maximum_schema_version)
        verify_restored_public(state_dir, hub)
        activate_restored_state(state_dir)
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def activate_restored_state(state_dir: Path) -> None:
    marker = state_dir / "RESTORE_PENDING"
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
    candidates = state_dir / "candidates"
    baseline_link = state_dir / "baseline"
    baseline_path = baseline_link.resolve() if baseline_link.is_symlink() else None
    candidate_paths = candidates.iterdir() if candidates.exists() else ()
    pending = [
        path
        for path in candidate_paths
        if (path / "PUBLISHING").is_file()
        and (baseline_path is None or path.resolve() != baseline_path)
    ]
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


def garbage_collect(state_dir: Path, *, older_than: float, now: float) -> list[Path]:
    """Prune old orphans while retaining reference closure and five dry candidates."""
    removed: list[Path] = []
    candidates = state_dir / "candidates"
    if not candidates.exists():
        return removed
    retained = _referenced_candidates(state_dir)
    disposable = sorted(
        (
            path
            for path in candidates.iterdir()
            if (path / "BUILT").is_file() and path.resolve() not in retained
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    retained.update(path.resolve() for path in disposable[:5])
    for candidate in candidates.iterdir():
        if candidate.resolve() in retained:
            continue
        if now - candidate.stat().st_mtime < older_than:
            continue
        shutil.rmtree(candidate)
        removed.append(candidate)
    database_path = state_dir / "state.sqlite"
    if database_path.is_file():
        connection = sqlite3.connect(database_path)
        try:
            referenced = _artifact_closure(state_dir, connection, retained)
        finally:
            connection.close()
        for top_level in ("blobs", "extracts", "inputs"):
            root = state_dir / top_level
            if not root.exists():
                continue
            for artifact in (path for path in root.rglob("*") if path.is_file()):
                if artifact.resolve() in referenced or now - artifact.stat().st_mtime < older_than:
                    continue
                artifact.unlink()
                removed.append(artifact)
            for directory in sorted(
                (path for path in root.rglob("*") if path.is_dir()), reverse=True
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
    if removed:
        fsync_dir(candidates)
    return removed
