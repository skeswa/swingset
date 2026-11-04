"""Publication metadata shared by builds and retention readers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from swingset.state.db import open_database
from swingset.state.engine import Connection
from swingset.state.environment import ensure_external_allowed


@dataclass(frozen=True)
class RemoteCommit:
    commit: str
    parent: str | None
    candidate_id: str
    manifest_hash: str
    files: dict[str, str]


class Hub(Protocol):
    def head(self) -> str | None: ...
    def is_initial_head(self, commit: str) -> bool: ...
    def inspect(self, commit: str) -> RemoteCommit: ...
    def create_commit(
        self,
        *,
        parent: str | None,
        message: str,
        additions: dict[str, Path],
        deletions: tuple[str, ...],
    ) -> str: ...


class PublishError(RuntimeError):
    pass


def expected_parent(
    state_dir: Path, hub: Hub, *, connection: Connection | None = None
) -> str | None:
    """Resolve a real publish parent, rejecting an already-published dataset."""
    if connection is None:
        with open_database(state_dir) as database:
            return expected_parent(state_dir, hub, connection=database.connection)
    ensure_external_allowed(connection, "publication reads", hub)
    baseline = state_dir / "baseline"
    if baseline.is_symlink():
        return str(json.loads((baseline.resolve() / "PUBLISHED").read_text())["commit"])
    head = hub.head()
    if head is None:
        return None
    if hub.is_initial_head(head):
        return head
    raise PublishError("public repository is not empty; restore it or review its unrelated head")


def pending_candidates(state_dir: Path) -> list[Path]:
    """Return active intents, ignoring receipts from ancestors of the baseline."""
    candidates = state_dir / "candidates"
    if not candidates.exists():
        return []
    baseline_link = state_dir / "baseline"
    baseline = baseline_link.resolve() if baseline_link.is_symlink() else None
    baseline_commit = None
    if baseline is not None:
        baseline_commit = str(json.loads((baseline / "PUBLISHED").read_text())["commit"])
    found = []
    for path in candidates.iterdir():
        if not (path / "PUBLISHING").is_file() or _is_baseline(state_dir, path):
            continue
        receipt = path / "PUBLISHED"
        if receipt.is_file() and baseline_commit is not None:
            built = json.loads((path / "BUILT").read_text())
            if built.get("expected_parent") != baseline_commit:
                continue
        found.append(path)
    return found


def _is_baseline(state_dir: Path, candidate: Path) -> bool:
    link = state_dir / "baseline"
    return link.is_symlink() and (link.parent / os.readlink(link)).resolve() == candidate.resolve()
