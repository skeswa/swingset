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
    monkeypatch.setattr(instance, "download", lambda name, commit: card)

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
    monkeypatch.setattr(instance, "download", lambda name, commit: card)

    assert not instance.is_initial_head("initial")


@pytest.mark.parametrize("name", ["LICENSE", "data/placements/data.parquet", "_meta/manifest.json"])
def test_initial_head_rejects_every_other_file(name: str) -> None:
    assert not hub([".gitattributes", name]).is_initial_head("initial")


def test_inspect_translates_actual_remote_entry_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(404, request=httpx.Request("GET", "https://huggingface.co/missing"))
    error = RemoteEntryNotFoundError("missing", response=response)
    instance = hub([])
    monkeypatch.setattr(instance, "download", lambda *_args: (_ for _ in ()).throw(error))

    with pytest.raises(FileNotFoundError, match="remote manifest") as raised:
        instance.inspect("initial")
    assert raised.value.__cause__ is error


class BranchApi:
    def __init__(self):
        self.reads = []
        self.writes = []

    def repo_info(self, *args, **kwargs):
        from types import SimpleNamespace

        self.reads.append((args, kwargs))
        return SimpleNamespace(sha="frozen-A")

    def create_commit(self, **kwargs):
        from types import SimpleNamespace

        self.writes.append(kwargs)
        return SimpleNamespace(oid="rehearsal-B")


@pytest.mark.parametrize("revision", [None, "stage2/frozen-A"])
def test_head_reads_only_the_configured_branch_and_preserves_default_kwargs(revision):
    api = BranchApi()
    adapter = HuggingFaceHub(
        "fixture/rehearsal", token="unused", api=cast("Any", api), revision=revision
    )
    assert adapter.head() == "frozen-A"
    expected = {"repo_type": "dataset"}
    if revision is not None:
        expected["revision"] = revision
    assert api.reads == [(("fixture/rehearsal",), expected)]


@pytest.mark.parametrize("revision", [None, "stage2/frozen-A"])
def test_publication_keeps_its_parent_and_writes_only_the_configured_branch(tmp_path, revision):
    api = BranchApi()
    adapter = HuggingFaceHub(
        "fixture/rehearsal", token="unused", api=cast("Any", api), revision=revision
    )
    artifact = tmp_path / "README.md"
    artifact.write_bytes(b"rehearsal release\n")
    assert (
        adapter.create_commit(
            parent="frozen-A",
            message="candidate B",
            additions={"README.md": artifact},
            deletions=(),
        )
        == "rehearsal-B"
    )
    (write,) = api.writes
    assert write["repo_id"] == "fixture/rehearsal"
    assert write["repo_type"] == "dataset"
    assert write["parent_commit"] == "frozen-A"
    assert write["commit_message"] == "candidate B"
    assert len(write["operations"]) == 1
    assert write["operations"][0].path_in_repo == "README.md"
    if revision is None:
        assert "revision" not in write
    else:
        assert write["revision"] == revision


def test_branch_does_not_replace_the_explicit_commit_used_for_baseline_download(
    tmp_path, monkeypatch
):
    from swingset.publish import huggingface

    artifact = tmp_path / "baseline"
    calls = []

    def download(**kwargs):
        calls.append(kwargs)
        return str(artifact)

    monkeypatch.setattr(huggingface, "hf_hub_download", download)
    adapter = HuggingFaceHub(
        "fixture/rehearsal",
        token="unused",
        api=cast("Any", BranchApi()),
        revision="stage2/frozen-A",
    )
    assert adapter.download("data/events/events.parquet", "exact-commit-A") == artifact
    assert calls == [
        {
            "repo_id": "fixture/rehearsal",
            "repo_type": "dataset",
            "filename": "data/events/events.parquet",
            "revision": "exact-commit-A",
            "token": "unused",
        }
    ]
