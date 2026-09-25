"""Immutable derivation generations selected by external input identity.

The scope catalog remembers retired scopes; its desired column is a diagnostic
cache. Currentness is always recomputed from selected dependencies and recipes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swingset import cutoff

from .attempts import SupersededWorkError
from .recipes import recipe_inputs
from .work import WorkUnit

if TYPE_CHECKING:
    from .db import Database


class NondeterministicOutputError(RuntimeError):
    """The same inputs produced different output: a defect, not an input change.

    Unlike `SupersededWorkError` this is a failure. Its attempt gets a retry
    clock and the cycle reports it, instead of parking the unit silently.
    """


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
    # The live-write witness: revisions a replay can move, read by capture().
    # None (desired() alone) makes any cutoff move the replay epoch.
    witness: tuple[tuple[str, int], ...] | None = None


#: Revisions whose movement between capture and completion means a replay
#: rewrote live rows. Findings and snapshots reach dependents through their own
#: dependency groups and tokens.
WITNESS = ("canonical", "dancers", "links", "source_event_map", "source_events", "observations")

#: Dependency groups whose leaves the release closure and the retention walk
#: follow, besides direct ``derivation`` entries. A cutoff keeps a generation
#: only when these are identical ([D-0205](../../../journal/decisions/0205-cut-off-identical-derivation-output.md)).
CLOSURE_SETS = frozenset({"source_observations", "calendar_observations"})

Rows = Callable[[], Iterable[OutputRow]] | Iterable[OutputRow]


@dataclass
class _Group:
    connection: sqlite3.Connection
    selected: dict[WorkUnit, Selection] = field(default_factory=dict)
    completed: set[WorkUnit] = field(default_factory=set)
    # A member whose provisional id another member captured never cuts off.
    pinned: set[WorkUnit] = field(default_factory=set)
    # The id each completed member stands for: its kept id after a cutoff.
    effective: dict[WorkUnit, str] = field(default_factory=dict)
    # Replay epochs to move once every member is certified, with the run
    # clock of the member that moved each.
    replays: dict[tuple[str, str], str] = field(default_factory=dict)


_group: ContextVar[_Group | None] = ContextVar("derivation_consistency_group", default=None)


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='derivation_scopes'").fetchone()
        is not None
    )


def materialized_at(conn: sqlite3.Connection, unit: WorkUnit) -> str | None:
    """When the scope's selected generation last became current: when it was
    written, or when a later run cut off to it (D-0205), whichever is newer."""
    row = conn.execute(
        "SELECT g.created_at FROM derivation_scopes s JOIN derivation_generations g "
        "ON g.generation_id=s.materialized_generation_id "
        "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=?",
        _key(unit),
    ).fetchone()
    if row is None:
        return None
    stamps = [str(row[0])]
    try:
        cut = conn.execute(
            "SELECT max(e.created_at) FROM derivation_scopes s JOIN derivation_equivalences e "
            "ON e.stage=s.stage AND e.unit_kind=s.unit_kind AND e.unit_id=s.unit_id "
            "AND e.generation_id=s.materialized_generation_id "
            "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=?",
            _key(unit),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        cut = None
    if cut is not None and cut[0] is not None:
        stamps.append(str(cut[0]))
    return max(stamps, key=lambda stamp: datetime.fromisoformat(stamp))


def selected_generation(conn: sqlite3.Connection, unit: WorkUnit) -> str | None:
    active = _group.get()
    if active is not None and active.connection is conn and unit in active.selected:
        return active.effective.get(unit, active.selected[unit].generation_id)
    row = conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes WHERE stage=? AND unit_kind=? AND unit_id=?",
        _key(unit),
    ).fetchone()
    return None if row is None else row[0]


def materialized_for(
    conn: sqlite3.Connection,
    scope: tuple[str, str, str],
    generation_id: str | None,
    fingerprint: str,
) -> bool:
    """Whether a generation is a materialization of this scope's input fingerprint.

    It is when the generation was written for that fingerprint, or when an
    equivalence row says a later run with that fingerprint produced exactly the
    generation's output (schema 33, [D-0206](../../../journal/decisions/0206-add-migration-33-after-interning.md)).
    An equivalence for another generation of the same scope does not count.
    Before schema 33 there are no equivalences, so only the first test applies.
    """
    if generation_id is None:
        return False
    row = conn.execute(
        "SELECT input_fingerprint FROM derivation_generations WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    if row is None:
        return False
    if row[0] == fingerprint:
        return True
    return _equivalent(conn, scope, generation_id, fingerprint)


def _equivalent(
    conn: sqlite3.Connection,
    scope: tuple[str, str, str],
    generation_id: str,
    fingerprint: str,
) -> bool:
    try:
        return (
            conn.execute(
                "SELECT 1 FROM derivation_equivalences WHERE stage=? AND unit_kind=? AND unit_id=? "
                "AND input_fingerprint=? AND generation_id=?",
                (*scope, fingerprint, generation_id),
            ).fetchone()
            is not None
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False
        raise


def current_for(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    *,
    recipe: Mapping[str, str] | None = None,
    context: Mapping[str, Any] | None = None,
) -> bool:
    """Whether the scope's selected generation is materialized for its desired inputs.

    Unlike `current()` this takes an explicit recipe, such as the helper recipe
    a project unit captures, and never trusts a stored signature. The answer
    does not depend on how provisional generation ids are minted.
    """
    selected = selected_generation(conn, unit)
    if selected is None:
        return False
    proposed = desired(conn, unit, recipe=recipe, context=context, _validate_artifacts=False)
    return materialized_for(conn, _key(unit), selected, proposed.fingerprint)


def bump_replay(
    conn: sqlite3.Connection, stage: str, kind: str, *, now: datetime | str | None = None
) -> int:
    """Move one kind's replay epoch inside the caller's output transaction.

    A cutoff whose unit changed live rows keeps its generation id, so its
    dependents see the change only through this per-kind token, which both
    ``dependencies()`` and ``certificate()`` include. Returns the new version.
    The hints it queues carry ``now``, the run's clock (wall clock if absent).
    """
    if not conn.in_transaction:
        raise ValueError("a replay epoch moves only inside an output transaction")
    key = canonical((stage, kind))
    conn.execute(
        "INSERT INTO derivation_input_versions VALUES ('replayed',?,1) "
        "ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1",
        (key,),
    )
    row = conn.execute(
        "SELECT version FROM derivation_input_versions WHERE namespace='replayed' AND input_key=?",
        (key,),
    ).fetchone()
    # The epoch makes dependents stale by fingerprint alone. Queue hints for
    # them so the watchdog, which reads only `pending_work`, sees the work
    # (I4). A unit already queued keeps its time, and so its clock.
    from .derivation_dependencies import replay_dependents

    queued_at = _at(now)
    for dependent_stage, dependent_kind in replay_dependents(stage, kind):
        conn.execute(
            "INSERT OR IGNORE INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
            "SELECT stage,unit_kind,unit_id,? FROM derivation_scopes WHERE stage=? AND unit_kind=?",
            (queued_at, dependent_stage, dependent_kind),
        )
    return int(row[0])


def replay_epochs(conn: sqlite3.Connection) -> dict[str, int]:
    """Each kind's replay epoch, keyed `stage/kind`."""
    return {
        "/".join(json.loads(key)): int(version)
        for key, version in conn.execute(
            "SELECT input_key,version FROM derivation_input_versions WHERE namespace='replayed'"
        )
    }


def cycle_progress(
    conn: sqlite3.Connection, run_id: str, replayed_before: Mapping[str, int] | None
) -> dict[str, dict[str, int]]:
    """Generations and equivalences one run wrote, by `stage/kind`, and the
    replay epochs that moved since `replayed_before`. Without a starting read
    (`None`) the replay bumps are left out, not guessed."""

    def by_kind(table: str) -> dict[str, int]:
        return {
            f"{stage}/{kind}": int(count)
            for stage, kind, count in conn.execute(
                f"SELECT stage,unit_kind,count(*) FROM {table} WHERE run_id=? GROUP BY 1,2",
                (run_id,),
            )
        }

    try:
        equivalences = by_kind("derivation_equivalences")
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        equivalences = {}
    result = {
        "generations": by_kind("derivation_generations"),
        "equivalences": equivalences,
    }
    if replayed_before is not None:
        result["replay_bumps"] = {
            key: version - replayed_before.get(key, 0)
            for key, version in replay_epochs(conn).items()
            if version != replayed_before.get(key, 0)
        }
    return result


def selected_dependency(conn: sqlite3.Connection, unit: WorkUnit) -> dict[str, Any]:
    generation = selected_generation(conn, unit)
    return {"kind": "derivation", "key": list(_key(unit)), "generation_id": generation}


def desired(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    *,
    recipe: Mapping[str, str] | None = None,
    context: Mapping[str, Any] | None = None,
    closure: Mapping[str, Any] | None = None,
    _validate_artifacts: bool = True,
) -> Selection:
    """``closure`` is the private release closure a build context names by digest.

    A caller that has not retained it yet passes it here; otherwise it is read
    back from the digest in ``context["release_closure"]``.
    """
    from .derivation_dependencies import dependencies

    policy = dict(recipe) if recipe is not None else recipe_inputs(conn, unit.stage, unit.unit_kind)
    captured_context = dict(context or {})
    pinned = captured_context.get("release_closure") if unit.stage == "build" else None
    if pinned is not None:
        from swingset.build.closure import hydrate, validate

        if closure is not None:
            if pinned.get("private_digest", pinned.get("digest")) != closure["digest"]:
                raise ValueError("build closure does not match the context it is named by")
            pinned = closure
        else:
            pinned = hydrate(conn, pinned)
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
    reuse = bool(
        previous
        and row
        and (row[1] == fingerprint or _equivalent(conn, _key(unit), str(previous), fingerprint))
    )
    if reuse and unit.stage == "build" and _validate_artifacts:
        reuse = _artifact_valid(conn, str(previous))
    support: dict[str, Any] = {}
    if unit.stage == "project" and unit.unit_kind in {"calendar", "inventory", "map"}:
        if reuse:
            # Replay the support these inputs were recorded with: the kept
            # generation's own, or the one its equivalence recorded.
            recipe_json = (
                row[2] if row[1] == fingerprint else _equivalence_recipe(conn, unit, fingerprint)
            )
            support = json.loads(recipe_json).get("continuity", {})
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


def _equivalence_recipe(conn: sqlite3.Connection, unit: WorkUnit, fingerprint: str) -> str:
    row = conn.execute(
        "SELECT r.recipe_json FROM derivation_scopes s JOIN derivation_equivalences e "
        "ON e.stage=s.stage AND e.unit_kind=s.unit_kind AND e.unit_id=s.unit_id "
        "AND e.generation_id=s.materialized_generation_id "
        "JOIN derivation_recipes r ON r.recipe_sha256=e.recipe_sha256 "
        "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=? AND e.input_fingerprint=?",
        (*_key(unit), fingerprint),
    ).fetchone()
    if row is None:
        raise ValueError("equivalence recipe is unavailable")
    return str(row[0])


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
    selection = replace(desired(conn, unit, recipe=recipe, context=context), witness=_witness(conn))
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
        _pin(active, unit, selection)
        active.selected[unit] = selection
    return selection


def _witness(conn: sqlite3.Connection) -> tuple[tuple[str, int], ...]:
    placeholders = ",".join("?" for _ in WITNESS)
    return tuple(
        (str(name), int(value))
        for name, value in conn.execute(
            f"SELECT name,value FROM revisions WHERE name IN ({placeholders}) ORDER BY name",
            WITNESS,
        )
    )


def _derivation_leaves(selection: Selection) -> Iterator[str]:
    for item in (
        *selection.dependencies,
        *(member for members in selection.dependency_sets.values() for member in members),
    ):
        if item.get("kind") == "derivation" and item.get("generation_id") is not None:
            yield str(item["generation_id"])


def _pin(active: _Group, unit: WorkUnit, selection: Selection) -> None:
    """Pin every unfinished member whose provisional id this selection names.

    A cutoff completes a member under its kept id, so a sibling that captured
    the provisional id would name a generation that never exists.
    """
    provisional = {
        member.generation_id: other
        for other, member in active.selected.items()
        if other != unit
        and other not in active.completed
        and member.generation_id != member.previous_generation_id
    }
    if not provisional:
        return
    for leaf in _derivation_leaves(selection):
        owner = provisional.get(leaf)
        if owner is not None:
            active.pinned.add(owner)


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
        for unit, selection in active.selected.items():
            _certify(conn, selection, active.effective.get(unit, selection.generation_id))
        # A member's own replay epoch never enters its own inputs, but it can
        # enter a sibling's. Move it only after every member is certified, so
        # the sibling re-runs later instead of failing its fence now.
        for (stage, kind), moved_at in sorted(active.replays.items()):
            bump_replay(conn, stage, kind, now=moved_at)
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
    It was the last migration until 33, so schemas 30 and 31 are deployable on
    their own and still have to plan, apply, hold, back up and restore
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


#: Columns a scope's generations may carry that its current output leaves out,
#: by table: `events.coverage_tier`, which history owns since phase 1 (D-0212).
Moved = Mapping[str, Sequence[str]]


def _without(
    conn: sqlite3.Connection, generation_id: str, moved: Moved
) -> Iterator[tuple[str, str, str]]:
    """A stored generation's rows in order, with the moved columns taken out."""
    for table, key, raw in conn.execute(
        "SELECT table_name,record_key,payload_json FROM derivation_rows "
        "WHERE generation_id=? ORDER BY ordinal",
        (generation_id,),
    ):
        columns = moved.get(str(table), ())
        payload = str(raw)
        if columns:
            decoded = json.loads(payload)
            if any(column in decoded for column in columns):
                payload = canonical({k: v for k, v in decoded.items() if k not in columns})
        yield str(table), str(key), payload


def _same_output(
    conn: sqlite3.Connection, generation_id: str, label: tuple[str, int], moved: Moved
) -> bool:
    """Whether a run's output label is the stored generation's output.

    It is when the labels match, or when they match once the moved columns
    are taken out of the stored rows: a generation made before history owned
    `events.coverage_tier` recorded it, and the output of the same code now
    leaves it out (D-0212). The build overlays history's value either way.
    """
    recorded = _recorded_label(conn, generation_id)
    if recorded is None or label == recorded:
        return label == recorded
    if not moved or label[1] != recorded[1]:
        return False
    return _fingerprint(_without(conn, generation_id, moved)) == label


def _match_label(expected: tuple[str, int], rows: Iterable[tuple[str, str, str]]) -> None:
    if _fingerprint(rows) != expected:
        raise SupersededWorkError("retained derivation output does not match its label")


def _replayable(rows: Rows) -> Callable[[], Iterable[OutputRow]] | None:
    """A way to read the rows again, or None for a one-shot iterator."""
    if callable(rows):
        return rows
    if isinstance(rows, Sequence):
        sequence = rows
        return lambda: sequence
    return None


def _once(rows: Rows) -> Iterable[OutputRow]:
    """One fresh read of the rows."""
    return rows() if callable(rows) else rows


def _in_closure(item: Mapping[str, Any]) -> bool:
    if item.get("kind") == "derivation":
        return True
    if item.get("kind") != "dependency_set":
        return False
    key = str(item.get("key"))
    return key.startswith("selected_") or key in CLOSURE_SETS


def _closure(dependencies: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted(canonical(item) for item in dependencies if _in_closure(item))


def _closure_stable(conn: sqlite3.Connection, selection: Selection, kept: str) -> bool:
    """Whether every walk over the kept generation equals one over this run.

    The release closure and the retention walk follow only direct derivation
    entries and the CLOSURE groups. When those are identical, keeping the old
    id changes no walk, so no published row can go stale (finding F1).
    """
    row = conn.execute(
        "SELECT d.manifest_json FROM derivation_generations g JOIN derivation_dependency_sets d "
        "ON d.dependency_set_id=g.dependency_set_id WHERE g.generation_id=?",
        (kept,),
    ).fetchone()
    if row is None:
        return False
    return _closure(json.loads(row[0])) == _closure(selection.dependencies)


def _cutoff_candidate(
    conn: sqlite3.Connection, selection: Selection, pointer: str | None, active: _Group | None
) -> bool:
    return bool(
        cutoff.enabled()
        and selection.unit.stage in {"project", "link"}
        and pointer is not None
        and pointer == selection.previous_generation_id
        and not (active is not None and selection.unit in active.pinned)
        # Before schema 33 there is nowhere to record an equivalence.
        and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='derivation_equivalences'"
        ).fetchone()
        is not None
        and _closure_stable(conn, selection, pointer)
    )


def _fence(conn: sqlite3.Connection, selection: Selection, message: str) -> None:
    fresh = desired(
        conn,
        selection.unit,
        recipe=selection.recipe if selection.explicit_recipe else None,
        context=selection.context,
        _validate_artifacts=False,
    )
    if fresh.fingerprint != selection.fingerprint:
        raise SupersededWorkError(message)


def _generation_recipe(selection: Selection) -> str:
    return canonical(
        {
            "inputs": dict(selection.recipe),
            "context": dict(selection.context),
            "continuity": dict(selection.continuity),
        }
    )


def _store_dependency_sets(conn: sqlite3.Connection, selection: Selection) -> str:
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
    return dependency_set


def _record_equivalence(
    conn: sqlite3.Connection,
    selection: Selection,
    kept: str,
    *,
    now: datetime | str,
    run_id: str,
) -> None:
    """Say that this run's inputs produced exactly the kept generation's output."""
    dependency_set = _store_dependency_sets(conn, selection)
    recipe_json = _generation_recipe(selection)
    recipe_sha256 = hashlib.sha256(recipe_json.encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO derivation_recipes VALUES (?,?)", (recipe_sha256, recipe_json)
    )
    conn.execute(
        "INSERT OR IGNORE INTO derivation_equivalences VALUES (?,?,?,?,?,?,?,?,?)",
        (
            *_key(selection.unit),
            selection.fingerprint,
            kept,
            recipe_sha256,
            dependency_set,
            _at(now),
            run_id,
        ),
    )


def complete(
    conn: sqlite3.Connection,
    selection: Selection,
    *,
    rows: Rows,
    now: datetime | str,
    run_id: str,
    moved: Moved | None = None,
) -> str:
    """Finish one scope and return the generation id it now stands for.

    ``rows`` is a callable or a sequence when the output can be read twice,
    which allows an early cutoff: a project or link re-run whose output equals
    the selected generation's keeps that id and records an equivalence instead
    of a new generation
    ([D-0205](../../../journal/decisions/0205-cut-off-identical-derivation-output.md)).
    A bare iterator is read once and always writes a new generation.
    ``moved`` names columns the scope's older generations recorded and its
    output no longer does; the comparison leaves them out (D-0212).
    """
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
    active = _group.get()
    if active is not None and active.connection is not conn:
        active = None
    replay = _replayable(rows)
    kept: str | None = None
    existing = conn.execute(
        "SELECT output_digest,row_count FROM derivation_generations WHERE generation_id=?",
        (selection.generation_id,),
    ).fetchone()
    if existing is not None:
        # A repeat of identical inputs reads its rows once and must reproduce the
        # recorded output. It writes nothing: the references and payloads of that
        # generation are already stored, and both are immutable.
        if not _same_output(
            conn, selection.generation_id, _fingerprint(_offered(_once(rows))), moved or {}
        ):
            raise NondeterministicOutputError(
                "identical derivation inputs produced different output"
            )
    else:
        label: tuple[str, int] | None = None
        if replay is not None and _cutoff_candidate(conn, selection, pointer[0], active):
            # Pass 1 reads the output without writing, then the inputs are
            # fenced again; the decision comes strictly after both fences.
            label = _fingerprint(_offered(replay()))
            _fence(conn, selection, "derivation inputs changed while output was being compared")
            if _same_output(conn, str(pointer[0]), label, moved or {}):
                kept = str(pointer[0])
        if kept is not None:
            _record_equivalence(conn, selection, kept, now=now, run_id=run_id)
        else:
            dependency_set = _store_dependency_sets(conn, selection)

            def record(output_digest: str, row_count: int) -> None:
                if label is not None and (output_digest, row_count) != label:
                    raise NondeterministicOutputError("derivation output is not deterministic")
                conn.execute(
                    "INSERT INTO derivation_generations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        selection.generation_id,
                        *_key(selection.unit),
                        selection.fingerprint,
                        _generation_recipe(selection),
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
            retain_output(conn, selection.generation_id, _once(rows), record_label=record)
    if kept is None:
        _fence(conn, selection, "derivation inputs changed while output rows were being retained")
        conn.execute(
            "UPDATE derivation_scopes SET desired_fingerprint=?,materialized_generation_id=? WHERE stage=? AND unit_kind=? AND unit_id=?",
            (selection.fingerprint, selection.generation_id, *_key(selection.unit)),
        )
    else:
        # The pointer stays, so the selected epoch does not move.
        conn.execute(
            "UPDATE derivation_scopes SET desired_fingerprint=? WHERE stage=? AND unit_kind=? AND unit_id=?",
            (selection.fingerprint, *_key(selection.unit)),
        )
    effective = kept if kept is not None else selection.generation_id
    # A cutoff whose replay rewrote live rows keeps its id, so dependents learn
    # of the rewrite only through this kind's replay epoch (finding F2). So
    # does a re-run of the generation it reuses, by fingerprint or equivalence.
    replayed = (
        kept is not None or (existing is not None and selection.unit.stage in {"project", "link"})
    ) and (selection.witness is None or selection.witness != _witness(conn))
    if active is not None:
        active.completed.add(selection.unit)
        active.effective[selection.unit] = effective
        if replayed:
            active.replays[(selection.unit.stage, selection.unit.unit_kind)] = _at(now)
    else:
        _validate_dependencies(conn, selection)
        _certify(conn, selection, effective)
        if replayed:
            bump_replay(conn, selection.unit.stage, selection.unit.unit_kind, now=now)
    return effective


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

        signature = certificate(
            conn, unit, recipe_inputs(conn, unit.stage, unit.unit_kind), context or {}
        )
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
    if not materialized_for(
        conn, _key(unit), selection.previous_generation_id, selection.fingerprint
    ):
        return False
    if unit.stage == "build":
        return _artifact_valid(conn, str(selection.previous_generation_id))
    return True


def _certify(conn: sqlite3.Connection, selection: Selection, generation_id: str) -> None:
    """Record the fast-path proof for the id the scope now stands for.

    After a cutoff that is the kept id, not the selection's provisional one.
    """
    from .derivation_signatures import certificate, materialized_proof

    _fence(conn, selection, "consistency group changed selected derivation inputs")
    signature = certificate(conn, selection.unit, selection.recipe, selection.context)
    proof, proof_id = materialized_proof(generation_id, signature)
    conn.execute("INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)", (proof_id, proof))
    conn.execute(
        "UPDATE derivation_scopes SET materialized_signature=? WHERE stage=? AND unit_kind=? AND unit_id=? AND materialized_generation_id=?",
        (proof_id, *_key(selection.unit), generation_id),
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
    """Every unit that is not current.

    A unit whose recipe cannot be formed (`RecipeScopeMissing`) cannot be
    shown current, so it counts as pending: callers that ask whether work
    remains fail closed instead of raising out of the cycle (I1). Offline
    selection names such a kind in `readiness_errors`.
    """
    from .derivation_query import query
    from .recipes import RecipeScopeMissing

    with query(conn):
        for unit in known_units(conn, stage):
            try:
                settled = current(conn, unit)
            except RecipeScopeMissing:
                settled = False
            if not settled:
                yield unit


def candidate_units(
    conn: sqlite3.Connection,
    stage: str,
    *,
    kind: str | None = None,
    current_hints: dict[WorkUnit, str] | None = None,
) -> Iterator[WorkUnit]:
    """Use queue/catalog hints first, with a complete derived-query fallback.

    A hint whose unit is already current (a revert deploy, a scope that
    became current without running) is skipped, and when ``current_hints``
    is given it is recorded there with its queue time, for
    `drop_current_hints` to delete after the read.
    """
    from .derivation_query import query

    with query(conn):
        seen: set[WorkUnit] = set()
        condition = " AND unit_kind=?" if kind is not None else ""
        args = (stage, kind) if kind is not None else (stage,)
        for row in conn.execute(
            "SELECT stage,unit_kind,unit_id,enqueued_at FROM pending_work WHERE stage=?"
            + condition
            + " ORDER BY enqueued_at,unit_kind,unit_id",
            args,
        ):
            unit = WorkUnit(str(row[0]), str(row[1]), str(row[2]))
            seen.add(unit)
            if not current(conn, unit):
                yield unit
            elif current_hints is not None:
                current_hints[unit] = str(row[3])
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


def drop_current_hints(database: Database, hints: Mapping[WorkUnit, str]) -> int:
    """Delete project and link hints that a read found current.

    Only a unit run deletes its own hint, so without this a hint for a unit
    that is current without running (an A->B->A deploy) stays forever and
    the watchdog counts it as ready work (I4). A row queued again since the
    read has a new time and stays. Returns the number deleted.
    """
    if not hints:
        return 0
    with database.transaction() as conn:
        return sum(
            conn.execute(
                "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=? "
                "AND enqueued_at=?",
                (*_key(unit), enqueued_at),
            ).rowcount
            for unit, enqueued_at in sorted(hints.items())
            if unit.stage in {"project", "link"}
        )


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
