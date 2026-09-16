"""Transfer finite phase-1 evidence without replacing a live database."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from swingset.clock import Clock
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.history.catalog import Target, load_catalog, retained_evidence_path, save_catalog
from swingset.history.closure import synchronize_year_findings, year_gaps
from swingset.model.history import HISTORY_START
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import Database
from swingset.state.work import WorkUnit

_HOSTS = ("web.archive.org", "worldsdc.com", "www.worldsdc.com")
_PARSERS = {
    "swingdancecouncil.events",
    "wsdc_calendar.events",
    "wsdc_newsletter.index",
    "wsdc_newsletter.events",
}


def export_evidence(
    database: Database, output: Path, *, repository: Path | None = None
) -> dict[str, int]:
    """Export catalog receipts and their immutable evidence under a reader lock."""
    conn = database.connection
    catalog = json.loads((database.state_dir / "phase1-catalog.json").read_bytes())
    ledger = json.loads((database.state_dir / "phase1-ledger.json").read_bytes())
    ids = {row.get("snapshot_id") for row in ledger["targets"].values()} - {None}
    snapshots = [
        dict(conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (identifier,)).fetchone())
        for identifier in sorted(ids)
    ]
    watches = {
        row["watch_id"]: dict(
            conn.execute("SELECT * FROM watches WHERE watch_id=?", (row["watch_id"],)).fetchone()
        )
        for row in snapshots
    }
    if any(row["parser"] not in _PARSERS or row["kind"] != "index" for row in watches.values()):
        raise ValueError("phase1 export contains a non-event-list watch")
    runs = {
        row["run_id"]: dict(
            conn.execute("SELECT * FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
        )
        for row in snapshots
    }
    source, destination = Archive(database.state_dir), Archive(output)
    for row in snapshots:
        if row["body_sha256"]:
            destination.store_body(source.read_body(row["body_sha256"]))
        if row["extract_sha256"]:
            destination.store_extract(source.read_extract(row["extract_sha256"]))
    cdx_receipts = [
        json.loads(path.read_bytes())
        for path in sorted((database.state_dir / "archive-cdx").glob("*/*.json"))
    ]
    for receipt in cdx_receipts:
        destination.store_body(source.read_body(receipt["body_sha256"]))
    catalog_inputs = []
    repository = repository or Path(__file__).resolve().parents[3]
    for relative in sorted({row["catalog_evidence"] for row in catalog["targets"]}):
        if not relative.startswith(("research/verification/", "journal/evidence/")):
            continue
        path = retained_evidence_path(repository, relative)
        catalog_inputs.append(
            {"path": relative, "body_sha256": destination.store_body(path.read_bytes())}
        )
    manifest = {
        "version": 1,
        "source_schema_version": database.schema_version,
        "catalog_inputs": catalog_inputs,
        "cdx_receipts": cdx_receipts,
        "catalog": catalog,
        "ledger": ledger,
        "snapshots": snapshots,
        "watches": watches,
        "runs": runs,
        "archive_queries": [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM archive_queries WHERE source IN ('wsdc_calendar','swingdancecouncil')"
            )
        ],
        "archive_captures": [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM archive_captures WHERE source IN ('wsdc_calendar','swingdancecouncil')"
            )
        ],
        "hosts": [
            dict(row) for row in conn.execute("SELECT * FROM hosts WHERE host IN (?,?,?)", _HOSTS)
        ],
        "host_budget": [
            dict(row)
            for row in conn.execute("SELECT * FROM host_budget WHERE host IN (?,?,?)", _HOSTS)
        ],
    }
    for host in manifest["hosts"]:
        if host["robots_sha256"]:
            destination.store_body(source.read_body(host["robots_sha256"]))
    durable_write(output / "phase1-export.json", canonical(manifest))
    return {"snapshots": len(snapshots), "watches": len(watches)}


def _insert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> bool:
    allowed = {str(column[1]) for column in conn.execute(f"PRAGMA table_info({table})")}
    if not set(row) <= allowed:
        raise ValueError(f"unknown {table} fields in phase1 export")
    columns = ",".join(row)
    marks = ",".join("?" for _ in row)
    return bool(
        conn.execute(
            f"INSERT OR IGNORE INTO {table}({columns}) VALUES ({marks})", tuple(row.values())
        ).rowcount
        > 0
    )


def import_evidence(database: Database, package: Path, *, clock: Clock) -> dict[str, int]:
    """Idempotent evidence merge; live watch controls and existing facts survive."""
    manifest = json.loads((package / "phase1-export.json").read_bytes())
    if manifest.get("version") != 1:
        raise ValueError("unsupported phase1 evidence export")
    if len(manifest["snapshots"]) > 10000:
        raise ValueError("phase1 export exceeds the bounded snapshot limit")
    watches = manifest["watches"]
    if any(row["parser"] not in _PARSERS or row["kind"] != "index" for row in watches.values()):
        raise ValueError("phase1 import contains a non-event-list watch")
    source, destination = Archive(package), Archive(database.state_dir)
    for row in manifest["snapshots"]:
        if row["body_sha256"]:
            destination.store_body(source.read_body(row["body_sha256"]))
        if row["extract_sha256"]:
            destination.store_extract(source.read_extract(row["extract_sha256"]))
    for host in manifest["hosts"]:
        if host["robots_sha256"]:
            destination.store_body(source.read_body(host["robots_sha256"]))
    for item in manifest.get("catalog_inputs", []):
        destination.store_body(source.read_body(item["body_sha256"]))
    for receipt in manifest.get("cdx_receipts", []):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", receipt["query_id"]) or not re.fullmatch(
            r"probe|[0-9]+", receipt["page"]
        ):
            raise ValueError("invalid CDX receipt identifier")
        destination.store_body(source.read_body(receipt["body_sha256"]))
        path = (
            database.state_dir / "archive-cdx" / receipt["query_id"] / (receipt["page"] + ".json")
        )
        if path.exists() and json.loads(path.read_bytes()) != receipt:
            raise ValueError("CDX receipt identifier collides with existing evidence")
        durable_write(path, canonical(receipt))
    conn = database.connection
    catalog_path = database.state_dir / "phase1-catalog.json"
    ledger_path = database.state_dir / "phase1-ledger.json"
    previous_targets = load_catalog(catalog_path) if catalog_path.exists() else ()
    merged = {target.target_id: target for target in previous_targets}
    merged.update(
        {
            target.target_id: target
            for target in (Target(**row) for row in manifest["catalog"]["targets"])
        }
    )
    previous_ledger: dict[str, Any] = (
        json.loads(ledger_path.read_bytes())
        if ledger_path.exists()
        else {"version": 1, "targets": {}}
    )
    previous_ledger["targets"].update(manifest["ledger"]["targets"])
    # Nested parse transactions use savepoints. A process interruption cannot
    # commit temporary watch state or a partially imported evidence graph.
    with database.transaction():
        run_id = database.start_run(clock.now(), dry_run=True)
        imported, failed = _merge_evidence(database, manifest, destination, clock, run_id)
        synchronize_year_findings(
            conn,
            year_gaps(
                conn,
                tuple(merged.values()),
                previous_ledger["targets"],
                final_year=clock.now().year,
                history_start=HISTORY_START,
            ),
            now=clock.now().isoformat(),
            run_id=run_id,
        )
        conn.execute(
            "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
            (
                clock.now().isoformat(),
                json.dumps({"imported": imported, "parse_failures": failed}),
                run_id,
            ),
        )
    # Merge in memory and replace each receipt file only once. A retry after
    # interruption reconciles files with the already atomic database import.
    save_catalog(catalog_path, tuple(merged.values()))
    durable_write(ledger_path, canonical(previous_ledger))
    return {"imported": imported, "parse_failures": failed}


def _merge_evidence(
    database: Database, manifest: dict[str, Any], destination: Archive, clock: Clock, run_id: str
) -> tuple[int, int]:
    conn = database.connection
    watches = manifest["watches"]
    imported = failed = 0
    existing_watch_ids = {str(row[0]) for row in conn.execute("SELECT watch_id FROM watches")}
    preserved: dict[str, dict[str, Any]] = {}
    with database.transaction():
        for row in manifest["runs"].values():
            _insert(conn, "runs", row)
        for watch_id, row in watches.items():
            existing = conn.execute(
                "SELECT * FROM watches WHERE watch_id=?", (watch_id,)
            ).fetchone()
            if existing:
                preserved[watch_id] = dict(existing)
            upsert_watch(
                conn,
                WatchSpec(
                    "",
                    row["source"],
                    "index",
                    row["method"],
                    row["url"],
                    row["parser"],
                    archive_url=row["archive_url"],
                ),
                clock.now(),
            )
        for row in manifest["snapshots"]:
            prior = conn.execute(
                "SELECT body_sha256,url,fetched_at FROM snapshots WHERE snapshot_id=?",
                (row["snapshot_id"],),
            ).fetchone()
            if prior and tuple(prior) != (row["body_sha256"], row["url"], row["fetched_at"]):
                raise ValueError("snapshot identifier collides with different immutable evidence")
            imported += _insert(conn, "snapshots", row)
        _merge_transport(conn, manifest)
    try:
        for row in sorted(
            manifest["snapshots"],
            key=lambda row: (row["observed_at"] or row["fetched_at"], row["snapshot_id"]),
        ):
            if row["http_status"] == 200 and row["classification"] == "Ok":
                failed += parse_snapshot(
                    database,
                    destination,
                    WorkUnit("parse", "snapshot", row["snapshot_id"]),
                    clock,
                    run_id,
                ).failed
    finally:
        with database.transaction():
            for watch_id in watches:
                if watch_id in preserved:
                    row = preserved[watch_id]
                    columns = [key for key in row if key != "watch_id"]
                    conn.execute(
                        "UPDATE watches SET "
                        + ",".join(key + "=?" for key in columns)
                        + " WHERE watch_id=?",
                        tuple(row[key] for key in columns) + (watch_id,),
                    )
                else:
                    conn.execute(
                        "UPDATE watches SET state='sealed',next_check_at=NULL WHERE watch_id=?",
                        (watch_id,),
                    )
    with database.transaction():
        for row in conn.execute(
            "SELECT watch_id,parser FROM watches WHERE kind='index'"
        ).fetchall():
            if row[0] not in existing_watch_ids and row[1] in _PARSERS:
                conn.execute(
                    "UPDATE watches SET state='sealed',next_check_at=NULL WHERE watch_id=?",
                    (row[0],),
                )
    return imported, failed


def _merge_transport(conn: sqlite3.Connection, manifest: dict[str, Any]) -> None:
    for row in manifest["archive_queries"]:
        _insert(conn, "archive_queries", row)
        conn.execute(
            "UPDATE archive_queries SET next_page=MAX(next_page,?),total_pages=COALESCE(total_pages,?),completed_at=COALESCE(completed_at,?) WHERE query_id=?",
            (row["next_page"], row["total_pages"], row["completed_at"], row["query_id"]),
        )
    for row in manifest["archive_captures"]:
        _insert(conn, "archive_captures", row)
    for row in manifest["host_budget"]:
        conn.execute(
            "INSERT INTO host_budget(host,day,requests,bytes) VALUES (?,?,?,?) ON CONFLICT(host,day) DO UPDATE SET requests=MAX(requests,excluded.requests),bytes=MAX(bytes,excluded.bytes)",
            (row["host"], row["day"], row["requests"], row["bytes"]),
        )
    for row in manifest["hosts"]:
        _insert(conn, "hosts", row)
        current = conn.execute("SELECT * FROM hosts WHERE host=?", (row["host"],)).fetchone()
        next_allowed = max(
            (value for value in (current["next_allowed_at"], row["next_allowed_at"]) if value),
            default=None,
        )
        paused = max(
            (value for value in (current["paused_until"], row["paused_until"]) if value),
            default=None,
        )
        conn.execute(
            "UPDATE hosts SET next_allowed_at=?,paused_until=?,pause_reason=COALESCE(pause_reason,?) WHERE host=?",
            (next_allowed, paused, row["pause_reason"], row["host"]),
        )
