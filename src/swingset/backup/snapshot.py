"""Pin database history and publication files together under the control turn."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from swingset.fetch.archive import canonical
from swingset.state import engine, postgres_backup
from swingset.state.control_lock import control_lock, holds_control_lock
from swingset.state.engine.locks import release, try_lock
from swingset.state.recipes import capture_runtime
from swingset.state.rwlock import is_lock_file


class BackupBusy(RuntimeError):
    """Another backup owns the shared job lock; publication must wait."""


@contextlib.contextmanager
def backup_lock(conn: engine.Connection) -> Iterator[None]:
    if not try_lock(conn, "backup/export"):
        raise BackupBusy("a backup is already running")
    try:
        yield
    finally:
        release(conn, "backup/export")


def current_runtime_identity() -> str:
    image = os.environ.get("SWINGSET_RUNTIME_IMAGE")
    if image:
        if "@sha256:" not in image or len(image.rsplit("@sha256:", 1)[1]) != 64:
            raise ValueError("backup runtime image must name an immutable OCI digest")
        return image
    return (
        "native:"
        + hashlib.sha256(
            canonical(
                {
                    name: hashlib.sha256(value).hexdigest()
                    for name, value in capture_runtime().items()
                }
            )
        ).hexdigest()
    )


def runtime_identity(conn: engine.Connection) -> str:
    if (
        not os.environ.get("SWINGSET_RUNTIME_IMAGE")
        and conn.execute("SELECT environment FROM ops.environment_identity").fetchone()[0]
        == "production"
    ):
        raise ValueError("production backups require SWINGSET_RUNTIME_IMAGE")
    return current_runtime_identity()


def capture(
    state: Path, writer: engine.Connection, target: Path, *, timeout: float = 60
) -> dict[str, Any]:
    if writer.in_transaction:
        raise ValueError("backup capture requires an idle writer connection")
    identity = runtime_identity(writer)
    captured = target.with_name("." + target.name + ".captured")
    captured.mkdir(parents=True)
    try:
        with engine.connect(writer.info.dsn, password=writer.info.password) as reader:
            postgres_backup.prepare(reader)
            fence = (
                contextlib.nullcontext()
                if holds_control_lock(state, connection=writer)
                else control_lock(state, connection=writer, timeout=timeout)
            )
            snapshot = reader.read_snapshot()
            with fence:
                snapshot.__enter__()
                try:
                    files: dict[str, Path] = {}
                    for source in sorted(state.rglob("*")):
                        relative = source.relative_to(state)
                        if (
                            not source.is_file()
                            or source.is_symlink()
                            or relative.parts[0]
                            in {
                                "checkpoints",
                                "gc",
                                "tmp",
                                ".cache",
                                "venv",
                                "uv-cache",
                                "config",
                                "overrides",
                            }
                        ):
                            continue
                        if (
                            source.name
                            in {
                                "database.json",
                                ".env",
                                "state.sqlite",
                                "state.sqlite-wal",
                                "state.sqlite-shm",
                                "RESTORE_PENDING",
                            }
                            or is_lock_file(source.name)
                            or any(".tmp-" in part for part in relative.parts)
                        ):
                            continue
                        if relative.parts[0] in {
                            "blobs",
                            "extracts",
                            "inputs",
                            "objects",
                            "partitions",
                        }:
                            files[relative.as_posix()] = source
                        else:
                            destination = captured / relative
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, destination)
                            files[relative.as_posix()] = destination
                    baseline = state / "baseline"
                    bundle = reader.execute(
                        "SELECT value FROM ops.meta WHERE key='input_bundle_hash'"
                    ).fetchone()
                    metadata = {
                        "input_bundle_hash": str(bundle[0]) if bundle else None,
                        "baseline_candidate": baseline.resolve().name
                        if baseline.is_symlink()
                        else None,
                        "pending_candidates": sorted(
                            p.parent.name
                            for p in state.glob("candidates/*/PUBLISHING")
                            if not (p.parent / "PUBLISHED").exists()
                        ),
                        "source_revision": os.environ.get("SWINGSET_REVISION", "native"),
                        "tool_identity": "swingset-postgres-history-v1",
                    }
                    writer.check_alive()
                except BaseException:
                    snapshot.__exit__(*sys.exc_info())
                    raise
            try:
                result = postgres_backup.export(
                    reader, target, files=files, runtime_identity=identity, metadata=metadata
                )
                writer.check_alive()
            finally:
                snapshot.__exit__(None, None, None)
        # Export now owns the copied files. Release the source path index
        # before independently decoding the complete retained manifest.
        del files
        receipt = {"manifest_sha256": result["manifest_sha256"], "bytes": result["bytes"]}
        del result
        verified = postgres_backup.verify(
            target, runtime_identity=identity, expected_manifest_sha256=receipt["manifest_sha256"]
        )
        return {**verified, **receipt}
    finally:
        shutil.rmtree(captured, ignore_errors=True)
