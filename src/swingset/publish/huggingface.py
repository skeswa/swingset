from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from huggingface_hub import (
    CommitOperationAdd,
    CommitOperationDelete,
    HfApi,
    hf_hub_download,
)

from swingset.publish.service import RemoteCommit

_INITIAL_CARD = b"---\nlicense: odc-by\n---\n"


class HuggingFaceHub:
    """Public-repository adapter; the journal logic remains independently testable."""

    def __init__(self, repo_id: str, *, token: str, api: HfApi | None = None) -> None:
        self.repo_id = repo_id
        self.token = token
        self.api = api or HfApi(token=token)

    def head(self) -> str | None:
        try:
            value = self.api.repo_info(self.repo_id, repo_type="dataset").sha
            return str(value) if value is not None else None
        except Exception as error:
            raise RuntimeError(
                f"cannot read dataset repository {self.repo_id}; verify repository and token access"
            ) from error

    def is_initial_head(self, commit: str) -> bool:
        files = set(self.api.list_repo_files(self.repo_id, repo_type="dataset", revision=commit))
        if not files <= {".gitattributes", "README.md"}:
            return False
        return "README.md" not in files or self._download("README.md", commit).read_bytes() == _INITIAL_CARD

    def _download(self, name: str, commit: str) -> Path:
        return Path(
            hf_hub_download(
                repo_id=self.repo_id,
                repo_type="dataset",
                filename=name,
                revision=commit,
                token=self.token,
            )
        )

    def inspect(self, commit: str) -> RemoteCommit:
        try:
            manifest_path = self._download("_meta/manifest.json", commit)
        except Exception as error:
            if type(error).__name__ == "EntryNotFoundError":
                raise FileNotFoundError("remote manifest is absent") from error
            raise
        manifest: dict[str, Any] = json.loads(manifest_path.read_text())
        files = {
            name: hashlib.sha256(self._download(name, commit).read_bytes()).hexdigest()
            for name in manifest["files"]
        }
        files["_meta/manifest.json"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        return RemoteCommit(
            commit=commit,
            parent=manifest.get("expected_parent"),
            candidate_id=str(manifest["candidate_id"]),
            manifest_hash=files["_meta/manifest.json"],
            files=files,
        )

    def create_commit(
        self,
        *,
        parent: str | None,
        message: str,
        additions: dict[str, Path],
        deletions: tuple[str, ...],
    ) -> str:
        operations: list[Any] = [
            CommitOperationAdd(path_in_repo=name, path_or_fileobj=path)
            for name, path in sorted(additions.items())
        ]
        operations.extend(CommitOperationDelete(path_in_repo=name) for name in deletions)
        result = self.api.create_commit(
            repo_id=self.repo_id,
            repo_type="dataset",
            operations=operations,
            commit_message=message,
            parent_commit=parent,
        )
        return str(result.oid)
