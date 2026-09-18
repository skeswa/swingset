from __future__ import annotations

import os
from pathlib import Path

import pytest

from swingset.backup.pruning import PruneError, PrunePolicy, plan_prune, prune, remove_named

DAY = 86400.0
NOW = 1_800_000_000.0


def checkpoint(root: Path, name: str, *, age: float, complete: bool = True, size: int = 10) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "state.sqlite").write_bytes(b"x" * size)
    if complete:
        (path / "checkpoint.json").write_text("{}")
        os.utime(path / "checkpoint.json", (NOW - age, NOW - age))
    os.utime(path, (NOW - age, NOW - age))
    return path


def test_policy_keeps_newest_timer_checkpoints_and_recent_ones(tmp_path: Path) -> None:
    old = [checkpoint(tmp_path, f"run_2026090{i}T040000Z", age=(10 - i) * DAY) for i in range(1, 6)]
    young = checkpoint(tmp_path, "run_20260910T040000Z", age=DAY / 2)
    newest = checkpoint(tmp_path, "run_20260911T040000Z", age=DAY / 4)
    plan = plan_prune(tmp_path, now=NOW, policy=PrunePolicy(keep_recent=2, max_age_seconds=2 * DAY))
    removed = {entry.path for entry in plan.remove}
    assert removed == set(old)
    assert {entry.path for entry in plan.keep} == {young, newest}
    assert plan.reclaimable_bytes == sum(10 + 2 for _ in old)


def test_young_timer_checkpoints_beyond_keep_recent_stay(tmp_path: Path) -> None:
    kept = [
        checkpoint(tmp_path, f"run_2026091{i}T040000Z", age=(3 - i) * DAY / 4) for i in range(3)
    ]
    plan = plan_prune(tmp_path, now=NOW, policy=PrunePolicy(keep_recent=1, max_age_seconds=DAY))
    assert plan.remove == ()
    assert {entry.path for entry in plan.keep} == set(kept)


def test_named_checkpoints_are_never_pruned_by_policy(tmp_path: Path) -> None:
    named = checkpoint(tmp_path, "extension28-held-20260917-004", age=40 * DAY)
    checkpoint(tmp_path, "run_20260901T040000Z", age=40 * DAY)
    plan = prune(tmp_path, now=NOW, policy=PrunePolicy(keep_recent=1, max_age_seconds=DAY))
    assert named.is_dir()
    assert [entry.path.name for entry in plan.remove] == []
    reasons = {entry.path: entry.reason for entry in plan.keep}
    assert "remove by name" in reasons[named]


def test_the_newest_timer_checkpoint_is_always_kept(tmp_path: Path) -> None:
    only = checkpoint(tmp_path, "run_20260901T040000Z", age=400 * DAY)
    plan = prune(tmp_path, now=NOW, policy=PrunePolicy(keep_recent=1, max_age_seconds=0))
    assert only.is_dir() and plan.remove == ()


def test_incomplete_directories_are_removed_only_after_their_limit(tmp_path: Path) -> None:
    stale = checkpoint(tmp_path, ".run_20260901T040000Z.tmp-abc", age=2 * DAY, complete=False)
    fresh = checkpoint(tmp_path, ".run_20260911T040000Z.tmp-def", age=60, complete=False)
    partial = checkpoint(tmp_path, "run_20260902T040000Z", age=3 * DAY, complete=False)
    plan = prune(tmp_path, now=NOW, policy=PrunePolicy(incomplete_max_age_seconds=DAY))
    assert {entry.path for entry in plan.remove} == {stale, partial}
    assert not stale.exists() and not partial.exists() and fresh.is_dir()


def test_a_temporary_copy_goes_even_when_it_reached_its_manifest(tmp_path: Path) -> None:
    """`create_checkpoint` writes the manifest inside the copy and renames after.

    A crash in that window leaves a complete `.<name>.tmp-<hex>` directory that
    no rename will ever claim, so it goes by the same limit as a half-written
    one.
    """
    finished = checkpoint(tmp_path, ".run_20260901T040000Z.tmp-abc", age=2 * DAY)
    checkpoint(tmp_path, "run_20260911T040000Z", age=0)
    plan = prune(tmp_path, now=NOW, policy=PrunePolicy(incomplete_max_age_seconds=DAY))
    assert [entry.path for entry in plan.remove] == [finished]
    assert not finished.exists()


def test_an_unfinished_operator_named_copy_is_kept_however_old(tmp_path: Path) -> None:
    """A half-copied hold is an operator's directory, so only a name removes it.

    An interrupted `cp -a` into `checkpoints/` leaves no `checkpoint.json`. The
    backup timer runs three times a day unattended, and removing an operator's
    directory by age is the rule D-0133 rejected.
    """
    staged = checkpoint(tmp_path, "h17-before-rollout", age=30 * DAY, complete=False)
    checkpoint(tmp_path, "run_20260911T040000Z", age=0)
    plan = prune(tmp_path, now=NOW, policy=PrunePolicy(incomplete_max_age_seconds=DAY))
    assert plan.remove == () and staged.is_dir()
    reasons = {entry.path: entry.reason for entry in plan.keep}
    assert "remove by name" in reasons[staged]
    # And a name does remove it, finished or not.
    assert remove_named(tmp_path, "h17-before-rollout") == staged
    assert not staged.exists()


def test_plan_is_read_only_and_prune_removes_exactly_the_plan(tmp_path: Path) -> None:
    old = checkpoint(tmp_path, "run_20260901T040000Z", age=30 * DAY)
    new = checkpoint(tmp_path, "run_20260911T040000Z", age=0)
    policy = PrunePolicy(keep_recent=1, max_age_seconds=DAY)
    planned = plan_prune(tmp_path, now=NOW, policy=policy)
    assert old.is_dir()
    applied = prune(tmp_path, now=NOW, policy=policy)
    assert applied.remove == planned.remove
    assert not old.exists() and new.is_dir()


def test_missing_directory_and_symlinks_are_ignored(tmp_path: Path) -> None:
    assert plan_prune(tmp_path / "none", now=NOW, policy=PrunePolicy()).remove == ()
    real = checkpoint(tmp_path / "elsewhere", "run_20260901T040000Z", age=30 * DAY)
    root = tmp_path / "checkpoints"
    root.mkdir()
    (root / "run_20260902T040000Z").symlink_to(real)
    plan = prune(root, now=NOW, policy=PrunePolicy(keep_recent=1, max_age_seconds=0))
    assert plan.remove == () and real.is_dir()


def test_remove_named_refuses_timer_names_and_missing_entries(tmp_path: Path) -> None:
    named = checkpoint(tmp_path, "h16-published-20260917", age=DAY)
    checkpoint(tmp_path, "run_20260911T040000Z", age=0)
    with pytest.raises(PruneError):
        remove_named(tmp_path, "run_20260911T040000Z")
    with pytest.raises(PruneError):
        remove_named(tmp_path, "absent")
    with pytest.raises(PruneError):
        remove_named(tmp_path, "../h16-published-20260917")
    assert remove_named(tmp_path, "h16-published-20260917") == named
    assert not named.exists()


def test_policy_rejects_keeping_nothing() -> None:
    with pytest.raises(ValueError):
        PrunePolicy(keep_recent=0)
