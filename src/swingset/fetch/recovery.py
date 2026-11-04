"""Restore exact artifacts from locally verified immutable private checkpoints."""

from __future__ import annotations

import gzip
import json
import zlib
from pathlib import Path
from typing import Any

from .archive import Archive, ArtifactKind, ArtifactUnavailable, digest, durable_write


class LocalCheckpointRecovery:
    """Share one instance across work units; healthy archive reads never call it.

    Successful closure verification is reused only for identical manifest bytes.
    Every recovery rechecks the selected file's physical and logical digests.
    Failed qualification is not cached: repaired local evidence remains usable.
    """

    def __init__(self, checkpoint_root: Path, *, maximum_schema_version: int) -> None:
        self.checkpoint_root = checkpoint_root.resolve()
        self.maximum_schema_version = maximum_schema_version
        self._qualified: dict[tuple[Path, str], dict[str, Any]] = {}
        self.restored: list[dict[str, str]] = []

    def recover(self, kind: ArtifactKind, sha256: str, destination: Path) -> None:
        Archive._validate_hash(sha256)
        target = destination.resolve()
        if target.is_relative_to(self.checkpoint_root) or any(
            (parent / "checkpoint.json").is_file() for parent in target.parents
        ):
            raise ArtifactUnavailable(kind, sha256, "recovery_target_is_immutable_checkpoint")
        relative = (
            Path("blobs/sha256") / sha256[:2] / sha256[2:4] / sha256
            if kind == "body"
            else Path("extracts") / sha256
        )
        failures = []
        try:
            candidates = sorted(self.checkpoint_root.glob("*/checkpoint.json"), reverse=True)
        except OSError as exc:
            raise ArtifactUnavailable(kind, sha256, "checkpoint_inventory_unreadable") from exc
        for manifest_path in candidates:
            checkpoint = manifest_path.parent.resolve()
            try:
                manifest_body = manifest_path.read_bytes()
                manifest_hash = digest(manifest_body)
                manifest = json.loads(manifest_body)
                # A checkpoint without this exact artifact cannot restore it and
                # need not spend time validating its unrelated file closure.
                key_name = "database/files/" + relative.as_posix()
                if key_name not in manifest["files"]:
                    continue
                key = checkpoint, manifest_hash
                if key not in self._qualified:
                    self._qualify(checkpoint, manifest)
                    self._qualified[key] = manifest
                else:
                    manifest = self._qualified[key]
                path = self._contained(checkpoint, key_name)
                body = path.read_bytes()
                self._physical(body, manifest["files"][key_name])
                decoded = gzip.decompress(body) if kind == "body" else body
                if digest(decoded) != sha256:
                    raise ValueError("artifact logical digest does not match requested history")
                if kind == "extract":
                    json.loads(decoded)
                durable_write(destination, body)
                self.restored.append(
                    {
                        "kind": kind,
                        "sha256": sha256,
                        "checkpoint": str(checkpoint),
                        "manifest_sha256": manifest_hash,
                    }
                )
                return
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                EOFError,
                zlib.error,
            ) as exc:
                failures.append({"checkpoint": str(checkpoint), "reason": str(exc)})
        raise ArtifactUnavailable(
            kind, sha256, "no_valid_matching_local_checkpoint", tuple(failures)
        )

    @staticmethod
    def _contained(root: Path, relative: str) -> Path:
        path = root / relative
        if Path(relative).is_absolute() or not path.resolve().is_relative_to(root):
            raise ValueError("checkpoint manifest path escapes its root")
        return path

    @staticmethod
    def _physical(body: bytes, record: dict[str, Any]) -> None:
        if (
            not isinstance(record, dict)
            or set(record) != {"size", "sha256"}
            or type(record["size"]) is not int
            or record["size"] != len(body)
            or not isinstance(record["sha256"], str)
            or digest(body) != record["sha256"]
        ):
            raise ValueError("checkpoint file size or digest does not match its manifest")

    def _qualify(self, root: Path, manifest: dict[str, Any]) -> None:
        from swingset.backup.checkpoint import CheckpointError, verify_checkpoint

        try:
            verified = verify_checkpoint(root, maximum_schema_version=self.maximum_schema_version)
        except CheckpointError as error:
            raise ValueError(str(error)) from error
        if verified != manifest:
            raise ValueError("checkpoint manifest changed during qualification")
