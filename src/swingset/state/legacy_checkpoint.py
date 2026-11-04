"""Read-only file closure checks for imported SQLite schema-37 checkpoints.

This is an import reader, never a pipeline database backend. Content-addressed
objects are followed from durable references; an unused object-index row does
not turn a legitimately collected cache file into a required history file.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from swingset.fetch.archive import Archive, canonical


class LegacyFilesRejected(ValueError):
    """A durable SQLite reference cannot be satisfied by the checkpoint files."""


def recipe_scope_references(conn: sqlite3.Connection) -> frozenset[tuple[str, str]]:
    """Collect every retained source/derive recipe without loading large fields."""
    from .legacy_recipes import scope_references

    references: set[tuple[str, str]] = set()
    for table in (
        "source_generations",
        "source_equivalences",
        "derivation_generations",
        "derivation_recipes",
    ):
        for rowid, stored in conn.execute(f"SELECT rowid,typeof(recipe_json) FROM {table}"):
            if stored == "null":
                continue
            if stored != "text":
                raise LegacyFilesRejected("a legacy recipe is not TEXT")
            with conn.blobopen(table, "recipe_json", rowid, readonly=True) as blob:
                references.update(scope_references(iter(lambda: blob.read(65536), b"")))
    return frozenset(references)


def verify_sqlite_files(
    state_dir: Path,
    conn: sqlite3.Connection,
    *,
    include_paths: bool = False,
    max_file_bytes: int | None = None,
    include_publication: bool = True,
) -> dict[str, Any]:
    from .body_packs import STUB, pack_manifest
    from .objects import Store

    root = state_dir.resolve()
    archive, store = Archive(root), Store(root)
    verified: set[Path] = set()
    objects: set[tuple[str, str]] = set()
    generations: set[str] = set()
    manifests: set[str] = set()
    artifacts: set[str] = set()
    bundles: set[str] = set()
    measured: set[Path] = set()
    total_bytes = 0
    missing_declarations = 0

    def safe(path: Path) -> Path:
        nonlocal total_bytes
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise LegacyFilesRejected("a referenced checkpoint file is absent or outside its root")
        if path.resolve() not in measured:
            measured.add(path.resolve())
            total_bytes += path.stat().st_size
            if max_file_bytes is not None and total_bytes > max_file_bytes:
                raise LegacyFilesRejected("required source files exceed the sample budget")
        return path

    def digest_file(path: Path) -> str:
        checksum = hashlib.sha256()
        with safe(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                checksum.update(block)
        verified.add(path.resolve())
        return checksum.hexdigest()

    def raw(value: Any, kind: str) -> None:
        if value is None:
            return
        try:
            Archive._validate_hash(str(value))
            path = (
                archive.blob_path(str(value))
                if kind == "body"
                else archive.extract_path(str(value))
            )
            safe(path)
            if path.resolve() in verified:
                return
            if kind == "body":
                Archive._verify_body(path, str(value))
                verified.add(path.resolve())
            elif digest_file(path) != value:
                raise LegacyFilesRejected("a referenced extract digest differs")
        except (OSError, ValueError, EOFError):
            raise LegacyFilesRejected("a referenced raw artifact is absent or corrupt") from None

    def object_file(kind: str, digest: str) -> None:
        if (kind, digest) in objects:
            return
        if (
            conn.execute(
                "SELECT 1 FROM objects WHERE class=? AND digest=?", (kind, digest)
            ).fetchone()
            is None
        ):
            raise LegacyFilesRejected("a referenced packed object has no storage ledger row")
        try:
            checksum = hashlib.sha256()
            safe(store.path(kind, digest))
            with store.open(kind, digest) as stream:
                for block in iter(lambda: stream.read(1 << 16), b""):
                    checksum.update(block)
            if checksum.hexdigest() != digest:
                raise LegacyFilesRejected("a referenced packed object digest differs")
        except (OSError, ValueError, EOFError, RuntimeError):
            raise LegacyFilesRejected("a referenced packed object is absent or corrupt") from None
        verified.add(store.path(kind, digest).resolve())
        objects.add((kind, digest))

    def source_manifest(body: str) -> None:
        try:
            values = json.loads(body)
            if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                raise ValueError
            for item in values:
                raw(item.get("body_sha256"), "body")
                raw(item.get("extract_sha256"), "extract")
        except (TypeError, ValueError):
            raise LegacyFilesRejected(
                "a source generation has an unsupported artifact manifest"
            ) from None

    for body, extract in conn.execute("SELECT body_sha256,extract_sha256 FROM snapshots"):
        raw(body, "body")
        raw(extract, "extract")
    for (body,) in conn.execute("SELECT robots_sha256 FROM hosts WHERE robots_sha256 IS NOT NULL"):
        raw(body, "body")
    for (body,) in conn.execute(
        "SELECT manifest_json FROM source_generations WHERE manifest_json<>?", (STUB,)
    ):
        source_manifest(str(body))
    for (digest,) in conn.execute("SELECT DISTINCT pack_digest FROM source_generation_packs"):
        object_file("parse_body", str(digest))
        try:
            source_manifest(pack_manifest(store, str(digest)))
        except RuntimeError:
            raise LegacyFilesRejected("a source-body pack cannot be decoded") from None
    for (digest,) in conn.execute("SELECT DISTINCT pack_digest FROM label_body_packs"):
        object_file("label_body", str(digest))
    for kind, digest in conn.execute(
        "SELECT r.kind,r.sha256 FROM finding_support_references r JOIN findings f USING(finding_id) WHERE f.closed_at IS NULL"
    ):
        if kind in {"body", "extract"}:
            declared = (
                archive.blob_path(str(digest))
                if kind == "body"
                else archive.extract_path(str(digest))
            )
            if declared.is_file():
                raw(digest, kind)
            else:
                missing_declarations += 1
        elif kind == "generation":
            generations.add(str(digest))
    for (identifier,) in conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes WHERE materialized_generation_id IS NOT NULL"
    ):
        generations.add(str(identifier))
    for (identifier,) in conn.execute(
        "SELECT generation_id FROM derivation_generations WHERE stage='project' AND unit_kind IN ('history','map')"
    ):
        generations.add(str(identifier))
    row = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    if row and row[0]:
        bundles.add(str(row[0]))
    for (digest,) in conn.execute(
        "SELECT artifact_digest FROM release_partitions WHERE artifact_digest IS NOT NULL"
    ):
        artifacts.add(str(digest))

    candidates: set[Path] = set()
    baseline = root / "baseline"
    if include_publication and baseline.is_symlink():
        candidate = baseline.resolve()
        safe(candidate / "PUBLISHED")
        candidates.add(candidate)
    checkpoint = root / "checkpoint.json"
    if include_publication and checkpoint.is_file():
        metadata = json.loads(checkpoint.read_text())
        for key in ("baseline_candidate", "pending_candidate"):
            if metadata.get(key):
                candidate = root / "candidates" / str(metadata[key])
                if key == "baseline_candidate":
                    safe(candidate / "PUBLISHED")
                candidates.add(candidate)
    candidates_dir = root / "candidates"
    if include_publication and candidates_dir.is_dir():
        candidates.update(
            path for path in candidates_dir.iterdir() if (path / "PUBLISHING").is_file()
        )
    for path in sorted(candidates):
        try:
            built = json.loads(safe(path / "BUILT").read_text())
            manifest_path = safe(path / "_meta" / "manifest.json")
            if digest_file(manifest_path) != built["manifest_hash"]:
                raise LegacyFilesRejected("candidate manifest hash differs from BUILT")
            manifest = json.loads(manifest_path.read_text())
            for name, checksum in manifest["files"].items():
                if digest_file(path / name) != checksum:
                    raise LegacyFilesRejected("a candidate artifact digest differs")
            for name in ("BUILT", "PUBLISHING", "PUBLISHED"):
                record = path / name
                if record.is_file():
                    json.loads(record.read_text())
                    verified.add(safe(record).resolve())
            if manifest.get("input_bundle_hash"):
                bundles.add(str(manifest["input_bundle_hash"]))
            policy = manifest.get("release_policy") or {}
            if policy.get("mode") == "closure":
                selected = policy.get("closure", {}).get("selected_generations")
                if not isinstance(selected, list):
                    raise LegacyFilesRejected("a candidate's selected closure is absent")
                generations.update(str(item) for item in selected)
            elif policy.get("mode") == "partitioned":
                manifests.add(str(policy["manifest_digest"]))
        except (OSError, ValueError, KeyError, TypeError):
            raise LegacyFilesRejected("a referenced candidate is incomplete or corrupt") from None
    holds = root / "holds"
    if include_publication and holds.is_dir():
        for path in holds.iterdir():
            if not path.is_file():
                continue
            hold = json.loads(path.read_text())
            verified.add(safe(path).resolve())
            generations.update(str(value) for value in hold.get("generations", []))
            for digest in hold.get("artifacts", []):
                if archive.blob_path(str(digest)).is_file():
                    raw(digest, "body")
                elif archive.extract_path(str(digest)).is_file():
                    raw(digest, "extract")
                else:
                    raise LegacyFilesRejected("a hold names a missing raw artifact")
    for digest in sorted(manifests):
        preferred = store.path("release_manifest", digest)
        path = (
            preferred
            if preferred.is_file()
            else root / "partitions" / "manifests" / f"{digest}.json"
        )
        if digest_file(path) != digest:
            raise LegacyFilesRejected("release manifest digest differs")
        if preferred.is_file():
            object_file("release_manifest", digest)
        manifest = json.loads(path.read_text())
        artifacts.update(str(artifact) for _partition, artifact in manifest["built"])
    for digest in sorted(artifacts):
        Archive._validate_hash(digest)
        directory = root / "partitions" / digest[:2] / digest
        sidecar = directory / "sidecar.json"
        if digest_file(sidecar) != digest:
            raise LegacyFilesRejected("a referenced partition sidecar digest differs")
        record = json.loads(sidecar.read_text())
        for entry in record["tables"].values():
            if digest_file(directory / entry["file"]) != entry["sha256"]:
                raise LegacyFilesRejected("a referenced partition file digest differs")
        generations.update(str(item) for item in record.get("generations", []))
    pending_generations = list(generations)
    visited_generations: set[str] = set()
    while pending_generations:
        identifier = pending_generations.pop()
        if identifier in visited_generations:
            continue
        visited_generations.add(identifier)
        row = conn.execute(
            "SELECT output_digest,previous_generation_id FROM derivation_generations WHERE generation_id=?",
            (identifier,),
        ).fetchone()
        if row is None:
            raise LegacyFilesRejected("a retained pointer names an absent derivation generation")
        if row[1] is not None:
            pending_generations.append(str(row[1]))
        if (
            conn.execute(
                "SELECT 1 FROM derivation_evicted WHERE generation_id=?", (identifier,)
            ).fetchone()
            is not None
        ):
            object_file("derivation_output", str(row[0]))
    from .legacy_recipes import resolve_scope_bundles

    references = recipe_scope_references(conn)
    if references:
        code_files = {
            path.relative_to(root).as_posix(): path
            for path in (root / "inputs").rglob("*")
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)
        }
        try:
            legacy_bundles = resolve_scope_bundles(code_files, references, preferred=bundles)
        except ValueError as error:
            raise LegacyFilesRejected(str(error)) from None
        bundles.update(legacy_bundles.values())
    for digest in sorted(bundles):
        Archive._validate_hash(digest)
        directory = root / "inputs" / digest
        manifest = json.loads(safe(directory / "manifest.json").read_text())
        if hashlib.sha256(canonical(manifest)).hexdigest() != digest:
            raise LegacyFilesRejected("an input bundle's manifest does not match its identity")
        for name, checksum in manifest.items():
            if digest_file(directory / name) != checksum:
                raise LegacyFilesRejected("an input bundle member digest differs")
        verified.add((directory / "manifest.json").resolve())
    return {
        **(
            {"paths": sorted(path.relative_to(root).as_posix() for path in verified)}
            if include_paths
            else {}
        ),
        "bytes": total_bytes,
        "missing_finding_declarations": missing_declarations,
        "files": len(verified),
        "objects": len(objects),
        "candidates": len(candidates),
        "input_bundles": len(bundles),
        "legacy_code_scopes": len(references),
        "complete": True,
    }
