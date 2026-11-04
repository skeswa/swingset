"""Frozen SQLite checkpoint format for the historical storage measurement tool.

Only disposable local copies are accepted. This module has no worker opener,
remote reconciliation or activation entry point. Its closure and checkpoint
format are retained from schema 37, before the Postgres migration.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, durable_write, fsync_dir, sha256_file
from swingset.state.rwlock import is_lock_file

HOLD_FORMAT = "retention-hold-v1"


class RetentionError(RuntimeError):
    """One closure, one error.

    `backup.checkpoint.CheckpointError` is this class. The file closure moved
    here with the walk, and the checkpoint messages it raises are unchanged.
    """


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _blob_path(state_dir: Path, digest: str) -> Path:
    return state_dir / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest


def artifact_closure(
    state_dir: Path,
    connection: sqlite3.Connection,
    candidates: set[Path],
    *,
    require_present: bool = True,
) -> set[Path]:
    """Every file the database and the given candidates declare.

    `require_present` is how a backup differs from a plan. A checkpoint refuses
    to be written when a declared artifact is gone; the planner wants to report
    that instead of failing, so it asks for the reachable set and reads the
    missing ones from `missing_artifacts`.
    """
    included, _ = _artifact_closure(
        state_dir, connection, candidates, require_present=require_present
    )
    return included


def _artifact_closure(
    state_dir: Path,
    connection: sqlite3.Connection,
    candidates: set[Path],
    *,
    require_present: bool,
) -> tuple[set[Path], list[Path]]:
    included: set[Path] = set()
    missing_packs: list[Path] = []
    table_names = _tables(connection)
    if "snapshots" in table_names:
        for body, extract in connection.execute(
            "SELECT body_sha256, extract_sha256 FROM snapshots"
        ):
            if body:
                included.add(_blob_path(state_dir, str(body)))
            if extract:
                included.add(state_dir / "extracts" / str(extract))
    if "source_generations" in table_names:
        for generation_id, manifest_json in _generation_manifests(
            state_dir, connection, table_names, missing_packs
        ):
            try:
                manifest = json.loads(manifest_json)
            except (TypeError, ValueError) as exc:
                raise RetentionError(
                    f"invalid generation artifact manifest: {generation_id}"
                ) from exc
            if not isinstance(manifest, list) or any(
                not isinstance(item, dict) for item in manifest
            ):
                raise RetentionError(f"invalid generation artifact manifest: {generation_id}")
            for item in manifest:
                for field in ("body_sha256", "extract_sha256"):
                    value = item.get(field)
                    if value is None:
                        continue
                    if not _is_digest(value):
                        raise RetentionError(f"invalid generation artifact digest: {generation_id}")
                    included.add(
                        _blob_path(state_dir, value)
                        if field == "body_sha256"
                        else state_dir / "extracts" / value
                    )
    if "hosts" in table_names:
        for (body,) in connection.execute(
            "SELECT robots_sha256 FROM hosts WHERE robots_sha256 IS NOT NULL"
        ):
            included.add(_blob_path(state_dir, str(body)))
    included.update(_finding_artifacts(state_dir, connection, table_names))
    # A hold names a digest, not which kind of file it is, so whichever of the
    # two exists is the file it is holding. `add_hold` refuses a digest that is
    # neither, so this cannot quietly hold nothing.
    for digest in hold_artifacts(state_dir):
        included.update(
            path
            for path in (_blob_path(state_dir, digest), state_dir / "extracts" / digest)
            if path.is_file()
        )
    bundle_hashes: set[str] = set()
    if "meta" in table_names:
        row = connection.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
        if row and row[0]:
            bundle_hashes.add(str(row[0]))
    for candidate in candidates:
        manifest_path = candidate / "_meta" / "manifest.json"
        if manifest_path.is_file():
            value = json.loads(manifest_path.read_text()).get("input_bundle_hash")
            if value:
                bundle_hashes.add(str(value))
    missing: list[Path] = []
    for bundle_hash in bundle_hashes:
        bundle = state_dir / "inputs" / bundle_hash
        if not bundle.is_dir():
            if require_present:
                raise RetentionError(f"referenced input bundle is missing: {bundle_hash}")
            missing.append(bundle)
            continue
        included.update(path for path in bundle.rglob("*") if path.is_file())
    missing.extend(path for path in sorted(included) if not path.is_file())
    missing.extend(missing_packs)
    if missing and require_present:
        raise RetentionError(f"referenced artifact is missing: {missing[0].relative_to(state_dir)}")
    return {path.resolve() for path in included}, missing


def _generation_manifests(
    state_dir: Path, connection: sqlite3.Connection, table_names: set[str], missing: list[Path]
) -> Iterator[tuple[str, str]]:
    """Every source generation's `manifest_json`; a packed one's from its parse body.

    Rows with the same body share one pack, so each pack is read once, and
    only as far as its manifest (phase 4, WP4). A pack that cannot be read is
    reported missing, so a backup never quietly drops the files it names.
    """
    from swingset.state.body_packs import STUB, BodyUnavailable, pack_manifest
    from swingset.state.objects import Store

    for generation_id, manifest_json in connection.execute(
        "SELECT generation_id,manifest_json FROM source_generations WHERE manifest_json<>? "
        "ORDER BY generation_id",
        (STUB,),
    ):
        yield str(generation_id), manifest_json
    if "source_generation_packs" not in table_names:
        return
    store = Store(state_dir)
    for (digest,) in connection.execute(
        "SELECT DISTINCT pack_digest FROM source_generation_packs ORDER BY pack_digest"
    ):
        try:
            yield f"parse_body/{digest}", pack_manifest(store, str(digest))
        except BodyUnavailable:
            missing.append(store.path("parse_body", str(digest)))


def _finding_artifacts(
    state_dir: Path, connection: sqlite3.Connection, table_names: set[str]
) -> Iterator[Path]:
    """Files open findings rely on.

    Findings declare their support in `finding_support_references` from schema
    30 on. A checkpoint of an older schema has no such table, so the original
    scan of the evidence JSON stays as the fallback for those, and only for
    those. It is what migration 30 backfilled the table from.
    """
    if "findings" not in table_names:
        return
    if "finding_support_references" in table_names:
        for kind, digest in connection.execute(
            "SELECT r.kind,r.sha256 FROM finding_support_references r "
            "JOIN findings f USING(finding_id) WHERE f.closed_at IS NULL ORDER BY r.sha256,r.kind"
        ):
            if kind == "body":
                path = _blob_path(state_dir, str(digest))
            elif kind == "extract":
                path = state_dir / "extracts" / str(digest)
            else:
                continue
            # A declaration that names a file which is not there is reported by
            # the planner as `missing_declared_references`, not raised. Nothing
            # else declares a file the writer never checked, and one bad row
            # must not stop every backup: the pre-30 evidence scan kept only
            # files that existed, and this keeps that behaviour.
            if path.is_file():
                yield path
        return
    for (evidence_json,) in connection.execute(
        "SELECT evidence_json FROM findings WHERE closed_at IS NULL"
    ):
        for digest in declared_digests(str(evidence_json)):
            possibilities = (
                _blob_path(state_dir, digest),
                state_dir / "extracts" / digest,
            )
            yield from (path for path in possibilities if path.is_file())


def declared_digests(evidence_json: str) -> Iterator[str]:
    """Every sha256-shaped string in one finding's evidence, in no order."""
    stack: list[Any] = [json.loads(evidence_json)]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif _is_digest(value):
            yield value


def referenced_candidates(state_dir: Path) -> set[Path]:
    from swingset.publish.service import pending_candidates

    result: set[Path] = set()
    baseline = state_dir / "baseline"
    if baseline.is_symlink():
        result.add(baseline.resolve())
    if (state_dir / "candidates").exists():
        result.update(path.resolve() for path in pending_candidates(state_dir))
    return result


def hold_directory(state_dir: Path) -> Path:
    return state_dir / "holds"


def holds(state_dir: Path) -> list[dict[str, Any]]:
    """Every hold file, ordered by hold id. A malformed hold is an error.

    Only `hold_*.json` is read, because that is what `add_hold` writes. An
    operator's own note saved in the same directory is therefore not mistaken
    for a broken hold. A file that is named like a hold and cannot be read is
    still an error everywhere: a hold says what a restore with no network must
    find, and guessing at half of one would be worse than stopping. Doctor
    reports the failure and keeps reporting the rest of the state.
    """
    directory = hold_directory(state_dir)
    if not directory.is_dir():
        return []
    result = []
    for path in sorted(directory.glob("hold_*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise RetentionError(f"unreadable hold file: {path.name}; repair or remove it") from exc
        if (
            not isinstance(record, dict)
            or record.get("format") != HOLD_FORMAT
            or record.get("hold_id") != path.stem
        ):
            raise RetentionError(f"unreadable hold file: {path.name}; repair or remove it")
        result.append(record)
    return result


def hold_artifacts(state_dir: Path) -> set[str]:
    """Artifact digests the holds name, whatever kind of file they turn out to be."""
    return {
        str(digest)
        for record in holds(state_dir)
        for digest in record.get("artifact_digests") or ()
    }


EXCLUDED_TOP_LEVEL = {".cache", "venv", "uv-cache", "checkpoints", "gc"}
EXCLUDED_NAMES = {
    "state.lock",
    "control.lock",
    "state.sqlite-wal",
    "state.sqlite-shm",
    "RESTORE_PENDING",
}


@dataclass(frozen=True)
class Checkpoint:
    path: Path
    manifest_hash: str
    files: dict[str, dict[str, int | str]]


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
    """Copy a disposable SQLite database and its frozen schema-37 file closure."""
    _check_database_schema(connection, schema_version)
    if destination.exists():
        raise CheckpointError(f"checkpoint destination exists: {destination}")
    _disposable(state_dir)
    _disposable(destination)
    fence = contextlib.nullcontext()
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
            included_candidates = referenced_candidates(state_dir)
            # Controls may append while the one data writer is checkpointing.
            # Resolve every database reference from the copied point-in-time
            # database.
            snapshot = sqlite3.connect(
                f"{database.resolve().as_uri()}?mode=ro&immutable=1", uri=True
            )
            try:
                included_artifacts = artifact_closure(state_dir, snapshot, included_candidates)
            finally:
                snapshot.close()
            for source in state_dir.rglob("*"):
                if (
                    not source.is_file()
                    or source.name in EXCLUDED_NAMES
                    or (source.parent == state_dir and is_lock_file(source.name))
                ):
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


def verify_checkpoint(
    checkpoint: Path, *, maximum_schema_version: int, quick: bool = False
) -> dict[str, Any]:
    """Check a checkpoint's closure, file hashes, database and candidates.

    `quick` runs `PRAGMA quick_check` in place of `integrity_check`: it skips
    the index-content cross checks, which on a full state copy cost more than
    the rest together. A local backup verifies its new copy this way before
    older ones may go (D-0203); restore always runs the full check.
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
    connection = sqlite3.connect(
        (checkpoint / "state.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        _check_database_schema(connection, int(manifest["schema_version"]))
        integrity = connection.execute(
            "PRAGMA quick_check" if quick else "PRAGMA integrity_check"
        ).fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity is None or integrity[0] != "ok" or foreign_keys:
            raise CheckpointError("checkpoint database failed integrity checks")
        candidates = {
            checkpoint / "candidates" / str(name)
            for name in (manifest.get("baseline_candidate"), manifest.get("pending_candidate"))
            if name is not None
        }
        artifact_closure(checkpoint, connection, candidates)
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
    _disposable(checkpoint)
    _disposable(state_dir)
    if state_dir.exists() and any(state_dir.iterdir()):
        raise CheckpointError("restore target must be empty")
    state_dir.mkdir(parents=True, exist_ok=True)
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


CheckpointError = RetentionError


def _disposable(path: Path) -> None:
    if path.resolve().is_relative_to("/var/lib/swingset"):
        raise RetentionError("legacy SQLite tooling refuses the live state directory")
    if (path / "database.json").exists():
        raise RetentionError("legacy SQLite tooling refuses Postgres state settings")
