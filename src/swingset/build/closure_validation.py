"""Reuse a closure proof only while its exact database evidence is unchanged."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


class _Digest:
    def __init__(self) -> None:
        self.hash = hashlib.sha256()

    def value(self, value: Any) -> None:
        # Length framing distinguishes NULL, strings, bytes and adjacent fields
        # without copying large JSON values into another JSON representation.
        body = value if isinstance(value, bytes) else str(value).encode()
        self.hash.update(type(value).__name__.encode() + b":" + str(len(body)).encode() + b":")
        self.hash.update(body)

    def rows(self, conn: sqlite3.Connection, sql: str, parameters: tuple[str, ...] = ()) -> None:
        self.value(sql)
        for row in conn.execute(sql, parameters):
            self.value(len(row))
            for value in row:
                self.value(value)


@dataclass(frozen=True)
class _ReadSet:
    generations: str
    dependencies: str
    sources: str
    snapshots: str
    event_witness: Mapping[str, Any] | None = None

    @classmethod
    def capture(cls, manifest: Mapping[str, Any], sources: Iterable[str]) -> _ReadSet:
        def identifiers(values: Iterable[str]) -> str:
            return json.dumps(sorted(set(values)), separators=(",", ":"))

        from .event_coverage import read_set

        return cls(
            identifiers(row["generation_id"] for row in manifest["selected"]),
            identifiers(manifest["dependency_sets"]),
            identifiers(sources),
            identifiers(row["snapshot_id"] for row in manifest["source_support"]),
            read_set(manifest["event_coverage"], manifest["source_support"])
            if manifest.get("event_coverage") is not None
            else None,
        )

    def fingerprint(self, conn: sqlite3.Connection) -> str | None:
        result = _Digest()
        result.rows(
            conn,
            "SELECT * FROM derivation_generations WHERE generation_id IN "
            "(SELECT value FROM json_each(?)) ORDER BY generation_id",
            (self.generations,),
        )
        # Some dependency arrays are registry-sized. Verify their bytes in
        # bounded chunks, just as full closure validation does.
        result.value("dependency manifests")
        for row in conn.execute(
            "SELECT dependency_set_id,rowid FROM derivation_dependency_sets "
            "WHERE dependency_set_id IN (SELECT value FROM json_each(?)) "
            "ORDER BY dependency_set_id",
            (self.dependencies,),
        ):
            result.value(row[0])
            with conn.blobopen(
                "derivation_dependency_sets", "manifest_json", row[1], readonly=True
            ) as blob:
                result.value(len(blob))
                while block := blob.read(65536):
                    result.hash.update(block)
        result.rows(
            conn,
            "SELECT generation_id,state,input_fingerprint,contract_version,page_kind,"
            "created_at,recipe_json,report_json,result_json FROM source_generations "
            "WHERE generation_id IN (SELECT value FROM json_each(?)) ORDER BY generation_id",
            (self.sources,),
        )
        result.rows(
            conn,
            "SELECT DISTINCT generation_id FROM admission_decisions WHERE state='accepted' "
            "AND generation_id IN (SELECT value FROM json_each(?)) ORDER BY generation_id",
            (self.sources,),
        )
        result.rows(conn, "SELECT * FROM admission_policies ORDER BY page_kind")
        result.rows(
            conn,
            "SELECT snapshot_id,body_sha256 FROM snapshots WHERE snapshot_id IN "
            "(SELECT value FROM json_each(?)) ORDER BY snapshot_id",
            (self.snapshots,),
        )
        result.rows(
            conn,
            "SELECT generation_id,input_fingerprint,recipe_json FROM source_generations "
            "WHERE state='revoked' ORDER BY generation_id",
        )
        if self.event_witness is not None:
            from .event_coverage import fingerprint

            event_hash = fingerprint(conn, self.event_witness)
            if event_hash is None:
                return None
            result.value(event_hash)
        return result.hash.hexdigest()


@dataclass
class _Proof:
    manifest_digest: str
    read_set: _ReadSet
    fingerprint: str


@dataclass
class _Scope:
    connection: sqlite3.Connection
    proof: _Proof | None = None


_scope: ContextVar[_Scope | None] = ContextVar("closure_validation_scope", default=None)


@contextmanager
def validation_scope(conn: sqlite3.Connection) -> Iterator[None]:
    """Limit proof reuse to one completion operation; retain no parsed payloads."""
    if not conn.in_transaction:
        raise ValueError("closure proof reuse requires a transaction")
    token = _scope.set(_Scope(conn))
    try:
        yield
    finally:
        _scope.reset(token)


def unchanged(conn: sqlite3.Connection, manifest_digest: str) -> bool:
    scope = _scope.get()
    if scope is None or scope.connection is not conn or not conn.in_transaction:
        return False
    proof = scope.proof
    if proof is None or proof.manifest_digest != manifest_digest:
        return False
    if proof.read_set.fingerprint(conn) == proof.fingerprint:
        return True
    scope.proof = None
    return False


def remember(conn: sqlite3.Connection, manifest: Mapping[str, Any], sources: Iterable[str]) -> None:
    scope = _scope.get()
    if scope is None or scope.connection is not conn or not conn.in_transaction:
        return
    read_set = _ReadSet.capture(manifest, sources)
    fingerprint = read_set.fingerprint(conn)
    scope.proof = (
        _Proof(str(manifest["digest"]), read_set, fingerprint) if fingerprint is not None else None
    )
