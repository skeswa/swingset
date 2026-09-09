from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download
from huggingface_hub.errors import EntryNotFoundError

from swingset.build.files import canonical_json, sha256_file

_INITIAL_CARD = (
    b"# swingset private archive\n\n"
    b"Private pipeline checkpoints and source evidence. No blanket license is granted\n"
    b"for archive contents; third-party materials retain their respective rights.\n"
)
_TRANSPORT_MANIFEST = "_transport/archive.json"
_TRANSPORT_ARCHIVE = "_transport/checkpoint.tar"
_MAX_ARCHIVE_BYTES = 20 * 1024**3


class HuggingFaceArchive:
    def __init__(self, repo_id: str, *, token: str, api: HfApi | None = None) -> None:
        self.repo_id = repo_id
        self.token = token
        self.api = api or HfApi(token=token)

    def head(self) -> str | None:
        try:
            return str(self.api.repo_info(self.repo_id, repo_type="dataset").sha)
        except Exception as error:
            raise RuntimeError(
                f"cannot read archive repository {self.repo_id}; verify repository and token access"
            ) from error

    def manifest_hash(self, commit: str) -> str:
        try:
            path = Path(
                hf_hub_download(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    filename="checkpoint.json",
                    revision=commit,
                    token=self.token,
                )
            )
        except EntryNotFoundError as error:
            raise FileNotFoundError("remote checkpoint manifest is absent") from error
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def is_initial_head(self, commit: str) -> bool:
        files = set(self.api.list_repo_files(self.repo_id, repo_type="dataset", revision=commit))
        if not files <= {".gitattributes", "README.md"}:
            return False
        if "README.md" not in files:
            return True
        path = Path(
            hf_hub_download(
                repo_id=self.repo_id,
                repo_type="dataset",
                filename="README.md",
                revision=commit,
                token=self.token,
            )
        )
        return path.read_bytes() == _INITIAL_CARD

    def create_commit(self, *, parent: str | None, message: str, files: dict[str, Path]) -> str:
        checkpoint_manifest = files.get("checkpoint.json")
        if checkpoint_manifest is None:
            raise ValueError("checkpoint upload is missing checkpoint.json")
        if {_TRANSPORT_MANIFEST, _TRANSPORT_ARCHIVE} & files.keys():
            raise ValueError("checkpoint contains a reserved transport path")
        with tempfile.TemporaryDirectory(prefix="swingset-checkpoint-") as temporary_name:
            temporary = Path(temporary_name)
            archive = temporary / "checkpoint.tar"
            with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as output:
                for name, path in sorted(files.items()):
                    if name == "checkpoint.json":
                        continue
                    relative = _safe_relative(name)
                    info = tarfile.TarInfo(relative.as_posix())
                    info.size = path.stat().st_size
                    info.mode = 0o644
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with path.open("rb") as source:
                        output.addfile(info, source)
            archive_size = archive.stat().st_size
            if archive_size > _MAX_ARCHIVE_BYTES:
                raise ValueError("checkpoint archive exceeds the 20 GiB transport limit")
            transport = temporary / "archive.json"
            transport.write_bytes(
                canonical_json(
                    {
                        "format": "swingset-checkpoint-tar-v1",
                        "archive": _TRANSPORT_ARCHIVE,
                        "size": archive_size,
                        "sha256": sha256_file(archive),
                    }
                )
            )
            operations: list[Any] = [
                CommitOperationAdd(
                    path_in_repo="checkpoint.json", path_or_fileobj=checkpoint_manifest
                ),
                CommitOperationAdd(path_in_repo=_TRANSPORT_MANIFEST, path_or_fileobj=transport),
                CommitOperationAdd(path_in_repo=_TRANSPORT_ARCHIVE, path_or_fileobj=archive),
            ]
            result = self.api.create_commit(
                repo_id=self.repo_id,
                repo_type="dataset",
                operations=operations,
                commit_message=message,
                parent_commit=parent,
            )
            return str(result.oid)

    def download(self, commit: str, destination: Path) -> None:
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("archive download destination must be empty")
        destination.mkdir(parents=True, exist_ok=True)
        manifest = Path(
            hf_hub_download(
                repo_id=self.repo_id,
                repo_type="dataset",
                filename="checkpoint.json",
                revision=commit,
                token=self.token,
            )
        )
        shutil.copyfile(manifest, destination / "checkpoint.json")
        contents = json.loads(manifest.read_text())
        try:
            transport_path = Path(
                hf_hub_download(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    filename=_TRANSPORT_MANIFEST,
                    revision=commit,
                    token=self.token,
                )
            )
        except EntryNotFoundError:
            self._download_flat(commit, destination, contents)
            return
        transport = json.loads(transport_path.read_text())
        if transport.get("format") != "swingset-checkpoint-tar-v1":
            raise ValueError("unsupported checkpoint transport format")
        archive_name = str(transport.get("archive"))
        if archive_name != _TRANSPORT_ARCHIVE:
            raise ValueError("invalid checkpoint transport archive path")
        archive = Path(
            hf_hub_download(
                repo_id=self.repo_id,
                repo_type="dataset",
                filename=archive_name,
                revision=commit,
                token=self.token,
            )
        )
        if archive.stat().st_size != int(transport["size"]) or sha256_file(archive) != str(
            transport["sha256"]
        ):
            raise ValueError("checkpoint transport archive failed verification")
        expected = {str(name) for name in contents["files"]}
        with tarfile.open(archive, "r:") as source:
            members = source.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ValueError("checkpoint transport archive has duplicate members")
            for member in members:
                if not member.isfile() or _safe_relative(member.name).as_posix() != member.name:
                    raise ValueError(f"unsafe checkpoint archive member: {member.name!r}")
            if set(names) != expected:
                raise ValueError("checkpoint transport archive members differ from manifest")
            for member in members:
                input_file = source.extractfile(member)
                if input_file is None:
                    raise ValueError(f"checkpoint archive member is unreadable: {member.name!r}")
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file)

    def _download_flat(self, commit: str, destination: Path, contents: dict[str, Any]) -> None:
        for name in contents["files"]:
            relative = _safe_relative(str(name))
            source = hf_hub_download(
                repo_id=self.repo_id,
                repo_type="dataset",
                filename=name,
                revision=commit,
                token=self.token,
            )
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def _safe_relative(name: str) -> PurePosixPath:
    relative = PurePosixPath(name)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or "" in relative.parts
    ):
        raise ValueError(f"unsafe checkpoint path: {name!r}")
    return relative
