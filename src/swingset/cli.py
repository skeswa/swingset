"""Operator commands sharing the same lock, input capture and durable stages."""

import argparse
import json
import os
import signal
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from swingset import __version__
from swingset.clock import SystemClock
from swingset.config import duration, load_config
from swingset.log import log
from swingset.publish.service import Hub
from swingset.state.db import Database, DatabaseLockedError, open_database


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="swingset")
    root.add_argument("--version", action="version", version=f"swingset {__version__}")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--state", type=Path, default=Path("/var/lib/swingset"))
    shared.add_argument(
        "--config", type=Path, default=Path(os.environ.get("SWINGSET_CONFIG_DIR", "config"))
    )
    shared.add_argument(
        "--overrides",
        type=Path,
        default=Path(os.environ.get("SWINGSET_OVERRIDES_DIR", "overrides")),
    )
    shared.add_argument("--lock-timeout", type=float, default=60)
    commands = root.add_subparsers(dest="command", required=True)
    for name in (
        "doctor",
        "summary",
        "discover",
        "parse",
        "project",
        "link",
        "build",
        "publish",
        "gc",
    ):
        commands.add_parser(name, parents=[shared])
    cycle = commands.add_parser("cycle", parents=[shared])
    mode = cycle.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--publish", dest="dry_run", action="store_false")
    cycle.add_argument("--budget", type=duration, default=720)
    cycle.add_argument("--timer", action="store_true")
    fetch = commands.add_parser("fetch-one", parents=[shared])
    fetch.add_argument("url")
    fetch.add_argument("--kind", required=True)
    fetch.add_argument("--form", action="append", default=[], metavar="KEY=VALUE")
    fetch.add_argument("--source-ref")
    for name in ("pause", "resume"):
        command = commands.add_parser(name, parents=[shared])
        selection = command.add_mutually_exclusive_group(required=True)
        selection.add_argument("--all", action="store_true")
        selection.add_argument("--host")
        selection.add_argument("--source")
        if name == "pause":
            command.add_argument("--until")
            command.add_argument("--reason", default="operator request")
    sweep = commands.add_parser("sweep", parents=[shared])
    sweep.add_argument("--start", type=int, default=1)
    reparse = commands.add_parser("reparse", parents=[shared])
    reparse.add_argument("--kind")
    reparse.add_argument("--since")
    crosscheck = commands.add_parser("registry-crosscheck", parents=[shared])
    dump_input = crosscheck.add_mutually_exclusive_group(required=True)
    dump_input.add_argument("dump", type=Path, nargs="?")
    dump_input.add_argument("--blob", help="Replay a dump by its archived SHA-256")
    crosscheck.add_argument("--archive-only", action="store_true")
    enums = commands.add_parser("enums")
    enums.add_argument("--write", action="store_true")
    backup = commands.add_parser("backup", parents=[shared])
    backup.add_argument("--local", action="store_true")
    backup.add_argument("--destination", type=Path)
    restore = commands.add_parser("restore", parents=[shared])
    restore_input = restore.add_mutually_exclusive_group(required=True)
    restore_input.add_argument("--checkpoint", type=Path)
    restore_input.add_argument("--archive-commit")
    restore.add_argument("--writer-stopped", action="store_true", required=True)
    return root


def hub() -> Hub:
    from swingset.publish.huggingface import HuggingFaceHub

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("HF_TOKEN is required for publication or remote verification")
    return HuggingFaceHub("skeswa/swingset", token=token)


def doctor(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    result: dict[str, Any] = {
        "version": __version__,
        "hosts": {name: asdict(value) for name, value in config.hosts.items()},
        "sources": {name: asdict(value) for name, value in config.sources.items()},
        "restore_pending": (args.state / "RESTORE_PENDING").exists(),
    }
    if not (args.state / "state.sqlite").exists():
        result["schema_version"] = 0
        return result
    with open_database(args.state, lock=False, read_only=True) as database:
        with database.transaction(immediate=False) as conn:
            result["schema_version"] = database.schema_version
            for label, query in {
                "operator_pauses": "SELECT * FROM operator_pauses",
                "host_pauses": "SELECT host,paused_until,pause_reason,pause_streak FROM hosts WHERE paused_until IS NOT NULL",
                "budgets": "SELECT * FROM host_budget ORDER BY day DESC,host",
                "watches": "SELECT source,state,COUNT(*) AS count FROM watches GROUP BY source,state",
                "pending_work": "SELECT stage,COUNT(*) AS count FROM pending_work GROUP BY stage",
                "link_statuses": "SELECT status,COUNT(*) AS count FROM identity_links GROUP BY status",
                "last_run": "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1",
                "last_success_by_source": "SELECT w.source,MAX(s.parsed_at) AS last_success FROM snapshots s JOIN watches w USING(watch_id) WHERE s.parse_status='ok' GROUP BY w.source",
                "last_backup": "SELECT key,value FROM meta WHERE key LIKE 'last_backup%'",
            }.items():
                result[label] = [dict(row) for row in conn.execute(query)]
            result["open_findings"] = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE closed_at IS NULL"
            ).fetchone()[0]
            result["review_queue_size"] = (
                result["open_findings"]
                + conn.execute(
                    "SELECT (SELECT COUNT(*) FROM identity_links WHERE status='ambiguous') + "
                    "(SELECT COUNT(*) FROM contests WHERE parse_status='unsupported') + "
                    "(SELECT COUNT(*) FROM source_events e LEFT JOIN source_event_map m "
                    "ON m.source=e.source AND m.source_ref=e.source_ref WHERE m.event_id IS NULL)"
                ).fetchone()[0]
            )
    from swingset.publish.service import pending_candidates

    result["pending_candidates"] = [path.name for path in pending_candidates(args.state)]
    from swingset.schedule.cycle import baseline_commit

    result["last_publish"] = baseline_commit(args.state)
    return result


def daily_summary(result: dict[str, Any]) -> None:
    now = SystemClock().now()
    paused = [
        row
        for row in result.get("host_pauses", [])
        if datetime.fromisoformat(row["paused_until"]) > now
    ]
    for row in paused:
        log("host-paused", **row)
    log(
        "summary",
        paused_hosts=paused,
        operator_pauses=result.get("operator_pauses", []),
        watches=result.get("watches", []),
        links=result.get("link_statuses", []),
        review_queue_size=result.get("review_queue_size", 0),
        budgets=[row for row in result.get("budgets", []) if row["day"] == now.date().isoformat()],
        last_publish=result.get("last_publish"),
        last_backup=result.get("last_backup", []),
        pending_work=result.get("pending_work", []),
        restore_pending=result["restore_pending"],
    )


def _finish_failed_manual_run(
    database: Database, command: str, error: Exception, previous_unfinished: set[str]
) -> None:
    """Close the current command's run after an exception without masking it."""
    row = database.connection.execute(
        "SELECT run_id FROM runs WHERE finished_at IS NULL ORDER BY started_at DESC"
    ).fetchone()
    if row is None or str(row[0]) in previous_unfinished:
        return
    finished_at = SystemClock().now().isoformat()
    completed = {
        "run_id": str(row[0]),
        "command": command,
        "finished_at": finished_at,
        "stopped": False,
        "failed": True,
        "error": f"{type(error).__name__}: {error}",
    }
    from swingset.fetch.archive import canonical, durable_write

    durable_write(database.state_dir / "runs" / f"{row[0]}.json", canonical(completed))
    with database.transaction() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
            (finished_at, json.dumps(completed, sort_keys=True), row[0]),
        )


def _mutate(args: argparse.Namespace, database: Database, stopped: list[bool]) -> int:
    from swingset.fetch.archive import Archive
    from swingset.schedule.cycle import build, run_cycle, versions
    from swingset.state.inputs import accept, capture
    from swingset.state.work import WorkUnit, enqueue, next_work

    clock = SystemClock()
    command = args.command
    if command == "cycle":
        cycle_result = run_cycle(
            database,
            config_dir=args.config,
            overrides_dir=args.overrides,
            clock=clock,
            dry_run=args.dry_run,
            budget=args.budget,
            hub=hub() if not args.dry_run else None,
            should_stop=lambda: stopped[0],
        )
        return int(cycle_result["failed"])
    if command in ("pause", "resume"):
        scope, identifier = (
            ("all", "all")
            if args.all
            else ("host", args.host)
            if args.host
            else ("source", args.source)
        )
        with database.transaction() as conn:
            if command == "pause":
                until = datetime.fromisoformat(args.until) if args.until else None
                if until is not None and until.tzinfo is None:
                    raise ValueError("--until requires an explicit timezone")
                conn.execute(
                    "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason) VALUES (?,?,?,?) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET until_at=excluded.until_at,reason=excluded.reason",
                    (scope, identifier, until.isoformat() if until else None, args.reason),
                )
            else:
                conn.execute(
                    "DELETE FROM operator_pauses WHERE scope_kind=? AND scope_id=?",
                    (scope, identifier),
                )
        log(command, scope=scope, scope_id=identifier)
        return 0
    bundle = capture(args.config, args.overrides, args.state, versions())
    accept(database, bundle, clock)
    run_id = database.start_run(clock.now(), dry_run=command != "publish")
    archive = Archive(args.state)
    command_failed = False
    if command == "sweep":
        from swingset.schedule.registry import seed_sweep

        seed_sweep(database, args.start)
    elif command == "reparse":
        clauses = []
        values = []
        if args.kind:
            clauses.append("w.parser=?")
            values.append(args.kind)
        if args.since:
            clauses.append("s.fetched_at>=?")
            values.append(datetime.fromisoformat(args.since).isoformat())
        query = "SELECT s.snapshot_id FROM snapshots s JOIN watches w USING(watch_id)"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with database.transaction() as conn:
            enqueue(
                conn,
                (WorkUnit("parse", "snapshot", str(row[0])) for row in conn.execute(query, values)),
                enqueued_at=clock.now().isoformat(),
            )
    elif command == "fetch-one":
        from swingset.fetch.client import FetchClient
        from swingset.schedule.watches import refresh_policy, upsert_watch
        from swingset.sources import get_page_kind
        from swingset.sources.base import WatchSpec

        page = get_page_kind(args.kind)
        source = args.kind.split(".")[0]
        form = tuple(tuple(part.split("=", 1)) for part in args.form)
        if any(len(pair) != 2 for pair in form):
            raise ValueError("--form requires KEY=VALUE")
        pairs = tuple((pair[0], pair[1]) for pair in form)
        ref = args.source_ref
        if source == "wsdc_registry" and ref is None:
            ref = "wsdc:" + dict(pairs).get("num", "")
        spec = WatchSpec(
            "",
            source,
            {
                "wsdc_calendar.events": "index",
                "wsdc_registry.dancer": "dancer",
                "eepro.index": "index",
                "eepro.autoindex": "autoindex",
                "eepro.round": "round",
                "scoringdance.sitemap": "index",
                "scoringdance.recent": "index",
                "scoringdance.event": "event",
                "scoringdance.round": "round",
                "wdr.rounds": "event",
                "wdr.awards": "json",
            }[args.kind],
            "POST" if pairs else "GET",
            args.url,
            args.kind,
            pairs or None,
            ref,
        )
        with database.transaction() as conn:
            upsert_watch(conn, spec, clock.now())
        client = FetchClient(
            database.connection, bundle.config, clock, archive, should_stop=lambda: stopped[0]
        )
        try:
            result = client.fetch(spec.watch_id, page, run_id)
        finally:
            client.close()
        if result.skipped:
            raise ValueError(f"fetch skipped: {result.skipped}")
        with database.transaction() as conn:
            refresh_policy(
                conn,
                bundle.config,
                spec.watch_id,
                clock.now(),
                outcome=result.classification.outcome,
            )
        from swingset.fetch.classify import Outcome

        if result.classification.outcome in {
            Outcome.BLOCKED,
            Outcome.INVALID,
            Outcome.THROTTLED,
            Outcome.SERVER_ERROR,
            Outcome.REDIRECT,
        }:
            raise RuntimeError(
                f"fetch failed with classification {result.classification.outcome.value}"
            )
        print(json.dumps(asdict(result), default=str))
    elif command == "registry-crosscheck":
        from swingset.schedule.registry import (
            archive_crosscheck_dump,
            crosscheck,
            replay_crosscheck,
        )

        if args.archive_only:
            if args.blob:
                raise ValueError("--archive-only requires a dump path, not --blob")
            print(archive_crosscheck_dump(database, args.dump, archive, clock.now(), run_id))
        elif args.blob:
            print(replay_crosscheck(database, args.blob, archive, clock.now(), run_id))
        else:
            print(crosscheck(database, args.dump, archive, clock.now(), run_id))
    elif command == "discover":
        from swingset.schedule.discover import discover

        print(discover(database, bundle, clock.now()))
    elif command in ("parse", "project", "link"):
        from collections import Counter

        attempts: Counter[str] = Counter()
        failures: Counter[str] = Counter()
        while not stopped[0] and (unit := next_work(database.connection, command)) is not None:
            if command == "parse":
                from swingset.schedule.parse import parse_snapshot

                attempt = parse_snapshot(database, archive, unit, clock, run_id)
                attempts[attempt.source] += 1
                failures[attempt.source] += int(attempt.failed)
                if attempt.source == "wsdc_registry":
                    from swingset.schedule.registry import advance_sweep

                    with database.transaction():
                        advance_sweep(database, now=clock.now())
            elif command == "project":
                from swingset.project import process_unit

                process_unit(database, unit, bundle, clock, run_id)
            else:
                from swingset.link import link_event

                link_event(database, unit.unit_id, bundle, clock, run_id)
        if (
            command == "project"
            and not stopped[0]
            and next_work(database.connection, "parse") is None
            and next_work(database.connection, "project") is None
        ):
            from swingset.schedule.registry import run_saved_crosscheck_if_due

            run_saved_crosscheck_if_due(database, archive, clock.now(), run_id)
        command_failed = any(failures[source] / count > 0.1 for source, count in attempts.items())
    elif command in ("build", "publish"):
        from swingset.publish.service import publish, reconcile

        remote = hub() if command == "publish" else None
        if remote:
            reconcile(args.state, remote, dry_run=False)
        candidate = build(database, bundle, clock, run_id, remote=remote)
        if remote:
            print(publish(args.state, candidate.path, remote))
        else:
            print(candidate.path)
    elif command == "backup":
        from swingset.backup.checkpoint import create_checkpoint

        destination = args.destination or args.state / "checkpoints" / run_id
        checkpoint = create_checkpoint(
            args.state,
            database.connection,
            destination,
            schema_version=1,
            versions=versions(),
            input_bundle_hash=bundle.digest,
        )
        if args.local:
            print(checkpoint.path)
        else:
            from swingset.backup.archive import upload_checkpoint
            from swingset.backup.huggingface import HuggingFaceArchive

            token = os.environ.get("HF_TOKEN")
            if not token:
                raise ValueError(
                    "HF_TOKEN required to upload backup; --local creates a local checkpoint only"
                )
            sha = upload_checkpoint(
                checkpoint, HuggingFaceArchive("skeswa/swingset-archive", token=token)
            )
            with database.transaction() as conn:
                receipt_values = [("last_backup_at", clock.now().isoformat())]
                if sha is not None:
                    receipt_values.append(("last_backup_commit", sha))
                for key, value in receipt_values:
                    conn.execute(
                        "INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value),
                    )
    elif command == "gc":
        from swingset.backup.checkpoint import garbage_collect

        print(
            json.dumps(
                [
                    str(p)
                    for p in garbage_collect(
                        args.state, older_than=86400, now=clock.now().timestamp()
                    )
                ]
            )
        )
    else:
        raise ValueError(f"unsupported command {command}")
    from swingset.fetch.archive import canonical, durable_write

    completed = {
        "run_id": run_id,
        "command": command,
        "finished_at": clock.now().isoformat(),
        "stopped": stopped[0],
        "failed": command_failed,
    }
    durable_write(args.state / "runs" / f"{run_id}.json", canonical(completed))
    with database.transaction() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
            (completed["finished_at"], json.dumps(completed), run_id),
        )
    return int(command_failed)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    stopped = [False]
    if args.command == "enums":
        from swingset.model.enums import enums_markdown

        text = enums_markdown()
        if args.write:
            Path("docs/enums.md").write_text(text)
        else:
            print(text)
        return 0
    if args.command in ("doctor", "summary"):
        try:
            result = doctor(args)
            if args.command == "summary":
                daily_summary(result)
            else:
                print(json.dumps(result, indent=2, default=str))
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=str(exc))
            return 1
    previous = signal.signal(signal.SIGTERM, lambda _signum, _frame: stopped.__setitem__(0, True))
    try:
        if args.command == "restore":
            from swingset.backup.checkpoint import restore_from_checkpoint

            remote = hub()
            if args.archive_commit:
                from tempfile import TemporaryDirectory

                from swingset.backup.huggingface import HuggingFaceArchive

                with TemporaryDirectory(prefix="swingset-restore-") as download_dir:
                    checkpoint = Path(download_dir)
                    token = os.environ.get("HF_TOKEN")
                    if not token:
                        raise ValueError("HF_TOKEN is required to download an archive checkpoint")
                    HuggingFaceArchive("skeswa/swingset-archive", token=token).download(
                        args.archive_commit, checkpoint
                    )
                    restore_from_checkpoint(
                        checkpoint,
                        args.state,
                        remote,
                        SystemClock(),
                        lock_timeout=args.lock_timeout,
                    )
            else:
                restore_from_checkpoint(
                    args.checkpoint,
                    args.state,
                    remote,
                    SystemClock(),
                    lock_timeout=args.lock_timeout,
                )
            return 0
        timeout = (
            None
            if args.command == "backup"
            else 0
            if getattr(args, "timer", False)
            else args.lock_timeout
        )
        with open_database(args.state, lock_timeout=timeout) as database:
            previous_unfinished = {
                str(row[0])
                for row in database.connection.execute(
                    "SELECT run_id FROM runs WHERE finished_at IS NULL"
                )
            }
            try:
                return _mutate(args, database, stopped)
            except Exception as error:
                _finish_failed_manual_run(database, args.command, error, previous_unfinished)
                raise
    except DatabaseLockedError as exc:
        if getattr(args, "timer", False):
            log("skipped-overlap", message=str(exc))
            return 0
        log("error", message=f"{exc}; no change was applied")
        return 1
    except (OSError, ValueError, RuntimeError) as exc:
        log("error", message=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
