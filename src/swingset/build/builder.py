from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from swingset.build.files import canonical_json, durable_write, fsync_dir, sha256_file
from swingset.model import enums

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


def _validate(rows: Mapping[str, list[dict[str, Any]]]) -> None:
    for (table, field), enum_type in ENUM_FIELDS.items():
        allowed_values = {member.value for member in enum_type}
        for row in rows.get(table, []):
            value = row.get(field)
            if value is not None and value not in allowed_values:
                raise BuildError(f"unknown {table}.{field} enum value: {value!r}")
    entry_ids = {row["entry_id"] for row in rows.get("entries", [])}
    for table in ("callback_marks", "callbacks", "heats"):
        for row in rows.get(table, []):
            if row.get("entry_id") not in entry_ids:
                raise BuildError(f"{table} references missing entry {row.get('entry_id')}")
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
    prior_history: list[dict[str, Any]] = []
    if baseline is None:
        old: dict[str, list[dict[str, Any]]] = {}
    else:
        old = {}
        for table in current:
            files = list((baseline / "data" / table).glob("*.parquet"))
            old[table] = pq.read_table(files).to_pylist() if files else []
        history = list((baseline / "data" / "changelog").glob("*.parquet"))
        if history:
            prior_history = pq.read_table(history).to_pylist()
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
    return prior_history + delta


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
        _validate(rows)
        build_time = meta.built_at or datetime.now(UTC)
        rows["changelog"] = _changelog(
            rows, baseline, data.primary_keys, changed_at=build_time, run_id=meta.run_id
        )
        hashes: dict[str, str] = {}
        for table_name in PUBLISHED_TABLES:
            schema = data.schemas[table_name]
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
        card = card_renderer(replace(data, tables=rows)) if card_renderer else meta.card
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
            "row_counts": {name: len(value) for name, value in rows.items()},
            "source_snapshot_counts": {
                source: sum(1 for row in rows["snapshots"] if row.get("source") == source)
                for source in sorted(
                    {str(row["source"]) for row in rows["snapshots"] if row.get("source")}
                )
            },
            "latest_event_covered": max(
                (str(row["end_date"]) for row in rows["events"] if row.get("end_date")),
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
