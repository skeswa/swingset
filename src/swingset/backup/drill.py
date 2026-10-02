"""The weekly restore drill (architecture phase 4, WP7; D-0232).

A backup nobody has restored is a hope. Once every `[storage]
drill_every_days`, the maintain loop installs the newest backup set into
`<drill_dir>/swingset-drill-<set>` with `restore_set.install`, from the
transport the set was written to: the local sets, a directory, or the
archive in upload mode. It never activates anything and never asks the
public hub. The checks:

- every database part, the index and every segment match their digests,
  and every indexed file matches its own (`install`);
- the restored database's schema, `PRAGMA quick_check` and
  `foreign_key_check` (`install`, integrity "quick");
- one release from the restored state: a partitioned pending or baseline
  candidate's manifest read from the restored objects with every artifact
  intact (`manifest.verify`), else a closure-mode baseline's private proof
  streamed through sha256 against its public commitment (not decoded: a
  proof is hundreds of MB).

A pass writes `backup_sets.drilled_at` and `drill_json`, removes the
scratch, then prunes the local sets (`segments.prune_sets` keeps the
newest and the newest drilled). A failure writes `drill_json` with
`passed` false, appends `[restore-drill-failed]` to `ALERTS.log`, and keeps
the scratch until the next drill or the scratch timer removes it. Without
room for the database, the files and 20 GB (the backup's own headroom), the
drill does not start and keeps the guard `loop:maintain drill_no_room`,
which expires after 24 hours so the watchdog's `[guard-expired]` names a
lack of room that lasts. It holds the backup's lock (`loop-backup.lock`) the
whole time; while a backup runs it does not start and keeps
`loop:maintain drill_backup_running` the same way. Before the install it
records `passed: null`, so a drill killed part way is not silent.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from swingset.state import disk, guards, rwlock
from swingset.state.db import Database

from .restore_set import SetError, install
from .segments import LOCAL_TRANSPORT, prune_sets
from .transport import (
    MANIFEST,
    SETS,
    HuggingFaceSegments,
    LocalDirectoryTransport,
    Transport,
    TransportError,
)

#: The drill's scratch directory under `drill_dir`; the scratch timer
#: (`swingset-scratch-clean`) removes `/var/tmp/swingset-*` left idle.
PREFIX = "swingset-drill-"
#: Room the drill leaves beyond the database and the files: the backup's own
#: headroom (nix/module.nix), so a passing drill never leaves the loops less.
HEADROOM_BYTES = 20_000_000_000
SUBJECT = "loop:maintain"
NO_ROOM = "drill_no_room"
#: The guard a drill leaves when the backup holds its lock; it expires after
#: 24 hours like `NO_ROOM`.
BACKUP_RUNNING = "drill_backup_running"
BACKUP_LOOP = "backup"
NO_ROOM_CLOCK = timedelta(hours=24)
#: The ALERTS.log key; the watchdog repeats it from `drill_json`.
ALERT_KEY = "restore-drill-failed"
#: A failed drill's scratch, less the restored database, stays this long for
#: inspection: under a day, so the next daily tick removes it before it drills.
FAILED_KEPT = timedelta(hours=20)
#: A drill is due this much before a whole interval, so a daily tick that
#: starts a few minutes earlier than last week's still drills.
SLACK = timedelta(hours=12)
#: Root's cached reading of the btrfs quota (`swingset.state.disk`).
QUOTA_CACHE = disk.QUOTA_FILE


class DrillError(RuntimeError):
    """The drill could not check the set."""


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_sets'"
        ).fetchone()
        is not None
    )


def due(conn: sqlite3.Connection, *, now: datetime, every_days: int) -> bool:
    """Whether a set exists and none passed a drill within `every_days` (less `SLACK`)."""
    if not available(conn):
        return False
    count, passed = conn.execute("SELECT count(*),max(drilled_at) FROM backup_sets").fetchone()
    if not count:
        return False
    if passed is None:
        return True
    return datetime.fromisoformat(str(passed)) < now - (timedelta(days=every_days) - SLACK)


def overdue(conn: sqlite3.Connection, *, now: datetime, every_days: int) -> bool:
    """Whether no set passed a drill for over `every_days` plus a day (since
    the first set, when none ever passed): maintain then drills before verify."""
    if not available(conn):
        return False
    first, passed = conn.execute(
        "SELECT min(created_at),max(drilled_at) FROM backup_sets"
    ).fetchone()
    since = passed if passed is not None else first
    if since is None:
        return False
    return datetime.fromisoformat(str(since)) < now - timedelta(days=every_days + 1)


def transport_for(state_dir: Path, name: str) -> Transport:
    """The transport a set was recorded with (`backup_sets.transport`)."""
    if name == LOCAL_TRANSPORT:
        return LocalDirectoryTransport(state_dir / "checkpoints", name=LOCAL_TRANSPORT)
    if name.startswith("dir:"):
        return LocalDirectoryTransport(Path(name.removeprefix("dir:")), name=name)
    if name.startswith("hf:"):
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise DrillError(f"HF_TOKEN is required to drill a set kept in {name}")
        return HuggingFaceSegments(name.removeprefix("hf:"), token=token)
    raise DrillError(f"unknown backup transport {name!r}")


def free_bytes(path: Path, *, quota: Path | None = None) -> int:
    """Free space under `path`, and under the machine's disk quota when known
    (`QUOTA_CACHE`, read when called)."""
    return disk.free_bytes(path, quota_file=quota if quota is not None else QUOTA_CACHE)


def run(
    database: Database,
    *,
    now: datetime,
    drill_dir: Path,
    transport: Transport | None = None,
    free: Callable[[Path], int] = free_bytes,
) -> dict[str, Any]:
    """Drill the newest backup set; returns what the maintain summary records.

    The caller decided it is due. Returns `{"skipped": ...}` when there is no
    set, no room, or a backup is running, else the drill record with
    `passed`. It holds the backup's own lock (`loop-backup.lock`) throughout,
    so no set is written or pruned beside it and the two never share the
    disk. Before it installs anything it records `passed: null`, so a drill
    killed part way reads as failed once it is old (`[restore-drill-failed]`).
    """
    conn = database.connection
    row = conn.execute(
        "SELECT set_id,transport FROM backup_sets ORDER BY set_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return {"skipped": "no_set"}
    set_id, recorded = str(row[0]), str(row[1])
    held = rwlock.try_loop(database.state_dir, BACKUP_LOOP)
    if held is None:
        with database.transaction() as write:
            guards.clocked(
                write, SUBJECT, BACKUP_RUNNING, loop="maintain", now=now, clock=NO_ROOM_CLOCK
            )
        return {"skipped": "backup_running", "set_id": set_id}
    try:
        with database.transaction() as write:
            guards.delete(write, SUBJECT, BACKUP_RUNNING)
        return _drill(
            database, set_id, recorded, now=now, drill_dir=drill_dir, transport=transport, free=free
        )
    finally:
        held.release()


def _drill(
    database: Database,
    set_id: str,
    recorded: str,
    *,
    now: datetime,
    drill_dir: Path,
    transport: Transport | None,
    free: Callable[[Path], int],
) -> dict[str, Any]:
    drill_dir.mkdir(parents=True, exist_ok=True)
    scratch = drill_dir / f"{PREFIX}{set_id}"
    _clear(drill_dir, scratch, now)
    if scratch.exists():
        # An operator kept the last one (`KEEP`); drill beside it.
        scratch = drill_dir / f"{PREFIX}{set_id}-{int(now.timestamp())}"
    started = time.monotonic()
    record: dict[str, Any] = {
        "set_id": set_id,
        "transport": recorded,
        "started_at": now.astimezone(UTC).isoformat(),
        "scratch": str(scratch),
    }
    try:
        source = transport if transport is not None else transport_for(database.state_dir, recorded)
        scratch.mkdir()
        manifest = json.loads(source.fetch(f"{SETS}/{set_id}/{MANIFEST}", scratch).read_bytes())
        need = _need(manifest, local=isinstance(source, LocalDirectoryTransport))
        room = free(drill_dir)
        record.update(need_bytes=need, free_bytes=room)
        if room < need:
            shutil.rmtree(scratch, ignore_errors=True)
            with database.transaction() as write:
                _no_room(write, now)
            return {"skipped": "no_room", "set_id": set_id, "need_bytes": need, "free_bytes": room}
        with database.transaction() as write:
            guards.delete(write, SUBJECT, NO_ROOM)
        _started(database, set_id, record, now=now)
        restored = scratch / "state"
        record["install"] = install(source, restored, set_id=set_id, integrity="quick")
        record["release"] = _release(restored, record["install"])
    except (
        DrillError,
        SetError,
        TransportError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        tarfile.TarError,
        sqlite3.DatabaseError,
    ) as error:
        if _pruned(transport, database.state_dir, recorded, set_id):
            # A set removed by hand meanwhile; the next tick drills the newer one.
            shutil.rmtree(scratch, ignore_errors=True)
            with database.transaction() as write:
                write.execute(
                    "UPDATE backup_sets SET drill_json=NULL WHERE set_id=? "
                    "AND json_valid(drill_json) AND json_extract(drill_json,'$.passed') IS NULL",
                    (set_id,),
                )
            return {"skipped": "set_pruned", "set_id": set_id}
        record.update(
            passed=False,
            error=f"{type(error).__name__}: {error}"[:500],
            seconds=round(time.monotonic() - started, 3),
        )
        _write(database, set_id, record, passed=False, now=now)
        if scratch.exists():
            _shrink(scratch)
            os.utime(scratch)
        record["alerted"] = _alert(database.state_dir, record, now=now)
        return record
    record.update(passed=True, seconds=round(time.monotonic() - started, 3))
    _write(database, set_id, record, passed=True, now=now)
    shutil.rmtree(scratch, ignore_errors=True)
    record["pruned"] = prune_sets(database, now=now)
    return record


def _started(database: Database, set_id: str, record: dict[str, Any], *, now: datetime) -> None:
    """`drill_json` with `passed` null before the install: a drill killed part
    way leaves this, and the watchdog reads it as failed once it is old."""
    body = json.dumps(
        {**record, "passed": None, "at": now.astimezone(UTC).isoformat()},
        sort_keys=True,
        default=str,
    )
    with database.transaction() as write:
        write.execute("UPDATE backup_sets SET drill_json=? WHERE set_id=?", (body, set_id))


def _pruned(transport: Transport | None, state_dir: Path, recorded: str, set_id: str) -> bool:
    """Whether a local set was removed by a newer set's prune during the drill."""
    if transport is None and recorded != LOCAL_TRANSPORT:
        return False
    source = transport or transport_for(state_dir, recorded)
    if not isinstance(source, LocalDirectoryTransport):
        return False
    newer = [name for name in source.sets() if name > set_id]
    return bool(newer) and set_id not in source.sets()


def _need(manifest: dict[str, Any], *, local: bool) -> int:
    """The database, the files, and `HEADROOM_BYTES`; a remote set also
    downloads its parts and segments into the scratch first."""
    database = manifest["database"]
    files = int(manifest["index"]["item_bytes"])
    need = int(database["raw_bytes"]) + files + HEADROOM_BYTES
    if not local:
        need += sum(int(part["bytes"]) for part in database["parts"]) + files
    return need


def _clear(drill_dir: Path, scratch: Path, now: datetime) -> None:
    """Remove this set's earlier scratch, and other drills' failed scratch
    older than `FAILED_KEPT`; a directory holding `KEEP` stays, as the
    scratch timer leaves it."""
    cutoff = (now - FAILED_KEPT).timestamp()
    for path in drill_dir.glob(f"{PREFIX}*"):
        try:
            old = path.stat().st_mtime < cutoff
        except OSError:
            continue
        if (path / "KEEP").exists():
            continue
        if path == scratch or old:
            shutil.rmtree(path, ignore_errors=True)


def _shrink(scratch: Path) -> None:
    """A failed drill keeps its small files, not the restored database: that
    is the size of the database's bytes in use, and left on the same disk it
    would refuse the next backup set's room check. `KEEP` keeps it all."""
    if (scratch / "KEEP").exists():
        return
    restored = scratch / "state"
    for name in ("state.sqlite", "state.sqlite-wal", "state.sqlite-shm"):
        with contextlib.suppress(FileNotFoundError):
            (restored / name).unlink()
    if restored.is_dir():
        for path in restored.glob(".install-*"):
            shutil.rmtree(path, ignore_errors=True)


def _no_room(conn: sqlite3.Connection, now: datetime) -> None:
    guards.clocked(conn, SUBJECT, NO_ROOM, loop="maintain", now=now, clock=NO_ROOM_CLOCK)


def _release(state: Path, installed: dict[str, Any]) -> dict[str, Any]:
    """Hydrate one release from the restored state; raises when it cannot."""
    from swingset.build import manifest

    policies: list[tuple[str, dict[str, Any]]] = []
    for field in ("pending_candidate", "baseline_candidate"):
        name = installed.get(field)
        if name is None:
            continue
        meta = state / "candidates" / str(name) / "_meta" / "manifest.json"
        policy = json.loads(meta.read_bytes()).get("release_policy") or {}
        if not isinstance(policy, dict):
            raise DrillError(f"candidate {name} has an unreadable release policy")
        policies.append((str(name), policy))
    for name, policy in policies:
        if policy.get("mode") == "partitioned":
            digest = str(policy["manifest_digest"])
            try:
                value = manifest.verify(state, digest)
            except manifest.ManifestError as error:
                raise DrillError(f"candidate {name}: {error}") from error
            return {
                "candidate": name,
                "mode": "partitioned",
                "manifest_digest": digest,
                "artifacts": len(value["built"]),
            }
    # A closure proof is hundreds of MB of JSON (about 235 MB), too much to
    # decode under maintain's 2 GB cap: the drill streams its bytes through
    # sha256 against the public commitment's `private_digest` and checks the
    # public manifest's own digest, without decoding the proof.
    for name, policy in policies:
        if name != installed.get("baseline_candidate") or policy.get("mode") != "closure":
            continue
        public = policy.get("closure")
        if not isinstance(public, dict):
            raise DrillError(f"candidate {name} names no closure")
        private = _proof_bytes(state / "state.sqlite", public)
        return {
            "candidate": name,
            "mode": "closure",
            "private_digest": str(public.get("private_digest")),
            "proof_bytes": private,
        }
    return {"candidate": None, "modes": sorted(str(p.get("mode")) for _, p in policies)}


def _proof_bytes(database: Path, public: dict[str, Any]) -> int:
    """Check a closure proof's stored bytes against its public commitment."""
    from swingset.build.closure_manifest import digest

    if public.get("format") != "release-closure-public-v1":
        raise DrillError("the closure is not a public commitment")
    if digest({key: item for key, item in public.items() if key != "digest"}) != public.get(
        "digest"
    ):
        raise DrillError("public_closure_digest_mismatch")
    private = str(public.get("private_digest") or "")
    uri = f"{database.resolve().as_uri()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT rowid FROM derivation_dependency_sets WHERE dependency_set_id=?", (private,)
        ).fetchone()
        if row is None:
            raise DrillError("private_closure_proof_unavailable")
        hasher = hashlib.sha256()
        size = 0
        with conn.blobopen(
            "derivation_dependency_sets", "manifest_json", row[0], readonly=True
        ) as blob:
            while block := blob.read(1 << 20):
                hasher.update(block)
                size += len(block)
    finally:
        conn.close()
    if hasher.hexdigest() != private:
        raise DrillError("private_closure_proof_corrupt")
    return size


def _write(
    database: Database, set_id: str, record: dict[str, Any], *, passed: bool, now: datetime
) -> None:
    stamp = now.astimezone(UTC).isoformat()
    body = json.dumps({**record, "at": stamp}, sort_keys=True, default=str)
    with database.transaction() as write:
        write.execute(
            "UPDATE backup_sets SET drilled_at=?,drill_json=? WHERE set_id=?",
            (stamp if passed else None, body, set_id),
        )


def _alert(state_dir: Path, record: dict[str, Any], *, now: datetime) -> bool:
    """Append the failure to `ALERTS.log` with the watchdog's stamp."""
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (
        f"[{ALERT_KEY}] set {record['set_id']} ({record['transport']}) failed its restore"
        f" drill: {record.get('error', '')[:240]}; scratch kept at {record['scratch']}"
        " for 20 hours, without the restored database (docs/guides/backup-and-restore.md)"
    )
    try:
        with open(state_dir / "ALERTS.log", "a") as handle:
            handle.write(f"{stamp} {line}\n")
    except OSError:
        return False
    return True


__all__ = ["ALERT_KEY", "DrillError", "NO_ROOM", "available", "due", "free_bytes", "run"]
