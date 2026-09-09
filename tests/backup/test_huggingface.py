import hashlib
import io
import json
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from huggingface_hub.errors import RemoteEntryNotFoundError

import swingset.backup.huggingface as module
from swingset.backup.checkpoint import create_checkpoint, verify_checkpoint
from swingset.backup.huggingface import HuggingFaceArchive


class FakeArchiveApi:
    def __init__(self, files: list[str]) -> None:
        self.files = files

    def list_repo_files(self, repo_id: str, *, repo_type: str, revision: str) -> list[str]:
        return self.files


class CapturingArchiveApi:
    def __init__(self) -> None:
        self.uploaded: dict[str, bytes] = {}

    def create_commit(self, **kwargs: Any) -> SimpleNamespace:
        for operation in kwargs["operations"]:
            self.uploaded[operation.path_in_repo] = Path(operation.path_or_fileobj).read_bytes()
        return SimpleNamespace(oid="packed")


def missing_entry() -> RemoteEntryNotFoundError:
    response = httpx.Response(404, request=httpx.Request("GET", "https://huggingface.co/missing"))
    return RemoteEntryNotFoundError("missing", response=response)


def test_download_rejects_manifest_path_traversal_before_artifact_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "remote-checkpoint.json"
    manifest.write_text(json.dumps({"files": {"../escape": {"sha256": "x", "size": 1}}}))

    def download(**_kwargs: object) -> str:
        if _kwargs["filename"] == "_transport/archive.json":
            raise missing_entry()
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


def test_packed_checkpoint_round_trip_uses_three_remote_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "checkpoint"
    (checkpoint / "extracts").mkdir(parents=True)
    (checkpoint / "state.sqlite").write_bytes(b"database")
    (checkpoint / "extracts" / "abc").write_bytes(b"raw extract")
    manifest = {
        "files": {
            "extracts/abc": {"size": 11, "sha256": "unused"},
            "state.sqlite": {"size": 8, "sha256": "unused"},
        }
    }
    (checkpoint / "checkpoint.json").write_text(json.dumps(manifest))
    api = CapturingArchiveApi()
    archive = HuggingFaceArchive("owner/archive", token="secret", api=cast("Any", api))
    files = {
        path.relative_to(checkpoint).as_posix(): path
        for path in checkpoint.rglob("*")
        if path.is_file()
    }
    assert archive.create_commit(parent="old", message="backup", files=files) == "packed"
    assert set(api.uploaded) == {
        "checkpoint.json",
        "_transport/archive.json",
        "_transport/checkpoint.tar",
    }

    remote = tmp_path / "remote"
    remote.mkdir()
    for name, body in api.uploaded.items():
        path = remote / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def download(**kwargs: object) -> str:
        return str(remote / str(kwargs["filename"]))

    monkeypatch.setattr(module, "hf_hub_download", download)
    restored = tmp_path / "restored"
    archive.download("packed", restored)
    assert (restored / "checkpoint.json").read_text() == json.dumps(manifest)
    assert (restored / "state.sqlite").read_bytes() == b"database"
    assert (restored / "extracts" / "abc").read_bytes() == b"raw extract"


def test_packed_checkpoint_is_deterministic(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "checkpoint.json").write_text('{"files":{"state.sqlite":{}}}')
    (checkpoint / "state.sqlite").write_bytes(b"database")
    files = {path.name: path for path in checkpoint.iterdir()}
    first, second = CapturingArchiveApi(), CapturingArchiveApi()
    HuggingFaceArchive("owner/archive", token="secret", api=cast("Any", first)).create_commit(
        parent="old", message="backup", files=files
    )
    HuggingFaceArchive("owner/archive", token="secret", api=cast("Any", second)).create_commit(
        parent="old", message="backup", files=files
    )
    assert first.uploaded == second.uploaded


def test_packed_transport_reconstructs_a_verifiable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    connection = sqlite3.connect(state / "state.sqlite")
    connection.execute("CREATE TABLE durable(value TEXT)")
    connection.execute("INSERT INTO durable VALUES ('kept')")
    connection.commit()
    checkpoint = create_checkpoint(
        state,
        connection,
        tmp_path / "checkpoint",
        schema_version=1,
        versions={"projector": "8"},
        input_bundle_hash=None,
    )
    connection.close()
    api = CapturingArchiveApi()
    archive = HuggingFaceArchive("owner/archive", token="secret", api=cast("Any", api))
    files = {
        path.relative_to(checkpoint.path).as_posix(): path
        for path in checkpoint.path.rglob("*")
        if path.is_file()
    }
    archive.create_commit(parent="old", message="backup", files=files)
    remote = tmp_path / "remote"
    for name, body in api.uploaded.items():
        path = remote / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    monkeypatch.setattr(
        module,
        "hf_hub_download",
        lambda **kwargs: str(remote / str(kwargs["filename"])),
    )
    restored = tmp_path / "restored"
    archive.download("packed", restored)
    manifest = verify_checkpoint(restored, maximum_schema_version=1)
    assert manifest["versions"] == {"projector": "8"}
    restored_database = sqlite3.connect(restored / "state.sqlite")
    assert restored_database.execute("SELECT value FROM durable").fetchone() == ("kept",)
    restored_database.close()


@pytest.mark.parametrize(
    ("members", "message"),
    [
        (["../escape"], "unsafe checkpoint path"),
        (["state.sqlite", "state.sqlite"], "duplicate members"),
        (["unexpected"], "members differ from manifest"),
    ],
)
def test_packed_download_rejects_unsafe_duplicate_and_unexpected_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    members: list[str],
    message: str,
) -> None:
    remote = tmp_path / "remote"
    (remote / "_transport").mkdir(parents=True)
    (remote / "checkpoint.json").write_text(
        json.dumps({"files": {"state.sqlite": {"size": 1, "sha256": "unused"}}})
    )
    archive_path = remote / "_transport" / "checkpoint.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name in members:
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    (remote / "_transport" / "archive.json").write_text(
        json.dumps(
            {
                "format": "swingset-checkpoint-tar-v1",
                "archive": "_transport/checkpoint.tar",
                "size": archive_path.stat().st_size,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            }
        )
    )
    monkeypatch.setattr(
        module,
        "hf_hub_download",
        lambda **kwargs: str(remote / str(kwargs["filename"])),
    )
    destination = tmp_path / "destination"
    archive = HuggingFaceArchive("owner/archive", token="secret", api=cast("Any", object()))
    with pytest.raises(ValueError, match=message):
        archive.download("packed", destination)
    assert set(path.name for path in destination.iterdir()) == {"checkpoint.json"}


def test_packed_download_rejects_symlink_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote"
    (remote / "_transport").mkdir(parents=True)
    (remote / "checkpoint.json").write_text(json.dumps({"files": {"state.sqlite": {}}}))
    archive_path = remote / "_transport" / "checkpoint.tar"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("state.sqlite")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    (remote / "_transport" / "archive.json").write_text(
        json.dumps(
            {
                "format": "swingset-checkpoint-tar-v1",
                "archive": "_transport/checkpoint.tar",
                "size": archive_path.stat().st_size,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            }
        )
    )
    monkeypatch.setattr(
        module,
        "hf_hub_download",
        lambda **kwargs: str(remote / str(kwargs["filename"])),
    )
    with pytest.raises(ValueError, match="unsafe checkpoint archive member"):
        HuggingFaceArchive("owner/archive", token="secret", api=cast("Any", object())).download(
            "packed", tmp_path / "destination"
        )


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
