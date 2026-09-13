"""Stream immutable derivation manifests without retaining registry-sized JSON."""

from __future__ import annotations

import codecs
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from typing import Any


class ClosureError(RuntimeError):
    pass


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def members(conn: sqlite3.Connection, identifier: str) -> Iterator[dict[str, Any]]:
    """Verify and decode one immutable JSON array using bounded blob reads."""
    row = conn.execute(
        "SELECT rowid FROM derivation_dependency_sets WHERE dependency_set_id=?", (identifier,)
    ).fetchone()
    if row is None:
        raise ClosureError("dependency_manifest_missing")
    with conn.blobopen(
        "derivation_dependency_sets", "manifest_json", row[0], readonly=True
    ) as blob:
        actual = hashlib.sha256()
        while block := blob.read(65536):
            actual.update(block)
        if actual.hexdigest() != identifier:
            raise ClosureError("dependency_manifest_hash_mismatch")
        blob.seek(0)
        decoder = json.JSONDecoder()
        utf8 = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        started = False
        finished = False
        eof = False
        while not finished:
            block = blob.read(65536)
            eof = not block
            buffer += utf8.decode(block, final=eof)
            position = 0
            while True:
                while position < len(buffer) and buffer[position].isspace():
                    position += 1
                if not started:
                    if position == len(buffer):
                        break
                    if buffer[position] != "[":
                        raise ClosureError("dependency_manifest_not_array")
                    started = True
                    position += 1
                    continue
                if position == len(buffer):
                    break
                if buffer[position] == "]":
                    position += 1
                    finished = True
                    break
                if buffer[position] == ",":
                    position += 1
                    continue
                try:
                    item, end = decoder.raw_decode(buffer, position)
                except json.JSONDecodeError:
                    if eof:
                        raise ClosureError("dependency_manifest_invalid_json") from None
                    break
                if not isinstance(item, dict):
                    raise ClosureError("dependency_manifest_member_invalid")
                yield item
                position = end
            buffer = buffer[position:]
            if eof and not finished:
                raise ClosureError("dependency_manifest_truncated")
        if buffer.strip() or blob.read(1):
            raise ClosureError("dependency_manifest_trailing_data")


def leaves(
    conn: sqlite3.Connection, identifier: str, *, visited: set[str] | None = None
) -> Iterator[dict[str, Any]]:
    seen = visited if visited is not None else set()
    if identifier in seen:
        return
    seen.add(identifier)
    for item in members(conn, identifier):
        if item.get("kind") == "dependency_set":
            yield from leaves(conn, str(item["fingerprint"]), visited=seen)
        else:
            yield item


def generation(conn: sqlite3.Connection, identifier: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM derivation_generations WHERE generation_id=?", (identifier,)
    ).fetchone()
    if row is None:
        raise ClosureError("selected_generation_missing")
    return {
        "generation_id": row["generation_id"],
        "stage": row["stage"],
        "unit_kind": row["unit_kind"],
        "unit_id": row["unit_id"],
        "input_fingerprint": row["input_fingerprint"],
        "recipe": json.loads(row["recipe_json"]),
        "dependency_set_id": row["dependency_set_id"],
        "previous_generation_id": row["previous_generation_id"],
        "output_digest": row["output_digest"],
        "row_count": row["row_count"],
        "created_at": row["created_at"],
        "run_id": row["run_id"],
    }
