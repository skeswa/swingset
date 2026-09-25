"""The backup command prunes its own checkpoints, and doctor lists them.

Pruning itself is covered by `test_pruning.py`. These tests are about the
wiring: a backup prunes after it has written its checkpoint, `--no-prune`
skips it, `--remove-checkpoint` removes one named checkpoint and makes no
backup, and doctor reports every checkpoint without removing anything.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest

import swingset.cli as cli
from swingset.backup.pruning import TIMER_NAME
from swingset.cli import FAILED_CHECKPOINT_PREFIX, main

DAY = 86400.0


def checkpoint(
    root: Path, name: str, *, age_days: float, complete: bool = True, files: int = 0
) -> Path:
    """A checkpoint directory of the shape the pruner reads, aged by its mtime."""
    path = root / name
    path.mkdir(parents=True)
    (path / "state.sqlite").write_bytes(b"x" * 16)
    for index in range(files):
        (path / "blobs").mkdir(exist_ok=True)
        (path / "blobs" / f"f{index}").write_bytes(b"x" * 8)
    when = time.time() - age_days * DAY
    if complete:
        (path / "checkpoint.json").write_text("{}")
        os.utime(path / "checkpoint.json", (when, when))
    os.utime(path, (when, when))
    return path


def config_with(tmp_path: Path, retention: str) -> list[str]:
    """The repository config with a `[retention]` table appended."""
    directory = tmp_path / "config"
    directory.mkdir(exist_ok=True)
    (directory / "hosts.toml").write_bytes(Path("config/hosts.toml").read_bytes())
    # The shipped configuration already carries a [retention] table (D-0186);
    # the test's values replace it rather than declare a second one.
    shipped = Path("config/sources.toml").read_text()
    shipped = shipped[: shipped.index("[retention]")]
    (directory / "sources.toml").write_bytes(
        shipped.encode() + f"\n[retention]\n{retention}".encode()
    )
    return ["--config", str(directory), "--overrides", "overrides"]


def defaults(tmp_path: Path) -> list[str]:
    """The repository config with the code's default checkpoint policy.

    Production keeps one checkpoint while backups are local only (D-0203);
    these tests pin the defaults, which keep two.
    """
    return config_with(tmp_path, "")


def backup(state: Path, *flags: str) -> int:
    return main(["backup", "--local", "--state", str(state), *defaults(state.parent), *flags])


def field(line: str, key: str) -> object:
    """One `key=<json>` field of a log line, whose values may contain spaces."""
    match = re.search(rf"(?:^| ){key}=(.*?)(?= [a-z_]+=|$)", line)
    assert match is not None, line
    return json.loads(match.group(1))


def test_backup_prunes_old_timer_checkpoints_and_keeps_the_one_it_wrote(tmp_path: Path) -> None:
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    old = [checkpoint(checkpoints, f"run_2024010{day}T040000Z", age_days=30) for day in (1, 2, 3)]
    assert backup(state) == 0
    written = [
        path
        for path in checkpoints.iterdir()
        if path.is_dir() and (path / "checkpoint.json").is_file() and path not in old
    ]
    assert len(written) == 1, sorted(path.name for path in checkpoints.iterdir())
    # The default policy keeps the newest two, which are the new checkpoint and
    # the newest old one; the other two are past the age limit and go.
    assert not old[0].exists() and not old[1].exists()
    assert old[2].is_dir() and written[0].is_dir()


def test_no_prune_keeps_every_checkpoint(tmp_path: Path) -> None:
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    old = [checkpoint(checkpoints, f"run_2024010{day}T040000Z", age_days=30) for day in (1, 2, 3)]
    stale = checkpoint(checkpoints, ".run_20240104T040000Z.tmp-abc", age_days=30, complete=False)
    assert backup(state, "--no-prune") == 0
    assert all(path.is_dir() for path in [*old, stale])


def test_the_policy_comes_from_the_retention_table(tmp_path: Path) -> None:
    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    young = [checkpoint(checkpoints, f"run_2026010{day}T040000Z", age_days=0.01) for day in (1, 2)]
    assert main(["backup", "--local", "--state", str(state), *config]) == 0
    # The default policy would keep both: they are the newest two and hours old.
    assert not any(path.exists() for path in young)


def test_pruning_never_removes_the_checkpoint_this_run_wrote(tmp_path: Path) -> None:
    """A `--destination` reusing an old timer name is the one case policy could eat."""
    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    newer = [checkpoint(checkpoints, f"run_2026010{day}T040000Z", age_days=30) for day in (1, 2)]
    destination = checkpoints / "run_20240101T040000Z"
    assert (
        main(
            [
                "backup",
                "--local",
                "--state",
                str(state),
                *config,
                "--destination",
                str(destination),
            ]
        )
        == 0
    )
    # Policy named this run's own checkpoint, so nothing was pruned at all.
    assert (destination / "checkpoint.json").is_file()
    assert all(path.is_dir() for path in newer)


def test_remove_checkpoint_removes_one_named_checkpoint_and_makes_no_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    named = checkpoint(checkpoints, "h16-published-20260917", age_days=40)
    timer = checkpoint(checkpoints, "run_20240101T040000Z", age_days=40)
    assert (
        main(["backup", "--remove-checkpoint", "h16-published-20260917", "--state", str(state)])
        == 0
    )
    assert capsys.readouterr().out.strip() == str(named)
    assert not named.exists()
    # No new checkpoint, no state database, no run: the command only removes.
    assert [path.name for path in checkpoints.iterdir()] == [timer.name]
    assert not (state / "state.sqlite").exists()


def test_remove_checkpoint_refuses_a_timer_name_and_a_missing_one(tmp_path: Path) -> None:
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    timer = checkpoint(checkpoints, "run_20240101T040000Z", age_days=40)
    assert (
        main(["backup", "--remove-checkpoint", "run_20240101T040000Z", "--state", str(state)]) == 1
    )
    assert timer.is_dir()
    assert main(["backup", "--remove-checkpoint", "absent", "--state", str(state)]) == 1
    assert main(["backup", "--remove-checkpoint", "../escape", "--state", str(state)]) == 1
    # A name that exists, so this fails on the combination and not on the name.
    named = checkpoint(checkpoints, "h16-published-20260917", age_days=40)
    assert (
        main(
            [
                "backup",
                "--local",
                "--remove-checkpoint",
                "h16-published-20260917",
                "--state",
                str(state),
                *defaults(tmp_path),
            ]
        )
        == 1
    )
    assert named.is_dir()
    # An unset shell variable is an empty name: an error, never a backup.
    assert (
        main(["backup", "--remove-checkpoint", "", "--state", str(state), *defaults(tmp_path)]) == 1
    )
    assert not (state / "state.sqlite").exists()


def test_doctor_reports_every_checkpoint_and_removes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    old = checkpoint(checkpoints, "run_20240101T040000Z", age_days=30)
    kept = checkpoint(checkpoints, "run_20250101T040000Z", age_days=30)
    fresh = checkpoint(checkpoints, "run_20260918T040000Z", age_days=0.25)
    named = checkpoint(checkpoints, "h16-published-20260917", age_days=40)
    incomplete = checkpoint(checkpoints, ".run_20240102T040000Z.tmp-a", age_days=30, complete=False)
    assert main(["doctor", "--json", "--state", str(state), *defaults(tmp_path)]) == 0
    report = {entry["name"]: entry for entry in json.loads(capsys.readouterr().out)["checkpoints"]}
    assert set(report) == {path.name for path in (old, kept, fresh, named, incomplete)}
    assert not report[kept.name]["prunes"]
    assert report[old.name]["prunes"] and report[incomplete.name]["prunes"]
    assert not report[fresh.name]["prunes"] and not report[named.name]["prunes"]
    assert report[named.name]["complete"] and not report[incomplete.name]["complete"]
    assert report[named.name]["bytes"] == 18 and report[named.name]["age_days"] == pytest.approx(
        40, abs=0.01
    )
    assert all(path.is_dir() for path in (old, kept, fresh, named, incomplete))


def test_the_prune_log_says_what_went_and_what_stayed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """That log line is the operator's only record of what the timer removed."""
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    checkpoint(checkpoints, "run_20240101T040000Z", age_days=30)
    checkpoint(checkpoints, "run_20240102T040000Z", age_days=30)
    assert backup(state) == 0
    pruned = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith('event="checkpoints-pruned"')
    ]
    assert len(pruned) == 1
    assert [row["name"] for row in field(pruned[0], "removed")] == ["run_20240101T040000Z"]
    assert {row["name"] for row in field(pruned[0], "kept")} >= {"run_20240102T040000Z"}
    assert field(pruned[0], "reclaimed_bytes") == 18


def test_the_skip_path_says_it_pruned_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    destination = state / "checkpoints" / "run_20240101T040000Z"
    checkpoint(state / "checkpoints", "run_20260101T040000Z", age_days=30)
    argv = ["backup", "--local", "--state", str(state), *config, "--destination", str(destination)]
    assert main(argv) == 0
    err = capsys.readouterr().err
    assert 'event="checkpoint-prune-skipped"' in err
    assert 'event="checkpoints-pruned"' not in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_removal_that_fails_does_not_fail_a_backup_that_worked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cleanup after a finished backup is logged, never turned into a failure.

    The unit restarts on failure, so a backup reported as failed after it wrote
    and uploaded its checkpoint means a fresh full copy every minute.
    """
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    blocked = checkpoint(checkpoints, "run_20240101T040000Z", age_days=30, files=1)
    removable = checkpoint(checkpoints, "run_20240102T040000Z", age_days=30)
    kept = checkpoint(checkpoints, "run_20240103T040000Z", age_days=30)
    os.chmod(blocked / "blobs", 0o500)
    try:
        assert backup(state) == 0
    finally:
        os.chmod(blocked / "blobs", 0o700)
    err = capsys.readouterr().err
    assert 'event="checkpoint-prune-failed"' in err
    assert '"run_20240102T040000Z"' in err
    assert blocked.is_dir() and kept.is_dir() and not removable.exists()
    # The backup itself stands: its checkpoint is there and the run did not fail.
    assert (state / "state.sqlite").exists()
    assert 'event="error"' not in err


def test_doctor_survives_a_file_that_vanishes_under_its_walk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup renaming or removing a directory must not fail the health check."""
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    stale = checkpoint(
        checkpoints, ".run_20240102T040000Z.tmp-a", age_days=30, complete=False, files=3
    )
    kept = checkpoint(checkpoints, "run_20260918T040000Z", age_days=0.25)
    target = stale / "blobs" / "f1"
    real = Path.stat
    seen = [0]

    def flaky(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == target:
            seen[0] += 1
            if seen[0] > 1:  # is_file() says yes, then the file is gone
                raise FileNotFoundError(2, "No such file or directory", str(self))
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", flaky)
    assert main(["doctor", "--json", "--state", str(state), *defaults(tmp_path)]) == 0
    report = {entry["name"]: entry for entry in json.loads(capsys.readouterr().out)["checkpoints"]}
    assert set(report) == {stale.name, kept.name}
    assert report[stale.name]["bytes"] == 16 + 2 * 8  # the two files still there


def test_the_timer_never_removes_an_operator_named_directory(tmp_path: Path) -> None:
    """Policy removes only what the code wrote, finished or not.

    An interrupted `cp -a` of a hold leaves no `checkpoint.json`, and the disk
    filling up is exactly when that copy is interrupted. The backup timer runs
    three times a day unattended, so it must not decide that directory is
    rubbish; an operator removes it by name.
    """
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    staged = checkpoint(checkpoints, "h17-before-rollout-20260915", age_days=3, complete=False)
    held = checkpoint(checkpoints, "h16-published-20260917", age_days=40)
    stale = checkpoint(checkpoints, ".run_20240104T040000Z.tmp-abc", age_days=30, complete=False)
    assert backup(state) == 0
    assert staged.is_dir() and held.is_dir()
    # The temporary directory of an interrupted backup is the code's own, and goes.
    assert not stale.exists()
    # And a name removes the unfinished hold, which nothing else does.
    assert main(["backup", "--remove-checkpoint", staged.name, "--state", str(state)]) == 0
    assert not staged.exists()


def test_remove_checkpoint_refuses_while_a_restore_is_pending(tmp_path: Path) -> None:
    """A restore reads a checkpoint file by file for minutes."""
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    named = checkpoint(checkpoints, "h16-published-20260917", age_days=40)
    (state / "RESTORE_PENDING").write_text("verification pending\n")
    assert main(["backup", "--remove-checkpoint", named.name, "--state", str(state)]) == 1
    assert named.is_dir()
    (state / "RESTORE_PENDING").unlink()
    assert main(["backup", "--remove-checkpoint", named.name, "--state", str(state)]) == 0
    assert not named.exists()


def test_an_uploaded_backup_prunes_after_the_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ordering the wiring is for: write, upload, commit the receipt, prune."""
    import swingset.backup.archive as archive_module
    import swingset.backup.huggingface as huggingface_module

    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    old = [checkpoint(checkpoints, f"run_2024010{day}T040000Z", age_days=30) for day in (1, 2, 3)]
    uploaded: list[str] = []

    class FakeArchive:
        def __init__(self, repo: str, token: str) -> None:
            self.repo = repo

    def upload(checkpoint_written: Any, archive: object) -> str:
        # Nothing may have been pruned yet: the upload could still fail.
        assert all(path.is_dir() for path in old)
        uploaded.append(str(checkpoint_written.path))
        return "a" * 40

    monkeypatch.setenv("HF_TOKEN", "token")
    monkeypatch.setattr(huggingface_module, "HuggingFaceArchive", FakeArchive)
    monkeypatch.setattr(archive_module, "upload_checkpoint", upload)
    assert main(["backup", "--state", str(state), *defaults(tmp_path)]) == 0
    assert len(uploaded) == 1
    assert 'event="checkpoints-pruned"' in capsys.readouterr().err
    assert not old[0].exists() and not old[1].exists() and old[2].is_dir()


def test_a_failed_upload_prunes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ordering is the safety argument: fewer copies must never follow a failure.

    Pruning sits after the upload so that a backup which could not be archived
    leaves every older checkpoint to fall back on.
    """
    import swingset.backup.archive as archive_module
    import swingset.backup.huggingface as huggingface_module

    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    old = [checkpoint(checkpoints, f"run_2024010{day}T040000Z", age_days=30) for day in (1, 2, 3)]

    class FakeArchive:
        def __init__(self, repo: str, token: str) -> None:
            self.repo = repo

    def refuse(checkpoint_written: Any, archive: object) -> str:
        raise RuntimeError("archive refused the commit")

    monkeypatch.setenv("HF_TOKEN", "token")
    monkeypatch.setattr(huggingface_module, "HuggingFaceArchive", FakeArchive)
    monkeypatch.setattr(archive_module, "upload_checkpoint", refuse)
    assert main(["backup", "--state", str(state), *defaults(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert 'event="checkpoints-pruned"' not in err
    assert all(path.is_dir() for path in old)


def test_a_backup_plans_the_prune_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Planning sizes what it would remove, so planning twice measures twice."""
    from swingset.backup import pruning

    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    for day in (1, 2, 3):
        checkpoint(checkpoints, f"run_2024010{day}T040000Z", age_days=30)
    plans: list[Path] = []
    real = pruning.plan_prune

    def counted(root: Path, **kwargs: Any) -> Any:
        plans.append(root)
        return real(root, **kwargs)

    monkeypatch.setattr(pruning, "plan_prune", counted)
    assert backup(state) == 0
    assert plans == [checkpoints]


def test_a_pending_restore_stops_the_automatic_prune(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The prune needs no restore guard of its own: the backup never starts.

    `open_database` refuses a state directory holding `RESTORE_PENDING`, so a
    restore into this directory stops the backup before a checkpoint is written
    and before anything is pruned.
    """
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    old = [checkpoint(checkpoints, f"run_2024010{day}T040000Z", age_days=30) for day in (1, 2, 3)]
    state.mkdir(parents=True, exist_ok=True)
    (state / "RESTORE_PENDING").write_text("verification pending\n")
    assert backup(state) == 1
    err = capsys.readouterr().err
    assert "restore verification is pending" in err
    assert 'event="checkpoints-pruned"' not in err
    assert all(path.is_dir() for path in old)


def damage_database(database: Path, *, table: str | None = None) -> None:
    """Overwrite one page of a SQLite file with junk, as a bad copy would carry.

    Without `table`, the second page; with it, the root page of that table,
    which neither the schema check nor the foreign-key or closure checks read,
    so only `quick_check` or `integrity_check` can see it.
    """
    import sqlite3

    size = int.from_bytes(database.read_bytes()[16:18], "big")
    page = 2
    if table is not None:
        connection = sqlite3.connect(database)
        try:
            (page,) = connection.execute(
                "SELECT rootpage FROM sqlite_master WHERE name = ?", (table,)
            ).fetchone()
        finally:
            connection.close()
    with database.open("r+b") as handle:
        handle.seek((page - 1) * size)
        handle.write(b"\xa5" * size)


def corrupt_new_checkpoints(
    monkeypatch: pytest.MonkeyPatch, *, written_bad: bool = False
) -> list[Path]:
    """Make every checkpoint the backup writes carry a damaged database.

    By default the copy is damaged after its manifest was written, so its
    file hash no longer matches. `written_bad` damages a table only
    `quick_check` reads and rewrites the manifest's hash and size to match,
    as a copy that was already bad when it was hashed would be. Returns the
    paths it damaged.
    """
    import swingset.backup.checkpoint as checkpoint_module
    from swingset.build.files import sha256_file

    real = checkpoint_module.create_checkpoint
    damaged: list[Path] = []

    def damaging(*args: Any, **kwargs: Any) -> Any:
        written = real(*args, **kwargs)
        database = written.path / "state.sqlite"
        damage_database(database, table="host_budget" if written_bad else None)
        if written_bad:
            manifest_path = written.path / "checkpoint.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["files"]["state.sqlite"] = {
                **manifest["files"]["state.sqlite"],
                "sha256": sha256_file(database),
                "size": database.stat().st_size,
            }
            manifest_path.write_text(json.dumps(manifest))
        damaged.append(written.path)
        return written

    monkeypatch.setattr(checkpoint_module, "create_checkpoint", damaging)
    return damaged


def test_a_local_checkpoint_that_fails_verification_is_renamed_and_prunes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With one kept checkpoint (D-0203), a bad copy must not replace the last good one.

    It leaves the timer namespace, so neither pruning nor the watchdog takes it
    for the newest good copy, and the backup fails.
    """
    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    good = checkpoint(checkpoints, "run_20260101T040000Z", age_days=1)
    damaged = corrupt_new_checkpoints(monkeypatch)
    assert main(["backup", "--local", "--state", str(state), *config]) == 1
    err = capsys.readouterr().err
    skipped = [
        line for line in err.splitlines() if line.startswith('event="checkpoint-prune-skipped"')
    ]
    assert len(skipped) == 1
    assert field(skipped[0], "reason") == "verification failed"
    verified = [line for line in err.splitlines() if line.startswith('event="checkpoint-verify"')]
    assert len(verified) == 1 and field(verified[0], "ok") is False
    assert 'event="checkpoints-pruned"' not in err
    assert 'event="error"' in err
    assert good.is_dir() and len(damaged) == 1 and not damaged[0].exists()
    failed = damaged[0].with_name(FAILED_CHECKPOINT_PREFIX + damaged[0].name)
    assert failed.is_dir() and not TIMER_NAME.match(failed.name)
    # The run record agrees with the exit code, on disk and in the runs row.
    run_id = damaged[0].name
    record = json.loads((state / "runs" / f"{run_id}.json").read_text())
    assert record["failed"] is True and record["command"] == "backup"
    assert "failed verification" in record["error"] and "held_units" in record
    import sqlite3

    connection = sqlite3.connect(state / "state.sqlite")
    try:
        (summary,) = connection.execute(
            "SELECT summary_json FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        connection.close()
    assert json.loads(summary)["failed"] is True
    # Policy never removes it; the operator does, by name.
    assert main(["backup", "--remove-checkpoint", failed.name, "--state", str(state), *config]) == 0
    assert not failed.exists()


def test_a_local_checkpoint_written_bad_fails_the_database_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A copy whose hash matches its manifest is caught by `quick_check` alone."""
    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    good = checkpoint(state / "checkpoints", "run_20260101T040000Z", age_days=1)
    damaged = corrupt_new_checkpoints(monkeypatch, written_bad=True)
    assert main(["backup", "--local", "--state", str(state), *config]) == 1
    captured = capsys.readouterr()
    skipped = [
        line
        for line in captured.err.splitlines()
        if line.startswith('event="checkpoint-prune-skipped"')
    ]
    assert len(skipped) == 1
    detail = str(field(skipped[0], "detail"))
    assert "file failed verification" not in detail
    assert "closure differs" not in detail
    assert 'event="checkpoints-pruned"' not in captured.err
    # A copy that failed is never printed as the backup's result.
    assert captured.out == ""
    assert good.is_dir() and not damaged[0].exists()
    assert damaged[0].with_name(FAILED_CHECKPOINT_PREFIX + damaged[0].name).is_dir()


def test_only_the_newest_failed_copy_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failure every day must not fill the disk a full copy at a time (D-0203)."""
    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    good = checkpoint(checkpoints, "run_20260101T040000Z", age_days=3)
    earlier = checkpoint(checkpoints, "failed-run_20260102T040000Z", age_days=2)
    by_hand = checkpoint(checkpoints, "failed-before-switch", age_days=2)
    damaged = corrupt_new_checkpoints(monkeypatch)
    assert main(["backup", "--local", "--state", str(state), *config]) == 1
    err = capsys.readouterr().err
    skipped = next(
        line for line in err.splitlines() if line.startswith('event="checkpoint-prune-skipped"')
    )
    assert field(skipped, "removed_failed") == [earlier.name]
    assert not earlier.exists()
    assert good.is_dir() and by_hand.is_dir()
    assert damaged[0].with_name(FAILED_CHECKPOINT_PREFIX + damaged[0].name).is_dir()


def test_a_failed_removal_of_an_older_copy_still_renames_the_bad_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bad copy leaves the timer namespace before anything can fail."""
    import shutil

    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    checkpoints = state / "checkpoints"
    good = checkpoint(checkpoints, "run_20260101T040000Z", age_days=3)
    earlier = checkpoint(checkpoints, "failed-run_20260102T040000Z", age_days=2)
    damaged = corrupt_new_checkpoints(monkeypatch)

    def refusing(path: Any, *args: Any, **kwargs: Any) -> None:
        raise PermissionError(f"cannot remove {path}")

    monkeypatch.setattr(shutil, "rmtree", refusing)
    assert main(["backup", "--local", "--state", str(state), *config]) == 1
    err = capsys.readouterr().err
    assert 'event="failed-checkpoint-removal-failed"' in err
    skipped = next(
        line for line in err.splitlines() if line.startswith('event="checkpoint-prune-skipped"')
    )
    assert field(skipped, "removed_failed") == []
    assert not damaged[0].exists()
    assert damaged[0].with_name(FAILED_CHECKPOINT_PREFIX + damaged[0].name).is_dir()
    assert good.is_dir() and earlier.is_dir()


def test_a_failed_copy_outside_the_checkpoints_directory_removes_nothing_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --destination elsewhere, the directory is the operator's: rename only."""
    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    elsewhere = tmp_path / "elsewhere"
    theirs = checkpoint(elsewhere, "failed-run_20260102T040000Z", age_days=2)
    damaged = corrupt_new_checkpoints(monkeypatch)
    destination = elsewhere / "run_20260103T040000Z"
    argv = ["backup", "--local", "--destination", str(destination), "--state", str(state)]
    assert main([*argv, *config]) == 1
    assert damaged == [destination] and not destination.exists()
    assert (elsewhere / f"{FAILED_CHECKPOINT_PREFIX}{destination.name}").is_dir()
    assert theirs.is_dir()


def test_a_malformed_manifest_fails_verification_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Any exception from the read-back is a verdict, so the copy is renamed."""
    import swingset.backup.checkpoint as checkpoint_module

    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    good = checkpoint(state / "checkpoints", "run_20260101T040000Z", age_days=1)
    real = checkpoint_module.create_checkpoint
    written_paths: list[Path] = []

    def listing(*args: Any, **kwargs: Any) -> Any:
        written = real(*args, **kwargs)
        manifest_path = written.path / "checkpoint.json"
        manifest = json.loads(manifest_path.read_text())
        # int(None) raises TypeError, which the read-back once let escape.
        manifest["schema_version"] = None
        manifest_path.write_text(json.dumps(manifest))
        written_paths.append(written.path)
        return written

    monkeypatch.setattr(checkpoint_module, "create_checkpoint", listing)
    assert main(["backup", "--local", "--state", str(state), *config]) == 1
    err = capsys.readouterr().err
    verified = next(
        line for line in err.splitlines() if line.startswith('event="checkpoint-verify"')
    )
    assert field(verified, "ok") is False
    assert "TypeError" in err
    assert not written_paths[0].exists()
    assert written_paths[0].with_name(FAILED_CHECKPOINT_PREFIX + written_paths[0].name).is_dir()
    assert good.is_dir()


def test_a_local_checkpoint_with_an_extra_file_fails_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The read-back covers the files beside the database, not only state.sqlite."""
    import swingset.backup.checkpoint as checkpoint_module

    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    good = checkpoint(state / "checkpoints", "run_20260101T040000Z", age_days=1)
    real = checkpoint_module.create_checkpoint

    def adding(*args: Any, **kwargs: Any) -> Any:
        written = real(*args, **kwargs)
        # A file the manifest does not name appears in the copy.
        (written.path / "inputs").mkdir(exist_ok=True)
        (written.path / "inputs" / "stray.json").write_text("{}")
        return written

    monkeypatch.setattr(checkpoint_module, "create_checkpoint", adding)
    assert main(["backup", "--local", "--state", str(state), *config]) == 1
    err = capsys.readouterr().err
    assert "checkpoint closure differs" in err and "extra=['inputs/stray.json']" in err
    assert good.is_dir()


def test_a_local_checkpoint_missing_a_closure_file_fails_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import swingset.backup.checkpoint as checkpoint_module

    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    good = checkpoint(state / "checkpoints", "run_20260101T040000Z", age_days=1)
    real = checkpoint_module.create_checkpoint
    lost: list[str] = []

    def losing(*args: Any, **kwargs: Any) -> Any:
        written = real(*args, **kwargs)
        # A file the manifest names goes missing after the copy.
        name = next(name for name in written.files if name != "state.sqlite")
        (written.path / name).unlink()
        lost.append(name)
        return written

    monkeypatch.setattr(checkpoint_module, "create_checkpoint", losing)
    assert main(["backup", "--local", "--state", str(state), *config]) == 1
    err = capsys.readouterr().err
    assert "checkpoint closure differs" in err and f"missing={lost!r}" in err
    assert good.is_dir()


def test_a_local_checkpoint_that_passes_verification_prunes_as_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = config_with(tmp_path, "checkpoint_keep_recent = 1\ncheckpoint_max_age = 0\n")
    state = tmp_path / "state"
    old = checkpoint(state / "checkpoints", "run_20260101T040000Z", age_days=1)
    assert main(["backup", "--local", "--state", str(state), *config]) == 0
    captured = capsys.readouterr()
    err = captured.err
    (printed,) = captured.out.splitlines()
    assert Path(printed).parent == state / "checkpoints"
    assert TIMER_NAME.match(Path(printed).name) and (Path(printed) / "checkpoint.json").is_file()
    verified = [line for line in err.splitlines() if line.startswith('event="checkpoint-verify"')]
    assert len(verified) == 1 and field(verified[0], "ok") is True
    assert isinstance(field(verified[0], "seconds"), float | int)
    assert 'event="checkpoints-pruned"' in err
    assert not old.exists()


def test_a_local_verification_runs_without_the_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cycles need not wait for the read-back; the prune takes the lock again."""
    import fcntl

    import swingset.backup.checkpoint as checkpoint_module

    state = tmp_path / "state"
    seen: list[str] = []
    real_verify = checkpoint_module.verify_checkpoint
    real_prune = cli._prune_checkpoints

    def free() -> bool:
        with (state / "state.lock").open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return True

    def verify(*args: Any, **kwargs: Any) -> Any:
        seen.append(f"verify quick={kwargs.get('quick')} free={free()}")
        return real_verify(*args, **kwargs)

    def prune(*args: Any, **kwargs: Any) -> None:
        seen.append(f"prune free={free()}")
        real_prune(*args, **kwargs)

    monkeypatch.setattr(checkpoint_module, "verify_checkpoint", verify)
    monkeypatch.setattr(cli, "_prune_checkpoints", prune)
    assert backup(state) == 0
    assert seen == ["verify quick=True free=True", "prune free=False"]


def test_an_uploaded_backup_skips_the_local_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The upload is the check in that mode; its wiring is unchanged."""
    import swingset.backup.archive as archive_module
    import swingset.backup.huggingface as huggingface_module

    class FakeArchive:
        def __init__(self, repo: str, token: str) -> None:
            self.repo = repo

    monkeypatch.setenv("HF_TOKEN", "token")
    monkeypatch.setattr(huggingface_module, "HuggingFaceArchive", FakeArchive)
    monkeypatch.setattr(archive_module, "upload_checkpoint", lambda written, archive: "a" * 40)
    corrupt_new_checkpoints(monkeypatch)
    state = tmp_path / "state"
    assert main(["backup", "--state", str(state), *defaults(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert 'event="checkpoint-verify"' not in err
    assert 'event="checkpoints-pruned"' in err


def test_the_watchdog_copies_the_failed_checkpoint_prefix() -> None:
    """nix/watchdog.py cannot import the package; it keeps its own copy."""
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "nix" / "watchdog.py"
    spec = importlib.util.spec_from_file_location("swingset_watchdog_prefix", script)
    assert spec is not None and spec.loader is not None
    watchdog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watchdog)
    assert watchdog.FAILED_PREFIX == FAILED_CHECKPOINT_PREFIX


def test_no_prune_prints_the_local_checkpoint_without_a_read_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    assert backup(state, "--no-prune") == 0
    captured = capsys.readouterr()
    assert 'event="checkpoint-verify"' not in captured.err
    (printed,) = captured.out.splitlines()
    assert (Path(printed) / "checkpoint.json").is_file()
