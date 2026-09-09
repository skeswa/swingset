"""One budgeted cycle, with durable downstream work ahead of new requests."""

import json
import os
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

from swingset import __version__
from swingset.build.builder import BuildMetadata, BuildResult, build_candidate
from swingset.build.input import read_build_input
from swingset.clock import Clock
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.client import FetchClient
from swingset.log import log
from swingset.publish.service import Hub, expected_parent, publish, reconcile
from swingset.schedule.discover import discover
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.registry import advance_sweep, discover_registry
from swingset.schedule.watches import due_watches, refresh_policy
from swingset.sources import get_page_kind, sources
from swingset.state.db import Database
from swingset.state.inputs import InputBundle, accept, capture
from swingset.state.work import next_work


def repository_identity(package_root: Path | None = None) -> str:
    """Return the deployed revision, or a deterministic digest for local source."""
    revision = os.environ.get("SWINGSET_REVISION", "").strip()
    if revision:
        return revision

    root = package_root or Path(__file__).resolve().parents[1]
    digest = sha256()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        body = path.read_bytes()
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return f"source-sha256:{digest.hexdigest()}"


def versions() -> dict[str, str]:
    from swingset.link.service import LINKER_VERSION
    from swingset.project import PROJECTOR_VERSION

    result = {
        "package": __version__,
        "projector": str(PROJECTOR_VERSION),
        "linker": str(LINKER_VERSION),
        "repository": repository_identity(),
        "schema": "1",
    }
    for source in sources():
        for name, page in source.page_kinds.items():
            result[f"extract/{name}"] = str(page.EXTRACT_VERSION)
            result[f"parser/{name}"] = str(page.PARSER_VERSION)
    return result


def baseline_commit(state_dir: Path) -> str | None:
    baseline = state_dir / "baseline"
    if not baseline.is_symlink():
        return None
    return str(json.loads((baseline / "PUBLISHED").read_bytes())["commit"])


def build(
    database: Database, bundle: InputBundle, clock: Clock, run_id: str, remote: Hub | None = None
) -> BuildResult:
    from swingset.publish.card import render_card

    with database.transaction(immediate=False) as conn:
        data = read_build_input(conn, bundle)
    captured_versions = {
        str(name): str(value) for name, value in json.loads(bundle.files["versions.json"]).items()
    }
    meta = BuildMetadata(
        run_id=run_id,
        repository_commit=captured_versions["repository"],
        expected_parent=expected_parent(database.state_dir, remote)
        if remote
        else baseline_commit(database.state_dir),
        schema_version=1,
        versions=captured_versions,
        card=render_card(data),
        built_at=clock.now(),
    )
    return build_candidate(
        database.state_dir,
        data,
        meta,
        suppressions=bundle.csv("suppressions.csv"),
        card_renderer=render_card,
    )


def run_cycle(
    database: Database,
    *,
    config_dir: Path,
    overrides_dir: Path,
    clock: Clock,
    dry_run: bool = True,
    budget: float = 720,
    hub: Hub | None = None,
    transport: httpx.BaseTransport | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    started = clock.now()
    deadline = started + timedelta(seconds=budget)
    run_id = database.start_run(started, dry_run=dry_run)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": dry_run,
        "checked": 0,
        "changed": 0,
        "not_modified": 0,
        "nonce_unchanged": 0,
        "stopped": False,
        "failed": False,
        "stages": [],
        "bytes_by_host": {},
    }
    attempts: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    archive = Archive(database.state_dir)
    fetcher: FetchClient | None = None

    def stop() -> bool:
        return should_stop() or clock.now() >= deadline

    try:
        bundle = capture(config_dir, overrides_dir, database.state_dir, versions())
        summary["accepted_inputs"] = sorted(accept(database, bundle, clock))
        if hub is not None:
            reconciled = reconcile(database.state_dir, hub, dry_run=dry_run)
            if reconciled.commit:
                summary["publish_commit"] = reconciled.commit
        queued = database.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0]
        if not queued and not stop():
            discover(database, bundle, clock.now())
            if bundle.config.enabled("wsdc_registry"):
                discover_registry(database, clock.now())
            fetcher = FetchClient(
                database.connection,
                bundle.config,
                clock,
                archive,
                transport=transport,
                should_stop=should_stop,
            )
            for watch_id in due_watches(database.connection, bundle.config, clock.now()):
                if stop():
                    break
                row = database.connection.execute(
                    "SELECT parser,notes,url FROM watches WHERE watch_id=?", (watch_id,)
                ).fetchone()
                result = fetcher.fetch(
                    watch_id,
                    get_page_kind(row["parser"]),
                    run_id,
                    deadline=deadline,
                    sweep=row["notes"] == "sweep",
                )
                if result.skipped:
                    continue
                summary["checked"] += 1
                summary["changed"] += int(result.changed)
                summary["not_modified"] += int(result.classification.outcome == "NotModified")
                summary["nonce_unchanged"] += int(result.nonce_unchanged)
                from urllib.parse import urlsplit

                host = urlsplit(row["url"]).hostname or ""
                summary["bytes_by_host"][host] = (
                    summary["bytes_by_host"].get(host, 0) + result.body_bytes
                )
                with database.transaction() as conn:
                    refresh_policy(
                        conn,
                        bundle.config,
                        watch_id,
                        clock.now(),
                        outcome=result.classification.outcome,
                    )
        for stage in ("parse", "project", "link"):
            while not stop() and (unit := next_work(database.connection, stage)) is not None:
                if stage not in summary["stages"]:
                    summary["stages"].append(stage)
                if stage == "parse":
                    attempt = parse_snapshot(database, archive, unit, clock, run_id)
                    attempts[attempt.source] += 1
                    failures[attempt.source] += int(attempt.failed)
                    if attempt.source == "wsdc_registry":
                        with database.transaction():
                            advance_sweep(database, now=clock.now())
                elif stage == "project":
                    from swingset.project import process_unit

                    process_unit(database, unit, bundle, clock, run_id)
                else:
                    from swingset.link import link_event

                    link_event(database, unit.unit_id, bundle, clock, run_id)
            if next_work(database.connection, stage) is not None:
                break
        settled = not database.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0]
        if settled and not stop():
            candidate = build(database, bundle, clock, run_id, remote=hub if not dry_run else None)
            if not candidate.reused:
                summary["stages"].append("build")
            summary["candidate_id"] = candidate.candidate_id
            if not dry_run:
                if hub is None:
                    raise ValueError("publication enabled but no Hub adapter configured")
                published = publish(database.state_dir, candidate.path, hub, dry_run=False)
                if published.commit:
                    summary["publish_commit"] = published.commit
        summary["parse_errors"] = dict(failures)
        summary["failed"] = any(
            failures[source] / count > 0.1 for source, count in attempts.items()
        )
        paused = [
            dict(row)
            for row in database.connection.execute(
                "SELECT host,paused_until,pause_reason FROM hosts WHERE paused_until IS NOT NULL"
            )
            if __import__("datetime").datetime.fromisoformat(row["paused_until"]) > clock.now()
        ]
        summary["paused_hosts"] = paused
        summary["failed"] = summary["failed"] or any(
            row["pause_reason"] == "Blocked" for row in paused
        )
    except Exception as exc:
        summary["failed"] = True
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if fetcher is not None:
            fetcher.close()
        summary["stopped"] = should_stop() or clock.now() >= deadline
        summary["duration_seconds"] = (clock.now() - started).total_seconds()
        summary["finished_at"] = clock.now().isoformat()
        durable_write(database.state_dir / "runs" / f"{run_id}.json", canonical(summary))
        with database.transaction() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
                (summary["finished_at"], json.dumps(summary, sort_keys=True), run_id),
            )
        log("cycle", **summary)
    return summary
