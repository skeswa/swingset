"""Shared bounded SQL loading and streaming artifact verification.

Budgets are per session. Calls never recover files or change connection settings;
SQLite/filesystem calls do not have a hard deadline. Exhaustion is unknown.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
import time
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, BinaryIO

from swingset.fetch.archive import Archive


@dataclass(frozen=True)
class Limits:
    members: int = 32
    candidates: int = 64
    rows: int = 1024
    manifest_members: int = 128
    json_bytes: int = 1024 * 1024
    total_json_bytes: int = 8 * 1024 * 1024
    compressed_bytes: int = 8 * 1024 * 1024
    decoded_bytes: int = 32 * 1024 * 1024
    seconds: float = 2.0

    def __post_init__(self) -> None:
        ceilings = {
            "members": 32,
            "candidates": 256,
            "rows": 8192,
            "manifest_members": 512,
            "json_bytes": 4 * 1024 * 1024,
            "total_json_bytes": 32 * 1024 * 1024,
            "compressed_bytes": 64 * 1024 * 1024,
            "decoded_bytes": 128 * 1024 * 1024,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be a positive integer at most {ceiling}")
        if (
            type(self.seconds) not in (int, float)
            or not math.isfinite(self.seconds)
            or not 0 < self.seconds <= 30
        ):
            raise ValueError("seconds must be positive and at most 30")


_DEFAULT_LIMITS = Limits()


class BudgetExceeded(Exception):
    """The shared bounded session cannot assess more evidence."""


# Internal compatibility name for the existing scheduling probe.
_Unknown = BudgetExceeded


class _Compressed:
    def __init__(self, raw: BinaryIO, budget: Budget):
        self.raw, self.budget = raw, budget

    def read(self, size: int = -1) -> bytes:
        self.budget.tick()
        remaining = self.budget.limits.compressed_bytes - self.budget.compressed
        if remaining <= 0:
            raise _Unknown("compressed_byte_budget")
        data = self.raw.read(min(65536, remaining, size if size >= 0 else 65536))
        self.budget.compressed += len(data)
        if len(data) == remaining and (size < 0 or size > remaining):
            raise _Unknown("compressed_byte_budget")
        return data

    def seek(self, offset: int, /) -> int:
        raise OSError("bounded verification is sequential")

    def close(self) -> None:
        pass  # The surrounding context owns the raw file.


class Budget:
    def __init__(self, conn: sqlite3.Connection, archive: Archive, limits: Limits):
        self.conn, self.archive, self.limits = conn, archive, limits
        self.deadline = time.monotonic() + limits.seconds
        self.rows = self.json_bytes = self.compressed = self.decoded = 0
        self.reasons: Counter[str] = Counter()
        self.artifacts: dict[tuple[str, str], bool] = {}

    def exhausted(self) -> bool:
        """Whether a cumulative budget is depleted, not a candidate-local cap."""
        return (
            self.rows >= self.limits.rows
            or self.json_bytes >= self.limits.total_json_bytes
            or self.compressed >= self.limits.compressed_bytes
            or self.decoded >= self.limits.decoded_bytes
            or time.monotonic() >= self.deadline
        )

    def tick(self) -> None:
        if time.monotonic() >= self.deadline:
            raise _Unknown("time_budget")

    def read(
        self, sql: str, parameters: tuple[Any, ...], columns: tuple[str, ...], *, cap: int
    ) -> list[dict[str, Any]]:
        """Do not return an oversized SQL value to Python, including corrupt BLOBs.

        SQLite may still inspect stored values internally. No PRAGMA, limit,
        progress handler, transaction, or other caller connection setting changes.
        """
        self.tick()
        remaining = self.limits.rows - self.rows
        if remaining <= 0:
            raise _Unknown("row_budget")
        limit = min(cap + 1, remaining)
        size = "+".join(f"coalesce(length(CAST({c} AS BLOB)),0)" for c in columns)
        bound = min(self.limits.json_bytes, self.limits.total_json_bytes - self.json_bytes)
        if bound <= 0:
            raise _Unknown("json_byte_budget")
        values = ",".join(f"CASE WHEN ({size})<={bound} THEN {c} END AS {c}" for c in columns)
        query = f"SELECT {values},({size}) AS _bytes FROM ({sql}) LIMIT ?"
        result: list[dict[str, Any]] = []
        cursor = self.conn.execute(query, (*parameters, limit))
        try:
            for row in cursor:
                self.rows += 1
                self.tick()
                if len(result) == cap:
                    raise _Unknown("candidate_budget")
                if row["_bytes"] > bound:
                    raise _Unknown("json_byte_budget")
                self.json_bytes += row["_bytes"]
                if self.json_bytes > self.limits.total_json_bytes:
                    raise _Unknown("json_byte_budget")
                result.append(dict(row))
        finally:
            cursor.close()
        if len(result) == remaining:
            raise _Unknown("row_budget")
        return result

    def artifact(self, kind: str, sha: str) -> bool:
        key = kind, sha
        if key in self.artifacts:
            return self.artifacts[key]
        self.tick()
        try:
            path = self.archive.blob_path(sha) if kind == "body" else self.archive.extract_path(sha)
            checksum = hashlib.sha256()
            chunks = []
            size = 0
            with path.open("rb") as raw:
                compressed = _Compressed(raw, self)
                stream = (
                    gzip.GzipFile(fileobj=compressed, mode="rb") if kind == "body" else compressed
                )
                try:
                    while True:
                        self.tick()
                        remaining = self.limits.decoded_bytes - self.decoded
                        if remaining <= 0:
                            raise _Unknown("decoded_byte_budget")
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.decoded += len(chunk)
                        size += len(chunk)
                        checksum.update(chunk)
                        if kind == "extract":
                            if size > self.limits.json_bytes:
                                raise _Unknown("extract_json_budget")
                            chunks.append(chunk)
                finally:
                    if kind == "body":
                        stream.close()
            valid = checksum.hexdigest() == sha
            if valid and kind == "extract":
                json.loads(b"".join(chunks))
            self.tick()
        except (OSError, ValueError, TypeError, EOFError, RecursionError, zlib.error):
            valid = False
        self.artifacts[key] = valid
        if not valid:
            self.reasons[kind + "_artifact_unavailable"] += 1
        return valid

    def policy_version(self, page_kind: str) -> str | None:
        rows = self.read(
            "SELECT contract_version FROM admission_policies WHERE page_kind=?",
            (page_kind,),
            ("contract_version",),
            cap=1,
        )
        return str(rows[0]["contract_version"]) if rows else None
