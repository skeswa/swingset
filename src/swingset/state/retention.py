"""One reachability walk over derivation history, two lists, and one written plan.

Plan step 3 of [the bounded state plan](../../../docs/plans/bounded-state-and-archive.md).
A saved computation is three things with three lifetimes: a permanent label, the
output bytes the label describes, and the pipeline's own current pointers. This
module answers retention's only question, which output bytes and which files
have to stay in the live state directory, and writes the answer down. It removes
nothing and never moves a pointer.

The two lists come from one walk:

- **durable** is every generation that has a label. Its bytes must exist in at
  least one checked place forever, local or archived. Ordinary superseded
  generations are on it, so they are never "unknown".
- **local** is every generation the walk reaches from a root. Roots are read
  from state that already exists, so no second list can drift.

Durable minus local is archivable. "Unknown" is separate and much smaller:
payload bytes no reference names, payload bytes only an unlabelled reference
names, a row reference whose label is missing, a file under `blobs/`,
`extracts/` or `inputs/` that nothing declares, and a file an open finding
declares that is not there. Nothing is ever removed because it is unknown, and
unknown bytes are never counted as bytes archiving would give back.

This module also owns the file closure a checkpoint copies, so one walk decides
what a backup carries and what retention keeps. The old direct collector is gone:
[`retention_apply`](retention_apply.py) removes what a written plan names, and
nothing else removes anything
([D-0147](../../../journal/decisions/0147-only-a-planned-locked-apply-removes-anything.md)).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from swingset.build.closure_manifest import ClosureError, leaves
from swingset.build.files import canonical_json, durable_write, fsync_dir

from .derivations import interned

ARTIFACT_DIRECTORIES = ("blobs", "extracts", "inputs")
HOLD_FORMAT = "retention-hold-v1"
PLAN_FORMAT = "retention-plan-v1"
SIZE_CAP_REASON = "state database file is over its retention size cap"
#: What an operator can actually do about the cap. `gc --apply` frees pages
#: inside the file and `gc --reclaim` gives them back to the filesystem, which
#: is what the cap measures; raising the cap is still the way to run again now.
SIZE_CAP_REMEDY = (
    "run gc --plan, then gc --apply <digest>, then gc --reclaim to shrink the file; "
    "or raise retention.max_database_bytes in config/sources.toml, then resume"
)

#: Why a generation stays local. One per kind of root, in report order.
ROOT_KINDS = (
    "baseline",
    "pending_candidate",
    "pointer",
    "finding",
    "hold",
    "restore_marker",
    "operator_hold",
    "window",
)


class RetentionError(RuntimeError):
    """One closure, one error.

    `backup.checkpoint.CheckpointError` is this class. The file closure moved
    here with the walk, and the checkpoint messages it raises are unchanged.
    """


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _blob_path(state_dir: Path, digest: str) -> Path:
    return state_dir / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest


# ---------------------------------------------------------------------------
# The file closure
# ---------------------------------------------------------------------------


def artifact_closure(
    state_dir: Path,
    connection: sqlite3.Connection,
    candidates: set[Path],
    *,
    require_present: bool = True,
) -> set[Path]:
    """Every file the database and the given candidates declare.

    `require_present` is how a backup differs from a plan. A checkpoint refuses
    to be written when a declared artifact is gone; the planner wants to report
    that instead of failing, so it asks for the reachable set and reads the
    missing ones from `missing_artifacts`.
    """
    included, _ = _artifact_closure(
        state_dir, connection, candidates, require_present=require_present
    )
    return included


def missing_artifacts(
    state_dir: Path, connection: sqlite3.Connection, candidates: set[Path]
) -> list[Path]:
    """Declared files that are not there. A checkpoint would refuse on these."""
    _, missing = _artifact_closure(state_dir, connection, candidates, require_present=False)
    return missing


def _artifact_closure(
    state_dir: Path,
    connection: sqlite3.Connection,
    candidates: set[Path],
    *,
    require_present: bool,
) -> tuple[set[Path], list[Path]]:
    included: set[Path] = set()
    table_names = _tables(connection)
    if "snapshots" in table_names:
        for body, extract in connection.execute(
            "SELECT body_sha256, extract_sha256 FROM snapshots"
        ):
            if body:
                included.add(_blob_path(state_dir, str(body)))
            if extract:
                included.add(state_dir / "extracts" / str(extract))
    if "source_generations" in table_names:
        for generation_id, manifest_json in connection.execute(
            "SELECT generation_id,manifest_json FROM source_generations ORDER BY generation_id"
        ):
            try:
                manifest = json.loads(manifest_json)
            except (TypeError, ValueError) as exc:
                raise RetentionError(
                    f"invalid generation artifact manifest: {generation_id}"
                ) from exc
            if not isinstance(manifest, list) or any(
                not isinstance(item, dict) for item in manifest
            ):
                raise RetentionError(f"invalid generation artifact manifest: {generation_id}")
            for item in manifest:
                for field in ("body_sha256", "extract_sha256"):
                    value = item.get(field)
                    if value is None:
                        continue
                    if not _is_digest(value):
                        raise RetentionError(f"invalid generation artifact digest: {generation_id}")
                    included.add(
                        _blob_path(state_dir, value)
                        if field == "body_sha256"
                        else state_dir / "extracts" / value
                    )
    if "hosts" in table_names:
        for (body,) in connection.execute(
            "SELECT robots_sha256 FROM hosts WHERE robots_sha256 IS NOT NULL"
        ):
            included.add(_blob_path(state_dir, str(body)))
    included.update(_finding_artifacts(state_dir, connection, table_names))
    # A hold names a digest, not which kind of file it is, so whichever of the
    # two exists is the file it is holding. `add_hold` refuses a digest that is
    # neither, so this cannot quietly hold nothing.
    for digest in hold_artifacts(state_dir):
        included.update(
            path
            for path in (_blob_path(state_dir, digest), state_dir / "extracts" / digest)
            if path.is_file()
        )
    bundle_hashes: set[str] = set()
    if "meta" in table_names:
        row = connection.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
        if row and row[0]:
            bundle_hashes.add(str(row[0]))
    for candidate in candidates:
        manifest_path = candidate / "_meta" / "manifest.json"
        if manifest_path.is_file():
            value = json.loads(manifest_path.read_text()).get("input_bundle_hash")
            if value:
                bundle_hashes.add(str(value))
    missing: list[Path] = []
    for bundle_hash in bundle_hashes:
        bundle = state_dir / "inputs" / bundle_hash
        if not bundle.is_dir():
            if require_present:
                raise RetentionError(f"referenced input bundle is missing: {bundle_hash}")
            missing.append(bundle)
            continue
        included.update(path for path in bundle.rglob("*") if path.is_file())
    missing.extend(path for path in sorted(included) if not path.is_file())
    if missing and require_present:
        raise RetentionError(f"referenced artifact is missing: {missing[0].relative_to(state_dir)}")
    return {path.resolve() for path in included}, missing


def _finding_artifacts(
    state_dir: Path, connection: sqlite3.Connection, table_names: set[str]
) -> Iterator[Path]:
    """Files open findings rely on.

    Findings declare their support in `finding_support_references` from schema
    30 on. A checkpoint of an older schema has no such table, so the original
    scan of the evidence JSON stays as the fallback for those, and only for
    those. It is what migration 30 backfilled the table from.
    """
    if "findings" not in table_names:
        return
    if "finding_support_references" in table_names:
        for kind, digest in connection.execute(
            "SELECT r.kind,r.sha256 FROM finding_support_references r "
            "JOIN findings f USING(finding_id) WHERE f.closed_at IS NULL ORDER BY r.sha256,r.kind"
        ):
            if kind == "body":
                path = _blob_path(state_dir, str(digest))
            elif kind == "extract":
                path = state_dir / "extracts" / str(digest)
            else:
                continue
            # A declaration that names a file which is not there is reported by
            # the planner as `missing_declared_references`, not raised. Nothing
            # else declares a file the writer never checked, and one bad row
            # must not stop every backup: the pre-30 evidence scan kept only
            # files that existed, and this keeps that behaviour.
            if path.is_file():
                yield path
        return
    for (evidence_json,) in connection.execute(
        "SELECT evidence_json FROM findings WHERE closed_at IS NULL"
    ):
        for digest in declared_digests(str(evidence_json)):
            possibilities = (
                _blob_path(state_dir, digest),
                state_dir / "extracts" / digest,
            )
            yield from (path for path in possibilities if path.is_file())


def _missing_declared_references(conn: sqlite3.Connection, state_dir: Path) -> list[str]:
    """Files an open finding declares that are not on disk, as `finding/kind/sha256`.

    Doctor shows these so a wrong declaration is found and fixed. They are
    reported, never raised: a finding is written by many callers, and one bad
    row must not stop a backup that is otherwise complete.
    """
    if "finding_support_references" not in _tables(conn):
        return []
    found = []
    for finding_id, kind, digest in conn.execute(
        "SELECT r.finding_id,r.kind,r.sha256 FROM finding_support_references r "
        "JOIN findings f USING(finding_id) WHERE f.closed_at IS NULL AND r.kind IN ('body',"
        "'extract') ORDER BY r.finding_id,r.kind,r.sha256"
    ):
        path = (
            _blob_path(state_dir, str(digest))
            if kind == "body"
            else state_dir / "extracts" / str(digest)
        )
        if not path.is_file():
            found.append(f"{finding_id}/{kind}/{digest}")
    return found


def declared_digests(evidence_json: str) -> Iterator[str]:
    """Every sha256-shaped string in one finding's evidence, in no order."""
    stack: list[Any] = [json.loads(evidence_json)]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif _is_digest(value):
            yield value


def referenced_candidates(state_dir: Path) -> set[Path]:
    from swingset.publish.service import pending_candidates

    result: set[Path] = set()
    baseline = state_dir / "baseline"
    if baseline.is_symlink():
        result.add(baseline.resolve())
    if (state_dir / "candidates").exists():
        result.update(path.resolve() for path in pending_candidates(state_dir))
    return result


# The old collector stood here. It removed candidate directories and artifact
# files directly, from a closure it computed on an unlocked connection, with no
# plan digest and no receipt: a hold created in the window between that closure
# and the unlink could pin a file that was already going. Plan step 3b replaced
# it with `gc --apply`, which removes only what a written plan names, under the
# writer lock and the control lock, and writes a note before the first unlink
# ([D-0147](../../../journal/decisions/0147-only-a-planned-locked-apply-removes-anything.md)).
#
# One behaviour went with it on purpose. The collector also deleted artifact
# files nothing declared, on age alone. The plan calls those "unknown" and never
# eligible: doctor reports them and nothing removes them until someone works out
# what they are (D-0143, D-0144).


def disposable_candidates(state_dir: Path) -> list[Path]:
    """Built candidates nothing points at, newest first."""
    candidates = state_dir / "candidates"
    if not candidates.exists():
        return []
    retained = referenced_candidates(state_dir)
    return sorted(
        (
            path
            for path in candidates.iterdir()
            if (path / "BUILT").is_file() and path.resolve() not in retained
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Root:
    """One reason some generations must stay local."""

    kind: str
    detail: str
    generations: tuple[str, ...]


def roots(conn: sqlite3.Connection, state_dir: Path, *, window: int) -> tuple[Root, ...]:
    """Every starting point of the walk, read from state that already exists.

    The restore marker and the operator hold pin the current pointers and the
    pending candidates. Both are already roots in their own right, so these two
    markers add no generation; they are listed so a plan says out loud that an
    interrupted restore or a paused operator keeps everything current. Neither
    marker records which generation it was about, so claiming more than the
    current pointers would be inventing a fact.
    """
    found: list[Root] = []
    found.extend(_candidate_roots(state_dir))
    pointers: list[str] = []
    for stage, unit_kind, unit_id, generation_id in conn.execute(
        "SELECT stage,unit_kind,unit_id,materialized_generation_id FROM derivation_scopes "
        "WHERE materialized_generation_id IS NOT NULL ORDER BY stage,unit_kind,unit_id"
    ):
        pointers.append(str(generation_id))
        found.append(Root("pointer", f"{stage}/{unit_kind}/{unit_id}", (str(generation_id),)))
    found.extend(_finding_roots(conn))
    found.extend(_hold_roots(state_dir))
    current = tuple(sorted({*pointers, *_candidate_generations(state_dir)}))
    if (state_dir / "RESTORE_PENDING").is_file():
        found.append(Root("restore_marker", "RESTORE_PENDING", current))
    if (state_dir / "operator-hold").is_file():
        found.append(Root("operator_hold", "operator-hold", current))
    found.extend(_window_roots(conn, window=window))
    return tuple(found)


def _candidate_roots(state_dir: Path) -> Iterator[Root]:
    baseline = state_dir / "baseline"
    seen: set[Path] = set()
    if baseline.is_symlink():
        target = baseline.resolve()
        seen.add(target)
        yield Root("baseline", target.name, _candidate_selection(target))
    from swingset.publish.service import pending_candidates

    if (state_dir / "candidates").exists():
        for path in sorted(pending_candidates(state_dir)):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield Root("pending_candidate", resolved.name, _candidate_selection(resolved))


def _candidate_generations(state_dir: Path) -> set[str]:
    return {generation for root in _candidate_roots(state_dir) for generation in root.generations}


def _candidate_selection(candidate: Path) -> tuple[str, ...]:
    """The generations a release says it was built from.

    A candidate keeps the public release closure in its own manifest, and that
    names every selected generation. Reading it there means a release pins its
    inputs even when the database has moved on.

    Only a release built in `closure` mode pins generations, so anything else
    pins none. When the mode says `closure` the list has to be there: silently
    pinning nothing would move every generation a published release was built
    from onto the archivable list, and no other check would notice.
    """
    manifest_path = candidate / "_meta" / "manifest.json"
    if not manifest_path.is_file():
        raise RetentionError(f"candidate has no manifest: {candidate.name}")
    try:
        manifest = json.loads(manifest_path.read_text())
    except ValueError as exc:
        raise RetentionError(f"unreadable candidate manifest: {candidate.name}") from exc
    policy = manifest.get("release_policy") or {}
    if policy.get("mode") != "closure":
        return ()
    closure = policy.get("closure")
    if not isinstance(closure, dict) or "selected_generations" not in closure:
        raise RetentionError(
            f"release closure is missing from candidate manifest: {candidate.name}"
        )
    selected = closure["selected_generations"]
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise RetentionError(f"invalid release closure in candidate manifest: {candidate.name}")
    return tuple(sorted(selected))


def _finding_roots(conn: sqlite3.Connection) -> Iterator[Root]:
    if "finding_support_references" not in _tables(conn):
        return
    grouped: dict[str, list[str]] = {}
    for finding_id, digest in conn.execute(
        "SELECT r.finding_id,r.sha256 FROM finding_support_references r "
        "JOIN findings f USING(finding_id) WHERE f.closed_at IS NULL AND r.kind='generation' "
        "ORDER BY r.finding_id,r.sha256"
    ):
        grouped.setdefault(str(finding_id), []).append(str(digest))
    for finding_id, generations in sorted(grouped.items()):
        yield Root("finding", finding_id, tuple(generations))


def _window_roots(conn: sqlite3.Connection, *, window: int) -> Iterator[Root]:
    """The newest `window` generations of every scope.

    The window picks roots and never replaces the walk, so an old generation a
    recent one was built from stays local through the walk, not through its age.
    """
    if window < 1:
        return
    for stage, unit_kind, unit_id, generation_id in conn.execute(
        "SELECT stage,unit_kind,unit_id,generation_id FROM ("
        "SELECT stage,unit_kind,unit_id,generation_id,"
        "row_number() OVER (PARTITION BY stage,unit_kind,unit_id "
        "ORDER BY created_at DESC,rowid DESC) AS position FROM derivation_generations"
        ") WHERE position<=? ORDER BY stage,unit_kind,unit_id,generation_id",
        (window,),
    ):
        yield Root("window", f"{stage}/{unit_kind}/{unit_id}", (str(generation_id),))


# ---------------------------------------------------------------------------
# Holds
# ---------------------------------------------------------------------------


def hold_directory(state_dir: Path) -> Path:
    return state_dir / "holds"


def holds(state_dir: Path) -> list[dict[str, Any]]:
    """Every hold file, ordered by hold id. A malformed hold is an error.

    Only `hold_*.json` is read, because that is what `add_hold` writes. An
    operator's own note saved in the same directory is therefore not mistaken
    for a broken hold. A file that is named like a hold and cannot be read is
    still an error everywhere: a hold says what a restore with no network must
    find, and guessing at half of one would be worse than stopping. Doctor
    reports the failure and keeps reporting the rest of the state.
    """
    directory = hold_directory(state_dir)
    if not directory.is_dir():
        return []
    result = []
    for path in sorted(directory.glob("hold_*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise RetentionError(f"unreadable hold file: {path.name}; repair or remove it") from exc
        if (
            not isinstance(record, dict)
            or record.get("format") != HOLD_FORMAT
            or record.get("hold_id") != path.stem
        ):
            raise RetentionError(f"unreadable hold file: {path.name}; repair or remove it")
        result.append(record)
    return result


def _hold_roots(state_dir: Path) -> Iterator[Root]:
    for record in holds(state_dir):
        generations = tuple(str(item) for item in record.get("generation_ids") or ())
        yield Root("hold", str(record["hold_id"]), generations)


def hold_artifacts(state_dir: Path) -> set[str]:
    """Artifact digests the holds name, whatever kind of file they turn out to be."""
    return {
        str(digest)
        for record in holds(state_dir)
        for digest in record.get("artifact_digests") or ()
    }


def add_hold(
    state_dir: Path,
    conn: sqlite3.Connection,
    *,
    who: str,
    why: str,
    now: datetime,
    generations: Sequence[str] = (),
    artifacts: Sequence[str] = (),
    timeout: float = 60,
) -> dict[str, Any]:
    """Write one hold, but only when everything it needs is already local.

    The caller must not hold a SQLite write transaction: this takes the control
    lock, which is ordered before SQLite. A hold is a promise that a restore
    with no network has what the hold is about, so a hold whose closure is
    archived, or which names a file that is not there, is refused and nothing is
    written.
    """
    from .control_lock import control_lock

    if not who.strip() or not why.strip():
        raise ValueError("a hold requires who asked for it and why")
    if not generations and not artifacts:
        raise ValueError("a hold must name at least one generation or artifact digest")
    for digest in artifacts:
        if not _is_digest(digest):
            raise ValueError(f"not a sha256 artifact digest: {digest}")
    record = {
        "format": HOLD_FORMAT,
        "hold_id": "hold_" + uuid.uuid4().hex,
        "who": who,
        "why": why,
        "created_at": now.isoformat(),
        "generation_ids": sorted(set(generations)),
        "artifact_digests": sorted(set(artifacts)),
    }
    with control_lock(state_dir, timeout=timeout):
        require_local(conn, record["generation_ids"])
        # A digest is held as a file, so the file has to be there. Restoring one
        # is not this plan's job, but claiming one that is gone would be a
        # promise a backup could not keep either.
        elsewhere = [
            item
            for item in record["artifact_digests"]
            if not _blob_path(state_dir, item).is_file()
            and not (state_dir / "extracts" / item).is_file()
        ]
        if elsewhere:
            raise RetentionError("no such file under blobs/ or extracts/: " + ", ".join(elsewhere))
        durable_write(
            hold_directory(state_dir) / f"{record['hold_id']}.json", canonical_json(record)
        )
    return record


def remove_hold(state_dir: Path, hold_id: str, *, timeout: float = 60) -> bool:
    from .control_lock import control_lock

    path = hold_directory(state_dir) / f"{hold_id}.json"
    with control_lock(state_dir, timeout=timeout):
        if not path.is_file():
            return False
        path.unlink()
        fsync_dir(path.parent)
    return True


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def archived_generations(conn: sqlite3.Connection) -> frozenset[str]:
    """Generations step 4 has archived. Empty until that table exists.

    The plan carries this, so the digest an operator reviews covers it. Reading
    it live at apply time instead would let a generation archived after the
    review make payload bytes eligible that the reviewed plan never named
    ([D-0155](../../../journal/decisions/0155-the-plan-digest-covers-what-is-archived.md)).
    """
    if "archived_generations" not in _tables(conn):
        return frozenset()
    return frozenset(
        str(row[0]) for row in conn.execute("SELECT generation_id FROM archived_generations")
    )


def _dependency_sets(conn: sqlite3.Connection) -> dict[str, str | None]:
    return {
        str(row[0]): (None if row[1] is None else str(row[1]))
        for row in conn.execute(
            "SELECT generation_id,dependency_set_id FROM derivation_generations"
        )
    }


def _children(conn: sqlite3.Connection, dependency_set_id: str | None) -> tuple[str, ...]:
    """Generations one dependency set names, through the shared manifest reader.

    This decodes the same immutable manifests the release closure decodes. It
    does not use the release selector: that selector refuses two generations for
    one scope, and a window above one needs exactly that. The walk collects by
    generation id and allows many per scope. It does not follow
    `previous_generation_id`; a label's predecessor is identity, not an input.
    """
    if dependency_set_id is None:
        return ()
    found: list[str] = []
    for item in leaves(conn, dependency_set_id):
        if item.get("kind") == "derivation" and item.get("generation_id") is not None:
            found.append(str(item["generation_id"]))
    return tuple(sorted(set(found)))


def reachable(
    conn: sqlite3.Connection, starting: Iterable[Root]
) -> tuple[dict[str, frozenset[str]], set[str]]:
    """Walk from every root and record which root kinds reached each generation.

    Returns the reached generations with their reasons, and the root generation
    ids that have no label at all. A missing label is unknown, not a failure of
    the walk, so it is reported rather than followed.
    """
    sets = _dependency_sets(conn)
    edges: dict[str, tuple[str, ...]] = {}

    def children(generation_id: str) -> tuple[str, ...]:
        if generation_id not in edges:
            try:
                edges[generation_id] = _children(conn, sets[generation_id])
            except ClosureError as exc:
                raise RetentionError(
                    f"unreadable dependency manifest for generation {generation_id}: {exc}"
                ) from exc
        return edges[generation_id]

    reasons: dict[str, set[str]] = {}
    absent: set[str] = set()
    for root in starting:
        for seed in root.generations:
            stack = [seed]
            while stack:
                generation_id = stack.pop()
                if generation_id not in sets:
                    absent.add(generation_id)
                    continue
                known = reasons.setdefault(generation_id, set())
                if root.kind in known:
                    continue
                known.add(root.kind)
                stack.extend(children(generation_id))
    return {key: frozenset(value) for key, value in reasons.items()}, absent


# ---------------------------------------------------------------------------
# Residency
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Residency:
    """How much of one generation's output is in the live database."""

    generation_id: str
    row_count: int
    reference_count: int
    payload_count: int
    payload_bytes: int
    missing_payloads: int

    @property
    def local(self) -> bool:
        return self.reference_count == self.row_count and not self.missing_payloads


def residency(conn: sqlite3.Connection, generation_ids: Iterable[str]) -> list[Residency]:
    shared = interned(conn)
    result = []
    for generation_id in generation_ids:
        label = conn.execute(
            "SELECT row_count FROM derivation_generations WHERE generation_id=?",
            (generation_id,),
        ).fetchone()
        if label is None:
            raise RetentionError(f"no such generation: {generation_id}")
        if shared:
            row = conn.execute(
                "SELECT count(*),count(DISTINCT payload_sha256) FROM derivation_row_refs "
                "WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
            bytes_and_missing = conn.execute(
                "SELECT coalesce(sum(octet_length(p.payload_json)),0),"
                "count(*) FILTER (WHERE p.payload_sha256 IS NULL) FROM "
                "(SELECT DISTINCT payload_sha256 FROM derivation_row_refs WHERE generation_id=?) r "
                "LEFT JOIN derivation_payloads p ON p.payload_sha256=r.payload_sha256",
                (generation_id,),
            ).fetchone()
        else:
            # Before interning nothing is shared and a row carries its own
            # bytes: each row is its own payload and none of them can be gone.
            row = conn.execute(
                "SELECT count(*),count(*) FROM derivation_rows WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
            bytes_and_missing = conn.execute(
                "SELECT coalesce(sum(octet_length(payload_json)),0),0 FROM derivation_rows "
                "WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
        result.append(
            Residency(
                str(generation_id),
                int(label[0]),
                int(row[0]),
                int(row[1]),
                int(bytes_and_missing[0]),
                int(bytes_and_missing[1]),
            )
        )
    return result


def require_local(conn: sqlite3.Connection, generation_ids: Sequence[str]) -> None:
    """Refuse a new root whose closure is not completely in the live database.

    A marker never claims data that is not there. The refusal names every
    generation to bring back, so the operator can run `gc --restore` on exactly
    those and try again.
    """
    if not generation_ids:
        return
    named = tuple(dict.fromkeys(generation_ids))
    sets = _dependency_sets(conn)
    unknown = [item for item in named if item not in sets]
    if unknown:
        raise RetentionError("no such generation: " + ", ".join(sorted(unknown)))
    reached, unlabelled = reachable(conn, (Root("hold", "requested", named),))
    absent = [item.generation_id for item in residency(conn, sorted(reached)) if not item.local]
    absent.extend(unlabelled)
    if absent:
        raise RetentionError(
            "generation output is not local; restore it first with "
            "`swingset gc --restore`: " + ", ".join(sorted(absent))
        )


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    content: dict[str, Any]

    @property
    def digest(self) -> str:
        return str(self.content["digest"])

    def bytes(self) -> bytes:
        return canonical_json(self.content)


def plan(
    conn: sqlite3.Connection,
    state_dir: Path,
    *,
    max_database_bytes: int,
    recent_window: int,
    collect_older_than: float,
) -> Plan:
    """Decide what stays and write nothing. The same state gives the same bytes."""
    starting = roots(conn, state_dir, window=recent_window)
    reached, absent_roots = reachable(conn, starting)
    generations = _generation_rows(conn, reached, archived_generations(conn))
    files, unknown_files, missing = _file_rows(
        conn, state_dir, collect_older_than=collect_older_than
    )
    measured = _payload_bytes(conn, set(reached))
    shared = interned(conn)
    stored = "derivation_row_refs" if shared else "derivation_rows"
    unknown = {
        "orphan_payloads": list(measured.orphan_payloads),
        # Bytes a reference names but no label owns. The plan cannot say whose
        # they are, so they are reported and never counted as archivable.
        "unowned_payloads": list(measured.unowned_payloads),
        "orphan_row_references": sorted(
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT generation_id FROM {stored} r WHERE NOT EXISTS("
                "SELECT 1 FROM derivation_generations g WHERE g.generation_id=r.generation_id)"
            )
        ),
        "undeclared_files": unknown_files,
        "missing_declared_references": _missing_declared_references(conn, state_dir),
        "missing_root_generations": sorted(absent_roots),
    }
    content: dict[str, Any] = {
        "format": PLAN_FORMAT,
        # Whether schema 32 has run. Before it there are no payload digests to
        # name, so `orphan_payloads` and `unowned_payloads` are empty because
        # nothing can be one, not because nothing is wrong, and no payload is
        # ever eligible for removal. The plan says which of the two it is rather
        # than leaving a reader to guess from empty lists.
        "payloads_interned": shared,
        "knobs": {
            "max_database_bytes": max_database_bytes,
            "recent_window": recent_window,
            "collect_older_than": collect_older_than,
        },
        "roots": [
            {"kind": root.kind, "detail": root.detail, "generations": list(root.generations)}
            for root in sorted(starting, key=lambda item: (item.kind, item.detail))
        ],
        "generations": generations,
        "files": files,
        "missing_artifacts": missing,
        "unknown": unknown,
        "totals": _totals(generations, reached, unknown, files, measured),
    }
    return Plan({**content, "digest": _digest(content)})


def _generation_rows(
    conn: sqlite3.Connection,
    reached: Mapping[str, frozenset[str]],
    archived: frozenset[str],
) -> list[dict[str, Any]]:
    if interned(conn):
        references = {
            str(row[0]): (int(row[1]), int(row[2]))
            for row in conn.execute(
                "SELECT generation_id,count(*),count(DISTINCT payload_sha256) "
                "FROM derivation_row_refs GROUP BY generation_id"
            )
        }
        payload_bytes = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT r.generation_id,coalesce(sum(octet_length(p.payload_json)),0) FROM "
                "(SELECT DISTINCT generation_id,payload_sha256 FROM derivation_row_refs) r "
                "JOIN derivation_payloads p ON p.payload_sha256=r.payload_sha256 "
                "GROUP BY r.generation_id"
            )
        }
    else:
        # Before interning every row is its own payload, so a generation names
        # as many payloads as it has rows and owns all of their bytes outright.
        references = {}
        payload_bytes = {}
        for row in conn.execute(
            "SELECT generation_id,count(*),coalesce(sum(octet_length(payload_json)),0) "
            "FROM derivation_rows GROUP BY generation_id"
        ):
            references[str(row[0])] = (int(row[1]), int(row[1]))
            payload_bytes[str(row[0])] = int(row[2])
    rows = []
    for generation_id, stage, unit_kind, unit_id, row_count in conn.execute(
        "SELECT generation_id,stage,unit_kind,unit_id,row_count FROM derivation_generations "
        "ORDER BY generation_id"
    ):
        identifier = str(generation_id)
        reference_count, payload_count = references.get(identifier, (0, 0))
        reasons = sorted(reached.get(identifier, ()))
        rows.append(
            {
                "generation_id": identifier,
                "scope": [str(stage), str(unit_kind), str(unit_id)],
                "row_count": int(row_count),
                "reference_count": reference_count,
                "payload_count": payload_count,
                "payload_bytes": payload_bytes.get(identifier, 0),
                "list": "local" if reasons else "archivable",
                "why": reasons,
                # Whether step 4 has put these bytes somewhere else. Only an
                # archivable generation with this flag may lose its bytes, and
                # the flag is in the plan so the digest covers it.
                "archived": identifier in archived,
            }
        )
    return rows


def _file_rows(
    conn: sqlite3.Connection, state_dir: Path, *, collect_older_than: float
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Every artifact file and candidate directory, with the list it is on.

    A file nothing declares is unknown, and unknown is never eligible. A
    candidate directory is different: the collector already keeps the baseline,
    every pending candidate and the five newest built ones, and drops the rest,
    so those are named `removable` for the apply step to act on.

    A removable candidate carries `eligible_after`: its own modification time
    plus the collector's age floor. That floor is the only thing standing
    between a build that is still writing its candidate and a removal, so the
    plan has to carry it, and apply compares it against its own clock. It comes
    from the directory and not from the clock, so an unchanged state still
    plans to the same bytes.
    """
    candidates = referenced_candidates(state_dir)
    included, absent = _artifact_closure(state_dir, conn, candidates, require_present=False)
    missing = sorted(path.relative_to(state_dir).as_posix() for path in absent)
    held = hold_artifacts(state_dir)
    rows: list[dict[str, Any]] = []
    unknown: list[str] = []
    for directory in ARTIFACT_DIRECTORIES:
        root = state_dir / directory
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(state_dir).as_posix()
            if path.resolve() in included:
                listed, why = "local", "hold" if path.name in held else "declared"
            else:
                listed, why = "unknown", "nothing_declares_it"
                unknown.append(relative)
            rows.append(
                {"path": relative, "list": listed, "why": why, "bytes": path.stat().st_size}
            )
    retained = {path.resolve() for path in disposable_candidates(state_dir)[:5]} | candidates
    candidate_root = state_dir / "candidates"
    if candidate_root.is_dir():
        for path in sorted(candidate_root.iterdir()):
            resolved = path.resolve()
            if resolved in candidates:
                rows.append(
                    {
                        "path": _relative(state_dir, path),
                        "list": "local",
                        "why": "referenced_candidate",
                    }
                )
            elif resolved in retained:
                rows.append(
                    {"path": _relative(state_dir, path), "list": "local", "why": "recent_candidate"}
                )
            else:
                rows.append(
                    {
                        "path": _relative(state_dir, path),
                        "list": "removable",
                        # A candidate with no BUILT marker is not a superseded
                        # release; it is a build that never finished, or one
                        # that is being written right now.
                        "why": "superseded_candidate"
                        if (path / "BUILT").is_file()
                        else "unbuilt_candidate",
                        "eligible_after": path.stat().st_mtime + collect_older_than,
                    }
                )
    return rows, unknown, missing


def _relative(state_dir: Path, path: Path) -> str:
    return path.relative_to(state_dir).as_posix()


def _totals(
    generations: Sequence[Mapping[str, Any]],
    reached: Mapping[str, frozenset[str]],
    unknown: Mapping[str, Sequence[str]],
    files: Sequence[Mapping[str, Any]],
    measured: PayloadBytes,
) -> dict[str, Any]:
    local = [row for row in generations if row["list"] == "local"]
    archivable = [row for row in generations if row["list"] == "archivable"]
    by_reason: dict[str, int] = {}
    for reasons in reached.values():
        for reason in reasons:
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "local_generations": len(local),
        "archivable_generations": len(archivable),
        # Archivable generations whose bytes step 4 has already put somewhere
        # else. Only these may lose their payload bytes, and it is zero until
        # step 4 exists.
        "archived_generations": sum(1 for row in archivable if row["archived"]),
        "local_payload_bytes": measured.local,
        "archivable_payload_bytes": measured.archivable,
        "unknown_payloads": len(measured.unknown_payloads),
        "unknown_payload_bytes": measured.unknown,
        "unknown_files": len(unknown["undeclared_files"]),
        "unknown_file_bytes": sum(
            int(row.get("bytes", 0)) for row in files if row["list"] == "unknown"
        ),
        "unknown_row_reference_generations": len(unknown["orphan_row_references"]),
        "local_files": sum(1 for row in files if row["list"] == "local"),
        "local_file_bytes": sum(
            int(row.get("bytes", 0)) for row in files if row["list"] == "local"
        ),
        "local_generations_by_root": dict(sorted(by_reason.items())),
    }


@dataclass(frozen=True)
class PayloadBytes:
    """Payload bytes split three ways, by what owns each distinct payload."""

    local: int
    archivable: int
    unknown: int
    #: Payloads no row reference names at all.
    orphan_payloads: tuple[str, ...]
    #: Payloads every naming reference of which belongs to a generation with no label.
    unowned_payloads: tuple[str, ...]

    @property
    def unknown_payloads(self) -> tuple[str, ...]:
        return tuple(sorted({*self.orphan_payloads, *self.unowned_payloads}))


def _payload_bytes(conn: sqlite3.Connection, local: set[str]) -> PayloadBytes:
    """Split every distinct payload's bytes into local, archivable and unknown.

    One pass, grouped by payload, so neither the owners of a payload nor the set
    of payloads is ever held whole. Bytes are counted with `octet_length`: a
    payload is stored as text and `length` would count characters, so an
    accented name would be under-counted by the bytes its accents cost.

    A payload is local when any generation that names it is on the local list,
    because archiving the others would free nothing. It is unknown when no
    reference names it at all, or when every reference that names it belongs to
    a generation with no label: the plan cannot say who owns those bytes, so
    nothing may treat them as bytes archiving would give back. Everything else
    is archivable, and that number is what it says it is: bytes that would leave
    the file.

    Before schema 32 there is nothing to share: every row holds its own bytes
    and belongs to exactly one generation, so the same three numbers come from
    one pass over the rows and no payload can be named, orphaned or shared. The
    plan records which of the two shapes it measured.
    """
    if not interned(conn):
        return _row_bytes(conn, local)
    local_bytes = 0
    archivable = 0
    unknown_bytes = 0
    unowned: list[str] = []
    current: str | None = None
    size = 0
    owners_local = False
    owners_labelled = False

    def close() -> None:
        nonlocal local_bytes, archivable, unknown_bytes
        if current is None:
            return
        if owners_local:
            local_bytes += size
        elif not owners_labelled:
            unknown_bytes += size
            unowned.append(current)
        else:
            archivable += size

    for payload_sha256, generation_id, length, labelled in conn.execute(
        "SELECT r.payload_sha256,r.generation_id,octet_length(p.payload_json),"
        "g.generation_id IS NOT NULL FROM "
        "(SELECT DISTINCT payload_sha256,generation_id FROM derivation_row_refs) r "
        "JOIN derivation_payloads p ON p.payload_sha256=r.payload_sha256 "
        "LEFT JOIN derivation_generations g ON g.generation_id=r.generation_id "
        "ORDER BY r.payload_sha256"
    ):
        if payload_sha256 != current:
            close()
            current, size = str(payload_sha256), int(length)
            owners_local = owners_labelled = False
        owners_local = owners_local or str(generation_id) in local
        owners_labelled = owners_labelled or bool(labelled)
    close()
    orphans = [
        str(row[0])
        for row in conn.execute(
            "SELECT payload_sha256 FROM derivation_payloads p WHERE NOT EXISTS("
            "SELECT 1 FROM derivation_row_refs r WHERE r.payload_sha256=p.payload_sha256)"
        )
    ]
    unknown_bytes += int(
        conn.execute(
            "SELECT coalesce(sum(octet_length(payload_json)),0) FROM derivation_payloads p "
            "WHERE NOT EXISTS(SELECT 1 FROM derivation_row_refs r "
            "WHERE r.payload_sha256=p.payload_sha256)"
        ).fetchone()[0]
    )
    return PayloadBytes(
        local_bytes, archivable, unknown_bytes, tuple(sorted(orphans)), tuple(sorted(unowned))
    )


def _row_bytes(conn: sqlite3.Connection, local: set[str]) -> PayloadBytes:
    """The same three numbers on a database whose rows are not interned yet.

    A row's bytes belong to its own generation, so the split is by generation:
    on the local list, with a label but not reached, or with no label at all.
    Nothing can be an orphan payload, because bytes only exist inside a row, and
    nothing can be a named unowned payload, because there are no digests to
    name; those rows are reported by generation under `orphan_row_references`.
    """
    local_bytes = 0
    archivable = 0
    unknown_bytes = 0
    for generation_id, labelled, total in conn.execute(
        "SELECT r.generation_id,g.generation_id IS NOT NULL,"
        "coalesce(sum(octet_length(r.payload_json)),0) FROM derivation_rows r "
        "LEFT JOIN derivation_generations g ON g.generation_id=r.generation_id "
        "GROUP BY r.generation_id"
    ):
        if str(generation_id) in local:
            local_bytes += int(total)
        elif not bool(labelled):
            unknown_bytes += int(total)
        else:
            archivable += int(total)
    return PayloadBytes(local_bytes, archivable, unknown_bytes, (), ())


#: How many written plans are kept. A plan names every artifact file, so on a
#: large state directory each one is megabytes, and every change of state gives
#: a new digest and a new file. Keeping the newest few leaves the plan an
#: operator or `gc --apply` just asked for while stopping the plans directory
#: growing without bound inside the very state the size cap defends.
KEPT_PLANS = 10


def write_plan(state_dir: Path, value: Plan) -> Path:
    """Write one plan under the state directory, named by its own digest.

    Writing the same plan twice writes nothing the second time, so the file
    keeps its first modification time and an unchanged state keeps one plan.
    """
    path = state_dir / "gc" / "plans" / f"{value.digest}.json"
    if not path.is_file():
        durable_write(path, value.bytes())
        _prune_plans(path.parent, keep=KEPT_PLANS)
    return path


def _prune_plans(directory: Path, *, keep: int) -> list[Path]:
    """Remove all but the newest `keep` written plans. Never touches anything else."""
    written = sorted(
        (item for item in directory.glob("*.json") if item.is_file()),
        key=lambda item: (item.stat().st_mtime, item.name),
        reverse=True,
    )
    removed = []
    for item in written[keep:]:
        item.unlink()
        removed.append(item)
    if removed:
        fsync_dir(directory)
    return removed


# ---------------------------------------------------------------------------
# Reporting and the size cap
# ---------------------------------------------------------------------------


def usage(conn: sqlite3.Connection, state_dir: Path) -> dict[str, Any]:
    """Bytes in use, file size, write-ahead log size and free-list bytes.

    Deleting rows returns pages to SQLite's free list and leaves the file the
    size it was, so a cap measured on the file needs all four numbers to be
    read honestly. `dbstat` is a compile-time option; when it is missing the
    bytes in use are reported as unknown rather than guessed.
    """
    database = state_dir / "state.sqlite"
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    try:
        row = conn.execute("SELECT coalesce(sum(pgsize),0) FROM dbstat").fetchone()
        in_use: int | None = int(row[0])
    except sqlite3.Error:
        in_use = None
    return {
        "bytes_in_use": in_use,
        "file_bytes": database.stat().st_size if database.is_file() else 0,
        "wal_bytes": (state_dir / "state.sqlite-wal").stat().st_size
        if (state_dir / "state.sqlite-wal").is_file()
        else 0,
        "free_list_bytes": page_size * free_pages,
    }


def report(
    conn: sqlite3.Connection,
    state_dir: Path,
    *,
    max_database_bytes: int,
    recent_window: int,
    collect_older_than: float,
    detail_limit: int = 20,
) -> dict[str, Any]:
    """The planner in report mode: the same walk, nothing written.

    An unreadable dependency manifest stops the planner. Doctor must still
    report the rest of the state, so the failure is reported here rather than
    raised at the operator.
    """
    measured = usage(conn, state_dir)
    result: dict[str, Any] = {
        "knobs": {
            "max_database_bytes": max_database_bytes,
            "recent_window": recent_window,
            "collect_older_than": collect_older_than,
        },
        "usage": measured,
        "over_size_cap": measured["file_bytes"] > max_database_bytes,
    }
    try:
        result["holds"] = [
            {key: record[key] for key in ("hold_id", "who", "why", "created_at")}
            for record in holds(state_dir)
        ]
        value = plan(
            conn,
            state_dir,
            max_database_bytes=max_database_bytes,
            recent_window=recent_window,
            collect_older_than=collect_older_than,
        )
    except RetentionError as exc:
        result["error"] = str(exc)
        return result
    content = value.content
    local = [row for row in content["generations"] if row["list"] == "local"]
    result["plan_digest"] = value.digest
    # Doctor says so plainly: before schema 32 no payload digest exists, so the
    # unknown-payload lists below are empty for that reason and no `gc --apply`
    # can remove row data.
    result["payloads_interned"] = content["payloads_interned"]
    result["totals"] = content["totals"]
    result["unknown"] = {key: len(items) for key, items in content["unknown"].items()}
    # Counts say there is something to work out; the items say what. Nothing
    # removes an unknown item, so somebody has to go and look at it, and an
    # operator should not have to write a plan file out to see which digest or
    # which path it is. Long lists are cut to the same limit as the rest of the
    # report, and the counts above stay whole.
    result["unknown_items"] = {
        key: list(items[:detail_limit]) for key, items in content["unknown"].items() if items
    }
    result["missing_artifacts"] = content["missing_artifacts"][:detail_limit]
    result["local_generations"] = [
        {
            "generation_id": row["generation_id"],
            "scope": row["scope"],
            "why": row["why"],
            "payload_bytes": row["payload_bytes"],
        }
        for row in sorted(local, key=lambda row: -int(row["payload_bytes"]))[:detail_limit]
    ]
    result["local_generations_truncated"] = max(0, len(local) - detail_limit)
    return result


def enforce_size_cap(
    state_dir: Path,
    *,
    max_database_bytes: int,
    now: datetime,
    actor: str = "swingset",
    timeout: float = 60,
) -> dict[str, Any]:
    """Pause the pipeline when the live database file is over its cap.

    Reading, controls and recovery do not go through admission, so they keep
    working while this pause stands. An existing whole-pipeline pause is left
    alone, whatever its reason: repeating this every cycle must never rewrite an
    operator's own words, and the cap pause itself is written once. A restore in
    progress owns the state directory, so nothing is written during one.

    The pause comes back on the next cycle while the file is still over the cap,
    because that is what the cap is for. Shrinking the file takes two commands:
    `gc --apply` removes what a plan named, and `gc --reclaim` rewrites the file
    so the pages that freed stop counting. Raising
    `retention.max_database_bytes` is still the way to run again when there is
    nothing to remove. The result says both rather than leaving the operator to
    find out by resuming twice.
    """
    from .controls import Selector, _at, change_control

    database = state_dir / "state.sqlite"
    size = database.stat().st_size if database.is_file() else 0
    result: dict[str, Any] = {
        "file_bytes": size,
        "max_database_bytes": max_database_bytes,
        "over_cap": size > max_database_bytes,
        "paused": False,
    }
    if size <= max_database_bytes or (state_dir / "RESTORE_PENDING").is_file():
        return result
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        if "operator_pauses" not in _tables(connection):
            return result
        # A timed pause whose moment has passed holds nothing: `matching_pauses`
        # skips it, and only a change_control or admission boundary deletes the
        # row. Reading it as an existing pause would leave the pipeline running
        # over the cap with nothing paused, so this asks the same question
        # controls asks, in the format controls writes.
        existing = connection.execute(
            "SELECT reason FROM operator_pauses WHERE scope_kind='all' AND scope_id='all' "
            "AND (until_at IS NULL OR until_at>?)",
            (_at(now),),
        ).fetchone()
    finally:
        connection.close()
    if existing is not None:
        result["existing_pause_reason"] = str(existing[0])
        return result
    change_control(
        state_dir,
        selector=Selector("all", "all"),
        paused=True,
        actor=actor,
        reason=SIZE_CAP_REASON,
        now=now,
        timeout=timeout,
    )
    result["paused"] = True
    result["reason"] = SIZE_CAP_REASON
    result["remedy"] = SIZE_CAP_REMEDY
    return result
