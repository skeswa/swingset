from __future__ import annotations

from pathlib import Path
from typing import Protocol

from swingset.backup.checkpoint import Checkpoint
from swingset.state.engine import Connection
from swingset.state.environment import EnvironmentRefused, FixtureIO, ensure_external_allowed


class Archive(Protocol):
    def head(self) -> str | None: ...
    def manifest_hash(self, commit: str) -> str: ...
    def is_initial_head(self, commit: str) -> bool: ...
    def create_commit(self, *, parent: str | None, message: str, files: dict[str, Path]) -> str: ...


def upload_checkpoint(
    checkpoint: Checkpoint, archive: Archive, *, connection: Connection | None = None
) -> str | None:
    """Atomically upload a complete checkpoint; equal closures make no commit."""

    def guard() -> None:
        adapter = getattr(archive, "api", archive)
        if connection is None:
            if not isinstance(adapter, FixtureIO):
                raise EnvironmentRefused("checkpoint upload requires its live database connection")
        else:
            ensure_external_allowed(connection, "checkpoint upload", adapter)
            connection.check_alive()

    guard()
    parent = archive.head()
    if parent is not None:
        try:
            guard()
            if archive.manifest_hash(parent) == checkpoint.manifest_hash:
                return None
        except FileNotFoundError as error:
            guard()
            if not archive.is_initial_head(parent):
                raise RuntimeError(
                    "archive repository has content but no checkpoint manifest"
                ) from error
    files = {
        path.relative_to(checkpoint.path).as_posix(): path
        for path in checkpoint.path.rglob("*")
        if path.is_file()
    }
    guard()
    return archive.create_commit(
        parent=parent, message=f"Backup checkpoint {checkpoint.manifest_hash}", files=files
    )
