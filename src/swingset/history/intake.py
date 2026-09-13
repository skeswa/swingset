"""One resumable phase-1 catalog, one persistent request gate, and explicit failures."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from swingset.clock import Clock
from swingset.config import Config, SourceConfig
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.client import FetchClient
from swingset.fetch.wayback import schedule_capture
from swingset.history.catalog import Target, load_catalog, save_catalog
from swingset.history.closure import synchronize_year_findings, year_gaps
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import upsert_watch
from swingset.sources import get_page_kind
from swingset.sources.base import WatchSpec
from swingset.sources.wsdc_newsletter.index import is_issue_url
from swingset.state.db import Database
from swingset.state.findings import Finding, replace_findings
from swingset.state.work import WorkUnit


def bootstrap(checkpoint: Path, state: Path) -> None:
    """Copy the database; share only immutable artifact inodes on the same device."""
    state.mkdir(parents=True, exist_ok=True)
    if (state / "state.sqlite").exists():
        return
    same_device = checkpoint.stat().st_dev == state.stat().st_dev
    shutil.copy2(checkpoint / "state.sqlite", state / "state.sqlite")
    for directory in ("blobs", "extracts", "inputs"):
        source = checkpoint / directory
        if source.exists():
            shutil.copytree(
                source,
                state / directory,
                dirs_exist_ok=True,
                copy_function=os.link if same_device else shutil.copy2,
            )
    durable_write(
        state / "phase1-baseline.json",
        canonical({"checkpoint": str(checkpoint), "immutable_hardlinks": same_device}),
    )


def intake_config(config: Config) -> Config:
    sources = dict(config.sources)
    sources.update(
        {
            source: SourceConfig(True)
            for source in ("swingdancecouncil", "wsdc_calendar", "wsdc_newsletter")
        }
    )
    archive = config.host("web.archive.org")
    hosts = dict(config.hosts)
    hosts["web.archive.org"] = replace(
        archive,
        min_gap_seconds=max(10, archive.min_gap_seconds),
        daily_request_budget=min(200, archive.daily_request_budget),
    )
    return replace(config, sources=sources, hosts=hosts)


def _snapshot(database: Database, target: Target) -> dict[str, Any] | None:
    conn = database.connection
    if target.archive_url:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE url=? AND (archive_url=? OR requested_archive_url=?) AND http_status=200 AND classification='Ok' ORDER BY fetched_at DESC,snapshot_id DESC LIMIT 1",
            (target.url, target.archive_url, target.archive_url),
        ).fetchone()
    else:
        parsed = urlsplit(target.url)
        alternate = (
            urlunsplit(parsed._replace(scheme="http" if parsed.scheme == "https" else "https"))
            if target.source == "wsdc_newsletter"
            else target.url
        )
        row = conn.execute(
            "SELECT * FROM snapshots WHERE url IN (?,?) AND via='origin' AND http_status=200 AND classification='Ok' ORDER BY fetched_at DESC,snapshot_id DESC LIMIT 1",
            (target.url, alternate),
        ).fetchone()
    return dict(row) if row else None


def run_intake(
    database: Database,
    catalog_path: Path,
    *,
    config: Config,
    clock: Clock,
    max_targets: int = 200,
    wall_seconds: int = 2700,
    retry_failures: bool = False,
    transport: Any = None,
) -> dict[str, Any]:
    targets = []
    issue_keys = set()
    for target in load_catalog(catalog_path):
        if target.parser == "wsdc_newsletter.events":
            if not is_issue_url(target.url):
                continue
            key = urlsplit(target.url).netloc, urlsplit(target.url).path
            if key in issue_keys:
                continue
            issue_keys.add(key)
        targets.append(target)
    save_catalog(catalog_path, tuple(targets))
    ledger_path = database.state_dir / "phase1-ledger.json"
    ledger: dict[str, Any] = (
        json.loads(ledger_path.read_bytes())
        if ledger_path.exists()
        else {"version": 1, "targets": {}}
    )
    statuses: dict[str, dict[str, Any]] = ledger["targets"]
    run_id = database.start_run(clock.now(), dry_run=True)
    with database.transaction() as conn:
        synchronize_year_findings(
            conn,
            year_gaps(
                conn,
                tuple(targets),
                statuses,
                final_year=clock.now().year,
                history_start=config.history_start,
            ),
            now=clock.now().isoformat(),
            run_id=run_id,
        )
    archive = Archive(database.state_dir)
    client = FetchClient(
        database.connection, intake_config(config), clock, archive, transport=transport
    )
    deadline = clock.now() + timedelta(seconds=wall_seconds)
    handled = 0
    try:
        for target in targets:
            prior = statuses.get(target.target_id, {})
            if (
                prior.get("status") == "duplicate"
                or (
                    prior.get("status") in {"parsed", "empty"}
                    and prior.get("parser_version")
                    == str(get_page_kind(target.parser).PARSER_VERSION)
                )
                or (prior.get("status") == "finding" and not retry_failures)
            ):
                continue
            if handled >= max_targets or clock.now() >= deadline:
                break
            spec = WatchSpec(
                "",
                target.source,
                "index",
                "GET",
                target.url,
                target.parser,
                archive_url=target.archive_url,
            )
            snapshot = _snapshot(database, target)
            if snapshot is None and target.digest:
                duplicate = next(
                    (
                        row
                        for row in statuses.values()
                        if row.get("digest") == target.digest
                        and row.get("source") == target.source
                        and row.get("status") in {"parsed", "empty"}
                    ),
                    None,
                )
                if duplicate:
                    statuses[target.target_id] = {
                        **asdict(target),
                        "status": "duplicate",
                        "snapshot_id": duplicate["snapshot_id"],
                        "duplicate_of": duplicate["target_id"],
                        "target_id": target.target_id,
                    }
                    durable_write(ledger_path, canonical(ledger))
                    continue
            try:
                if snapshot is None:
                    if target.archive_url:
                        schedule_capture(database.connection, spec, now=clock.now())
                    else:
                        upsert_watch(database.connection, spec, clock.now())
                    fetched = client.fetch(
                        spec.watch_id, get_page_kind(target.parser), run_id, deadline=deadline
                    )
                    if fetched.skipped:
                        # A gate stop is resumable work, not a missing-source claim.
                        statuses[target.target_id] = {
                            **asdict(target),
                            "target_id": target.target_id,
                            "status": "pending",
                            "reason": fetched.skipped,
                        }
                        durable_write(ledger_path, canonical(ledger))
                        break
                    if fetched.snapshot_id:
                        snapshot = dict(
                            database.connection.execute(
                                "SELECT * FROM snapshots WHERE snapshot_id=?",
                                (fetched.snapshot_id,),
                            ).fetchone()
                        )
                    if snapshot is None or snapshot["classification"] != "Ok":
                        raise ValueError("event-list fetch did not return a usable 200 response")
                attempt = parse_snapshot(
                    database,
                    archive,
                    WorkUnit("parse", "snapshot", str(snapshot["snapshot_id"])),
                    clock,
                    run_id,
                )
                if attempt.failed:
                    raise ValueError(
                        "event-list parser failed; see archived body and parse finding"
                    )
                count = database.connection.execute(
                    "SELECT COUNT(*) FROM observations WHERE snapshot_id=?",
                    (snapshot["snapshot_id"],),
                ).fetchone()[0]
                status = "parsed" if count or target.parser.endswith(".index") else "empty"
                receipt = {
                    **asdict(target),
                    "target_id": target.target_id,
                    "status": status,
                    "snapshot_id": snapshot["snapshot_id"],
                    "observations": count,
                    "observed_at": snapshot["observed_at"] or snapshot["fetched_at"],
                    "parser_version": str(get_page_kind(target.parser).PARSER_VERSION),
                }
                statuses[target.target_id] = receipt
                _finding(database, target, (), clock.now().isoformat(), run_id)
                if target.parser == "wsdc_newsletter.index":
                    discovered = [
                        Target(
                            "wsdc_newsletter",
                            str(row[0]),
                            "wsdc_newsletter.events",
                            catalog_evidence=str(snapshot["snapshot_id"]),
                        )
                        for row in database.connection.execute(
                            "SELECT url FROM watches WHERE source='wsdc_newsletter' AND parser='wsdc_newsletter.events' ORDER BY url"
                        )
                    ]
                    for item in discovered:
                        key = urlsplit(item.url).netloc, urlsplit(item.url).path
                        if is_issue_url(item.url) and key not in issue_keys:
                            targets.append(item)
                            issue_keys.add(key)
                    save_catalog(catalog_path, tuple(targets))
            except (ValueError, OSError) as error:
                receipt = {
                    **asdict(target),
                    "target_id": target.target_id,
                    "status": "finding",
                    "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
                    "error": str(error),
                }
                statuses[target.target_id] = receipt
                _finding(
                    database,
                    target,
                    (
                        Finding(
                            "phase1_capture",
                            "history_capture",
                            target.target_id,
                            "warning",
                            str(error),
                            receipt,
                            snapshot_id=receipt["snapshot_id"],
                        ),
                    ),
                    clock.now().isoformat(),
                    run_id,
                )
            handled += 1
            durable_write(ledger_path, canonical(ledger))
            print(
                json.dumps(
                    {
                        "phase1": target.target_id,
                        "source": target.source,
                        "timestamp": target.timestamp,
                        "status": statuses[target.target_id]["status"],
                        "handled": handled,
                    }
                ),
                flush=True,
            )
    finally:
        client.close()
    for target in targets:
        statuses.setdefault(
            target.target_id, {**asdict(target), "target_id": target.target_id, "status": "pending"}
        )
    ledger["run_id"] = run_id
    ledger["updated_at"] = clock.now().isoformat()
    durable_write(ledger_path, canonical(ledger))
    with database.transaction() as conn:
        synchronize_year_findings(
            conn,
            year_gaps(
                conn,
                tuple(targets),
                statuses,
                final_year=clock.now().year,
                history_start=config.history_start,
            ),
            now=clock.now().isoformat(),
            run_id=run_id,
        )
    return ledger


def _finding(
    database: Database, target: Target, findings: tuple[Finding, ...], now: str, run_id: str
) -> None:
    with database.transaction() as conn:
        replace_findings(
            conn,
            owner_kind="phase1_capture",
            owner_id=target.target_id,
            findings=findings,
            opened_at=now,
            run_id=run_id,
        )


def import_recording(
    database: Database,
    *,
    body_path: Path,
    metadata_path: Path,
    source: str,
    parser: str,
    clock: Clock,
) -> str:
    """Reuse already-fetched fixture bytes with their recorded original provenance."""
    from datetime import datetime

    from swingset.fetch.archive import digest

    metadata = json.loads(metadata_path.read_bytes())
    body = body_path.read_bytes()
    if digest(body) != metadata["body_sha256"]:
        raise ValueError("recorded fixture digest does not match metadata")
    spec = WatchSpec(
        "", source, "index", "GET", metadata["url"], parser, archive_url=metadata.get("archive_url")
    )
    conn = database.connection
    conn.execute(
        "INSERT OR IGNORE INTO runs(run_id,started_at,dry_run) VALUES (?,?,1)",
        (metadata["run_id"], metadata["fetched_at"]),
    )
    upsert_watch(conn, spec, datetime.fromisoformat(metadata["fetched_at"]))
    archive = Archive(database.state_dir)
    archive.store_body(body)
    fields = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    values = {key: value for key, value in metadata.items() if key in fields}
    values.update(
        watch_id=spec.watch_id,
        extract_sha256=None,
        extract_status="pending",
        parse_status="pending",
        requested_archive_url=metadata.get("requested_archive_url") or metadata.get("archive_url"),
    )
    columns = ",".join(values)
    marks = ",".join("?" for _ in values)
    conn.execute(
        f"INSERT OR IGNORE INTO snapshots({columns}) VALUES ({marks})", tuple(values.values())
    )
    result = parse_snapshot(
        database,
        archive,
        WorkUnit("parse", "snapshot", metadata["snapshot_id"]),
        clock,
        metadata["run_id"],
    )
    if result.failed:
        raise ValueError("recorded fixture failed to parse; inspect its preserved finding")
    return str(metadata["snapshot_id"])
