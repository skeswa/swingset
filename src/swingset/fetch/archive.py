"""Content-addressed files become durable before SQLite can reference them."""

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

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


class Archive:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

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
        body = gzip.decompress(self.blob_path(sha256).read_bytes())
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
        from typing import cast

        body = self.extract_path(sha256).read_bytes()
        if digest(body) != sha256:
            raise ValueError(f"corrupt extract: {sha256}")
        return cast(JsonValue, json.loads(body))
