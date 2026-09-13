"""Content-addressed files become durable before SQLite can reference them."""

import gzip
import hashlib
import json
import os
import tempfile
import zlib
from pathlib import Path
from typing import Literal, Protocol

from swingset.sources.base import JsonValue


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def durable_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


type ArtifactKind = Literal["body", "extract"]


class ArtifactUnavailable(OSError):
    """The exact historical digest cannot currently be recovered locally."""

    def __init__(
        self,
        kind: ArtifactKind,
        sha256: str,
        reason: str,
        attempts: tuple[dict[str, str], ...] = (),
    ) -> None:
        self.kind, self.sha256, self.reason, self.attempts = kind, sha256, reason, attempts
        super().__init__(f"{kind} artifact {sha256} unavailable: {reason}")


class ArtifactRecovery(Protocol):
    def recover(self, kind: ArtifactKind, sha256: str, destination: Path) -> None: ...


class Archive:
    def __init__(self, state_dir: Path, *, recovery: ArtifactRecovery | None = None) -> None:
        self.state_dir = state_dir
        self.recovery = recovery

    def blob_path(self, sha256: str) -> Path:
        self._validate_hash(sha256)
        return self.state_dir / "blobs" / "sha256" / sha256[:2] / sha256[2:4] / sha256

    def extract_path(self, sha256: str) -> Path:
        self._validate_hash(sha256)
        return self.state_dir / "extracts" / sha256

    @staticmethod
    def _validate_hash(value: str) -> None:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("invalid artifact digest")

    def store_body(self, body: bytes) -> str:
        sha = digest(body)
        path = self.blob_path(sha)
        if not path.exists():
            durable_write(path, gzip.compress(body, mtime=0))
        return sha

    def read_body(self, sha256: str) -> bytes:
        path = self.blob_path(sha256)
        try:
            return self._body(path, sha256)
        except (OSError, ValueError, EOFError, zlib.error):
            if self.recovery is None:
                raise
            self.recovery.recover("body", sha256, path)
            return self._body(path, sha256)

    @staticmethod
    def _body(path: Path, sha256: str) -> bytes:
        body = gzip.decompress(path.read_bytes())
        if digest(body) != sha256:
            raise ValueError(f"corrupt blob: {sha256}")
        return body

    def store_extract(self, value: JsonValue) -> str:
        body = canonical(value)
        sha = digest(body)
        if not self.extract_path(sha).exists():
            durable_write(self.extract_path(sha), body)
        return sha

    def read_extract(self, sha256: str) -> JsonValue:
        path = self.extract_path(sha256)
        try:
            return self._extract(path, sha256)
        except (OSError, ValueError):
            if self.recovery is None:
                raise
            self.recovery.recover("extract", sha256, path)
            return self._extract(path, sha256)

    @staticmethod
    def _extract(path: Path, sha256: str) -> JsonValue:
        from typing import cast

        body = path.read_bytes()
        if digest(body) != sha256:
            raise ValueError(f"corrupt extract: {sha256}")
        return cast(JsonValue, json.loads(body))
