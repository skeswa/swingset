"""Partition artifacts: stage 1 of a partitioned release (plan phase 3, WP6).

Selection reads current pointers only (`derivation_scopes`), never live
rows and never a dependency walk. Building generations are left out. Three
partition kinds:

- `event/<E>` holds the `events` row merged from the current calendar,
  inventory and `resolution/E` generations in `closure_rows.reconstruct`'s
  rank order and merge rule, the year's tier patch, the `event/E`
  generation's rows, and `link/E`'s patch and identity rows, plus
  `resolution/E`'s `source_event_map` rows. A row of `event/E` that names
  another event F (the `support_closure._EventScopes` rule) joins them into
  one partition `event/<E>+<F>`. When no admissible header row remains
  because each legacy one differs from the baseline, the baseline's own
  `events` row stands in (`closure_rows._baseline_header`, D-0251), so the
  event's accepted results still publish; an event the baseline lacks is
  withheld.
- `dancers/<k>`, `k = wsdc_id // 512`: dancer and registry placement rows,
  each placement's `event_id` patched from the year generation of its month.
  A dancer generation joins the batch of the WSDC id its rows carry, which
  is its scope id except after a lookup redirect, so the year's patch of a
  placement and the placement meet in one batch.
- `year/<Y>`: the year generation's `coverage` rows.

An event partition is built only from generations that are consistent with
each other: its event, resolution and link units must all be current, so the
link's identity rows were computed from that event generation and its
dancers. When one is not (derive has not caught up), the partition keeps the
artifact it last built, which was consistent when built, and its row records
`kept_not_current_since` (cleared by the next rebuild); with none, it is
withheld (`units_not_current`) until derive catches up. A listing that two
event partitions claim at once (a map move built mid-drain) stays only with
the partition whose units are current, else with the event the live map
names; the other is withheld (`listing_claimed_elsewhere`) under a digest
no reuse matches, so the claim is contested again every build.

A partition's inputs digest covers what it reads: `(scope, generation_id,
output_digest)` of each generation it reads whole, `(scope, table, key,
payload sha256)` of each row it takes from a generation many partitions share
(calendar, inventory, year), the observation sets each generation last
recorded reading (`_read_set`), the support of every snapshot its rows name
(state and accepted source generations), the revocation digest, the build
code and environment, the history start, and the baseline when a row's
support is legacy. An unchanged digest keeps the artifact. Processing columns
(`run_id`, `first_seen_at`, `last_seen_at`, `asserted_at`) are not facts: a
kept artifact keeps those of the build that last changed its facts.

Each partition is checked alone (`partition_checks`); a failure withholds
that partition, with its reason in `release_partitions`, and nothing else.
So does any exception while one partition is selected, extracted, checked or
written (`partition_error`, with its class and message): one malformed row
never fails the release (I1). A prior artifact is reused or kept only when
every file matches the sha256 its sidecar records (`artifact_store.intact`);
a damaged one is rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import resource
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from swingset.log import log
from swingset.state import output_packs, release_store
from swingset.state.output_packs import GenerationUnavailable
from swingset.state.release_store import ArtifactRow, PartitionRow

from . import artifact_store
from .builder import PUBLISHED_TABLES
from .closure_manifest import canonical, digest
from .closure_rows import (
    _NEUTRAL,
    _baseline_header,
    _corrected_status,
    _header_pair,
    _legacy_matches,
)
from .input import _convert
from .partition_checks import (
    Observation,
    SupportIndex,
    dangling,
    live_observations,
    release_checks,
    support_inputs,
)
from .schema import PRIMARY_KEYS, SCHEMAS
from .support_closure import _EventScopes

FORMAT = "partition-artifact-v1"
#: Dancers per `dancers/<k>` batch: about 64 batches for 29k dancers.
DANCER_BATCH = 512
#: Partition rows written per transaction, so one write stays short.
WRITE_BATCH = 200
#: `closure_rows.reconstruct`'s merge order: lower ranks first, a link last.
RANK = {
    "calendar": 0,
    "source_index": 0,
    "dancer": 0,
    "inventory": 1,
    "map": 2,
    "mapping": 2,
    "resolution": 2,
    "event": 3,
    "source_event": 4,
    "history": 5,
    "year": 5,
}
LINK_RANK = 6
_PROCESSING = ("run_id", "first_seen_at", "last_seen_at", "asserted_at")
#: Tables the release reads that the public schema does not publish.
INTERNAL_SCHEMAS: dict[str, pa.Schema] = {
    "source_event_map": pa.schema(
        [
            ("source", pa.string()),
            ("source_ref", pa.string()),
            ("event_id", pa.string()),
            ("match_method", pa.string()),
            ("match_confidence", pa.float64()),
        ]
    )
}
_INTERNAL_KEYS = {"source_event_map": ("source", "source_ref")}
_TABLES = {
    "event": (
        *(
            name
            for name in PUBLISHED_TABLES
            if name
            not in {
                "dancers",
                "registry_placements",
                "review_queue",
                "changelog",
                "snapshots",
                "coverage",
            }
        ),
        "source_event_map",
    ),
    "dancers": ("dancers", "registry_placements"),
    "year": ("coverage",),
}
_IDENTITY_TABLES = ("identity_links", "link_candidates", "identity_link_resolutions")
_MODULES = ("partitions.py", "partition_checks.py", "artifact_store.py")


@dataclass(frozen=True)
class Generation:
    stage: str
    unit_kind: str
    unit_id: str
    generation_id: str
    output_digest: str
    row_count: int
    created_at: str
    run_id: str
    #: A link's recorded read of its event: the event generation its identity
    #: rows were computed from (plan F20), part of the partition's content.
    reads_event: str | None = None

    @property
    def scope(self) -> str:
        return f"{self.stage}/{self.unit_kind}/{self.unit_id}"

    @property
    def unit(self) -> Any:
        from swingset.state.work import WorkUnit

        return WorkUnit(self.stage, self.unit_kind, self.unit_id)

    @property
    def order(self) -> tuple[int, str, str]:
        rank = LINK_RANK if self.stage == "link" else RANK.get(self.unit_kind, 3)
        return rank, self.created_at, self.unit_id


@dataclass(frozen=True)
class SharedRow:
    """One row a partition takes from a generation many partitions share."""

    generation: Generation
    table: str
    key: str
    payload_sha256: str


@dataclass
class Partition:
    name: str
    kind: str
    generations: list[Generation] = field(default_factory=list)
    shared: list[SharedRow] = field(default_factory=list)
    events: tuple[str, ...] = ()
    #: Generations whose rows selection could not read (scope, class,
    #: message): the partition is withheld as `partition_error` (I1).
    errors: list[tuple[str, str, str]] = field(default_factory=list)

    def sources(self) -> list[tuple[Generation, list[SharedRow] | None]]:
        """What to read, in merge order: a whole generation, or some of its rows."""
        items: dict[str, tuple[Generation, list[SharedRow] | None]] = {
            generation.generation_id: (generation, None) for generation in self.generations
        }
        for row in self.shared:
            generation = row.generation
            if generation.generation_id not in items:
                items[generation.generation_id] = (generation, [])
            rows = items[generation.generation_id][1]
            if rows is not None:
                rows.append(row)
        ordered = sorted(items.values(), key=lambda item: item[0].order)
        return [
            (generation, None if rows is None else sorted(rows, key=lambda r: (r.table, r.key)))
            for generation, rows in ordered
        ]

    def content(self, reads: Callable[[Generation], Sequence[str]] | None = None) -> dict[str, Any]:
        """The generation part of the inputs: every generation or row read, in merge order.

        ``reads`` gives the observation sets each generation last recorded
        reading (`_Provenance.groups`). A re-run with unchanged output keeps
        the generation but can record other observations, and support is
        matched against those, so they count too.
        """
        generations: list[list[str]] = []
        rows: list[list[str]] = []
        observations: list[list[str]] = []
        for generation, shared in self.sources():
            groups = reads(generation) if reads is not None else ()
            if groups:
                observations.append([generation.scope, generation.generation_id, *groups])
            if shared is None:
                entry = [generation.scope, generation.generation_id, generation.output_digest]
                if generation.reads_event is not None:
                    entry.append(generation.reads_event)
                generations.append(entry)
            else:
                rows.extend(
                    [generation.scope, row.table, row.key, row.payload_sha256] for row in shared
                )
        return {"generations": generations, "rows": rows, "observations": observations}

    def generation_ids(self) -> list[str]:
        return sorted(
            {g.generation_id for g in self.generations}
            | {r.generation.generation_id for r in self.shared}
        )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def pointers(conn: sqlite3.Connection) -> list[Generation]:
    """Every current project and link generation, retired and unfinished ones left out."""
    from swingset.state.derivation_dependencies import RETIRED_KINDS
    from swingset.state.derivations import unfinished_excluded

    result = []
    for row in conn.execute(
        "SELECT s.stage,s.unit_kind,s.unit_id,g.generation_id,g.output_digest,g.row_count,"
        "g.created_at,g.run_id FROM derivation_scopes s JOIN derivation_generations g "
        "ON g.generation_id=s.materialized_generation_id WHERE s.stage IN ('project','link')"
        + unfinished_excluded(conn, "g")
        + " ORDER BY s.stage,s.unit_kind,s.unit_id"
    ):
        generation = Generation(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            int(row[5]),
            str(row[6]),
            str(row[7]),
        )
        if (generation.stage, generation.unit_kind) not in RETIRED_KINDS:
            result.append(generation)
    return result


def _refs(
    conn: sqlite3.Connection,
    generation: Generation,
    tables: Iterable[str],
    evicted: frozenset[str] = frozenset(),
) -> Iterator[SharedRow]:
    """Rows of some tables of a current generation, by key, with payload digests.

    Current pointers stay resident (F34), so this is one indexed query per
    table. A current generation found evicted (an invariant broken) is read
    through the accessor instead, never taken as empty.
    """
    if generation.generation_id in evicted:
        wanted = set(tables)
        found = sorted(
            (table, key, hashlib.sha256(payload.encode()).hexdigest())
            for table, key, payload in output_packs.rows(
                conn,
                generation.generation_id,
                expected=(generation.output_digest, generation.row_count),
            )
            if table in wanted
        )
        for table in tables:
            for name, key, sha in found:
                if name == table:
                    yield SharedRow(generation, table, key, sha)
        return
    for table in tables:
        for key, sha in conn.execute(
            "SELECT record_key,payload_sha256 FROM derivation_row_refs "
            "WHERE generation_id=? AND table_name=? ORDER BY record_key",
            (generation.generation_id, table),
        ):
            yield SharedRow(generation, table, str(key), str(sha))


def _first(key: str) -> Any:
    value = json.loads(key)
    return value[0] if isinstance(value, list) and value else None


def dancer_batch(wsdc_id: Any) -> str:
    try:
        return str(int(wsdc_id) // DANCER_BATCH)
    except (TypeError, ValueError):
        return "unknown"


def _dancer_rows(
    conn: sqlite3.Connection, evicted: frozenset[str] = frozenset()
) -> dict[str, list[int]]:
    """The WSDC ids each current dancer generation's `dancers` rows carry.

    One join over resident current pointers (F34); an evicted one is read
    through the accessor.
    """
    result: dict[str, list[int]] = {}
    for generation_id, key in conn.execute(
        "SELECT r.generation_id,r.record_key FROM derivation_scopes s "
        "JOIN derivation_row_refs r ON r.generation_id=s.materialized_generation_id "
        "AND r.table_name='dancers' WHERE s.stage='project' AND s.unit_kind='dancer'"
    ):
        value = _first(str(key))
        if isinstance(value, int):
            result.setdefault(str(generation_id), []).append(value)
    for (generation_id,) in conn.execute(
        "SELECT materialized_generation_id FROM derivation_scopes "
        "WHERE stage='project' AND unit_kind='dancer' AND materialized_generation_id IS NOT NULL"
    ).fetchall():
        if str(generation_id) not in evicted:
            continue
        try:
            keys = [
                key
                for table, key, _ in output_packs.rows(conn, str(generation_id))
                if table == "dancers"
            ]
        except GenerationUnavailable:
            continue
        for key in keys:
            value = _first(key)
            if isinstance(value, int):
                result.setdefault(str(generation_id), []).append(value)
    return result


def _evicted_pointers(conn: sqlite3.Connection) -> frozenset[str]:
    """Current pointers found evicted: none while F34 holds."""
    if not output_packs.available(conn):
        return frozenset()
    return frozenset(
        str(row[0])
        for row in conn.execute(
            "SELECT s.materialized_generation_id FROM derivation_scopes s "
            "JOIN derivation_evicted e ON e.generation_id=s.materialized_generation_id"
        )
    )


def _read_set(conn: sqlite3.Connection, generation: Generation) -> str | None:
    """The dependency set a generation last recorded reading.

    A re-run whose output is unchanged keeps the generation and records what
    it read as an equivalence: the newest one names what the rows stand on
    now. Without one, the set saved with the generation.
    """
    row = conn.execute(
        "SELECT dependency_set_id FROM derivation_equivalences WHERE stage=? AND unit_kind=? "
        "AND unit_id=? AND generation_id=? ORDER BY created_at DESC LIMIT 1",
        (generation.stage, generation.unit_kind, generation.unit_id, generation.generation_id),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT dependency_set_id FROM derivation_generations WHERE generation_id=?",
            (generation.generation_id,),
        ).fetchone()
    return None if row is None else str(row[0])


def _manifest(conn: sqlite3.Connection, set_id: str | None) -> list[Any]:
    row = (
        conn.execute(
            "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
            (set_id,),
        ).fetchone()
        if set_id is not None
        else None
    )
    return list(json.loads(row[0])) if row is not None else []


def _link_event_reads(conn: sqlite3.Connection, links: Iterable[Generation]) -> dict[str, str]:
    """Each link generation's recorded event generation, from its newest record.

    The newest equivalence to the generation (a cutoff re-run) or else the
    generation itself names the event generation the link last read.
    """
    result: dict[str, str] = {}
    for generation in links:
        for item in _manifest(conn, _read_set(conn, generation)):
            if (
                isinstance(item, dict)
                and item.get("kind") == "derivation"
                and item.get("key") == ["project", "event", generation.unit_id]
            ):
                result[generation.generation_id] = str(item.get("generation_id"))
                break
    return result


def events_of(conn: sqlite3.Connection, generation: Generation) -> list[str]:
    """Every event an event generation's rows name, its own included."""
    tables: dict[str, list[Mapping[str, Any]]] = {}
    for table, _, payload in output_packs.rows(
        conn,
        generation.generation_id,
        expected=(generation.output_digest, generation.row_count),
    ):
        tables.setdefault(table, []).append(json.loads(payload))
    scopes = _EventScopes(tables)
    found = {generation.unit_id}
    for rows in tables.values():
        for row in rows:
            found |= scopes.events(row)
    return sorted(found)


def select(
    conn: sqlite3.Connection, *, known: Mapping[str, list[str]] | None = None
) -> tuple[list[Partition], dict[str, list[str]]]:
    """Every partition of the current pointers, and each event generation's events.

    ``known`` holds event lists of event generations from earlier builds; an
    event generation is immutable, so its list never changes. Every read is
    in one snapshot: a pointer that moves, and an old generation maintain
    evicts, after the pointers were read cannot leave a short reference list.
    """
    if conn.in_transaction:
        return _select(conn, known=known)
    conn.execute("BEGIN")
    try:
        return _select(conn, known=known)
    finally:
        conn.execute("ROLLBACK")


def _select(
    conn: sqlite3.Connection, *, known: Mapping[str, list[str]] | None = None
) -> tuple[list[Partition], dict[str, list[str]]]:
    known = dict(known or {})
    shared_events: dict[str, list[SharedRow]] = {}
    placements: dict[str, list[SharedRow]] = {}
    owned: dict[str, list[Generation]] = {}
    dancers: dict[str, list[Generation]] = {}
    years: dict[str, list[SharedRow]] = {}
    universe: set[str] = set()
    edges: dict[str, list[str]] = {}
    failed: dict[str, list[tuple[str, str, str]]] = {}
    links: list[Generation] = []
    #: Each listing's `source_events` rows, and the listings each event maps (D-0254).
    listings: dict[str, list[SharedRow]] = {}
    mapped: dict[str, list[str]] = {}
    evicted = _evicted_pointers(conn)
    carried = _dancer_rows(conn, evicted)
    for generation in pointers(conn):
        kind = generation.stage, generation.unit_kind
        if kind in {("project", "calendar"), ("project", "inventory")}:
            for row in _refs(conn, generation, ("events",), evicted):
                event = str(_first(row.key))
                shared_events.setdefault(event, []).append(row)
                universe.add(event)
        elif kind == ("project", "year"):
            years[generation.unit_id] = list(_refs(conn, generation, ("coverage",), evicted))
            for row in _refs(conn, generation, ("events",), evicted):
                shared_events.setdefault(str(_first(row.key)), []).append(row)
            for row in _refs(conn, generation, ("registry_placements",), evicted):
                placements.setdefault(dancer_batch(_first(row.key)), []).append(row)
        elif kind == ("project", "source_index"):
            for row in _refs(conn, generation, ("source_events",), evicted):
                listings.setdefault(row.key, []).append(row)
        elif kind == ("project", "resolution"):
            owned.setdefault(generation.unit_id, []).append(generation)
            universe.add(generation.unit_id)
            mapped.setdefault(generation.unit_id, []).extend(
                row.key for row in _refs(conn, generation, ("source_event_map",), evicted)
            )
        elif kind == ("project", "event"):
            owned.setdefault(generation.unit_id, []).append(generation)
            universe.add(generation.unit_id)
            named = known.get(generation.generation_id)
            if named is None:
                try:
                    named = events_of(conn, generation)
                except Exception as error:
                    # One malformed row fails only its partition, never the
                    # selection of the release (I1). Not remembered in
                    # ``known``: the next build reads it again.
                    message = f"{type(error).__name__}: {error}"[:300]
                    log("partition_error", scope=generation.scope, error=message)
                    failed.setdefault(generation.unit_id, []).append(
                        (generation.scope, type(error).__name__, str(error)[:300])
                    )
                    named = [generation.unit_id]
                else:
                    known[generation.generation_id] = named
            edges[generation.generation_id] = named
            universe.update(named)
        elif kind == ("link", "event"):
            links.append(generation)
        elif kind == ("project", "dancer"):
            # The batch of the WSDC id its rows carry, which the year's patch
            # of each of its placements and the release's key checks use.
            ids = carried.get(generation.generation_id)
            batch = dancer_batch(min(ids) if ids else generation.unit_id)
            dancers.setdefault(batch, []).append(generation)
    reads = _link_event_reads(conn, (item for item in links if item.unit_id in universe))
    for generation in links:
        if generation.unit_id in universe:
            owned.setdefault(generation.unit_id, []).append(
                replace(generation, reads_event=reads.get(generation.generation_id))
            )
    parent = {event: event for event in universe}

    def find(event: str) -> str:
        while parent[event] != event:
            parent[event] = parent[parent[event]]
            event = parent[event]
        return event

    for named in edges.values():
        first = find(named[0])
        for other in named[1:]:
            root = find(other)
            if root != first:
                parent[max(root, first)] = min(root, first)
                first = min(root, first)
    groups: dict[str, list[str]] = {}
    for event in sorted(universe):
        groups.setdefault(find(event), []).append(event)
    result = []
    for members in groups.values():
        partition = Partition("event/" + "+".join(members), "event", events=tuple(members))
        for event in members:
            partition.generations.extend(owned.get(event, ()))
            partition.shared.extend(shared_events.get(event, ()))
            for listing in mapped.get(event, ()):
                partition.shared.extend(listings.get(listing, ()))
            partition.errors.extend(failed.get(event, ()))
        result.append(partition)
    for batch, generations in dancers.items():
        result.append(
            Partition(f"dancers/{batch}", "dancers", list(generations), placements.get(batch, []))
        )
    for year, coverage in years.items():
        result.append(Partition(f"year/{year}", "year", [], coverage))
    result.sort(key=lambda item: item.name)
    current = {g.generation_id for items in owned.values() for g in items}
    return result, {key: value for key, value in known.items() if key in current}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class _Verifier:
    """Checks each generation read against its output digest, once per build.

    Reads go through `output_packs` (the view, or the pack of an evicted
    generation); `GenerationUnavailable` withholds the generation's partitions.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.checked: dict[str, bool] = {}
        self.evicted: dict[str, bool] = {}

    def rows(self, generation: Generation) -> Iterator[tuple[str, str, str]]:
        try:
            yield from output_packs.rows(
                self.conn,
                generation.generation_id,
                expected=(generation.output_digest, generation.row_count),
            )
        except GenerationUnavailable as error:
            self.checked[generation.generation_id] = False
            raise GenerationUnavailable(generation.scope) from error
        self.checked[generation.generation_id] = True

    def verify(self, generation: Generation) -> None:
        if generation.generation_id not in self.checked:
            for _ in self.rows(generation):
                pass
        if not self.checked[generation.generation_id]:
            raise GenerationUnavailable(generation.scope)

    def row(self, row: SharedRow) -> str:
        self.verify(row.generation)
        generation_id = row.generation.generation_id
        if generation_id not in self.evicted:
            self.evicted[generation_id] = output_packs.evicted(self.conn, generation_id)
        try:
            found = output_packs.row(
                self.conn,
                generation_id,
                row.table,
                row.key,
                is_evicted=self.evicted[generation_id],
            )
        except GenerationUnavailable as error:
            raise GenerationUnavailable(row.generation.scope) from error
        if found is None:
            raise GenerationUnavailable(row.generation.scope)
        return found


class _Legacy:
    """The published baseline's rows, loaded once and only when a legacy row needs them."""

    def __init__(self, baseline: Path | None, scratch: Path) -> None:
        self.baseline = baseline
        self.scratch = scratch
        self.spool: sqlite3.Connection | None = None
        self.available = baseline is not None
        self._directory: tempfile.TemporaryDirectory[str] | None = None

    def _load(self) -> sqlite3.Connection | None:
        if not self.available:
            return None
        if self.spool is None:
            from .closure_manifest import ClosureError
            from .closure_rows import _baseline

            self.scratch.mkdir(parents=True, exist_ok=True)
            self._directory = tempfile.TemporaryDirectory(prefix="partitions-", dir=self.scratch)
            self.spool = sqlite3.connect(Path(self._directory.name) / "baseline.sqlite")
            self.spool.execute(
                "CREATE TABLE baseline(table_name TEXT,record_key TEXT,payload TEXT,"
                "PRIMARY KEY(table_name,record_key))"
            )
            try:
                _baseline(self.spool, self.baseline)
            except (ClosureError, OSError, ValueError, KeyError):
                self.available = False
                return None
        return self.spool

    def matches(self, table: str, key: str, payload: Mapping[str, Any]) -> bool:
        spool = self._load()
        return spool is not None and _legacy_matches(spool, table, key, dict(payload))

    def header(self, key: str) -> dict[str, Any] | None:
        """The baseline's `events` row for a rejected legacy header (D-0251)."""
        spool = self._load()
        return None if spool is None else _baseline_header(spool, key)

    def close(self) -> None:
        if self.spool is not None:
            self.spool.close()
        if self._directory is not None:
            self._directory.cleanup()


_OBSERVATION_GROUPS = ("source_observations", "calendar_observations")


class _Provenance:
    """The observations each generation recorded reading, by (snapshot, parser).

    A generation's newest read set (`_read_set`) names its source
    observations (and the inventory's, every calendar's); those are what its
    rows came from. A re-run with unchanged output keeps the generation but
    may read relabelled observations (D-0241), so the set saved with the
    generation can name observations no accepted generation holds any more.
    A shared generation (a calendar, the inventory) is read once per build.
    A pair no generation of the partition names falls back to the snapshot's
    live observations, which then enter the partition's inputs digest.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._cache: dict[str, dict[tuple[str, str], frozenset[Observation]]] = {}
        self._groups: dict[str, tuple[str, ...]] = {}

    def groups(self, generation: Generation) -> tuple[str, ...]:
        """The ids of the observation sets the generation last recorded reading."""
        found = self._groups.get(generation.generation_id)
        if found is None:
            found = self._groups[generation.generation_id] = tuple(
                str(item.get("fingerprint"))
                for item in _manifest(self.conn, _read_set(self.conn, generation))
                if isinstance(item, dict)
                and item.get("kind") == "dependency_set"
                and item.get("key") in _OBSERVATION_GROUPS
            )
        return found

    def of(self, generation: Generation) -> dict[tuple[str, str], frozenset[Observation]]:
        found = self._cache.get(generation.generation_id)
        if found is not None:
            return found
        result: dict[tuple[str, str], set[Observation]] = {}
        for group in self.groups(generation):
            for leaf in _manifest(self.conn, group):
                if leaf.get("kind") != "observation":
                    continue
                scope = leaf.get("scope") or [None, None]
                result.setdefault(
                    (str(leaf.get("snapshot_id")), str(leaf.get("parser_version"))), set()
                ).add(
                    (
                        str(scope[0]),
                        str(scope[1]),
                        str(leaf.get("observation_kind")),
                        int(leaf.get("seq") or 0),
                        str(leaf.get("payload_sha256")),
                        str(leaf.get("extract_version")),
                    )
                )
        value = {pair: frozenset(keys) for pair, keys in result.items()}
        if generation.stage == "project" and generation.unit_kind in {"calendar", "inventory"}:
            self._cache[generation.generation_id] = value
        return value


@dataclass
class Extracted:
    rows: dict[tuple[str, str], dict[str, Any]]
    snapshots: set[tuple[str, str]]
    counts: Counter[str]
    withheld: tuple[str, dict[str, Any]] | None = None
    subjects: dict[str, list[Any]] = field(default_factory=dict)
    #: Observation-level support of each pair its rows name:
    #: `[snapshot, parser, state, accepted generations holding them]`.
    supported: list[list[Any]] = field(default_factory=list)
    #: Pairs no generation's provenance named, read from live observations.
    live: dict[tuple[str, str], str] = field(default_factory=dict)


def live_digest(conn: sqlite3.Connection, snapshot: str, parser: str) -> str:
    """A digest of a snapshot's live observations at one parser version."""
    return digest(sorted(list(item) for item in live_observations(conn, snapshot, parser)))


def extract(
    partition: Partition,
    *,
    verifier: _Verifier,
    support: SupportIndex,
    legacy: _Legacy,
    provenance: _Provenance | None = None,
) -> Extracted:
    """Merge a partition's rows as `reconstruct` does, then close them within it."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    rejected: list[tuple[str, str, dict[str, Any], str]] = []
    snapshots: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    provenance = provenance or _Provenance(verifier.conn)
    named: dict[tuple[str, str], set[Observation]] = {}
    for generation, _shared in partition.sources():
        if generation.unit_kind == "source_index":
            # Its listings are header candidates only (D-0254); what it read
            # does not change the support of the rows the partition merges.
            continue
        for pair, keys in provenance.of(generation).items():
            named.setdefault(pair, set()).update(keys)
    states: dict[tuple[str, str], Any] = {}
    live: dict[tuple[str, str], str] = {}

    def state_of(pair: tuple[str, str]) -> str:
        found = states.get(pair)
        if found is None:
            observations = named.get(pair)
            if observations is None:
                observations = set(live_observations(verifier.conn, *pair))
                live[pair] = live_digest(verifier.conn, *pair)
            found = states[pair] = support.matched(*pair, observations)
        return str(found.state)

    #: Events whose header is the baseline's stand-in (D-0251).
    stand_ins: set[str] = set()
    #: The listings mapped to the partition's events, by key: header
    #: candidates only, never merged or checked as rows (D-0254).
    listings: dict[str, dict[str, Any]] = {}
    #: Each event's last rejected legacy header, and patches that found no base.
    headers: dict[str, dict[str, Any]] = {}
    pending: dict[str, list[dict[str, Any]]] = {}

    def stand_in(key: str, rejected: Mapping[str, Any]) -> None:
        """Put the baseline's header for a rejected legacy one, when no row holds the event yet.

        The stand-in's own snapshot joins the partition's support like any
        row's; a revoked one leaves the event withheld. Its status follows the
        rejected row where D-0253 corrected it (`_corrected_status`); a later
        rejected row of the event corrects it again.
        """
        if ("events", key) in rows and key not in stand_ins:
            return
        header = legacy.header(key)
        if header is None:
            return
        pair = _header_pair(header)
        if pair is not None:
            snapshots.add(pair)
            if state_of(pair) == "revoked":
                return
        rows[("events", key)] = _corrected_status(header, rejected)
        stand_ins.add(key)

    moved = _Moved(legacy, listings, rows)

    try:
        for generation, shared in partition.sources():
            items: Iterable[tuple[str, str, str]] = (
                verifier.rows(generation)
                if shared is None
                else ((row.table, row.key, verifier.row(row)) for row in shared)
            )
            for table, key, raw in items:
                payload = json.loads(raw)
                if table == "source_events":
                    listings[key] = payload
                    continue
                snapshot = payload.get("snapshot_id")
                if snapshot and snapshot != "override":
                    pair = str(snapshot), str(payload.get("parser_version", ""))
                    snapshots.add(pair)
                    state = state_of(pair)
                    if state != "accepted":
                        kept = state != "revoked" and legacy.matches(table, key, payload)
                        if (
                            not kept
                            and state != "revoked"
                            and table != "events"
                            and moved.matches(table, key, payload)
                        ):
                            # Published before under the placeholder it moved
                            # from (D-0254): no new unadmitted fact.
                            counts["legacy_moved_retained"] += 1
                            kept = True
                        elif kept:
                            counts["legacy_unassessed_retained"] += 1
                        if not kept:
                            rejected.append(
                                (
                                    table,
                                    key,
                                    payload,
                                    "source_revoked"
                                    if state == "revoked"
                                    else "legacy_owned_values_not_published",
                                )
                            )
                            if table == "events" and state != "revoked":
                                payload.setdefault("run_id", generation.run_id)
                                for name in _PROCESSING[1:]:
                                    payload.setdefault(name, generation.created_at)
                                headers[key] = payload
                                stand_in(key, payload)
                            continue
                payload.setdefault("run_id", generation.run_id)
                for name in _PROCESSING[1:]:
                    payload.setdefault(name, generation.created_at)
                patch = (
                    generation.stage == "link" and table in {"entries", "judges", "placements"}
                ) or (
                    generation.stage == "project"
                    and generation.unit_kind in {"history", "year"}
                    and table in {"events", "registry_placements"}
                )
                if not patch and table == "events" and key in stand_ins:
                    # An admissible header replaces the baseline's stand-in whole.
                    stand_ins.discard(key)
                    del rows[(table, key)]
                previous = rows.get((table, key))
                if patch and previous is None:
                    counts["partial_overlay_without_structural_base"] += 1
                    if table == "events":
                        pending.setdefault(key, []).append(payload)
                    continue
                merged = previous if previous is not None else dict(_NEUTRAL.get(table, {}))
                merged.update(payload)
                rows[(table, key)] = merged
    except GenerationUnavailable as error:
        return Extracted(
            {}, snapshots, counts, ("generation_output_digest_mismatch", {"scope": str(error)})
        )
    for key, rejected_header in sorted(headers.items()):
        if ("events", key) in rows:
            continue
        found = _listing_header(key, rejected_header, listings, rows, state_of)
        if found is None:
            continue
        header, pair = found
        snapshots.add(pair)
        # The year's patch found no base when it was read; it applies now.
        for overlay in pending.pop(key, ()):
            header.update(overlay)
            counts["partial_overlay_without_structural_base"] -= 1
        if counts.get("partial_overlay_without_structural_base") == 0:
            del counts["partial_overlay_without_structural_base"]
        rows[("events", key)] = header
        counts["listing_header_published"] += 1
    if stand_ins:
        counts["baseline_header_republished"] += len(stand_ins)
    result = Extracted(
        rows,
        snapshots,
        counts,
        supported=[
            [pair[0], pair[1], states[pair].state, list(states[pair].generations)]
            for pair in sorted(states)
        ],
        live=dict(sorted(live.items())),
    )
    _close(partition, result, rejected)
    return result


class _Moved:
    """Legacy rows of an event whose listings moved from a placeholder (D-0254).

    D-0253 moves a placeholder's contests onto its WSDC event, so their ids
    take the WSDC event's prefix. A legacy row that equals the baseline only
    under the placeholder's ids was published already: it is kept when the
    same row with the placeholder's id in place of the event's matches the
    baseline (`_legacy_matches`). The placeholder is the id the map generates
    for a listing the event now holds (`ids.event_id` of its end date and
    name), read from the partition's `source_event_map` and `source_events`
    rows.
    """

    def __init__(
        self,
        legacy: _Legacy,
        listings: Mapping[str, Mapping[str, Any]],
        rows: Mapping[tuple[str, str], Mapping[str, Any]],
    ) -> None:
        self.legacy = legacy
        self.listings = listings
        self.rows = rows
        self._moves: dict[str, tuple[str, ...]] | None = None

    def moves(self) -> dict[str, tuple[str, ...]]:
        """Each event of the partition and the placeholders its listings left."""
        if self._moves is None:
            from swingset.model.ids import event_id

            found: dict[str, set[str]] = {}
            for (table, listing), row in self.rows.items():
                if table != "source_event_map" or listing not in self.listings:
                    continue
                target = str(row.get("event_id") or "")
                payload = self.listings[listing]
                name, end = payload.get("name_raw"), payload.get("end_date")
                if not target or not name or not end:
                    continue
                placeholder = event_id(date.fromisoformat(str(end)), str(name))
                if placeholder != target:
                    found.setdefault(target, set()).add(placeholder)
            self._moves = {event: tuple(sorted(ids)) for event, ids in found.items()}
        return self._moves

    def matches(self, table: str, key: str, payload: Mapping[str, Any]) -> bool:
        """Whether the row, under a placeholder's ids, equals the baseline's row."""
        parts = json.loads(key)
        for event, placeholders in self.moves().items():
            if not any(_names(value, event) for value in (*parts, payload.get("event_id"))):
                continue
            for old in placeholders:
                if self.legacy.matches(
                    table,
                    canonical([_renamed(part, event, old) for part in parts]),
                    {name: _renamed(value, event, old) for name, value in payload.items()},
                ):
                    return True
        return False


def _names(value: Any, event: str) -> bool:
    """Whether ``value`` is the event's id or an id under it."""
    return isinstance(value, str) and (value == event or value.startswith(event + "/"))


def _renamed(value: Any, event: str, old: str) -> Any:
    """``value`` with the event's id prefix replaced by ``old``, in lists too."""
    if _names(value, event):
        return old + str(value)[len(event) :]
    if isinstance(value, list):
        return [_renamed(item, event, old) for item in value]
    return value


def _listing_header(
    key: str,
    rejected: Mapping[str, Any],
    listings: Mapping[str, Mapping[str, Any]],
    rows: Mapping[tuple[str, str], Mapping[str, Any]],
    state_of: Callable[[tuple[str, str]], str],
) -> tuple[dict[str, Any], tuple[str, str]] | None:
    """A header from an accepted listing, for a registry occurrence the baseline lacks (D-0254).

    Only a held occurrence of a `wsdc-*` series qualifies: its event id,
    series, month, `held` and status are the registry's, which accepted
    registry pages hold. The listings are the ones the event's resolution
    maps to it; those whose snapshot and parser are `accepted` qualify, and
    the one with the widest span, then the newest snapshot, wins. Its name,
    dates, source and provenance are the header's, as the placeholder it
    replaced published them. Place, website and live window stay empty:
    no admitted page gave them. None when nothing qualifies.
    """
    if rejected.get("held") != "held" or not str(rejected.get("series_id") or "").startswith(
        "wsdc-"
    ):
        return None
    event = json.loads(key)[0]
    candidates = []
    for (table, listing), row in rows.items():
        if table != "source_event_map" or row.get("event_id") != event or listing not in listings:
            continue
        found = listings[listing]
        start, end = found.get("start_date"), found.get("end_date")
        pair = str(found.get("snapshot_id") or ""), str(found.get("parser_version") or "")
        if not start or not end or not pair[0] or pair[0] == "override":
            continue
        if state_of(pair) != "accepted":
            continue
        span = (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
        candidates.append(((span, pair[0], listing), found, pair))
    if not candidates:
        return None
    _, found, pair = max(candidates, key=lambda item: item[0])
    status = rejected.get("wsdc_status")
    header = {
        **_NEUTRAL["events"],
        "event_id": event,
        "series_id": rejected["series_id"],
        "event_month": rejected.get("event_month"),
        "held": "held",
        "wsdc_status": status if status in {"registry", "trial"} else "registry",
        "name": found.get("name_raw"),
        "year": int(str(found["end_date"])[:4]),
        "start_date": found["start_date"],
        "end_date": found["end_date"],
        "date_precision": "day",
        "city": None,
        "region": None,
        "country": None,
        "website": None,
        "live_window_start": None,
        "live_window_end": None,
        "history_source": json.dumps(["platform", "registry"]),
        "sources": json.dumps(sorted({str(found.get("source")), "wsdc_registry"})),
        "source": found.get("source"),
        "snapshot_id": pair[0],
        "parser_version": pair[1],
    }
    for name in _PROCESSING:
        if name in rejected:
            header[name] = rejected[name]
    return header, pair


def _close(
    partition: Partition,
    result: Extracted,
    rejected: list[tuple[str, str, dict[str, Any], str]],
) -> None:
    rows, counts = result.rows, result.counts
    omitted_dancers: dict[Any, str] = {}
    reasons: Counter[str] = Counter()
    for table, key, payload, reason in rejected:
        counts[reason] += 1
        if (
            table == "events"
            and reason == "legacy_owned_values_not_published"
            and ("events", key) in rows
        ):
            # Another admissible generation, or the baseline's stand-in
            # (D-0251), supplied this event (reconstruct).
            continue
        if table in {"dancers", "registry_placements"} and payload.get("wsdc_id") is not None:
            omitted_dancers.setdefault(payload["wsdc_id"], reason)
            continue
        reasons[reason] += 1
    if reasons:
        result.withheld = (
            "source_revoked" if reasons["source_revoked"] else "source_support_unavailable",
            {"rejected": dict(sorted(reasons.items()))},
        )
        return
    if omitted_dancers:
        # A dancer without support leaves its batch; entries and links that
        # name it are closed at assembly, across partitions (WP8).
        for table, key in [
            item
            for item, row in rows.items()
            if item[0] in {"dancers", "registry_placements"}
            and row.get("wsdc_id") in omitted_dancers
        ]:
            del rows[(table, key)]
        result.subjects["dancers"] = sorted(
            [[dancer, reason] for dancer, reason in omitted_dancers.items()], key=str
        )
    missing = dangling(rows)
    if missing:
        result.withheld = (
            "dangling_reference",
            {"rows": [list(item) for item in missing[:20]], "count": len(missing)},
        )
        return
    # Identity claims about a subject the partition lacks go; a placement's
    # dancer follows its entry's (reconstruct's close, within the partition).
    subjects = {("entry", key) for (table, key) in rows if table == "entries"} | {
        ("judge", key) for (table, key) in rows if table == "judges"
    }
    for table, key in [item for item in rows if item[0] in _IDENTITY_TABLES]:
        row = rows[(table, key)]
        subject = (
            "entry" if row.get("subject_kind") == "entry" else "judge",
            canonical([row.get("subject_id")]),
        )
        if subject not in subjects:
            del rows[(table, key)]
            counts["identity_without_subject_dropped"] += 1
    for (table, _key), row in rows.items():
        if table != "placements":
            continue
        for role in ("leader", "follower"):
            entry = rows.get(("entries", canonical([row.get(role + "_entry_id")])))
            if entry is None or entry.get("wsdc_id") is None:
                row[role + "_wsdc_id"] = None
                row["registry_points_" + role] = None
                row["registry_confirmed"] = 0
                row["points_matches_expected"] = None


def public_tables(
    partition: Partition, rows: Mapping[tuple[str, str], Mapping[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """The partition's rows in the public (or internal) schema, sorted by primary key."""
    result: dict[str, list[dict[str, Any]]] = {}
    for table in _TABLES[partition.kind]:
        schema = SCHEMAS.get(table) or INTERNAL_SCHEMAS[table]
        converted = []
        for (name, _key), original in rows.items():
            if name != table:
                continue
            row = dict(original)
            if table == "coverage":
                row.update(scope_kind="year", scope_id=str(row["year"]))
            converted.append({f.name: _convert(row.get(f.name), f.type) for f in schema})
        keys = PRIMARY_KEYS.get(table) or _INTERNAL_KEYS[table]
        converted.sort(key=lambda row: json.dumps(tuple(row.get(k) for k in keys), default=str))
        if converted:
            result[table] = converted
    return result


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def code_identity(conn: sqlite3.Connection) -> dict[str, str]:
    """The accepted build code, environment and history start, and these modules' bytes."""
    result = {
        str(name): str(value)
        for name, value in conn.execute(
            "SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline' "
            "AND input_name IN ('recipe/env','recipe/code/build','policy/history_start')"
        )
    }
    here = Path(__file__).parent
    result["partition_modules"] = hashlib.sha256(
        b"".join(hashlib.sha256((here / name).read_bytes()).digest() for name in _MODULES)
    ).hexdigest()
    return result


@dataclass
class _Outcome:
    partition: Partition
    inputs_digest: str
    detail: dict[str, Any]
    artifact: str | None = None
    reason: str | None = None
    size: int = 0
    reused: bool = False
    #: Whether every unit the partition reads whole is current (event
    #: partitions); None for a reused artifact, checked when a claim needs it.
    current: bool | None = True
    #: A kept artifact's row to write again with this detail (the first build
    #: that keeps it while its units are not current stamps the time).
    restamp: PartitionRow | None = None


def _inputs(
    common: Mapping[str, Any],
    partition: Partition,
    content: str,
    supported: list[list[Any]],
    baseline: Mapping[str, Any] | None,
    *,
    legacy: bool,
    live: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    """Everything a partition's artifact depends on; its digest is the inputs digest.

    ``supported`` is each pair's standing (`support_inputs`): with the
    generations in ``content`` and the observation sets each last recorded
    reading, it decides the observation-level support. ``live`` digests the live
    observations of pairs no generation recorded. The baseline counts only
    when a row's support is legacy (``legacy``): only then can it keep or
    drop a row.
    """
    return {
        **common,
        "partition": partition.name,
        "content": content,
        "support": supported,
        "live_observations": [[pair[0], pair[1], value] for pair, value in sorted(live.items())],
        "baseline": baseline if legacy else None,
    }


def _stale_units(conn: sqlite3.Connection, partition: Partition) -> list[str]:
    """The units an event partition reads whole that are not current."""
    from swingset.state import derivations
    from swingset.state.recipes import RecipeScopeMissing

    if partition.kind != "event":
        return []
    stale = []
    for generation in partition.generations:
        try:
            settled = derivations.current(conn, generation.unit)
        except RecipeScopeMissing:
            settled = False
        if not settled:
            stale.append(generation.scope)
    return stale


def _listings(tables: Mapping[str, list[dict[str, Any]]]) -> list[list[str]]:
    return sorted(
        [str(row["source"]), str(row["source_ref"])] for row in tables.get("source_event_map", ())
    )


def build(
    database: Any,
    *,
    history_start: date,
    baseline: Path | None,
    run_id: str,
    now: datetime,
    only: str | None = None,
) -> dict[str, Any]:
    """Build every partition whose inputs changed; keep the rest. Return counts.

    ``only`` builds the one partition of that name, or the event partition
    holding that event (`event/<E>`), and removes no stale partition row.
    """
    started = time.monotonic()
    conn = database.connection
    state_dir: Path = database.state_dir
    previous = {row.partition: row for row in release_store.all_rows(conn, PartitionRow)}
    details = {name: json.loads(row.detail_json or "{}") for name, row in previous.items()}
    known = {
        generation: events
        for detail in details.values()
        for generation, events in detail.get("events_of", {}).items()
    }
    partitions, known = select(conn, known=known)
    if only is not None:
        wanted = only.removeprefix("event/")
        partitions = [item for item in partitions if item.name == only or wanted in item.events]
        if not partitions:
            raise ValueError(f"no partition {only}")
    from .closure import _baseline, _revocations

    common = {
        "format": FORMAT,
        "revocations": _revocations(conn),
        "code": code_identity(conn),
        "history_start": history_start.isoformat(),
    }
    baseline_identity = _baseline(baseline) if baseline is not None else None
    support = SupportIndex(conn)
    verifier = _Verifier(conn)
    provenance = _Provenance(conn)
    legacy = _Legacy(baseline, state_dir / "tmp")
    counts: Counter[str] = Counter()
    pending: list[_Outcome] = []
    claims: dict[tuple[str, str], list[_Outcome]] = {}

    def keep(partition: Partition, prior: PartitionRow, detail: dict[str, Any]) -> _Outcome:
        return _Outcome(
            partition,
            prior.inputs_digest,
            detail,
            prior.artifact_digest,
            prior.reason,
            reused=True,
        )

    try:
        for partition in partitions:
            prior = previous.get(partition.name)
            prior_detail = details.get(partition.name, {})
            try:
                outcome = _one(
                    database,
                    partition,
                    prior,
                    prior_detail,
                    common=common,
                    baseline_identity=baseline_identity,
                    support=support,
                    verifier=verifier,
                    provenance=provenance,
                    legacy=legacy,
                    known=known,
                    history_start=history_start,
                    counts=counts,
                    keep=keep,
                )
            except Exception as error:
                # One partition's defect withholds that partition only (I1).
                counts["partition_errors"] += 1
                log(
                    "partition_error",
                    partition=partition.name,
                    error=f"{type(error).__name__}: {error}"[:300],
                )
                outcome = _Outcome(
                    partition,
                    digest({"partition": partition.name, "error": type(error).__name__}),
                    {
                        "withheld": {
                            "class": type(error).__name__,
                            "message": str(error)[:300],
                        }
                    },
                    reason="partition_error",
                )
            counts["reused" if outcome.reused else "rebuilt"] += 1
            if outcome.reason is None and outcome.artifact is not None:
                for listing in outcome.detail.get("listings", ()):
                    claims.setdefault((str(listing[0]), str(listing[1])), []).append(outcome)
            pending.append(outcome)
            if len(pending) >= WRITE_BATCH:
                _flush(database, pending, run_id=run_id, now=now, counts=counts)
        _flush(database, pending, run_id=run_id, now=now, counts=counts)
        _settle_claims(database, claims, run_id=run_id, now=now, counts=counts)
    finally:
        legacy.close()
    if only is None:
        names = {partition.name for partition in partitions}
        with database.transaction() as write:
            for name in sorted(set(previous) - names):
                release_store.delete(write, PartitionRow, name)
                counts["stale_removed"] += 1
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary = release_store.counts(conn)
    return {
        "partitions": len(partitions),
        "multi_event": sum(1 for item in partitions if len(item.events) > 1),
        **dict.fromkeys(
            ("built", "withheld", "rebuilt", "reused", "bytes_written", "stale_removed"), 0
        ),
        **{key: int(value) for key, value in sorted(counts.items())},
        "state": summary,
        "seconds": round(time.monotonic() - started, 3),
        "peak_rss_bytes": int(peak if sys.platform == "darwin" else peak * 1024),
    }


def _one(
    database: Any,
    partition: Partition,
    prior: PartitionRow | None,
    prior_detail: dict[str, Any],
    *,
    common: Mapping[str, Any],
    baseline_identity: Mapping[str, Any] | None,
    support: SupportIndex,
    verifier: _Verifier,
    provenance: _Provenance,
    legacy: _Legacy,
    known: Mapping[str, list[str]],
    history_start: date,
    counts: Counter[str],
    keep: Any,
) -> _Outcome:
    """Reuse, keep or build one partition."""
    conn = database.connection
    state_dir: Path = database.state_dir
    if partition.errors:
        scope, kind, message = partition.errors[0]
        counts["partition_errors"] += 1
        return _Outcome(
            partition,
            digest({"partition": partition.name, "error": kind, "scope": scope}),
            {"withheld": {"class": kind, "message": message, "scope": scope}},
            reason="partition_error",
        )
    # A prior artifact counts only when every file its sidecar lists is
    # there with its recorded sha256: a damaged one is rebuilt, never read
    # by the assembly (I1, I8).
    prior_usable = prior is not None and (
        prior.state == "withheld"
        or bool(prior.artifact_digest and artifact_store.intact(state_dir, prior.artifact_digest))
    )
    content = digest(partition.content(provenance.groups))
    detail: dict[str, Any] = {
        "content": content,
        "events_of": {
            g.generation_id: known[g.generation_id]
            for g in partition.generations
            if g.generation_id in known
        },
    }
    if prior is not None and prior_usable and prior_detail.get("content") == content:
        live = {
            (str(item[0]), str(item[1])): live_digest(conn, str(item[0]), str(item[1]))
            for item in prior_detail.get("live_observations", ())
        }
        value = _inputs(
            common,
            partition,
            content,
            support_inputs(support, prior_detail.get("snapshots", ())),
            baseline_identity,
            legacy=bool(prior_detail.get("legacy")),
            live=live,
        )
        if digest(value) == prior.inputs_digest:
            outcome = keep(partition, prior, prior_detail)
            outcome.current = None  # checked only if a listing claim needs it
            return outcome  # type: ignore[no-any-return]
    # A rebuild needs consistent generations; an unchanged artifact was
    # consistent when built, so reuse above needs no currency check.
    stale = _stale_units(conn, partition)
    if stale:
        # Mixed generations are never published: keep the last consistent
        # artifact until derive catches up, or withhold the partition. A
        # kept artifact whose support was revoked since is withheld.
        revoked = [
            item[:2]
            for item in support_inputs(support, prior_detail.get("snapshots", ()))
            if item[2] == "revoked"
        ]
        if revoked:
            return _Outcome(
                partition,
                digest({"partition": partition.name, "revoked": revoked}),
                {"withheld": {"rejected": {"source_revoked": len(revoked)}, "units": stale[:20]}},
                reason="source_revoked",
                current=False,
            )
        if prior is not None and prior_usable:
            outcome = keep(partition, prior, prior_detail)
            outcome.current = False
            if prior.state == "built":
                counts["kept_not_current"] += 1
                if "kept_not_current_since" not in prior_detail:
                    # When it went stale, for the watchdog; a rebuild clears it.
                    outcome.detail = {**prior_detail, "kept_not_current_since": None}
                    outcome.restamp = prior
            return outcome  # type: ignore[no-any-return]
        return _Outcome(
            partition,
            digest({"partition": partition.name, "stale": stale}),
            {"withheld": {"units": stale[:20], "count": len(stale)}},
            reason="units_not_current",
            current=False,
        )
    extracted = extract(
        partition, verifier=verifier, support=support, legacy=legacy, provenance=provenance
    )
    is_legacy = any(item[2] == "legacy_unassessed" for item in extracted.supported)
    value = _inputs(
        common,
        partition,
        content,
        support_inputs(support, extracted.snapshots),
        baseline_identity,
        legacy=is_legacy,
        live=extracted.live,
    )
    inputs_digest = digest(value)
    detail.update(
        snapshots=sorted([list(pair) for pair in extracted.snapshots]),
        counts=dict(sorted(extracted.counts.items())),
        legacy=is_legacy,
        live_observations=[list(pair) for pair in extracted.live],
    )
    outcome = _Outcome(partition, inputs_digest, detail)
    tables = public_tables(partition, extracted.rows)
    failure = extracted.withheld
    if failure is None:
        found = release_checks(tables, history_start)
        if found is not None:
            failure = found[0], {"message": found[1]}
    if failure is not None:
        outcome.reason = failure[0]
        detail["withheld"] = failure[1]
        return outcome
    detail["listings"] = _listings(tables)
    sidecar = {
        "format": FORMAT,
        "partition": partition.name,
        "inputs_digest": inputs_digest,
        "inputs": value,
        "support": extracted.supported,
        "generations": partition.generation_ids(),
        "withheld_subjects": extracted.subjects,
        "counts": dict(sorted(extracted.counts.items())),
    }
    arrow = {
        name: pa.Table.from_pylist(rows, schema=SCHEMAS.get(name) or INTERNAL_SCHEMAS[name])
        for name, rows in tables.items()
    }
    outcome.artifact, outcome.size = artifact_store.write(state_dir, arrow, sidecar)
    counts["bytes_written"] += outcome.size
    return outcome


def _settle_claims(
    database: Any,
    claims: Mapping[tuple[str, str], list[_Outcome]],
    *,
    run_id: str,
    now: datetime,
    counts: Counter[str],
) -> None:
    """Withhold all but one event partition that claims the same listing.

    A map move built mid-drain can leave a placeholder's kept artifact and
    the catalog event's new one both holding a listing's results. The
    partition whose units are current keeps it, else the one holding the
    event the live map names; the others are withheld this build.
    """
    conn = database.connection
    losers: dict[str, tuple[_Outcome, list[str], str]] = {}
    for listing, outcomes in sorted(claims.items()):
        if len(outcomes) < 2:
            continue
        for outcome in outcomes:
            if outcome.current is None:
                outcome.current = not _stale_units(conn, outcome.partition)
        mapped = conn.execute(
            "SELECT event_id FROM source_event_map WHERE source=? AND source_ref=?", listing
        ).fetchone()
        live = str(mapped[0]) if mapped is not None else None

        def rank(outcome: _Outcome, live: str | None = live) -> tuple[bool, bool, str]:
            return (
                bool(outcome.current),
                live is not None and live in outcome.partition.events,
                outcome.partition.name,
            )

        winner = max(outcomes, key=rank)
        for outcome in outcomes:
            if outcome is not winner:
                losers.setdefault(
                    outcome.partition.name, (outcome, list(listing), winner.partition.name)
                )
    if not losers:
        return
    stamp = now.isoformat()
    with database.transaction() as write:
        for name in sorted(losers):
            loser, claimed, holder = losers[name]
            counts["built"] -= 1
            counts["withheld"] += 1
            counts["withheld:listing_claimed_elsewhere"] += 1
            detail = {
                **{
                    key: value
                    for key, value in loser.detail.items()
                    if key not in {"listings", "kept_not_current_since"}
                },
                "withheld": {"listing": claimed, "claimed_by": holder},
            }
            # A digest the reuse check never matches: the claim is contested
            # again every build, so the loser is built once the other claim
            # goes, not held until its own generations change.
            release_store.upsert(
                write,
                PartitionRow(
                    name,
                    digest(
                        {"claimed_by": holder, "listing": claimed, "inputs": loser.inputs_digest}
                    ),
                    "withheld",
                    stamp,
                    run_id,
                    reason="listing_claimed_elsewhere",
                    detail_json=canonical(detail),
                ),
            )


def _flush(
    database: Any, pending: list[_Outcome], *, run_id: str, now: datetime, counts: Counter[str]
) -> None:
    if not pending:
        return
    stamp = now.isoformat()
    state_dir: Path = database.state_dir
    with database.transaction() as conn:
        for outcome in pending:
            name = outcome.partition.name
            if outcome.reason is not None:
                counts["withheld"] += 1
                counts[f"withheld:{outcome.reason}"] += 1
                if outcome.reused:
                    continue
                release_store.upsert(
                    conn,
                    PartitionRow(
                        name,
                        outcome.inputs_digest,
                        "withheld",
                        stamp,
                        run_id,
                        reason=outcome.reason,
                        detail_json=canonical(outcome.detail),
                    ),
                )
                continue
            assert outcome.artifact is not None
            if not artifact_store.exists(state_dir, outcome.artifact):
                # The collector removed an unnamed copy after it was found;
                # leave no row, so the next build writes it again.
                counts["collected_while_building"] += 1
                continue
            counts["built"] += 1
            existing = release_store.get(conn, ArtifactRow, outcome.artifact)
            release_store.upsert(
                conn,
                ArtifactRow(
                    outcome.artifact,
                    name,
                    existing.bytes if existing is not None else outcome.size,
                    existing.created_at if existing is not None else stamp,
                    stamp,
                ),
            )
            if outcome.restamp is not None:
                detail = {**outcome.detail, "kept_not_current_since": stamp}
                release_store.upsert(
                    conn,
                    replace(outcome.restamp, detail_json=canonical(detail)),
                )
                continue
            if outcome.reused:
                continue
            release_store.upsert(
                conn,
                PartitionRow(
                    name,
                    outcome.inputs_digest,
                    "built",
                    stamp,
                    run_id,
                    artifact_digest=outcome.artifact,
                    detail_json=canonical(outcome.detail),
                ),
            )
    pending.clear()


def show(database: Any, name: str) -> dict[str, Any]:
    """One partition's row, detail and, when built, its sidecar's tables."""
    row = release_store.get(database.connection, PartitionRow, name)
    if row is None:
        raise ValueError(f"no partition {name}")
    result: dict[str, Any] = {
        "partition": row.partition,
        "state": row.state,
        "reason": row.reason,
        "artifact_digest": row.artifact_digest,
        "inputs_digest": row.inputs_digest,
        "built_at": row.built_at,
        "run_id": row.run_id,
        "detail": json.loads(row.detail_json or "{}"),
    }
    if row.artifact_digest is not None and artifact_store.exists(
        database.state_dir, row.artifact_digest
    ):
        sidecar = artifact_store.sidecar(database.state_dir, row.artifact_digest)
        result["tables"] = {name: item["rows"] for name, item in sidecar["tables"].items()}
        result["withheld_subjects"] = sidecar.get("withheld_subjects", {})
    return result


def listing(database: Any, *, state: str | None = None) -> list[dict[str, Any]]:
    """Every partition row, optionally of one state."""
    return [
        {
            "partition": row.partition,
            "state": row.state,
            "reason": row.reason,
            "artifact_digest": row.artifact_digest,
            "built_at": row.built_at,
        }
        for row in release_store.all_rows(database.connection, PartitionRow)
        if state is None or row.state == state
    ]


def rows_for(state_dir: Path, digest_value: str) -> dict[str, list[dict[str, Any]]]:
    """Every table of one artifact, for tests and reports."""
    tables = artifact_store.sidecar(state_dir, digest_value)["tables"]
    return {name: artifact_store.read_table(state_dir, digest_value, name) for name in tables}


__all__ = [
    "DANCER_BATCH",
    "FORMAT",
    "Generation",
    "Partition",
    "build",
    "listing",
    "pointers",
    "rows_for",
    "select",
    "show",
]
