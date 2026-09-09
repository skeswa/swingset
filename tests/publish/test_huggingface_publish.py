from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from huggingface_hub.errors import RemoteEntryNotFoundError

from swingset.publish.huggingface import HuggingFaceHub


class FakePublishApi:
    def __init__(self, files: list[str]) -> None:
        self.files = files

    def list_repo_files(self, repo_id: str, *, repo_type: str, revision: str) -> list[str]:
        assert repo_id == "skeswa/swingset"
        assert repo_type == "dataset"
        assert revision == "initial"
        return self.files


def hub(files: list[str]) -> HuggingFaceHub:
    instance = object.__new__(HuggingFaceHub)
    instance.repo_id = "skeswa/swingset"
    instance.token = "unused"
    instance.api = cast("Any", FakePublishApi(files))
    return instance


@pytest.mark.parametrize("files", [[], [".gitattributes"]])
def test_initial_head_accepts_empty_generated_metadata(files: list[str]) -> None:
    assert hub(files).is_initial_head("initial")


def test_initial_head_accepts_generated_license_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = tmp_path / "README.md"
    card.write_bytes(b"---\nlicense: odc-by\n---\n")
    instance = hub([".gitattributes", "README.md"])
    monkeypatch.setattr(instance, "_download", lambda name, commit: card)

    assert instance.is_initial_head("initial")


@pytest.mark.parametrize(
    "contents",
    [
        b"---\nlicense: mit\n---\n",
        b"---\nlicense: odc-by\n---\n\n# Existing dataset\n",
        b"---\r\nlicense: odc-by\r\n---\r\n",
    ],
)
def test_initial_head_rejects_non_generated_card(
    contents: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = tmp_path / "README.md"
    card.write_bytes(contents)
    instance = hub([".gitattributes", "README.md"])
    monkeypatch.setattr(instance, "_download", lambda name, commit: card)

    assert not instance.is_initial_head("initial")


@pytest.mark.parametrize("name", ["LICENSE", "data/placements/data.parquet", "_meta/manifest.json"])
def test_initial_head_rejects_every_other_file(name: str) -> None:
    assert not hub([".gitattributes", name]).is_initial_head("initial")


def test_inspect_translates_actual_remote_entry_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(404, request=httpx.Request("GET", "https://huggingface.co/missing"))
    error = RemoteEntryNotFoundError("missing", response=response)
    instance = hub([])
    monkeypatch.setattr(instance, "_download", lambda *_args: (_ for _ in ()).throw(error))

    with pytest.raises(FileNotFoundError, match="remote manifest") as raised:
        instance.inspect("initial")
    assert raised.value.__cause__ is error
