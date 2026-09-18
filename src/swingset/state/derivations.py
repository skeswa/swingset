"""Immutable derivation generations selected by external input identity.

The scope catalog remembers retired scopes; its desired column is a diagnostic
cache. Currentness is always recomputed from selected dependencies and recipes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .attempts import SupersededWorkError
from .recipes import recipe_inputs
from .work import WorkUnit


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _key(unit: WorkUnit) -> tuple[str, str, str]:
    return unit.stage, unit.unit_kind, unit.unit_id


def _at(now: datetime | str | None) -> str:
    value = datetime.fromisoformat(now) if isinstance(now, str) else now or datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("derivation timestamps require a timezone")
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class OutputRow:
    table: str
    key: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class Selection:
    unit: WorkUnit
    fingerprint: str
    recipe: Mapping[str, str]
    dependencies: tuple[dict[str, Any], ...]
    generation_id: str
    previous_generation_id: str | None
    context: Mapping[str, Any]
    dependency_sets: Mapping[str, tuple[dict[str, Any], ...]] = field(repr=False)
    explicit_recipe: bool = False
    continuity: Mapping[str, Any] = field(default_factory=dict)
    continuity_digest: str = ""


@dataclass
class _Group:
    connection: sqlite3.Connection
    selected: dict[WorkUnit, Selection] = field(default_factory=dict)
    completed: set[WorkUnit] = field(default_factory=set)


_group: ContextVar[_Group | None] = ContextVar("derivation_consistency_group", default=None)


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='derivation_scopes'").fetchone()
        is not None
    )


def selected_generation(conn: sqlite3.Connection, unit: WorkUnit) -> str | None:
    active = _group.get()
    if active is not None and active.connection is conn and unit in active.selected:
        return active.selected[unit].generation_id
    row = conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes WHERE stage=? AND unit_kind=? AND unit_id=?",
        _key(unit),
    ).fetchone()
    return None if row is None else row[0]


def selected_dependency(conn: sqlite3.Connection, unit: WorkUnit) -> dict[str, Any]:
    generation = selected_generation(conn, unit)
    return {"kind": "derivation", "key": list(_key(unit)), "generation_id": generation}


def desired(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    *,
    recipe: Mapping[str, str] | None = None,
    context: Mapping[str, Any] | None = None,
    _validate_artifacts: bool = True,
) -> Selection:
    from .derivation_dependencies import dependencies

    policy = dict(recipe) if recipe is not None else recipe_inputs(conn, unit.stage)
    captured_context = dict(context or {})
    pinned = captured_context.get("release_closure") if unit.stage == "build" else None
    if pinned is not None:
        from swingset.build.closure import validate

        validate(conn, pinned)
        manifest = [
            {
                "kind": "derivation",
                "key": [item["stage"], item["unit_kind"], item["unit_id"]],
                "generation_id": item["generation_id"],
            }
            for item in pinned["selected"]
        ]
        manifest.append({"kind": "release_closure", "fingerprint": pinned["digest"]})
        sets: dict[str, tuple[dict[str, Any], ...]] = {}
    else:
        manifest, sets = dependencies(conn, unit, selected_dependency)
    ordered = tuple(sorted(manifest, key=canonical))
    captured_context = dict(context or {})
    fingerprint = digest(
        {
            "format": "derivation-v1",
            "scope": _key(unit),
            "recipe": policy,
            "dependencies": ordered,
            "context": captured_context,
        }
    )
    row = conn.execute(
        "SELECT s.materialized_generation_id,g.input_fingerprint,g.recipe_json FROM derivation_scopes s "
        "LEFT JOIN derivation_generations g ON g.generation_id=s.materialized_generation_id "
        "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=?",
        _key(unit),
    ).fetchone()
    previous = None if row is None else row[0]
    reuse = bool(previous and row and row[1] == fingerprint)
    if reuse and unit.stage == "build" and _validate_artifacts:
        reuse = _artifact_valid(conn, str(previous))
    support: dict[str, Any] = {}
    if unit.stage == "project" and unit.unit_kind in {"calendar", "inventory", "map"}:
        if reuse:
            support = json.loads(row[2]).get("continuity", {})
        else:
            # Prior output support preserves IDs without becoming a self-changing trigger.
            members = tuple(
                {"kind": "continuity", "table": table, "row": dict(value)}
                for table, order in (
                    ("events", "event_id"),
                    ("source_event_map", "source,source_ref"),
                )
                for value in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
            )
            support_id = digest(members)
            sets[support_id] = members
            support = {"previous_generation_id": previous, "dependency_set_id": support_id}
    support_digest = digest(support)
    generation = (
        str(previous)
        if reuse
        else "dg_" + digest((_key(unit), fingerprint, previous, support_digest))
    )
    return Selection(
        unit,
        fingerprint,
        policy,
        ordered,
        generation,
        previous,
        captured_context,
        sets,
        recipe is not None,
        support,
        support_digest,
    )


def capture(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    *,
    now: datetime | str | None = None,
    recipe: Mapping[str, str] | None = None,
    context: Mapping[str, Any] | None = None,
) -> Selection:
    if not conn.in_transaction:
        raise ValueError("capture requires the admission transaction")
    conn.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,?,?)",
        (*_key(unit), _at(now)),
    )
    selection = desired(conn, unit, recipe=recipe, context=context)
    conn.execute(
        "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,desired_fingerprint,registered_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET desired_fingerprint=excluded.desired_fingerprint",
        (*_key(unit), selection.fingerprint, _at(now)),
    )
    for set_id, members in selection.dependency_sets.items():
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)",
            (set_id, canonical(members)),
        )
    active = _group.get()
    if active is not None and active.connection is conn:
        active.selected[unit] = selection
    return selection


@contextmanager
def group(conn: sqlite3.Connection) -> Iterator[None]:
    """Reserve inline member identities and require all members to commit together."""
    if not conn.in_transaction:
        raise ValueError("a derivation group requires an output transaction")
    previous = _group.get()
    if previous is not None:
        if previous.connection is not conn:
            raise ValueError("a consistency group cannot span connections")
        yield
        return
    active = _Group(conn)
    token = _group.set(active)
    try:
        yield
        if set(active.selected) != active.completed:
            raise SupersededWorkError("consistency group has unfinished generations")
        for selection in active.selected.values():
            _validate_dependencies(conn, selection)
        # Inline children selected reserved parents; certify after every pointer
        # in the atomic group has reached its committed generation.
        for selection in active.selected.values():
            _certify(conn, selection)
    finally:
        _group.reset(token)


def _validate_dependencies(conn: sqlite3.Connection, selection: Selection) -> None:
    for dependency in (
        *selection.dependencies,
        *(item for members in selection.dependency_sets.values() for item in members),
    ):
        if dependency["kind"] == "derivation" and dependency.get("generation_id") is not None:
            if (
                conn.execute(
                    "SELECT 1 FROM derivation_generations WHERE generation_id=?",
                    (dependency["generation_id"],),
                ).fetchone()
                is None
            ):
                raise SupersededWorkError("selected dependency generation is incomplete")


def interned(conn: sqlite3.Connection) -> bool:
    """Whether derivation output rows are stored once behind the `derivation_rows` view.

    Schema 32 splits the old `derivation_rows` table into `derivation_payloads`
    and `derivation_row_refs` and puts a view of the old shape back over them.
    It is the last migration, so schemas 30 and 31 are deployable on their own
    and still have to plan, apply, hold, back up and restore
    ([D-0167](../../../journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md)).
    Everything that names the interned tables asks here first and falls back to
    the one table; reading through the `derivation_rows` name needs no branch,
    because it is a table before the split and a view after it.
    """
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='derivation_row_refs'"
        ).fetchone()
        is not None
    )


def retain_output(
    conn: sqlite3.Connection,
    generation_id: str,
    rows: Iterable[OutputRow],
    *,
    record_label: Callable[[str, int], None] | None = None,
) -> None:
    """Store one generation's output rows and check them against its label.

    This is the only writer of derivation output. Each distinct payload is
    stored once and every row keeps one reference to it. The function never
    touches a scope pointer or any scheduling state, so bringing archived output
    back later calls it exactly as completion does. Rows are read in one pass
    and never held in memory.

    A generation with no label yet belongs to a caller that is completing it.
    That caller passes ``record_label``, which is handed the digest and row count
    of the stream as it was stored and writes the label from them; the label is
    then read back and compared. References are written before the label. Their
    foreign key to ``derivation_generations`` is deferred, so a commit still
    refuses a reference whose label never arrived.

    With a label already recorded, residency decides what happens:

    - every row of the generation is there: nothing to do;
    - the references are there but some payload bytes are not, which is what
      plan step 4 leaves behind: store the missing bytes and read the whole
      generation back to check it; or
    - there are no references yet: write both and check the stream.
    """
    if not conn.in_transaction:
        # A failed retention must leave nothing behind. References are permanent
        # once committed, so a half-written generation on an autocommit
        # connection could never be finished or retried.
        raise ValueError("output retention must share the caller's transaction")
    recorded = _recorded_label(conn, generation_id)
    if record_label is not None:
        if recorded is not None:
            raise SupersededWorkError("output retention cannot relabel a generation")
        stored = _fingerprint(_store(conn, generation_id, rows))
        record_label(*stored)
        if _recorded_label(conn, generation_id) != stored:
            raise SupersededWorkError("retained derivation output does not match its label")
        return
    if recorded is None:
        raise SupersededWorkError("output retention requires a recorded generation label")
    row_table = "derivation_row_refs" if interned(conn) else "derivation_rows"
    references = int(
        conn.execute(
            f"SELECT count(*) FROM {row_table} WHERE generation_id=?", (generation_id,)
        ).fetchone()[0]
    )
    if not references:
        _match_label(recorded, _store(conn, generation_id, rows))
        return
    if references == recorded[1] and not _payload_bytes_missing(conn, generation_id):
        # Whatever wrote these references checked them against this same label,
        # and references and payloads are both immutable, so counting is proof
        # enough. Reading the whole generation back through the view would undo
        # the saving this change exists to make.
        return
    _restore(conn, generation_id, rows, recorded)


def _recorded_label(conn: sqlite3.Connection, generation_id: str) -> tuple[str, int] | None:
    row = conn.execute(
        "SELECT output_digest,row_count FROM derivation_generations WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    return None if row is None else (str(row[0]), int(row[1]))


def _payload_bytes_missing(conn: sqlite3.Connection, generation_id: str) -> bool:
    """Does any reference of this generation name payload bytes that are gone?

    Never, before interning: a row of the one table carries its own bytes, so a
    row that is there cannot have lost them.
    """
    if not interned(conn):
        return False
    return bool(
        conn.execute(
            "SELECT EXISTS(SELECT 1 FROM derivation_row_refs r WHERE r.generation_id=? "
            "AND NOT EXISTS(SELECT 1 FROM derivation_payloads p "
            "WHERE p.payload_sha256=r.payload_sha256))",
            (generation_id,),
        ).fetchone()[0]
    )


def _store(
    conn: sqlite3.Connection, generation_id: str, rows: Iterable[OutputRow]
) -> Iterator[tuple[str, str, str]]:
    """Write one payload per distinct row and one reference per row, in order.

    Before interning there is nothing to share, so each row is written whole
    into the one table. Either way the stream this yields is the same, and the
    label is computed from it.
    """
    shared = interned(conn)
    for ordinal, row in enumerate(rows):
        payload = canonical(dict(row.payload))
        if not shared:
            conn.execute(
                "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
                (generation_id, ordinal, row.table, row.key, payload),
            )
            yield row.table, row.key, payload
            continue
        payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)",
            (payload_sha256, payload),
        )
        conn.execute(
            "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
            (generation_id, ordinal, row.table, row.key, payload_sha256),
        )
        yield row.table, row.key, payload


def _offered(rows: Iterable[OutputRow]) -> Iterator[tuple[str, str, str]]:
    """The fingerprint shape of rows that are read but not stored."""
    for row in rows:
        yield row.table, row.key, canonical(dict(row.payload))


def _restore(
    conn: sqlite3.Connection,
    generation_id: str,
    rows: Iterable[OutputRow],
    expected: tuple[str, int],
) -> None:
    """Put back the payload bytes of a generation whose references are all there.

    The references already say which payloads this generation is made of, so the
    offered rows are only a source of bytes. A row the references do not name is
    ignored rather than stored, so a wrong restore leaves no ownerless payload
    behind, and the check below still fails because the generation does not read
    back whole.
    """
    if interned(conn):
        wanted = {
            str(row[0])
            for row in conn.execute(
                "SELECT payload_sha256 FROM derivation_row_refs WHERE generation_id=?",
                (generation_id,),
            )
        }
        for row in rows:
            payload = canonical(dict(row.payload))
            payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
            if payload_sha256 in wanted:
                conn.execute(
                    "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)",
                    (payload_sha256, payload),
                )
    # Before interning a row's bytes never leave it, so there is nothing to put
    # back and the read-back below is the whole check.
    _match_label(
        expected,
        (
            (str(table_name), str(record_key), str(payload_json))
            for table_name, record_key, payload_json in conn.execute(
                "SELECT table_name,record_key,payload_json FROM derivation_rows "
                "WHERE generation_id=? ORDER BY ordinal",
                (generation_id,),
            )
        ),
    )


def _fingerprint(rows: Iterable[tuple[str, str, str]]) -> tuple[str, int]:
    """The output digest and row count of one generation's rows, in order."""
    output = hashlib.sha256()
    count = 0
    for row in rows:
        count += 1
        output.update(canonical(row).encode() + b"\n")
    return output.hexdigest(), count


def _match_label(expected: tuple[str, int], rows: Iterable[tuple[str, str, str]]) -> None:
    if _fingerprint(rows) != expected:
        raise SupersededWorkError("retained derivation output does not match its label")


def complete(
    conn: sqlite3.Connection,
    selection: Selection,
    *,
    rows: Iterable[OutputRow],
    now: datetime | str,
    run_id: str,
) -> str:
    if not conn.in_transaction:
        raise ValueError("materialization must share the output transaction")
    captured = digest(
        {
            "format": "derivation-v1",
            "scope": _key(selection.unit),
            "recipe": dict(selection.recipe),
            "dependencies": selection.dependencies,
            "context": dict(selection.context),
        }
    )
    if (
        captured != selection.fingerprint
        or digest(selection.continuity) != selection.continuity_digest
        or any(digest(members) != key for key, members in selection.dependency_sets.items())
    ):
        raise SupersededWorkError("captured derivation manifest was modified")
    current = desired(
        conn,
        selection.unit,
        recipe=selection.recipe if selection.explicit_recipe else None,
        context=selection.context,
        _validate_artifacts=False,
    )
    conn.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,?,?)",
        (*_key(selection.unit), _at(now)),
    )
    pointer = conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes WHERE stage=? AND unit_kind=? AND unit_id=?",
        _key(selection.unit),
    ).fetchone()
    if (
        current.fingerprint != selection.fingerprint
        or pointer is None
        or pointer[0] not in (selection.previous_generation_id, selection.generation_id)
    ):
        raise SupersededWorkError("derivation inputs changed while output was being computed")
    existing = conn.execute(
        "SELECT output_digest,row_count FROM derivation_generations WHERE generation_id=?",
        (selection.generation_id,),
    ).fetchone()
    if existing is not None:
        # A repeat of identical inputs reads its rows once and must reproduce the
        # recorded output. It writes nothing: the references and payloads of that
        # generation are already stored, and both are immutable.
        if _fingerprint(_offered(rows)) != (str(existing[0]), int(existing[1])):
            raise SupersededWorkError("identical derivation inputs produced different output")
    else:
        for set_id, members in selection.dependency_sets.items():
            conn.execute(
                "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)",
                (set_id, canonical(members)),
            )
        dependency_set = digest(selection.dependencies)
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)",
            (dependency_set, canonical(selection.dependencies)),
        )

        def record(output_digest: str, row_count: int) -> None:
            conn.execute(
                "INSERT INTO derivation_generations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    selection.generation_id,
                    *_key(selection.unit),
                    selection.fingerprint,
                    canonical(
                        {
                            "inputs": dict(selection.recipe),
                            "context": dict(selection.context),
                            "continuity": dict(selection.continuity),
                        }
                    ),
                    dependency_set,
                    selection.previous_generation_id,
                    output_digest,
                    row_count,
                    _at(now),
                    run_id,
                ),
            )

        # The rows stream straight into storage and the label is written from
        # what was stored, so no scope's output is ever held in memory.
        retain_output(conn, selection.generation_id, rows, record_label=record)
    after_rows = desired(
        conn,
        selection.unit,
        recipe=selection.recipe if selection.explicit_recipe else None,
        context=selection.context,
        _validate_artifacts=False,
    )
    if after_rows.fingerprint != selection.fingerprint:
        raise SupersededWorkError("derivation inputs changed while output rows were being retained")
    conn.execute(
        "UPDATE derivation_scopes SET desired_fingerprint=?,materialized_generation_id=? WHERE stage=? AND unit_kind=? AND unit_id=?",
        (selection.fingerprint, selection.generation_id, *_key(selection.unit)),
    )
    active = _group.get()
    if active is not None and active.connection is conn:
        active.completed.add(selection.unit)
    else:
        _validate_dependencies(conn, selection)
        _certify(conn, selection)
    return selection.generation_id


def current(
    conn: sqlite3.Connection, unit: WorkUnit, *, context: Mapping[str, Any] | None = None
) -> bool:
    # A shared prerequisite can block thousands of event candidates. Reuse its
    # exact answer only for one owned read selection; worker/write transactions
    # and filesystem-dependent build checks always perform their normal checks.
    from .derivation_query import currentness

    if unit.stage in {"project", "link"} and _group.get() is None:
        return currentness(
            conn,
            ("current", unit, canonical(context or {})),
            lambda: _current(conn, unit, context=context),
        )
    return _current(conn, unit, context=context)


def _current(
    conn: sqlite3.Connection, unit: WorkUnit, *, context: Mapping[str, Any] | None = None
) -> bool:
    pointer = conn.execute(
        "SELECT materialized_generation_id,materialized_signature FROM derivation_scopes WHERE stage=? AND unit_kind=? AND unit_id=?",
        _key(unit),
    ).fetchone()
    if pointer is None or pointer[0] is None:
        return False
    if unit.stage != "build" and pointer[1] is not None:
        from .derivation_signatures import certificate, materialized_proof

        signature = certificate(conn, unit, recipe_inputs(conn, unit.stage), context or {})
        proof, proof_id = materialized_proof(pointer[0], signature)
        if pointer[1] == proof_id:
            return (
                conn.execute(
                    "SELECT 1 FROM derivation_dependency_sets WHERE dependency_set_id=? AND manifest_json=?",
                    (proof_id, proof),
                ).fetchone()
                is not None
            )
    selection = desired(conn, unit, context=context, _validate_artifacts=False)
    if selection.previous_generation_id is None:
        return False
    row = conn.execute(
        "SELECT input_fingerprint FROM derivation_generations WHERE generation_id=?",
        (selection.previous_generation_id,),
    ).fetchone()
    if row is None or row[0] != selection.fingerprint:
        return False
    if unit.stage == "build":
        return _artifact_valid(conn, selection.previous_generation_id)
    return True


def _certify(conn: sqlite3.Connection, selection: Selection) -> None:
    from .derivation_signatures import certificate, materialized_proof

    fresh = desired(
        conn,
        selection.unit,
        recipe=selection.recipe if selection.explicit_recipe else None,
        context=selection.context,
        _validate_artifacts=False,
    )
    if fresh.fingerprint != selection.fingerprint:
        raise SupersededWorkError("consistency group changed selected derivation inputs")
    signature = certificate(conn, selection.unit, selection.recipe, selection.context)
    proof, proof_id = materialized_proof(selection.generation_id, signature)
    conn.execute("INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)", (proof_id, proof))
    conn.execute(
        "UPDATE derivation_scopes SET materialized_signature=? WHERE stage=? AND unit_kind=? AND unit_id=? AND materialized_generation_id=?",
        (proof_id, *_key(selection.unit), selection.generation_id),
    )


def _artifact_valid(conn: sqlite3.Connection, generation: str) -> bool:
    from swingset.publish.safety import StaleCandidateError, verify_candidate_files

    artifacts = conn.execute(
        "SELECT payload_json FROM derivation_rows WHERE generation_id=? AND table_name='artifact'",
        (generation,),
    ).fetchall()
    if not artifacts:
        return False
    try:
        for artifact in artifacts:
            payload = json.loads(artifact[0])
            path = Path(payload["path"])
            verify_candidate_files(path)
            built = json.loads((path / "BUILT").read_bytes())
            if built["manifest_hash"] != payload["manifest_hash"]:
                return False
    except (OSError, ValueError, KeyError, StaleCandidateError):
        return False
    return True


def known_units(conn: sqlite3.Connection, stage: str) -> Iterator[WorkUnit]:
    from .derivation_dependencies import scopes

    yield from scopes(conn, stage)


def pending_units(conn: sqlite3.Connection, stage: str) -> Iterator[WorkUnit]:
    from .derivation_query import query

    with query(conn):
        for unit in known_units(conn, stage):
            if not current(conn, unit):
                yield unit


def candidate_units(
    conn: sqlite3.Connection, stage: str, *, kind: str | None = None
) -> Iterator[WorkUnit]:
    """Use queue/catalog hints first, with a complete derived-query fallback."""
    from .derivation_query import query

    with query(conn):
        seen: set[WorkUnit] = set()
        condition = " AND unit_kind=?" if kind is not None else ""
        args = (stage, kind) if kind is not None else (stage,)
        for row in conn.execute(
            "SELECT stage,unit_kind,unit_id FROM pending_work WHERE stage=?"
            + condition
            + " ORDER BY enqueued_at,unit_kind,unit_id",
            args,
        ):
            unit = WorkUnit(*row)
            seen.add(unit)
            if not current(conn, unit):
                yield unit
        for row in conn.execute(
            "SELECT stage,unit_kind,unit_id FROM derivation_scopes WHERE stage=? AND materialized_generation_id IS NULL"
            + condition
            + " ORDER BY unit_kind,unit_id",
            args,
        ):
            unit = WorkUnit(*row)
            if unit not in seen:
                seen.add(unit)
                yield unit
        for unit in known_units(conn, stage):
            if (
                unit not in seen
                and (kind is None or kind == unit.unit_kind)
                and not current(conn, unit)
            ):
                yield unit


def ready(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    # Bulk prerequisite checks must observe one snapshot. Reuse existing caller
    # transactions; never commit or retain a cache across any caller write.
    snapshot = unit.stage == "link" and _group.get() is None and not conn.in_transaction
    if snapshot:
        conn.execute("BEGIN")
    try:
        return _ready(conn, unit)
    finally:
        if snapshot:
            conn.rollback()


def _ready(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    from .derivation_dependencies import prerequisites

    active = _group.get()
    global_kinds = (
        ("calendar", "source_index", "dancer")
        if unit.unit_kind == "inventory"
        else (
            ("calendar", "source_index", "dancer", "inventory", "map", "event", "source_event")
            if unit.unit_kind == "history"
            else (("dancer",) if unit.stage == "link" else ())
        )
    )
    if active is None and global_kinds:
        placeholders = ",".join("?" for _ in global_kinds)
        if conn.execute(
            f"SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind IN ({placeholders}) AND materialized_generation_id IS NULL LIMIT 1",
            global_kinds,
        ).fetchone():
            return False
    if unit.stage == "link" and active is None:
        from .derivation_readiness import all_dancers_current

        if not all_dancers_current(conn):
            return False
    dependencies = prerequisites(conn, unit)
    if unit.stage == "link" and active is None:
        dependencies = tuple(item for item in dependencies if item.unit_kind != "dancer")
    return all(
        (active is not None and active.connection is conn and dependency in active.selected)
        or current(conn, dependency)
        for dependency in dependencies
    )


def refresh(conn: sqlite3.Connection, now: datetime | str | None = None) -> int:
    """Register retained scopes without making queues or cached desires authoritative."""
    count = 0
    for stage in ("project", "link"):
        for unit in known_units(conn, stage):
            count += conn.execute(
                "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,?,?)",
                (*_key(unit), _at(now)),
            ).rowcount
    return count
