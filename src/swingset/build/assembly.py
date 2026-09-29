"""Streamed assembly of a partitioned release (plan phase 3, WP8; D-0224).

The `whole` assembly loads every partition artifact into Python lists. This
module builds the same candidate rows while holding one partition at a time:

1. Pass 1 reads key columns across the manifest's artifacts in DuckDB: the
   dancers, events and contests the release holds, every snapshot and parser
   the rows name, and anti-joins of `entries`, `judges`, `identity_links`,
   `link_candidates` and `placements` WSDC ids against `dancers` and of
   `registry_placements.event_id` against `events`. Each (snapshot, parser)
   is checked for revocation once; an event partition holding a revoked row
   is closed first, so the events left are known before any partition is
   written.
2. Pass 2 goes one partition at a time, dancer batches first: key checks
   (the whole path's `partitioned.load` rules plus the anti-joins), the
   revocation closure, then for events the identity policy with that
   event's baseline entries and judges and the registry facts naming its
   events. Rows go to per-table staging files, one row group per partition.
3. Suppression learns over every dancer, entry and judge to a fixed point,
   then transforms each partition (only when `suppressions.csv` has a
   selector). Scope annotation runs per partition. The builder's checks run
   per partition on its final rows (before coverage, after the suppression
   pass when there is one): a partition that fails is withheld with reason
   `assembly_check:<check>`, counted in `release_policy.partitions`, its
   events' registry associations are detached, and entries naming its
   dancers lose the join. The release goes on without it (I1).
4. The release level adds snapshots, the review queue and coverage (the
   year fragments, scope rows from SQL aggregates, event coverage rows).
5. The candidate: each table sorted by primary key from DuckDB into public
   Parquet, identity counts refreshed from SQL, a changelog from a per-table
   full outer join with the baseline merged with the baseline changelog, and
   the card from lazy tables. Suppression convergence re-streams.

DuckDB runs with a 1 GB memory limit and spills under `state_dir/tmp`. Rows
equal the `whole` assembly's rows; file bytes need not. `Meter` counts the
rows each unit of work materializes in Python.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from swingset.log import log
from swingset.state.retention import artifact_directory

from .builder import (
    PUBLISHED_TABLES,
    BuildError,
    BuildInput,
    BuildMetadata,
    BuildResult,
    CandidateFacts,
    StreamedTables,
    _sorted_parquet_rows,
    _validate,
    _write_changelog,
    _write_parquet,
    build_streamed_candidate,
)
from .changelog import generated_delta, joined_changes
from .coverage import (
    FACT_METRICS,
    TIER_FIELDS,
    Facts,
    Scope,
    _at,
    _Coverage,
    annotate,
    apply_identity_counts,
)
from .manifest import ReleaseManifest
from .partition_checks import release_checks
from .schema import PRIMARY_KEYS, SCHEMAS
from .support_closure import (
    _EVENT_TABLES,
    REVIEW_SUBJECTS,
    Revocations,
    close_subjects,
    removed_ids,
)
from .suppression import SUBJECT_TABLES, SuppressionPolicy

MEMORY_LIMIT = "1GB"
THREADS = 2
#: Rows per Arrow batch read back from DuckDB into Python.
BATCH_ROWS = 2048
#: Rows per row group of a final table.
WRITE_BATCH_ROWS = 16384
#: Tables staged per partition; the rest are release-level lists.
STAGED_TABLES = tuple(
    name
    for name in PUBLISHED_TABLES
    if name not in {"review_queue", "changelog", "snapshots", "coverage"}
)
DANCER_TABLES = ("dancers", "registry_placements")
EVENT_TABLES = tuple(name for name in STAGED_TABLES if name not in DANCER_TABLES)
_NEUTRAL: dict[str, dict[str, Any]] = {
    "entries": {"wsdc_id": None, "link_status": "unmatched", "link_confidence": 0.0},
    "judges": {"wsdc_id": None},
}
_MARKS = ("callback_marks", "final_marks")
#: Scratch a killed build left is removed by the next build after this long.
STALE_SECONDS = 86400


class Meter:
    """Rows materialized in Python per unit of work: a partition, a batch, a list.

    `peak` is the largest unit; `units` the largest per unit name.
    """

    def __init__(self) -> None:
        self.peak = 0
        self.units: dict[str, int] = {}
        self._open: list[list[Any]] = []

    @contextmanager
    def unit(self, name: str) -> Iterator[None]:
        frame: list[Any] = [name, 0]
        self._open.append(frame)
        try:
            yield
        finally:
            self._open.pop()
            self._record(name, frame[1])

    def take(self, rows: int, name: str = "stream") -> None:
        if self._open:
            self._open[-1][1] += rows
        else:
            self._record(name, rows)

    def _record(self, name: str, rows: int) -> None:
        self.peak = max(self.peak, rows)
        self.units[name] = max(self.units.get(name, 0), rows)


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _files(paths: Iterable[Path]) -> str:
    return "[" + ", ".join("'" + str(path).replace("'", "''") + "'" for path in paths) + "]"


def _rows(table: pa.Table, meter: Meter) -> list[dict[str, Any]]:
    meter.take(table.num_rows)
    rows: list[dict[str, Any]] = table.to_pylist()
    return rows


class _Stage:
    """Per-table staging files; each partition's rows are one row group."""

    def __init__(self, directory: Path, tables: Sequence[str]) -> None:
        self.directory = directory
        self.tables = tuple(tables)
        directory.mkdir(parents=True)
        self.writers: dict[str, pq.ParquetWriter] = {}
        self.groups: dict[str, dict[str, int]] = defaultdict(dict)
        self.count: Counter[str] = Counter()
        self.order: list[str] = []
        self.readers: dict[str, pq.ParquetFile] = {}
        self.closed = False

    def path(self, table: str) -> Path:
        return self.directory / f"{table}.parquet"

    def write(self, partition: str, tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        self.order.append(partition)
        for table in self.tables:
            rows = tables.get(table) or ()
            if not rows:
                continue
            arrow = pa.Table.from_pylist(list(rows), schema=SCHEMAS[table])
            writer = self.writers.get(table)
            if writer is None:
                writer = self.writers[table] = pq.ParquetWriter(self.path(table), SCHEMAS[table])
            writer.write_table(arrow, row_group_size=max(1, arrow.num_rows))
            self.groups[partition][table] = self.count[table]
            self.count[table] += 1

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for table in self.tables:
            writer = self.writers.get(table)
            if writer is None:
                pq.write_table(SCHEMAS[table].empty_table(), self.path(table))
            else:
                writer.close()
                groups = pq.ParquetFile(self.path(table)).metadata.num_row_groups
                if groups != self.count[table]:
                    raise BuildError(f"staged {table} has {groups} row groups, expected one each")

    def read(self, partition: str, meter: Meter) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for table, index in self.groups.get(partition, {}).items():
            reader = self.readers.get(table)
            if reader is None:
                reader = self.readers[table] = pq.ParquetFile(self.path(table))
            result[table] = _rows(reader.read_row_group(index), meter)
        return result

    def batches(self, table: str, meter: Meter) -> Iterator[list[dict[str, Any]]]:
        for batch in pq.ParquetFile(self.path(table)).iter_batches(batch_size=BATCH_ROWS):
            meter.take(batch.num_rows)
            yield batch.to_pylist()

    def relation(self, table: str) -> str:
        return f"read_parquet('{self.path(table)}')"


def close_keys(
    tables: Mapping[str, list[dict[str, Any]]],
    *,
    absent: set[Any],
    withheld: Callable[[Any], bool],
    counts: Counter[str],
) -> None:
    """Close identity claims naming a dancer the release lacks, in place.

    ``absent`` holds the WSDC ids the release's rows name without a
    `dancers` row; ``withheld`` says which of them stage 1 withheld (a
    dancer left out of its batch, or a withheld batch). Entries and judges
    lose the join, identity links and candidates go, and a placement's
    dancer follows its entry's, as `closure_rows._close` and
    `partitioned.load` close a release.
    """
    for table in ("entries", "judges"):
        for row in tables.get(table, ()):
            wsdc_id = row.get("wsdc_id")
            if wsdc_id is not None and wsdc_id in absent:
                row.update(_NEUTRAL[table])
                counts[
                    "identity_of_withheld_dancer_closed"
                    if withheld(wsdc_id)
                    else "identity_of_absent_dancer_closed"
                ] += 1
    for table in ("identity_links", "link_candidates"):
        if table not in tables:
            continue
        kept = []
        for row in tables[table]:
            wsdc_id = row.get("wsdc_id")
            if wsdc_id is not None and wsdc_id in absent:
                counts[
                    "identity_of_withheld_dancer_closed"
                    if withheld(wsdc_id)
                    else "identity_of_absent_dancer_closed"
                ] += 1
            else:
                kept.append(row)
        tables[table][:] = kept
    entries = {row["entry_id"]: row for row in tables.get("entries", ())}
    for row in tables.get("placements", ()):
        for role in ("leader", "follower"):
            entry = entries.get(row.get(role + "_entry_id"))
            wsdc_id = row.get(role + "_wsdc_id")
            if (
                entry is None
                or entry.get("wsdc_id") is None
                or (wsdc_id is not None and wsdc_id in absent)
            ):
                row[role + "_wsdc_id"] = None
                row["registry_points_" + role] = None
                row["registry_confirmed"] = False
                row["points_matches_expected"] = None


def close_absent_dancers(tables: Mapping[str, list[dict[str, Any]]], counts: Counter[str]) -> None:
    """`close_keys` for a whole release in memory (the `whole` path)."""
    dancers = {row["wsdc_id"] for row in tables.get("dancers", ())}
    named = {
        row.get(field)
        for table, fields in (
            ("entries", ("wsdc_id",)),
            ("judges", ("wsdc_id",)),
            ("identity_links", ("wsdc_id",)),
            ("link_candidates", ("wsdc_id",)),
            ("placements", ("leader_wsdc_id", "follower_wsdc_id")),
        )
        for row in tables.get(table, ())
        for field in fields
    }
    absent = {value for value in named if value is not None and value not in dancers}
    close_keys(tables, absent=absent, withheld=lambda _value: False, counts=counts)


def withhold_invalid(
    tables: dict[str, list[Any]], history_start: Any, counts: Counter[str]
) -> list[dict[str, Any]]:
    """The whole path's per-partition checks: withhold what fails, in place.

    Rows are grouped as stage 1 groups them: event rows by the events they
    name (joined when a row names two), identity rows by their subject's
    event, dancers and registry placements by batch. A group that fails a
    builder check leaves the release with an `assembly_check:<check>`
    omission; its events' registry associations are detached and entries
    naming its dancers lose the join. Returns the omission items.
    """
    from .partitions import dancer_batch
    from .support_closure import _EventScopes

    scopes = _EventScopes(tables)
    subjects = {("entry", key): value for key, value in scopes.entries.items()}
    subjects |= {("judge", key): value for key, value in scopes.judges.items()}
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def events_of(table: str, row: Mapping[str, Any]) -> set[str]:
        if table in {"identity_links", "link_candidates", "identity_link_resolutions"}:
            value = subjects.get(
                ("entry" if row.get("subject_kind") == "entry" else "judge", row.get("subject_id"))
            )
            return {str(value)} if value is not None else set()
        return scopes.events(row)

    placed: list[tuple[str, int, str]] = []
    for table, rows in tables.items():
        if table in {"snapshots", "review_queue", "changelog", "coverage"}:
            continue
        for index, row in enumerate(rows):
            if table in DANCER_TABLES:
                placed.append((table, index, "dancers/" + dancer_batch(row.get("wsdc_id"))))
                continue
            named = sorted(events_of(table, row))
            if not named:
                continue
            for other in named[1:]:
                parent[find(other)] = find(named[0])
            placed.append((table, index, named[0]))
    groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    members: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for table, index, key in placed:
        name = key if key.startswith("dancers/") else "event/" + find(key)
        groups[name][table].append(tables[table][index])
        members[name].append((table, index))
    events_by_root: dict[str, set[str]] = defaultdict(set)
    for value in list(parent):
        events_by_root[find(value)].add(value)
    items: list[dict[str, Any]] = []
    dropped: dict[str, set[int]] = defaultdict(set)
    absent: set[Any] = set()
    withheld_events: set[str] = set()
    for name in sorted(groups):
        found = release_checks(groups[name], history_start)
        if found is None:
            continue
        label = f"assembly_check:{found[0]}"
        counts["assembly_withheld"] += 1
        counts[label] += 1
        for table, index in members[name]:
            dropped[table].add(index)
        kind, _, identifier = name.partition("/")
        if kind == "event":
            events = events_by_root.get(identifier, {identifier})
            withheld_events |= events
            items.extend(
                {
                    "stage": "project",
                    "unit_kind": "event",
                    "unit_id": event,
                    "status": "withheld",
                    "reason": label,
                }
                for event in sorted(events)
            )
        else:
            absent |= {
                row["wsdc_id"] for row in groups[name].get("dancers", ()) if row.get("wsdc_id")
            }
            items.append(
                {
                    "stage": "project",
                    "unit_kind": "dancer",
                    "unit_id": identifier,
                    "status": "withheld",
                    "reason": label,
                }
            )
    if not items:
        return []
    for table, indexes in dropped.items():
        tables[table] = [row for index, row in enumerate(tables[table]) if index not in indexes]
    for row in tables.get("registry_placements", ()):
        if row.get("event_id") in withheld_events:
            row["event_id"] = None
            counts["registry_association_outside_release"] += 1
    if absent:
        close_keys(tables, absent=absent, withheld=lambda _value: True, counts=counts)
    return items


def omissions(manifest: ReleaseManifest) -> tuple[list[dict[str, Any]], set[str]]:
    """What the release leaves out, as coverage's omission items, and withheld batches."""
    result: list[dict[str, Any]] = []
    batches: set[str] = set()
    for partition, reason in manifest.withheld:
        kind, _, name = partition.partition("/")
        if kind == "event":
            result.extend(
                {
                    "stage": "project",
                    "unit_kind": "event",
                    "unit_id": event,
                    "status": "withheld",
                    "reason": reason,
                }
                for event in name.split("+")
            )
        else:
            if kind == "dancers":
                batches.add(name)
            result.append(
                {
                    "stage": "project",
                    "unit_kind": "dancer" if kind == "dancers" else kind,
                    "unit_id": name,
                    "status": "withheld",
                    "reason": reason,
                }
            )
    return result, batches


def _snapshot_rows(conn: sqlite3.Connection, wanted: set[str]) -> Iterator[dict[str, Any]]:
    """`input._read_snapshots`, filtered to ``wanted`` while reading."""
    from .input import _convert

    schema = SCHEMAS["snapshots"]
    for values in conn.execute(
        """
        SELECT s.snapshot_id, w.source, s.url, s.fetched_at, s.http_status,
               s.body_sha256, s.body_bytes, s.content_changed, w.parser,
               s.parser_version, s.parse_status, s.via,
               COALESCE(s.captured_at,s.fetched_at), s.archive_url,
               COALESCE(s.observed_at,s.captured_at,s.fetched_at)
        FROM snapshots AS s JOIN watches AS w ON w.watch_id = s.watch_id
        """
    ):
        if values[0] in wanted:
            row = dict(zip(schema.names, values, strict=True))
            yield {field.name: _convert(row[field.name], field.type) for field in schema}


def _remove_stale(scratch: Path) -> None:
    """Remove assembly scratch a killed build left, once it is a day old."""
    if not scratch.is_dir():
        return
    limit = time.time() - STALE_SECONDS
    for path in scratch.glob("assembly-*"):
        try:
            if path.stat().st_mtime < limit:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _sorted(table: str, rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    keys = PRIMARY_KEYS[table]
    return sorted(rows, key=lambda row: json.dumps(tuple(row.get(k) for k in keys), default=str))


class Assembly:
    """One streamed assembly: `run` inside the build's read snapshot, then `write`."""

    def __init__(
        self,
        state_dir: Path,
        manifest: ReleaseManifest,
        *,
        baseline: Path | None,
        suppressions: Sequence[Mapping[str, Any]],
        history_start: Any,
        cutoff: datetime,
    ) -> None:
        self.state_dir = state_dir
        self.manifest = manifest
        self.baseline = baseline
        self.suppressions = list(suppressions)
        self.history_start = history_start
        self.cutoff = cutoff
        self.suppressing = SuppressionPolicy(self.suppressions).active
        self.meter = Meter()
        scratch = state_dir / "tmp"
        _remove_stale(scratch)
        self.directory = scratch / f"assembly-{uuid.uuid4().hex}"
        self.directory.mkdir(parents=True)
        (self.directory / "spill").mkdir()
        self.db = duckdb.connect(
            config={
                "memory_limit": MEMORY_LIMIT,
                "threads": THREADS,
                "temp_directory": str(self.directory / "spill"),
                "preserve_insertion_order": False,
            }
        )
        self.db.execute("SET TimeZone='UTC'")
        self.db.execute("SET enable_external_file_cache=false")
        self.counts: Counter[str] = Counter()
        self.stages: list[_Stage] = []
        self.started = time.monotonic()
        self.data: BuildInput | None = None
        self.coverage: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.review_queue: list[dict[str, Any]] = []
        self.witness: Mapping[str, Any] | None = None

    def close(self) -> None:
        try:
            self.db.close()
        finally:
            shutil.rmtree(self.directory, ignore_errors=True)

    # -- artifacts ---------------------------------------------------------

    def _artifact_files(self) -> dict[str, list[tuple[str, Path]]]:
        result: dict[str, list[tuple[str, Path]]] = defaultdict(list)
        for partition, digest in self.manifest.built:
            sidecar = self.manifest.sidecars.get(digest)
            if sidecar is None:
                from . import artifact_store

                sidecar = artifact_store.sidecar(self.state_dir, digest)
            for table, entry in sidecar["tables"].items():
                result[table].append(
                    (partition, artifact_directory(self.state_dir, digest) / entry["file"])
                )
        return result

    def _load(self, partition: str, tables: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for table in tables:
            path = self.paths.get((partition, table))
            result[table] = _rows(pq.read_table(path), self.meter) if path is not None else []
        return result

    def _view(self, table: str) -> str:
        files = [path for _, path in self.artifacts.get(table, ())]
        if not files:
            empty = self.directory / "empty" / f"{table}.parquet"
            if not empty.exists():
                empty.parent.mkdir(exist_ok=True)
                pq.write_table(SCHEMAS[table].empty_table(), empty)
            files = [empty]
        return f"read_parquet({_files(files)}, union_by_name=true, filename=true)"

    # -- pass 1 ------------------------------------------------------------

    def _pass_one(self, conn: sqlite3.Connection) -> None:
        db = self.db
        self.artifacts = self._artifact_files()
        self.paths = {
            (partition, table): path
            for table, items in self.artifacts.items()
            for partition, path in items
        }
        for table in STAGED_TABLES:
            db.execute(f"CREATE VIEW a_{table} AS SELECT * FROM {self._view(table)}")
        db.execute(
            "CREATE TABLE a_files(filename VARCHAR, partition VARCHAR)",
        )
        files = [
            (str(path), partition) for items in self.artifacts.values() for partition, path in items
        ]
        if files:
            db.executemany("INSERT INTO a_files VALUES (?, ?)", files)
        self.release_events = {
            str(row[0]) for row in db.execute("SELECT DISTINCT event_id FROM a_events").fetchall()
        }
        self.absent = {
            int(row[0])
            for row in db.execute(
                """
                SELECT DISTINCT wsdc_id FROM (
                    SELECT wsdc_id FROM a_entries UNION ALL SELECT wsdc_id FROM a_judges
                    UNION ALL SELECT wsdc_id FROM a_identity_links
                    UNION ALL SELECT wsdc_id FROM a_link_candidates
                    UNION ALL SELECT leader_wsdc_id FROM a_placements
                    UNION ALL SELECT follower_wsdc_id FROM a_placements
                ) WHERE wsdc_id IS NOT NULL
                AND wsdc_id NOT IN (SELECT wsdc_id FROM a_dancers WHERE wsdc_id IS NOT NULL)
                """
            ).fetchall()
        }
        self.absent_events = {
            str(row[0])
            for row in db.execute(
                "SELECT DISTINCT event_id FROM a_registry_placements WHERE event_id IS NOT NULL "
                "AND event_id NOT IN (SELECT event_id FROM a_events)"
            ).fetchall()
        }
        withheld_dancers: set[Any] = set()
        for _, digest in self.manifest.built:
            sidecar = self.manifest.sidecars.get(digest) or {}
            for dancer, _reason in sidecar.get("withheld_subjects", {}).get("dancers", ()):
                withheld_dancers.add(dancer)
        self.omissions, self.withheld_batches = omissions(self.manifest)
        from .partitions import dancer_batch

        self.withheld = lambda value: (
            value in withheld_dancers or (dancer_batch(value) in self.withheld_batches)
        )
        # Revocation: every (snapshot, parser) the rows name, checked once.
        self.revocations = Revocations(conn)
        sources = " UNION ALL ".join(
            f"SELECT snapshot_id, parser_version, filename FROM a_{table}"
            for table in (*DANCER_TABLES, *sorted(_EVENT_TABLES))
        )
        db.execute(
            f"CREATE TABLE a_support AS SELECT DISTINCT CAST(snapshot_id AS VARCHAR) AS s, "
            f"coalesce(CAST(parser_version AS VARCHAR), '') AS p FROM ({sources}) "
            "WHERE snapshot_id IS NOT NULL AND snapshot_id <> ''"
        )
        revoked = [
            (snapshot, parser)
            for snapshot, parser in db.execute(
                "SELECT s, p FROM a_support ORDER BY s, p"
            ).fetchall()
            if self.revocations.check((str(snapshot), str(parser)))
        ]
        db.execute("CREATE TABLE a_revoked(s VARCHAR, p VARCHAR)")
        if revoked:
            db.executemany("INSERT INTO a_revoked VALUES (?, ?)", revoked)

        def matching(table: str, select: str) -> str:
            return (
                f"SELECT {select} FROM a_{table} t JOIN a_revoked r ON r.s = t.snapshot_id "
                "AND r.p = coalesce(t.parser_version, '')"
            )

        self.revoked_dancers = {
            row[0] for row in db.execute(matching("dancers", "DISTINCT t.wsdc_id")).fetchall()
        }
        registry = db.execute(
            f"SELECT count(*), list(DISTINCT t.wsdc_id) FROM ({matching('registry_placements', 't.wsdc_id')}) t"
        ).fetchone() or (0, None)
        self.revocations.reasons["registry_fact_revoked"] = int(registry[0])
        self.registry_ids = set(registry[1] or ())
        self.revoked_partitions = sorted(
            str(row[0])
            for row in db.execute(
                "SELECT DISTINCT f.partition FROM ("
                + " UNION ALL ".join(
                    matching(table, "t.filename") for table in sorted(_EVENT_TABLES)
                )
                + ") x JOIN a_files f USING (filename)"
            ).fetchall()
        )
        # Close revoked event partitions first: the events left bound every
        # registry association written below.
        self.omitted: dict[str, set[str]] = {}
        omitted_years: set[Any] = set()
        for partition in self.revoked_partitions:
            with self.meter.unit("revoked_partition"):
                tables = self._load(partition, EVENT_TABLES)
                self.omitted[partition] = self.revocations.omitted_events(tables)
                omitted_years.update(
                    row.get("year")
                    for row in tables["events"]
                    if row.get("event_id") in self.omitted[partition]
                )
        self.omitted_years = omitted_years
        omitted = set().union(*self.omitted.values()) if self.omitted else set()
        self.revocations.reasons["event_scope_omitted"] = len(omitted)
        self.revocations.reasons["dancer_support_revoked"] = len(self.revoked_dancers)
        self.final_events = self.release_events - omitted

    # -- pass 2 ------------------------------------------------------------

    def _annotator(
        self, conn: sqlite3.Connection, support: Iterable[Mapping[str, Any]]
    ) -> _Coverage:
        """Coverage state known before any row: statuses, snapshots, omissions."""
        from swingset.sources import sources as registered_sources

        coverage = _Coverage(
            conn,
            BuildInput({"snapshots": self.snapshots}, SCHEMAS, PRIMARY_KEYS, {}, {}, ""),
            self.cutoff,
            {"source_support": support, "inventory": self.omissions},
            tuple(source.name for source in registered_sources()),
            self.mapping,
        )
        coverage.omissions()
        return coverage

    def _withhold(self, partition: str, reason: str, tables: Mapping[str, Any]) -> None:
        """Leave a partition that failed a builder check out of the release (I1).

        Its omission reaches coverage; its events leave the release's event
        set, and a dancer batch's ids become absent, so entries that name them
        lose the join (`close_keys`).
        """
        kind, _, name = partition.partition("/")
        label = f"assembly_check:{reason}"
        self.counts["assembly_withheld"] += 1
        self.counts[label] += 1
        self.assembly_withheld[partition] = label
        log("partition_assembly_withheld", partition=partition, reason=label)
        if kind == "event":
            events = set(name.split("+"))
            items = [
                {
                    "stage": "project",
                    "unit_kind": "event",
                    "unit_id": event,
                    "status": "withheld",
                    "reason": label,
                }
                for event in sorted(events)
            ]
            self.withheld_events |= events
            self.final_events -= events
        else:
            items = [
                {
                    "stage": "project",
                    "unit_kind": "dancer",
                    "unit_id": name,
                    "status": "withheld",
                    "reason": label,
                }
            ]
            self.withheld_batches.add(name)
            self.absent |= {
                row["wsdc_id"]
                for row in tables.get("dancers", ())
                if row.get("wsdc_id") is not None
            }
        for item in items:
            self.omissions.append(item)
            self.annotator.omit(item)

    def _finish_partition(self, stage: _Stage, partition: str, tables: dict[str, Any]) -> None:
        """Annotate and validate a partition whose rows are final, then stage them."""
        contests = {
            str(row["contest_id"]): row.get("event_id") for row in tables.get("contests", ())
        }
        assertions = {
            (str(row.get("subject_kind")), str(row.get("subject_id"))): row
            for row in tables.get("identity_links", ())
        }
        present = {name for name in tables if name in stage.tables}
        annotated = annotate(
            tables, contests=contests, assertions=assertions, coverage=self.annotator
        )
        tables.update({name: rows for name, rows in annotated.items() if name in present})
        if not self.suppressing:
            found = release_checks(tables, self.history_start)
            if found is not None:
                self._withhold(partition, found[0], tables)
                return
        stage.write(partition, tables)

    def _dancers(self) -> _Stage:
        stage = _Stage(self.directory / "dancers", DANCER_TABLES)
        self.stages.append(stage)
        for partition, _ in self.manifest.built:
            if not partition.startswith("dancers/"):
                continue
            with self.meter.unit("dancer_batch"):
                tables = self._load(partition, DANCER_TABLES)
                before = {name: len(rows) for name, rows in tables.items()}
                for row in tables["registry_placements"]:
                    if row.get("event_id") is not None and row["event_id"] in self.absent_events:
                        row["event_id"] = None
                        self.counts["registry_association_outside_release"] += 1
                self.revocations.close_registry(tables, self.revoked_dancers, self.final_events)
                self.revocations.withdraw(tables, self.revoked_dancers, self.registry_ids)
                for name, count in before.items():
                    self.removed[name] += count - len(tables[name])
                if self.suppressing:
                    stage.write(partition, tables)
                else:
                    self._finish_partition(stage, partition, tables)
        stage.close()
        db = self.db
        db.execute(
            "CREATE TABLE r_registry AS SELECT wsdc_id, event_id, role, division, dance_style, "
            f"result FROM {stage.relation('registry_placements')} WHERE event_id IS NOT NULL"
        )
        db.execute(
            "CREATE TABLE r_names AS SELECT wsdc_id, first_name, last_name, name_norm "
            f"FROM {stage.relation('dancers')}"
        )
        return stage

    def _baseline_subjects(self) -> None:
        """The baseline's entries and judges, verified, in DuckDB for per-event lookups."""
        from .identity_policy import verify_baseline

        db = self.db
        for table, key in (("entries", "entry_id"), ("judges", "judge_id")):
            files = (
                sorted((self.baseline / "data" / table).glob("*.parquet"))
                if self.baseline is not None
                else []
            )
            if files:
                names = set(pq.read_schema(files[0]).names)
                columns = ", ".join(
                    _quoted(name) if name in names else f"NULL AS {_quoted(name)}"
                    for name in (key, "wsdc_id", "snapshot_id", "parser_version")
                )
                db.execute(
                    f"CREATE TABLE b_{table} AS SELECT {columns} FROM "
                    f"read_parquet({_files(files)}, union_by_name=true)"
                )
            else:
                db.execute(
                    f"CREATE TABLE b_{table}({_quoted(key)} VARCHAR, wsdc_id INTEGER, "
                    "snapshot_id VARCHAR, parser_version VARCHAR)"
                )
        if self.baseline is not None:
            verify_baseline(self.baseline)

    def _query(self, sql: str, **tables: pa.Table) -> list[dict[str, Any]]:
        for name, value in tables.items():
            self.db.register(name, value)
        try:
            return _rows(self.db.execute(sql).to_arrow_table(), self.meter)
        finally:
            for name in tables:
                self.db.unregister(name)

    def _previous(self, tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
        result: dict[str, list[dict[str, Any]]] = {}
        for table, key in (("entries", "entry_id"), ("judges", "judge_id")):
            ids = [str(row[key]) for row in tables.get(table, ())]
            result[table] = (
                self._query(
                    f"SELECT * FROM b_{table} WHERE {_quoted(key)} IN (SELECT id FROM ids)",
                    ids=pa.table({"id": pa.array(ids, pa.string())}),
                )
                if ids and self.baseline is not None
                else []
            )
        return result

    def _registry(self, tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
        """The registry facts naming this partition's placements' events, and their dancers."""
        events = sorted(
            {str(row["event_id"]) for row in tables.get("placements", ()) if row.get("event_id")}
        )
        if not events:
            return {"dancers": [], "registry_placements": []}
        registry = self._query(
            "SELECT * FROM r_registry WHERE event_id IN (SELECT id FROM ids)",
            ids=pa.table({"id": pa.array(events, pa.string())}),
        )
        dancers = sorted({row["wsdc_id"] for row in registry if row.get("wsdc_id") is not None})
        names = (
            self._query(
                "SELECT * FROM r_names WHERE wsdc_id IN (SELECT id FROM ids)",
                ids=pa.table({"id": pa.array(dancers, pa.int32())}),
            )
            if dancers
            else []
        )
        return {"dancers": names, "registry_placements": registry}

    def _events(self, conn: sqlite3.Connection) -> _Stage:
        from .identity_policy import IdentityPolicy

        stage = _Stage(self.directory / "events", EVENT_TABLES)
        self.stages.append(stage)
        self.identity = IdentityPolicy(conn, baseline=self.baseline)
        self.closed_ids: set[str] = set()
        for partition, _ in self.manifest.built:
            if not partition.startswith("event/"):
                continue
            with self.meter.unit("event_partition"):
                tables = self._load(partition, (*EVENT_TABLES, "source_event_map"))
                self.mapping.extend(tables.pop("source_event_map"))
                close_keys(tables, absent=self.absent, withheld=self.withheld, counts=self.counts)
                before = {name: len(rows) for name, rows in tables.items()}
                structural = (
                    {name: list(tables[name]) for name, _ in REVIEW_SUBJECTS}
                    if partition in self.omitted
                    else None
                )
                self.revocations.close_events(tables, self.omitted.get(partition, set()))
                self.revocations.withdraw(tables, self.revoked_dancers, self.registry_ids)
                close_subjects(tables)
                if structural is not None:
                    self.closed_ids |= removed_ids(structural, tables)
                for name, count in before.items():
                    self.removed[name] += count - len(tables[name])
                self.identity.apply(
                    {"contests": tables["contests"], **self._registry(tables)},
                    tables,
                    self._previous(tables),
                )
                if self.suppressing:
                    stage.write(partition, tables)
                else:
                    self._finish_partition(stage, partition, tables)
        stage.close()
        return stage

    def _detach(self, _partition: str, tables: dict[str, list[dict[str, Any]]]) -> None:
        """Detach registry placements from events a builder check withheld."""
        for row in tables.get("registry_placements", ()):
            if row.get("event_id") in self.withheld_events:
                row["event_id"] = None
                self.counts["registry_association_outside_release"] += 1

    def _failing(self, stages: Sequence[_Stage]) -> dict[str, tuple[str, dict[str, Any]]]:
        """Each staged partition whose rows fail a builder check, with its reason."""
        failing: dict[str, tuple[str, dict[str, Any]]] = {}
        for stage in stages:
            for partition in stage.order:
                with self.meter.unit("validate"):
                    tables = stage.read(partition, self.meter)
                    found = release_checks(tables, self.history_start)
                    if found is not None:
                        failing[partition] = (found[0], tables)
        return failing

    def _drop(
        self, stages: Sequence[_Stage], failing: Mapping[str, tuple[str, dict[str, Any]]]
    ) -> list[_Stage]:
        """Withhold the failing partitions: re-stream without their rows."""
        if not failing:
            return list(stages)
        for partition, (reason, tables) in sorted(failing.items()):
            self._withhold(partition, reason, tables)
        dropped = set(failing)

        def change(partition: str, tables: dict[str, list[dict[str, Any]]]) -> None:
            if partition in dropped:
                for name in tables:
                    tables[name] = []
                return
            self._detach(partition, tables)
            close_keys(tables, absent=self.absent, withheld=self.withheld, counts=self.counts)

        return self._restream(stages, f"withheld{len(self.stages)}", change)

    # -- suppression -------------------------------------------------------

    def _learn(self, policy: SuppressionPolicy, stages: Sequence[_Stage]) -> None:
        """`SuppressionPolicy.apply`'s fixed point, one staged batch at a time."""
        while True:
            before = policy.size()
            for table in SUBJECT_TABLES:
                for stage in stages:
                    if table in stage.tables:
                        for rows in stage.batches(table, self.meter):
                            policy.learn(table, rows)
            if before == policy.size():
                break
        policy.prepare()

    def _restream(
        self,
        stages: Sequence[_Stage],
        name: str,
        change: Callable[[str, dict[str, list[dict[str, Any]]]], None],
    ) -> list[_Stage]:
        result = []
        for stage in stages:
            target = _Stage(self.directory / f"{name}-{stage.directory.name}", stage.tables)
            self.stages.append(target)
            for partition in stage.order:
                with self.meter.unit("restream"):
                    tables = stage.read(partition, self.meter)
                    change(partition, tables)
                    target.write(partition, tables)
            target.close()
            result.append(target)
        return result

    def _suppress(self, stages: Sequence[_Stage], name: str) -> list[_Stage]:
        """Suppression before coverage (the `whole` path's first policy)."""
        policy = SuppressionPolicy(self.suppressions)
        self._learn(policy, stages)
        release = {"snapshots": self.snapshots, "review_queue": self.review_queue}
        release["coverage"] = self.coverage
        policy.transform(release)

        def change(partition: str, tables: dict[str, list[dict[str, Any]]]) -> None:
            policy.transform(tables)
            contests = {
                str(row["contest_id"]): row.get("event_id") for row in tables.get("contests", ())
            }
            assertions = {
                (str(row.get("subject_kind")), str(row.get("subject_id"))): row
                for row in tables.get("identity_links", ())
            }
            present = set(tables)
            annotated = annotate(
                tables, contests=contests, assertions=assertions, coverage=self.annotator
            )
            tables.update({key: rows for key, rows in annotated.items() if key in present})

        return self._restream(stages, name, change)

    # -- coverage ----------------------------------------------------------

    def fact_counts(
        self, stages: Sequence[_Stage], *, mode: str, snapshots: Sequence[Mapping[str, Any]]
    ) -> Facts:
        """`_Coverage.facts` (``mode='enrich'``) or `identity_counts` (``'refresh'``) in SQL."""
        db = self.db
        relation = {table: stage.relation(table) for stage in stages for table in stage.tables}
        rows = []
        for row in snapshots:
            snapshot = str(row["snapshot_id"])
            observed = None
            if mode == "enrich":
                value = _at(
                    row.get("observed_at") or row.get("captured_at") or row.get("fetched_at")
                )
                observed = value if value is not None and value <= self.cutoff else None
            rows.append((snapshot, row.get("via"), observed))
        db.execute("DROP TABLE IF EXISTS f_snap")
        db.execute("CREATE TABLE f_snap(snapshot_id VARCHAR, via VARCHAR, observed TIMESTAMPTZ)")
        if rows:
            db.executemany("INSERT INTO f_snap VALUES (?, ?, ?)", rows)
        self.meter.take(len(rows), "snapshots")

        def event(table: str, *, identity: bool = False) -> str:
            columns = set(SCHEMAS[table].names)
            own = "NULLIF(CAST(t.event_id AS VARCHAR), '')" if "event_id" in columns else "NULL"
            if identity and mode == "enrich":
                return "CASE WHEN t.event_id IS NULL THEN 'None' ELSE NULLIF(t.event_id, '') END"
            via = "NULLIF(CAST(c.event_id AS VARCHAR), '')" if "contest_id" in columns else "NULL"
            return f"coalesce({own}, {via})"

        def joined(table: str) -> str:
            if "contest_id" in SCHEMAS[table].names:
                return (
                    f"{relation[table]} t LEFT JOIN ct c "
                    "ON c.contest_id = coalesce(CAST(t.contest_id AS VARCHAR), 'None')"
                )
            return f"{relation[table]} t"

        common = (
            "coalesce(NULLIF(CAST(t.source AS VARCHAR), ''), 'unknown') AS source, "
            "coalesce(CAST(t.snapshot_id AS VARCHAR), '') AS snapshot_id, "
            "CAST(t.parser_version AS VARCHAR) AS parser_version"
        )
        parts = [
            f"SELECT '{table}' AS metric, CAST(t.{key} AS VARCHAR) AS ident, {common}, "
            f"{event(table)} AS ev FROM {joined(table)}"
            for table, key in (
                ("events", "event_id"),
                ("contests", "contest_id"),
                ("rounds", "round_id"),
                ("entries", "entry_id"),
            )
        ]
        parts.append(
            f"SELECT 'events', CAST(t.event_id AS VARCHAR), {common}, CAST(t.event_id AS VARCHAR) "
            f"FROM {relation['registry_placements']} t "
            "WHERE t.event_id IS NOT NULL AND t.event_id <> ''"
        )
        for table, kind, key in (("entries", "entry", "entry_id"), ("judges", "judge", "judge_id")):
            ev = event(table, identity=True)
            base = (
                f"FROM {joined(table)} LEFT JOIN il a ON a.subject_kind = '{kind}' "
                f"AND a.subject_id = CAST(t.{key} AS VARCHAR)"
            )
            ident = f"CAST(t.{key} AS VARCHAR)"
            parts.append(f"SELECT 'identity_subjects', {ident}, {common}, {ev} {base}")
            parts.append(
                f"SELECT 'resolved_identities', {ident}, {common}, {ev} {base} "
                "WHERE t.wsdc_id IS NOT NULL"
            )
            parts.append(
                f"SELECT 'withheld_identities', {ident}, {common}, {ev} {base} "
                "WHERE t.wsdc_id IS NULL AND a.wsdc_id IS NOT NULL "
                "AND a.acceptance_state IS DISTINCT FROM 'accepted'"
            )
        if mode == "enrich":
            parts.append(
                f"SELECT 'dancer_records', CAST(t.wsdc_id AS VARCHAR), {common}, NULL "
                f"FROM {relation['dancers']} t"
            )
        db.execute("DROP TABLE IF EXISTS f_rec")
        db.execute(
            f"""
            CREATE TABLE f_rec AS
            WITH ct AS (SELECT CAST(contest_id AS VARCHAR) AS contest_id, event_id
                        FROM {relation["contests"]}),
                 il AS (SELECT CAST(subject_kind AS VARCHAR) AS subject_kind,
                               CAST(subject_id AS VARCHAR) AS subject_id, wsdc_id,
                               acceptance_state FROM {relation["identity_links"]})
            {" UNION ALL ".join(parts)}
            """
        )
        db.execute("DROP TABLE IF EXISTS f_scope")
        db.execute(
            f"""
            CREATE TABLE f_scope AS
            WITH ev AS (SELECT CAST(event_id AS VARCHAR) AS event_id, year
                        FROM {relation["events"]}),
                 rec AS (SELECT DISTINCT metric, ident, source, snapshot_id, ev FROM f_rec
                         UNION SELECT 'events', ev, source, snapshot_id, ev
                         FROM f_rec WHERE ev IS NOT NULL),
                 scoped AS (
                    SELECT r.metric, r.ident, r.source, r.ev, s.observed, e.event_id AS released,
                           e.year,
                           coalesce(NULLIF(s.via, ''), CASE WHEN r.snapshot_id = 'override'
                                    THEN 'manual' ELSE 'origin' END) AS via
                    FROM rec r LEFT JOIN f_snap s ON s.snapshot_id = r.snapshot_id
                    LEFT JOIN ev e ON e.event_id = r.ev)
            SELECT 'source' AS kind, source AS id, source, via, metric, ident, observed FROM scoped
            UNION ALL SELECT 'event', ev, source, via, metric, ident, observed FROM scoped
                WHERE released IS NOT NULL
            UNION ALL SELECT 'year', CAST(year AS VARCHAR), source, via, metric, ident, observed
                FROM scoped WHERE released IS NOT NULL AND year IS NOT NULL
            """
        )
        metrics = ", ".join(
            f"count(*) FILTER (WHERE metric = '{name}') AS {name}" for name in FACT_METRICS
        )
        facts = Facts()
        for row in self._query(
            f"""
            WITH idents AS (SELECT DISTINCT kind, id, source, via, metric, ident FROM f_scope),
            counted AS (SELECT kind, id, source, via, {metrics} FROM idents GROUP BY ALL),
            timed AS (
                SELECT kind, id, source, via, max(observed) AS observed,
                       bool_or(observed IS NULL) AS unknown_time
                FROM f_scope GROUP BY ALL),
            summary AS (
                SELECT d.kind, d.id, d.source, d.via,
                    count(*) FILTER (WHERE e.coverage_tier = 'registry_only') AS events_registry_only,
                    count(*) FILTER (WHERE e.coverage_tier = 'index_only') AS events_index_only,
                    count(*) FILTER (WHERE e.coverage_tier = 'sheets_partial') AS events_sheets_partial,
                    count(*) FILTER (WHERE e.coverage_tier = 'sheets_complete') AS events_sheets_complete,
                    count(*) FILTER (WHERE e.date_precision = 'day') AS events_day_precision,
                    count(*) FILTER (WHERE e.held IS DISTINCT FROM 'held') AS events_listed_only
                FROM (SELECT kind, id, source, via, ident FROM idents WHERE metric = 'events') d
                JOIN (SELECT CAST(event_id AS VARCHAR) AS event_id, coverage_tier,
                             date_precision, held FROM {relation["events"]}) e
                  ON e.event_id = d.ident
                GROUP BY ALL)
            SELECT t.*, {", ".join(f"coalesce(c.{name}, 0) AS {name}" for name in FACT_METRICS)},
                   {", ".join(f"coalesce(s.{name}, 0) AS {name}" for name in TIER_FIELDS)}
            FROM timed t LEFT JOIN counted c USING (kind, id, source, via)
            LEFT JOIN summary s USING (kind, id, source, via)
            """
        ):
            scope: Scope = (str(row["kind"]), str(row["id"]), str(row["source"]), str(row["via"]))
            facts.counts[scope] = {name: int(row[name]) for name in (*FACT_METRICS, *TIER_FIELDS)}
            if row["observed"] is not None:
                facts.observed[scope] = row["observed"]
            if row["unknown_time"]:
                facts.unknown_time.add(scope)
        if mode == "enrich":
            for snapshot, events in db.execute(
                "SELECT snapshot_id, list(DISTINCT ev) FROM f_rec WHERE snapshot_id <> '' "
                "GROUP BY snapshot_id"
            ).fetchall():
                facts.selected_snapshots.add(str(snapshot))
                values = {str(value) for value in events or () if value is not None}
                if values:
                    facts.snapshot_events[str(snapshot)] = values
            for snapshot, parsers in db.execute(
                "SELECT snapshot_id, list(DISTINCT parser_version) FROM f_rec "
                "WHERE snapshot_id <> '' AND parser_version IS NOT NULL GROUP BY snapshot_id"
            ).fetchall():
                facts.snapshot_parsers[str(snapshot)] = {str(value) for value in parsers}
        db.execute("DROP TABLE f_scope")
        db.execute("DROP TABLE f_rec")
        db.execute("DROP TABLE f_snap")
        return facts

    # -- run ---------------------------------------------------------------

    def run(
        self,
        conn: sqlite3.Connection,
        *,
        support: Iterable[Mapping[str, Any]],
        data: BuildInput,
        policy: Mapping[str, Any],
    ) -> BuildInput:
        """Everything that reads the database, inside the build's read snapshot.

        ``data`` carries revisions, captured files and the bundle; ``policy``
        the partitioned release's own policy fields. Returns the release's
        `BuildInput` whose tables are the release-level lists only.
        """
        from .event_coverage import capture as capture_events
        from .event_coverage import rows as event_rows
        from .input import _review_queue

        self.removed: Counter[str] = Counter()
        self.mapping: list[dict[str, Any]] = []
        #: Partitions a builder check withheld at assembly, with their reason.
        self.assembly_withheld: dict[str, str] = {}
        self.withheld_events: set[str] = set()
        with self.meter.unit("pass_one"):
            self._pass_one(conn)
        wanted = {str(item[0]) for item in self.manifest.support}
        with self.meter.unit("snapshots"):
            self.snapshots = list(_snapshot_rows(conn, wanted))
            self.meter.take(len(self.snapshots))
        with self.meter.unit("review_queue"):
            self.review_queue = _review_queue(conn, {"findings"})
            self.meter.take(len(self.review_queue))
        with self.meter.unit("year_fragments"):
            fragments: list[dict[str, Any]] = []
            for partition, _ in self.manifest.built:
                if partition.startswith("year/"):
                    fragments.extend(self._load(partition, ("coverage",))["coverage"])
            kept = [row for row in fragments if row.get("year") not in self.omitted_years]
            self.removed["coverage"] += len(fragments) - len(kept)
            self.coverage = kept
        self.annotator = self._annotator(conn, support)
        self._baseline_subjects()
        dancers = self._dancers()
        events = self._events(conn)
        if self.withheld_events:
            # Placements of the dancers pass may name an event a check withheld.
            dancers = self._restream([dancers], "detached", self._detach)[0]
        # Free what only pass 2 read, so later queries reuse DuckDB's memory.
        for table in ("r_registry", "r_names", "b_entries", "b_judges", "a_support", "a_revoked"):
            self.db.execute(f"DROP TABLE {table}")
        # The source_event_map rows arrive with their events; the annotator
        # only needs them for coverage, which runs below.
        self.annotator.selected_mapping = tuple(self.mapping)
        before = len(self.review_queue)
        self.review_queue = [
            row for row in self.review_queue if str(row.get("subject_id")) not in self.closed_ids
        ]
        self.removed["review_queue"] += before - len(self.review_queue)
        self.revocations.reasons["support_checks"] = len(self.revocations.cache)
        stages = [dancers, events]
        if self.suppressing:
            stages = self._suppress(stages, "suppressed")
            # Validation waited for the suppression pass: check each
            # partition's final rows now, before coverage reads them.
            stages = self._drop(stages, self._failing(stages))
        self.current = stages
        release_policy = self.identity.release_policy(data, correction_only=False)
        release_policy.update(policy)
        release_policy["partitions"] = {
            **release_policy.get("partitions", {}),
            **dict(sorted(self.counts.items())),
        }
        release_policy["support_withdrawals"] = {
            "removed_rows": {name: count for name, count in sorted(self.removed.items()) if count},
            "withdrawn_claims": dict(sorted(self.revocations.claims.items())),
            "reasons": {
                name: count for name, count in sorted(self.revocations.reasons.items()) if count
            },
        }
        # Coverage: the year fragments, scope rows from aggregates, event rows.
        with self.meter.unit("coverage"):
            current = next(stage for stage in stages if "events" in stage.tables)
            events_rows = [row for rows in current.batches("events", self.meter) for row in rows]
            coverage = self.annotator
            coverage.snapshots = {str(row["snapshot_id"]): row for row in self.snapshots}
            coverage.release(events_rows, self.coverage)
            coverage.absorb(self.fact_counts(stages, mode="enrich", snapshots=self.snapshots))
            coverage.inventory()
            rows = coverage.rows()
            self.witness = capture_events(
                conn, cutoff=self.cutoff.isoformat(), selected_support=support
            )
            if self.witness is not None:
                rows.extend(
                    event_rows(self.witness, selected_mapping=self.mapping, schemas=SCHEMAS)
                )
            self.coverage = rows
            self.meter.take(len(rows))
        self.data = replace(
            data,
            tables={
                "snapshots": self.snapshots,
                "review_queue": self.review_queue,
                "coverage": self.coverage,
            },
            schemas=SCHEMAS,
            primary_keys=PRIMARY_KEYS,
            release_policy=release_policy,
        )
        return self.data

    # -- the candidate -----------------------------------------------------

    def _finalize(self, stages: Sequence[_Stage]) -> None:
        """`refresh_identity_counts` and event coverage's `finalize`, from SQL."""
        from .event_coverage import finalize

        counts = self.fact_counts(stages, mode="refresh", snapshots=self.snapshots)
        apply_identity_counts(self.coverage, counts.counts)
        if self.witness is not None:
            relation = {table: stage.relation(table) for stage in stages for table in stage.tables}
            result_tables = [
                name
                for name in (
                    "contests",
                    "rounds",
                    "heats",
                    "entries",
                    "judges",
                    "callbacks",
                    "callback_marks",
                    "final_marks",
                    "placements",
                )
                if name in relation
            ]
            snapshots = [
                {"snapshot_id": row[0]}
                for row in self.db.execute(
                    "SELECT DISTINCT CAST(snapshot_id AS VARCHAR) FROM ("
                    + " UNION ALL ".join(
                        f"SELECT snapshot_id FROM {relation[name]}" for name in result_tables
                    )
                    + ") WHERE snapshot_id IS NOT NULL AND snapshot_id <> ''"
                ).fetchall()
            ]
            finalize({"contests": snapshots, "coverage": self.coverage}, self.witness)

    def _write_table(self, path: Path, table: str, source: str, where: str = "") -> Path:
        schema = SCHEMAS[table]
        columns = ", ".join(_quoted(name) for name in schema.names)
        order = ", ".join(_quoted(name) for name in PRIMARY_KEYS[table])
        reader = self.db.execute(
            f"SELECT {columns} FROM {source} {where} ORDER BY {order}"
        ).to_arrow_reader(WRITE_BATCH_ROWS)
        with pq.ParquetWriter(
            path,
            schema,
            compression="zstd",
            write_page_index=True,
            use_content_defined_chunking=True,
        ) as writer:
            wrote = False
            for batch in reader:
                writer.write_table(pa.Table.from_batches([batch]).cast(schema))
                wrote = True
            if not wrote:
                writer.write_table(schema.empty_table())
        return path

    def _write_tables(self, root: Path, stages: Sequence[_Stage]) -> dict[str, list[Path]]:
        """Every table sorted by primary key into ``root/data``; marks by event year."""
        relation = {table: stage.relation(table) for stage in stages for table in stage.tables}
        data = root / "data"
        if data.exists():
            shutil.rmtree(data)
        files: dict[str, list[Path]] = {}
        for table in PUBLISHED_TABLES:
            if table == "changelog":
                continue
            directory = data / table
            directory.mkdir(parents=True)
            if table not in relation:
                rows = {
                    "snapshots": self.snapshots,
                    "review_queue": self.review_queue,
                    "coverage": self.coverage,
                }[table]
                path = directory / f"{table}.parquet"
                _write_parquet(
                    pa.Table.from_pylist(_sorted(table, rows), schema=SCHEMAS[table]), path
                )
                files[table] = [path]
                continue
            if table in _MARKS:
                source = (
                    f"(SELECT m.*, CAST(e.year AS VARCHAR) AS __year FROM {relation[table]} m "
                    f"LEFT JOIN {relation['rounds']} r ON r.round_id = m.round_id "
                    f"LEFT JOIN {relation['contests']} c ON c.contest_id = r.contest_id "
                    f"LEFT JOIN {relation['events']} e ON e.event_id = c.event_id)"
                )
                years = [
                    row[0]
                    for row in self.db.execute(f"SELECT DISTINCT __year FROM {source}").fetchall()
                ]
                if not years:
                    files[table] = [
                        self._write_table(directory / f"{table}.parquet", table, relation[table])
                    ]
                    continue
                files[table] = [
                    self._write_table(
                        directory / f"year={year if year is not None else 'unknown'}.parquet",
                        table,
                        source,
                        "WHERE __year IS NULL" if year is None else f"WHERE __year = '{year}'",
                    )
                    for year in sorted(years, key=str)
                ]
                continue
            files[table] = [
                self._write_table(directory / f"{table}.parquet", table, relation[table])
            ]
        return files

    def write(
        self, meta: BuildMetadata, card_renderer: Callable[[BuildInput], bytes] | None
    ) -> BuildResult:
        """Build the candidate from the staged tables (no database reads)."""
        assert self.data is not None
        baseline = self.baseline
        history = sorted((baseline / "data" / "changelog").glob("*.parquet")) if baseline else []
        build_time = meta.built_at
        assert build_time is not None

        def write(temporary: Path) -> StreamedTables:
            stages = self.current
            policy = SuppressionPolicy(self.suppressions)
            passes = 0

            def suppress(stages: Sequence[_Stage]) -> list[_Stage]:
                nonlocal passes
                passes += 1
                self._learn(policy, stages)
                release = {
                    "snapshots": self.snapshots,
                    "review_queue": self.review_queue,
                    "coverage": self.coverage,
                }
                policy.transform(release)
                return self._restream(
                    stages, f"pass{passes}", lambda _partition, tables: policy.transform(tables)
                )

            if policy.active:
                stages = suppress(stages)
            with generated_delta(temporary / "delta.sqlite", PRIMARY_KEYS["changelog"]) as delta:
                for _pass in range(32):
                    discovered = policy.discover_history(_sorted_parquet_rows(history))
                    if policy.active:
                        stages = suppress(stages)
                    self._finalize(stages)
                    files = self._write_tables(temporary, stages)
                    delta.replace(
                        change
                        for table in PUBLISHED_TABLES
                        if table != "changelog"
                        for change in joined_changes(
                            self.db,
                            table,
                            files[table],
                            baseline,
                            PRIMARY_KEYS[table],
                            changed_at=build_time,
                            run_id=meta.run_id,
                            batch_rows=BATCH_ROWS,
                            meter=self.meter,
                        )
                    )
                    discovered |= policy.discover_history(delta)
                    if not discovered:
                        break
                else:
                    raise BuildError("suppression history did not converge within 32 passes")
                if policy.active or self.suppressing:
                    late = self._failing(stages)
                    if late:
                        # A suppression the history discovered left a partition
                        # failing: withhold it and write the tables once more.
                        # Coverage keeps its counts from before (documented).
                        stages = self._drop(stages, late)
                        self._finalize(stages)
                        files = self._write_tables(temporary, stages)
                        delta.replace(
                            change
                            for table in PUBLISHED_TABLES
                            if table != "changelog"
                            for change in joined_changes(
                                self.db,
                                table,
                                files[table],
                                baseline,
                                PRIMARY_KEYS[table],
                                changed_at=build_time,
                                run_id=meta.run_id,
                                batch_rows=BATCH_ROWS,
                                meter=self.meter,
                            )
                        )
                        assert self.data is not None
                        policy_value = self.data.release_policy
                        if isinstance(policy_value, dict):
                            policy_value.setdefault("partitions", {}).update(
                                dict(sorted(self.counts.items()))
                            )
                    self._validate(stages)
                directory = temporary / "data" / "changelog"
                directory.mkdir(parents=True)
                path = directory / "changelog.parquet"
                changelog_rows = _write_changelog(
                    history,
                    delta.sorted_rows(),
                    path,
                    SCHEMAS["changelog"],
                    PRIMARY_KEYS["changelog"],
                    policy,
                )
                files["changelog"] = [path]
            from .files import sha256_file

            hashes = {
                path.relative_to(temporary).as_posix(): sha256_file(path)
                for table in PUBLISHED_TABLES
                for path in files[table]
            }
            row_counts = {
                table: sum(pq.ParquetFile(path).metadata.num_rows for path in files[table])
                for table in PUBLISHED_TABLES
            }
            row_counts["changelog"] = changelog_rows
            events = _files(files["events"])
            placements = _files(files["placements"])
            horizon, covered = self.db.execute(
                f"""
                SELECT
                  (SELECT max(CAST(end_date AS VARCHAR)) FROM read_parquet({events})
                   WHERE end_date IS NOT NULL),
                  (SELECT max(CAST(end_date AS VARCHAR)) FROM read_parquet({events})
                   WHERE end_date IS NOT NULL
                   AND event_id IN (SELECT event_id FROM read_parquet({placements})))
                """
            ).fetchone() or (None, None)
            snapshots = self.snapshots
            return StreamedTables(
                hashes=hashes,
                files=files,
                facts=CandidateFacts(
                    row_counts=row_counts,
                    source_snapshot_counts={
                        source: sum(1 for row in snapshots if row.get("source") == source)
                        for source in sorted(
                            {str(row["source"]) for row in snapshots if row.get("source")}
                        )
                    },
                    calendar_horizon=horizon,
                    latest_event_covered=covered,
                ),
            )

        return build_streamed_candidate(
            self.state_dir,
            self.data,
            meta,
            write=write,
            card_renderer=card_renderer,
            meter=self.meter,
        )

    def _validate(self, stages: Sequence[_Stage]) -> None:
        """The builder's checks per partition, after the last suppression pass."""
        for stage in stages:
            for partition in stage.order:
                with self.meter.unit("validate"):
                    try:
                        _validate(stage.read(partition, self.meter), self.history_start)
                    except BuildError as error:
                        raise BuildError(f"{partition}: {error}") from error

    def report(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "assembly": "streamed",
            "assembly_seconds": round(time.monotonic() - self.started, 3),
            "assembly_peak_rows": self.meter.peak,
        }
        if getattr(self, "assembly_withheld", None):
            # Stage 1's rows and the release manifest still say `built`; the
            # watchdog's [partitions-withheld] reads these from the record.
            result["assembly_withheld"] = dict(sorted(self.assembly_withheld.items()))
        return result
