from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, overload

import pyarrow as pa
import pyarrow.parquet as pq

from swingset.build.files import canonical_json, durable_write, fsync_dir, sha256_file
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
        }
    )


def _validate(rows: Mapping[str, list[dict[str, Any]]], history_start: date) -> None:
    for (table, field), enum_type in ENUM_FIELDS.items():
        allowed_values = {member.value for member in enum_type}
        for row in rows.get(table, []):
            value = row.get(field)
            if value is not None and value not in allowed_values:
                raise BuildError(f"unknown {table}.{field} enum value: {value!r}")
    for row in rows.get("events", []):
        start, end = row.get("start_date"), row.get("end_date")
        if start is not None and end is not None and start > end:
            raise BuildError(f"event {row.get('event_id')} starts after it ends")
        if not in_history(history_start, end_date=end, start_date=start, year=row.get("year")):
            raise BuildError(
                f"event {row.get('event_id')} ended before the history start {history_start}"
            )
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
    callback_marks: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in rows.get("callback_marks", []):
        if row.get("judge_id") not in judge_ids:
            raise BuildError(f"callback_marks references missing judge {row.get('judge_id')}")
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
    allowed = {"confirmed", "probable"}
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


def apply_suppressions(
    rows: Mapping[str, list[dict[str, Any]]], suppressions: Sequence[Mapping[str, Any]]
) -> None:
    ids = {int(item["wsdc_id"]) for item in suppressions if item.get("wsdc_id") not in (None, "")}
    names = {str(item["name_norm"]).casefold() for item in suppressions if item.get("name_norm")}
    suppressed_subjects: set[str] = set()
    for table in ("entries", "judges", "dancers"):
        for row in rows.get(table, []):
            row_names = {str(row.get(key, "")).casefold() for key in ("name_norm", "name_raw")}
            if row.get("wsdc_id") in ids or bool(row_names & names):
                subject = row.get("entry_id") or row.get("judge_id")
                if subject:
                    suppressed_subjects.add(str(subject))
                for key in (
                    "name_raw",
                    "name_norm",
                    "first_name",
                    "last_name",
                    "wsdc_id",
                    "city_raw",
                    "country_raw",
                ):
                    if key in row:
                        row[key] = None
                if "link_status" in row:
                    row["link_status"] = "suppressed"
    rows.get("link_candidates", [])[:] = [
        row
        for row in rows.get("link_candidates", [])
        if str(row.get("subject_id")) not in suppressed_subjects and row.get("wsdc_id") not in ids
    ]
    rows.get("identity_links", [])[:] = [
        row
        for row in rows.get("identity_links", [])
        if str(row.get("subject_id")) not in suppressed_subjects
    ]
    for table_rows in rows.values():
        for row in table_rows:
            for key, value in tuple(row.items()):
                if "wsdc_id" in key and value in ids:
                    row[key] = None
                if "name" in key and isinstance(value, str) and value.casefold() in names:
                    row[key] = None


def _changelog(
    current: Mapping[str, list[dict[str, Any]]],
    baseline: Path | None,
    keys: Mapping[str, tuple[str, ...]],
    *,
    changed_at: datetime,
    run_id: str,
) -> list[dict[str, Any]]:
    if baseline is None:
        old: dict[str, list[dict[str, Any]]] = {}
    else:
        old = {}
        for table in current:
            if table == "changelog":
                continue
            files = list((baseline / "data" / table).glob("*.parquet"))
            old[table] = pq.read_table(files).to_pylist() if files else []
    delta: list[dict[str, Any]] = []
    for table, new_rows in current.items():
        if table == "changelog" or table not in keys:
            continue
        key_fields = keys[table]
        before = {_row_key(row, key_fields): row for row in old.get(table, [])}
        after = {_row_key(row, key_fields): row for row in new_rows}
        for row_key in sorted(before.keys() | after.keys(), key=repr):
            old_row, new_row = before.get(row_key), after.get(row_key)
            fields: Sequence[str | None]
            if old_row is None or new_row is None:
                fields = [None]
            else:
                fields = sorted(
                    field
                    for field in old_row.keys() | new_row.keys()
                    if old_row.get(field) != new_row.get(field)
                )
            for field in fields:
                old_value = old_row if field is None else old_row.get(field) if old_row else None
                new_value = new_row if field is None else new_row.get(field) if new_row else None
                reason = (
                    "suppression"
                    if (new_row and new_row.get("link_status") == "suppressed")
                    else "new_source_data"
                )
                delta.append(
                    {
                        "changed_at": changed_at,
                        "run_id": run_id,
                        "table": table,
                        "record_key": json.dumps(row_key, default=str),
                        "field": field,
                        "old_value": json.dumps(old_value, sort_keys=True, default=str),
                        "new_value": json.dumps(new_value, sort_keys=True, default=str),
                        "change_type": "added"
                        if old_row is None
                        else "removed"
                        if new_row is None
                        else "updated",
                        "reason": reason,
                    }
                )
    return delta


class ParquetRows(Sequence[Mapping[str, Any]]):
    """Lazy public rows for consumers that usually need only their count."""

    def __init__(self, path: Path) -> None:
        self.file = pq.ParquetFile(path)

    def __len__(self) -> int:
        return int(self.file.metadata.num_rows)

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        for batch in self.file.iter_batches(batch_size=8192):
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
        for batch in self.file.iter_batches(batch_size=8192):
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


def _table_rows(table: pa.Table) -> Iterator[dict[str, Any]]:
    for batch in table.to_batches(max_chunksize=8192):
        yield from batch.to_pylist()


def _sortable_key(row: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    # This is the ordering used by every published build. In particular,
    # record_key is itself JSON, so Arrow's typed string ordering is different.
    return json.dumps(tuple(row.get(key) for key in keys), default=str)


def _write_changelog(
    history: Sequence[Path], delta: pa.Table, path: Path, schema: pa.Schema, keys: tuple[str, ...]
) -> int:
    """Merge sorted changelog runs without materializing history in memory."""
    sources: list[Iterator[dict[str, Any]]] = []
    if history:
        sources.append(_sorted_parquet_rows(history))
    sources.append(_table_rows(delta))
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
            if len(pending) == 8192:
                writer.write_table(pa.Table.from_pylist(pending, schema=schema))
                pending.clear()
            if next_row := next(source, None):
                heapq.heappush(
                    heap, (_sortable_key(next_row, keys), source_number, next_row, source)
                )
        if pending:
            writer.write_table(pa.Table.from_pylist(pending, schema=schema))
    return count


def build_candidate(
    state_dir: Path,
    data: BuildInput,
    meta: BuildMetadata,
    *,
    suppressions: Sequence[Mapping[str, Any]] = (),
    card_renderer: Callable[[BuildInput], bytes] | None = None,
) -> BuildResult:
    state_dir.mkdir(parents=True, exist_ok=True)
    baseline, baseline_commit, baseline_content = _baseline(state_dir)
    fingerprint = _fingerprint(data, meta)
    candidates = state_dir / "candidates"
    candidates.mkdir(exist_ok=True)
    for existing in candidates.iterdir():
        record = existing / "BUILT"
        if record.is_file():
            value = json.loads(record.read_text())
            if (
                value["build_fingerprint"] == fingerprint
                and value.get("baseline_commit") == baseline_commit
                and value.get("expected_parent") == meta.expected_parent
            ):
                return BuildResult(
                    existing.name,
                    existing,
                    value["content_hash"],
                    value["manifest_hash"],
                    bool(value["changed"]),
                    True,
                )
    candidate_id = meta.candidate_id or f"cand_{uuid.uuid4().hex[:16]}"
    final = candidates / candidate_id
    temporary = candidates / f".{candidate_id}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        rows = {name: [dict(row) for row in data.tables.get(name, ())] for name in PUBLISHED_TABLES}
        apply_suppressions(rows, suppressions)
        _validate(rows, data.history_start)
        build_time = meta.built_at or datetime.now(UTC)
        rows["changelog"] = _changelog(
            rows, baseline, data.primary_keys, changed_at=build_time, run_id=meta.run_id
        )
        hashes: dict[str, str] = {}
        row_counts = {name: len(value) for name, value in rows.items()}
        for table_name in PUBLISHED_TABLES:
            schema = data.schemas[table_name]
            ordered = sorted(
                rows[table_name],
                key=lambda row: json.dumps(
                    tuple(row.get(k) for k in data.primary_keys[table_name]), default=str
                ),
            )
            table = pa.Table.from_pylist(ordered, schema=schema)
            history: list[Path] = []
            if table_name == "changelog":
                history = (
                    sorted((baseline / "data" / "changelog").glob("*.parquet")) if baseline else []
                )
            if not table.schema.equals(schema, check_metadata=True):
                raise BuildError(f"schema mismatch for {table_name}")
            table_dir = temporary / "data" / table_name
            table_dir.mkdir(parents=True)
            if table_name == "changelog":
                path = table_dir / "changelog.parquet"
                row_counts[table_name] = _write_changelog(
                    history,
                    table,
                    path,
                    schema,
                    data.primary_keys[table_name],
                )
                hashes[path.relative_to(temporary).as_posix()] = sha256_file(path)
            elif table_name in {"callback_marks", "final_marks"} and ordered:
                contest_events = {
                    row["contest_id"]: row.get("event_id") for row in rows["contests"]
                }
                event_years = {row["event_id"]: row.get("year") for row in rows["events"]}
                round_years = {
                    row["round_id"]: event_years.get(contest_events.get(row.get("contest_id")))
                    for row in rows["rounds"]
                }
                years = sorted({round_years.get(row.get("round_id")) for row in ordered}, key=str)
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
        card = card_renderer(replace(data, tables=card_rows)) if card_renderer else meta.card
        (temporary / "README.md").write_bytes(card)
        hashes["README.md"] = sha256_file(temporary / "README.md")
        (temporary / "LICENSE").write_bytes(DATASET_LICENSE)
        hashes["LICENSE"] = sha256_file(temporary / "LICENSE")
        semantic_files = {
            name: digest
            for name, digest in hashes.items()
            if not name.startswith("data/changelog/")
        }
        semantic = {
            "files": semantic_files,
            "schema_version": meta.schema_version,
            "versions": meta.versions,
        }
        content_hash = _hash_json(semantic)
        built_at = build_time.isoformat()
        manifest = {
            "candidate_id": candidate_id,
            "built_at": built_at,
            "run_id": meta.run_id,
            "repository_commit": meta.repository_commit,
            "schema_version": meta.schema_version,
            "versions": meta.versions,
            "row_counts": row_counts,
            "source_snapshot_counts": {
                source: sum(1 for row in rows["snapshots"] if row.get("source") == source)
                for source in sorted(
                    {str(row["source"]) for row in rows["snapshots"] if row.get("source")}
                )
            },
            "history_start": data.history_start.isoformat(),
            "calendar_horizon": max(
                (str(row["end_date"]) for row in rows["events"] if row.get("end_date")),
                default=None,
            ),
            "latest_event_covered": max(
                (
                    str(row["end_date"])
                    for row in rows["events"]
                    if row.get("end_date")
                    and row["event_id"]
                    in {placement["event_id"] for placement in rows["placements"]}
                ),
                default=None,
            ),
            "content_hash": content_hash,
            "build_fingerprint": fingerprint,
            "expected_parent": meta.expected_parent,
            "baseline_commit": baseline_commit,
            "input_bundle_hash": data.input_bundle_hash,
            "files": hashes,
        }
        manifest_path = temporary / "_meta" / "manifest.json"
        manifest_path.parent.mkdir()
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
        }
        durable_write(temporary / "BUILT", canonical_json(built_record))
        fsync_dir(temporary)
        os.replace(temporary, final)
        fsync_dir(candidates)
        return BuildResult(
            candidate_id,
            final,
            content_hash,
            manifest_hash,
            content_hash != baseline_content,
            False,
        )
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def assert_settled(connection: sqlite3.Connection) -> None:
    pending = connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()
    if pending is not None and int(pending[0]) != 0:
        raise BuildError("build is blocked by pending parse, project, or link work")
