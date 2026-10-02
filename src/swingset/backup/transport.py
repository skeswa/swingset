"""Where a backup set goes: a local directory or the private Hugging Face archive.

A transport holds the layout a set writer leaves under `state/checkpoints/`:

    segments/<sha256>.tar              shared by every set, never rewritten
    sets/<set_id>/db/part-NNNN.zst     the database, zstd, split
    sets/<set_id>/index.jsonl.zst      one line per backed-up file
    sets/<set_id>/backup.json          the commit point, written last

`publish` copies one finished set and the new segments it names, segments
first and `backup.json` last. `HuggingFaceSegments` does it in one commit,
which also removes the sets beyond the newest `keep`. No file over
`MAX_FILE_BYTES` is ever sent, so the archive's 20 GiB file limit cannot be
met (D-0232).

`collect` is the remote collector, run after each publish: it removes every
segment no kept set's `backup.json` names. Deleting a file in a commit leaves
its bytes in the repository's history, so when anything was removed the
archive transport squashes the history to one commit, and the removed parts
and segments leave its storage (D-0234). That is why sets have a repository
of their own (`SETS_REPO`): the collector refuses, and removes nothing, while
the repository holds any path outside `OWN_PATHS`. It reports the files and bytes the
transport holds; the backup writes that to `checkpoints/remote.json`, which
the watchdog reads (`[backup-stale]`, `[archive-size]`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from swingset.build.files import fsync_dir

#: No set file or segment is larger (4 GiB).
MAX_FILE_BYTES = 4 * 1024**3
SETS = "sets"
SEGMENTS = "segments"
MANIFEST = "backup.json"
#: Remote sets kept by the archive transport, the new one included.
REMOTE_KEPT = 3
#: The private dataset repository of backup sets. It is not the full-checkpoint
#: archive (`skeswa/swingset-archive`, `backup/huggingface.py`): the collector
#: squashes this one's history, which would drop every old checkpoint commit
#: `restore --archive-commit` reads (D-0234).
SETS_REPO = "skeswa/swingset-backup-sets"
#: The only top-level paths the sets repository may hold; anything else means
#: it is not a sets-only repository, and the collector will not squash it.
OWN_PATHS = frozenset({SETS, SEGMENTS, "README.md", ".gitattributes"})

SET_ID = re.compile(r"^set_\d{8}T\d{6}Z(?:-\d+)?$")
SEGMENT_NAME = re.compile(r"^([0-9a-f]{64})\.tar$")


class TransportError(RuntimeError):
    """A set could not be published to, or read from, its transport."""


class Transport(Protocol):
    #: Recorded in `backup_objects.transport` and `backup_sets.transport`.
    name: str

    def segments(self) -> frozenset[str]:
        """The digests of the segments the transport holds."""
        ...

    def sets(self) -> list[str]:
        """Complete set ids (their `backup.json` is there), oldest first."""
        ...

    def publish(self, local: Path, set_id: str, segments: Sequence[str]) -> str | None:
        """Send one finished set from `local` (a checkpoints directory).

        `segments` are every segment the set names; those the transport
        already holds are not sent again. Returns a commit id, if any.
        """
        ...

    def fetch(self, relative: str, scratch: Path) -> Path:
        """A readable local path for one published file. Never modify it."""
        ...

    def collect(self) -> dict[str, Any]:
        """Remove segments no kept set names; report what the transport holds."""
        ...


def set_files(local: Path, set_id: str) -> list[str]:
    """A set's files relative to `local`, `backup.json` last."""
    root = local / SETS / set_id
    if not (root / MANIFEST).is_file():
        raise TransportError(f"set {set_id} has no {MANIFEST}")
    files = sorted(
        path.relative_to(local).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST
    )
    return [*files, f"{SETS}/{set_id}/{MANIFEST}"]


def segment_path(segment: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", segment):
        raise TransportError(f"not a segment digest: {segment!r}")
    return f"{SEGMENTS}/{segment}.tar"


def check_size(path: Path) -> int:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise TransportError(f"{path.name} is {size} bytes, over the {MAX_FILE_BYTES} limit")
    return size


def _relative(name: str) -> PurePosixPath:
    from swingset.backup.huggingface import safe_relative

    try:
        return safe_relative(name)
    except ValueError as error:
        raise TransportError(str(error)) from error


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".writing-{target.name}")
    with source.open("rb") as reader, temporary.open("wb") as writer:
        shutil.copyfileobj(reader, writer, 1 << 20)
        writer.flush()
        os.fsync(writer.fileno())
    os.replace(temporary, target)
    fsync_dir(target.parent)


class LocalDirectoryTransport:
    """A directory with the transport layout; the checkpoints directory itself
    in local mode, where publishing copies nothing and checks every file."""

    def __init__(self, root: Path, *, name: str | None = None, keep: int | None = None) -> None:
        self.root = Path(root)
        self.name = name or f"dir:{self.root.resolve()}"
        self.keep = keep

    def segments(self) -> frozenset[str]:
        directory = self.root / SEGMENTS
        if not directory.is_dir():
            return frozenset()
        return frozenset(
            match.group(1)
            for path in directory.iterdir()
            if (match := SEGMENT_NAME.match(path.name))
        )

    def sets(self) -> list[str]:
        directory = self.root / SETS
        if not directory.is_dir():
            return []
        return sorted(
            path.name
            for path in directory.iterdir()
            if SET_ID.match(path.name) and (path / MANIFEST).is_file()
        )

    def publish(self, local: Path, set_id: str, segments: Sequence[str]) -> str | None:
        same = self.root.resolve() == local.resolve()
        files = [segment_path(segment) for segment in sorted(set(segments))]
        files += set_files(local, set_id)
        for relative in files:
            source = local / relative
            target = self.root / relative
            if same or (relative.startswith(f"{SEGMENTS}/") and target.is_file()):
                # Same name, same bytes: a segment is named by its digest.
                if not target.is_file():
                    raise TransportError(f"{relative} is missing")
                check_size(target)
                continue
            check_size(source)
            _copy(source, target)
        if self.keep is not None and not same:
            for old in self.sets()[: -self.keep]:
                shutil.rmtree(self.root / SETS / old)
            fsync_dir(self.root / SETS)
        return None

    def fetch(self, relative: str, scratch: Path) -> Path:
        path = self.root.joinpath(*_relative(relative).parts)
        if not path.is_file():
            raise TransportError(f"{relative} is missing")
        return path

    def collect(self) -> dict[str, Any]:
        """Remove segments no kept set names. The state's own checkpoints
        are pruned by `segments.prune_sets` instead, which knows the drill."""
        if self.keep is None:
            return {"sets": len(self.sets()), "removed_segments": 0}
        needed = _named_segments(self, self.sets())
        removed = 0
        for segment in sorted(self.segments() - needed):
            (self.root / segment_path(segment)).unlink(missing_ok=True)
            removed += 1
        if removed:
            fsync_dir(self.root / SEGMENTS)
        files = [path for path in self.root.rglob("*") if path.is_file()]
        return {
            "sets": len(self.sets()),
            "segments": len(self.segments()),
            "removed_segments": removed,
            "files": len(files),
            "bytes": sum(path.stat().st_size for path in files),
        }


class HuggingFaceSegments:
    """The private sets repository (`SETS_REPO`), which the owner creates before
    upload is turned on. `api` is an `HfApi`; tests pass a fake."""

    def __init__(
        self,
        repo_id: str = SETS_REPO,
        *,
        token: str | None,
        api: Any = None,
        keep: int = REMOTE_KEPT,
    ) -> None:
        if api is None:
            from huggingface_hub import HfApi

            api = HfApi(token=token)
        self.repo_id = repo_id
        self.token = token
        self.api = api
        self.keep = keep
        self.name = f"hf:{repo_id}"

    def _files(self) -> list[str]:
        try:
            return [
                str(name) for name in self.api.list_repo_files(self.repo_id, repo_type="dataset")
            ]
        except Exception as error:
            raise TransportError(f"cannot list archive repository {self.repo_id}") from error

    def segments(self) -> frozenset[str]:
        return _segments_of(self._files())

    def sets(self) -> list[str]:
        return _sets_of(self._files())

    def publish(self, local: Path, set_id: str, segments: Sequence[str]) -> str | None:
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete

        listing = self._files()
        operations: list[Any] = []
        for segment in sorted(set(segments) - _segments_of(listing)):
            relative = segment_path(segment)
            check_size(local / relative)
            operations.append(
                CommitOperationAdd(path_in_repo=relative, path_or_fileobj=str(local / relative))
            )
        for relative in set_files(local, set_id):
            check_size(local / relative)
            operations.append(
                CommitOperationAdd(path_in_repo=relative, path_or_fileobj=str(local / relative))
            )
        existing = _sets_of(listing)
        kept = sorted({*existing, set_id})[-self.keep :]
        operations.extend(
            CommitOperationDelete(path_in_repo=f"{SETS}/{old}/", is_folder=True)
            for old in existing
            if old not in kept
        )
        try:
            result = self.api.create_commit(
                repo_id=self.repo_id,
                repo_type="dataset",
                operations=operations,
                commit_message=f"Backup set {set_id}",
            )
        except Exception as error:
            raise TransportError(f"archive commit for {set_id} failed: {error}") from error
        return str(result.oid)

    def collect(self) -> dict[str, Any]:
        """Remove unnamed segments in one commit, then squash the history so
        their bytes leave the repository's storage (D-0234)."""
        from huggingface_hub import CommitOperationDelete

        listing = self._files()
        foreign = sorted({name.split("/", 1)[0] for name in listing} - OWN_PATHS)
        if foreign:
            raise TransportError(
                f"{self.repo_id} holds {', '.join(foreign[:3])}, which no backup set wrote; "
                "squashing its history would drop them and every old commit, so nothing was "
                "collected. Sets need a repository of their own"
            )
        kept = _sets_of(listing)
        needed = _named_segments(self, kept)
        unnamed = sorted(_segments_of(listing) - needed)
        commit: str | None = None
        squashed = False
        if unnamed:
            try:
                result = self.api.create_commit(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    operations=[
                        CommitOperationDelete(path_in_repo=segment_path(segment))
                        for segment in unnamed
                    ],
                    commit_message=f"Collect {len(unnamed)} unnamed segments",
                )
                commit = str(result.oid)
                self.api.super_squash_history(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    commit_message="Backup archive: history squashed after collection",
                )
                squashed = True
            except Exception as error:
                raise TransportError(f"archive collection failed: {error}") from error
        sizes = self._sizes()
        return {
            "sets": len(kept),
            "segments": len(needed),
            "removed_segments": len(unnamed),
            "commit": commit,
            "squashed": squashed,
            "files": len(sizes),
            "bytes": sum(sizes.values()),
        }

    def _sizes(self) -> dict[str, int]:
        try:
            return {
                str(entry.path): int(getattr(entry, "size", 0) or 0)
                for entry in self.api.list_repo_tree(
                    self.repo_id, recursive=True, repo_type="dataset"
                )
                if getattr(entry, "size", None) is not None
            }
        except Exception as error:
            raise TransportError(f"cannot size archive repository {self.repo_id}") from error

    def fetch(self, relative: str, scratch: Path) -> Path:
        name = _relative(relative).as_posix()
        try:
            path = self.api.hf_hub_download(
                self.repo_id,
                name,
                repo_type="dataset",
                token=self.token,
                local_dir=scratch,
            )
        except Exception as error:
            raise TransportError(f"cannot fetch {name} from {self.repo_id}") from error
        return Path(str(path))


def _named_segments(source: Transport, sets: Sequence[str]) -> frozenset[str]:
    """Every segment the given sets' `backup.json` files name."""
    needed: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="swingset-collect-") as scratch:
        for set_id in sets:
            path = source.fetch(f"{SETS}/{set_id}/{MANIFEST}", Path(scratch))
            try:
                value = json.loads(path.read_bytes())
                needed.update(str(segment) for segment in value["segments"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise TransportError(f"set {set_id} has an unreadable {MANIFEST}") from error
    return frozenset(needed)


def _segments_of(listing: list[str]) -> frozenset[str]:
    found = set()
    for name in listing:
        parts = name.split("/")
        if len(parts) == 2 and parts[0] == SEGMENTS and (match := SEGMENT_NAME.match(parts[1])):
            found.add(match.group(1))
    return frozenset(found)


def _sets_of(listing: list[str]) -> list[str]:
    return sorted(
        parts[1]
        for name in listing
        if len(parts := name.split("/")) == 3
        and parts[0] == SETS
        and parts[2] == MANIFEST
        and SET_ID.match(parts[1])
    )
