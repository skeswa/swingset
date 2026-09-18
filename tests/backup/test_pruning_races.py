"""Pruning reads a directory a backup is writing, and must not fail on it.

Nothing locks `state/checkpoints`: a backup renames its temporary directory
into place and removes old ones while doctor is sizing them. These tests pin
the behavior that follows from that -- an entry that goes away is skipped, a
removal that cannot finish says what did go -- and the manifest shortcut that
keeps a report from stat-ing every file of a full state copy.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from swingset.backup import pruning
from swingset.backup.pruning import (
    PruneError,
    PrunePolicy,
    describe,
    plan_prune,
    prune,
    remove_named,
)

DAY = 86400.0
NOW = 1_800_000_000.0


def checkpoint(
    root: Path, name: str, *, age: float, complete: bool = True, files: int = 1, size: int = 10
) -> Path:
    """A checkpoint directory, with a manifest that really describes its files."""
    path = root / name
    (path / "blobs").mkdir(parents=True)
    for index in range(files):
        (path / "blobs" / f"f{index}").write_bytes(b"x" * size)
    if complete:
        manifest = {
            "format": 1,
            "files": {
                f"blobs/f{index}": {"size": size, "sha256": "0" * 64} for index in range(files)
            },
        }
        (path / "checkpoint.json").write_text(json.dumps(manifest))
        os.utime(path / "checkpoint.json", (NOW - age, NOW - age))
    os.utime(path, (NOW - age, NOW - age))
    return path


def vanishing(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Make `target` exist for its first stat and be gone for every later one.

    That is the real window: `is_file()` says yes, and the stat that follows
    lands after a rename or an rmtree has taken the file away.
    """
    real = Path.stat
    seen = [0]

    def flaky(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == target:
            seen[0] += 1
            if seen[0] > 1:
                raise FileNotFoundError(2, "No such file or directory", str(self))
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", flaky)


def test_a_file_that_vanishes_under_the_walk_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = checkpoint(
        tmp_path, ".run_20260901T040000Z.tmp-a", age=2 * DAY, complete=False, files=3
    )
    vanishing(monkeypatch, stale / "blobs" / "f1")
    plan = plan_prune(tmp_path, now=NOW, policy=PrunePolicy())
    # Two of the three files, instead of an error the caller cannot act on.
    assert [entry.bytes for entry in plan.remove] == [20]


def test_an_unreadable_checkpoint_root_plans_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint(tmp_path, "run_20260901T040000Z", age=40 * DAY)

    def refuse(self: Path) -> object:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "iterdir", refuse)
    assert plan_prune(tmp_path, now=NOW, policy=PrunePolicy()) == pruning.PrunePlan((), ())


def test_a_checkpoint_removed_by_someone_else_still_counts_as_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = checkpoint(tmp_path, "run_20260901T040000Z", age=40 * DAY)
    checkpoint(tmp_path, "run_20260911T040000Z", age=0)
    real = shutil.rmtree

    def gone(path: object, *args: object, **kwargs: object) -> None:
        real(old)
        raise FileNotFoundError(2, "No such file or directory", str(path))

    monkeypatch.setattr(pruning.shutil, "rmtree", gone)
    plan = prune(tmp_path, now=NOW, policy=PrunePolicy(keep_recent=1, max_age_seconds=DAY))
    assert [entry.path for entry in plan.remove] == [old] and not old.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_removal_that_cannot_finish_says_what_did_go(tmp_path: Path) -> None:
    blocked = checkpoint(tmp_path, "run_20260901T040000Z", age=40 * DAY)
    removable = checkpoint(tmp_path, "run_20260902T040000Z", age=40 * DAY)
    checkpoint(tmp_path, "run_20260911T040000Z", age=0)
    os.chmod(blocked / "blobs", 0o500)
    try:
        with pytest.raises(PruneError) as error:
            prune(tmp_path, now=NOW, policy=PrunePolicy(keep_recent=1, max_age_seconds=DAY))
    finally:
        os.chmod(blocked / "blobs", 0o700)
    assert [entry.path for entry in error.value.removed] == [removable]
    assert not removable.exists() and blocked.is_dir()
    assert "run_20260901T040000Z" in str(error.value)


def test_remove_named_reports_a_removal_it_cannot_finish(tmp_path: Path) -> None:
    named = checkpoint(tmp_path, "h16-published-20260917", age=DAY)
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    os.chmod(named / "blobs", 0o500)
    try:
        with pytest.raises(PruneError, match="could not remove h16-published-20260917"):
            remove_named(tmp_path, "h16-published-20260917")
    finally:
        os.chmod(named / "blobs", 0o700)
    assert named.is_dir()


def test_a_complete_checkpoint_is_sized_from_its_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor sizes a full state copy without one stat per file."""
    kept = checkpoint(tmp_path, "run_20260911T040000Z", age=0, files=4, size=10)

    def never(path: Path) -> int:
        raise AssertionError(f"walked {path}")

    monkeypatch.setattr(pruning, "_tree_bytes", never)
    (report,) = describe(tmp_path, now=NOW, policy=PrunePolicy())
    manifest = (kept / "checkpoint.json").stat().st_size
    assert report.bytes == 4 * 10 + manifest


def test_a_checkpoint_policy_would_remove_is_also_sized_from_its_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The directories that dominate the cost are the ones policy would remove.

    Doctor plans before it reports, so a plan that walked what it would remove
    would walk most of the directory: the worker held seventeen checkpoints on
    2026-09-18 and `doctor --watch` measures every five seconds.
    """
    old = checkpoint(tmp_path, "run_20260901T040000Z", age=40 * DAY, files=4, size=10)
    checkpoint(tmp_path, "run_20260911T040000Z", age=0, files=4, size=10)
    checkpoint(tmp_path, "run_20260912T040000Z", age=0, files=4, size=10)

    def never(path: Path) -> int:
        raise AssertionError(f"walked {path}")

    monkeypatch.setattr(pruning, "_tree_bytes", never)
    plan = plan_prune(tmp_path, now=NOW, policy=PrunePolicy())
    assert [entry.path for entry in plan.remove] == [old]
    manifest = (old / "checkpoint.json").stat().st_size
    assert plan.reclaimable_bytes == 4 * 10 + manifest
    report = {item.name: item for item in describe(tmp_path, now=NOW, policy=PrunePolicy())}
    assert report[old.name].prunes and report[old.name].bytes == 4 * 10 + manifest


def test_a_plan_already_worked_out_is_applied_without_planning_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sizing a full state copy twice per backup is a pass over the disk for nothing."""
    old = checkpoint(tmp_path, "run_20260901T040000Z", age=40 * DAY)
    checkpoint(tmp_path, "run_20260911T040000Z", age=0)
    plan = plan_prune(tmp_path, now=NOW, policy=PrunePolicy(keep_recent=1, max_age_seconds=DAY))

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("planned again")

    monkeypatch.setattr(pruning, "plan_prune", never)
    assert pruning.remove_planned(tmp_path, plan) is plan
    assert not old.exists()


def test_a_checkpoint_without_a_usable_manifest_is_walked(tmp_path: Path) -> None:
    kept = checkpoint(tmp_path, "run_20260911T040000Z", age=0, files=2, size=10)
    (kept / "checkpoint.json").write_text("{}")
    (report,) = describe(tmp_path, now=NOW, policy=PrunePolicy())
    assert report.bytes == 2 * 10 + 2


def test_the_report_says_which_kept_checkpoint_the_next_backup_takes(tmp_path: Path) -> None:
    for day in (1, 2, 3):
        checkpoint(tmp_path, f"run_2026090{day}T040000Z", age=40 * DAY)
    reasons = {
        report.name: report.reason for report in describe(tmp_path, now=NOW, policy=PrunePolicy())
    }
    assert reasons["run_20260903T040000Z"] == "the newest timer checkpoint"
    # An operator reading only `prunes` would think this one is safe; it is
    # kept only until the next backup's own checkpoint takes its slot.
    assert reasons["run_20260902T040000Z"].endswith("until the next backup takes a slot")
    assert reasons["run_20260901T040000Z"] == "timer checkpoint outside the kept window"
