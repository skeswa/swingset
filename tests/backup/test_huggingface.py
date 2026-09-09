import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from huggingface_hub.errors import RemoteEntryNotFoundError

import swingset.backup.huggingface as module
from swingset.backup.huggingface import HuggingFaceArchive


class FakeArchiveApi:
    def __init__(self, files: list[str]) -> None:
        self.files = files

    def list_repo_files(self, repo_id: str, *, repo_type: str, revision: str) -> list[str]:
        return self.files


def test_download_rejects_manifest_path_traversal_before_artifact_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "remote-checkpoint.json"
    manifest.write_text(json.dumps({"files": {"../escape": {"sha256": "x", "size": 1}}}))

    def download(**_kwargs: object) -> str:
        return str(manifest)

    monkeypatch.setattr(module, "hf_hub_download", download)
    archive = HuggingFaceArchive("owner/archive", token="secret", api=object())  # type: ignore[arg-type]
    destination = tmp_path / "download"
    with pytest.raises(ValueError, match="unsafe checkpoint path"):
        archive.download("commit", destination)
    assert not (tmp_path / "escape").exists()
    assert set(path.name for path in destination.iterdir()) == {"checkpoint.json"}


def test_missing_manifest_translates_actual_remote_entry_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(404, request=httpx.Request("GET", "https://huggingface.co/missing"))
    error = RemoteEntryNotFoundError("missing", response=response)
    monkeypatch.setattr(module, "hf_hub_download", lambda **_kwargs: (_ for _ in ()).throw(error))
    archive = HuggingFaceArchive("owner/archive", token="secret", api=cast("Any", object()))

    with pytest.raises(FileNotFoundError, match="checkpoint manifest") as raised:
        archive.manifest_hash("initial")
    assert raised.value.__cause__ is error


def test_initial_head_requires_exact_private_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = tmp_path / "README.md"
    card.write_bytes(
        b"# swingset private archive\n\n"
        b"Private pipeline checkpoints and source evidence. No blanket license is granted\n"
        b"for archive contents; third-party materials retain their respective rights.\n"
    )
    api = FakeArchiveApi([".gitattributes", "README.md"])
    archive = HuggingFaceArchive("owner/archive", token="secret", api=cast("Any", api))
    monkeypatch.setattr(module, "hf_hub_download", lambda **_kwargs: str(card))
    assert archive.is_initial_head("initial")

    card.write_bytes(card.read_bytes() + b"extra\n")
    assert not archive.is_initial_head("initial")
    api.files.append("data.sqlite")
    assert not archive.is_initial_head("initial")
