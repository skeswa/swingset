from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from swingset.build.files import canonical_json, durable_write, fsync_dir


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


@dataclass(frozen=True)
class PublishResult:
    state: str
    candidate_id: str | None = None
    commit: str | None = None


class PublishError(RuntimeError):
    pass


def expected_parent(state_dir: Path, hub: Hub) -> str | None:
    """Resolve a real publish parent, rejecting an already-published dataset."""
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


def _pending(state_dir: Path) -> Path | None:
    found = pending_candidates(state_dir)
    if len(found) > 1:
        raise PublishError("more than one pending publication")
    return found[0] if found else None


def _is_baseline(state_dir: Path, candidate: Path) -> bool:
    link = state_dir / "baseline"
    return link.is_symlink() and (link.parent / os.readlink(link)).resolve() == candidate.resolve()


def _promote(state_dir: Path, candidate: Path) -> None:
    relative = os.path.relpath(candidate, state_dir)
    temporary = state_dir / f".baseline.tmp-{os.getpid()}"
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    os.symlink(relative, temporary)
    os.replace(temporary, state_dir / "baseline")
    fsync_dir(state_dir)
    try:
        (candidate / "PUBLISHING").unlink()
    except FileNotFoundError:
        pass
    fsync_dir(candidate)


def _verify_remote(candidate: Path, remote: RemoteCommit, built: dict[str, object]) -> None:
    if (
        remote.candidate_id != candidate.name
        or remote.manifest_hash != built["manifest_hash"]
        or remote.parent != built.get("expected_parent")
    ):
        raise PublishError("remote commit metadata does not match pending candidate")
    manifest = json.loads((candidate / "_meta" / "manifest.json").read_text())
    expected = dict(manifest["files"])
    expected["_meta/manifest.json"] = str(built["manifest_hash"])
    for name, digest in expected.items():
        if remote.files.get(name) != digest:
            raise PublishError(f"remote file hash mismatch: {name}")


def reconcile(state_dir: Path, hub: Hub, *, dry_run: bool) -> PublishResult:
    candidate = _pending(state_dir)
    if candidate is None:
        return PublishResult("none")
    built = json.loads((candidate / "BUILT").read_text())
    receipt = candidate / "PUBLISHED"
    if receipt.is_file():
        commit = str(json.loads(receipt.read_text())["commit"])
        _promote(state_dir, candidate)
        return PublishResult("promoted", candidate.name, commit)
    head = hub.head()
    expected = built.get("expected_parent")
    if head != expected:
        if head is None:
            raise PublishError(f"public head changed: expected {expected!r}, actual None")
        try:
            remote = hub.inspect(head)
        except (KeyError, FileNotFoundError) as error:
            raise PublishError(
                f"public head changed: expected {expected!r}, actual {head!r}"
            ) from error
        if remote.parent != expected:
            raise PublishError(f"public head changed: expected {expected!r}, actual {head!r}")
        _verify_remote(candidate, remote, built)
        durable_write(receipt, canonical_json({"commit": head}))
        _promote(state_dir, candidate)
        return PublishResult("recovered", candidate.name, head)
    if dry_run:
        return PublishResult("pending", candidate.name)
    return _submit(state_dir, candidate, hub)


def _files(candidate: Path) -> dict[str, Path]:
    return {
        path.relative_to(candidate).as_posix(): path
        for path in candidate.rglob("*")
        if path.is_file() and path.name not in {"BUILT", "PUBLISHING", "PUBLISHED"}
    }


def _submit(state_dir: Path, candidate: Path, hub: Hub) -> PublishResult:
    built = json.loads((candidate / "BUILT").read_text())
    manifest = json.loads((candidate / "_meta" / "manifest.json").read_text())
    expected_files = set(manifest["files"]) | {"_meta/manifest.json"}
    baseline = state_dir / "baseline"
    old_files = set(_files(baseline.resolve())) if baseline.is_symlink() else set()
    commit = hub.create_commit(
        parent=built.get("expected_parent"),
        message=f"Publish {candidate.name} ({built['manifest_hash']})",
        additions=_files(candidate),
        deletions=tuple(sorted(old_files - expected_files)),
    )
    remote = hub.inspect(commit)
    _verify_remote(candidate, remote, built)
    durable_write(candidate / "PUBLISHED", canonical_json({"commit": commit}))
    _promote(state_dir, candidate)
    return PublishResult("published", candidate.name, commit)


def publish(state_dir: Path, candidate: Path, hub: Hub, *, dry_run: bool = False) -> PublishResult:
    if (state_dir / "RESTORE_PENDING").exists():
        raise PublishError("publication is disabled while restore verification is pending")
    recovered = reconcile(state_dir, hub, dry_run=dry_run)
    if recovered.state != "none":
        return recovered
    built_path = candidate / "BUILT"
    if not built_path.is_file():
        raise PublishError("candidate is incomplete")
    built = json.loads(built_path.read_text())
    if not bool(built["changed"]):
        return PublishResult("unchanged", candidate.name)
    baseline = state_dir / "baseline"
    baseline_commit = None
    if baseline.is_symlink():
        receipt = baseline.resolve() / "PUBLISHED"
        baseline_commit = json.loads(receipt.read_text())["commit"]
    if built.get("baseline_commit") != baseline_commit:
        raise PublishError("candidate was built against a different baseline")
    if dry_run:
        return PublishResult("dry-run", candidate.name)
    durable_write(
        candidate / "PUBLISHING",
        canonical_json(
            {
                "expected_parent": built.get("expected_parent"),
                "manifest_hash": built["manifest_hash"],
            }
        ),
    )
    return _submit(state_dir, candidate, hub)
