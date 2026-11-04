"""Recover historical Parquet only when retained bytes prove an exact match.

This reads immutable generation labels and retained sidecars. It never selects
current pointers, consults live admission gates, or changes a database row. An
old sidecar can lack enough information to reproduce a shared-row selection or
baseline stand-in; that artifact remains unavailable rather than being replaced
with today's output.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa

from swingset.build import artifact_store, manifest, partitions
from swingset.build.closure_rows import _NEUTRAL
from swingset.build.schema import SCHEMAS
from swingset.build.support_closure import _EventScopes
from swingset.state import output_packs
from swingset.state.db import open_database
from swingset.state.engine import Connection
from swingset.state.retention import artifact_directory


class ArtifactUnavailable(ValueError):
    """Retained history cannot prove the original artifact's exact bytes."""


def _rows(conn: Connection, sidecar: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    identifiers = sidecar.get("generations")
    if (
        not isinstance(identifiers, list)
        or not identifiers
        or not all(isinstance(item, str) for item in identifiers)
    ):
        raise ArtifactUnavailable("historical artifact has no retained generation recipe")
    generations = []
    for identifier in identifiers:
        found = conn.execute(
            "SELECT stage,unit_kind,unit_id,generation_id,output_digest,row_count,created_at,run_id FROM derivation_generations WHERE generation_id=%s",
            (identifier,),
        ).fetchone()
        if found is None:
            raise ArtifactUnavailable("historical artifact generation is unavailable")
        generations.append(partitions.Generation(*found))
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for generation in sorted(generations, key=lambda item: item.order):
        for table, key, raw in output_packs.rows(
            conn,
            generation.generation_id,
            expected=(generation.output_digest, generation.row_count),
        ):
            payload = json.loads(raw)
            payload.setdefault("run_id", generation.run_id)
            for name in ("first_seen_at", "last_seen_at", "asserted_at"):
                payload.setdefault(name, generation.created_at)
            patch = (
                generation.stage == "link" and table in {"entries", "judges", "placements"}
            ) or (
                generation.stage == "project"
                and generation.unit_kind in {"history", "year"}
                and table in {"events", "registry_placements"}
            )
            if patch and (table, key) not in rows:
                continue
            previous = rows.setdefault((table, key), dict(_NEUTRAL.get(table, {})))
            previous.update(payload)
    return rows


def _selected(
    partition: partitions.Partition,
    rows: dict[tuple[str, str], dict[str, Any]],
    sidecar: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Apply only the recorded partition boundary and recorded subject omissions."""
    suffix = partition.name.split("/", 1)[1]
    if partition.kind == "year":
        return {
            key: row
            for key, row in rows.items()
            if key[0] == "coverage" and str(row.get("year")) == suffix
        }
    if partition.kind == "dancers":
        omitted = {item[0] for item in sidecar.get("withheld_subjects", {}).get("dancers", [])}
        return {
            key: row
            for key, row in rows.items()
            if key[0] in {"dancers", "registry_placements"}
            and isinstance(row.get("wsdc_id"), int)
            and row["wsdc_id"] // partitions.DANCER_BATCH == int(suffix)
            and row["wsdc_id"] not in omitted
        }
    tables: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for (table, _key), row in rows.items():
        tables[table].append(row)
    scopes = _EventScopes(tables)
    events = set(suffix.split("+"))
    return {key: row for key, row in rows.items() if scopes.events(row) & events}


def _recover(state: Path, conn: Connection, partition_name: str, digest: str) -> bool:
    if artifact_store.intact(state, digest):
        return False
    directory = artifact_directory(state, digest)
    try:
        body = (directory / "sidecar.json").read_bytes()
        if hashlib.sha256(body).hexdigest() != digest:
            raise ArtifactUnavailable("historical artifact sidecar digest differs")
        sidecar = json.loads(body)
        kind = partition_name.split("/", 1)[0]
        if (
            sidecar.get("format") != partitions.FORMAT
            or sidecar.get("partition") != partition_name
            or kind not in {"year", "dancers", "event"}
        ):
            raise ArtifactUnavailable("historical artifact sidecar recipe differs")
        partition = partitions.Partition(partition_name, kind)
        rows = _selected(partition, _rows(conn, sidecar), sidecar)
        public = partitions.public_tables(partition, rows)
        arrow = {
            name: pa.Table.from_pylist(
                values, schema=SCHEMAS.get(name) or partitions.INTERNAL_SCHEMAS[name]
            )
            for name, values in public.items()
        }
        scratch = state / "tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="historical-artifact-", dir=scratch) as temporary:
            staging = Path(temporary)
            recovered, _size = artifact_store.write(
                staging, arrow, {key: value for key, value in sidecar.items() if key != "tables"}
            )
            if recovered != digest:
                raise ArtifactUnavailable(
                    "historical artifact cannot be reconstructed from its retained recipe"
                )
            source = artifact_directory(staging, recovered)
            # All file hashes and the final sidecar digest matched before any
            # file becomes visible in the restored artifact directory.
            for file in source.iterdir():
                if file.name != "sidecar.json":
                    os.replace(file, directory / file.name)
        if not artifact_store.intact(state, digest):
            raise ArtifactUnavailable("historical artifact changed during reconstruction")
        return True
    except (OSError, KeyError, TypeError, IndexError, output_packs.GenerationUnavailable) as error:
        raise ArtifactUnavailable(
            f"historical artifact recipe is unavailable: {partition_name}"
        ) from error


def hydrate(
    state: Path, manifest_digest: str, *, connection: Connection | None = None
) -> dict[str, int]:
    """Recover a pinned manifest's exact historical artifacts, or refuse it."""
    if connection is None:
        with open_database(state, read_only=True, lock=False) as database:
            return hydrate(state, manifest_digest, connection=database.connection)
    value = manifest.read(state, manifest_digest)
    recovered = 0
    with connection.read_snapshot():
        for partition_name, digest in value["built"]:
            recovered += _recover(state, connection, str(partition_name), str(digest))
    return {"recovered": recovered, "artifacts": len(value["built"])}
