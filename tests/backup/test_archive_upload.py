from pathlib import Path

import pytest

from swingset.backup.archive import upload_checkpoint
from swingset.backup.checkpoint import Checkpoint


class FakeArchive:
    def __init__(self, *, initial: bool) -> None:
        self.initial = initial
        self.commits = 0

    def head(self) -> str:
        return "initial"

    def manifest_hash(self, commit: str) -> str:
        raise FileNotFoundError("absent")

    def is_initial_head(self, commit: str) -> bool:
        return self.initial

    def create_commit(self, *, parent: str | None, message: str, files: dict[str, Path]) -> str:
        assert parent == "initial"
        self.commits += 1
        return "backup"


def checkpoint(tmp_path: Path) -> Checkpoint:
    path = tmp_path / "checkpoint"
    path.mkdir()
    (path / "checkpoint.json").write_text("{}")
    return Checkpoint(path, "hash", {})


def test_upload_bootstraps_only_recognized_initial_archive(tmp_path: Path) -> None:
    archive = FakeArchive(initial=True)
    assert upload_checkpoint(checkpoint(tmp_path), archive) == "backup"
    assert archive.commits == 1


def test_upload_rejects_unrelated_head_without_manifest(tmp_path: Path) -> None:
    archive = FakeArchive(initial=False)
    with pytest.raises(RuntimeError, match="content but no checkpoint manifest"):
        upload_checkpoint(checkpoint(tmp_path), archive)
    assert archive.commits == 0
