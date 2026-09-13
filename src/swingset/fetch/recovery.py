"""Restore exact artifacts from locally verified immutable private checkpoints."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import zlib
from contextlib import closing
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
                if relative.as_posix() not in manifest["files"]:
                    continue
                key = checkpoint, manifest_hash
                if key not in self._qualified:
                    self._qualify(checkpoint, manifest)
                    self._qualified[key] = manifest
                else:
                    manifest = self._qualified[key]
                path = self._contained(checkpoint, relative.as_posix())
                body = path.read_bytes()
                self._physical(body, manifest["files"][relative.as_posix()])
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
                sqlite3.Error,
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
        if (
            manifest.get("format") != 1
            or type(manifest.get("schema_version")) is not int
            or not 1 <= manifest["schema_version"] <= self.maximum_schema_version
        ):
            raise ValueError("unsupported checkpoint format or schema")
        expected = manifest["files"]
        actual = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path != root / "checkpoint.json"
        }
        if actual != set(expected):
            raise ValueError("checkpoint file closure differs from its manifest")
        for relative, record in expected.items():
            path = self._contained(root, relative)
            # Stream large databases and published tables; only the requested
            # artifact is subsequently materialized for decompression/JSON.
            if (
                not isinstance(record, dict)
                or set(record) != {"size", "sha256"}
                or type(record["size"]) is not int
                or path.stat().st_size != record["size"]
            ):
                raise ValueError("checkpoint file size differs from its manifest")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                    raise ValueError("checkpoint file digest differs from its manifest")
        database = self._contained(root, "state.sqlite")
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            schema = conn.execute("PRAGMA user_version").fetchone()[0]
            metadata = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if (
                schema != manifest["schema_version"]
                or metadata is None
                or int(metadata[0]) != schema
            ):
                raise ValueError("checkpoint declared schema differs from its actual database")
            if (
                conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or conn.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise ValueError("checkpoint database integrity failure")
            from swingset.backup.checkpoint import CheckpointError, _artifact_closure

            candidates = set()
            for field in ("baseline_candidate", "pending_candidate"):
                if identifier := manifest.get(field):
                    candidate = self._contained(root, f"candidates/{identifier}")
                    if not (candidate / "BUILT").is_file():
                        raise ValueError("checkpoint candidate is incomplete")
                    candidates.add(candidate)
            try:
                _artifact_closure(root, conn, candidates)
            except CheckpointError as exc:
                raise ValueError(str(exc)) from exc
