"""Mark and sweep every stored class that has a collector (architecture phase 4, WP5).

Each class has a root rule. Whatever no root names and is older than the
cutoff is collected ([D-0231](../../../journal/decisions/0231-collect-every-unnamed-dependency-set-and-unrooted-object.md)).
The cutoff is the start of the current UTC day minus one day, so everything
collected is at least a day old and a plan and its apply on the same day agree.

| Class                | Roots                                                                                   |
| -------------------- | --------------------------------------------------------------------------------------- |
| `derivation_output`  | every label's `output_digest`                                                           |
| `parse_body`         | `source_generation_packs`                                                               |
| `label_body`         | `label_body_packs`                                                                      |
| `release_manifest`   | retained candidates, current build pointers, the last 3 published releases              |
| `partition_artifact` | current `release_partitions` rows and every artifact a kept manifest names              |
| `dependency_set`     | sets a generation, an equivalence, a scope's proof, a recipe, a candidate or a set names |

Orphans: `objects/.writing-*` files, files under `objects/` with no `objects`
row, and the references of partial generations that no pointer, equivalence or
building row names (D-0217). Bodies, extracts and input bundles have no
collector here: an undeclared file stays unknown (D-0143).

**Memory (I10).** Roots and victims live in temporary tables under
`<state>/tmp`, never in a Python set. Object roots are SQL anti-joins. A
dependency set's roots are its direct names, then every set a rooted set's
JSON array names by `"fingerprint"`, followed to a fixpoint one set at a time
with bounded blob reads. A set written in the last day is a root too, and so
is what it names: a staged run's step A captures sets that only its step B
names, and B writes every one of them again in the transaction that names
them, so a set collected between A and B comes back.

**Sweep.** The mark runs in one read transaction. Deletes go in transactions of
`BATCH` victims, each rechecking its rule in that transaction; an object's
file is unlinked after its row's delete commits, so a crash leaves only an
unindexed file. A dependency-set delete first catches up with what committed
since the mark: new generations, equivalences, sets and candidate manifests
add roots, and the set holding the highest rowid at the mark is never
deleted, so every later insert is seen.

**Entry points.** `plan` writes nothing to the database: per class counts,
bytes and a digest over the sorted victim list. `apply` (the first production
run) recomputes the plan under the writer and control locks, stops if the
digest moved, and sweeps what it names, with a `storage_applies` note and a
receipt. `sweep` is maintain's version, bounded by `stop`. On schemas 33 to 35
only the dependency-set class runs (the F39 backport): every set counts as
old, the no-delete trigger is lifted inside each transaction as D-0200 does,
and `operator-hold` is required.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, durable_write

from . import disk, objects
from .control_lock import control_lock
from .db import Database
from .publication_fence import PUBLICATION_KINDS, kinds_sql
from .retention_apply import receipt_directory, sqlite_temp_directory

PLAN_FORMAT = "collect-plan-v1"
RECEIPT_FORMAT = "collect-receipt-v1"
OBJECT_CLASSES = ("derivation_output", "parse_body", "label_body")
CLASSES = (*OBJECT_CLASSES, "release_manifest", "partition_artifact", "dependency_set")
ORPHANS = ("writing", "unindexed", "partial_refs")
#: Victims per transaction.
BATCH = 500
#: Newest published releases whose manifests stay.
PUBLISHED_KEPT = 3
#: Newest built candidates whose manifests stay.
NEWEST_CANDIDATES = 3
#: Bytes read from a dependency set per step of its scan.
_CHUNK = 1 << 20
#: A name a set's JSON gives another set: `{"fingerprint":"<id>",...}`.
_FINGERPRINT = re.compile(rb'"fingerprint":"([^"\\]{1,256})"')
#: A digest a build recipe gives: its release closure's, public or private.
_DIGESTS = re.compile(rb'"(?:private_)?digest":"([^"\\]{1,256})"')
#: Bytes kept across a chunk boundary, longer than any match.
_OVERLAP = 300
_HOLD = "operator-hold"
_NO_DELETE = "derivation_dependency_no_delete"
_OBJECT_ROOTS = {
    "derivation_output": "SELECT 1 FROM derivation_generations g WHERE g.output_digest=o.digest",
    "parse_body": "SELECT 1 FROM source_generation_packs p WHERE p.pack_digest=o.digest",
    "label_body": "SELECT 1 FROM label_body_packs p WHERE p.pack_digest=o.digest",
}
_OBJECT_TABLES = {
    "derivation_output": "derivation_generations",
    "parse_body": "source_generation_packs",
    "label_body": "label_body_packs",
}
_TEMP = (
    "collect_victims",
    "collect_set_roots",
    "collect_set_candidates",
    "collect_manifest_roots",
    "collect_artifact_roots",
    "collect_batch",
)


class CollectError(RuntimeError):
    """A collection refused, and nothing was removed by this call."""


def cutoff(now: datetime) -> datetime:
    """Start of the current UTC day minus one day: anything collected is older."""
    if now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=1)


# ---------------------------------------------------------------------------
# Schema and file helpers
# ---------------------------------------------------------------------------


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _storage_gate(conn: sqlite3.Connection) -> bool:
    """Schema 36: deletes pass under `storage_eviction_authority`."""
    return _has_table(conn, "storage_eviction_authority")


def publication_active(conn: sqlite3.Connection) -> bool:
    if not _has_table(conn, "execution_admissions"):
        return False
    return (
        conn.execute(
            "SELECT 1 FROM execution_admissions WHERE state='active' "
            f"AND action_kind IN {kinds_sql(PUBLICATION_KINDS)} LIMIT 1"
        ).fetchone()
        is not None
    )


def _older(path: Path, before: datetime) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) < before
    except OSError:
        return False


def _size(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def _remove(path: Path) -> int:
    size = _size(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    return size


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _drop_temporary(conn: sqlite3.Connection) -> None:
    for name in _TEMP:
        conn.execute(f"DROP TABLE IF EXISTS temp.{name}")


def _victim(conn: sqlite3.Connection, cls: str, key: str, size: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO temp.collect_victims(class,key,size) VALUES (?,?,?)",
        (cls, key, size),
    )


# ---------------------------------------------------------------------------
# Manifest and artifact roots (moved here from build/artifact_store in WP5)
# ---------------------------------------------------------------------------


def _candidate_policy(path: Path) -> dict[str, Any] | None:
    try:
        manifest = json.loads((path / "_meta" / "manifest.json").read_bytes())
    except (OSError, ValueError):
        return None
    policy = manifest.get("release_policy") if isinstance(manifest, dict) else None
    return policy if isinstance(policy, dict) else None


def _partitioned_manifest(path: Path) -> str | None:
    policy = _candidate_policy(path)
    if policy is not None and policy.get("mode") == "partitioned":
        digest = policy.get("manifest_digest")
        if _is_digest(digest):
            return str(digest)
    return None


def published_manifests(state_dir: Path, *, keep: int = PUBLISHED_KEPT) -> list[str]:
    """Manifest digests of the newest `keep` published releases, by `verified_at`."""
    directory = state_dir / "candidates"
    found: list[tuple[str, str, str]] = []
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            try:
                receipt = json.loads((path / "PUBLISHED").read_bytes())
            except (OSError, ValueError):
                continue
            digest = receipt.get("manifest_digest") if isinstance(receipt, dict) else None
            if _is_digest(digest):
                found.append((str(receipt.get("verified_at") or ""), path.name, str(digest)))
    found.sort(reverse=True)
    return [digest for _, _, digest in found[:keep]]


def retained_manifests(state_dir: Path) -> set[str]:
    """Manifest digests the baseline, pending candidates, the newest three built
    and the last three published releases name. A handful of digests."""
    from .retention import referenced_candidates

    candidates = set(referenced_candidates(state_dir))
    directory = state_dir / "candidates"
    if directory.is_dir():
        built = sorted(
            (path for path in directory.iterdir() if (path / "BUILT").is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        candidates.update(path.resolve() for path in built[:NEWEST_CANDIDATES])
    result = {digest for path in candidates if (digest := _partitioned_manifest(path))}
    result.update(published_manifests(state_dir))
    return result


def build_manifests(conn: sqlite3.Connection) -> set[str]:
    """Manifest digests the current build generations' candidates name.

    `derivations._artifact_valid` reads a partitioned candidate's manifest, so
    it stays while a build pointer stands for it. Unverified for a resident
    generation, as before: the collector keeps what a current build row names
    even if its label is off.
    """
    from . import output_packs

    result: set[str] = set()
    for (generation_id,) in conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes WHERE stage='build' "
        "AND materialized_generation_id IS NOT NULL ORDER BY materialized_generation_id"
    ).fetchall():
        try:
            payloads = [
                payload
                for _, payload in output_packs.table_rows(conn, str(generation_id), "artifact")
            ]
        except output_packs.GenerationUnavailable:
            continue
        for payload in payloads:
            try:
                path = Path(json.loads(payload)["path"])
            except (ValueError, KeyError, TypeError):
                continue
            digest = _partitioned_manifest(path)
            if digest is not None:
                result.add(digest)
    return result


def _manifest_roots(conn: sqlite3.Connection, state_dir: Path) -> None:
    conn.execute("DELETE FROM temp.collect_manifest_roots")
    conn.executemany(
        "INSERT OR IGNORE INTO temp.collect_manifest_roots(digest) VALUES (?)",
        ((digest,) for digest in sorted(retained_manifests(state_dir) | build_manifests(conn))),
    )


def _artifact_roots(conn: sqlite3.Connection, state_dir: Path) -> bool:
    """Fill the artifact roots; False when a retained manifest cannot be read."""
    from .retention import manifest_artifacts

    conn.execute("DELETE FROM temp.collect_artifact_roots")
    conn.execute(
        "INSERT OR IGNORE INTO temp.collect_artifact_roots(digest) SELECT artifact_digest "
        "FROM release_partitions WHERE artifact_digest IS NOT NULL"
    )
    for manifest in sorted(retained_manifests(state_dir)):
        try:
            pairs = manifest_artifacts(state_dir, manifest)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        conn.executemany(
            "INSERT OR IGNORE INTO temp.collect_artifact_roots(digest) VALUES (?)",
            ((artifact,) for _, artifact in pairs),
        )
    return True


# ---------------------------------------------------------------------------
# Dependency-set roots
# ---------------------------------------------------------------------------


@dataclass
class _Marks:
    """What the mark's snapshot saw, so a later delete can catch up."""

    generations: int = 0
    equivalences: int = 0
    sets: int = 0
    #: The highest set rowid at the mark; that row is never deleted.
    first_sets: int = 0
    #: Candidate manifests read: path -> (mtime_ns, size).
    candidates: dict[str, tuple[int, int]] = field(default_factory=dict)


def _root_ids(conn: sqlite3.Connection, ids: Iterable[str]) -> None:
    """Add existing sets to the roots, queued for their own names."""
    conn.executemany(
        "INSERT OR IGNORE INTO temp.collect_set_roots(id,expanded) SELECT ?,0 "
        "WHERE EXISTS(SELECT 1 FROM derivation_dependency_sets WHERE dependency_set_id=?)",
        ((str(value), str(value)) for value in ids if value),
    )


def _candidate_manifests(state_dir: Path) -> Iterator[Path]:
    directory = state_dir / "candidates"
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            yield path / "_meta" / "manifest.json"
    baseline = state_dir / "baseline"
    if baseline.exists():
        yield baseline.resolve() / "_meta" / "manifest.json"


def _candidate_names(state_dir: Path, seen: dict[str, tuple[int, int]]) -> Iterator[str]:
    """Closure digests of every candidate directory, rejected or not, and the
    baseline (D-0200), reading only manifests new or changed since `seen`.

    An unreadable manifest might name any set: the class refuses.
    """
    for manifest in _candidate_manifests(state_dir):
        try:
            stat = manifest.stat()
        except OSError:
            continue
        key = str(manifest)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if seen.get(key) == stamp:
            continue
        try:
            policy = json.loads(manifest.read_bytes()).get("release_policy") or {}
            closure = policy.get("closure") or {}
        except (OSError, ValueError, AttributeError) as error:
            raise CollectError(f"unreadable candidate manifest {manifest}") from error
        seen[key] = stamp
        if isinstance(closure, dict):
            for name in ("private_digest", "digest"):
                if closure.get(name):
                    yield str(closure[name])


def _scan(chunks: Iterable[bytes], pattern: re.Pattern[bytes]) -> Iterator[str]:
    """Matches of `pattern` across a stream of chunks, in bounded memory."""
    tail = b""
    for chunk in chunks:
        data = tail + chunk
        for match in pattern.finditer(data):
            yield match.group(1).decode("utf-8", "replace")
        tail = data[-_OVERLAP:]


def _blob(conn: sqlite3.Connection, table: str, column: str, rowid: int) -> Iterator[bytes]:
    with conn.blobopen(table, column, rowid, readonly=True) as blob:
        while chunk := blob.read(_CHUNK):
            yield chunk


def _object(state_dir: Path, cls: str, digest: str) -> Iterator[bytes]:
    try:
        with objects.Store(state_dir).open(cls, digest) as handle:
            while chunk := handle.read(_CHUNK):
                yield chunk
    except (OSError, EOFError, objects.ObjectUnavailable) as error:
        raise CollectError(f"unreadable {cls} object {digest}: {error}") from error


def _recipe_names(
    conn: sqlite3.Connection, state_dir: Path, where: str, args: tuple[Any, ...] = ()
) -> list[str]:
    """Sets the matching generations' recipes name: a project unit's continuity
    support and a build's release closure, public and private.

    A build recipe can be hundreds of megabytes, so it is scanned in chunks for
    every `"digest"` and `"private_digest"` value, from the row or, packed, from
    its `label_body` object; only values that are set ids become roots, and a
    match that is not the closure keeps more, never less.
    """
    names: list[str] = []
    for continuity in conn.execute(
        "SELECT json_extract(recipe_json,'$.continuity.dependency_set_id') "
        "FROM derivation_generations WHERE stage<>'build' "
        "AND instr(recipe_json,'\"dependency_set_id\"')>0 AND json_valid(recipe_json)"
        f"{where}",
        args,
    ):
        if continuity[0]:
            names.append(str(continuity[0]))
    # A stub always has its pack row (migration 36's trigger), so the row is
    # never compared with the stub: that would load the whole recipe.
    pack = (
        "(SELECT p.pack_digest FROM label_body_packs p WHERE p.generation_id=g.generation_id)"
        if _has_table(conn, "label_body_packs")
        else "NULL"
    )
    for rowid, pack_digest in conn.execute(
        f"SELECT g.rowid,{pack} FROM derivation_generations g WHERE g.stage='build'"
        f"{where.replace('rowid', 'g.rowid')}",
        args,
    ).fetchall():
        chunks: Iterable[bytes]
        if pack_digest is not None:
            chunks = _object(state_dir, "label_body", str(pack_digest))
        else:
            chunks = _blob(conn, "derivation_generations", "recipe_json", int(rowid))
        names.extend(_scan(chunks, _DIGESTS))
    return names


def _equivalence_names(conn: sqlite3.Connection, after: int) -> list[str]:
    if not _has_table(conn, "derivation_equivalences"):
        return []
    names = [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT dependency_set_id FROM derivation_equivalences WHERE rowid>?",
            (after,),
        )
    ]
    if _has_table(conn, "derivation_recipes"):
        names.extend(
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT json_extract(r.recipe_json,'$.continuity.dependency_set_id') "
                "FROM derivation_equivalences e JOIN derivation_recipes r "
                "ON r.recipe_sha256=e.recipe_sha256 WHERE e.rowid>? "
                "AND instr(r.recipe_json,'\"dependency_set_id\"')>0 AND json_valid(r.recipe_json)",
                (after,),
            )
            if row[0]
        )
    return names


def _named_in(conn: sqlite3.Connection, rowid: int) -> Iterator[str]:
    """Names a JSON-array set gives other sets, read in bounded chunks.

    A proof (a JSON object) names none. A match inside a member's data only
    keeps more, never less.
    """
    with conn.blobopen("derivation_dependency_sets", "manifest_json", rowid, readonly=True) as blob:
        if blob.read(1) != b"[":
            return
    yield from _scan(
        _blob(conn, "derivation_dependency_sets", "manifest_json", rowid), _FINGERPRINT
    )


def _expand(conn: sqlite3.Connection) -> int:
    """Follow every queued root's names until no root is left to read."""
    read = 0
    while True:
        queued = conn.execute(
            "SELECT r.id,d.rowid FROM temp.collect_set_roots r "
            "LEFT JOIN derivation_dependency_sets d ON d.dependency_set_id=r.id "
            "WHERE r.expanded=0 LIMIT ?",
            (BATCH,),
        ).fetchall()
        if not queued:
            return read
        for set_id, rowid in queued:
            if rowid is not None:
                read += 1
                batch: list[str] = []
                for name in _named_in(conn, int(rowid)):
                    batch.append(name)
                    if len(batch) >= 1000:
                        _root_ids(conn, batch)
                        batch.clear()
                _root_ids(conn, batch)
            conn.execute("UPDATE temp.collect_set_roots SET expanded=1 WHERE id=?", (set_id,))


def _max_rowid(conn: sqlite3.Connection, table: str) -> int:
    if not _has_table(conn, table):
        return 0
    return int(conn.execute(f"SELECT coalesce(max(rowid),0) FROM {table}").fetchone()[0])


def _mark_sets(
    conn: sqlite3.Connection, state_dir: Path, before: str, marks: _Marks
) -> dict[str, Any]:
    """Fill the set roots and victims; returns the counts by rule."""
    marks.generations = _max_rowid(conn, "derivation_generations")
    marks.equivalences = _max_rowid(conn, "derivation_equivalences")
    marks.sets = marks.first_sets = _max_rowid(conn, "derivation_dependency_sets")
    conn.execute(
        "INSERT OR IGNORE INTO temp.collect_set_roots(id,expanded) "
        "SELECT DISTINCT dependency_set_id,0 FROM derivation_generations"
    )
    _root_ids(conn, _equivalence_names(conn, 0))
    _root_ids(
        conn,
        (
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT materialized_signature FROM derivation_scopes "
                "WHERE materialized_signature IS NOT NULL"
            )
        ),
    )
    _root_ids(conn, _recipe_names(conn, state_dir, ""))
    _root_ids(conn, _candidate_names(state_dir, marks.candidates))
    direct = int(conn.execute("SELECT count(*) FROM temp.collect_set_roots").fetchone()[0])
    expanded = _expand(conn)
    young = (
        "(d.created_at IS NOT NULL AND julianday(d.created_at)>=julianday(?))"
        if _has_column(conn, "derivation_dependency_sets", "created_at")
        else "(0 AND ? IS NULL)"
    )
    # Only unrooted sets are read past their id: created_at follows a value
    # of up to hundreds of megabytes, and reading it walks that value's pages.
    conn.execute(
        "INSERT INTO temp.collect_set_candidates(id,size,young) "
        f"SELECT d.dependency_set_id,octet_length(d.manifest_json),{young} "
        "FROM derivation_dependency_sets d WHERE NOT EXISTS("
        "SELECT 1 FROM temp.collect_set_roots r WHERE r.id=d.dependency_set_id)",
        (before,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO temp.collect_set_roots(id,expanded) "
        "SELECT id,0 FROM temp.collect_set_candidates WHERE young"
    )
    expanded += _expand(conn)
    # The highest set is never deleted (`_catch_up`), so it is no victim either.
    conn.execute(
        "INSERT INTO temp.collect_victims(class,key,size) "
        "SELECT 'dependency_set',c.id,c.size FROM temp.collect_set_candidates c "
        "WHERE NOT EXISTS(SELECT 1 FROM temp.collect_set_roots r WHERE r.id=c.id) "
        "AND c.id IS NOT (SELECT dependency_set_id FROM derivation_dependency_sets WHERE rowid=?)",
        (marks.first_sets,),
    )
    roots, young_count = conn.execute(
        "SELECT (SELECT count(*) FROM temp.collect_set_roots),"
        "(SELECT count(*) FROM temp.collect_set_candidates WHERE young)"
    ).fetchone()
    return {
        "roots": int(roots),
        "named_directly": direct,
        "young": int(young_count),
        "sets_read": expanded,
        "created_at": young.startswith("(d."),
    }


def _catch_up(conn: sqlite3.Connection, state_dir: Path, marks: _Marks) -> None:
    """Add the roots of everything committed since the mark (inside a write)."""
    generations = _max_rowid(conn, "derivation_generations")
    if generations > marks.generations:
        conn.execute(
            "INSERT OR IGNORE INTO temp.collect_set_roots(id,expanded) "
            "SELECT DISTINCT dependency_set_id,0 FROM derivation_generations WHERE rowid>?",
            (marks.generations,),
        )
        _root_ids(conn, _recipe_names(conn, state_dir, " AND rowid>?", (marks.generations,)))
        marks.generations = generations
    equivalences = _max_rowid(conn, "derivation_equivalences")
    if equivalences > marks.equivalences:
        _root_ids(conn, _equivalence_names(conn, marks.equivalences))
        marks.equivalences = equivalences
    sets = _max_rowid(conn, "derivation_dependency_sets")
    if sets > marks.sets:
        conn.execute(
            "INSERT OR IGNORE INTO temp.collect_set_roots(id,expanded) "
            "SELECT dependency_set_id,0 FROM derivation_dependency_sets WHERE rowid>?",
            (marks.sets,),
        )
        marks.sets = sets
    _root_ids(conn, _candidate_names(state_dir, marks.candidates))
    _expand(conn)


# ---------------------------------------------------------------------------
# Mark
# ---------------------------------------------------------------------------


@dataclass
class _Mark:
    content: dict[str, Any]
    marks: _Marks

    @property
    def digest(self) -> str:
        return str(self.content["digest"])


def _mark_objects(conn: sqlite3.Connection, cls: str, before: str) -> str | None:
    if not _has_table(conn, "objects") or not _has_table(conn, _OBJECT_TABLES[cls]):
        return "absent"
    conn.execute(
        "INSERT INTO temp.collect_victims(class,key,size) SELECT o.class,o.digest,o.stored_bytes "
        "FROM objects o WHERE o.class=? AND julianday(o.stored_at)<julianday(?) "
        f"AND NOT EXISTS({_OBJECT_ROOTS[cls]})",
        (cls, before),
    )
    return None


def _manifest_young(
    conn: sqlite3.Connection, state_dir: Path, digest: str, before: datetime
) -> bool:
    """Whether any trace of a manifest is younger than the cutoff."""
    from .retention import legacy_manifest_file

    stamp = before.isoformat()
    young = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM release_manifests WHERE manifest_digest=? "
        "AND NOT julianday(created_at)<julianday(?))",
        (digest, stamp),
    ).fetchone()[0]
    if not young and _has_table(conn, "objects"):
        young = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM objects WHERE class='release_manifest' AND digest=? "
            "AND NOT julianday(stored_at)<julianday(?))",
            (digest, stamp),
        ).fetchone()[0]
    if young:
        return True
    for path in (
        objects.Store(state_dir).path("release_manifest", digest),
        legacy_manifest_file(state_dir, digest),
    ):
        if path.exists() and not _older(path, before):
            return True
    return False


def _manifest_paths(state_dir: Path, digest: str) -> tuple[Path, Path]:
    from .retention import legacy_manifest_file

    return (
        objects.Store(state_dir).path("release_manifest", digest),
        legacy_manifest_file(state_dir, digest),
    )


def _manifest_digests(conn: sqlite3.Connection, state_dir: Path) -> list[str]:
    """Every manifest the state knows of: object rows, release rows, phase 3 files."""
    from .retention import partition_root

    query = "SELECT manifest_digest FROM release_manifests"
    if _has_table(conn, "objects"):
        query += " UNION SELECT digest FROM objects WHERE class='release_manifest'"
    found = {str(row[0]) for row in conn.execute(query)}
    legacy = partition_root(state_dir) / "manifests"
    if legacy.is_dir():
        found.update(path.stem for path in legacy.glob("*.json") if _is_digest(path.stem))
    return sorted(found)


def _mark_manifests(conn: sqlite3.Connection, state_dir: Path, before: datetime) -> str | None:
    if not _has_table(conn, "release_manifests"):
        return "absent"
    _manifest_roots(conn, state_dir)
    for digest in _manifest_digests(conn, state_dir):
        if conn.execute(
            "SELECT 1 FROM temp.collect_manifest_roots WHERE digest=?", (digest,)
        ).fetchone() or _manifest_young(conn, state_dir, digest, before):
            continue
        _victim(
            conn,
            "release_manifest",
            digest,
            sum(_size(p) for p in _manifest_paths(state_dir, digest)),
        )
    return None


def _mark_artifacts(conn: sqlite3.Connection, state_dir: Path, before: datetime) -> str | None:
    from .retention import artifact_directory, partition_root

    if not _has_table(conn, "release_artifacts"):
        return "absent"
    if not _artifact_roots(conn, state_dir):
        return "unreadable"
    for digest, last_named in conn.execute(
        "SELECT a.artifact_digest,a.last_named_at FROM release_artifacts a "
        "WHERE NOT EXISTS(SELECT 1 FROM temp.collect_artifact_roots r "
        "WHERE r.digest=a.artifact_digest) ORDER BY a.artifact_digest"
    ).fetchall():
        if datetime.fromisoformat(str(last_named)) < before:
            _victim(
                conn,
                "partition_artifact",
                str(digest),
                _size(artifact_directory(state_dir, str(digest))),
            )
    root = partition_root(state_dir)
    if not root.is_dir():
        return None
    for prefix in sorted(root.iterdir()):
        if prefix.name.startswith(".tmp-"):
            if _older(prefix, before):
                _victim(conn, "partition_artifact", prefix.name, _size(prefix))
            continue
        if len(prefix.name) != 2 or not prefix.is_dir():
            continue
        for directory in sorted(prefix.iterdir()):
            digest = directory.name
            if not _is_digest(digest) or not _older(directory, before):
                continue
            if conn.execute(
                "SELECT EXISTS(SELECT 1 FROM temp.collect_artifact_roots WHERE digest=?) "
                "OR EXISTS(SELECT 1 FROM release_artifacts WHERE artifact_digest=?)",
                (digest, digest),
            ).fetchone()[0]:
                continue
            _victim(conn, "partition_artifact", digest, _size(directory))
    return None


def _mark_dependency_sets(
    conn: sqlite3.Connection, state_dir: Path, before: str, marks: _Marks
) -> tuple[str | None, dict[str, Any]]:
    if not _has_table(conn, "derivation_dependency_sets"):
        return "absent", {}
    try:
        return None, _mark_sets(conn, state_dir, before, marks)
    except CollectError as error:
        conn.execute("DELETE FROM temp.collect_victims WHERE class='dependency_set'")
        return f"refused: {error}", {}


def _mark_orphans(conn: sqlite3.Connection, state_dir: Path, before: datetime) -> dict[str, str]:
    store = objects.Store(state_dir)
    if store.root.is_dir():
        for path in sorted(store.root.iterdir()):
            if path.name.startswith((objects.WRITING_PREFIX, COLLECTING_PREFIX)) and _older(
                path, before
            ):
                _victim(conn, "writing", path.name, _size(path))
    absent: dict[str, str] = {}
    if _has_table(conn, "objects"):
        for cls in sorted(objects.STORED):
            directory = store.root / cls
            if not directory.is_dir():
                continue
            for prefix in sorted(directory.iterdir()):
                if len(prefix.name) != 2 or not prefix.is_dir():
                    continue
                for path in sorted(prefix.iterdir()):
                    if not _is_digest(path.name) or not path.is_file():
                        continue
                    if conn.execute(
                        "SELECT 1 FROM objects WHERE class=? AND digest=?", (cls, path.name)
                    ).fetchone():
                        continue
                    if _older(path, before):
                        _victim(conn, "unindexed", f"{cls}/{path.name}", _size(path))
    else:
        absent["unindexed"] = "absent"
    if _storage_gate(conn) and _has_table(conn, "derivation_row_refs"):
        conn.execute(
            "INSERT INTO temp.collect_victims(class,key,size) "
            "SELECT 'partial_refs',g.generation_id,"
            "(SELECT count(*) FROM derivation_row_refs r WHERE r.generation_id=g.generation_id) "
            f"FROM derivation_generations g WHERE {_PARTIAL}",
            (before.isoformat(),),
        )
    else:
        absent["partial_refs"] = "absent"
    return absent


#: A partial generation (its last reference is missing) that is old, not
#: evicted, and that no pointer, equivalence or building row names (D-0217).
_PARTIAL = (
    "g.row_count>0 AND julianday(g.created_at)<julianday(?) "
    "AND EXISTS(SELECT 1 FROM derivation_row_refs r WHERE r.generation_id=g.generation_id) "
    "AND NOT EXISTS(SELECT 1 FROM derivation_row_refs r WHERE r.generation_id=g.generation_id "
    "AND r.ordinal=g.row_count-1) "
    "AND NOT EXISTS(SELECT 1 FROM derivation_evicted e WHERE e.generation_id=g.generation_id) "
    "AND NOT EXISTS(SELECT 1 FROM derivation_scopes s "
    "WHERE s.materialized_generation_id=g.generation_id) "
    "AND NOT EXISTS(SELECT 1 FROM derivation_equivalences q WHERE q.generation_id=g.generation_id) "
    "AND NOT EXISTS(SELECT 1 FROM derivation_building b WHERE b.generation_id=g.generation_id)"
)


def _create_temporary(conn: sqlite3.Connection) -> None:
    _drop_temporary(conn)
    conn.execute(
        "CREATE TEMP TABLE collect_victims(class TEXT NOT NULL, key TEXT NOT NULL, "
        "size INTEGER NOT NULL, PRIMARY KEY(class,key)) WITHOUT ROWID"
    )
    conn.execute(
        "CREATE TEMP TABLE collect_set_roots(id TEXT PRIMARY KEY, expanded INTEGER NOT NULL) "
        "WITHOUT ROWID"
    )
    conn.execute(
        "CREATE INDEX temp.collect_set_roots_queue ON collect_set_roots(expanded) WHERE expanded=0"
    )
    conn.execute(
        "CREATE TEMP TABLE collect_set_candidates(id TEXT PRIMARY KEY, size INTEGER NOT NULL, "
        "young INTEGER NOT NULL) WITHOUT ROWID"
    )
    conn.execute("CREATE TEMP TABLE collect_manifest_roots(digest TEXT PRIMARY KEY) WITHOUT ROWID")
    conn.execute("CREATE TEMP TABLE collect_artifact_roots(digest TEXT PRIMARY KEY) WITHOUT ROWID")
    conn.execute("CREATE TEMP TABLE collect_batch(key TEXT PRIMARY KEY) WITHOUT ROWID")


def _schema(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _mark(
    conn: sqlite3.Connection,
    state_dir: Path,
    now: datetime,
    classes: tuple[str, ...] = CLASSES,
    orphans: tuple[str, ...] = ORPHANS,
) -> _Mark:
    """Fill the victim lists in one read snapshot; digest them, sorted.

    Call inside `sqlite_temp_directory`: the temporary tables live there.
    """
    before = cutoff(now)
    stamp = before.isoformat()
    _create_temporary(conn)
    marks = _Marks()
    state: dict[str, str] = {}
    details: dict[str, Any] = {}
    conn.execute("BEGIN")
    try:
        for cls in classes:
            if cls not in CLASSES:
                raise ValueError(f"not a collected class: {cls}")
            reason: str | None
            # One class that raises is isolated: its marks roll back to the
            # savepoint, its state says why, and the other classes go on.
            conn.execute("SAVEPOINT collect_mark")
            try:
                if cls in OBJECT_CLASSES:
                    reason = _mark_objects(conn, cls, stamp)
                elif cls == "release_manifest":
                    reason = _mark_manifests(conn, state_dir, before)
                elif cls == "partition_artifact":
                    reason = _mark_artifacts(conn, state_dir, before)
                else:
                    reason, details = _mark_dependency_sets(conn, state_dir, stamp, marks)
            except CLASS_ERRORS as error:
                conn.execute("ROLLBACK TO collect_mark")
                reason = _error(error)
            conn.execute("RELEASE collect_mark")
            if reason is not None:
                state[cls] = reason
        if orphans:
            for name, reason in _mark_orphans(conn, state_dir, before).items():
                if name in orphans:
                    state[name] = reason
            conn.execute(
                "DELETE FROM temp.collect_victims WHERE class IN ("
                + ",".join("?" * len(ORPHANS))
                + ") AND class NOT IN ("
                + ",".join("?" * len(orphans))
                + ")",
                (*ORPHANS, *orphans),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    summary: dict[str, Any] = {}
    for cls in (*classes, *orphans):
        hasher = hashlib.sha256()
        count = size = 0
        for key, value in conn.execute(
            "SELECT key,size FROM temp.collect_victims WHERE class=? ORDER BY key", (cls,)
        ):
            hasher.update(f"{key}\t{value}\n".encode())
            count += 1
            size += int(value)
        entry: dict[str, Any] = {
            "victims": count,
            "references" if cls == "partial_refs" else "bytes": size,
            "digest": hasher.hexdigest(),
        }
        if cls in state:
            entry["state"] = state[cls]
        summary[cls] = entry
    content: dict[str, Any] = {
        "format": PLAN_FORMAT,
        "schema_version": _schema(conn),
        "cutoff": stamp,
        "classes": summary,
    }
    content["digest"] = hashlib.sha256(canonical_json(content)).hexdigest()
    if details:
        content["dependency_set_roots"] = details
    return _Mark(content, marks)


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def _never() -> bool:
    return False


#: What one class's failure is, in the mark or the sweep: anything but a
#: stop of the process. It is recorded as `error: ...` and the next class runs.
CLASS_ERRORS = (RuntimeError, ValueError, KeyError, TypeError, OSError, sqlite3.Error)


def _error(error: BaseException) -> str:
    return f"error: {type(error).__name__}: {error}"[:300]


def _batch(conn: sqlite3.Connection, cls: str, after: str) -> list[tuple[str, int]]:
    return [
        (str(key), int(size))
        for key, size in conn.execute(
            "SELECT key,size FROM temp.collect_victims WHERE class=? AND key>? ORDER BY key LIMIT ?",
            (cls, after, BATCH),
        )
    ]


@dataclass
class _Tally:
    removed: int = 0
    size: int = 0
    stopped: str | None = None
    extra: dict[str, int] = field(default_factory=dict)

    def report(self, cls: str) -> dict[str, Any]:
        value: dict[str, Any] = {
            "removed": self.removed,
            "references" if cls == "partial_refs" else "bytes": self.size,
            **self.extra,
        }
        if self.stopped:
            value["stopped"] = self.stopped
        return value


def _sweep_objects(database: Database, cls: str, before: str, stop: Callable[[], bool]) -> _Tally:
    tally = _Tally()
    store = objects.Store(database.state_dir)
    after = ""
    while True:
        if stop():
            tally.stopped = "stop"
            return tally
        moved = _Moved(store)
        with moved, database.transaction() as conn:
            if publication_active(conn):
                tally.stopped = "publication_active"
                return tally
            batch = _batch(conn, cls, after)
            if not batch:
                return tally
            after = batch[-1][0]
            for digest, size in batch:
                if conn.execute(
                    "DELETE FROM objects AS o WHERE o.class=? AND o.digest=? "
                    "AND julianday(o.stored_at)<julianday(?) "
                    f"AND NOT EXISTS({_OBJECT_ROOTS[cls]})",
                    (cls, digest, before),
                ).rowcount:
                    moved.away(cls, digest)
                    tally.removed += 1
                    tally.size += size


#: An object the collector moved aside inside its transaction.
COLLECTING_PREFIX = ".collecting-"


class _Moved:
    """Object files a sweep's transaction removes, moved aside inside it.

    A writer that reuses an object records its row after the file check
    (`objects.record`), so the file must be gone by the time this
    transaction commits, not after: each victim is renamed to
    `objects/.collecting-<class>-<digest>` in the transaction, renamed back
    if it rolls back, and unlinked once it commits. A crash after the commit
    leaves only a `.collecting-` file, which the orphan sweep removes.
    """

    def __init__(self, store: objects.Store) -> None:
        self.store = store
        self.moved: list[tuple[Path, Path]] = []

    def away(self, cls: str, digest: str) -> int:
        path = self.store.path(cls, digest)
        aside = self.store.root / f"{COLLECTING_PREFIX}{cls}-{digest}"
        try:
            size = path.stat().st_size
            os.replace(path, aside)
        except FileNotFoundError:
            return 0
        self.moved.append((path, aside))
        return size

    def __enter__(self) -> _Moved:
        return self

    def __exit__(self, kind: object, error: object, trace: object) -> None:
        for path, aside in reversed(self.moved):
            if kind is None:
                aside.unlink(missing_ok=True)
            else:
                with contextlib.suppress(FileNotFoundError):
                    os.replace(aside, path)
        self.moved.clear()


def _sweep_manifests(database: Database, before: datetime, stop: Callable[[], bool]) -> _Tally:
    tally = _Tally()
    state_dir = database.state_dir
    after = ""
    while True:
        if stop():
            tally.stopped = "stop"
            return tally
        paths: list[Path] = []
        moved = _Moved(objects.Store(state_dir))
        with moved, database.transaction() as conn:
            if publication_active(conn):
                tally.stopped = "publication_active"
                return tally
            batch = _batch(conn, "release_manifest", after)
            if not batch:
                return tally
            after = batch[-1][0]
            _manifest_roots(conn, state_dir)
            for digest, _ in batch:
                if conn.execute(
                    "SELECT 1 FROM temp.collect_manifest_roots WHERE digest=?", (digest,)
                ).fetchone() or _manifest_young(conn, state_dir, digest, before):
                    continue
                if _has_table(conn, "objects"):
                    conn.execute(
                        "DELETE FROM objects WHERE class='release_manifest' AND digest=?",
                        (digest,),
                    )
                conn.execute("DELETE FROM release_manifests WHERE manifest_digest=?", (digest,))
                tally.size += moved.away("release_manifest", digest)
                paths.append(_manifest_paths(state_dir, digest)[1])
                tally.removed += 1
        for path in paths:
            tally.size += _remove(path) if path.exists() else 0


def _sweep_artifacts(database: Database, before: datetime, stop: Callable[[], bool]) -> _Tally:
    """Rows and directories go inside one write, so a build that names an
    artifact either sees it gone and writes it again, or names it first and
    keeps it."""
    from .retention import artifact_directory, partition_root

    tally = _Tally(extra={"rows": 0, "temporary": 0})
    state_dir = database.state_dir
    after = ""
    while True:
        if stop():
            tally.stopped = "stop"
            return tally
        with database.transaction() as conn:
            batch = _batch(conn, "partition_artifact", after)
            if not batch:
                return tally
            after = batch[-1][0]
            if not _artifact_roots(conn, state_dir):
                tally.stopped = "unreadable"
                return tally
            for key, _ in batch:
                if not _is_digest(key):
                    path = partition_root(state_dir) / key
                    if path.exists() and _older(path, before):
                        tally.size += _remove(path)
                        tally.removed += 1
                        tally.extra["temporary"] += 1
                    continue
                if conn.execute(
                    "SELECT 1 FROM temp.collect_artifact_roots WHERE digest=?", (key,)
                ).fetchone():
                    continue
                row = conn.execute(
                    "SELECT last_named_at FROM release_artifacts WHERE artifact_digest=?", (key,)
                ).fetchone()
                directory = artifact_directory(state_dir, key)
                if row is not None:
                    if datetime.fromisoformat(str(row[0])) >= before:
                        continue
                    conn.execute("DELETE FROM release_artifacts WHERE artifact_digest=?", (key,))
                    tally.extra["rows"] += 1
                elif not _older(directory, before):
                    continue
                if directory.exists():
                    tally.size += _remove(directory)
                    tally.removed += 1


#: The backup set writer's own loop lock (`loop-backup.lock`).
BACKUP_LOOP = "backup"


def _beside_backup(
    database: Database, cls: str, before: datetime, stop: Callable[[], bool]
) -> _Tally:
    """Manifests and artifacts go only while no backup set is being written.

    A set copies the database first and lists the files after; one removed
    in between would be named by the copy and absent from the set
    (`[backup-incomplete]`, a failed drill). The backup holds its loop lock
    throughout, so a sweep that cannot take it stops these classes until
    the next pass.
    """
    from . import rwlock

    held = rwlock.try_loop(database.state_dir, BACKUP_LOOP)
    if held is None:
        return _Tally(stopped="backup_running")
    try:
        if cls == "release_manifest":
            return _sweep_manifests(database, before, stop)
        return _sweep_artifacts(database, before, stop)
    finally:
        held.release()


def _lift(conn: sqlite3.Connection, reason: str, now: datetime) -> str | None:
    """Open the set no-delete guard for this transaction; returns what restores it."""
    if _storage_gate(conn):
        conn.execute(
            "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) VALUES (1,?,?)",
            (reason, objects.stamp(now)),
        )
        return None
    trigger = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (_NO_DELETE,)
    ).fetchone()
    if trigger is None:
        raise CollectError(f"the {_NO_DELETE} trigger is missing")
    conn.execute(f"DROP TRIGGER {_NO_DELETE}")
    return str(trigger[0])


def _restore(conn: sqlite3.Connection, trigger: str | None) -> None:
    if trigger is None:
        conn.execute("DELETE FROM storage_eviction_authority")
    else:
        conn.execute(trigger)


def _sweep_sets(
    database: Database, marks: _Marks, before: str, now: datetime, stop: Callable[[], bool]
) -> _Tally:
    tally = _Tally()
    conn = database.connection
    young = (
        " AND (d.created_at IS NULL OR julianday(d.created_at)<julianday(?))"
        if _has_column(conn, "derivation_dependency_sets", "created_at")
        else " AND ? IS NOT NULL"
    )
    equivalences = (
        " AND NOT EXISTS(SELECT 1 FROM derivation_equivalences q "
        "WHERE q.dependency_set_id=d.dependency_set_id)"
        if _has_table(conn, "derivation_equivalences")
        else ""
    )
    # Every rule again, in the deleting transaction, after catching up.
    statement = (
        "DELETE FROM derivation_dependency_sets AS d WHERE d.dependency_set_id IN "
        "(SELECT key FROM temp.collect_batch) AND d.rowid<? "
        "AND NOT EXISTS(SELECT 1 FROM temp.collect_set_roots r WHERE r.id=d.dependency_set_id) "
        "AND NOT EXISTS(SELECT 1 FROM derivation_generations g "
        "WHERE g.dependency_set_id=d.dependency_set_id) "
        "AND d.dependency_set_id NOT IN (SELECT materialized_signature "
        "FROM derivation_scopes WHERE materialized_signature IS NOT NULL)"
        f"{equivalences}{young}"
    )
    after: str | None = ""
    while after is not None:
        if stop():
            tally.stopped = "stop"
            return tally
        try:
            after = _delete_sets(database, marks, statement, before, now, after, tally)
        except CollectError as error:
            tally.stopped = f"refused: {error}"
            return tally
    return tally


def _delete_sets(
    database: Database,
    marks: _Marks,
    statement: str,
    before: str,
    now: datetime,
    after: str,
    tally: _Tally,
) -> str | None:
    """One transaction: catch up, then delete one batch. None when done or held."""
    with database.transaction() as conn:
        if publication_active(conn):
            tally.stopped = "publication_active"
            return None
        batch = _batch(conn, "dependency_set", after)
        if not batch:
            return None
        _catch_up(conn, database.state_dir, marks)
        conn.execute("DELETE FROM temp.collect_batch")
        conn.executemany(
            "INSERT INTO temp.collect_batch(key) VALUES (?)", ((key,) for key, _ in batch)
        )
        trigger = _lift(conn, "collect dependency sets", now)
        try:
            conn.execute(statement, (marks.first_sets, before))
        finally:
            _restore(conn, trigger)
        count, size = conn.execute(
            "SELECT count(*),coalesce(sum(v.size),0) FROM temp.collect_batch b "
            "JOIN temp.collect_victims v ON v.class='dependency_set' AND v.key=b.key "
            "WHERE NOT EXISTS(SELECT 1 FROM derivation_dependency_sets d "
            "WHERE d.dependency_set_id=b.key)"
        ).fetchone()
        tally.removed += int(count)
        tally.size += int(size)
    return batch[-1][0]


def _sweep_writing(database: Database, before: datetime, stop: Callable[[], bool]) -> _Tally:
    tally = _Tally()
    root = objects.Store(database.state_dir).root
    after = ""
    while not stop():
        batch = _batch(database.connection, "writing", after)
        if not batch:
            return tally
        after = batch[-1][0]
        for name, _ in batch:
            path = root / name
            if path.exists() and _older(path, before):
                tally.size += _remove(path)
                tally.removed += 1
    tally.stopped = "stop"
    return tally


def _sweep_unindexed(database: Database, before: datetime, stop: Callable[[], bool]) -> _Tally:
    tally = _Tally()
    store = objects.Store(database.state_dir)
    after = ""
    while not stop():
        # Inside a write: no object row for these can commit meanwhile.
        with database.transaction() as conn:
            batch = _batch(conn, "unindexed", after)
            if not batch:
                return tally
            after = batch[-1][0]
            for key, _ in batch:
                cls, digest = key.split("/", 1)
                path = store.path(cls, digest)
                if not path.is_file() or not _older(path, before):
                    continue
                if conn.execute(
                    "SELECT 1 FROM objects WHERE class=? AND digest=?", (cls, digest)
                ).fetchone():
                    continue
                tally.size += _remove(path)
                tally.removed += 1
    tally.stopped = "stop"
    return tally


def _sweep_partial(
    database: Database, before: str, now: datetime, stop: Callable[[], bool]
) -> _Tally:
    """Delete partial generations' references from the top, `CHUNK_ROWS` a write.

    The label stays and the generation stays partial; a run whose inputs return
    to it stores it again by ordinal (D-0217).
    """
    from .derivations import CHUNK_ROWS

    tally = _Tally(extra={"generations": 0, "payloads": 0})
    after = ""
    while True:
        batch = _batch(database.connection, "partial_refs", after)
        if not batch:
            return tally
        after = batch[-1][0]
        for generation_id, _ in batch:
            touched = False
            while True:
                if stop():
                    tally.stopped = "stop"
                    return tally
                with database.transaction() as conn:
                    if publication_active(conn):
                        tally.stopped = "publication_active"
                        return tally
                    if not conn.execute(
                        f"SELECT 1 FROM derivation_generations g WHERE g.generation_id=? "
                        f"AND {_PARTIAL}",
                        (generation_id, before),
                    ).fetchone():
                        break
                    chunk = conn.execute(
                        "SELECT ordinal,payload_sha256 FROM derivation_row_refs "
                        "WHERE generation_id=? ORDER BY ordinal DESC LIMIT ?",
                        (generation_id, CHUNK_ROWS),
                    ).fetchall()
                    conn.execute(
                        "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) "
                        "VALUES (1,?,?)",
                        ("collect partial " + generation_id, objects.stamp(now)),
                    )
                    try:
                        deleted = conn.execute(
                            "DELETE FROM derivation_row_refs WHERE generation_id=? AND ordinal>=?",
                            (generation_id, int(chunk[-1][0])),
                        ).rowcount
                        for payload in sorted({str(row[1]) for row in chunk}):
                            tally.extra["payloads"] += conn.execute(
                                "DELETE FROM derivation_payloads WHERE payload_sha256=? "
                                "AND NOT EXISTS(SELECT 1 FROM derivation_row_refs "
                                "WHERE payload_sha256=?)",
                                (payload, payload),
                            ).rowcount
                    finally:
                        conn.execute("DELETE FROM storage_eviction_authority")
                    tally.size += deleted
                    touched = True
                if len(chunk) < CHUNK_ROWS:
                    break
            if touched:
                tally.removed += 1
                tally.extra["generations"] += 1


def _sweep_marked(
    database: Database,
    mark: _Mark,
    now: datetime,
    stop: Callable[[], bool],
) -> dict[str, Any]:
    before = cutoff(now)
    stamp = before.isoformat()
    result: dict[str, Any] = {}
    seconds: dict[str, float] = {}
    for cls, entry in mark.content["classes"].items():
        if entry.get("state") or not entry["victims"]:
            result[cls] = _Tally(stopped=entry.get("state")).report(cls)
            continue
        started = time.monotonic()
        try:
            if cls in OBJECT_CLASSES:
                tally = _sweep_objects(database, cls, stamp, stop)
            elif cls in ("release_manifest", "partition_artifact"):
                tally = _beside_backup(database, cls, before, stop)
            elif cls == "dependency_set":
                tally = _sweep_sets(database, mark.marks, stamp, now, stop)
            elif cls == "writing":
                tally = _sweep_writing(database, before, stop)
            elif cls == "unindexed":
                tally = _sweep_unindexed(database, before, stop)
            else:
                tally = _sweep_partial(database, stamp, now, stop)
        except CLASS_ERRORS as error:
            # Its own transactions rolled back; the next class still runs.
            if database.connection.in_transaction:
                database.connection.rollback()
            tally = _Tally(stopped=_error(error))
        seconds[cls] = round(time.monotonic() - started, 3)
        result[cls] = tally.report(cls)
        if tally.stopped == "stop":
            break
    return {"classes": result, "seconds": seconds}


def _require_hold(database: Database, classes: tuple[str, ...]) -> None:
    """Before schema 36 sets carry no age, so no loop may be writing them."""
    if (
        "dependency_set" in classes
        and not _storage_gate(database.connection)
        and not (database.state_dir / _HOLD).is_file()
    ):
        raise CollectError(
            f"dependency sets before schema 36 are collected under {_HOLD}; create "
            f"{database.state_dir / _HOLD} and wait until no loop is running. Nothing was removed"
        )


def _usage(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "page_size": int(conn.execute("PRAGMA page_size").fetchone()[0]),
        "page_count": int(conn.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(conn.execute("PRAGMA freelist_count").fetchone()[0]),
    }


def sweep(
    database: Database,
    *,
    now: datetime,
    stop: Callable[[], bool] | None = None,
    classes: tuple[str, ...] = CLASSES,
    orphans: tuple[str, ...] = ORPHANS,
) -> dict[str, Any]:
    """Maintain's collection: mark, then remove what the rules name, bounded by `stop`.

    Every delete rechecks its rule in its own transaction, so a sweep stopped
    part way leaves a consistent state and the next one finishes it.
    """
    conn = database.connection
    if conn.in_transaction:
        raise CollectError("the collector cannot run inside a transaction")
    _require_hold(database, classes)
    started = time.monotonic()
    # Deletes free pages without zeroing them through the WAL (`disk.fast_delete`).
    with sqlite_temp_directory(conn, database.state_dir), disk.fast_delete(conn):
        try:
            mark = _mark(conn, database.state_dir, now, classes, orphans)
            swept = _sweep_marked(database, mark, now, stop or _never)
        finally:
            _drop_temporary(conn)
    return {
        "cutoff": mark.content["cutoff"],
        "planned": {cls: entry["victims"] for cls, entry in mark.content["classes"].items()},
        **swept,
        "mark_seconds": round(time.monotonic() - started, 3),
    }


#: Scratch directories a build makes under `state_dir/tmp`; a killed build
#: leaves them (the baseline spool, the assembly's staging, the closure spool).
SCRATCH_PREFIXES = ("assembly-", "partitions-", "closure-")
#: Build scratch younger than this stays.
SCRATCH_AGE = timedelta(days=1)


def collect_artifacts(database: Database, *, now: datetime) -> dict[str, int]:
    """Maintain's daily pass over partition artifacts, release manifests and
    build scratch (phase 3, WP6; moved here so the build's code scope does
    not name the collector).

    An artifact's row and directory go inside one write, so a build that
    names an artifact either sees it gone and writes it again, or names it
    first and keeps it.
    """
    result = {
        "removed": 0,
        "bytes": 0,
        "rows": 0,
        "temporary": 0,
        "kept": 0,
        "unreadable": 0,
        "manifests": 0,
        "scratch": 0,
    }
    scratch = database.state_dir / "tmp"
    if scratch.is_dir():
        for path in sorted(scratch.iterdir()):
            if path.name.startswith(SCRATCH_PREFIXES) and _older(path, now - SCRATCH_AGE):
                result["bytes"] += _remove(path)
                result["scratch"] += 1
    swept = sweep(
        database, now=now, classes=("partition_artifact", "release_manifest"), orphans=()
    )["classes"]
    artifacts, manifests = swept["partition_artifact"], swept["release_manifest"]
    result["removed"] = int(artifacts["removed"])
    result["rows"] = int(artifacts.get("rows", 0))
    result["temporary"] = int(artifacts.get("temporary", 0))
    result["unreadable"] = int(artifacts.get("stopped") == "unreadable")
    result["manifests"] = int(manifests["removed"])
    result["bytes"] += int(artifacts["bytes"]) + int(manifests["bytes"])
    if _has_table(database.connection, "release_artifacts"):
        result["kept"] = int(
            database.connection.execute("SELECT count(*) FROM release_artifacts").fetchone()[0]
        )
    return result


# ---------------------------------------------------------------------------
# Plan and apply
# ---------------------------------------------------------------------------


def plan(database: Database, *, now: datetime) -> dict[str, Any]:
    """What `apply` would remove, by class, with the plan digest. Removes nothing."""
    conn = database.connection
    if conn.in_transaction:
        raise CollectError("the collector cannot plan inside a transaction")
    with sqlite_temp_directory(conn, database.state_dir):
        try:
            return dict(_mark(conn, database.state_dir, now).content)
        finally:
            _drop_temporary(conn)


def write_plan(state_dir: Path, value: dict[str, Any]) -> Path:
    """`gc/plans/collect-<digest>.json`, written once."""
    path = state_dir / "gc" / "plans" / f"collect-{value['digest']}.json"
    if not path.is_file():
        durable_write(path, canonical_json(value))
    return path


def _note(conn: sqlite3.Connection, plan_digest: str) -> tuple[str, str | None] | None:
    if not _has_table(conn, "storage_applies"):
        return None
    row = conn.execute(
        "SELECT started_at,receipt_json FROM storage_applies WHERE plan_digest=?", (plan_digest,)
    ).fetchone()
    return None if row is None else (str(row[0]), None if row[1] is None else str(row[1]))


def _write_receipt(state_dir: Path, plan_digest: str, receipt: dict[str, Any]) -> dict[str, Any]:
    path = receipt_directory(state_dir) / f"collect-{plan_digest}.json"
    if not path.is_file():
        durable_write(path, canonical_json(receipt))
    return {**receipt, "receipt": str(path)}


def apply(
    database: Database, *, plan_digest: str, now: datetime, timeout: float = 60
) -> dict[str, Any]:
    """Remove what the plan with this digest names; return the receipt.

    Needs the writer lock, then takes the control lock. The plan is computed
    again and must have the same digest. A finished digest returns its
    receipt; one that stopped part way no longer matches its state: plan again.
    """
    if not database.holds_writer_lock:
        raise CollectError("gc --collect --apply needs the writer lock; open the state for writing")
    conn = database.connection
    if conn.in_transaction:
        raise CollectError("the collector cannot apply inside a transaction")
    _require_hold(database, CLASSES)
    state_dir = database.state_dir
    with (
        control_lock(state_dir, timeout=timeout),
        sqlite_temp_directory(conn, state_dir),
        disk.fast_delete(conn),
    ):
        try:
            recorded = _note(conn, plan_digest)
            if recorded is not None and recorded[1] is not None:
                return _write_receipt(state_dir, plan_digest, dict(json.loads(recorded[1])))
            started = time.monotonic()
            mark = _mark(conn, state_dir, now)
            plan_seconds = round(time.monotonic() - started, 3)
            if mark.digest != plan_digest:
                raise CollectError(
                    f"plan {plan_digest} is not the plan of this state, which is {mark.digest}; "
                    "nothing was removed. Write a new plan with `gc --collect --plan`"
                )
            if publication_active(conn):
                raise CollectError("a publication is active; nothing was removed")
            started_at = objects.stamp(now)
            if _has_table(conn, "storage_applies") and recorded is None:
                with database.transaction() as txn:
                    txn.execute(
                        "INSERT INTO storage_applies(plan_digest,kind,started_at) "
                        "VALUES (?,'collect',?)",
                        (plan_digest, started_at),
                    )
            before = _usage(conn)
            swept = _sweep_marked(database, mark, now, _never)
            receipt = {
                "format": RECEIPT_FORMAT,
                "plan_digest": plan_digest,
                "started_at": recorded[0] if recorded else started_at,
                "cutoff": mark.content["cutoff"],
                "planned": mark.content["classes"],
                "removed": swept["classes"],
                "seconds": {"plan": plan_seconds, **swept["seconds"]},
                "before": before,
                "after": _usage(conn),
                "next": "gc --reclaim rewrites the file so the freed pages leave it",
            }
            if _has_table(conn, "storage_applies"):
                with database.transaction() as txn:
                    txn.execute(
                        "UPDATE storage_applies SET finished_at=?,receipt_json=? "
                        "WHERE plan_digest=?",
                        (objects.stamp(now), canonical_json(receipt).decode(), plan_digest),
                    )
        finally:
            _drop_temporary(conn)
    return _write_receipt(state_dir, plan_digest, receipt)


# ---------------------------------------------------------------------------
# Release manifests into the object store (`storage apply`)
# ---------------------------------------------------------------------------


def move_manifests(database: Database, *, now: datetime) -> dict[str, int]:
    """Move phase 3 manifest files into `release_manifest` objects.

    Per file: copy into the store, verify the object, record its row, then
    unlink the old file. A file whose bytes do not hash to its name stays
    where it is (unknown, D-0143). Idempotent.
    """
    from .retention import partition_root

    store = objects.Store(database.state_dir)
    directory = partition_root(database.state_dir) / "manifests"
    result = {"moved": 0, "bytes": 0, "mismatched": 0}
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.json")):
        digest = path.stem
        if not _is_digest(digest):
            continue
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != digest:
            result["mismatched"] += 1
            continue
        found, stored = store.put_bytes("release_manifest", body)
        store.verify("release_manifest", found)
        with database.transaction() as conn:
            objects.record(conn, "release_manifest", digest, len(body), stored, now)
        path.unlink()
        result["moved"] += 1
        result["bytes"] += len(body)
    return result
