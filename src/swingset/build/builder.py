from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, overload

import pyarrow as pa
import pyarrow.parquet as pq

from swingset.build.changelog import changes, generated_delta, sort_key
from swingset.build.files import canonical_json, durable_write, fsync_dir, sha256_file
from swingset.build.suppression import SuppressionPolicy, apply_suppressions
from swingset.model import enums
from swingset.model.history import HISTORY_START, in_history

PUBLISHED_TABLES = (
    "events",
    "contests",
    "rounds",
    "entries",
    "heats",
    "judges",
    "callback_marks",
    "callbacks",
    "final_marks",
    "placements",
    "dancers",
    "registry_placements",
    "identity_links",
    "link_candidates",
    "review_queue",
    "changelog",
    "snapshots",
    "coverage",
)
DATASET_LICENSE = b"Open Data Commons Attribution License (ODC-By) v1.0\nhttps://opendatacommons.org/licenses/by/1-0/\n"


@dataclass(frozen=True)
class BuildInput:
    tables: Mapping[str, Sequence[Mapping[str, Any]]]
    schemas: Mapping[str, Any]
    primary_keys: Mapping[str, tuple[str, ...]]
    revisions: Mapping[str, int]
    captured_file_hashes: Mapping[str, str]
    input_bundle_hash: str
    history_start: date = HISTORY_START
    release_policy: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BuildMetadata:
    run_id: str
    repository_commit: str
    expected_parent: str | None
    schema_version: int
    versions: Mapping[str, str]
    card: bytes
    built_at: datetime | None = None
    candidate_id: str | None = None
    input_paths: Mapping[str, str] | None = None
    #: `overrides/release_decreases.csv` for the publication gate (D-0225): its
    #: bytes, parsed by the gate, which refuses on a malformed file.
    decrease_overrides: bytes | Sequence[Mapping[str, str]] = ()
    #: A correction-only candidate: its verdict records decreases but never refuses.
    correction_only: bool = False


@dataclass(frozen=True)
class BuildResult:
    candidate_id: str
    path: Path
    content_hash: str
    manifest_hash: str
    changed: bool
    reused: bool


class BuildError(RuntimeError):
    pass


ENUM_FIELDS: dict[tuple[str, str], type[StrEnum]] = {
    ("events", "wsdc_status"): enums.WSDCStatus,
    ("contests", "division"): enums.Division,
    ("contests", "age_division"): enums.AgeDivision,
    ("contests", "contest_type"): enums.ContestType,
    ("contests", "partner_mode"): enums.PartnerMode,
    ("contests", "dance_style"): enums.DanceStyle,
    ("contests", "parse_status"): enums.ParseStatus,
    ("rounds", "round_type"): enums.RoundType,
    ("rounds", "scoring_method"): enums.ScoringMethod,
    ("rounds", "callback_legend"): enums.CallbackLegend,
    ("entries", "role"): enums.Role,
    ("entries", "link_status"): enums.LinkStatus,
    ("callback_marks", "mark"): enums.CallbackMark,
    ("callbacks", "outcome"): enums.CallbackOutcome,
    ("registry_placements", "role"): enums.Role,
    ("registry_placements", "dance_style"): enums.DanceStyle,
    ("registry_placements", "division"): enums.RegistryDivision,
    ("identity_links", "subject_kind"): enums.SubjectKind,
    ("identity_links", "method"): enums.LinkMethod,
    ("identity_links", "status"): enums.LinkStatus,
    ("link_candidates", "subject_kind"): enums.SubjectKind,
}


def _hash_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _row_key(row: Mapping[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in fields)


def _baseline(state_dir: Path) -> tuple[Path | None, str | None, str | None]:
    link = state_dir / "baseline"
    if not link.is_symlink():
        return None, None, None
    target = (link.parent / os.readlink(link)).resolve()
    built = json.loads((target / "BUILT").read_text())
    published = json.loads((target / "PUBLISHED").read_text())
    return target, str(published["commit"]), str(built["content_hash"])


def _fingerprint(data: BuildInput, meta: BuildMetadata) -> str:
    schema_text = {name: str(schema) for name, schema in data.schemas.items()}
    return _hash_json(
        {
            "revisions": data.revisions,
            "files": data.captured_file_hashes,
            "schemas": schema_text,
            "versions": meta.versions,
            "card": hashlib.sha256(meta.card).hexdigest(),
            "bundle": data.input_bundle_hash,
            "release_policy": data.release_policy,
        }
    )


Rows = Mapping[str, Sequence[Mapping[str, Any]]]


def check_enums(rows: Rows) -> None:
    """Every enumerated column holds a known value."""
    for (table, field), enum_type in ENUM_FIELDS.items():
        allowed_values = {member.value for member in enum_type}
        for row in rows.get(table, []):
            value = row.get(field)
            if value is not None and value not in allowed_values:
                raise BuildError(f"unknown {table}.{field} enum value: {value!r}")


def check_events(rows: Rows, history_start: date) -> None:
    """Event dates, months, precision and closed vocabularies are consistent."""
    for row in rows.get("events", []):
        start, end = row.get("start_date"), row.get("end_date")
        if start is not None and end is not None and start > end:
            raise BuildError(f"event {row.get('event_id')} starts after it ends")
        month = row.get("event_month")
        if month is not None:
            try:
                month_date = date.fromisoformat(f"{month}-01")
            except ValueError as error:
                raise BuildError(f"invalid event month: {month!r}") from error
            if len(str(month)) != 7:
                raise BuildError(f"invalid event month: {month!r}")
        else:
            month_date = None
        if not in_history(
            history_start, end_date=end, start_date=start or month_date, year=row.get("year")
        ):
            raise BuildError(
                f"event {row.get('event_id')} ended before the history start {history_start}"
            )
        precision = row.get("date_precision")
        if precision == "day" and (start is None or end is None):
            raise BuildError(f"day-precision event {row.get('event_id')} needs both dates")
        if precision == "month" and (start is not None or end is not None or month is None):
            raise BuildError(f"month-precision event {row.get('event_id')} has inconsistent dates")
        for field, allowed_values in {
            "date_precision": {"day", "month"},
            "held": {"held", "listed", "cancelled"},
            "coverage_tier": {"registry_only", "index_only", "sheets_partial", "sheets_complete"},
        }.items():
            if row.get(field) is not None and row[field] not in allowed_values:
                raise BuildError(f"unknown events.{field}: {row[field]!r}")


def check_references(rows: Rows) -> None:
    """Marks, callbacks and heats name an entry, a round and a judge that exist."""
    entry_ids = {row["entry_id"] for row in rows.get("entries", [])}
    round_ids = {row["round_id"] for row in rows.get("rounds", [])}
    judge_ids = {row["judge_id"] for row in rows.get("judges", [])}
    for table in ("callback_marks", "callbacks", "heats"):
        for row in rows.get(table, []):
            if row.get("entry_id") not in entry_ids:
                raise BuildError(f"{table} references missing entry {row.get('entry_id')}")
    for table in ("callback_marks", "callbacks"):
        for row in rows.get(table, []):
            if row.get("round_id") not in round_ids:
                raise BuildError(f"{table} references missing round {row.get('round_id')}")
    for row in rows.get("callback_marks", []):
        if row.get("judge_id") not in judge_ids:
            raise BuildError(f"callback_marks references missing judge {row.get('judge_id')}")


def check_callbacks(rows: Rows) -> None:
    """Each callback's counts and score sum agree with its retained marks."""
    callback_marks: dict[tuple[Any, Any], list[Mapping[str, Any]]] = {}
    for row in rows.get("callback_marks", []):
        callback_marks.setdefault((row.get("round_id"), row.get("entry_id")), []).append(row)
    for callback in rows.get("callbacks", []):
        callback_key = callback.get("round_id"), callback.get("entry_id")
        marks = callback_marks.get(callback_key, [])
        expected = {
            "yes_count": sum(mark.get("mark") == "yes" for mark in marks),
            "alt_count": sum(str(mark.get("mark", "")).startswith("alt") for mark in marks),
            "no_count": sum(mark.get("mark") == "no" for mark in marks),
        }
        for field, value in expected.items():
            if callback.get(field) != value:
                raise BuildError(f"callback {callback_key!r} {field} disagrees with retained marks")
        score_sum = sum(float(mark.get("mark_value") or 0) for mark in marks)
        if not math.isclose(
            float(callback.get("score_sum") or 0), score_sum, rel_tol=1e-6, abs_tol=1e-4
        ):
            raise BuildError(f"callback {callback_key!r} score_sum disagrees with retained marks")


def check_placements(rows: Rows) -> None:
    """Placements name entries that exist, and places are contiguous and unique."""
    entry_ids = {row["entry_id"] for row in rows.get("entries", [])}
    for table, field in (
        ("placements", "leader_entry_id"),
        ("placements", "follower_entry_id"),
        ("placements", "couple_entry_id"),
    ):
        for row in rows.get(table, []):
            value = row.get(field)
            if value is not None and value not in entry_ids:
                raise BuildError(f"{table}.{field} references missing entry {value}")
    by_round: dict[str, list[int]] = {}
    for row in rows.get("placements", []):
        by_round.setdefault(str(row["round_id"]), []).append(int(row["place"]))
    for round_id, places in by_round.items():
        if sorted(set(places)) != list(range(1, max(places) + 1)) or len(set(places)) != len(
            places
        ):
            raise BuildError(f"placements are not contiguous and unique for {round_id}")


def check_identities(rows: Rows) -> None:
    """Linked judges, placements and entries carry the identity support they claim.

    A linked judge needs a confirmed assertion, a placement's dancer is its
    entry's confirmed dancer in the same role, a linked entry is confirmed, and
    one bib in a contest and role maps to one WSDC id.
    """
    allowed = {"confirmed"}
    confirmed_judges = {
        (row.get("subject_id"), row.get("wsdc_id"))
        for row in rows.get("identity_links", [])
        if row.get("subject_kind") == "judge" and row.get("status") == "confirmed"
    }
    for row in rows.get("judges", []):
        if (
            row.get("wsdc_id") is not None
            and (row.get("judge_id"), row["wsdc_id"]) not in confirmed_judges
        ):
            raise BuildError(
                f"linked judge {row.get('judge_id')} lacks a confirmed identity assertion"
            )
    entries_by_id = {row["entry_id"]: row for row in rows.get("entries", [])}
    for row in rows.get("placements", []):
        for role in ("leader", "follower"):
            dancer = row.get(f"{role}_wsdc_id")
            entry = entries_by_id.get(row.get(f"{role}_entry_id"))
            if dancer is not None and (
                entry is None
                or entry.get("wsdc_id") != dancer
                or entry.get("link_status") != "confirmed"
                or entry.get("role") != role
            ):
                raise BuildError(
                    f"placement {row.get('placement_id')} has an unsupported {role} identity"
                )
    bib_links: dict[tuple[Any, Any, Any], Any] = {}
    for row in rows.get("entries", []):
        wsdc_id = row.get("wsdc_id")
        if wsdc_id is not None and row.get("link_status") not in allowed:
            raise BuildError(f"linked entry {row.get('entry_id')} has invalid link status")
        if wsdc_id is not None and row.get("bib") is not None:
            key = row.get("contest_id"), row.get("role"), row.get("bib")
            previous = bib_links.setdefault(key, wsdc_id)
            if previous != wsdc_id:
                raise BuildError(f"bib {key!r} maps to multiple WSDC ids")


#: The builder's release checks, in the order `_validate` runs them. Each
#: reads only the rows it is given, so a partition's rows can be checked
#: alone (plan phase 3, WP6): every table they join lives in one event
#: partition. The name is the reason a failing partition is withheld with.
CHECKS: tuple[tuple[str, Callable[[Rows, date], None]], ...] = (
    ("invalid_enum", lambda rows, _start: check_enums(rows)),
    ("invalid_event_dates", check_events),
    ("missing_reference", lambda rows, _start: check_references(rows)),
    ("callback_counts_disagree", lambda rows, _start: check_callbacks(rows)),
    ("invalid_placements", lambda rows, _start: check_placements(rows)),
    ("unsupported_identity", lambda rows, _start: check_identities(rows)),
)


def _validate(rows: Rows, history_start: date) -> None:
    for _reason, check in CHECKS:
        check(rows, history_start)


def _changelog(
    current: Mapping[str, list[dict[str, Any]]],
    baseline: Path | None,
    keys: Mapping[str, tuple[str, ...]],
    *,
    changed_at: datetime,
    run_id: str,
) -> list[dict[str, Any]]:
    # Compatibility helper for callers explicitly requesting an eager result.
    # Candidate builds use changes()/generated_delta() directly.
    with tempfile.TemporaryDirectory(prefix="swingset-changelog-") as directory:
        return list(
            changes(
                current,
                baseline,
                keys,
                changed_at=changed_at,
                run_id=run_id,
                scratch=Path(directory) / "comparison.sqlite",
            )
        )


class ParquetRows(Sequence[Mapping[str, Any]]):
    """Lazy public rows for consumers that usually need only their count.

    Iteration reads one batch at a time; ``meter`` (the streamed assembly's
    row meter) counts the rows each batch materializes.
    """

    def __init__(self, path: Path | Sequence[Path], meter: Any = None) -> None:
        paths = [path] if isinstance(path, Path) else list(path)
        self.files = [pq.ParquetFile(item) for item in paths]
        self.meter = meter

    @property
    def file(self) -> pq.ParquetFile:
        return self.files[0]

    def __len__(self) -> int:
        return sum(int(file.metadata.num_rows) for file in self.files)

    def _batches(self) -> Iterator[pa.RecordBatch]:
        for file in self.files:
            for batch in file.iter_batches(batch_size=CHANGELOG_BATCH_ROWS):
                if self.meter is not None:
                    self.meter.take(batch.num_rows)
                yield batch

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        for batch in self._batches():
            yield from batch.to_pylist()

    @overload
    def __getitem__(self, index: int) -> Mapping[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Mapping[str, Any]]: ...

    def __getitem__(self, index: int | slice) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            indices = list(range(start, stop, step))
            wanted = set(indices)
            selected = {position: row for position, row in enumerate(self) if position in wanted}
            return [selected[position] for position in indices]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        offset = 0
        for batch in self._batches():
            if index < offset + batch.num_rows:
                return dict(batch.slice(index - offset, 1).to_pylist()[0])
            offset += batch.num_rows
        raise IndexError(index)


def _write_parquet(table: pa.Table, path: Path) -> None:
    row_group_size = max(
        1,
        min(
            table.num_rows or 1,
            int((128 * 1024 * 1024) / max(1, table.nbytes) * max(1, table.num_rows)),
        ),
    )
    pq.write_table(
        table,
        path,
        compression="zstd",
        write_page_index=True,
        use_content_defined_chunking=True,
        row_group_size=row_group_size,
    )


def _sorted_parquet_rows(paths: Sequence[Path]) -> Iterator[dict[str, Any]]:
    for path in paths:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=8192):
            yield from batch.to_pylist()


#: Rows per batch when typing and writing the changelog; bounded working memory.
CHANGELOG_BATCH_ROWS = 2048


def _typed_rows(rows: Iterable[dict[str, Any]], schema: pa.Schema) -> Iterator[dict[str, Any]]:
    # The old eager Arrow table normalized timestamps and null fields before
    # merging with retained history. Preserve that boundary in bounded batches.
    pending: list[dict[str, Any]] = []
    for row in rows:
        pending.append(row)
        if len(pending) == CHANGELOG_BATCH_ROWS:
            yield from pa.Table.from_pylist(pending, schema=schema).to_pylist()
            pending.clear()
    if pending:
        yield from pa.Table.from_pylist(pending, schema=schema).to_pylist()


def _sortable_key(row: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    # This is the ordering used by every published build. In particular,
    # record_key is itself JSON, so Arrow's typed string ordering is different.
    return sort_key(row, keys)


def _write_changelog(
    history: Sequence[Path],
    delta: Iterable[dict[str, Any]],
    path: Path,
    schema: pa.Schema,
    keys: tuple[str, ...],
    suppression: SuppressionPolicy | None = None,
) -> int:
    """Merge sorted changelog runs without materializing history in memory."""
    sources: list[Iterator[dict[str, Any]]] = []
    if history:
        sources.append(_sorted_parquet_rows(history))
    sources.append(_typed_rows(delta, schema))
    if suppression is not None:
        sources = [suppression.filter_changelog(source) for source in sources]
    heap: list[tuple[str, int, dict[str, Any], Iterator[dict[str, Any]]]] = []
    for source_number, source in enumerate(sources):
        if row := next(source, None):
            heap.append((_sortable_key(row, keys), source_number, row, source))
    heapq.heapify(heap)
    count = 0
    pending: list[dict[str, Any]] = []
    with pq.ParquetWriter(
        path,
        schema,
        compression="zstd",
        write_page_index=True,
        use_content_defined_chunking=True,
    ) as writer:
        while heap:
            _key, source_number, row, source = heapq.heappop(heap)
            pending.append(row)
            count += 1
            if len(pending) == CHANGELOG_BATCH_ROWS:
                writer.write_table(pa.Table.from_pylist(pending, schema=schema))
                pending.clear()
            if next_row := next(source, None):
                heapq.heappush(
                    heap, (_sortable_key(next_row, keys), source_number, next_row, source)
                )
        if pending:
            writer.write_table(pa.Table.from_pylist(pending, schema=schema))
    return count


@dataclass(frozen=True)
class CandidateFacts:
    """What a candidate manifest states about its final tables."""

    row_counts: Mapping[str, int]
    source_snapshot_counts: Mapping[str, int]
    calendar_horizon: str | None
    latest_event_covered: str | None


def _cached(
    candidates: Path, fingerprint: str, baseline_commit: str | None, meta: BuildMetadata
) -> BuildResult | None:
    """A retained, verified candidate built from the same inputs, if any."""
    for existing in candidates.iterdir():
        record = existing / "BUILT"
        if record.is_file() and not (existing / "REJECTED").exists():
            value = json.loads(record.read_text())
            if (
                value["build_fingerprint"] == fingerprint
                and value.get("baseline_commit") == baseline_commit
                and value.get("expected_parent") == meta.expected_parent
            ):
                from swingset.publish.safety import (
                    StaleCandidateError,
                    reject_candidate,
                    verify_candidate_files,
                )

                try:
                    verify_candidate_files(existing)
                except (OSError, ValueError, KeyError, StaleCandidateError) as error:
                    if not (existing / "PUBLISHED").exists():
                        reject_candidate(existing, f"cached candidate artifact is invalid: {error}")
                    continue
                return BuildResult(
                    existing.name,
                    existing,
                    value["content_hash"],
                    value["manifest_hash"],
                    bool(value["changed"]),
                    True,
                )
    return None


def _finish(
    temporary: Path,
    candidate_id: str,
    data: BuildInput,
    meta: BuildMetadata,
    *,
    hashes: dict[str, str],
    facts: CandidateFacts,
    card_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    card_renderer: Callable[[BuildInput], bytes] | None,
    fingerprint: str,
    baseline: Path | None,
    baseline_commit: str | None,
    baseline_content: str | None,
    build_time: datetime,
) -> BuildResult:
    """Write the card, license, verdict, manifest and receipt, then rename into place."""
    card = card_renderer(replace(data, tables=card_rows)) if card_renderer else meta.card
    (temporary / "README.md").write_bytes(card)
    hashes["README.md"] = sha256_file(temporary / "README.md")
    (temporary / "LICENSE").write_bytes(DATASET_LICENSE)
    hashes["LICENSE"] = sha256_file(temporary / "LICENSE")
    # The publication gate's verdict is named and hashed like every file, but
    # it describes the comparison, not the content (D-0225).
    from swingset.publish import gate

    hashes[gate.VERDICT] = gate.write(
        temporary,
        row_counts=facts.row_counts,
        baseline=baseline,
        baseline_commit=baseline_commit,
        overrides=meta.decrease_overrides,
        exempt="correction" if meta.correction_only else None,
    )
    semantic_files = {
        name: digest
        for name, digest in hashes.items()
        if not name.startswith("data/changelog/") and name != gate.VERDICT
    }
    semantic = {
        "files": semantic_files,
        "schema_version": meta.schema_version,
        "versions": meta.versions,
        "correction_token": data.release_policy.get("token") if data.release_policy else None,
    }
    content_hash = _hash_json(semantic)
    manifest = {
        "candidate_id": candidate_id,
        "built_at": build_time.isoformat(),
        "run_id": meta.run_id,
        "repository_commit": meta.repository_commit,
        "schema_version": meta.schema_version,
        "versions": meta.versions,
        "row_counts": dict(facts.row_counts),
        "source_snapshot_counts": dict(facts.source_snapshot_counts),
        "history_start": data.history_start.isoformat(),
        "calendar_horizon": facts.calendar_horizon,
        "latest_event_covered": facts.latest_event_covered,
        "content_hash": content_hash,
        "build_fingerprint": fingerprint,
        "expected_parent": meta.expected_parent,
        "baseline_commit": baseline_commit,
        "input_bundle_hash": data.input_bundle_hash,
        "release_policy": data.release_policy,
        "files": hashes,
    }
    manifest_path = temporary / "_meta" / "manifest.json"
    manifest_path.parent.mkdir(exist_ok=True)
    manifest_path.write_bytes(canonical_json(manifest))
    manifest_hash = sha256_file(manifest_path)
    built_record = {
        "candidate_id": candidate_id,
        "build_fingerprint": fingerprint,
        "baseline_commit": baseline_commit,
        "expected_parent": meta.expected_parent,
        "content_hash": content_hash,
        "manifest_hash": manifest_hash,
        "changed": content_hash != baseline_content,
        "input_paths": meta.input_paths,
    }
    durable_write(temporary / "BUILT", canonical_json(built_record))
    fsync_dir(temporary)
    final = temporary.parent / candidate_id
    os.replace(temporary, final)
    fsync_dir(temporary.parent)
    return BuildResult(
        candidate_id,
        final,
        content_hash,
        manifest_hash,
        content_hash != baseline_content,
        False,
    )


@dataclass(frozen=True)
class StreamedTables:
    """The final tables a streamed assembly wrote into a candidate directory."""

    #: Relative path to sha256 of every data file, changelog included.
    hashes: dict[str, str]
    #: Each table's files, for the card's lazy views.
    files: Mapping[str, Sequence[Path]]
    facts: CandidateFacts


def build_streamed_candidate(
    state_dir: Path,
    data: BuildInput,
    meta: BuildMetadata,
    *,
    write: Callable[[Path], StreamedTables],
    card_renderer: Callable[[BuildInput], bytes] | None = None,
    meter: Any = None,
) -> BuildResult:
    """A candidate whose tables ``write`` streams into its directory (plan phase 3, WP8).

    ``data`` carries the release policy and schemas; its tables are not read.
    The card reads each final table lazily, one batch at a time. Reuse,
    manifest, receipt and rename are `build_candidate`'s.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    baseline, baseline_commit, baseline_content = _baseline(state_dir)
    fingerprint = _fingerprint(data, meta)
    candidates = state_dir / "candidates"
    candidates.mkdir(exist_ok=True)
    cached = _cached(candidates, fingerprint, baseline_commit, meta)
    if cached is not None:
        return cached
    candidate_id = meta.candidate_id or f"cand_{uuid.uuid4().hex[:16]}"
    temporary = candidates / f".{candidate_id}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        written = write(temporary)
        card_rows: dict[str, Sequence[Mapping[str, Any]]] = {
            name: ParquetRows(written.files[name], meter) for name in PUBLISHED_TABLES
        }
        return _finish(
            temporary,
            candidate_id,
            data,
            meta,
            hashes=dict(written.hashes),
            facts=written.facts,
            card_rows=card_rows,
            card_renderer=card_renderer,
            fingerprint=fingerprint,
            baseline=baseline,
            baseline_commit=baseline_commit,
            baseline_content=baseline_content,
            build_time=meta.built_at or datetime.now(UTC),
        )
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def build_candidate(
    state_dir: Path,
    data: BuildInput,
    meta: BuildMetadata,
    *,
    suppressions: Sequence[Mapping[str, Any]] = (),
    card_renderer: Callable[[BuildInput], bytes] | None = None,
    rows_finalizer: Callable[[dict[str, list[dict[str, Any]]]], None] | None = None,
) -> BuildResult:
    state_dir.mkdir(parents=True, exist_ok=True)
    baseline, baseline_commit, baseline_content = _baseline(state_dir)
    fingerprint = _fingerprint(data, meta)
    candidates = state_dir / "candidates"
    candidates.mkdir(exist_ok=True)
    cached = _cached(candidates, fingerprint, baseline_commit, meta)
    if cached is not None:
        return cached
    candidate_id = meta.candidate_id or f"cand_{uuid.uuid4().hex[:16]}"
    temporary = candidates / f".{candidate_id}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        rows = {name: [dict(row) for row in data.tables.get(name, ())] for name in PUBLISHED_TABLES}
        suppression = apply_suppressions(rows, suppressions)
        history = sorted((baseline / "data" / "changelog").glob("*.parquet")) if baseline else []
        build_time = meta.built_at or datetime.now(UTC)
        with generated_delta(temporary / "delta.sqlite", data.primary_keys["changelog"]) as delta:
            for _pass in range(32):
                discovered = suppression.discover_history(_sorted_parquet_rows(history))
                suppression.apply(rows)
                if rows_finalizer is not None:
                    rows_finalizer(rows)
                with closing(
                    changes(
                        rows,
                        baseline,
                        data.primary_keys,
                        changed_at=build_time,
                        run_id=meta.run_id,
                        scratch=temporary / "comparison.sqlite",
                    )
                ) as comparison:
                    delta.replace(comparison)
                discovered |= suppression.discover_history(delta)
                if not discovered:
                    break
            else:
                raise BuildError("suppression history did not converge within 32 passes")
            _validate(rows, data.history_start)
            hashes: dict[str, str] = {}
            row_counts = {name: len(value) for name, value in rows.items()}
            for table_name in PUBLISHED_TABLES:
                schema = data.schemas[table_name]
                if table_name == "changelog":
                    table_dir = temporary / "data" / table_name
                    table_dir.mkdir(parents=True)
                    path = table_dir / "changelog.parquet"
                    row_counts[table_name] = _write_changelog(
                        history,
                        delta.sorted_rows(),
                        path,
                        schema,
                        data.primary_keys[table_name],
                        suppression,
                    )
                    hashes[path.relative_to(temporary).as_posix()] = sha256_file(path)
                    continue
                ordered = sorted(
                    rows[table_name],
                    key=lambda row: json.dumps(
                        tuple(row.get(k) for k in data.primary_keys[table_name]), default=str
                    ),
                )
                table = pa.Table.from_pylist(ordered, schema=schema)
                if not table.schema.equals(schema, check_metadata=True):
                    raise BuildError(f"schema mismatch for {table_name}")
                table_dir = temporary / "data" / table_name
                table_dir.mkdir(parents=True)
                if table_name in {"callback_marks", "final_marks"} and ordered:
                    contest_events = {
                        row["contest_id"]: row.get("event_id") for row in rows["contests"]
                    }
                    event_years = {row["event_id"]: row.get("year") for row in rows["events"]}
                    round_years = {
                        row["round_id"]: event_years.get(contest_events.get(row.get("contest_id")))
                        for row in rows["rounds"]
                    }
                    years = sorted(
                        {round_years.get(row.get("round_id")) for row in ordered}, key=str
                    )
                    for year in years:
                        partition = [
                            row for row in ordered if round_years.get(row.get("round_id")) == year
                        ]
                        partition_table = pa.Table.from_pylist(partition, schema=schema)
                        path = table_dir / f"year={year if year is not None else 'unknown'}.parquet"
                        _write_parquet(partition_table, path)
                        hashes[path.relative_to(temporary).as_posix()] = sha256_file(path)
                else:
                    path = table_dir / f"{table_name}.parquet"
                    _write_parquet(table, path)
                    hashes[path.relative_to(temporary).as_posix()] = sha256_file(path)
        # Counts and coverage must describe the final rows, including the
        # generated changelog and applied suppressions.
        card_rows: dict[str, Sequence[Mapping[str, Any]]] = dict(rows)
        # Card renderers consume row counts and coverage, not the change payloads.
        # Expose real history through a lazy sequence rather than Python copies.
        history_path = temporary / "data" / "changelog" / "changelog.parquet"
        card_rows["changelog"] = ParquetRows(history_path)
        placed_events = {placement["event_id"] for placement in rows["placements"]}
        return _finish(
            temporary,
            candidate_id,
            data,
            meta,
            hashes=hashes,
            facts=CandidateFacts(
                row_counts=row_counts,
                source_snapshot_counts={
                    source: sum(1 for row in rows["snapshots"] if row.get("source") == source)
                    for source in sorted(
                        {str(row["source"]) for row in rows["snapshots"] if row.get("source")}
                    )
                },
                calendar_horizon=max(
                    (str(row["end_date"]) for row in rows["events"] if row.get("end_date")),
                    default=None,
                ),
                latest_event_covered=max(
                    (
                        str(row["end_date"])
                        for row in rows["events"]
                        if row.get("end_date") and row["event_id"] in placed_events
                    ),
                    default=None,
                ),
            ),
            card_rows=card_rows,
            card_renderer=card_renderer,
            fingerprint=fingerprint,
            baseline=baseline,
            baseline_commit=baseline_commit,
            baseline_content=baseline_content,
            build_time=build_time,
        )
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def assert_settled(connection: sqlite3.Connection) -> None:
    pending = connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()
    if pending is not None and int(pending[0]) != 0:
        raise BuildError("build is blocked by pending parse, project, or link work")
