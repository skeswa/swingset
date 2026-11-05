"""Install a segmented backup set offline (architecture phase 4, WP6; D-0232).

`install` fetches `backup.json`, every database part, the index and every
segment the set names, and checks each digest before it decompresses
anything. It joins the checked parts into a filtered history COPY bundle,
checks its schema and typed rows, then extracts exactly the files the index
assigns to each segment, verifying every one (gunzip for bodies and packs).
Installation into a fresh Postgres database validates every foreign key. All
of this happens in a scratch directory inside the target; only when every
check passed are the files moved into place and the baseline link made. A
failure removes the scratch and leaves the target as it was.

`install` needs no network beyond its transport and never activates
anything: the drill calls it alone. `restore_from_set` is `swingset restore
--backup-set`: the exclusive locks, `RESTORE_PENDING`, `install`, the public
hub check and activation, as a checkpoint restore does.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tarfile
import uuid
from pathlib import Path
from typing import Any, Literal

from swingset.build.files import fsync_dir
from swingset.clock import Clock
from swingset.publish.journal import Hub

from .segments import FORMAT, INDEX, Digest, read_index
from .transport import MANIFEST, SETS, Transport, TransportError, segment_path

_CHUNK = 1 << 20
#: Top-level names an index may not write: the database, the baseline link,
#: the restore marker, and directories a set never carries.
_RESERVED = frozenset(
    {
        "database.json",
        "state.sqlite",
        "state.sqlite-wal",
        "state.sqlite-shm",
        "baseline",
        "RESTORE_PENDING",
        "checkpoints",
        "gc",
        "tmp",
    }
)


class SetError(RuntimeError):
    """A set failed to install; nothing was placed."""


def latest(source: Transport) -> str:
    found = source.sets()
    if not found:
        raise SetError("the transport holds no backup set")
    return found[-1]


def install(
    source: Transport,
    target: Path,
    *,
    set_id: str | None = None,
    maximum_schema_version: int | None = None,
    integrity: str | None = "full",
    owner_dsn: str | None = None,
    environment: str = "dev",
) -> dict[str, Any]:
    """Verify one set completely, then place it into `target`.

    `target` may hold lock files and `RESTORE_PENDING`, nothing else.
    ``integrity`` accepts the existing full/quick/None operator spelling;
    every mode checks native COPY rows, constraints and referenced files.
    """
    from swingset.state.rwlock import is_lock_file

    if integrity not in {"full", "quick", None}:
        raise ValueError(f"unknown integrity check: {integrity}")
    if maximum_schema_version is None:
        from swingset.state.db import SCHEMA_VERSION

        maximum_schema_version = SCHEMA_VERSION
    target.mkdir(parents=True, exist_ok=True)
    for path in target.iterdir():
        if path.name.startswith(".install-") and path.is_dir():
            shutil.rmtree(path)  # a crashed install's scratch
        elif not is_lock_file(path.name) and path.name not in {"RESTORE_PENDING", "database.json"}:
            raise SetError(f"restore target is not empty: {path.name}")
    work = target / f".install-{uuid.uuid4().hex}"
    stage, fetched = work / "state", work / "fetch"
    stage.mkdir(parents=True)
    fetched.mkdir()
    try:
        try:
            chosen = set_id or latest(source)
            summary = _install(
                source,
                chosen,
                stage,
                fetched,
                maximum_schema_version,
                integrity,
                target,
                owner_dsn,
                environment,
            )
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            tarfile.TarError,
            TransportError,
        ) as error:
            raise SetError(f"backup set failed verification: {error}") from error
        for path in sorted(stage.iterdir()):
            os.replace(path, target / path.name)
        fsync_dir(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return summary


def _install(
    source: Transport,
    set_id: str,
    stage: Path,
    fetched: Path,
    maximum_schema_version: int,
    integrity: str | None,
    database_target: Path,
    owner_dsn: str | None,
    environment: str,
    check_mode: Literal["verify", "drill"] | None = None,
) -> dict[str, Any]:
    from .huggingface import safe_relative

    prefix = f"{SETS}/{set_id}"
    body = source.fetch(f"{prefix}/{MANIFEST}", fetched).read_bytes()
    manifest = json.loads(body)
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise SetError(f"{set_id} is not a backup set")
    if manifest.get("set_id") != set_id:
        raise SetError(f"backup.json names {manifest.get('set_id')!r}, not {set_id!r}")
    schema = manifest.get("schema_version")
    if type(schema) is not int or not 1 <= schema <= maximum_schema_version:
        raise SetError(f"set schema {schema!r} is not supported by this program")
    database = manifest["database"]
    parts = []
    for part in database["parts"]:
        path = source.fetch(f"{prefix}/{safe_relative(part['path']).as_posix()}", fetched)
        _check_file(path, part["sha256"], part["bytes"], part["path"])
        parts.append(path)
    index = manifest["index"]
    if index["path"] != INDEX:
        raise SetError("unexpected index path")
    index_path = source.fetch(f"{prefix}/{INDEX}", fetched)
    _check_file(index_path, index["sha256"], index["bytes"], INDEX)
    segments: dict[str, Path] = {}
    for segment in manifest["segments"]:
        path = source.fetch(segment_path(str(segment)), fetched)
        _check_file(path, str(segment), None, f"segment {segment}")
        segments[str(segment)] = path
    # Every fetched digest matched: decompress and extract.
    if database.get("format") != "postgres-history":
        raise SetError("legacy SQLite sets require the explicit SQLite importer")
    packed = fetched / "database.tar"
    _join(parts, packed, database["raw_sha256"], database["raw_bytes"])
    history = fetched / "history"
    history.mkdir()
    with tarfile.open(packed, "r:") as archive:
        seen = set()
        for member in archive:
            relative = safe_relative(member.name)
            if (
                not member.isfile()
                or member.name in seen
                or relative.parts[0] not in {"tables", "keys", "history.json"}
            ):
                raise SetError("database history archive contains an unsupported member")
            seen.add(member.name)
            destination = history / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            reader = archive.extractfile(member)
            if reader is None:
                raise SetError("database history archive lacks a regular member body")
            with reader, destination.open("xb") as output:
                shutil.copyfileobj(reader, output, length=_CHUNK)
    wanted: dict[str, dict[str, tuple[str, str, int]]] = {}
    count = total = 0
    classes: dict[str, int] = {}
    for entry in read_index(index_path):
        relative = safe_relative(entry.path)
        if relative.parts[0] in _RESERVED or relative.as_posix() != entry.path:
            raise SetError(f"index names a reserved or unsafe path: {entry.path!r}")
        if entry.segment not in segments:
            raise SetError(f"index names a segment the set does not: {entry.segment}")
        bucket = wanted.setdefault(entry.segment, {})
        if entry.path in bucket:
            raise SetError(f"index names {entry.path} twice")
        bucket[entry.path] = (entry.cls, entry.sha256, entry.bytes)
        count += 1
        total += entry.bytes
        classes[entry.cls] = classes.get(entry.cls, 0) + 1
    if count != index["items"]:
        raise SetError(f"index has {count} items, backup.json says {index['items']}")
    for segment, expected in sorted(wanted.items()):
        _extract(segments[segment], expected, stage)
        expected.clear()
    for field in ("baseline_candidate", "pending_candidate"):
        name = manifest.get(field)
        if name is not None and not (stage / "candidates" / str(name) / "BUILT").is_file():
            raise SetError(f"{field} {name} is missing or incomplete")
    baseline = manifest.get("baseline_candidate")
    from swingset.state import postgres_backup

    from .snapshot import current_runtime_identity

    history_manifest = json.loads((history / "history.json").read_text())
    for relative in history_manifest["files"]:
        safe = safe_relative(relative)
        source_file = stage / safe
        destination = history / "files" / safe
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(source_file, destination)
    del history_manifest
    postgres_backup.verify(history, runtime_identity=current_runtime_identity())
    missing = manifest.get("missing") or []
    if missing:
        # A file the set left out (gone, or not matching its digest) that the
        # checks above do not reach, such as a body: the set is not whole.
        raise SetError(
            f"the set was written without {len(missing)} named files, first {missing[:3]}"
        )
    from .destination import owner

    checked = None
    if check_mode is not None:
        manifest_sha256 = hashlib.sha256((history / "history.json").read_bytes()).hexdigest()
        if check_mode == "drill":
            from .check import drill_check

            checked = drill_check(history, stage, manifest_sha256=manifest_sha256)
        else:
            from .check import check

            checked = check(history, manifest_sha256=manifest_sha256)
        if checked.get("complete") is not True:
            raise SetError("owner restore check did not complete")
    else:
        with owner(database_target, owner_dsn) as connection:
            postgres_backup.restore(
                history,
                connection,
                environment=environment,
                runtime_identity=current_runtime_identity(),
            )
        from .checkpoint import retain_restore_history

        retain_restore_history(stage, history)
    if baseline is not None and check_mode != "drill":
        os.symlink(Path("candidates") / str(baseline), stage / "baseline")
    summary = {
        "set_id": set_id,
        "manifest_sha256": hashlib.sha256(body).hexdigest(),
        "schema_version": schema,
        "db_raw_bytes": database["raw_bytes"],
        "parts": len(parts),
        "segments": len(segments),
        "items": count,
        "item_bytes": total,
        "classes": dict(sorted(classes.items())),
        "baseline_candidate": baseline,
        "pending_candidate": manifest.get("pending_candidate"),
        "integrity": integrity,
    }
    if check_mode == "drill":
        assert checked is not None
        summary["release"] = checked["release"]
        summary["restore_check"] = {
            "complete": True,
            "equal": checked["audit"]["equal"],
            "runtime_identity": checked["runtime_identity"],
        }
    return summary


def verify_set(
    source: Transport, set_id: str, *, maximum_schema_version: int | None = None
) -> dict[str, Any]:
    """Read back all published bytes and require a fresh owner-job restore check."""
    return _check_set(source, set_id, maximum_schema_version=maximum_schema_version, mode="verify")


def drill_set(
    source: Transport, set_id: str, *, maximum_schema_version: int | None = None
) -> dict[str, Any]:
    """Restore and reproduce the historical release in the separate owner job."""
    return _check_set(source, set_id, maximum_schema_version=maximum_schema_version, mode="drill")


def _check_set(
    source: Transport,
    set_id: str,
    *,
    maximum_schema_version: int | None,
    mode: Literal["verify", "drill"],
) -> dict[str, Any]:
    import tempfile

    if maximum_schema_version is None:
        from swingset.state.db import SCHEMA_VERSION

        maximum_schema_version = SCHEMA_VERSION
    check_root = os.environ.get("SWINGSET_BACKUP_CHECK_ROOT")
    if check_root is None and os.environ.get("SWINGSET_BACKUP_CHECK_SOCKET"):
        check_root = "/var/lib/swingset/checkpoints"
    if check_root:
        Path(check_root).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".install-" if mode == "drill" else "swingset-backup-check-",
        dir=check_root or "/tmp",
    ) as temporary:
        work = Path(temporary)
        stage, fetched = work / "state", work / "fetch"
        stage.mkdir()
        fetched.mkdir()
        return _install(
            source,
            set_id,
            stage,
            fetched,
            maximum_schema_version,
            "full",
            stage,
            None,
            "dev",
            check_mode=mode,
        )


def _check_file(path: Path, sha256: str, size: int | None, label: str) -> None:
    hasher = hashlib.sha256()
    length = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
            length += len(chunk)
    if (size is not None and length != size) or hasher.hexdigest() != sha256:
        raise SetError(f"{label} does not match its digest")


def _join(parts: list[Path], target: Path, sha256: str, size: int) -> None:
    import pyarrow as pa

    class Joined:
        def __init__(self) -> None:
            self.files = iter(parts)
            self.handle = next(self.files).open("rb")
            self.done = False

        def read(self, n: int = -1) -> bytes:
            while not self.done:
                data = self.handle.read(n)
                if data or n == 0:
                    return data
                following = next(self.files, None)
                if following is None:
                    self.done = True
                    break
                self.handle.close()
                self.handle = following.open("rb")
            return b""

        def close(self) -> None:
            self.handle.close()

        @property
        def closed(self) -> bool:
            return bool(self.handle.closed)

        def readable(self) -> bool:
            return True

    hasher = hashlib.sha256()
    length = 0
    joined = Joined()
    try:
        with (
            pa.CompressedInputStream(pa.PythonFile(joined, mode="r"), "zstd") as stream,
            target.open("xb") as output,
        ):
            while chunk := stream.read(_CHUNK):
                hasher.update(chunk)
                length += len(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except pa.ArrowException as error:
        raise SetError(f"database parts do not decompress: {error}") from error
    finally:
        with contextlib.suppress(OSError):
            joined.close()
    if length != size or hasher.hexdigest() != sha256:
        raise SetError("the joined database does not match its digest")


def _extract(segment: Path, expected: dict[str, tuple[str, str, int]], stage: Path) -> None:
    remaining = dict(expected)
    with tarfile.open(segment, "r:") as archive:
        for member in archive:
            if member.name not in remaining:
                continue
            cls, sha256, size = remaining.pop(member.name)
            if not member.isfile() or member.size != size:
                raise SetError(f"segment member {member.name} is not the indexed file")
            reader = archive.extractfile(member)
            if reader is None:
                raise SetError(f"segment member {member.name} is unreadable")
            destination = stage / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = Digest(cls)
            with reader, destination.open("xb") as output:
                while chunk := reader.read(_CHUNK):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.stored != size or digest.hexdigest() != sha256:
                raise SetError(f"{member.name} does not match its digest")
    if remaining:
        raise SetError(f"segment {segment.stem} lacks {len(remaining)} indexed files")


def restore_from_set(
    source: Transport,
    set_id: str | None,
    state_dir: Path,
    hub: Hub,
    clock: Clock,
    *,
    lock_timeout: float,
    maximum_schema_version: int | None = None,
    owner_dsn: str | None = None,
    environment: str = "dev",
    defer_activation: bool = False,
) -> dict[str, Any]:
    """Install a set under the writer lock, check the public hub, activate."""
    from swingset.build.files import durable_write
    from swingset.state.control_lock import control_lock
    from swingset.state.rwlock import exclusive, is_lock_file

    from .checkpoint import CheckpointError, activate_restored_state, verify_restored_public
    from .destination import owner

    state_dir.mkdir(parents=True, exist_ok=True)
    if any(
        path.name != "database.json" and not is_lock_file(path.name) for path in state_dir.iterdir()
    ):
        raise CheckpointError("restore requires a fresh database and volume")
    with owner(state_dir, owner_dsn) as connection:
        locks = exclusive(state_dir, connection=connection, timeout=lock_timeout, clock=clock)
        try:
            with control_lock(state_dir, connection=connection, timeout=lock_timeout):
                durable_write(state_dir / "RESTORE_PENDING", b"verification pending\n")
                from psycopg.conninfo import make_conninfo

                summary = install(
                    source,
                    state_dir,
                    set_id=set_id,
                    maximum_schema_version=maximum_schema_version,
                    owner_dsn=make_conninfo(connection.info.dsn, password=connection.info.password),
                    environment=environment,
                )
            if not defer_activation:
                verify_restored_public(state_dir, hub, connection=connection)
                activate_restored_state(state_dir, connection=connection, locks=locks, clock=clock)
            else:
                summary["activation_pending"] = True
                summary["public_reconciled"] = False
        finally:
            locks.release()
    return summary
