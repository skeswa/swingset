"""Operator commands sharing the same lock, input capture and durable stages."""

import argparse
import getpass
import hashlib
import json
import math
import os
import signal
import sys
import time
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
        command = commands.add_parser(name, parents=[shared])
        if name in {"build", "publish"}:
            command.add_argument(
                "--correction-only",
                action="store_true",
                help="Withdraw unsafe identities from the published baseline without waiting for unrelated parsing",
            )
        if name in {"doctor", "summary"}:
            command.add_argument("--json", action="store_true")
            command.add_argument("--watch", action="store_true")
            command.add_argument("--interval", type=float, default=5.0)
            command.add_argument("--source")
            command.add_argument(
                "--source-event",
                help="Verify one source event's local page evidence; requires --source",
            )
            command.add_argument("--kind")
            command.add_argument("--requirement")
            command.add_argument("--since", type=datetime.fromisoformat)
    cycle = commands.add_parser("cycle", parents=[shared])
    mode = cycle.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--publish", dest="dry_run", action="store_false")
    cycle.add_argument("--budget", type=duration, default=720)
    cycle.add_argument("--timer", action="store_true")
    cohort = commands.add_parser("cohort", parents=[shared])
    cohort.add_argument("name")
    cohort.add_argument("--source")
    cohort.add_argument("--kind")
    cohort.add_argument("--unbounded", action="store_true")
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
        selection.add_argument("--kind")
        command.add_argument("--reason", default="operator request")
        command.add_argument("--actor", default=None)
        command.add_argument(
            "--wait",
            type=float,
            nargs="?",
            const=60.0,
            default=None,
            metavar="SECONDS",
            help="wait for matching admitted actions to settle (default 60 seconds)",
        )
        if name == "pause":
            command.add_argument("--until")
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
    if getattr(args, "source_event", None) and not getattr(args, "source", None):
        raise ValueError("--source-event requires --source")
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
            from swingset.state.requirement_report import inventory
            from swingset.state.verification import verification_summary

            now = SystemClock().now()
            from swingset.schedule.fairness import report as scheduling_report
            from swingset.state.controls import status as control_status

            result["scheduler"] = scheduling_report(conn, config, now=now)
            from swingset.fetch.archive import Archive
            from swingset.schedule.event_report import report as event_report

            result["event_inventory"] = event_report(
                conn,
                Archive(args.state),
                now=now,
                source=getattr(args, "source", None),
                source_ref=getattr(args, "source_event", None),
                operator_hold=(args.state / "operator-hold").is_file(),
                config=config,
            )
            from swingset.state.publication_report import publication_report

            result["publication"] = publication_report(conn, args.state, now)

            result["requirements"] = inventory(
                conn,
                now,
                source=getattr(args, "source", None),
                kind=getattr(args, "kind", None),
                requirement_id=getattr(args, "requirement", None),
                since=getattr(args, "since", None),
                transition_after=getattr(args, "transition_after", None),
                attempt_after=getattr(args, "attempt_after", None),
            )
            result["pending_work_basis"] = (
                "queue hints; authoritative unfinished counts are requirements.derivation.pending"
                if database.schema_version >= 14
                else "legacy offline work queue"
            )
            result["controls"] = result["requirements"].get("controls") or control_status(
                conn, now=now
            )
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='registry_verifications'"
            ).fetchone():
                result["registry_verification"] = verification_summary(conn, now)
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='admission_policies'"
            ).fetchone():
                from swingset.admission.support import admission_summary

                result["admission"] = admission_summary(conn)
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='identity_decisions'"
            ).fetchone():
                from swingset.state.identity_journal import token

                result["identity_journal"] = {
                    **asdict(token(conn)),
                    "decisions": conn.execute("SELECT COUNT(*) FROM identity_decisions").fetchone()[
                        0
                    ],
                    "pending_reference_migrations": conn.execute(
                        "SELECT COUNT(*) FROM identity_reference_migrations WHERE status!='approved' AND migration_id NOT IN (SELECT supersedes FROM identity_reference_migrations WHERE supersedes IS NOT NULL)"
                    ).fetchone()[0],
                    "resolution_states": {
                        str(row[0]): int(row[1])
                        for row in conn.execute(
                            "SELECT state,COUNT(*) FROM identity_link_resolutions GROUP BY state"
                        )
                    },
                }
            for label, query in {
                "registry_cursors": "SELECT name,value FROM cursors WHERE name LIKE 'registry_%' ORDER BY name",
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
    if (args.state / "baseline").is_symlink():
        result["last_publication"] = json.loads((args.state / "baseline/PUBLISHED").read_bytes())
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
        controls=result.get("controls"),
        scheduler=result.get("scheduler"),
        event_inventory=result.get("event_inventory"),
        watches=result.get("watches", []),
        links=result.get("link_statuses", []),
        review_queue_size=result.get("review_queue_size", 0),
        budgets=[row for row in result.get("budgets", []) if row["day"] == now.date().isoformat()],
        last_publish=result.get("last_publish"),
        publication=result.get("publication"),
        last_backup=result.get("last_backup", []),
        pending_work=result.get("pending_work", []),
        pending_work_basis=result.get("pending_work_basis"),
        restore_pending=result["restore_pending"],
        requirements=result.get("requirements"),
        registry_verification=result.get("registry_verification"),
        registry_cursors=result.get("registry_cursors"),
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
    from swingset.state.attempts import recover_interrupted
    from swingset.state.controls import recover_admissions

    with database.transaction() as conn:
        recover_admissions(conn, now=clock.now())
        recover_interrupted(database, now=clock.now())
    bundle = capture(args.config, args.overrides, args.state, versions())
    accept(database, bundle, clock)
    run_id = database.start_run(clock.now(), dry_run=command != "publish")
    from swingset.fetch.recovery import LocalCheckpointRecovery
    from swingset.state.db import SCHEMA_VERSION

    archive = Archive(
        args.state,
        recovery=LocalCheckpointRecovery(
            args.state / "checkpoints",
            maximum_schema_version=SCHEMA_VERSION,
        ),
    )
    command_failed = False
    held_units: dict[WorkUnit, list[dict[str, Any]]] = {}
    held_operations: list[dict[str, Any]] = []
    if command == "cohort":
        from swingset.state.requirements import capture_cohort

        capture_cohort(
            database.connection,
            args.name,
            clock.now(),
            source=args.source,
            kind=args.kind,
            bounded=not args.unbounded,
        )
        print(args.name)
    elif command == "sweep":
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
        from swingset.state.attempts import latest_attempt, request_retry

        with database.transaction() as conn:
            for row in conn.execute(query, values):
                reparse_unit = WorkUnit("parse", "snapshot", str(row[0]))
                enqueue(conn, (reparse_unit,), enqueued_at=clock.now().isoformat())
                prior = latest_attempt(conn, reparse_unit)
                if prior is not None and prior["outcome"] not in {"running", "succeeded"}:
                    request_retry(
                        conn, reparse_unit, now=clock.now(), reason_code="operator_reparse"
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
            if spec.source == "wsdc_registry":
                from swingset.schedule.registry import advance_sweep

                advance_sweep(database, now=clock.now())
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
        else:
            from uuid import uuid4

            from swingset.state.control_scopes import for_unit
            from swingset.state.controls import ControlPaused, operation

            try:
                with operation(
                    database,
                    action_id="crosscheck_" + uuid4().hex,
                    action_kind="registry_crosscheck",
                    scope=for_unit(database.connection, WorkUnit("project", "history", "all")),
                    clock=clock,
                    run_id=run_id,
                ):
                    if args.blob:
                        print(replay_crosscheck(database, args.blob, archive, clock.now(), run_id))
                    else:
                        print(crosscheck(database, args.dump, archive, clock.now(), run_id))
            except ControlPaused as exc:
                held_operations.append({"action": "registry_crosscheck", "pauses": exc.pauses})
                log("operation-held", **held_operations[-1])
    elif command == "discover":
        from swingset.schedule.discover import discover

        print(discover(database, bundle, clock.now()))
    elif command in ("parse", "project", "link"):
        from collections import Counter

        from swingset.schedule.derive import derive_one
        from swingset.state.control_scopes import for_unit
        from swingset.state.controls import matching_pauses

        attempts: Counter[str] = Counter()
        failures: Counter[str] = Counter()
        attempted: set[WorkUnit] = set()

        def allowed(unit: WorkUnit) -> bool:
            pauses = matching_pauses(
                database.connection, for_unit(database.connection, unit), now=clock.now()
            )
            if pauses:
                held_units[unit] = pauses
            else:
                held_units.pop(unit, None)
            return not pauses

        while (
            not stopped[0]
            and (
                unit := next_work(
                    database.connection,
                    command,
                    now=clock.now(),
                    exclude=attempted,
                    allowed=allowed,
                )
            )
            is not None
        ):
            attempted.add(unit)
            derived = derive_one(database, archive, unit, bundle, clock, run_id)
            if derived.source is not None:
                attempts[derived.source] += 1
                failures[derived.source] += int(derived.failed)
            elif derived.failed:
                command_failed = True
            if derived.reason == "operator_pause":
                allowed(unit)
            if derived.source == "wsdc_registry" and not derived.failed:
                from swingset.schedule.registry import advance_sweep

                with database.transaction():
                    advance_sweep(database, now=clock.now())
        if held_units:
            log(
                "derivation-held",
                command=command,
                units=[
                    {**asdict(unit), "pauses": pauses}
                    for unit, pauses in sorted(held_units.items())
                ],
            )
        if (
            command == "project"
            and not stopped[0]
            and next_work(database.connection, "parse") is None
            and next_work(database.connection, "project") is None
        ):
            from uuid import uuid4

            from swingset.schedule.registry import run_saved_crosscheck_if_due
            from swingset.state.controls import ControlPaused, operation

            if database.connection.execute(
                "SELECT 1 FROM meta WHERE key='registry_crosscheck_due'"
            ).fetchone():
                try:
                    with operation(
                        database,
                        action_id="crosscheck_" + uuid4().hex,
                        action_kind="registry_crosscheck",
                        scope=for_unit(database.connection, WorkUnit("project", "history", "all")),
                        clock=clock,
                        run_id=run_id,
                    ):
                        run_saved_crosscheck_if_due(database, archive, clock.now(), run_id)
                except ControlPaused as exc:
                    held_operations.append({"action": "registry_crosscheck", "pauses": exc.pauses})
                    log("operation-held", **held_operations[-1])
        command_failed = command_failed or any(
            failures[source] / count > 0.1 for source, count in attempts.items()
        )
    elif command in ("build", "publish"):
        from swingset.publish.service import publish, reconcile

        remote = hub() if command == "publish" else None
        if remote:
            reconcile(args.state, remote, dry_run=False)
        from swingset.state.controls import ControlPaused

        try:
            candidate = build(
                database, bundle, clock, run_id, remote=remote, correction_only=args.correction_only
            )
        except ControlPaused as exc:
            receipt = {
                "state": "held",
                "action": "correction_build" if args.correction_only else "build",
                "reason": "operator_pause",
                "pauses": exc.pauses,
            }
            held_operations.append(receipt)
            print(json.dumps(receipt, indent=2, default=str))
        else:
            if remote:
                print(
                    json.dumps(
                        asdict(publish(args.state, candidate.path, remote)), indent=2, default=str
                    )
                )
            else:
                print(candidate.path)
    elif command == "backup":
        from swingset.backup.checkpoint import create_checkpoint

        destination = args.destination or args.state / "checkpoints" / run_id
        checkpoint = create_checkpoint(
            args.state,
            database.connection,
            destination,
            schema_version=database.schema_version,
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
        "held_units": [
            {**asdict(unit), "pauses": pauses} for unit, pauses in sorted(held_units.items())
        ],
        "held_operations": held_operations,
    }
    durable_write(args.state / "runs" / f"{run_id}.json", canonical(completed))
    with database.transaction() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
            (completed["finished_at"], json.dumps(completed), run_id),
        )
    return int(command_failed)


def _control(args: argparse.Namespace) -> int:
    """Commit controls without the long-lived pipeline lock or input acceptance."""
    from swingset.state.controls import Selector, change_control, status

    if not math.isfinite(args.lock_timeout) or args.lock_timeout < 0:
        raise ValueError("--lock-timeout must be a finite nonnegative number")
    if args.wait is not None and (not math.isfinite(args.wait) or args.wait < 0):
        raise ValueError("--wait must be a finite nonnegative number of seconds")
    actor = getpass.getuser() if args.actor is None else args.actor
    if not actor.strip() or not args.reason.strip():
        raise ValueError("--actor and --reason must not be blank")
    selector = (
        Selector("all", "all")
        if args.all
        else Selector("host", args.host)
        if args.host is not None
        else Selector("source", args.source)
        if args.source is not None
        else Selector("kind", args.kind)
    )
    clock = SystemClock()
    until = datetime.fromisoformat(args.until) if getattr(args, "until", None) else None
    if until is not None and (until.tzinfo is None or until <= clock.now()):
        raise ValueError("--until requires a future time with an explicit timezone")
    config = load_config(args.config)
    result = change_control(
        args.state,
        selector=selector,
        paused=args.command == "pause",
        actor=actor,
        reason=args.reason,
        now=clock.now(),
        until=until,
        timeout=args.lock_timeout,
        sources=tuple(config.sources),
        hosts=tuple(config.hosts),
    )
    code = 0
    if args.wait is not None:
        deadline = time.monotonic() + args.wait
        try:
            while True:
                with open_database(args.state, lock=False, read_only=True) as database:
                    with database.transaction(immediate=False) as conn:
                        current = status(conn, now=clock.now(), selector=selector)
                result.update(current)
                result["status"] = current
                if not current["draining_attempts"]:
                    result["wait_completed"] = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result["wait_completed"] = False
                    result["wait_timed_out"] = True
                    code = 1
                    break
                time.sleep(min(0.1, remaining))
        except KeyboardInterrupt:
            result["wait_completed"] = False
            result["wait_interrupted"] = True
            code = 130
    print(json.dumps(result, indent=2, default=str), flush=True)
    return code


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    stopped = [False]
    if args.command in {"pause", "resume"}:
        try:
            return _control(args)
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=str(exc))
            return 1
    if args.command == "enums":
        from swingset.model.enums import enums_markdown

        text = enums_markdown()
        if args.write:
            Path("docs/reference/enums.md").write_text(text)
        else:
            print(text)
        return 0
    if args.command in ("doctor", "summary"):
        try:
            if args.interval <= 0 or args.interval > 60:
                raise ValueError("--interval must be greater than zero and at most 60 seconds")
            if args.since is not None and args.since.tzinfo is None:
                raise ValueError("--since requires an explicit timezone")
            report_key = hashlib.sha256(
                json.dumps((args.source, args.kind, args.requirement)).encode()
            ).hexdigest()[:16]
            report_checkpoint = args.state / "reports" / f"requirements-{report_key}.json"
            if args.command == "summary" and args.since is None and report_checkpoint.exists():
                checkpoint = json.loads(report_checkpoint.read_text())
                args.since = datetime.fromisoformat(checkpoint["at"])
                args.transition_after = checkpoint.get("transition_cursor")
                args.attempt_after = checkpoint.get("attempt_cursor")
            while True:
                result = doctor(args)
                if args.command == "summary" and not args.json:
                    daily_summary(result)
                elif args.json:
                    print(json.dumps(result, indent=2, default=str), flush=True)
                else:
                    from swingset.state.requirement_report import human_report

                    print(
                        json.dumps(
                            {key: value for key, value in result.items() if key != "requirements"},
                            indent=2,
                            default=str,
                        )
                    )
                    if "requirements" in result:
                        print(human_report(result["requirements"]), flush=True)
                if args.command == "summary" and "requirements" in result:
                    from swingset.fetch.archive import canonical, durable_write

                    at = result["requirements"]["snapshot_at"]
                    args.transition_after = result["requirements"].get("transition_cursor")
                    args.attempt_after = result["requirements"].get("attempt_cursor")
                    durable_write(
                        report_checkpoint,
                        canonical(
                            {
                                "at": at,
                                "transition_cursor": args.transition_after,
                                "attempt_cursor": args.attempt_after,
                            }
                        ),
                    )
                    args.since = datetime.fromisoformat(at)
                if not args.watch:
                    break
                time.sleep(args.interval)
            return 0
        except KeyboardInterrupt:
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
