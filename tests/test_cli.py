import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import swingset.cli as cli
from swingset.backup.checkpoint import create_checkpoint
from swingset.cli import main
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.client import FetchResult
from swingset.publish.service import RemoteCommit
from swingset.state.db import open_database


class EmptyHub:
    def head(self) -> None:
        return None

    def is_initial_head(self, commit: str) -> bool:
        return False

    def inspect(self, commit: str) -> RemoteCommit:
        raise AssertionError(commit)

    def create_commit(self, **_kwargs: object) -> str:
        raise AssertionError("restore must not publish")


def checkpoint(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    with open_database(source) as database:
        result = create_checkpoint(
            source,
            database.connection,
            tmp_path / "checkpoint",
            schema_version=1,
            versions={},
            input_bundle_hash=None,
        )
    return result.path


def test_pause_timeout_applies_no_change(tmp_path):
    with open_database(tmp_path) as db:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "swingset.cli",
                "pause",
                "--all",
                "--state",
                str(tmp_path),
                "--lock-timeout",
                "0.1",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "no change was applied" in result.stderr
        assert db.connection.execute("SELECT COUNT(*) FROM operator_pauses").fetchone()[0] == 0


def test_pause_waits_and_commits_before_success(tmp_path):
    db = open_database(tmp_path)
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "swingset.cli",
            "pause",
            "--all",
            "--state",
            str(tmp_path),
            "--lock-timeout",
            "5",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.15)
    assert child.poll() is None
    db.close()
    _, error = child.communicate(timeout=10)
    assert child.returncode == 0, error
    with open_database(tmp_path) as restored:
        assert (
            restored.connection.execute("SELECT scope_kind FROM operator_pauses").fetchone()[0]
            == "all"
        )


def test_doctor_does_not_wait_for_writer(tmp_path, capsys):
    with open_database(tmp_path):
        assert main(["doctor", "--state", str(tmp_path)]) == 0
    assert '"schema_version": 1' in capsys.readouterr().out


def test_restore_pending_blocks_mutations_but_allows_diagnosis(tmp_path):
    with open_database(tmp_path):
        pass
    (tmp_path / "RESTORE_PENDING").write_text("verify first")
    assert main(["pause", "--all", "--state", str(tmp_path)]) == 1
    assert main(["doctor", "--state", str(tmp_path)]) == 0


def test_restore_checkpoint_uses_remote_verification_and_activates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = checkpoint(tmp_path)
    target = tmp_path / "restored"
    monkeypatch.setattr(cli, "hub", lambda: EmptyHub())
    assert (
        main(["restore", "--checkpoint", str(saved), "--writer-stopped", "--state", str(target)])
        == 0
    )
    assert (target / "state.sqlite").is_file()
    assert not (target / "RESTORE_PENDING").exists()


def test_restore_archive_commit_downloads_selected_commit_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = checkpoint(tmp_path)
    target = tmp_path / "restored"
    selected: list[str] = []

    def download(_self: object, commit: str, destination: Path) -> None:
        selected.append(commit)
        shutil.copytree(saved, destination, dirs_exist_ok=True)

    monkeypatch.setattr(cli, "hub", lambda: EmptyHub())
    monkeypatch.setattr("swingset.backup.huggingface.HuggingFaceArchive.download", download)
    monkeypatch.setenv("HF_TOKEN", "test-token")
    assert (
        main(
            [
                "restore",
                "--archive-commit",
                "archive-sha",
                "--writer-stopped",
                "--state",
                str(target),
            ]
        )
        == 0
    )
    assert selected == ["archive-sha"]
    assert (target / "state.sqlite").is_file()


def test_registry_crosscheck_blob_replay_finishes_run_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: list[str] = []

    def replay(_database: object, digest: str, _archive: object, _now: object, _run_id: str) -> int:
        called.append(digest)
        return 3

    monkeypatch.setattr("swingset.schedule.registry.replay_crosscheck", replay)
    assert (
        main(
            [
                "registry-crosscheck",
                "--blob",
                "a" * 64,
                "--state",
                str(tmp_path),
                "--config",
                "config",
                "--overrides",
                "overrides",
            ]
        )
        == 0
    )
    assert called == ["a" * 64]
    assert capsys.readouterr().out.strip() == "3"
    with open_database(tmp_path) as database:
        row = database.connection.execute(
            "SELECT finished_at,summary_json FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert row[0] is not None
        summary = json.loads(row[1])
        assert summary["command"] == "registry-crosscheck"
        assert not summary["failed"]


@pytest.mark.parametrize(
    "outcome", [Outcome.BLOCKED, Outcome.INVALID, Outcome.THROTTLED, Outcome.SERVER_ERROR]
)
def test_fetch_one_failure_classification_exits_nonzero_and_finishes_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: Outcome
) -> None:
    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def fetch(self, *_args: object, **_kwargs: object) -> FetchResult:
            return FetchResult(Classification(outcome))

        def close(self) -> None:
            pass

    monkeypatch.setattr("swingset.fetch.client.FetchClient", FakeClient)
    assert (
        main(
            [
                "fetch-one",
                "https://worldsdc.com/events/",
                "--kind",
                "wsdc_calendar.events",
                "--state",
                str(tmp_path),
                "--config",
                "config",
                "--overrides",
                "overrides",
            ]
        )
        == 1
    )
    with open_database(tmp_path) as database:
        row = database.connection.execute(
            "SELECT finished_at,summary_json FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert row[0] is not None
        summary = json.loads(row[1])
        assert summary["failed"]
        assert outcome.value in summary["error"]
