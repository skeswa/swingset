"""Unpack a checked SQLite-37 backup without opening a pipeline database.

This is the explicit bridge from retained SQLite sets to the native importer.
It does not activate a release, contact a Hub or create a Postgres database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import zlib
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from swingset.backup.huggingface import safe_relative
from swingset.backup.restore_set import SetError, _check_file, _extract, _join
from swingset.backup.segments import FORMAT, INDEX, read_index
from swingset.backup.transport import (
    MANIFEST,
    SET_ID,
    SETS,
    Transport,
    TransportError,
    segment_path,
)
from swingset.build.files import fsync_dir

from .legacy_checkpoint import verify_sqlite_files

SOURCE_SCHEMA_VERSION = 37
_RESERVED = frozenset(
    {
        "database.json",
        "state.sqlite",
        "state.sqlite-wal",
        "state.sqlite-shm",
        "state.sqlite-journal",
        "baseline",
        "RESTORE_PENDING",
        "checkpoints",
        "gc",
        "tmp",
    }
)


def unpack(
    source: Transport,
    target: Path,
    *,
    set_id: str,
    expected_manifest_sha256: str | None = None,
    keep_failed: bool = False,
    supplemental_inputs: Path | None = None,
) -> dict[str, Any]:
    """Verify a legacy set and place it in a strictly new private directory.

    Existing targets are never adopted. By default failed attempts remove their
    own directories. keep_failed retains a fenced workspace for diagnosis;
    another attempt still requires a fresh target.
    """
    if not SET_ID.fullmatch(set_id):
        raise ValueError("invalid legacy backup set id")
    target = target.absolute()
    target.mkdir(mode=0o700)
    created = target.stat()
    root: Path | None = None
    complete = False
    fence_identity: tuple[int, int] | None = None
    try:
        root = Path(tempfile.mkdtemp(prefix=".sqlite37-", dir=target.parent))
        stage, fetched = root / "state", root / "fetch"
        stage.mkdir()
        fetched.mkdir()
        if keep_failed:
            _fence(target / "RESTORE_PENDING", {"workspace": str(root), "complete": False})
            fence = (target / "RESTORE_PENDING").stat()
            fence_identity = fence.st_dev, fence.st_ino
        try:
            result = _unpack(
                source, stage, fetched, set_id, expected_manifest_sha256, supplemental_inputs
            )
        except (
            SetError,
            TransportError,
            KeyError,
            TypeError,
            sqlite3.DatabaseError,
            tarfile.TarError,
            zlib.error,
        ) as error:
            raise ValueError(f"legacy backup failed verification: {error}") from error
        current = target.lstat()
        expected = {"RESTORE_PENDING"} if keep_failed else set()
        if (
            (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino)
            or target.is_symlink()
            or {path.name for path in target.iterdir()} != expected
        ):
            raise ValueError("legacy unpack target changed while checking the set")
        if keep_failed:
            fence = (target / "RESTORE_PENDING").lstat()
            if (fence.st_dev, fence.st_ino) != fence_identity:
                raise ValueError("legacy unpack fence changed while checking the set")
        for path in stage.iterdir():
            os.replace(path, target / path.name)
        if keep_failed:
            (target / "RESTORE_PENDING").unlink()
        fsync_dir(target)
        complete = True
        return {**result, "state_sqlite": str(target / "state.sqlite"), "complete": True}
    except BaseException as error:
        owned = False
        if target.exists() and not target.is_symlink():
            current = target.stat()
            owned = (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino)
        if keep_failed and root is not None:
            failure = {
                "format": "swingset-sqlite37-unpack-failure-v1",
                "set_id": set_id,
                "workspace": str(root),
                "complete": False,
                "error_class": type(error).__name__,
                "error": str(error)[:1000],
            }
            # Diagnostic writes must never replace the original failure.
            for directory in (root, root / "state", *((target,) if owned else ())):
                try:
                    _fence(directory / "UNPACK_FAILED.json", failure)
                    if not (directory / "RESTORE_PENDING").exists():
                        _fence(directory / "RESTORE_PENDING", failure)
                except OSError:
                    pass
        elif owned:
            shutil.rmtree(target)
        raise
    finally:
        if root is not None and (complete or not keep_failed):
            shutil.rmtree(root)


def _fence(path: Path, value: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(value, output, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    fsync_dir(path.parent)


def _unpack(
    source: Transport,
    stage: Path,
    fetched: Path,
    set_id: str,
    expected_manifest_sha256: str | None,
    supplemental_inputs: Path | None,
) -> dict[str, Any]:
    prefix = f"{SETS}/{set_id}"
    body = source.fetch(f"{prefix}/{MANIFEST}", fetched).read_bytes()
    manifest_sha256 = hashlib.sha256(body).hexdigest()
    if expected_manifest_sha256 is not None and manifest_sha256 != expected_manifest_sha256:
        raise ValueError("legacy backup manifest does not match its digest")
    manifest = json.loads(body)
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("not a legacy SQLite backup set")
    if manifest.get("set_id") != set_id:
        raise ValueError("legacy backup manifest names a different set")
    if manifest.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ValueError("legacy backup is not SQLite schema 37")
    database = manifest["database"]
    if not isinstance(database, dict) or "format" in database:
        raise ValueError("native Postgres sets require the native restore command")
    if manifest.get("missing") != []:
        raise ValueError("legacy backup was written without required files")
    records = database["parts"]
    if not isinstance(records, list) or not records:
        raise ValueError("legacy backup has no database parts")
    parts = []
    for record in records:
        relative = safe_relative(record["path"])
        if len(relative.parts) != 2 or relative.parts[0] != "db":
            raise ValueError("legacy database part path is invalid")
        path = source.fetch(f"{prefix}/{relative.as_posix()}", fetched)
        _check_file(path, record["sha256"], record["bytes"], relative.as_posix())
        parts.append(path)
    index = manifest["index"]
    if index["path"] != INDEX:
        raise ValueError("legacy backup index path is invalid")
    index_path = source.fetch(f"{prefix}/{INDEX}", fetched)
    _check_file(index_path, index["sha256"], index["bytes"], INDEX)
    segments = {}
    for digest in manifest["segments"]:
        if digest in segments:
            raise ValueError("legacy backup names a segment twice")
        path = source.fetch(segment_path(digest), fetched)
        _check_file(path, digest, None, f"segment {digest}")
        segments[digest] = path
    # No unverified compressed bytes reach the decoder or extraction step.
    _join(parts, stage / "state.sqlite", database["raw_sha256"], database["raw_bytes"])
    wanted: dict[str, dict[str, tuple[str, str, int]]] = defaultdict(dict)
    seen = set()
    count = total = 0
    classes: dict[str, dict[str, int]] = {}
    for entry in read_index(index_path):
        relative = safe_relative(entry.path)
        if relative.parts[0] in _RESERVED or relative.as_posix() != entry.path:
            raise ValueError("legacy index names a reserved or unsafe state path")
        if entry.path in seen:
            raise ValueError("legacy index names a file twice")
        if entry.segment not in segments:
            raise ValueError("legacy index names an unlisted segment")
        seen.add(entry.path)
        wanted[entry.segment][entry.path] = (entry.cls, entry.sha256, entry.bytes)
        count += 1
        total += entry.bytes
        counts = classes.setdefault(entry.cls, {"items": 0, "bytes": 0})
        counts["items"] += 1
        counts["bytes"] += entry.bytes
    if count != index["items"] or total != index["item_bytes"]:
        raise ValueError("legacy index counts or byte total differ")
    if classes != manifest["classes"]:
        raise ValueError("legacy index classes differ from the manifest")
    seen.clear()
    for digest, expected in wanted.items():
        _extract(segments[digest], expected, stage)
        expected.clear()
    for key in ("baseline_candidate", "pending_candidate"):
        candidate = manifest.get(key)
        if candidate is not None:
            relative = safe_relative(candidate)
            if (
                len(relative.parts) != 1
                or not (stage / "candidates" / candidate / "BUILT").is_file()
            ):
                raise ValueError("legacy publication candidate is absent or unsafe")
    baseline = manifest.get("baseline_candidate")
    if baseline is not None:
        os.symlink(Path("candidates") / baseline, stage / "baseline")
    pending = manifest.get("pending_candidate")
    if pending is not None and not (stage / "candidates" / pending / "PUBLISHING").is_file():
        raise ValueError("legacy pending candidate has no publication intent")
    supplements = (
        _supplement_inputs(supplemental_inputs, stage) if supplemental_inputs is not None else []
    )
    with closing(
        sqlite3.connect((stage / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as conn:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA user_version").fetchone() != (SOURCE_SCHEMA_VERSION,):
            raise ValueError("legacy database is not SQLite schema 37")
        if conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() != ("37",):
            raise ValueError("legacy database schema metadata differs")
        from .postgres_schema import reference_manifest

        objects = [
            dict(zip(("type", "name", "tbl_name", "sql"), row, strict=True))
            for row in conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ]
        if objects != reference_manifest()["objects"]:
            raise ValueError("legacy database schema or trigger fingerprint differs")
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("legacy database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("legacy database foreign-key check failed")
        actual = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
        if (actual[0] if actual else None) != manifest.get("input_bundle_hash"):
            raise ValueError("legacy input bundle differs from the captured database")
        closure = verify_sqlite_files(stage, conn)
    return {
        "format": "swingset-sqlite37-unpack-v1",
        "set_id": set_id,
        "manifest_sha256": manifest_sha256,
        "source_sha256": database["raw_sha256"],
        "source_bytes": database["raw_bytes"],
        "schema_version": SOURCE_SCHEMA_VERSION,
        "baseline_candidate": baseline,
        "pending_candidate": manifest.get("pending_candidate"),
        "indexed_files": count,
        "indexed_bytes": total,
        "segments": len(segments),
        "supplemental_input_bundles": supplements,
        "closure": closure,
    }


def _supplement_inputs(source: Path, stage: Path) -> list[dict[str, Any]]:
    """Copy complete immutable witnesses; never replace checkpoint members."""
    from .legacy_recipes import verify_bundle

    if source.is_symlink() or not source.is_dir():
        raise ValueError("supplemental inputs must be a real directory")

    def inventory(directory: Path, bundle: str) -> dict[str, Path]:
        def fail(error: OSError) -> None:
            raise error

        files = {}
        for parent, directories, names in os.walk(directory, followlinks=False, onerror=fail):
            for name in (*directories, *names):
                path = Path(parent) / name
                if path.is_symlink() or not (path.is_dir() or path.is_file()):
                    raise ValueError("supplemental bundle contains a link or special file")
                if path.is_file():
                    files[f"inputs/{bundle}/{path.relative_to(directory).as_posix()}"] = path
        return files

    result = []
    for directory in sorted(source.iterdir()):
        bundle = directory.name
        if (
            not re.fullmatch(r"[0-9a-f]{64}", bundle)
            or directory.is_symlink()
            or not directory.is_dir()
        ):
            raise ValueError("supplemental inputs contain an invalid bundle directory")
        files = inventory(directory, bundle)
        expected = verify_bundle(files, bundle)
        if set(files) != expected:
            raise ValueError("supplemental bundle contains undeclared files")
        destination = stage / "inputs" / bundle
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError("supplemental bundle collides with a checkpoint path")
            copied = inventory(destination, bundle)
            if set(copied) != expected or verify_bundle(copied, bundle) != expected:
                raise ValueError("supplemental bundle differs from checkpoint members")
            manifest_name = f"inputs/{bundle}/manifest.json"
            if files[manifest_name].read_bytes() != copied[manifest_name].read_bytes():
                raise ValueError("supplemental manifest differs from checkpoint bytes")
            added = False
        else:
            destination.mkdir(parents=True)
            for name in sorted(expected):
                relative = Path(name).relative_to(Path("inputs") / bundle)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with files[name].open("rb") as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1 << 20)
            copied = inventory(destination, bundle)
            # Recheck copied bytes, including semantic dispatch digests, against
            # the bundle identity. A changed source cannot become an accepted copy.
            if set(copied) != expected or verify_bundle(copied, bundle) != expected:
                raise ValueError("supplemental bundle changed while copying")
            added = True
        result.append(
            {
                "bundle": bundle,
                "manifest_sha256": hashlib.sha256(
                    (destination / "manifest.json").read_bytes()
                ).hexdigest(),
                "files": len(copied),
                "bytes": sum(path.stat().st_size for path in copied.values()),
                "added": added,
            }
        )
    return result
