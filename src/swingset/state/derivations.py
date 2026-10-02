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
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swingset import cutoff

from . import output_packs
from .attempts import SupersededWorkError
from .derivation_dependencies import store_set
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


def _now(now: datetime | str | None) -> datetime:
    return datetime.fromisoformat(_at(now))


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
    # A link's read set: the keys its dependencies were formed from (plan
    # F20). Completion stores them as the unit's `derivation_reads`.
    reads: tuple[str, ...] | None = None


#: Revisions whose movement between capture and completion means a replay
#: rewrote live rows. Findings and snapshots reach dependents through their own
#: dependency groups and tokens.
WITNESS = ("canonical", "dancers", "links", "source_event_map", "source_events", "observations")

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
    # clock of the member that moved each, and the event units among them.
    replays: dict[tuple[str, str], str] = field(default_factory=dict)
    unit_replays: dict[WorkUnit, str] = field(default_factory=dict)
    # Members whose completion was deferred to a staged finish (`stage`).
    staged: set[WorkUnit] = field(default_factory=set)


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
    conn: sqlite3.Connection,
    stage: str,
    kind: str,
    *,
    now: datetime | str | None = None,
    unit_id: str | None = None,
) -> int:
    """Move one kind's replay epoch inside the caller's output transaction.

    A cutoff whose unit changed live rows keeps its generation id, so its
    dependents see the change only through this per-kind token, which both
    ``dependencies()`` and ``certificate()`` include. Returns the new version.
    The hints it queues carry ``now``, the run's clock (wall clock if absent).
    An event unit (``unit_id``) also moves its own version, which only its
    readers (its link) include (plan F20).
    """
    if not conn.in_transaction:
        raise ValueError("a replay epoch moves only inside an output transaction")
    if unit_id is not None and (stage, kind) == ("project", "event"):
        _bump_unit_replay(conn, WorkUnit(stage, kind, unit_id), now=now)
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


def _bump_unit_replay(
    conn: sqlite3.Connection, unit: WorkUnit, *, now: datetime | str | None
) -> None:
    from .derivation_dependencies import UNIT_REPLAY, read_hints

    conn.execute(
        "INSERT INTO derivation_input_versions VALUES (?,?,1) "
        "ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1",
        (UNIT_REPLAY, canonical(_key(unit))),
    )
    read_hints(conn, "/".join(_key(unit)), enqueued_at=_at(now))


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

    # A generation still being stored in chunks is not progress yet (WP4).
    building = (
        " AND generation_id NOT IN (SELECT generation_id FROM derivation_building)"
        if building_available(conn)
        else ""
    )

    def by_kind(table: str, extra: str = "") -> dict[str, int]:
        return {
            f"{stage}/{kind}": int(count)
            for stage, kind, count in conn.execute(
                f"SELECT stage,unit_kind,count(*) FROM {table} WHERE run_id=?{extra} GROUP BY 1,2",
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
        "generations": by_kind("derivation_generations", building),
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
    reads: Iterable[str] | None = None,
    _validate_artifacts: bool = True,
) -> Selection:
    """``closure`` is the private release closure a build context names by digest.

    A caller that has not retained it yet passes it here; otherwise it is read
    back from the digest in ``context["release_closure"]``. ``reads`` is a
    link's read set; without it a link's recorded one is used (plan F20).
    """
    from .derivation_dependencies import dependencies, recorded_reads

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
    read_set: tuple[str, ...] | None = None
    release_manifest = captured_context.get("release_manifest") if unit.stage == "build" else None
    if pinned is None and release_manifest is not None:
        # A partitioned build is its release manifest (D-0224): the manifest
        # names every artifact, the correction token and the build inputs, so
        # it is the whole dependency. No pointer or graph is read here.
        if (
            conn.execute(
                "SELECT 1 FROM release_manifests WHERE manifest_digest=?", (release_manifest,)
            ).fetchone()
            is None
        ):
            from swingset.build.closure_manifest import ClosureError

            raise ClosureError("release_manifest_unavailable")
        manifest = [{"kind": "release_manifest", "fingerprint": str(release_manifest)}]
        sets = {}
    elif pinned is None:
        if unit.stage == "link":
            read_set = tuple(
                sorted(set(reads) if reads is not None else recorded_reads(conn, unit))
            )
        manifest, sets = dependencies(conn, unit, selected_dependency, reads=read_set)
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
    if unit.stage == "project" and unit.unit_kind in {"calendar", "inventory"}:
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
        reads=read_set,
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
    reads: Iterable[str] | None = None,
) -> Selection:
    """Select a unit's inputs inside the admission or output transaction.

    A link's inputs are those of ``reads``, the read set of the run about to
    commit, else of its recorded read set (plan F20).
    """
    if not conn.in_transaction:
        raise ValueError("capture requires the admission transaction")
    conn.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,?,?)",
        (*_key(unit), _at(now)),
    )
    selection = replace(
        desired(conn, unit, recipe=recipe, context=context, reads=reads), witness=_witness(conn)
    )
    conn.execute(
        "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,desired_fingerprint,registered_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET desired_fingerprint=excluded.desired_fingerprint",
        (*_key(unit), selection.fingerprint, _at(now)),
    )
    for set_id, members in selection.dependency_sets.items():
        store_set(conn, set_id, canonical(members), created_at=_at(now))
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
        # A staged member is validated and certified when it finishes.
        for unit, selection in active.selected.items():
            if unit not in active.staged:
                _validate_dependencies(conn, selection)
        # Inline children selected reserved parents; certify after every pointer
        # in the atomic group has reached its committed generation.
        for unit, selection in active.selected.items():
            if unit not in active.staged:
                _certify(conn, selection, active.effective.get(unit, selection.generation_id))
        # A member's own replay epoch never enters its own inputs, but it can
        # enter a sibling's. Move it only after every member is certified, so
        # the sibling re-runs later instead of failing its fence now.
        for (stage, kind), moved_at in sorted(active.replays.items()):
            bump_replay(conn, stage, kind, now=moved_at)
        for unit, moved_at in sorted(active.unit_replays.items()):
            if (unit.stage, unit.unit_kind) == ("project", "event"):
                _bump_unit_replay(conn, unit, now=moved_at)
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
    - there are no references yet: write both and check the stream; or
    - the generation is evicted (schema 36): its pack holds it, nothing to do.
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
    if output_packs.evicted(conn, generation_id):
        # The pack holds the output whole (F33). Writing references from the
        # offered rows would mix two copies; `output_packs.page_in` is the way
        # back.
        return
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


def _partial_label(conn: sqlite3.Connection, generation_id: str) -> tuple[str, int] | None:
    """The recorded label of a generation with rows still to store, else None.

    Such a generation is one a staged run stopped storing. Its building row
    may be gone (`collect_orphans`), so only its references say it is not
    whole. Rows are stored by ordinal in committed order (a chunk at a time,
    or all at once), so a partial generation always lacks its last ordinal:
    one index probe answers, as in `unfinished_excluded`, and a repeat of a
    whole generation reads none of its rows.
    """
    recorded = _recorded_label(conn, generation_id)
    if recorded is None or recorded[1] == 0 or not interned(conn) or not building_available(conn):
        return None
    last = conn.execute(
        "SELECT 1 FROM derivation_row_refs WHERE generation_id=? AND ordinal=?",
        (generation_id, recorded[1] - 1),
    ).fetchone()
    if last is not None or output_packs.evicted(conn, generation_id):
        # An evicted generation is whole in its pack, even while a page-in
        # has put back only some of its references (F33).
        return None
    return recorded


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
    try:
        for _ in output_packs.rows(conn, generation_id, expected=expected):
            pass
    except output_packs.GenerationUnavailable as error:
        raise SupersededWorkError("retained derivation output does not match its label") from error


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
    """A stored generation's rows in order, with the moved columns taken out.

    Read through `output_packs.rows`, so an evicted generation is read from
    its pack; a generation that does not match its label raises.
    """
    for table, key, raw in output_packs.rows(conn, generation_id):
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
    try:
        return _fingerprint(_without(conn, generation_id, moved)) == label
    except output_packs.GenerationUnavailable:
        return False


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


def _cutoff_candidate(
    conn: sqlite3.Connection, selection: Selection, pointer: str | None, active: _Group | None
) -> bool:
    """Whether a re-run may keep its scope's pointer when its output is identical.

    Output alone decides. The closure-stable rule (F1 of D-0205) is gone
    (D-0224): releases take current pointers and never walk a kept
    generation's dependencies, so a kept generation that names an older
    upstream one publishes nothing stale. The closure path withholds such an
    anchor instead. Replay epochs (F2), pinning (F3) and moved columns
    (D-0212) still apply.
    """
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
    )


def _fence(conn: sqlite3.Connection, selection: Selection, message: str) -> None:
    fresh = desired(
        conn,
        selection.unit,
        recipe=selection.recipe if selection.explicit_recipe else None,
        context=selection.context,
        reads=selection.reads,
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


def _store_dependency_sets(
    conn: sqlite3.Connection, selection: Selection, *, now: datetime | str | None
) -> str:
    """Store the selection's sets (again: a set A captured may have been collected)."""
    at = _at(now)
    for set_id, members in selection.dependency_sets.items():
        store_set(conn, set_id, canonical(members), created_at=at)
    dependency_set = digest(selection.dependencies)
    store_set(conn, dependency_set, canonical(selection.dependencies), created_at=at)
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
    dependency_set = _store_dependency_sets(conn, selection, now=now)
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
    pointer = _check_capture(conn, selection, now)
    active = _group.get()
    if active is not None and active.connection is not conn:
        active = None
    replay = _replayable(rows)
    kept: str | None = None
    existing = conn.execute(
        "SELECT output_digest,row_count FROM derivation_generations WHERE generation_id=?",
        (selection.generation_id,),
    ).fetchone()
    building = _building_label(conn, selection.generation_id)
    if existing is not None and building is None:
        # A partial generation whose building row was collected is still
        # building: it must never be reused as whole.
        building = _partial_label(conn, selection.generation_id)
    if existing is not None and building is not None:
        # A staged completion stored part of this generation and stopped: store
        # the rest here, verifying the rows already there.
        _store_all(conn, selection.generation_id, _once(rows), building, now=_at(now))
        existing = None
    elif (
        existing is not None
        and output_packs.evicted(conn, selection.generation_id)
        and not output_packs.pack_intact(conn, selection.generation_id)
    ):
        # A reuse of an evicted generation whose pack is missing or damaged:
        # the output just computed puts it back, checked against its label,
        # and writes the pack again for every label that shares it.
        output_packs.rebuild(conn, selection.generation_id, _offered(_once(rows)), now=_now(now))
    elif existing is not None:
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
        if replay is not None and _cutoff_candidate(conn, selection, pointer, active):
            # Pass 1 reads the output without writing, then the inputs are
            # fenced again; the decision comes strictly after both fences.
            label = _fingerprint(_offered(replay()))
            _fence(conn, selection, "derivation inputs changed while output was being compared")
            if _same_output(conn, str(pointer), label, moved or {}):
                kept = str(pointer)
        if kept is not None:
            _record_equivalence(conn, selection, kept, now=now, run_id=run_id)
        else:
            dependency_set = _store_dependency_sets(conn, selection, now=now)

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
        # A current pointer stays resident (F34): an evicted target comes
        # back whole in this transaction before the pointer names it.
        output_packs.page_in(conn, selection.generation_id, now=_now(now))
        conn.execute(
            "UPDATE derivation_scopes SET desired_fingerprint=?,materialized_generation_id=? WHERE stage=? AND unit_kind=? AND unit_id=?",
            (selection.fingerprint, selection.generation_id, *_key(selection.unit)),
        )
        if pointer != selection.generation_id:
            _hint_readers(conn, selection.unit, now)
    else:
        # The pointer stays, so the selected epoch does not move.
        conn.execute(
            "UPDATE derivation_scopes SET desired_fingerprint=? WHERE stage=? AND unit_kind=? AND unit_id=?",
            (selection.fingerprint, *_key(selection.unit)),
        )
    _record_reads(conn, selection)
    effective = kept if kept is not None else selection.generation_id
    # A cutoff whose replay rewrote live rows keeps its id, so dependents learn
    # of the rewrite only through this kind's replay epoch (finding F2). So
    # does a re-run of the generation it reuses, by fingerprint or equivalence.
    replayed = (
        kept is not None or (existing is not None and selection.unit.stage in {"project", "link"})
    ) and (
        selection.witness is None
        or selection.witness != _witness(conn)
        # A staged A of this unit committed live rows before this run captured.
        or building_moved(conn, selection.unit, selection.generation_id)
    )
    if active is not None:
        active.completed.add(selection.unit)
        active.effective[selection.unit] = effective
        if replayed:
            active.replays[(selection.unit.stage, selection.unit.unit_kind)] = _at(now)
            active.unit_replays[selection.unit] = _at(now)
    else:
        _validate_dependencies(conn, selection)
        _certify(conn, selection, effective, now=now)
        if replayed:
            bump_replay(
                conn,
                selection.unit.stage,
                selection.unit.unit_kind,
                now=now,
                unit_id=selection.unit.unit_id,
            )
    if building_available(conn):
        # This completion finished a generation a staged run had started.
        conn.execute(
            "DELETE FROM derivation_building WHERE generation_id=?", (selection.generation_id,)
        )
    return effective


def _record_reads(conn: sqlite3.Connection, selection: Selection) -> None:
    """Make a completed link's read set its recorded one (`derivation_reads`).

    Before schema 35 there is no table; `recorded_reads` then reads the set
    back from the dependency manifest.
    """
    if selection.reads is None or not _has_table(conn, "derivation_reads"):
        return
    from . import read_index

    read_index.replace(conn, selection.unit, selection.reads)


def _hint_readers(conn: sqlite3.Connection, unit: WorkUnit, now: datetime | str) -> None:
    """A project scope's pointer moved: queue the units whose read set names it."""
    if unit.stage != "project":
        return
    from .derivation_dependencies import read_hints

    read_hints(conn, "/".join(_key(unit)), enqueued_at=_at(now))


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def check_reads(
    conn: sqlite3.Connection, selection: Selection, observed: Mapping[str, Any]
) -> None:
    """Raise when a link input moved between the run's load and its commit.

    ``observed`` holds each read key's value at load (plan F20). The values
    now are formed inside the output transaction, from the same keys.
    """
    from .derivation_dependencies import link_inputs

    if selection.reads is None or set(selection.reads) != set(observed):
        raise SupersededWorkError("a link commits the read set it loaded")
    now = link_inputs(conn, selection.unit.unit_id, selection.reads)
    moved = sorted(key for key in selection.reads if now[key] != observed[key])
    if moved:
        raise SupersededWorkError(f"link inputs changed during linking: {', '.join(moved[:5])}")


def _check_capture(
    conn: sqlite3.Connection, selection: Selection, now: datetime | str
) -> str | None:
    """Check a selection against its inputs now and return the scope pointer.

    The captured manifest must be unmodified, the inputs unchanged, and the
    pointer still the capture's (or the generation it selected).
    """
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
        reads=selection.reads,
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
    return None if pointer[0] is None else str(pointer[0])


# ---------------------------------------------------------------------------
# Staged completion (WP4)
# ---------------------------------------------------------------------------

#: Output rows one staged write transaction stores. A unit takes the staged
#: path when it is a project year or its kept generation has more rows.
CHUNK_ROWS = 5000

Output = Callable[[sqlite3.Connection], Iterable[OutputRow]]


@dataclass(frozen=True)
class Deferred:
    """A completion after step A: live rows committed, output not yet stored.

    ``output`` reads the unit's output rows from a connection. ``candidate``
    is the kept generation a cutoff may keep, decided in A. ``written`` is
    the live-write witness at the end of A; against the capture's witness it
    says whether the unit's replay moved live rows.
    """

    selection: Selection
    moved: Moved
    now: str
    run_id: str
    output: Output = field(repr=False)
    candidate: str | None = None
    written: tuple[tuple[str, int], ...] | None = None


@dataclass
class Chunking:
    """One processing transaction's permission to stage one unit."""

    connection: sqlite3.Connection
    unit: WorkUnit
    enabled: bool
    deferred: Deferred | None = None


_chunking: ContextVar[Chunking | None] = ContextVar("derivation_chunking", default=None)


@contextmanager
def chunking(conn: sqlite3.Connection, unit: WorkUnit) -> Iterator[Chunking]:
    """Let ``unit``'s completion inside the next transaction take the staged path.

    Only `derive_one` sets this, around its processing transaction. A caller
    already inside a transaction (such as `accept-year`) never stages, since
    A must commit before its output is read from another connection.
    """
    context = Chunking(conn, unit, enabled=not conn.in_transaction)
    token = _chunking.set(context)
    try:
        yield context
    finally:
        _chunking.reset(token)


def building_available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='derivation_building'"
        ).fetchone()
        is not None
    )


def stageable(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    """Whether a completion of ``unit`` about to be captured may be staged.

    Asked before the unit opens its consistency group: a unit captured
    inside another's group completes with it, in one transaction.
    """
    context = _chunking.get()
    if (
        context is None
        or not context.enabled
        or context.connection is not conn
        or context.unit != unit
        or context.deferred is not None
        or _group.get() is not None
        or unit.stage not in {"project", "link"}
        or not building_available(conn)
    ):
        return False
    if (unit.stage, unit.unit_kind) == ("project", "year"):
        return True
    row = conn.execute(
        "SELECT g.row_count FROM derivation_scopes s JOIN derivation_generations g "
        "ON g.generation_id=s.materialized_generation_id "
        "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=?",
        _key(unit),
    ).fetchone()
    if row is not None and int(row[0]) > CHUNK_ROWS:
        return True
    # A run that returns to an evicted generation over `CHUNK_ROWS` pages it
    # in before its pointer flip: staged, a chunk per write (step B), never
    # whole inside one inline transaction.
    return output_packs.available(conn) and (
        conn.execute(
            "SELECT 1 FROM derivation_generations g JOIN derivation_evicted e "
            "ON e.generation_id=g.generation_id WHERE g.stage=? AND g.unit_kind=? "
            "AND g.unit_id=? AND g.row_count>? LIMIT 1",
            (*_key(unit), CHUNK_ROWS),
        ).fetchone()
        is not None
    )


def stage(
    conn: sqlite3.Connection,
    selection: Selection,
    *,
    output: Output,
    now: datetime | str,
    run_id: str,
    moved: Moved | None = None,
) -> Deferred | None:
    """Step A of a staged completion, inside the processing transaction.

    Runs the checks `complete` runs, then records a building row instead of
    reading the output; the scope pointer, attempt and queue are left for
    step C. Returns None, and the caller completes inline, when the unit's
    group has other members.
    """
    if not conn.in_transaction:
        raise ValueError("materialization must share the output transaction")
    active = _group.get()
    if (
        active is None
        or active.connection is not conn
        or set(active.selected) != {selection.unit}
        or selection.unit in active.completed
    ):
        return None
    context = _chunking.get()
    if context is None or context.connection is not conn or context.unit != selection.unit:
        return None
    pointer = _check_capture(conn, selection, now)
    candidate = pointer if _cutoff_candidate(conn, selection, pointer, active) else None
    written = _witness(conn)
    at = _at(now)
    generation_id = selection.generation_id
    # The first capture's witness and a sticky "moved" flag survive a resume:
    # a retry's own capture already sees what an earlier A moved, and its A
    # rewrites nothing, so only the stored record says the live rows moved
    # under the pointer. Another generation's building row of this unit is an
    # earlier A that committed live rows for other inputs; it counts as moved.
    first = selection.witness
    live_moved = first is None or first != written
    for other, stored in conn.execute(
        "SELECT generation_id,witness_json FROM derivation_building "
        "WHERE stage=? AND unit_kind=? AND unit_id=?",
        _key(selection.unit),
    ).fetchall():
        if other != generation_id:
            live_moved = True
            continue
        recorded = _stored_witness(stored)
        first = recorded.captured
        live_moved = live_moved or recorded.moved
    witness = canonical(
        {
            "captured": None if first is None else [list(i) for i in first],
            "written": [list(item) for item in written],
            "moved": live_moved,
        }
    )
    row = conn.execute(
        "SELECT label_digest FROM derivation_building WHERE generation_id=?", (generation_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO derivation_building(generation_id,stage,unit_kind,unit_id,"
            "input_fingerprint,witness_json,run_id,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (generation_id, *_key(selection.unit), selection.fingerprint, witness, run_id, at, at),
        )
    else:
        # A resume. B stores whatever is missing: `_store_chunk` checks the
        # references themselves, and `rows_stored` only reports progress, so
        # a generation row that is gone (a restore from an older copy) needs
        # no reset here; `_begin_generation` writes it again.
        conn.execute(
            "UPDATE derivation_building SET witness_json=?,run_id=?,updated_at=? "
            "WHERE generation_id=?",
            (witness, run_id, at, generation_id),
        )
    partial = _partial_label(conn, generation_id)
    if partial is not None:
        # A partial generation whose building row was collected: B resumes it
        # by ordinal under its recorded label instead of reusing it as whole.
        conn.execute(
            "UPDATE derivation_building SET label_digest=?,label_rows=? WHERE generation_id=?",
            (*partial, generation_id),
        )
    active.completed.add(selection.unit)
    active.staged.add(selection.unit)
    deferred = Deferred(
        selection,
        dict(moved or {}),
        at,
        run_id,
        output,
        candidate=candidate,
        written=written,
    )
    context.deferred = deferred
    return deferred


def finish_building(
    database: Database,
    deferred: Deferred,
    *,
    commit: Callable[[sqlite3.Connection], None] | None = None,
) -> str:
    """Pass 1, step B and step C of a staged completion; return the effective id.

    Pass 1 labels the output from a read snapshot and decides between a
    cutoff, a reuse and a new generation. B stores a new generation's rows,
    ``CHUNK_ROWS`` per write transaction, verifying rows a killed run already
    stored. C flips the pointer (or records the equivalence) in one short
    transaction, in which ``commit`` also runs.
    """
    selection = deferred.selection
    generation_id = selection.generation_id
    kept: str | None = None
    existing = False
    with database.reader() as reader:
        label = _fingerprint(_offered(deferred.output(reader)))
        if not reader.execute(
            "SELECT 1 FROM derivation_building WHERE generation_id=?", (generation_id,)
        ).fetchone():
            raise SupersededWorkError("staged generation lost its building row")
        building = _building_label(reader, generation_id)
        recorded = _recorded_label(reader, generation_id)
        if building is not None:
            if label != building:
                raise NondeterministicOutputError("derivation output is not deterministic")
        elif recorded is not None:
            existing = True
            if not _same_output(reader, generation_id, label, deferred.moved):
                raise NondeterministicOutputError(
                    "identical derivation inputs produced different output"
                )
        elif deferred.candidate is not None and _same_output(
            reader, deferred.candidate, label, deferred.moved
        ):
            kept = deferred.candidate
    if kept is None and not existing:
        _store_building(database, deferred, label)
    elif kept is None:
        _page_in_building(database, generation_id, now=deferred.now, output=deferred.output)
    with database.transaction() as conn:
        effective = _finish(conn, deferred, kept=kept, existing=existing)
        if commit is not None:
            commit(conn)
    return effective


@dataclass(frozen=True)
class _StoredWitness:
    captured: tuple[tuple[str, int], ...] | None
    moved: bool


def _stored_witness(value: str | None) -> _StoredWitness:
    """A building row's witness: the first capture, and whether any A moved live rows.

    A row written before the flag existed moved when its two witnesses differ;
    one that cannot be read counts as moved.
    """
    try:
        data = json.loads(value or "null")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return _StoredWitness(None, True)
    captured = data.get("captured")
    first = None if captured is None else tuple((str(n), int(v)) for n, v in captured)
    written = tuple((str(n), int(v)) for n, v in data.get("written") or ())
    moved = data.get("moved")
    if not isinstance(moved, bool):
        moved = first is None or first != written
    return _StoredWitness(first, moved)


def building_moved(conn: sqlite3.Connection, unit: WorkUnit, generation_id: str) -> bool:
    """Whether a staged step A of ``unit`` moved its live rows under its pointer.

    True when the building row of ``generation_id`` says an A moved them, or
    when a building row of another generation of the unit exists: that A
    committed live rows for other inputs and never finished.
    """
    if not building_available(conn):
        return False
    return any(
        other != generation_id or _stored_witness(stored).moved
        for other, stored in conn.execute(
            "SELECT generation_id,witness_json FROM derivation_building "
            "WHERE stage=? AND unit_kind=? AND unit_id=?",
            _key(unit),
        )
    )


def building(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    """Whether a staged completion of ``unit`` committed its live rows and has not finished."""
    return (
        building_available(conn)
        and conn.execute(
            "SELECT 1 FROM derivation_building WHERE stage=? AND unit_kind=? AND unit_id=? LIMIT 1",
            _key(unit),
        ).fetchone()
        is not None
    )


def unfinished_excluded(conn: sqlite3.Connection, table: str = "derivation_generations") -> str:
    """An SQL condition, starting with AND, that leaves out unfinished staged output.

    A generation still building, or one a staged run stopped storing (fewer
    references than its label counts; chunks store whole, by ordinal; an
    evicted generation is whole in its pack and does not count), was
    never selected and must not be read as output: not by a release closure
    at a cutoff, not as a retention window root (F8, I6). ``table`` names the
    `derivation_generations` row being tested.
    """
    clauses = []
    if building_available(conn):
        clauses.append(
            f" AND {table}.generation_id NOT IN (SELECT generation_id FROM derivation_building)"
        )
    if interned(conn):
        # An evicted generation is whole in its pack (F33).
        evicted = (
            f" OR {table}.generation_id IN (SELECT generation_id FROM derivation_evicted)"
            if output_packs.available(conn)
            else ""
        )
        clauses.append(
            " AND (coalesce((SELECT max(r.ordinal)+1 FROM derivation_row_refs r "
            f"WHERE r.generation_id={table}.generation_id),0)>={table}.row_count{evicted})"
        )
    return "".join(clauses)


def incomplete_generations(conn: sqlite3.Connection) -> dict[str, int]:
    """Partial generations no building row names: how many, and their references.

    `collect_orphans` leaves these when a unit's inputs changed between B
    and C. They are never read as output and nothing collects them yet
    (phase 4, D-0217), so maintain counts them and the watchdog names a
    growing count. One indexed lookup per generation. An evicted
    generation has no references and is not partial (F33).
    """
    if not interned(conn):
        return {"rows": 0, "refs": 0}
    building = (
        " AND g.generation_id NOT IN (SELECT generation_id FROM derivation_building)"
        if building_available(conn)
        else ""
    )
    if output_packs.available(conn):
        # Evicted generations are whole in their packs (F33).
        building += " AND g.generation_id NOT IN (SELECT generation_id FROM derivation_evicted)"
    rows, refs = conn.execute(
        "SELECT count(*),coalesce(sum(stored),0) FROM (SELECT g.row_count,"
        "coalesce((SELECT max(r.ordinal)+1 FROM derivation_row_refs r "
        "WHERE r.generation_id=g.generation_id),0) AS stored "
        f"FROM derivation_generations g WHERE 1{building}) WHERE stored<row_count"
    ).fetchone()
    return {"rows": int(rows), "refs": int(refs)}


def holding_generations(conn: sqlite3.Connection) -> int:
    """Partial generations that still hold references: `incomplete_generations`
    without the labels whose references the collector removed (D-0231).
    Maintain records it for the watchdog's `[incomplete-generations]`."""
    if not interned(conn):
        return 0
    building = (
        " AND g.generation_id NOT IN (SELECT generation_id FROM derivation_building)"
        if building_available(conn)
        else ""
    )
    if output_packs.available(conn):
        building += " AND g.generation_id NOT IN (SELECT generation_id FROM derivation_evicted)"
    row = conn.execute(
        "SELECT count(*) FROM (SELECT g.row_count,(SELECT max(r.ordinal)+1 "
        "FROM derivation_row_refs r WHERE r.generation_id=g.generation_id) AS stored "
        f"FROM derivation_generations g WHERE 1{building}) "
        "WHERE stored IS NOT NULL AND stored<row_count"
    ).fetchone()
    return int(row[0])


def collect_orphans(database: Database, unit: WorkUnit, *, keep: str | None) -> int:
    """Delete ``unit``'s building rows other than ``keep``'s, once its run finished (I6).

    Such a row is a step A whose inputs changed before its C. While it
    exists the unit is not current, so the unit runs again, counts it as a
    live-row move (`building_moved`) and then drops it here. Generations and
    row references are immutable: a partial generation its B left stays, no
    reader takes it for output (`unfinished_excluded`), and the retention
    plan lists it `incomplete`, never local or archivable. A later run with
    the same inputs resumes it by ordinal. Returns the rows deleted.
    """
    if not building_available(database.connection):
        return 0
    with database.transaction() as conn:
        return int(
            conn.execute(
                "DELETE FROM derivation_building WHERE stage=? AND unit_kind=? AND unit_id=? "
                "AND generation_id IS NOT ?",
                (*_key(unit), keep),
            ).rowcount
        )


def _building_label(conn: sqlite3.Connection, generation_id: str) -> tuple[str, int] | None:
    """The label a staged run is storing a generation under, if it got that far."""
    if not building_available(conn):
        return None
    row = conn.execute(
        "SELECT label_digest,label_rows FROM derivation_building WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    return None if row is None or row[0] is None else (str(row[0]), int(row[1]))


def _store_building(database: Database, deferred: Deferred, label: tuple[str, int]) -> None:
    """Step B: store a new generation's rows in chunks, resuming by ordinal."""
    selection = deferred.selection
    generation_id = selection.generation_id
    output = hashlib.sha256()
    count = 0
    with database.reader() as reader:
        rows = iter(deferred.output(reader))
        first = 0
        while True:
            batch = list(islice(rows, CHUNK_ROWS))
            with database.transaction() as conn:
                if first == 0:
                    _begin_generation(conn, deferred, label)
                for item in _store_chunk(conn, generation_id, first, batch, now=deferred.now):
                    count += 1
                    output.update(canonical(item).encode() + b"\n")
            first += len(batch)
            if len(batch) < CHUNK_ROWS:
                break
    if (output.hexdigest(), count) != label:
        raise NondeterministicOutputError("derivation output is not deterministic")


def _page_in_building(
    database: Database, generation_id: str, *, now: str, output: Output | None = None
) -> None:
    """Step B for a reused evicted generation: page it in, ``CHUNK_ROWS`` per write.

    A pack that is missing or damaged is rebuilt from the unit's own output
    instead, which pass 1 checked against the same label: each write reads the
    whole output again from a fresh snapshot, so nothing commits unless it
    reproduces the label (`output_packs.rebuild`).
    """
    if not output_packs.evicted(database.connection, generation_id):
        return
    moment = _now(now)
    if output is not None and not output_packs.pack_intact(database.connection, generation_id):
        while True:
            with database.reader() as reader, database.transaction() as conn:
                if output_packs.rebuild(
                    conn,
                    generation_id,
                    _offered(output(reader)),
                    limit=CHUNK_ROWS,
                    now=moment,
                ):
                    return
    while True:
        with database.transaction() as conn:
            if output_packs.page_in(conn, generation_id, limit=CHUNK_ROWS, now=moment):
                return


def _begin_generation(conn: sqlite3.Connection, deferred: Deferred, label: tuple[str, int]) -> None:
    """Write the generation row under its pass-1 label, once, and the building label."""
    selection = deferred.selection
    generation_id = selection.generation_id
    if _recorded_label(conn, generation_id) is None:
        conn.execute(
            "INSERT INTO derivation_generations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                generation_id,
                *_key(selection.unit),
                selection.fingerprint,
                _generation_recipe(selection),
                _store_dependency_sets(conn, selection, now=deferred.now),
                selection.previous_generation_id,
                *label,
                deferred.now,
                deferred.run_id,
            ),
        )
        # Without its generation row no reference can have committed, so the
        # progress count starts again.
        conn.execute(
            "UPDATE derivation_building SET rows_stored=0 WHERE generation_id=?", (generation_id,)
        )
    elif _recorded_label(conn, generation_id) != label:
        raise NondeterministicOutputError("derivation output is not deterministic")
    conn.execute(
        "UPDATE derivation_building SET label_digest=?,label_rows=?,updated_at=? "
        "WHERE generation_id=?",
        (*label, deferred.now, generation_id),
    )


def _store_chunk(
    conn: sqlite3.Connection,
    generation_id: str,
    first: int,
    rows: Sequence[OutputRow],
    *,
    now: str,
) -> Iterator[tuple[str, str, str]]:
    """Store rows at ordinals ``first`` onward; a row already stored must match.

    Yields the stream items the label is computed from.
    """
    stored = {
        int(ordinal): (str(table), str(key), str(sha))
        for ordinal, table, key, sha in conn.execute(
            "SELECT ordinal,table_name,record_key,payload_sha256 FROM derivation_row_refs "
            "WHERE generation_id=? AND ordinal>=? AND ordinal<?",
            (generation_id, first, first + len(rows)),
        )
    }
    for ordinal, row in enumerate(rows, first):
        payload = canonical(dict(row.payload))
        payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
        have = stored.get(ordinal)
        if have is None:
            conn.execute(
                "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (payload_sha256, payload)
            )
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
                (generation_id, ordinal, row.table, row.key, payload_sha256),
            )
        elif have != (row.table, row.key, payload_sha256):
            raise NondeterministicOutputError("a stored row of a building generation differs")
        yield row.table, row.key, payload
    conn.execute(
        "UPDATE derivation_building SET rows_stored=max(rows_stored,?),updated_at=? "
        "WHERE generation_id=?",
        (first + len(rows), now, generation_id),
    )


def _store_all(
    conn: sqlite3.Connection,
    generation_id: str,
    rows: Iterable[OutputRow],
    label: tuple[str, int],
    *,
    now: str,
) -> None:
    """Finish a building generation inline, in the caller's transaction."""
    iterator = iter(rows)

    def stream() -> Iterator[tuple[str, str, str]]:
        first = 0
        while batch := list(islice(iterator, CHUNK_ROWS)):
            yield from _store_chunk(conn, generation_id, first, batch, now=now)
            first += len(batch)

    if _fingerprint(stream()) != label:
        raise NondeterministicOutputError("derivation output is not deterministic")
    _check_stored(conn, generation_id, label)


def _check_stored(conn: sqlite3.Connection, generation_id: str, label: tuple[str, int]) -> None:
    """A generation is whole when its label is recorded and every row is
    referenced, or it is evicted and its pack holds every row (F33)."""
    if _recorded_label(conn, generation_id) != label:
        raise SupersededWorkError("staged generation is incomplete")
    if output_packs.evicted(conn, generation_id):
        return
    references = conn.execute(
        "SELECT count(*) FROM derivation_row_refs WHERE generation_id=?", (generation_id,)
    ).fetchone()[0]
    if int(references) != label[1]:
        raise SupersededWorkError("staged generation is incomplete")


def _finish(
    conn: sqlite3.Connection, deferred: Deferred, *, kept: str | None, existing: bool
) -> str:
    """Step C: fence, flip or keep, replay, certify; inside the caller's transaction."""
    selection = deferred.selection
    unit = selection.unit
    generation_id = selection.generation_id
    _fence(conn, selection, "derivation inputs changed while output was being stored")
    pointer = conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes WHERE stage=? AND unit_kind=? AND unit_id=?",
        _key(unit),
    ).fetchone()
    allowed = (kept,) if kept is not None else (selection.previous_generation_id, generation_id)
    if pointer is None or pointer[0] not in allowed:
        raise SupersededWorkError("derivation pointer moved while output was being stored")
    if kept is not None:
        _record_equivalence(conn, selection, kept, now=deferred.now, run_id=deferred.run_id)
        conn.execute(
            "UPDATE derivation_scopes SET desired_fingerprint=? WHERE stage=? AND unit_kind=? AND unit_id=?",
            (selection.fingerprint, *_key(unit)),
        )
    else:
        label = _recorded_label(conn, generation_id)
        if label is None:
            raise SupersededWorkError("staged generation was removed before it was selected")
        if output_packs.evicted(conn, generation_id):
            # B pages a reused evicted generation in chunk by chunk. One that
            # is still evicted here pages in now when it fits one chunk; a
            # larger one goes back to B, so no transaction pages in more than
            # `CHUNK_ROWS` rows.
            if label[1] > CHUNK_ROWS:
                raise SupersededWorkError("evicted generation paging in")
            output_packs.page_in(conn, generation_id, now=_now(deferred.now))
        # A reused generation is checked too: one indexed count guards the
        # pointer against a partial generation taken for whole.
        _check_stored(conn, generation_id, label)
        conn.execute(
            "UPDATE derivation_scopes SET desired_fingerprint=?,materialized_generation_id=? WHERE stage=? AND unit_kind=? AND unit_id=?",
            (selection.fingerprint, generation_id, *_key(unit)),
        )
        if pointer[0] != generation_id:
            _hint_readers(conn, unit, deferred.now)
    _record_reads(conn, selection)
    effective = kept if kept is not None else generation_id
    # As in `complete`: a cutoff or a reuse whose replay moved live rows in A
    # tells dependents through the kind's replay epoch. The building row says
    # whether any A of this unit moved them, a killed earlier one included.
    replayed = (kept is not None or (existing and unit.stage in {"project", "link"})) and (
        selection.witness is None
        or selection.witness != deferred.written
        or building_moved(conn, unit, generation_id)
    )
    _validate_dependencies(conn, selection)
    _certify(conn, selection, effective, now=deferred.now)
    if replayed:
        bump_replay(conn, unit.stage, unit.unit_kind, now=deferred.now, unit_id=unit.unit_id)
    conn.execute("DELETE FROM derivation_building WHERE generation_id=?", (generation_id,))
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
    if unit.stage in {"project", "link"} and building(conn, unit):
        # A staged A committed live rows its pointer does not stand for,
        # possibly for inputs since reverted: the unit runs until a C (and
        # `collect_orphans`) settles them (F8, I6).
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


def _certify(
    conn: sqlite3.Connection,
    selection: Selection,
    generation_id: str,
    *,
    now: datetime | str | None = None,
) -> None:
    """Record the fast-path proof for the id the scope now stands for.

    After a cutoff that is the kept id, not the selection's provisional one.
    """
    from .derivation_signatures import certificate, materialized_proof

    _fence(conn, selection, "consistency group changed selected derivation inputs")
    signature = certificate(
        conn, selection.unit, selection.recipe, selection.context, reads=selection.reads
    )
    proof, proof_id = materialized_proof(generation_id, signature)
    store_set(conn, proof_id, proof, created_at=_at(now))
    conn.execute(
        "UPDATE derivation_scopes SET materialized_signature=? WHERE stage=? AND unit_kind=? AND unit_id=? AND materialized_generation_id=?",
        (proof_id, *_key(selection.unit), generation_id),
    )


def _artifact_valid(conn: sqlite3.Connection, generation: str) -> bool:
    from swingset.publish.safety import StaleCandidateError, verify_candidate_files

    try:
        artifacts = [
            payload
            for table, _, payload in output_packs.rows(conn, generation)
            if table == "artifact"
        ]
    except output_packs.GenerationUnavailable:
        return False
    if not artifacts:
        return False
    try:
        for artifact in artifacts:
            payload = json.loads(artifact)
            path = Path(payload["path"])
            verify_candidate_files(path)
            built = json.loads((path / "BUILT").read_bytes())
            if built["manifest_hash"] != payload["manifest_hash"]:
                return False
            policy = json.loads((path / "_meta/manifest.json").read_bytes()).get("release_policy")
            if isinstance(policy, dict) and policy.get("mode") == "partitioned":
                # The release manifest file is part of what the build stands
                # for; candidates live at `<state>/candidates/<id>` (D-0224).
                from .retention import manifest_file

                digest_value = str(policy["manifest_digest"])
                body = manifest_file(path.parent.parent, digest_value).read_bytes()
                if hashlib.sha256(body).hexdigest() != digest_value:
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
    from .derivation_dependencies import retired
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
            if retired(unit):
                continue
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
            if unit not in seen and not retired(unit):
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


#: A pacing gate's own answer, set by `schedule/pacing.configured`: whether it
#: holds a unit on purpose (the inventory within its interval), and its clock.
_paced: ContextVar[
    tuple[Callable[[sqlite3.Connection, WorkUnit], bool], Callable[[], datetime]] | None
] = ContextVar("derivation_pacing_gate", default=None)
#: Prerequisites readiness took as settled at their pointer, by reason.
_settled: ContextVar[dict[str, set[str]] | None] = ContextVar("derivation_settled", default=None)


@contextmanager
def pacing_gate(
    holds: Callable[[sqlite3.Connection, WorkUnit], bool], now: Callable[[], datetime]
) -> Iterator[None]:
    """Let readiness take a unit the pacing gate holds as settled, for the block."""
    token = _paced.set((holds, now))
    try:
        yield
    finally:
        _paced.reset(token)


@contextmanager
def counting_settled() -> Iterator[dict[str, set[str]]]:
    """Collect, by reason, the prerequisites readiness takes as settled in the block."""
    counts: dict[str, set[str]] = {}
    token = _settled.set(counts)
    try:
        yield counts
    finally:
        _settled.reset(token)


def settled_reason(conn: sqlite3.Connection, unit: WorkUnit) -> str | None:
    """Why a prerequisite that is not current is settled at its pointer, or None.

    A unit that is quarantined, waits for input or backs off to a later
    retry (its `work_queue` row), or that a pacing gate holds on purpose,
    stands at its current pointer (or its absence) until it runs. Its
    dependents read that pointer and run; they re-run when it moves. Without
    this one failing dancer froze the inventory, and through it every year
    and link that needed to run (I1). The queue and the watchdog report the
    failing unit itself.
    """
    paced = _paced.get()
    try:
        row = conn.execute(
            "SELECT state,next_attempt_at FROM work_queue WHERE stage=? AND unit_kind=? AND unit_id=?",
            _key(unit),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        row = None
    if row is not None:
        state, next_attempt = str(row[0]), row[1]
        if state in {"quarantined", "waiting_input"}:
            return state
        if state == "ready" and next_attempt is not None:
            from .queue import at

            moment = paced[1]() if paced is not None else datetime.now(UTC)
            if str(next_attempt) > at(moment):
                return "backing_off"
    if paced is not None and paced[0](conn, unit):
        return "paced"
    return None


def _counted_settled(conn: sqlite3.Connection, unit: WorkUnit) -> bool:
    reason = settled_reason(conn, unit)
    if reason is None:
        return False
    counts = _settled.get()
    if counts is not None:
        counts.setdefault(reason, set()).add("/".join(_key(unit)))
    return True


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
    from .derivation_dependencies import prerequisites, waits_for

    active = _group.get()
    global_kinds = (
        ("calendar", "source_index", "dancer")
        if unit.unit_kind == "inventory"
        # A year waits for the event list to exist, then for its own
        # prerequisites (plan F17). A link that never ran has no read set
        # yet, so it waits for every dancer to exist (plan F20).
        else (
            ("calendar", "source_index", "inventory")
            if unit.stage == "project" and unit.unit_kind == "year"
            else (
                ("dancer",)
                if unit.stage == "link" and selected_generation(conn, unit) is None
                else ()
            )
        )
    )
    if active is None and global_kinds:
        placeholders = ",".join("?" for _ in global_kinds)
        # A unit that never materialized and is quarantined or waits stands
        # absent (`settled_reason`); any other one holds the unit back.
        if any(
            not _counted_settled(conn, WorkUnit("project", str(kind), str(identifier)))
            for kind, identifier in conn.execute(
                f"SELECT unit_kind,unit_id FROM derivation_scopes WHERE stage='project' "
                f"AND unit_kind IN ({placeholders}) AND materialized_generation_id IS NULL",
                global_kinds,
            )
        ):
            return False
    # A link's prerequisites are its event, its resolution and the dancers
    # its read set names, never the registry or a year (plan F20). A
    # prerequisite held on purpose or failing is settled at its pointer.
    dependencies = (*prerequisites(conn, unit), *waits_for(conn, unit))
    return all(
        (active is not None and active.connection is conn and dependency in active.selected)
        or current(conn, dependency)
        or _counted_settled(conn, dependency)
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
