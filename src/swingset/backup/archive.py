from __future__ import annotations

from pathlib import Path
from typing import Protocol

from swingset.backup.checkpoint import Checkpoint


class Archive(Protocol):
    def head(self) -> str | None: ...
    def manifest_hash(self, commit: str) -> str: ...
    def create_commit(self, *, parent: str | None, message: str, files: dict[str, Path]) -> str: ...


def upload_checkpoint(checkpoint: Checkpoint, archive: Archive) -> str | None:
    """Atomically upload a complete checkpoint; equal closures make no commit."""
    parent = archive.head()
    if parent is not None and archive.manifest_hash(parent) == checkpoint.manifest_hash:
        return None
    files = {
        path.relative_to(checkpoint.path).as_posix(): path
        for path in checkpoint.path.rglob("*")
        if path.is_file()
    }
    return archive.create_commit(
        parent=parent, message=f"Backup checkpoint {checkpoint.manifest_hash}", files=files
    )
