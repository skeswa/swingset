"""Move derivation output out of the file and into packs (architecture phase 4, WP3).

A generation's output is its label stream, and `output_packs.pack` writes that
stream as a `derivation_output` object named by the label's `output_digest`.
Once the pack is written and verified, the generation's row references and the
payloads nothing else names can leave the file. A `derivation_evicted` marker
says they left, and `output_packs.rows` then reads the pack (F32, F33).

**Hot and evictable.** Current pointers (`derivation_scopes`) and building
generations (`derivation_building`) stay resident: the partitioned build reads
them through SQL joins (F34). A generation that is not complete (its references
are short or a payload is missing) stays too; the collector deals with it
(WP5). Every other generation is evictable.

Three entry points:

- `plan` decides and writes nothing to the database. It streams the sorted
  evictable `(generation_id, output_digest)` list into a sha256, with the kept
  and already evicted sets beside it, so the same state gives the same digest
  and memory stays bounded. The lists live in temporary tables under
  `<state>/tmp`, never in Python.
- `apply` acts on one plan digest under `operator-hold`, the writer lock and
  then the control lock: recompute (stop if the digest moved), note, pack every
  distinct output, verify every pack, then one exclusive transaction that
  rebuilds `derivation_row_refs` with only the kept references, marks the rest
  evicted and deletes the payloads nothing names. A crash before that
  transaction commits leaves only extra packs; one inside it rolls back.
- `evict` is the maintain loop's bounded version: a few generations a tick, at
  most `CHUNK_ROWS` references per transaction.

Parse and build-recipe bodies of unprotected rows (`state/body_packs.py`) are
two more stages of the same plan and apply: pack every target row's body,
verify every object, then stub the rows, `STUB_ROWS` source rows or one build
label per transaction, before the output move. A plan names the target rows
whether or not they are already packed, so a resume keeps its digest.
`pack_bodies` is the maintain loop's bounded version.

The move retains every index and trigger. Row guards require the transaction's
storage eviction authority, and the history dispatch fence advances through
its existing row triggers. A crash rolls back the move and its receipt.

"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, durable_write
from swingset.state import engine

from . import body_packs, disk, guards, objects, output_packs
from .control_lock import control_lock
from .db import SCHEMA_VERSION, Database
from .derivations import CHUNK_ROWS
from .objects import ObjectUnavailable, Store
from .publication_fence import PUBLICATION_KINDS, kinds_sql
from .retention_apply import receipt_directory, temporary_directory

PLAN_FORMAT = "storage-plan-v1"
RECEIPT_FORMAT = "storage-apply-receipt-v1"
#: The stages this tree plans and applies, in order.
STAGES = ("derivation_output", "parse_body", "label_body")
#: Stages of phase 4 that later work packages add.
PENDING_STAGES: tuple[str, ...] = ()
#: Source rows stubbed per transaction.
STUB_ROWS = 1000
#: The incremental evictor leaves a generation alone until it is this old.
MIN_AGE = timedelta(days=1)
#: Distinct packs read per query page while packing and verifying.
PAGE = 500

_TABLE = "derivation_row_refs"
_REASON = "storage apply "
_HOLD = "operator-hold"


class StorageError(RuntimeError):
    """A storage plan or apply refused, and nothing was moved."""


@dataclass(frozen=True)
class Plan:
    """The digested part of a plan and, when asked for, its estimate."""

    content: dict[str, Any]
    estimate: dict[str, Any] | None = None

    @property
    def digest(self) -> str:
        return str(self.content["digest"])

    def document(self) -> dict[str, Any]:
        return {**self.content, "estimate": self.estimate}


def _stage(name: str) -> None:
    """A seam between the apply's stages; tests replace it to kill the process."""


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def _require(conn: engine.Connection) -> None:
    if not output_packs.available(conn):
        raise StorageError("storage needs schema 36; run `swingset migrate` first")


def _drop_temporary(conn: engine.Connection) -> None:
    if conn.closed:
        return
    for name in (
        "storage_evict",
        "storage_keep",
        "storage_kept_refs",
        "storage_kept_payloads",
        "storage_bodies",
        "storage_labels",
        "storage_body_digests",
    ):
        conn.execute(f"DROP TABLE IF EXISTS pg_temp.{name}")
    body_packs.drop_temporary(conn)


def _hot(conn: engine.Connection) -> Iterator[tuple[str, str]]:
    """Hot generations `(generation_id, why)`, sorted, one row per generation."""
    for generation_id, pointer in conn.execute(
        "SELECT generation_id, MAX(pointer) AS _value_1 FROM (SELECT materialized_generation_id AS generation_id, 1 AS pointer FROM derivation_scopes WHERE NOT materialized_generation_id IS NULL UNION ALL SELECT generation_id, 0 AS _value_1 FROM derivation_building) GROUP BY generation_id ORDER BY generation_id NULLS FIRST"
    ):
        yield str(generation_id), "pointer" if pointer else "building"


def hot_digest(conn: engine.Connection) -> str:
    hasher = hashlib.sha256()
    for generation_id, why in _hot(conn):
        hasher.update(f"{generation_id}\t{why}\n".encode())
    return hasher.hexdigest()


def _groups(conn: engine.Connection) -> Iterator[tuple[str, int, int, int]]:
    """Per generation with references: (id, count, max ordinal + 1, missing payloads)."""
    for generation_id, count, top, missing in conn.execute(
        "SELECT r.generation_id, COUNT(*) AS _value_1, MAX(r.ordinal) + 1 AS _value_2, COUNT(*) - COUNT(p.payload_sha256) AS _value_3 FROM derivation_row_refs AS r LEFT JOIN derivation_payloads AS p ON p.payload_sha256 = r.payload_sha256 GROUP BY r.generation_id ORDER BY r.generation_id NULLS FIRST"
    ):
        yield str(generation_id), int(count), int(top), int(missing)


def _labels(conn: engine.Connection) -> Iterator[tuple[str, str, int, bool, str | None]]:
    """Every label, sorted: (id, output digest, rows, evicted, hot reason or None)."""
    for generation_id, output_digest, row_count, evicted, pointer, building in conn.execute(
        "SELECT g.generation_id, g.output_digest, g.row_count, CAST(EXISTS(SELECT 1 AS _value_0 FROM derivation_evicted AS e WHERE e.generation_id = g.generation_id) AS INTEGER) AS _value_3, CAST(EXISTS(SELECT 1 AS _value_0 FROM derivation_scopes AS s WHERE s.materialized_generation_id = g.generation_id) AS INTEGER) AS _value_4, CAST(EXISTS(SELECT 1 AS _value_0 FROM derivation_building AS b WHERE b.generation_id = g.generation_id) AS INTEGER) AS _value_5 FROM derivation_generations AS g ORDER BY g.generation_id NULLS FIRST"
    ):
        hot = "pointer" if pointer else "building" if building else None
        yield str(generation_id), str(output_digest), int(row_count), bool(evicted), hot


def _classify(conn: engine.Connection) -> dict[str, Any]:
    """Stream labels and reference groups once, merged by id; fill the temp tables.

    `pg_temp.storage_evict` gets every evictable generation and `pg_temp.storage_keep`
    every other generation that has references in the file, with whether it is
    resident, so the move can copy exactly those back. Runs in one read
    transaction, so both streams see the same state.
    """
    _drop_temporary(conn)
    conn.execute(
        "CREATE TEMPORARY TABLE storage_evict (generation_id TEXT PRIMARY KEY, output_digest TEXT "
        "NOT NULL, row_count INT NOT NULL)"
    )
    conn.execute(
        "CREATE TEMPORARY TABLE storage_keep (generation_id TEXT PRIMARY KEY, why TEXT NOT NULL, "
        "refs INT NOT NULL, resident INT NOT NULL)"
    )
    evict_hash, keep_hash, evicted_hash = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    counts = {"pointer": 0, "building": 0, "partial": 0, "unlabelled": 0}
    evict_count = evict_rows = kept_refs = evicted_count = evicted_with_refs = 0
    evict_batch: list[tuple[str, str, int]] = []
    keep_batch: list[tuple[str, str, int, int]] = []

    def flush() -> None:
        conn.executemany("INSERT INTO pg_temp.storage_evict VALUES (%s, %s, %s)", evict_batch)
        conn.executemany("INSERT INTO pg_temp.storage_keep VALUES (%s, %s, %s, %s)", keep_batch)
        evict_batch.clear()
        keep_batch.clear()

    def keep(generation_id: str, why: str, refs: int, resident: bool) -> None:
        nonlocal kept_refs
        counts[why] += 1
        keep_hash.update(f"{generation_id}\t{why}\n".encode())
        if refs:
            kept_refs += refs
            keep_batch.append((generation_id, why, refs, int(resident)))

    conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
    try:
        groups = _groups(conn)
        group = next(groups, None)
        for generation_id, output_digest, row_count, evicted, hot in _labels(conn):
            # References with no label sort before this one: keep them as they are.
            while group is not None and group[0] < generation_id:
                keep(group[0], "unlabelled", group[1], False)
                group = next(groups, None)
            count = top = missing = 0
            if group is not None and group[0] == generation_id:
                _, count, top, missing = group
                group = next(groups, None)
            resident = count == row_count and top == row_count and not missing
            if evicted:
                evicted_count += 1
                evicted_hash.update(f"{generation_id}\n".encode())
                if count:
                    # An incremental eviction stopped part way; its prefix stays.
                    evicted_with_refs += 1
                    keep_batch.append((generation_id, "evicted", count, 0))
                    kept_refs += count
            elif hot is not None:
                keep(generation_id, hot, count, resident)
            elif not resident:
                keep(generation_id, "partial", count, False)
            else:
                evict_count += 1
                evict_rows += row_count
                evict_hash.update(f"{generation_id}\t{output_digest}\n".encode())
                evict_batch.append((generation_id, output_digest, row_count))
            if len(evict_batch) + len(keep_batch) >= 1000:
                flush()
        while group is not None:
            keep(group[0], "unlabelled", group[1], False)
            group = next(groups, None)
        flush()
        packs = int(
            conn.execute(
                "SELECT COUNT(DISTINCT output_digest) AS _value_0 FROM pg_temp.storage_evict"
            ).fetchone()[0]
        )
        hot = hot_digest(conn)
        conn.commit()
    except BaseException:
        if not conn.closed:
            with contextlib.suppress(engine.Error):
                conn.rollback()
        _drop_temporary(conn)
        raise
    return {
        "hot_digest": hot,
        "evict": {
            "generations": evict_count,
            "rows": evict_rows,
            "packs": packs,
            "digest": evict_hash.hexdigest(),
        },
        "keep": {
            **counts,
            "references": kept_refs,
            "digest": keep_hash.hexdigest(),
        },
        "evicted": {
            "generations": evicted_count,
            "with_references": evicted_with_refs,
            "digest": evicted_hash.hexdigest(),
        },
    }


def retained(conn: engine.Connection, state_dir: Path) -> tuple[list[str], list[str]]:
    """Source generations retained closure-mode candidates' closures name, and the
    build generations whose output names a retained candidate.

    A closure-mode candidate names its private proof by digest. Every text
    `generation_id` anywhere in the proof that is a source generation counts:
    `source_support` names most, and `event_coverage` names enumeration
    parents that `closure.validate` decodes again (F35). A proof that is not
    in the database leaves them unknown, so the plan refuses.
    """
    from .retention import RetentionError, _candidate_policy, referenced_candidates

    sources: set[str] = set()
    names: list[str] = []
    try:
        candidates = sorted(referenced_candidates(state_dir))
        for candidate in candidates:
            names.append(candidate.name)
            policy = _candidate_policy(candidate)
            if policy.get("mode") != "closure":
                continue
            closure = policy.get("closure")
            private = closure.get("private_digest") if isinstance(closure, dict) else None
            if not isinstance(private, str) or not private:
                raise StorageError(f"candidate {candidate.name} names no closure proof")
            if (
                conn.execute(
                    "SELECT 1 AS _value_0 FROM derivation_dependency_sets WHERE dependency_set_id = %s",
                    (private,),
                ).fetchone()
                is None
            ):
                raise StorageError(
                    f"closure proof {private} of candidate {candidate.name} is not in the "
                    "database, so the source generations it names are unknown"
                )
            sources.update(
                str(row[0])
                for row in conn.execute(
                    (
                        "SELECT DISTINCT j.value #>> '{}' FROM derivation_dependency_sets d "
                        "CROSS JOIN LATERAL jsonb_path_query(d.manifest_json::jsonb, "
                        "'lax $.**.generation_id ? (@.type() == \"string\")') j(value) "
                        "WHERE d.dependency_set_id=%s AND EXISTS(SELECT 1 FROM source_generations g "
                        "WHERE g.generation_id=j.value #>> '{}')"
                    ),
                    (private,),
                )
            )
    except RetentionError as error:
        raise StorageError(str(error)) from error
    builds: list[str] = []
    if names:
        for (generation_id,) in conn.execute(
            "SELECT generation_id FROM derivation_generations WHERE stage = 'build' ORDER BY "
            "generation_id NULLS FIRST"
        ).fetchall():
            for name in names:
                try:
                    found = output_packs.row(conn, str(generation_id), "artifact", name)
                except output_packs.GenerationUnavailable:
                    found = None
                if found is not None:
                    builds.append(str(generation_id))
                    break
    return sorted(sources), builds


def _protected_counts(conn: engine.Connection, table: str) -> dict[str, int]:
    return {
        str(why): int(count)
        for why, count in conn.execute(
            f"SELECT why,count(*) FROM pg_temp.{table} GROUP BY why ORDER BY why"
        )
    }


def _targets(
    conn: engine.Connection, table: str, rows: Iterator[tuple[str, bool]]
) -> dict[str, Any]:
    """Stream target ids into a sha256 and `pg_temp.<table>`; packed ones count apart."""
    conn.execute(
        f"CREATE TEMP TABLE {table}(generation_id TEXT PRIMARY KEY, packed INTEGER NOT NULL)"
    )
    hasher = hashlib.sha256()
    count = 0
    batch: list[tuple[str, int]] = []
    for generation_id, packed in rows:
        count += 1
        hasher.update(f"{generation_id}\n".encode())
        batch.append((generation_id, int(packed)))
        if len(batch) >= 1000:
            conn.executemany(f"INSERT INTO pg_temp.{table} VALUES (%s,%s)", batch)
            batch.clear()
    conn.executemany(f"INSERT INTO pg_temp.{table} VALUES (%s,%s)", batch)
    return {"generations": count, "digest": hasher.hexdigest()}


def _classify_bodies(conn: engine.Connection, state_dir: Path, before: str) -> dict[str, Any]:
    """The parse and label stages' targets, in one read transaction (temp tables only)."""
    conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
    try:
        sources, builds = retained(conn, state_dir)
        body_packs.protect_sources(conn, before=before, candidates=sources)
        parse = {
            "before": before,
            "protected": _protected_counts(conn, "body_protected"),
            **_targets(conn, "storage_bodies", body_packs.source_targets(conn)),
        }
        body_packs.protect_labels(conn, before=before, candidates=builds)
        label = {
            "before": before,
            "min_bytes": body_packs.LABEL_MIN_BYTES,
            "protected": _protected_counts(conn, "label_protected"),
            **_targets(conn, "storage_labels", body_packs.label_targets(conn)),
        }
        conn.commit()
    except BaseException:
        if not conn.closed:
            with contextlib.suppress(engine.Error):
                conn.rollback()
        raise
    return {"parse_body": parse, "label_body": label}


def _compute(conn: engine.Connection, state_dir: Path, before: str) -> dict[str, Any]:
    """The plan's content; `before` is the bodies' `young` boundary."""
    _require(conn)
    classified = _classify(conn)
    bodies = _classify_bodies(conn, state_dir, before)
    content: dict[str, Any] = {
        "format": PLAN_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "stages": list(STAGES),
        "pending_stages": list(PENDING_STAGES),
        **classified,
        **bodies,
    }
    return {**content, "digest": hashlib.sha256(canonical_json(content)).hexdigest()}


def _btree_bytes(conn: engine.Connection, table: str) -> int | None:
    """Allocated table, TOAST and index bytes from the native catalog."""
    row = conn.execute("SELECT pg_total_relation_size(to_regclass(%s))", (table,)).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def _estimate(conn: engine.Connection, content: dict[str, Any]) -> dict[str, Any]:
    """What the apply would free and write. Outside the digest: it reads page sizes."""
    total_refs = int(conn.execute(f"SELECT count(*) FROM {_TABLE}").fetchone()[0])
    moved_refs = total_refs - int(content["keep"]["references"])
    ref_bytes = _btree_bytes(conn, _TABLE)
    conn.execute("CREATE TEMPORARY TABLE storage_kept_payloads (payload_sha256 TEXT PRIMARY KEY)")
    conn.execute(
        f"INSERT INTO pg_temp.storage_kept_payloads SELECT r.payload_sha256 FROM pg_temp.storage_keep k JOIN {_TABLE} r ON r.generation_id=k.generation_id ON CONFLICT DO NOTHING"
    )
    payloads, payload_bytes = conn.execute(
        "SELECT COUNT(*) AS _value_0, COALESCE(CAST(SUM(OCTET_LENGTH(payload_json)) AS BIGINT), 0) AS _value_1 FROM derivation_payloads WHERE NOT payload_sha256 IN (SELECT payload_sha256 FROM pg_temp.storage_kept_payloads)"
    ).fetchone()
    conn.execute("DROP TABLE pg_temp.storage_kept_payloads")
    missing = int(
        conn.execute(
            (
                "SELECT COUNT(*) AS _value_0 FROM (SELECT DISTINCT output_digest FROM pg_temp.storage_evict) AS d WHERE NOT EXISTS(SELECT 1 AS _value_0 FROM objects AS o WHERE o.class = %s AND o.digest = d.output_digest)"
            ),
            (output_packs.CLASS,),
        ).fetchone()[0]
    )
    bodies = conn.execute(
        "SELECT COUNT(*) AS _value_0, COALESCE(CAST(SUM(OCTET_LENGTH(g.manifest_json) + OCTET_LENGTH(g.report_json) + OCTET_LENGTH(g.result_json)) AS BIGINT), 0) AS _value_1 FROM pg_temp.storage_bodies AS t JOIN source_generations AS g USING (generation_id) WHERE t.packed=0"
    ).fetchone()
    labels = conn.execute(
        "SELECT COUNT(*) AS _value_0, COALESCE(CAST(SUM(OCTET_LENGTH(g.recipe_json)) AS BIGINT), 0) AS _value_1 FROM pg_temp.storage_labels AS t JOIN derivation_generations AS g USING (generation_id) WHERE t.packed=0"
    ).fetchone()
    return {
        "parse_bodies_to_pack": int(bodies[0]),
        "parse_body_bytes": int(bodies[1]),
        "label_bodies_to_pack": int(labels[0]),
        "label_body_bytes": int(labels[1]),
        "references": total_refs,
        "references_moved": moved_refs,
        "reference_bytes": ref_bytes,
        "reference_bytes_freed": (
            None if ref_bytes is None or not total_refs else ref_bytes * moved_refs // total_refs
        ),
        "payloads_removed": int(payloads),
        "payload_text_bytes_removed": int(payload_bytes),
        "packs_to_write": missing,
        "free_list_bytes": None,
    }


def plan(
    conn: engine.Connection,
    state_dir: Path,
    *,
    estimate: bool = True,
    now: datetime | None = None,
) -> Plan:
    """What `apply` would move. Moves nothing; the same state gives the same digest.

    `now` sets the bodies' `young` boundary, a whole UTC day (`body_packs.cutoff`).
    Apply recomputes with the boundary of the written plan
    (`gc/plans/storage-<digest>.json`), so a plan written late in a UTC day
    still applies after midnight.
    """
    moment = now if now is not None else datetime.now(UTC)
    with temporary_directory(conn, state_dir):
        try:
            content = _compute(conn, state_dir, body_packs.cutoff(moment))
            return Plan(content, _estimate(conn, content) if estimate else None)
        finally:
            _drop_temporary(conn)


def write_plan(state_dir: Path, value: Plan) -> Path:
    """`gc/plans/storage-<digest>.json`, written once."""
    path = state_dir / "gc" / "plans" / f"storage-{value.digest}.json"
    if not path.is_file():
        durable_write(path, canonical_json(value.document()))
    return path


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _note(conn: engine.Connection, plan_digest: str) -> engine.Row | None:
    found: engine.Row | None = conn.execute(
        (
            "SELECT storage_applies.plan_digest, storage_applies.kind, storage_applies.started_at, "
            "storage_applies.finished_at, storage_applies.receipt_json FROM storage_applies WHERE "
            "plan_digest = %s"
        ),
        (plan_digest,),
    ).fetchone()
    return found


def publication_active(conn: engine.Connection) -> bool:
    return (
        conn.execute(
            f"SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN {kinds_sql(PUBLICATION_KINDS)} LIMIT 1"
        ).fetchone()
        is not None
    )


def _write_receipt(state_dir: Path, plan_digest: str, receipt: dict[str, Any]) -> dict[str, Any]:
    path = receipt_directory(state_dir) / f"storage-{plan_digest}.json"
    if not path.is_file():
        durable_write(path, canonical_json(receipt))
    return receipt


#: Room the move leaves on the state's disk beyond what it writes.
MOVE_MARGIN_BYTES = 10_000_000_000


def _written_plan(state_dir: Path, plan_digest: str) -> dict[str, Any] | None:
    """The plan document `storage plan` wrote for this digest, if any."""
    path = state_dir / "gc" / "plans" / f"storage-{plan_digest}.json"
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("digest") == plan_digest else None


def _plan_before(written: dict[str, Any] | None, now: datetime) -> str:
    """The written plan's `young` boundary, else `now`'s. An older boundary
    only protects more rows, so a plan applied after midnight keeps its digest."""
    if written is not None:
        before = (written.get("parse_body") or {}).get("before")
        if isinstance(before, str) and before:
            return before
    return body_packs.cutoff(now)


def move_need(
    conn: engine.Connection, fresh: dict[str, Any], written: dict[str, Any] | None
) -> dict[str, Any]:
    """Conservative room for deleting reference tuples and recording their WAL.

    Both copies of the allocated relation count, plus the existing margin;
    this budget does not assume that dead tuples have already been reclaimed.
    """
    estimate = (written or {}).get("estimate") or {}
    reference_bytes = estimate.get("reference_bytes")
    if not isinstance(reference_bytes, int):
        reference_bytes = _btree_bytes(conn, _TABLE) or 0
    total = int(estimate.get("references") or 0) or int(
        conn.execute(f"SELECT count(*) FROM {_TABLE}").fetchone()[0]
    )
    kept = int(fresh["keep"]["references"])
    kept_bytes = reference_bytes * kept // total if total else 0
    secure = 0
    need = 2 * reference_bytes + MOVE_MARGIN_BYTES
    return {
        "reference_bytes": reference_bytes,
        "kept_reference_bytes": kept_bytes,
        "secure_delete": secure,
        "needed_bytes": need,
    }


def apply(
    database: Database, *, plan_digest: str, now: datetime, timeout: float = 60
) -> dict[str, Any]:
    """Pack, verify and move what the plan with this digest names; return the receipt.

    Rerunning an applied digest returns the recorded receipt and moves nothing.
    Rerunning one that stopped part way recomputes the plan, which is the same
    while nothing moved, and resumes: packs are content-addressed and kept.

    The move refuses before it starts when the disk, quota included
    (`state.disk`), has less room than the conservative `move_need` budget.
    """
    if not database.holds_writer_lock:
        raise StorageError("storage apply needs the writer lock; open the state for writing")
    state_dir = database.state_dir
    conn = database.connection
    _require(conn)
    if not (state_dir / _HOLD).is_file():
        raise StorageError(
            f"storage apply runs under {_HOLD}; create {state_dir / _HOLD} and wait until "
            "no loop is running. Nothing was moved"
        )
    if conn.in_transaction:
        raise StorageError("storage apply cannot run inside a transaction; nothing was moved")
    written = _written_plan(state_dir, plan_digest)
    with (
        control_lock(state_dir, connection=conn, timeout=timeout),
        temporary_directory(conn, state_dir),
        disk.fast_delete(conn),
    ):
        recorded = _note(conn, plan_digest)
        if recorded is not None and recorded["receipt_json"] is not None:
            return _write_receipt(
                state_dir, plan_digest, dict(json.loads(str(recorded["receipt_json"])))
            )
        seconds: dict[str, float] = {}
        try:
            started = time.monotonic()
            fresh = _compute(conn, state_dir, _plan_before(written, now))
            seconds["plan"] = round(time.monotonic() - started, 3)
            if fresh["digest"] != plan_digest:
                raise StorageError(
                    f"plan {plan_digest} is not the plan of this state, which is "
                    f"{fresh['digest']}; nothing was moved. Write a new plan with "
                    "`swingset storage plan` and apply that one"
                )
            if publication_active(conn):
                raise StorageError("a publication is active; nothing was moved")
            room = _room(conn, state_dir, fresh, written)
            started_at = objects.stamp(datetime.now(UTC))
            if recorded is None:
                with database.transaction() as txn:
                    txn.execute(
                        "INSERT INTO storage_applies (plan_digest, kind, started_at) VALUES (%s, 'storage', %s)",
                        (plan_digest, started_at),
                    )
            else:
                started_at = str(recorded["started_at"])
            _stage("noted")
            store = objects.store_for(conn)
            started = time.monotonic()
            packed = _pack_all(conn, store, now)
            seconds["pack"] = round(time.monotonic() - started, 3)
            _stage("packed")
            started = time.monotonic()
            verified = _verify_all(conn, store, now)
            seconds["verify"] = round(time.monotonic() - started, 3)
            _stage("verified")
            started = time.monotonic()
            bodies = _pack_bodies(database, store, now)
            seconds["bodies"] = round(time.monotonic() - started, 3)
            # Phase 3 manifest files into `release_manifest` objects (WP5).
            from .collector import move_manifests

            manifests = move_manifests(database, now=now)
            receipt = {
                "format": RECEIPT_FORMAT,
                "plan_digest": plan_digest,
                "started_at": started_at,
                "plan": {
                    key: fresh[key]
                    for key in ("evict", "keep", "evicted", "parse_body", "label_body")
                },
                "packs": {**packed, **verified},
                "bodies": bodies,
                "manifests": manifests,
                "room": room,
                "resumed": recorded is not None,
            }
            _move(conn, fresh, receipt, seconds, now=now)
        finally:
            _drop_temporary(conn)
    return _write_receipt(state_dir, plan_digest, receipt)


def _room(
    conn: engine.Connection,
    state_dir: Path,
    fresh: dict[str, Any],
    written: dict[str, Any] | None,
) -> dict[str, Any]:
    """Refuse before anything is written when the move cannot fit."""
    need = move_need(conn, fresh, written)
    found = disk.room(state_dir)
    measured = {**need, **found.document()}
    if found.quota_stale:
        raise StorageError(
            f"the disk quota reading is from {found.quota_read_at}, too old to trust; run "
            f"`{disk.REFRESH}` and apply again. Nothing was moved"
        )
    if found.free < need["needed_bytes"]:
        raise StorageError(
            f"the move needs about {need['needed_bytes']} free bytes on the state's disk "
            f"(quota included) and there are {found.free}; nothing was moved. Free space "
            "first (docs/guides/operation.md, packing cold output)"
        )
    return measured


def _evict_digests(conn: engine.Connection) -> Iterator[tuple[str, str, int]]:
    """Distinct output digests to evict, each with one generation and its row count."""
    after = ""
    while True:
        page = conn.execute(
            (
                "SELECT output_digest, MIN(generation_id) AS _value_1, MAX(row_count) AS _value_2 FROM pg_temp.storage_evict WHERE output_digest > %s GROUP BY output_digest ORDER BY output_digest NULLS FIRST LIMIT %s"
            ),
            (after, PAGE),
        ).fetchall()
        if not page:
            return
        for output_digest, generation_id, row_count in page:
            yield str(output_digest), str(generation_id), int(row_count)
        after = str(page[-1][0])


def _pack_all(conn: engine.Connection, store: Store, now: datetime) -> dict[str, int]:
    """One pack per distinct output; an indexed pack whose file is there is kept."""
    written = kept = raw_bytes = stored_bytes = 0
    for output_digest, generation_id, _ in _evict_digests(conn):
        present = conn.execute(
            "SELECT raw_bytes FROM objects WHERE class = %s AND digest = %s",
            (output_packs.CLASS, output_digest),
        ).fetchone()
        if present is not None and store.exists(output_packs.CLASS, output_digest):
            kept += 1
        else:
            if output_packs.pack(conn, store, generation_id, now=now) != output_digest:
                raise StorageError(f"pack of {generation_id} is not named by its label")
            written += 1
        row = conn.execute(
            "SELECT raw_bytes, stored_bytes FROM objects WHERE class = %s AND digest = %s",
            (output_packs.CLASS, output_digest),
        ).fetchone()
        raw_bytes += int(row[0])
        stored_bytes += int(row[1])
    return {"written": written, "kept": kept, "raw_bytes": raw_bytes, "stored_bytes": stored_bytes}


def verify_pack(store: Store, output_digest: str, row_count: int) -> None:
    """The pack hashes to its name and has the label's line count, or this raises."""
    lines = 0
    try:
        for _ in store.lines(output_packs.CLASS, output_digest):
            lines += 1
    except ObjectUnavailable as error:
        raise StorageError(str(error)) from error
    if lines != row_count:
        raise StorageError(
            f"{output_packs.CLASS}/{output_digest} has {lines} lines and its label {row_count}"
        )


def _verify_all(conn: engine.Connection, store: Store, now: datetime) -> dict[str, int]:
    """Every pack once, before anything leaves the file.

    A kept pack that fails is written again from its resident generation
    (`put_stream` replaces the damaged file) and verified again.
    """
    count = repaired = 0
    for output_digest, generation_id, row_count in _evict_digests(conn):
        try:
            verify_pack(store, output_digest, row_count)
        except StorageError:
            output_packs.pack(conn, store, generation_id, now=now, repair=True)
            verify_pack(store, output_digest, row_count)
            repaired += 1
        count += 1
    return {"verified": count, "repaired": repaired}


def _unpacked(conn: engine.Connection, table: str) -> Iterator[str]:
    """Target ids not packed at plan time, a page at a time."""
    after = ""
    while True:
        page = [
            str(row[0])
            for row in conn.execute(
                f"SELECT generation_id FROM pg_temp.{table} WHERE packed=0 AND generation_id>%s ORDER BY generation_id LIMIT %s",
                (after, PAGE),
            ).fetchall()
        ]
        if not page:
            return
        yield from page
        after = page[-1]


def _pack_bodies(database: Database, store: Store, now: datetime) -> dict[str, Any]:
    """Pack every target body, verify every object, then stub the rows.

    Objects and their `objects` rows go first (content-addressed, so a rerun
    keeps them). Nothing is stubbed until every object hashes to its name.
    Source rows are stubbed `STUB_ROWS` to a transaction and build labels one
    to a transaction; each stub rechecks that the row still hashes to its
    object. A row already packed is skipped, so a resume continues.
    """
    conn = database.connection
    conn.execute("DROP TABLE IF EXISTS pg_temp.storage_body_digests")
    conn.execute(
        "CREATE TEMPORARY TABLE storage_body_digests (kind TEXT NOT NULL, generation_id TEXT NOT "
        "NULL, digest TEXT NOT NULL, PRIMARY KEY (kind, generation_id))"
    )
    stages = (
        ("parse_body", "storage_bodies", body_packs.write_source, body_packs.PARSE),
        ("label_body", "storage_labels", body_packs.write_label, body_packs.LABEL),
    )
    result: dict[str, Any] = {}
    for kind, table, write, _ in stages:
        written = kept = 0
        for generation_id in _unpacked(conn, table):
            digest, new = write(conn, store, generation_id, now=now)
            written += int(new)
            kept += int(not new)
            conn.execute(
                "INSERT INTO pg_temp.storage_body_digests VALUES (%s, %s, %s)",
                (kind, generation_id, digest),
            )
        result[kind] = {"written": written, "kept": kept}
    _stage("bodies_packed")
    for kind, _, _, cls in stages:
        verified = raw_bytes = stored_bytes = 0
        for (digest,) in conn.execute(
            "SELECT DISTINCT digest FROM pg_temp.storage_body_digests WHERE kind = %s ORDER BY digest NULLS FIRST",
            (kind,),
        ).fetchall():
            try:
                store.verify(cls, str(digest))
            except ObjectUnavailable as error:
                raise StorageError(f"{error}; nothing was stubbed") from error
            row = conn.execute(
                "SELECT raw_bytes, stored_bytes FROM objects WHERE class = %s AND digest = %s",
                (cls, str(digest)),
            ).fetchone()
            verified += 1
            raw_bytes += int(row[0])
            stored_bytes += int(row[1])
        result[kind].update(
            {"objects": verified, "raw_bytes": raw_bytes, "stored_bytes": stored_bytes}
        )
    _stage("bodies_verified")
    for kind, _, _, _ in stages:
        size = STUB_ROWS if kind == "parse_body" else 1
        stub = body_packs.stub_source if kind == "parse_body" else body_packs.stub_label
        stubbed = 0
        after = ""
        while True:
            page = conn.execute(
                (
                    "SELECT generation_id, digest FROM pg_temp.storage_body_digests WHERE kind = %s AND generation_id > %s ORDER BY generation_id NULLS FIRST LIMIT %s"
                ),
                (kind, after, size),
            ).fetchall()
            if not page:
                break
            with database.transaction() as txn:
                for generation_id, digest in page:
                    stubbed += int(stub(txn, str(generation_id), str(digest), now=now))
            after = str(page[-1][0])
        result[kind]["stubbed"] = stubbed
    _stage("bodies_stubbed")
    return result


def _schema(conn: engine.Connection) -> list[tuple[str, str, str | None]]:
    """Stable native index and trigger definitions; no schema objects are replaced."""
    return [
        (str(kind), str(name), str(definition))
        for kind, name, definition in conn.execute(
            "SELECT 'index',c.relname,pg_get_indexdef(c.oid) FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid=c.oid WHERE i.indrelid=to_regclass(%s) "
            "UNION ALL SELECT 'trigger',t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t "
            "WHERE t.tgrelid=to_regclass(%s) ORDER BY 1,2",
            (_TABLE, _TABLE),
        )
    ]


def _revision(conn: engine.Connection) -> int:
    return int(
        conn.execute("SELECT revision FROM history_dispatch_fence WHERE singleton = 1").fetchone()[
            0
        ]
    )


def _free_bytes(conn: engine.Connection) -> int | None:
    # Postgres dead-tuple space is not an exactly measurable SQLite free list.
    return None


def _move(
    conn: engine.Connection,
    fresh: dict[str, Any],
    receipt: dict[str, Any],
    seconds: dict[str, float],
    *,
    now: datetime,
) -> None:
    """One writer transaction: delete eligible references without changing guards.

    Not `Database.transaction`: this is an operator step under the hold, and
    its 75-second bound is for loop writes (D-0217). The note is finished in
    the same transaction, so a crash leaves either nothing moved or the move
    and its receipt.
    """
    started = time.monotonic()
    conn.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
    from .engine.locks import acquire_writer

    try:
        acquire_writer(conn)
        if hot_digest(conn) != fresh["hot_digest"]:
            raise StorageError("the hot set moved after the plan; nothing was moved")
        before = _schema(conn)
        free_before = _free_bytes(conn)
        revision = _revision(conn)
        total_refs = int(conn.execute(f"SELECT count(*) FROM {_TABLE}").fetchone()[0])
        evicted = int(fresh["evict"]["generations"])
        payloads = payload_bytes = 0
        kept = total_refs
        if evicted:
            kept = int(
                conn.execute(
                    f"SELECT count(*) FROM {_TABLE} r JOIN pg_temp.storage_keep k USING(generation_id)"
                ).fetchone()[0]
            )
            if kept != int(fresh["keep"]["references"]):
                raise StorageError("the kept references moved after the plan; nothing was moved")
            conn.execute(
                "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) VALUES(1,%s,%s)",
                (_REASON + str(receipt["plan_digest"]), objects.stamp(now)),
            )
            conn.execute(
                f"DELETE FROM {_TABLE} WHERE generation_id NOT IN "
                "(SELECT generation_id FROM pg_temp.storage_keep)"
            )
            _stage("deleted")
            moved_revision = _revision(conn)
            conn.execute(
                (
                    "INSERT INTO derivation_evicted (generation_id, evicted_at) SELECT generation_id, %s FROM pg_temp.storage_evict ORDER BY generation_id NULLS FIRST"
                ),
                (objects.stamp(now),),
            )
            _stage("marked")
            orphaned = (
                "FROM derivation_payloads p WHERE NOT EXISTS("
                f"SELECT 1 FROM {_TABLE} r WHERE r.payload_sha256=p.payload_sha256)"
            )
            payloads, payload_bytes = (
                int(value)
                for value in conn.execute(
                    f"SELECT count(*),coalesce(sum(octet_length(p.payload_json)),0) {orphaned}"
                ).fetchone()
            )
            conn.execute(
                f"DELETE FROM derivation_payloads WHERE payload_sha256 IN (SELECT p.payload_sha256 {orphaned})"
            )
            conn.execute("DELETE FROM storage_eviction_authority")
            _check(conn, before, revision, moved_revision)
        seconds["move"] = round(time.monotonic() - started, 3)
        receipt.update(
            {
                "references_before": total_refs,
                "references_kept": kept,
                "generations_evicted": evicted,
                "payloads_removed": payloads,
                "payload_text_bytes_removed": payload_bytes,
                "history_fence_revision": [revision, _revision(conn)],
                "free_list_bytes": [free_before, _free_bytes(conn)],
                "seconds": dict(seconds),
                "finished_at": objects.stamp(datetime.now(UTC)),
                "next": "gc --reclaim rewrites application tables to return dead-tuple space",
            }
        )
        conn.execute(
            (
                "UPDATE storage_applies SET finished_at = %s, receipt_json = %s WHERE plan_digest = %s AND "
                "finished_at IS NULL"
            ),
            (objects.stamp(now), canonical_json(receipt).decode(), receipt["plan_digest"]),
        )
        _stage("finished")
        conn.commit()
    except BaseException:
        if not conn.closed:
            with contextlib.suppress(engine.Error):
                conn.rollback()
        raise


def _check(
    conn: engine.Connection,
    before: list[tuple[str, str, str | None]],
    revision: int,
    moved_revision: int,
) -> None:
    """Inside the move: everything that has to hold before it may commit."""
    if _schema(conn) != before:
        raise StorageError("the reference table's indexes or triggers differ")
    if moved_revision < revision:
        raise StorageError("the history dispatch fence moved backwards")
    for (generation_id,) in conn.execute(
        "SELECT generation_id FROM pg_temp.storage_keep WHERE resident = 1 ORDER BY generation_id NULLS FIRST"
    ).fetchall():
        if not output_packs.resident(conn, str(generation_id)):
            raise StorageError(f"kept generation {generation_id} is not resident after the move")
    # Force deferred foreign-key validation before recording the receipt.
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    conn.execute("SET CONSTRAINTS ALL DEFERRED")


# ---------------------------------------------------------------------------
# Incremental eviction
# ---------------------------------------------------------------------------


def _leftovers(conn: engine.Connection) -> list[str]:
    """Evicted generations whose references a stopped tick left behind."""
    return [
        str(row[0])
        for row in conn.execute(
            f"SELECT e.generation_id FROM derivation_evicted e WHERE EXISTS(SELECT 1 FROM {_TABLE} r WHERE r.generation_id=e.generation_id) ORDER BY e.generation_id"
        )
    ]


def _candidates(conn: engine.Connection, cutoff: str) -> Iterator[str]:
    """Old generations that are neither hot nor evicted, by id, a page at a time."""
    after = ""
    while True:
        page = [
            str(row[0])
            for row in conn.execute(
                (
                    "SELECT g.generation_id FROM derivation_generations AS g WHERE g.generation_id > %s AND (CAST(EXTRACT(EPOCH FROM ops.try_timestamp(g.created_at)) AS DOUBLE PRECISION) / 86400.0 + 2440587.5) < (CAST(EXTRACT(EPOCH FROM ops.try_timestamp(%s)) AS DOUBLE PRECISION) / 86400.0 + 2440587.5) AND NOT EXISTS(SELECT 1 AS _value_0 FROM derivation_evicted AS e WHERE e.generation_id = g.generation_id) AND NOT EXISTS(SELECT 1 AS _value_0 FROM derivation_scopes AS s WHERE s.materialized_generation_id = g.generation_id) AND NOT EXISTS(SELECT 1 AS _value_0 FROM derivation_building AS b WHERE b.generation_id = g.generation_id) ORDER BY g.generation_id NULLS FIRST LIMIT %s"
                ),
                (after, cutoff, PAGE),
            )
        ]
        if not page:
            return
        yield from page
        after = page[-1]


def _is_hot(conn: engine.Connection, generation_id: str) -> bool:
    found = conn.execute(
        (
            "SELECT CAST(EXISTS(SELECT 1 AS _value_0 FROM derivation_scopes WHERE materialized_generation_id = %s) OR EXISTS(SELECT 1 AS _value_0 FROM derivation_building WHERE generation_id = %s) AS INTEGER) AS _value_0"
        ),
        (generation_id, generation_id),
    ).fetchone()
    return bool(found[0])


def _shed(database: Database, generation_id: str, limit: int, *, mark: bool, now: datetime) -> int:
    """One transaction: mark (first time), drop up to `limit` top references and their orphans.

    References go from the highest ordinal down, so what stays is a prefix and
    a page-in resumes after it. Returns the references deleted, or -1 when the
    generation turned hot, was evicted by someone else first, or (after the
    first transaction) was paged back in meanwhile, so it carries no marker.
    """
    with database.transaction() as conn:
        if _is_hot(conn, generation_id):
            return -1
        if output_packs.evicted(conn, generation_id) == mark:
            # Marking one already marked, or shedding one no longer marked.
            return -1
        chunk = conn.execute(
            f"SELECT ordinal,payload_sha256 FROM {_TABLE} WHERE generation_id=%s ORDER BY ordinal DESC LIMIT %s",
            (generation_id, limit),
        ).fetchall()
        if mark:
            conn.execute(
                "INSERT INTO derivation_evicted (generation_id, evicted_at) VALUES (%s, %s)",
                (generation_id, objects.stamp(now)),
            )
        if chunk:
            conn.execute(
                "INSERT INTO storage_eviction_authority (singleton, reason, granted_at) VALUES (1, %s, %s)",
                ("evict " + generation_id, objects.stamp(now)),
            )
            try:
                conn.execute(
                    f"DELETE FROM {_TABLE} WHERE generation_id=%s AND ordinal>=%s",
                    (generation_id, int(chunk[-1][0])),
                )
                for (payload_sha256,) in sorted({(str(row[1]),) for row in chunk}):
                    conn.execute(
                        f"DELETE FROM derivation_payloads WHERE payload_sha256=%s AND NOT EXISTS(SELECT 1 FROM {_TABLE} WHERE payload_sha256=%s)",
                        (payload_sha256, payload_sha256),
                    )
            finally:
                conn.execute("DELETE FROM storage_eviction_authority")
        return len(chunk)


#: The guard a generation that cannot be evicted, or a lost pack, leaves on
#: `loop:maintain`; it expires a day after it began, so a failure that lasts
#: is `[guard-expired]`, and it goes on a pass with no failure and no
#: generation waiting out a retry.
EVICT_FAILED = "evict_failed"
GUARD_SUBJECT = "loop:maintain"
GUARD_CLOCK = timedelta(hours=24)
#: Failures a pass names in its summary at most (it counts them all).
MAX_FAILURES = 20
#: What a failure isolates: the generation's pack or its verification.
ISOLATED = (output_packs.GenerationUnavailable, StorageError, ObjectUnavailable, OSError)
#: Errors no other generation would escape (a full disk or quota, a store
#: that cannot be written): the pass stops at the first one.
SHARED_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EACCES, errno.EPERM})
#: A generation that failed waits this long before it is tried again,
#: doubling with each failure up to `RETRY_MAX`, so one that always fails
#: never holds back the generations after it (I1).
RETRY_FIRST = timedelta(days=1)
RETRY_MAX = timedelta(days=7)
#: Where the retry windows live: a hint beside the plans, never backed up.
RETRY_FILE = Path("gc") / "evict-retry.json"
#: Retry windows kept at most; a failure past it is tried again next pass.
RETRY_KEPT = 10_000


def _failure(conn: engine.Connection, now: datetime, reason: str, failed: int) -> None:
    """Note or clear a storage pass's guard, in the caller's transaction."""
    if failed:
        guards.clocked(conn, GUARD_SUBJECT, reason, loop="maintain", now=now, clock=GUARD_CLOCK)
    else:
        guards.delete(conn, GUARD_SUBJECT, reason)


def _shared(error: BaseException) -> bool:
    """Whether the error would stop any generation, not only this one."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, OSError) and seen.errno in SHARED_ERRNOS:
            return True
        seen = seen.__cause__
    return False


class _Retries:
    """Per-generation retry windows (`RETRY_FILE`): `{id: {first_failed,
    failures, next_retry, error}}`. A lost or unreadable file is empty: every
    generation is tried again, which is only slower."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / RETRY_FILE
        self.entries: dict[str, dict[str, Any]] = {}
        self.changed = False
        try:
            value = json.loads(self.path.read_bytes())
            entries = value.get("generations") if isinstance(value, dict) else None
            if isinstance(entries, dict):
                self.entries = {
                    str(key): entry for key, entry in entries.items() if isinstance(entry, dict)
                }
        except (OSError, ValueError):
            pass

    def waiting(self, generation_id: str, now: datetime) -> bool:
        entry = self.entries.get(generation_id)
        if entry is None:
            return False
        try:
            return datetime.fromisoformat(str(entry["next_retry"])) > now
        except (KeyError, ValueError, TypeError):
            return False

    def failed(self, generation_id: str, error: str, now: datetime) -> None:
        entry = self.entries.get(generation_id)
        count = int(entry.get("failures") or 0) + 1 if entry else 1
        if entry is None and len(self.entries) >= RETRY_KEPT:
            return
        wait = min(RETRY_FIRST * (2 ** (count - 1)), RETRY_MAX)
        self.entries[generation_id] = {
            "first_failed": (entry or {}).get("first_failed") or now.isoformat(),
            "failures": count,
            "next_retry": (now + wait).isoformat(),
            "error": error,
        }
        self.changed = True

    def cleared(self, generation_id: str) -> None:
        if self.entries.pop(generation_id, None) is not None:
            self.changed = True

    def prune(self, conn: engine.Connection) -> None:
        """Forget generations that are gone, hot or evicted since they failed."""
        for generation_id in list(self.entries):
            row = conn.execute(
                (
                    "SELECT 1 AS _value_0 FROM derivation_generations AS g WHERE g.generation_id = %s AND NOT EXISTS(SELECT 1 AS _value_0 FROM derivation_evicted AS e WHERE e.generation_id = g.generation_id)"
                ),
                (generation_id,),
            ).fetchone()
            if row is None or _is_hot(conn, generation_id):
                self.cleared(generation_id)

    def save(self) -> None:
        if not self.changed:
            return
        try:
            durable_write(
                self.path,
                canonical_json(
                    {"format": "evict-retry-v1", "generations": dict(sorted(self.entries.items()))}
                ),
            )
        except OSError:
            pass
        self.changed = False


def restore_lost(
    database: Database, *, classes: tuple[str, ...] = tuple(sorted(objects.STORED))
) -> dict[str, Any]:
    """Put back every `objects` file of `classes` whose row stands and whose
    file is gone, from the newest local backup set that holds it (no database
    write: the row is there already).

    A pack of an evicted generation and the object of a stubbed body are the
    only copies outside the sets (F40). One stat per row, a page at a time.
    Returns `{checked, restored, lost}` with up to `MAX_FAILURES` lost named.
    """
    from swingset.backup.recovery import SegmentRecovery

    conn = database.connection
    store = objects.store_for(conn)
    recovery: SegmentRecovery | None = None
    result: dict[str, Any] = {"checked": 0, "restored": 0, "lost": 0, "lost_objects": []}
    try:
        for cls in classes:
            after = ""
            while True:
                page = [
                    str(row[0])
                    for row in conn.execute(
                        "SELECT digest FROM objects WHERE class = %s AND digest > %s ORDER BY digest NULLS FIRST LIMIT %s",
                        (cls, after, PAGE),
                    ).fetchall()
                ]
                if not page:
                    break
                after = page[-1]
                for digest in page:
                    result["checked"] += 1
                    if store.exists(cls, digest):
                        continue
                    if recovery is None:
                        recovery = SegmentRecovery(database.state_dir / "checkpoints")
                    try:
                        recovery.recover_object(store, cls, digest)
                    except ObjectUnavailable as error:
                        result["lost"] += 1
                        if len(result["lost_objects"]) < MAX_FAILURES:
                            result["lost_objects"].append(
                                {"class": cls, "digest": digest, "error": str(error)[:300]}
                            )
                        continue
                    result["restored"] += 1
    finally:
        if recovery is not None:
            recovery.close()
    return result


def _remaining(conn: engine.Connection, cutoff: str, after: str) -> int:
    """Old generations neither hot nor evicted, after `after` by id."""
    return int(
        conn.execute(
            (
                "SELECT COUNT(*) AS _value_0 FROM derivation_generations AS g WHERE g.generation_id > %s AND (CAST(EXTRACT(EPOCH FROM ops.try_timestamp(g.created_at)) AS DOUBLE PRECISION) / 86400.0 + 2440587.5) < (CAST(EXTRACT(EPOCH FROM ops.try_timestamp(%s)) AS DOUBLE PRECISION) / 86400.0 + 2440587.5) AND NOT EXISTS(SELECT 1 AS _value_0 FROM derivation_evicted AS e WHERE e.generation_id = g.generation_id) AND NOT EXISTS(SELECT 1 AS _value_0 FROM derivation_scopes AS s WHERE s.materialized_generation_id = g.generation_id) AND NOT EXISTS(SELECT 1 AS _value_0 FROM derivation_building AS b WHERE b.generation_id = g.generation_id)"
            ),
            (after, cutoff),
        ).fetchone()[0]
    )


def evict(
    database: Database,
    *,
    now: datetime,
    budget_refs: int,
    stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """`_evict` with `secure_delete` off (`disk.fast_delete`)."""
    with disk.fast_delete(database.connection):
        return _evict(database, now=now, budget_refs=budget_refs, stop=stop)


def _evict(
    database: Database,
    *,
    now: datetime,
    budget_refs: int,
    stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Evict old, complete, cold generations, at most `budget_refs` references a call.

    For the maintain loop. A generation is packed and its pack verified before
    its marker is written; each transaction deletes at most `CHUNK_ROWS`
    references. Generations younger than `MIN_AGE`, hot ones and partial ones
    are left alone; a stopped generation is finished first next time.

    First every pack and body object whose file is gone is put back from a
    local backup set (`restore_lost`); one no set holds counts in `lost`.

    One generation's failure (its pack cannot be written, read or verified)
    is isolated: it counts in `failed`, its id in `failures`, the guard
    `loop:maintain evict_failed` notes it, its rows count against the
    budget, and the pass goes on. The generation then waits out a retry
    window (`RETRY_FIRST`, doubling), counted in `deferred`, so one that
    always fails never holds back the rest. Only an error no generation
    would escape (`SHARED_ERRNOS`) stops the pass. A damaged pack of a
    resident generation is written again from the file (`repaired`).
    `backlog` is what the pass left: unfinished leftovers, failed and
    deferred generations, lost objects and, when it stopped early, the
    candidates it did not reach.
    """
    conn = database.connection
    counters: dict[str, Any] = {
        "generations": 0,
        "references": 0,
        "packed": 0,
        "repaired": 0,
        "skipped": 0,
        "failed": 0,
        "deferred": 0,
        "failures": [],
        "restored": 0,
        "lost": 0,
        "backlog": 0,
    }
    if not output_packs.available(conn):
        return {**counters, "reason": "schema"}
    if publication_active(conn):
        return {**counters, "reason": "publication"}
    store = objects.store_for(conn)
    cutoff = (now - MIN_AGE).isoformat()
    spent = 0
    lost = restore_lost(database)
    counters["restored"], counters["lost"] = lost["restored"], lost["lost"]
    if lost["lost_objects"]:
        counters["lost_objects"] = lost["lost_objects"]
    retries = _Retries(database.state_dir)
    retries.prune(conn)

    def stopped() -> bool:
        return spent >= budget_refs or (stop is not None and stop())

    def shed(generation_id: str, *, mark: bool) -> str:
        """'done', 'skipped' (hot, or marked or paged in elsewhere) or 'stopped'."""
        nonlocal spent
        first = mark
        while not stopped():
            limit = min(CHUNK_ROWS, budget_refs - spent)
            deleted = _shed(database, generation_id, limit, mark=first, now=now)
            if deleted < 0:
                counters["skipped"] += 1
                return "skipped"
            first = False
            counters["references"] += deleted
            spent += max(deleted, 1)
            if deleted < limit:
                return "done"
        return "stopped"

    def failed(generation_id: str, error: BaseException) -> None:
        text = f"{type(error).__name__}: {error}"[:300]
        counters["failed"] += 1
        if len(counters["failures"]) < MAX_FAILURES:
            counters["failures"].append({"generation_id": generation_id, "error": text})
        if not _shared(error):
            retries.failed(generation_id, text, now)

    leftovers = _leftovers(conn)
    for index, generation_id in enumerate(leftovers):
        if stopped() or shed(generation_id, mark=False) == "stopped":
            counters["backlog"] = (
                len(leftovers) - index + _remaining(conn, cutoff, "") + counters["lost"]
            )
            _record(database, now, counters, retries)
            return counters
    last = ""
    finished = True
    for generation_id in _candidates(conn, cutoff):
        if stopped():
            finished = False
            break
        last = generation_id
        if retries.waiting(generation_id, now):
            counters["deferred"] += 1
            continue
        row_count = 0
        try:
            if not output_packs.resident(conn, generation_id):
                continue
            output_digest, row_count = output_packs.label(conn, generation_id)
            new = not store.exists(output_packs.CLASS, output_digest)
            output_packs.pack(conn, store, generation_id, now=now)
            try:
                verify_pack(store, output_digest, row_count)
            except StorageError:
                # Bit rot in a pack many labels share: write it again from
                # this generation's rows, which are still in the file.
                output_packs.pack(conn, store, generation_id, now=now, repair=True)
                verify_pack(store, output_digest, row_count)
                counters["repaired"] += 1
            counters["packed"] += int(new)
        except ISOLATED as error:
            failed(generation_id, error)
            # Its pack and verify read the rows: they count against the budget.
            spent += max(row_count, 1)
            if _shared(error):
                finished = False
                break
            continue
        retries.cleared(generation_id)
        outcome = shed(generation_id, mark=True)
        if outcome == "stopped":
            # The budget ran out part way; the leftover is finished first next time.
            counters["backlog"] += 1
            finished = False
            break
        counters["generations"] += int(outcome == "done")
    counters["backlog"] += counters["failed"] + counters["deferred"] + counters["lost"]
    if not finished:
        counters["backlog"] += _remaining(conn, cutoff, last)
    _record(database, now, counters, retries)
    return counters


def _record(database: Database, now: datetime, counters: dict[str, Any], retries: _Retries) -> None:
    retries.save()
    # A generation waiting out its retry still holds the guard, so one that
    # always fails is `[guard-expired]` a day after it began.
    waiting = int(counters["failed"]) + int(counters["lost"]) + len(retries.entries)
    with database.transaction() as write:
        _failure(write, now, EVICT_FAILED, waiting)


# ---------------------------------------------------------------------------
# Incremental body packing
# ---------------------------------------------------------------------------


#: Source rows a maintain pass stubs per transaction.
PASS_ROWS = 100


#: The guard a body that cannot be packed leaves on `loop:maintain`.
PACK_FAILED = "pack_failed"


def pack_bodies(
    database: Database,
    *,
    now: datetime,
    budget_rows: int,
    stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """`_pack_bodies_tick` with `secure_delete` off (`disk.fast_delete`)."""
    with disk.fast_delete(database.connection):
        return _pack_bodies_tick(database, now=now, budget_rows=budget_rows, stop=stop)


def _pack_bodies_tick(
    database: Database,
    *,
    now: datetime,
    budget_rows: int,
    stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Pack bodies of rows that became unprotected, at most `budget_rows` a call.

    For the maintain loop's daily pass. Each object is written and verified
    before the transaction that indexes it, rechecks the row's protection
    (`body_packs.source_protection`) and stubs it. It waits while a
    publication is active. One row whose object cannot be written, read or
    verified, or whose body changed, counts in `failed` with its id in
    `failures` and the guard `loop:maintain pack_failed`; the pass goes on.
    `backlog` is the unpacked target rows the pass left.
    """
    conn = database.connection
    counters: dict[str, Any] = {
        "parse_body": 0,
        "label_body": 0,
        "written": 0,
        "skipped": 0,
        "failed": 0,
        "failures": [],
        "backlog": 0,
    }
    if not body_packs.available(conn):
        return {**counters, "reason": "schema"}
    if publication_active(conn):
        return {**counters, "reason": "publication"}
    store = objects.store_for(conn)
    before = body_packs.cutoff(now)
    state_dir = Path(store.state_dir)
    try:
        with temporary_directory(conn, state_dir):
            conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            try:
                sources, builds = retained(conn, state_dir)
                body_packs.protect_sources(conn, before=before, candidates=sources)
                events = body_packs.event_reads(conn)
                waiting = body_packs.source_backlog(conn)
                parse = [
                    generation_id
                    for generation_id, _ in body_packs.source_targets(
                        conn, unpacked=True, limit=max(0, budget_rows)
                    )
                ]
                body_packs.protect_labels(conn, before=before, candidates=builds)
                labels = [
                    generation_id
                    for generation_id, packed in body_packs.label_targets(conn)
                    if not packed
                ]
                conn.commit()
            except BaseException:
                if not conn.closed:
                    with contextlib.suppress(engine.Error):
                        conn.rollback()
                raise
            finally:
                body_packs.drop_temporary(conn)
    except StorageError as error:
        # A refusal stops every body, so it is the task's failure: `error`
        # is `[storage-failed]` and the guard is `[guard-expired]` after a day.
        unpacked = int(
            conn.execute(
                "SELECT COUNT(*) AS _value_0 FROM source_generations WHERE result_json <> %s",
                (objects.STUB,),
            ).fetchone()[0]
        )
        with database.transaction() as write_txn:
            _failure(write_txn, now, PACK_FAILED, 1)
        return {
            **counters,
            "error": f"StorageError: {error}"[:300],
            "reason": "refused",
            "backlog": unpacked,
        }
    source_set, build_set = frozenset(sources), frozenset(builds)

    def write(kind: str, generation_id: str) -> tuple[str, int, int]:
        raw = (
            body_packs.encode_source(*body_packs.source_columns(conn, generation_id))
            if kind == "parse_body"
            else body_packs.recipe_bytes(conn, generation_id)
        )
        cls = body_packs.PARSE if kind == "parse_body" else body_packs.LABEL
        digest = hashlib.sha256(raw).hexdigest()
        if not store.exists(cls, digest):
            counters["written"] += 1
        written, stored = store.put_bytes(cls, raw)
        store.verify(cls, written)
        return written, len(raw), stored

    def failed(generation_id: str, error: BaseException) -> None:
        counters["failed"] += 1
        if len(counters["failures"]) < MAX_FAILURES:
            counters["failures"].append(
                {"generation_id": generation_id, "error": f"{type(error).__name__}: {error}"[:300]}
            )

    isolated = (body_packs.BodyUnavailable, ObjectUnavailable, OSError)
    batches = [
        ("parse_body", parse[start : start + PASS_ROWS])
        for start in range(0, len(parse), PASS_ROWS)
    ] + [("label_body", [generation_id]) for generation_id in labels]
    for kind, batch in batches:
        if (stop is not None and stop()) or counters["failed"] >= MAX_FAILURES:
            break
        cls = body_packs.PARSE if kind == "parse_body" else body_packs.LABEL
        prepared = []
        for generation_id in batch:
            try:
                prepared.append((generation_id, *write(kind, generation_id)))
            except isolated as error:
                failed(generation_id, error)
        with database.transaction() as txn:
            for generation_id, digest, raw_bytes, stored_bytes in prepared:
                protected = (
                    body_packs.source_protection(
                        txn, generation_id, before=before, candidates=source_set, events=events
                    )
                    if kind == "parse_body"
                    else body_packs.label_protection(
                        txn, generation_id, before=before, candidates=build_set
                    )
                )
                if protected is not None:
                    counters["skipped"] += 1
                    continue
                stub = body_packs.stub_source if kind == "parse_body" else body_packs.stub_label
                try:
                    # The stub checks the row against the object before it
                    # writes anything, so a refusal leaves the row as it was.
                    objects.record(txn, cls, digest, raw_bytes, stored_bytes, now)
                    if stub(txn, generation_id, digest, now=now):
                        counters[kind] += 1
                except body_packs.BodyUnavailable as error:
                    failed(generation_id, error)
    counters["backlog"] = max(
        0, waiting + len(labels) - counters["parse_body"] - counters["label_body"]
    )
    with database.transaction() as write_txn:
        _failure(write_txn, now, PACK_FAILED, int(counters["failed"]))
    return counters
