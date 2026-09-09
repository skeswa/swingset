from __future__ import annotations

import hashlib
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download


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
        path = Path(
            hf_hub_download(
                repo_id=self.repo_id,
                repo_type="dataset",
                filename="checkpoint.json",
                revision=commit,
                token=self.token,
            )
        )
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def create_commit(self, *, parent: str | None, message: str, files: dict[str, Path]) -> str:
        operations: list[Any] = [
            CommitOperationAdd(path_in_repo=name, path_or_fileobj=path)
            for name, path in sorted(files.items())
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
        import json

        contents = json.loads(manifest.read_text())
        for name in contents["files"]:
            relative = PurePosixPath(str(name))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError(f"unsafe checkpoint path in remote manifest: {name!r}")
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
