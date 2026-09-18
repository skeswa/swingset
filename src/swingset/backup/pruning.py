"""Remove local checkpoints that nothing needs.

The backup timer writes one complete checkpoint per run under
``state/checkpoints/run_<timestamp>``. Each is a full copy of the state, so
keeping every one fills the disk within days. Pruning keeps the newest few
timer checkpoints and anything recent, removes the rest, and clears the
temporary directories an interrupted backup leaves behind.

Policy removes only what the machine wrote: a ``run_*`` checkpoint, or a
``.<name>.tmp-<hex>`` directory from an interrupted copy. Every other name is
an operator's, and is removed only by name, finished or not, because the gate
a hold protects runs for a length nobody knows in advance.

Nothing here holds a lock, and a backup writing, renaming or removing a
checkpoint runs at the same time as a reader. Every walk therefore skips an
entry that goes away under it rather than failing the caller: a checkpoint
that is not there needs no decision, and a report is not worth an error.

Sizes come from ``checkpoint.json``, which records a size for every file, so
neither a plan nor a report stats hundreds of thousands of files. Only a
directory with no usable manifest is walked.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from swingset.build.files import fsync_dir

TIMER_NAME = re.compile(r"^run_\d{8}T\d{6}Z(?:-\d+)?$")
# `create_checkpoint` copies into `.<name>.tmp-<uuid hex>` and renames.
TEMPORARY_NAME = re.compile(r"^\..+\.tmp-[0-9a-f]+$")


class PruneError(RuntimeError):
    """Something could not be removed. `removed` is what did go first."""

    def __init__(self, message: str, *, removed: tuple[PruneEntry, ...] = ()) -> None:
        super().__init__(message)
        self.removed = removed


@dataclass(frozen=True)
class PrunePolicy:
    """How many timer checkpoints stay, and how young a checkpoint must be to stay."""

    keep_recent: int = 2
    max_age_seconds: float = 2 * 86400
    incomplete_max_age_seconds: float = 86400

    def __post_init__(self) -> None:
        if self.keep_recent < 1:
            raise ValueError("keep_recent must keep at least one checkpoint")
        if self.max_age_seconds < 0 or self.incomplete_max_age_seconds < 0:
            raise ValueError("ages must be nonnegative")


@dataclass(frozen=True)
class PruneEntry:
    path: Path
    reason: str
    bytes: int


@dataclass(frozen=True)
class PrunePlan:
    remove: tuple[PruneEntry, ...]
    keep: tuple[PruneEntry, ...]

    @property
    def reclaimable_bytes(self) -> int:
        return sum(entry.bytes for entry in self.remove)


def _tree_bytes(path: Path) -> int:
    """Add up every file under `path`, skipping whatever disappears mid-walk."""
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _manifest_bytes(path: Path) -> int | None:
    """The size of a complete checkpoint, read from the manifest that lists every file.

    Returns None when there is no usable manifest, so the caller walks instead.
    A full copy of the state is hundreds of thousands of files; one read beats
    one stat per file, and `verify_checkpoint` already checks these sizes
    against the bytes on disk.
    """
    try:
        body = (path / "checkpoint.json").read_bytes()
        files = json.loads(body)["files"]
        total = sum(int(record["size"]) for record in files.values())
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return total + len(body)


def _size(path: Path) -> int:
    """How big a checkpoint is: from its manifest, or by walking when there is none.

    Every caller wants the same answer, so nothing walks a directory that
    carries a manifest -- not a plan, not a report. A full state copy is
    hundreds of thousands of files, and the worker holds more than a dozen.
    """
    total = _manifest_bytes(path)
    return _tree_bytes(path) if total is None else total


def _machine_written(name: str) -> bool:
    """True for the two names the code writes: a timer run, and its temporary copy."""
    return bool(TIMER_NAME.match(name) or TEMPORARY_NAME.match(name))


def _is_incomplete(path: Path) -> bool:
    return not (path / "checkpoint.json").is_file()


def _written_at(path: Path) -> float:
    manifest = path / "checkpoint.json"
    return (manifest if manifest.is_file() else path).stat().st_mtime


def plan_prune(checkpoints: Path, *, now: float, policy: PrunePolicy) -> PrunePlan:
    """Decide what to remove without touching anything."""
    remove: list[PruneEntry] = []
    keep: list[PruneEntry] = []
    try:
        if not checkpoints.is_dir():
            return PrunePlan((), ())
        entries = sorted(checkpoints.iterdir())
    except OSError:
        return PrunePlan((), ())
    timer: list[tuple[Path, float]] = []
    for path in entries:
        try:
            if not path.is_dir() or path.is_symlink():
                continue
            temporary = bool(TEMPORARY_NAME.match(path.name))
            # A temporary copy is abandoned whether or not it got as far as its
            # manifest: `create_checkpoint` writes that inside the copy and
            # renames afterwards, so a crash in between leaves a complete one.
            if temporary or _is_incomplete(path):
                if not _machine_written(path.name):
                    # A half-copied hold is still an operator's directory, and
                    # a timer must not decide it is rubbish. Doctor shows it as
                    # incomplete so somebody removes it by name.
                    keep.append(
                        PruneEntry(path, "unfinished operator-named copy; remove by name", 0)
                    )
                    continue
                if now - _written_at(path) >= policy.incomplete_max_age_seconds:
                    reason = (
                        "abandoned temporary copy older than the limit"
                        if temporary
                        else "incomplete checkpoint older than the limit"
                    )
                    remove.append(PruneEntry(path, reason, _size(path)))
                else:
                    keep.append(PruneEntry(path, "unfinished copy still within the limit", 0))
                continue
            if TIMER_NAME.match(path.name):
                timer.append((path, _written_at(path)))
            else:
                keep.append(PruneEntry(path, "operator-named checkpoint; remove by name", 0))
        except OSError:
            continue
    timer.sort(key=lambda item: (item[0].name, item[1]), reverse=True)
    for index, (path, written) in enumerate(timer):
        if index < policy.keep_recent:
            # The next backup writes a checkpoint that takes one of these
            # slots, so all but the newest are kept only until then.
            reason = (
                "the newest timer checkpoint"
                if index == 0
                else f"one of the newest {policy.keep_recent}, until the next backup takes a slot"
            )
            keep.append(PruneEntry(path, reason, 0))
        elif now - written < policy.max_age_seconds:
            keep.append(PruneEntry(path, "younger than the age limit", 0))
        else:
            remove.append(PruneEntry(path, "timer checkpoint outside the kept window", _size(path)))
    return PrunePlan(tuple(remove), tuple(keep))


def remove_planned(checkpoints: Path, plan: PrunePlan) -> PrunePlan:
    """Remove exactly what `plan` names and return it.

    A caller that has already worked out a plan passes it here rather than
    having one worked out again: planning sizes what it would remove, and
    doing that twice per backup is two passes over the directory for nothing.
    """
    removed: list[PruneEntry] = []
    failed: list[str] = []
    for entry in plan.remove:
        try:
            shutil.rmtree(entry.path)
        except FileNotFoundError:
            removed.append(entry)
        except OSError as error:
            failed.append(f"{entry.path.name}: {error}")
        else:
            removed.append(entry)
    if removed:
        fsync_dir(checkpoints)
    if failed:
        raise PruneError("could not remove " + "; ".join(failed), removed=tuple(removed))
    return plan


def prune(checkpoints: Path, *, now: float, policy: PrunePolicy) -> PrunePlan:
    """Work out a plan and remove what it names.

    A directory that is already gone counts as removed. Anything else that
    cannot be removed raises `PruneError` carrying what did go, so a caller
    can still report the reclaimed space.
    """
    return remove_planned(checkpoints, plan_prune(checkpoints, now=now, policy=policy))


@dataclass(frozen=True)
class CheckpointReport:
    """One checkpoint directory as doctor shows it."""

    name: str
    complete: bool
    age_days: float
    bytes: int
    prunes: bool
    reason: str


def describe(checkpoints: Path, *, now: float, policy: PrunePolicy) -> tuple[CheckpointReport, ...]:
    """Say what is there and what policy would do with it. Removes nothing.

    A report is for a person deciding what to remove by name, so it measures
    every directory -- from its manifest, and by walking only a directory that
    has no usable one.

    `prunes` is what policy says now. The next backup adds a checkpoint of its
    own, which takes one of the kept slots; the reasons say which entries that
    moves.
    """
    plan = plan_prune(checkpoints, now=now, policy=policy)
    reports = []
    for prunes, entries in ((True, plan.remove), (False, plan.keep)):
        for entry in entries:
            try:
                written = _written_at(entry.path)
            except OSError:
                continue
            reports.append(
                CheckpointReport(
                    name=entry.path.name,
                    complete=not _is_incomplete(entry.path),
                    age_days=round(max(now - written, 0.0) / 86400, 3),
                    bytes=entry.bytes or _size(entry.path),
                    prunes=prunes,
                    reason=entry.reason,
                )
            )
    return tuple(sorted(reports, key=lambda report: report.name))


def remove_named(checkpoints: Path, name: str) -> Path:
    """Remove one operator-named checkpoint by its exact directory name."""
    if not name or "/" in name or name in {".", ".."}:
        raise PruneError(f"invalid checkpoint name: {name!r}")
    if TIMER_NAME.match(name):
        raise PruneError("timer checkpoints are pruned by policy, not by name")
    path = checkpoints / name
    if not path.is_dir() or path.is_symlink():
        raise PruneError(f"named checkpoint is missing: {name}")
    try:
        shutil.rmtree(path)
        fsync_dir(checkpoints)
    except OSError as error:
        raise PruneError(f"could not remove {name}: {error}") from error
    return path
