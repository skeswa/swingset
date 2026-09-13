"""Reconstruct a pinned release in a disposable, disk-backed SQLite spool."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from swingset.model.schema import TABLES

from .closure import ReleaseClosure, validate
from .closure_manifest import ClosureError, canonical
from .closure_support import interpretation_lookup

_PROCESSING = frozenset({"run_id", "first_seen_at", "last_seen_at", "asserted_at"})
_NEUTRAL: dict[str, dict[str, Any]] = {
    "entries": {"wsdc_id": None, "link_status": "unmatched", "link_confidence": 0.0},
    "judges": {"wsdc_id": None},
    "placements": {
        "leader_wsdc_id": None,
        "follower_wsdc_id": None,
        "registry_points_leader": None,
        "registry_points_follower": None,
        "registry_confirmed": 0,
        "points_matches_expected": None,
    },
    "registry_placements": {"event_id": None},
    "events": {"coverage_tier": "index_only"},
}


def _normalize(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    return value


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, (list, dict)):
        try:
            left = json.loads(left)
        except ValueError:
            return False
    return bool(left == _normalize(right))


class ReconstructedRows:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.omissions: list[dict[str, Any]] = []
        self.counts: Counter[str] = Counter()

    def iter_table(self, name: str) -> Iterator[dict[str, Any]]:
        for (payload,) in self.connection.execute(
            "SELECT payload FROM rows WHERE table_name=? ORDER BY record_key", (name,)
        ):
            yield json.loads(payload)

    def table_names(self) -> tuple[str, ...]:
        return tuple(
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT table_name FROM rows ORDER BY table_name"
            )
        )


def _baseline(conn: sqlite3.Connection, path: Path | None) -> None:
    if path is None:
        return
    import pyarrow.parquet as pq

    from .files import sha256_file
    from .schema import PRIMARY_KEYS

    manifest = json.loads((path / "_meta/manifest.json").read_bytes())
    for table, keys in PRIMARY_KEYS.items():
        if table in {"changelog", "review_queue", "snapshots", "coverage"}:
            continue
        for file in sorted((path / "data" / table).rglob("*.parquet")):
            expected = manifest.get("files", {}).get(file.relative_to(path).as_posix())
            if expected is None or sha256_file(file) != expected:
                raise ClosureError("baseline_owned_values_artifact_changed")
            for batch in pq.ParquetFile(file).iter_batches(batch_size=4096):
                conn.executemany(
                    "INSERT OR REPLACE INTO baseline VALUES (?,?,?)",
                    (
                        (
                            table,
                            canonical([_normalize(row[key]) for key in keys]),
                            canonical(_normalize(row)),
                        )
                        for row in batch.to_pylist()
                    ),
                )


def _legacy_matches(
    conn: sqlite3.Connection, table: str, key: str, payload: dict[str, Any]
) -> bool:
    import pyarrow as pa

    from .schema import SCHEMAS

    old = conn.execute(
        "SELECT payload FROM baseline WHERE table_name=? AND record_key=?", (table, key)
    ).fetchone()
    if old is None:
        return False
    baseline = json.loads(old[0])
    ignored = (
        _PROCESSING
        | set(TABLES[table].owner_columns if table in TABLES else ())
        | {"scope_status", "evidence_observed_at"}
    )
    if table == "events":
        ignored |= {"coverage_tier"}

    # Public numeric values use the declared Arrow representation. Coerce through
    # that exact type, rather than introducing a tolerance around source scores.
    def matches(name: str, value: Any) -> bool:
        if name not in baseline:
            return False
        schema = SCHEMAS.get(table)
        if value is not None and schema is not None and name in schema.names:
            field = schema.field(name)
            if pa.types.is_floating(field.type):
                value = pa.scalar(value, type=field.type).as_py()
        return _equivalent(value, baseline[name])

    return all(matches(name, value) for name, value in payload.items() if name not in ignored)


def _put(
    conn: sqlite3.Connection, table: str, key: str, payload: dict[str, Any], *, patch: bool = False
) -> bool:
    previous = conn.execute(
        "SELECT payload FROM rows WHERE table_name=? AND record_key=?", (table, key)
    ).fetchone()
    if patch and previous is None:
        return False
    merged = json.loads(previous[0]) if previous else dict(_NEUTRAL.get(table, {}))
    merged.update(payload)
    conn.execute(
        "INSERT INTO rows VALUES (?,?,?) ON CONFLICT(table_name,record_key) DO UPDATE SET payload=excluded.payload",
        (table, key, canonical(merged)),
    )
    return True


def _event(conn: sqlite3.Connection, table: str, key: str, row: Mapping[str, Any]) -> str | None:
    if row.get("event_id"):
        return str(row["event_id"])
    for field, target in (
        ("contest_id", "contests"),
        ("round_id", "rounds"),
        ("entry_id", "entries"),
        ("placement_id", "placements"),
    ):
        if target == table or not row.get(field):
            continue
        parent = conn.execute(
            "SELECT payload FROM rows WHERE table_name=? AND record_key=?",
            (target, canonical([row[field]])),
        ).fetchone()
        if parent:
            value = _event(conn, target, canonical([row[field]]), json.loads(parent[0]))
            if value:
                return value
    return str(json.loads(key)[0]) if table == "events" else None


def _close(conn: sqlite3.Connection, result: ReconstructedRows) -> None:
    omitted_events = set()
    omitted_dancers = set()
    for table, key, payload, reason in conn.execute("SELECT * FROM rejected"):
        row = json.loads(payload)
        event = _event(conn, table, key, row)
        if event:
            omitted_events.add(event)
        if table in {"dancers", "registry_placements"} and row.get("wsdc_id") is not None:
            omitted_dancers.add(row["wsdc_id"])
        result.counts[reason] += 1
    conn.execute("CREATE TABLE omitted_events(event_id TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO omitted_events VALUES (?)", ((value,) for value in sorted(omitted_events))
    )
    # Resolve descendants while all structural parent rows still exist.
    conn.execute(
        "CREATE TABLE to_remove(table_name TEXT,record_key TEXT,PRIMARY KEY(table_name,record_key))"
    )
    for table, key, payload in conn.execute("SELECT table_name,record_key,payload FROM rows"):
        row = json.loads(payload)
        if table in {"dancers", "registry_placements"} and row.get("wsdc_id") in omitted_dancers:
            conn.execute("INSERT OR IGNORE INTO to_remove VALUES (?,?)", (table, key))
        elif table != "registry_placements" and _event(conn, table, key, row) in omitted_events:
            conn.execute("INSERT OR IGNORE INTO to_remove VALUES (?,?)", (table, key))
    conn.execute(
        "DELETE FROM rows WHERE (table_name,record_key) IN (SELECT table_name,record_key FROM to_remove)"
    )
    for event in sorted(omitted_events):
        result.omissions.append(
            {
                "stage": "project",
                "unit_kind": "event",
                "unit_id": event,
                "status": "withheld",
                "reason": "source_support_unavailable",
            }
        )
    # Null associations and identity claims whose structural/registry support was omitted.
    for table, key, payload in conn.execute(
        "SELECT table_name,record_key,payload FROM rows WHERE table_name IN ('registry_placements','entries','judges','placements','identity_links','link_candidates','identity_link_resolutions')"
    ):
        row = json.loads(payload)
        if table == "registry_placements" and row.get("event_id") in omitted_events:
            row["event_id"] = None
        if table in {"entries", "judges"} and row.get("wsdc_id") in omitted_dancers:
            row.update(_NEUTRAL[table])
        if table in {"identity_links", "link_candidates", "identity_link_resolutions"}:
            subject_table = "entries" if row.get("subject_kind") == "entry" else "judges"
            exists = conn.execute(
                "SELECT 1 FROM rows WHERE table_name=? AND record_key=?",
                (subject_table, canonical([row.get("subject_id")])),
            ).fetchone()
            if not exists or row.get("wsdc_id") in omitted_dancers:
                conn.execute("DELETE FROM rows WHERE table_name=? AND record_key=?", (table, key))
                continue
        if table == "placements":
            for role in ("leader", "follower"):
                entry = conn.execute(
                    "SELECT payload FROM rows WHERE table_name='entries' AND record_key=?",
                    (canonical([row.get(role + "_entry_id")]),),
                ).fetchone()
                if not entry or json.loads(entry[0]).get("wsdc_id") is None:
                    row[role + "_wsdc_id"] = None
                    row["registry_points_" + role] = None
                    row["registry_confirmed"] = 0
                    row["points_matches_expected"] = None
        conn.execute(
            "UPDATE rows SET payload=? WHERE table_name=? AND record_key=?",
            (canonical(row), table, key),
        )


@contextmanager
def reconstruct(
    conn: sqlite3.Connection,
    closure: ReleaseClosure,
    *,
    directory: Path,
    baseline: Path | None = None,
) -> Iterator[ReconstructedRows]:
    """Materialize only selected immutable rows; no reads from live canonical facts."""
    validate(conn, closure)
    if closure.baseline is not None:
        from .closure import _baseline as baseline_identity

        if baseline is None or baseline_identity(baseline) != closure.baseline:
            raise ClosureError("baseline_compatibility_artifact_changed")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="closure-", dir=directory) as temporary:
        spool = sqlite3.connect(Path(temporary) / "rows.sqlite")
        try:
            spool.executescript(
                "CREATE TABLE rows(table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(table_name,record_key)); CREATE TABLE baseline(table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(table_name,record_key)); CREATE TABLE rejected(table_name TEXT,record_key TEXT,payload TEXT,reason TEXT);"
            )
            result = ReconstructedRows(spool)
            _baseline(spool, baseline)
            supported = interpretation_lookup(closure.manifest())
            # Prior published facts may have provenance outside the selected raw
            # observation graph. Explicit revocation still forbids that fallback.
            revoked = {
                (str(recipe["context"]["snapshot_id"]), str(recipe["parser_version"]))
                for (raw,) in conn.execute(
                    "SELECT recipe_json FROM source_generations WHERE state='revoked'"
                )
                for recipe in (json.loads(raw),)
            }
            rank = {
                "calendar": 0,
                "source_index": 0,
                "dancer": 0,
                "inventory": 1,
                "map": 2,
                "event": 3,
                "source_event": 4,
                "history": 5,
            }
            selected = sorted(
                closure.selected,
                key=lambda item: (
                    6 if item["stage"] == "link" else rank.get(item["unit_kind"], 3),
                    item["created_at"],
                    item["unit_id"],
                ),
            )
            for generation in selected:
                output = hashlib.sha256()
                count = 0
                for _, table, key, raw in conn.execute(
                    "SELECT ordinal,table_name,record_key,payload_json FROM derivation_rows WHERE generation_id=? ORDER BY ordinal",
                    (generation["generation_id"],),
                ):
                    count += 1
                    output.update(canonical((table, key, raw)).encode() + b"\n")
                    payload = json.loads(raw)
                    snapshot = payload.get("snapshot_id")
                    if snapshot and snapshot != "override":
                        state = supported.get(
                            (str(snapshot), str(payload.get("parser_version", ""))),
                            {"state": "legacy_unassessed"},
                        )["state"]
                        if (str(snapshot), str(payload.get("parser_version", ""))) in revoked:
                            state = "revoked"
                        if state != "accepted":
                            if state == "revoked" or not _legacy_matches(
                                spool, table, key, payload
                            ):
                                spool.execute(
                                    "INSERT INTO rejected VALUES (?,?,?,?)",
                                    (
                                        table,
                                        key,
                                        raw,
                                        "source_revoked"
                                        if state == "revoked"
                                        else "legacy_owned_values_not_published",
                                    ),
                                )
                                continue
                            result.counts["legacy_unassessed_retained"] += 1
                    # Processing metadata comes from this immutable materialization receipt.
                    payload.setdefault("run_id", generation["run_id"])
                    for field in ("first_seen_at", "last_seen_at", "asserted_at"):
                        payload.setdefault(field, generation["created_at"])
                    patch = (
                        generation["stage"] == "link"
                        and table in {"entries", "judges", "placements"}
                    ) or (
                        generation["stage"] == "project"
                        and generation["unit_kind"] == "history"
                        and table in {"events", "registry_placements"}
                    )
                    if not _put(spool, table, key, payload, patch=patch):
                        result.counts["partial_overlay_without_structural_base"] += 1
                if (
                    count != generation["row_count"]
                    or output.hexdigest() != generation["output_digest"]
                ):
                    raise ClosureError("selected_output_digest_mismatch")
            _close(spool, result)
            spool.commit()
            yield result
        finally:
            spool.close()
