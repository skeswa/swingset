"""Parse and build-recipe bodies in packs (architecture phase 4, WP4).

A source generation's body is its `manifest_json`, `report_json` and
`result_json`. Packed, it is one `parse_body` object whose raw bytes are
`canonical({"manifest": m, "report": r, "result": s})` of the three column
strings, so rows with the same three strings share one object. The row then
holds the stub `{"packed":true}` in all three columns and a
`source_generation_packs` row names the object. `recipe_json` is never packed:
parse-scope code (`admission/support.py`) reads it for every generation.

A build label's `recipe_json` over `LABEL_MIN_BYTES` is packed the same way, as
a `label_body` object holding the column's bytes, named by `label_body_packs`.

Packing and restoring change rows only under `storage_eviction_authority`
(migration 36's triggers allow exactly these two moves). Restoring checks the
object's sha256 against the pack row before it writes anything back.

**Protected rows are never packed.** Parse-scope modules cannot be edited, so
the protected set covers every row they read (F35):

- `state`: every state other than `accepted` and `superseded`;
- `accepted`: each unit's accepted generation;
- `newest`: each unit's newest generation by `(created_at, generation_id)`;
- `shadow`: every generation with no admission decision;
- `chain`: the predecessor chain from each accepted generation back to and
  including the previous `accepted` one, which a revocation restores;
- `equivalence`: generations a `source_equivalences` row names;
- `finding`: generations an open finding names;
- `review`: generations an admission review's cohort names, which
  `activate_contract` hashes again;
- `event`: generations the kept `event_*` subsystem reads: every event
  enumeration's own generation, the keys of its `parent_support_json`, and
  the `generation_id`s in its members' `support_json` (event coverage and
  event retirement decode them again);
- `candidate`: generations a retained closure-mode candidate's release
  closure names (the caller passes them);
- `young`: anything created on or after the cutoff (`MIN_AGE`).

A build label is protected while a pointer or a building row names it, while a
retained candidate's artifact row is in its output (the caller passes those),
or while it is young.

Readers outside parse scopes resolve a stub through `source_body`, `resolve`
or `recipe`. Parse-scope readers of a stub fail closed: the stub is not a
triple a parse produces, has no `failures`, `observations` or manifest list,
and breaks the generation's content-addressed id (`tests/test_body_packs.py`).
"""

from __future__ import annotations

import codecs
import hashlib
import json
import sqlite3
import zlib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import objects
from .objects import ObjectUnavailable, Store

PARSE = "parse_body"
LABEL = "label_body"
#: What a packed column holds (migration 36's triggers name it).
STUB = objects.STUB
#: The packed columns of a source generation, in canonical key order.
COLUMNS = ("manifest_json", "report_json", "result_json")
#: A build recipe is packed only above this many bytes.
LABEL_MIN_BYTES = 1_000_000
#: Rows created on or after `day(now) - MIN_AGE` stay in the file.
MIN_AGE = timedelta(days=7)
#: Why a source generation stays, in the order the rules are applied.
SOURCE_RULES = (
    "state",
    "accepted",
    "newest",
    "shadow",
    "chain",
    "equivalence",
    "finding",
    "review",
    "event",
    "candidate",
    "young",
)
#: Why a build label stays.
LABEL_RULES = ("pointer", "building", "candidate", "young")

_PREFIX = '{"manifest":'


class BodyUnavailable(RuntimeError):
    """A packed body's object is missing, damaged, or not the one its row names."""


class BodyPacked(RuntimeError):
    """An operation would read a packed body that only a restore brings back."""


@dataclass(frozen=True)
class SourceBody:
    """The three packed columns of one source generation, as stored."""

    manifest_json: str
    report_json: str
    result_json: str

    def columns(self) -> dict[str, str]:
        return {
            "manifest_json": self.manifest_json,
            "report_json": self.report_json,
            "result_json": self.result_json,
        }


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def available(conn: sqlite3.Connection) -> bool:
    """Whether this schema can pack bodies (36 and later)."""
    return _has_table(conn, "source_generation_packs")


def is_stub(value: object) -> bool:
    return value == STUB


def hint(generation_id: str) -> str:
    return f"run `swingset storage restore-body {generation_id}` first"


def cutoff(now: datetime) -> str:
    """The `young` boundary: the start of `now`'s UTC day, less `MIN_AGE`.

    Whole days keep a plan's digest stable from plan to apply on one day.
    """
    day = objects.stamp(now)[:10]
    return (datetime.fromisoformat(day + "T00:00:00+00:00") - MIN_AGE).isoformat()


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def encode_source(manifest_json: str, report_json: str, result_json: str) -> bytes:
    return _canonical(
        {"manifest": manifest_json, "report": report_json, "result": result_json}
    ).encode()


def decode_source(raw: bytes, digest: str) -> SourceBody:
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise BodyUnavailable(f"{PARSE}/{digest} is not a parse body") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"manifest", "report", "result"}
        or not all(isinstance(item, str) for item in value.values())
    ):
        raise BodyUnavailable(f"{PARSE}/{digest} is not a parse body")
    return SourceBody(value["manifest"], value["report"], value["result"])


def _read(store: Store, cls: str, digest: str) -> bytes:
    """The whole object, checked against its digest."""
    hasher = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        with store.open(cls, digest) as handle:
            while chunk := handle.read(objects.CHUNK_BYTES):
                hasher.update(chunk)
                chunks.append(chunk)
    except ObjectUnavailable as error:
        raise BodyUnavailable(str(error)) from error
    except (OSError, EOFError, ValueError, zlib.error) as error:
        raise BodyUnavailable(f"{cls}/{digest} is unreadable: {error}") from error
    if hasher.hexdigest() != digest:
        raise BodyUnavailable(f"{cls}/{digest} does not match its digest")
    return b"".join(chunks)


def pack_manifest(store: Store, digest: str) -> str:
    """The `manifest_json` string of a parse body, read from the object's start.

    Canonical keys sort `manifest` first, so this reads only as far as that
    string ends. It does not check the digest; `source_body` does.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    text = ""
    try:
        with store.open(PARSE, digest) as handle:
            while True:
                chunk = handle.read(1 << 16)
                text += decoder.decode(chunk, final=not chunk)
                if len(text) >= len(_PREFIX) and not text.startswith(_PREFIX):
                    break
                try:
                    value, _ = json.JSONDecoder().raw_decode(text, len(_PREFIX))
                except ValueError:
                    if not chunk:
                        break
                    continue
                if isinstance(value, str):
                    return value
                break
    except ObjectUnavailable as error:
        raise BodyUnavailable(str(error)) from error
    except (OSError, EOFError, ValueError, zlib.error) as error:
        raise BodyUnavailable(f"{PARSE}/{digest} is unreadable: {error}") from error
    raise BodyUnavailable(f"{PARSE}/{digest} is not a parse body")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def source_pack(conn: sqlite3.Connection, generation_id: str) -> str | None:
    if not available(conn):
        return None
    found = conn.execute(
        "SELECT pack_digest FROM source_generation_packs WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    return None if found is None else str(found[0])


def packed_body(
    conn: sqlite3.Connection, generation_id: str, *, store: Store | None = None
) -> SourceBody:
    """A packed row's body from its object, verified; raises `BodyUnavailable`."""
    digest = source_pack(conn, generation_id)
    if digest is None:
        raise BodyUnavailable(f"source generation {generation_id} has no pack row")
    return decode_source(_read(store or objects.store_for(conn), PARSE, digest), digest)


def source_body(
    conn: sqlite3.Connection, generation_id: str, *, store: Store | None = None
) -> SourceBody | None:
    """The three body columns as written, from the row or its pack; None if no row."""
    row = conn.execute(
        "SELECT manifest_json,report_json,result_json FROM source_generations "
        "WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    if row is None:
        return None
    values = tuple(str(value) for value in row)
    if not any(is_stub(value) for value in values):
        return SourceBody(*values)
    if not all(is_stub(value) for value in values):
        raise BodyUnavailable(f"source generation {generation_id} is partly packed")
    return packed_body(conn, generation_id, store=store)


def resolve(conn: sqlite3.Connection, generation_id: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """`row` as a dict, with any stubbed body column replaced from the pack."""
    value = dict(row)
    if any(is_stub(value.get(name)) for name in COLUMNS):
        body = packed_body(conn, generation_id).columns()
        value.update({name: body[name] for name in COLUMNS if name in value})
    return value


def label_pack(conn: sqlite3.Connection, generation_id: str) -> str | None:
    if not available(conn):
        return None
    found = conn.execute(
        "SELECT pack_digest FROM label_body_packs WHERE generation_id=?", (generation_id,)
    ).fetchone()
    return None if found is None else str(found[0])


def recipe(conn: sqlite3.Connection, generation_id: str, *, store: Store | None = None) -> str:
    """A derivation label's `recipe_json` as written, from the row or its pack."""
    row = conn.execute(
        "SELECT recipe_json FROM derivation_generations WHERE generation_id=?", (generation_id,)
    ).fetchone()
    if row is None:
        raise BodyUnavailable(f"no label for generation {generation_id}")
    if not is_stub(row[0]):
        return str(row[0])
    digest = label_pack(conn, generation_id)
    if digest is None:
        raise BodyUnavailable(f"build label {generation_id} has no pack row")
    try:
        return _read(store or objects.store_for(conn), LABEL, digest).decode()
    except UnicodeDecodeError as error:
        raise BodyUnavailable(f"{LABEL}/{digest} is not text") from error


def revocation_blockers(conn: sqlite3.Connection, generation_id: str) -> list[str]:
    """Packed generations a revocation of `generation_id` would read.

    `admission.select.revoke_generation` reads the predecessor chain back to
    the previous accepted generation and that one's result.
    """
    if not available(conn):
        return []
    row = conn.execute(
        "SELECT g.previous_generation_id,u.accepted_generation_id FROM source_generations g "
        "JOIN source_units u USING(unit_key) WHERE g.generation_id=?",
        (generation_id,),
    ).fetchone()
    if row is None or row[1] != generation_id:
        return []
    found = []
    predecessor = row[0]
    seen: set[str] = set()
    while predecessor is not None and predecessor not in seen:
        seen.add(str(predecessor))
        candidate = conn.execute(
            "SELECT state,previous_generation_id,result_json FROM source_generations "
            "WHERE generation_id=?",
            (predecessor,),
        ).fetchone()
        if candidate is None:
            break
        if candidate[0] == "accepted":
            if is_stub(candidate[2]):
                found.append(str(predecessor))
            break
        predecessor = candidate[1]
    return found


def check_revocation(conn: sqlite3.Connection, generation_id: str) -> None:
    """Raise `BodyPacked` with the restore hint when a revocation would read a pack."""
    blockers = revocation_blockers(conn, generation_id)
    if blockers:
        raise BodyPacked(
            f"revoking {generation_id} restores {blockers[0]}, whose body is packed; "
            + hint(blockers[0])
        )


# ---------------------------------------------------------------------------
# Protected sets
# ---------------------------------------------------------------------------


def _fill(
    conn: sqlite3.Connection, table: str, rules: Iterable[tuple[str, str, tuple[Any, ...]]]
) -> None:
    conn.execute(f"DROP TABLE IF EXISTS temp.{table}")
    conn.execute(
        f"CREATE TEMP TABLE {table}(generation_id TEXT PRIMARY KEY, why TEXT NOT NULL) "
        "WITHOUT ROWID"
    )
    for why, select, parameters in rules:
        conn.execute(
            f"INSERT OR IGNORE INTO temp.{table}(generation_id,why) "
            f"SELECT generation_id,'{why}' FROM ({select}) WHERE generation_id IS NOT NULL",
            parameters,
        )


def _candidate_table(conn: sqlite3.Connection, table: str, generation_ids: Iterable[str]) -> None:
    conn.execute(f"DROP TABLE IF EXISTS temp.{table}")
    conn.execute(f"CREATE TEMP TABLE {table}(generation_id TEXT PRIMARY KEY) WITHOUT ROWID")
    conn.executemany(
        f"INSERT OR IGNORE INTO temp.{table} VALUES (?)", ((str(g),) for g in generation_ids)
    )


#: The predecessor chain revocation walks, from each accepted generation.
_CHAIN = (
    "WITH RECURSIVE chain(generation_id,state) AS ("
    "SELECT p.generation_id,p.state FROM source_units u "
    "JOIN source_generations a ON a.generation_id=u.accepted_generation_id "
    "JOIN source_generations p ON p.generation_id=a.previous_generation_id "
    "UNION SELECT p.generation_id,p.state FROM chain c "
    "JOIN source_generations g ON g.generation_id=c.generation_id "
    "JOIN source_generations p ON p.generation_id=g.previous_generation_id "
    "WHERE c.state<>'accepted') SELECT generation_id FROM chain"
)


#: Generations the `event_*` subsystem reads (D-0204 keeps its rows; open
#: owner decision 2 keeps the subsystem): enumerations, their parents and
#: their members' claims.
_EVENT = (
    "SELECT generation_id FROM source_event_enumerations "
    "UNION SELECT j.key FROM source_event_enumerations e, json_each(e.parent_support_json) j "
    "WHERE json_valid(e.parent_support_json) AND json_type(e.parent_support_json)='object' "
    "UNION SELECT j.value FROM source_event_enumeration_members m, json_tree(m.support_json) j "
    "WHERE json_valid(m.support_json) AND j.key='generation_id' AND j.type='text'"
)
#: Rows appended since a snapshot (both tables are append-only, migration 16).
_EVENT_SINCE = (
    "SELECT EXISTS(SELECT 1 FROM source_event_enumerations WHERE rowid>:e AND generation_id=:g) "
    "OR EXISTS(SELECT 1 FROM source_event_enumerations e, json_each(e.parent_support_json) j "
    "WHERE e.rowid>:e AND json_valid(e.parent_support_json) "
    "AND json_type(e.parent_support_json)='object' AND j.key=:g) "
    "OR EXISTS(SELECT 1 FROM source_event_enumeration_members m, json_tree(m.support_json) j "
    "WHERE m.rowid>:m AND json_valid(m.support_json) AND j.key='generation_id' "
    "AND j.type='text' AND j.value=:g)"
)


@dataclass(frozen=True)
class EventReads:
    """The `event` rule's generations at one moment, and where the two
    append-only tables ended then, so a later point check reads only rows
    added since."""

    generations: frozenset[str]
    enumerations: int
    members: int


def event_reads(conn: sqlite3.Connection) -> EventReads:
    """The `event` rule's whole set, in the caller's read transaction."""
    enumerations, members = conn.execute(
        "SELECT (SELECT coalesce(max(rowid),0) FROM source_event_enumerations),"
        "(SELECT coalesce(max(rowid),0) FROM source_event_enumeration_members)"
    ).fetchone()
    return EventReads(
        frozenset(str(row[0]) for row in conn.execute(_EVENT) if row[0] is not None),
        int(enumerations),
        int(members),
    )


def event_protected(conn: sqlite3.Connection, generation_id: str, reads: EventReads) -> bool:
    """Whether the `event` rule holds now: in the snapshot or in a row added since."""
    if generation_id in reads.generations:
        return True
    found = conn.execute(
        _EVENT_SINCE, {"g": generation_id, "e": reads.enumerations, "m": reads.members}
    ).fetchone()
    return bool(found[0])


def protect_sources(
    conn: sqlite3.Connection, *, before: str, candidates: Iterable[str] = ()
) -> None:
    """Fill `temp.body_protected(generation_id, why)` with every protected source generation.

    `before` is the `young` boundary (`cutoff`); `candidates` are the source
    generations retained closure-mode candidates name. Each row keeps the
    first rule of `SOURCE_RULES` that holds.
    """
    _candidate_table(conn, "body_candidate_sources", candidates)
    rules: list[tuple[str, str, tuple[Any, ...]]] = [
        (
            "state",
            "SELECT generation_id FROM source_generations "
            "WHERE state NOT IN ('accepted','superseded')",
            (),
        ),
        ("accepted", "SELECT accepted_generation_id AS generation_id FROM source_units", ()),
        (
            "newest",
            "SELECT (SELECT g.generation_id FROM source_generations g WHERE g.unit_key=u.unit_key "
            "ORDER BY g.created_at DESC,g.generation_id DESC LIMIT 1) AS generation_id "
            "FROM source_units u",
            (),
        ),
        (
            "shadow",
            "SELECT g.generation_id FROM source_generations g WHERE NOT EXISTS("
            "SELECT 1 FROM admission_decisions d WHERE d.generation_id=g.generation_id)",
            (),
        ),
        ("chain", _CHAIN, ()),
        ("equivalence", "SELECT DISTINCT generation_id FROM source_equivalences", ()),
        (
            "finding",
            "SELECT g.generation_id FROM finding_support_references r "
            "JOIN findings f USING(finding_id) "
            "JOIN source_generations g ON g.generation_id=r.sha256 "
            "WHERE f.closed_at IS NULL AND r.kind='generation'",
            (),
        ),
        (
            "review",
            "SELECT j.value AS generation_id FROM admission_reviews r, json_each(r.cohort_json) j "
            "WHERE json_valid(r.cohort_json) AND json_type(r.cohort_json)='array' "
            "AND j.type='text'",
            (),
        ),
        ("event", _EVENT, ()),
        (
            "candidate",
            "SELECT c.generation_id FROM temp.body_candidate_sources c "
            "JOIN source_generations g USING(generation_id)",
            (),
        ),
        (
            "young",
            "SELECT generation_id FROM source_generations "
            "WHERE NOT julianday(created_at)<julianday(?)",
            (before,),
        ),
    ]
    _fill(conn, "body_protected", rules)


def source_targets(
    conn: sqlite3.Connection, *, unpacked: bool = False, limit: int = -1
) -> Iterator[tuple[str, bool]]:
    """Unprotected source generations `(id, packed)`, by id; after `protect_sources`."""
    for generation_id, stub in conn.execute(
        "SELECT g.generation_id,g.result_json=? AS stub FROM source_generations g "
        "WHERE NOT EXISTS(SELECT 1 FROM temp.body_protected p "
        "WHERE p.generation_id=g.generation_id) AND (NOT ? OR g.result_json<>?) "
        "ORDER BY g.generation_id LIMIT ?",
        (STUB, int(unpacked), STUB, limit),
    ):
        yield str(generation_id), bool(stub)


def source_backlog(conn: sqlite3.Connection) -> int:
    """Unprotected source generations not yet packed; after `protect_sources`."""
    return int(
        conn.execute(
            "SELECT count(*) FROM source_generations g WHERE NOT EXISTS("
            "SELECT 1 FROM temp.body_protected p WHERE p.generation_id=g.generation_id) "
            "AND g.result_json<>?",
            (STUB,),
        ).fetchone()[0]
    )


def protect_labels(
    conn: sqlite3.Connection, *, before: str, candidates: Iterable[str] = ()
) -> None:
    """Fill `temp.label_protected` with every protected build label.

    `candidates` are the build generations whose output names a retained
    candidate.
    """
    _candidate_table(conn, "body_candidate_builds", candidates)
    rules: list[tuple[str, str, tuple[Any, ...]]] = [
        (
            "pointer",
            "SELECT materialized_generation_id AS generation_id FROM derivation_scopes "
            "WHERE stage='build'",
            (),
        ),
        ("building", "SELECT generation_id FROM derivation_building WHERE stage='build'", ()),
        ("candidate", "SELECT generation_id FROM temp.body_candidate_builds", ()),
        (
            "young",
            "SELECT generation_id FROM derivation_generations "
            "WHERE stage='build' AND NOT julianday(created_at)<julianday(?)",
            (before,),
        ),
    ]
    _fill(conn, "label_protected", rules)


def label_targets(conn: sqlite3.Connection) -> Iterator[tuple[str, bool]]:
    """Unprotected large build labels `(id, packed)`, by id; after `protect_labels`."""
    for generation_id, stub in conn.execute(
        "SELECT g.generation_id,g.recipe_json=? FROM derivation_generations g "
        "WHERE g.stage='build' AND (octet_length(g.recipe_json)>? OR EXISTS("
        "SELECT 1 FROM label_body_packs p WHERE p.generation_id=g.generation_id)) "
        "AND NOT EXISTS(SELECT 1 FROM temp.label_protected p "
        "WHERE p.generation_id=g.generation_id) ORDER BY g.generation_id",
        (STUB, LABEL_MIN_BYTES),
    ):
        yield str(generation_id), bool(stub)


def drop_temporary(conn: sqlite3.Connection) -> None:
    for name in (
        "body_protected",
        "label_protected",
        "body_candidate_sources",
        "body_candidate_builds",
    ):
        conn.execute(f"DROP TABLE IF EXISTS temp.{name}")


def source_protection(
    conn: sqlite3.Connection,
    generation_id: str,
    *,
    before: str,
    candidates: frozenset[str],
    events: EventReads | None = None,
) -> str | None:
    """The first rule that protects one source generation now, or None.

    The same rules as `protect_sources`, as point queries, for a writer that
    rechecks a row inside the transaction that packs it. `events` is the
    `event` rule's snapshot (`event_reads`); without it the whole set is read.
    """
    row = conn.execute(
        "SELECT g.state,g.unit_key,u.accepted_generation_id,"
        "NOT julianday(g.created_at)<julianday(?) FROM source_generations g "
        "JOIN source_units u USING(unit_key) WHERE g.generation_id=?",
        (before, generation_id),
    ).fetchone()
    if row is None:
        return "state"
    state, unit_key, accepted, young = row
    if state not in ("accepted", "superseded"):
        return "state"
    if accepted == generation_id:
        return "accepted"
    newest = conn.execute(
        "SELECT generation_id FROM source_generations WHERE unit_key=? "
        "ORDER BY created_at DESC,generation_id DESC LIMIT 1",
        (unit_key,),
    ).fetchone()
    if newest is not None and newest[0] == generation_id:
        return "newest"
    checks = (
        (
            "shadow",
            "SELECT NOT EXISTS(SELECT 1 FROM admission_decisions WHERE generation_id=?)",
        ),
        ("equivalence", "SELECT EXISTS(SELECT 1 FROM source_equivalences WHERE generation_id=?)"),
        (
            "finding",
            "SELECT EXISTS(SELECT 1 FROM finding_support_references r JOIN findings f "
            "USING(finding_id) WHERE r.kind='generation' AND r.sha256=? AND f.closed_at IS NULL)",
        ),
        (
            "review",
            "SELECT EXISTS(SELECT 1 FROM admission_reviews r, json_each(r.cohort_json) j "
            "WHERE json_valid(r.cohort_json) AND json_type(r.cohort_json)='array' "
            "AND j.value=?)",
        ),
    )
    for why, sql in checks[:1]:
        if conn.execute(sql, (generation_id,)).fetchone()[0]:
            return why
    if accepted is not None and _in_chain(conn, str(accepted), generation_id):
        return "chain"
    for why, sql in checks[1:]:
        if conn.execute(sql, (generation_id,)).fetchone()[0]:
            return why
    if event_protected(conn, generation_id, events if events is not None else event_reads(conn)):
        return "event"
    if generation_id in candidates:
        return "candidate"
    if young:
        return "young"
    return None


def _in_chain(conn: sqlite3.Connection, accepted: str, generation_id: str) -> bool:
    row = conn.execute(
        "SELECT previous_generation_id FROM source_generations WHERE generation_id=?", (accepted,)
    ).fetchone()
    predecessor = None if row is None else row[0]
    seen: set[str] = set()
    while predecessor is not None and predecessor not in seen:
        if predecessor == generation_id:
            return True
        seen.add(str(predecessor))
        found = conn.execute(
            "SELECT state,previous_generation_id FROM source_generations WHERE generation_id=?",
            (predecessor,),
        ).fetchone()
        if found is None or found[0] == "accepted":
            return False
        predecessor = found[1]
    return False


def label_protection(
    conn: sqlite3.Connection, generation_id: str, *, before: str, candidates: frozenset[str]
) -> str | None:
    row = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM derivation_scopes WHERE materialized_generation_id=?),"
        "EXISTS(SELECT 1 FROM derivation_building WHERE generation_id=?),"
        "NOT julianday(created_at)<julianday(?) FROM derivation_generations "
        "WHERE generation_id=? AND stage='build'",
        (generation_id, generation_id, before, generation_id),
    ).fetchone()
    if row is None:
        return "pointer"
    if row[0]:
        return "pointer"
    if row[1]:
        return "building"
    if generation_id in candidates:
        return "candidate"
    if row[2]:
        return "young"
    return None


# ---------------------------------------------------------------------------
# Packing and restoring
# ---------------------------------------------------------------------------


def source_columns(conn: sqlite3.Connection, generation_id: str) -> tuple[str, str, str]:
    row = conn.execute(
        "SELECT manifest_json,report_json,result_json FROM source_generations "
        "WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    if row is None:
        raise BodyUnavailable(f"no source generation {generation_id}")
    values = (str(row[0]), str(row[1]), str(row[2]))
    if any(is_stub(value) for value in values):
        raise BodyUnavailable(f"source generation {generation_id} is already packed")
    return values


def _indexed(conn: sqlite3.Connection, store: Store, cls: str, digest: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM objects WHERE class=? AND digest=?", (cls, digest)
    ).fetchone() is not None and store.exists(cls, digest)


def write_source(
    conn: sqlite3.Connection, store: Store, generation_id: str, *, now: datetime
) -> tuple[str, bool]:
    """Write a source row's parse body object and its `objects` row.

    Returns the digest and whether a new object was written; an indexed
    object whose file is there is kept. The row is not changed.
    """
    raw = encode_source(*source_columns(conn, generation_id))
    digest = hashlib.sha256(raw).hexdigest()
    if _indexed(conn, store, PARSE, digest):
        return digest, False
    written, stored = store.put_bytes(PARSE, raw)
    if written != digest:
        raise BodyUnavailable(f"parse body of {generation_id} was not stored under its digest")
    objects.record(conn, PARSE, digest, len(raw), stored, now)
    return digest, True


def _grant(conn: sqlite3.Connection, reason: str, now: datetime) -> None:
    conn.execute(
        "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) VALUES (1,?,?)",
        (reason, objects.stamp(now)),
    )


def _revoke(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM storage_eviction_authority")


def stub_source(
    conn: sqlite3.Connection, generation_id: str, digest: str, *, now: datetime
) -> bool:
    """In the caller's transaction: name the pack and stub the three columns.

    The row's body must still hash to `digest`, and the object must be
    indexed (written and verified by the caller). Returns False for a row
    already packed.
    """
    if not conn.in_transaction:
        raise ValueError("stubbing must share the caller's transaction")
    if source_pack(conn, generation_id) is not None:
        return False
    raw = encode_source(*source_columns(conn, generation_id))
    if hashlib.sha256(raw).hexdigest() != digest:
        raise BodyUnavailable(f"source generation {generation_id} is not parse body {digest}")
    if (
        conn.execute("SELECT 1 FROM objects WHERE class=? AND digest=?", (PARSE, digest)).fetchone()
        is None
    ):
        raise BodyUnavailable(f"{PARSE}/{digest} is not indexed")
    stamp = objects.stamp(now)
    conn.execute(
        "INSERT INTO source_generation_packs(generation_id,pack_digest,packed_at) VALUES (?,?,?)",
        (generation_id, digest, stamp),
    )
    _grant(conn, "pack body " + generation_id, now)
    try:
        conn.execute(
            "UPDATE source_generations SET manifest_json=?,report_json=?,result_json=? "
            "WHERE generation_id=?",
            (STUB, STUB, STUB, generation_id),
        )
    finally:
        _revoke(conn)
    return True


def restore_source(
    conn: sqlite3.Connection, generation_id: str, *, now: datetime, store: Store | None = None
) -> bool:
    """In the caller's transaction: write a packed row's body back; False if not packed.

    The object is checked against the pack row's digest first; the pack row
    goes once the row is whole again, so the row is exactly as before packing.
    """
    if not conn.in_transaction:
        raise ValueError("restoring must share the caller's transaction")
    digest = source_pack(conn, generation_id)
    if digest is None:
        return False
    body = packed_body(conn, generation_id, store=store)
    _grant(conn, "restore body " + generation_id, now)
    try:
        conn.execute(
            "UPDATE source_generations SET manifest_json=?,report_json=?,result_json=? "
            "WHERE generation_id=?",
            (body.manifest_json, body.report_json, body.result_json, generation_id),
        )
        conn.execute("DELETE FROM source_generation_packs WHERE generation_id=?", (generation_id,))
    finally:
        _revoke(conn)
    return True


def recipe_bytes(conn: sqlite3.Connection, generation_id: str) -> bytes:
    row = conn.execute(
        "SELECT recipe_json FROM derivation_generations WHERE generation_id=? AND stage='build'",
        (generation_id,),
    ).fetchone()
    if row is None:
        raise BodyUnavailable(f"no build label {generation_id}")
    if is_stub(row[0]):
        raise BodyUnavailable(f"build label {generation_id} is already packed")
    return str(row[0]).encode()


def write_label(
    conn: sqlite3.Connection, store: Store, generation_id: str, *, now: datetime
) -> tuple[str, bool]:
    """Write a build label's recipe as a `label_body` object and its `objects` row."""
    raw = recipe_bytes(conn, generation_id)
    digest = hashlib.sha256(raw).hexdigest()
    if _indexed(conn, store, LABEL, digest):
        return digest, False
    written, stored = store.put_bytes(LABEL, raw)
    if written != digest:
        raise BodyUnavailable(f"recipe of {generation_id} was not stored under its digest")
    objects.record(conn, LABEL, digest, len(raw), stored, now)
    return digest, True


def stub_label(conn: sqlite3.Connection, generation_id: str, digest: str, *, now: datetime) -> bool:
    """In the caller's transaction: name the pack and stub the build label's recipe."""
    if not conn.in_transaction:
        raise ValueError("stubbing must share the caller's transaction")
    if label_pack(conn, generation_id) is not None:
        return False
    if hashlib.sha256(recipe_bytes(conn, generation_id)).hexdigest() != digest:
        raise BodyUnavailable(f"build label {generation_id} is not label body {digest}")
    if (
        conn.execute("SELECT 1 FROM objects WHERE class=? AND digest=?", (LABEL, digest)).fetchone()
        is None
    ):
        raise BodyUnavailable(f"{LABEL}/{digest} is not indexed")
    conn.execute(
        "INSERT INTO label_body_packs(generation_id,pack_digest,packed_at) VALUES (?,?,?)",
        (generation_id, digest, objects.stamp(now)),
    )
    _grant(conn, "pack recipe " + generation_id, now)
    try:
        conn.execute(
            "UPDATE derivation_generations SET recipe_json=? WHERE generation_id=?",
            (STUB, generation_id),
        )
    finally:
        _revoke(conn)
    return True


def restore_label(
    conn: sqlite3.Connection, generation_id: str, *, now: datetime, store: Store | None = None
) -> bool:
    """In the caller's transaction: write a packed build recipe back; False if not packed."""
    if not conn.in_transaction:
        raise ValueError("restoring must share the caller's transaction")
    if label_pack(conn, generation_id) is None:
        return False
    text = recipe(conn, generation_id, store=store)
    _grant(conn, "restore recipe " + generation_id, now)
    try:
        conn.execute(
            "UPDATE derivation_generations SET recipe_json=? WHERE generation_id=?",
            (text, generation_id),
        )
        conn.execute("DELETE FROM label_body_packs WHERE generation_id=?", (generation_id,))
    finally:
        _revoke(conn)
    return True


def restore(conn: sqlite3.Connection, generation_id: str, *, now: datetime) -> str | None:
    """Restore whichever kind of row `generation_id` names; returns the kind or None."""
    if restore_source(conn, generation_id, now=now):
        return "source"
    if restore_label(conn, generation_id, now=now):
        return "label"
    return None
