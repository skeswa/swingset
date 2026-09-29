"""Operator commands sharing the same lock, input capture and durable stages."""

import argparse
import contextlib
import getpass
import hashlib
import json
import math
import os
import signal
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swingset import __version__
from swingset.clock import SystemClock
from swingset.config import RetentionConfig, duration, load_config
from swingset.log import log
from swingset.publish.service import Hub
from swingset.state.db import Database, DatabaseLockedError, open_database

if TYPE_CHECKING:
    from swingset.backup.pruning import PrunePolicy


#: Loop commands built so far and their default budgets in seconds.
LOOP_BUDGETS = {
    "acquire": 240.0,
    "plan": 600.0,
    "derive": 240.0,
    "release": 1800.0,
    "maintain": 1200.0,
}


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
            command.add_argument(
                "--retention",
                action="store_true",
                help="Also walk the retention plan (gc --plan's walk; at production "
                "size it needs more memory than the 8 GB worker has)",
            )
    collect = commands.add_parser("gc", parents=[shared])
    # Bare `gc` summarises the plan and removes nothing. Nothing removes
    # anything without a plan digest and both locks.
    step = collect.add_mutually_exclusive_group()
    step.add_argument(
        "--plan",
        action="store_true",
        help="Write the retention plan under state/gc/plans and remove nothing",
    )
    step.add_argument(
        "--apply",
        metavar="PLAN_DIGEST",
        help="Remove what that written plan named, under the writer and control locks",
    )
    step.add_argument(
        "--reclaim",
        action="store_true",
        help="Rewrite state.sqlite in place so freed pages leave the file",
    )
    step.add_argument(
        "--closures",
        action="store_true",
        help="Say which release-closure proofs nothing names; remove nothing (D-0200)",
    )
    step.add_argument(
        "--collect-closures",
        metavar="PLAN_DIGEST",
        help="Remove the unnamed release-closure proofs that plan listed (D-0200)",
    )
    collect.add_argument(
        "--candidates-only",
        action="store_true",
        help="With --plan or --apply: candidate directories only, walking no graph, "
        "so it runs on the worker",
    )
    accept_year = commands.add_parser("accept-year", parents=[shared])
    accept_year.add_argument("--year", type=int, required=True)
    accept_year.add_argument("--by", required=True, help="who accepts: the reviewer and session")
    hold = commands.add_parser("hold", parents=[shared])
    hold.add_argument("action", choices=("add", "list", "remove"))
    hold.add_argument("--generation", action="append", default=[], metavar="ID", help="repeatable")
    hold.add_argument("--artifact", action="append", default=[], metavar="SHA256")
    hold.add_argument("--who")
    hold.add_argument("--why")
    hold.add_argument("--hold-id")
    cycle = commands.add_parser("cycle", parents=[shared])
    mode = cycle.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--publish", dest="dry_run", action="store_false")
    cycle.add_argument("--budget", type=duration, default=720)
    cycle.add_argument("--timer", action="store_true")
    # One tick of a loop (D-0215); budgets are the timer units' defaults.
    for name, budget in LOOP_BUDGETS.items():
        loop = commands.add_parser(name, parents=[shared], help=f"Run one {name} loop tick")
        loop.add_argument("--budget", type=duration, default=budget)
        loop.add_argument("--timer", action="store_true")
        if name == "release":
            publishing = loop.add_mutually_exclusive_group()
            publishing.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
            publishing.add_argument("--publish", dest="dry_run", action="store_false")
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
    work_queue = commands.add_parser(
        "queue", parents=[shared], help="Show the work queue, or requeue waiting units"
    )
    work_queue.add_argument("action", nargs="?", choices=("requeue",))
    work_queue.add_argument(
        "--queue-state",
        choices=("ready", "leased", "waiting_input", "quarantined"),
        help="list the units in this state (default: quarantined)",
    )
    work_queue.add_argument("--json", action="store_true")
    requeued = work_queue.add_mutually_exclusive_group()
    requeued.add_argument(
        "--error-class", help="requeue: every unit whose last failure is this class"
    )
    requeued.add_argument("--unit", metavar="STAGE/KIND/ID", help="requeue: one unit")
    work_queue.add_argument("--stage", choices=("parse", "project", "link"))
    work_queue.add_argument("--kind")
    work_queue.add_argument("--by", help="requeue: who asks, the operator and session")
    partitions = commands.add_parser(
        "partitions",
        parents=[shared],
        help="Build, list or show partition artifacts, stage 1 of a partitioned release",
    )
    partitions.add_argument("action", nargs="?", choices=("build", "list", "show"))
    partitions.add_argument("partition", nargs="?", help="show: the partition name")
    partitions.add_argument(
        "--only", metavar="PARTITION", help="build: one partition, or event/<id> by member"
    )
    partitions.add_argument("--partition-state", choices=("built", "withheld"))
    partitions.add_argument(
        "--withheld",
        action="store_true",
        help="with no action: the withheld partitions, counted by reason, with their detail",
    )
    commands.add_parser(
        "migrate",
        parents=[shared],
        help="Open the database once, applying pending migrations; print both schemas and seconds",
    )
    verify = commands.add_parser(
        "verify-derivations",
        parents=[shared],
        help="Re-derive this week's sample of current units, roll it back and compare output",
    )
    verify.add_argument(
        "--sample", type=float, default=0.01, help="share of each kind's units (at least one)"
    )
    verify.add_argument("--kinds", default="link,year", help="comma-separated: link, year")
    enums = commands.add_parser("enums")
    enums.add_argument("--write", action="store_true")
    backup = commands.add_parser("backup", parents=[shared])
    backup.add_argument("--local", action="store_true")
    backup.add_argument("--destination", type=Path)
    backup.add_argument(
        "--no-prune",
        action="store_true",
        help="Keep every local checkpoint instead of pruning timer checkpoints by policy",
    )
    backup.add_argument(
        "--remove-checkpoint",
        metavar="NAME",
        help="Remove one operator-named checkpoint by name and make no backup",
    )
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


def _checkpoint_policy(retention: RetentionConfig) -> "PrunePolicy":
    """The checkpoint knobs of the `[retention]` table, as the pruner wants them."""
    from swingset.backup.pruning import PrunePolicy

    return PrunePolicy(
        keep_recent=retention.checkpoint_keep_recent,
        max_age_seconds=retention.checkpoint_max_age,
        incomplete_max_age_seconds=retention.checkpoint_incomplete_max_age,
    )


def _prune_checkpoints(state: Path, created: Path, retention: RetentionConfig) -> None:
    """Remove the local checkpoints policy no longer needs, never the new one.

    `state/checkpoints` is outside the checkpoint file closure and outside the
    retention walk, so a pruned directory is never a root and never a file a
    plan must keep. One plan is worked out, read once to refuse the one case
    that could remove this run's own checkpoint -- a `--destination` reusing a
    timer name older than the ones already there -- and then applied. Working
    it out twice would size every removable full state copy twice.

    Nothing here checks `RESTORE_PENDING`: `open_database` refuses to open a
    state directory with that marker, so a backup, and with it this prune,
    cannot run while a restore into this directory is pending. A restore into
    a *different* state directory that reads a checkpoint from this one leaves
    no mark here; rename that checkpoint before starting such a restore, since
    policy never removes an operator-named one.

    This is cleanup after a backup that already succeeded, so nothing here can
    fail the backup. A failure is logged and the command still reports what it
    did; the alternative is a unit that restarts on failure and writes a fresh
    full copy every minute while one old directory stays unremovable.
    """
    from swingset.backup.pruning import PruneError, plan_prune, remove_planned

    checkpoints = state / "checkpoints"
    try:
        policy = _checkpoint_policy(retention)
        fresh = created.resolve()
        plan = plan_prune(checkpoints, now=time.time(), policy=policy)
        if any(entry.path.resolve() == fresh for entry in plan.remove):
            log(
                "checkpoint-prune-skipped",
                checkpoint=str(created),
                reason="policy named this run's own checkpoint",
            )
            return
        plan = remove_planned(checkpoints, plan)
    except PruneError as exc:
        log(
            "checkpoint-prune-failed",
            message=str(exc),
            removed=[entry.path.name for entry in exc.removed],
        )
        return
    except (OSError, ValueError, RuntimeError) as exc:
        log("checkpoint-prune-failed", message=f"{type(exc).__name__}: {exc}", removed=[])
        return
    log(
        "checkpoints-pruned",
        removed=[
            {"name": entry.path.name, "reason": entry.reason, "bytes": entry.bytes}
            for entry in plan.remove
        ],
        kept=[{"name": entry.path.name, "reason": entry.reason} for entry in plan.keep],
        reclaimed_bytes=plan.reclaimable_bytes,
    )


#: A local checkpoint that fails verification is renamed with this prefix. The
#: name is outside the timer's namespace, so pruning, `[backup-stale]` and an
#: operator never count it as the newest good copy, and retention policy never
#: removes it; nix/watchdog.py reports it as `[backup-failed]` until it is
#: removed. Only the newest failed copy is kept (D-0203).
FAILED_CHECKPOINT_PREFIX = "failed-"


def _verify_local_checkpoint(created: Path) -> str | None:
    """Why the new checkpoint is not safe to keep alone, or None.

    A local backup has no upload to prove its copy, and pruning after it can
    leave that copy as the only one (D-0203). So the whole copy is read back
    as a restore would read it: closure, every file hash, the schema, foreign
    keys and the artifact closure, with `PRAGMA quick_check` in place of the
    slower `integrity_check` that restore still runs. Any exception is a
    verdict, not a crash: a malformed manifest can raise TypeError, and the
    copy must still leave the timer namespace.
    """
    from swingset.backup.checkpoint import verify_checkpoint
    from swingset.state.db import SCHEMA_VERSION

    started = time.monotonic()
    problem: str | None = None
    try:
        verify_checkpoint(created, maximum_schema_version=SCHEMA_VERSION, quick=True)
    except Exception as exc:
        problem = f"{type(exc).__name__}: {exc}"
    log(
        "checkpoint-verify",
        checkpoint=str(created),
        ok=problem is None,
        seconds=round(time.monotonic() - started, 3),
    )
    return problem


@dataclass(frozen=True)
class ReadBack:
    """A local checkpoint `_mutate` wrote, to verify once the writer lock is released."""

    checkpoint: Path
    retention: RetentionConfig
    run_id: str


def _verify_then_prune(state: Path, read_back: ReadBack) -> None:
    """Verify a local checkpoint, then prune older ones under the writer lock.

    The read-back runs after the backup released `state.lock`: the copy is an
    independent file, so cycles need not wait the minutes it takes. The prune
    takes the lock again, since a cycle may be reading an older checkpoint to
    recover a file. A copy that fails is renamed out of the timer namespace
    first, so nothing after it can leave it under a timer name. Then, only in
    `state/checkpoints`, any older failed copy is removed so a repeated
    failure cannot fill the disk a full copy a day; a removal that fails is
    logged, not raised. The run's record is rewritten as failed and the
    backup fails; no good checkpoint is removed. The path is printed only
    once the copy has passed.
    """
    import shutil

    from swingset.backup.pruning import TIMER_NAME
    from swingset.build.files import fsync_dir

    created = read_back.checkpoint
    problem = _verify_local_checkpoint(created)
    if problem is None:
        print(created)
        with open_database(state, lock_timeout=None):
            _prune_checkpoints(state, created, read_back.retention)
        return
    failed = created.with_name(FAILED_CHECKPOINT_PREFIX + created.name)
    os.replace(created, failed)
    fsync_dir(failed.parent)
    removed: list[str] = []
    # Only the timer's directory: a --destination elsewhere is the operator's.
    if failed.parent.resolve() == (state / "checkpoints").resolve():
        # Only a name this code gives: an operator's directory never matches.
        older = sorted(
            path
            for path in failed.parent.iterdir()
            if path.name != failed.name
            and path.name.startswith(FAILED_CHECKPOINT_PREFIX)
            and TIMER_NAME.match(path.name.removeprefix(FAILED_CHECKPOINT_PREFIX))
            and path.is_dir()
            and not path.is_symlink()
        )
        for path in older:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                log(
                    "failed-checkpoint-removal-failed",
                    checkpoint=str(path),
                    message=f"{type(exc).__name__}: {exc}",
                )
            else:
                removed.append(path.name)
    log(
        "checkpoint-prune-skipped",
        checkpoint=str(failed),
        reason="verification failed",
        detail=problem,
        removed_failed=removed,
    )
    error = (
        f"checkpoint {created.name} failed verification and was renamed {failed.name}: {problem}"
    )
    # The run record was written as a success before the read-back; make it
    # agree with the exit code.
    with open_database(state, lock_timeout=None) as database:
        _write_failed_run(database, read_back.run_id, "backup", error)
    raise RuntimeError(error)


HOLD_OVERDUE_SECONDS = 48 * 3600


def describe_hold(path: Path, *, now: float) -> dict[str, Any]:
    """The maintenance hold: whether it exists, how old it is, and what it says.

    The marker holds ``who``, ``why`` and ``review-by`` lines. A hold is a
    runtime interlock for one named review, so one older than 48 hours is
    reported as overdue (D-0176).
    """
    if not path.is_file():
        return {"present": False}
    text = path.read_text(errors="replace").strip()
    age = max(0.0, now - path.stat().st_mtime)
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in {"who", "why", "review-by"}:
            fields[key.strip()] = value.strip()
    return {
        "present": True,
        "age_hours": round(age / 3600, 1),
        "overdue": age > HOLD_OVERDUE_SECONDS,
        "text": text,
        **fields,
    }


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
    from swingset.backup.pruning import describe as describe_checkpoints

    # The pruner in report mode: every local checkpoint, its size, and whether
    # the next backup would prune it. Nothing is removed here, so a stale
    # operator-named checkpoint is visible before anyone has to go looking.
    result["checkpoints"] = [
        asdict(entry)
        for entry in describe_checkpoints(
            args.state / "checkpoints",
            now=time.time(),
            policy=_checkpoint_policy(config.retention),
        )
    ]
    result["operator_hold"] = describe_hold(args.state / "operator-hold", now=time.time())
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
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='derivation_payload_removal_authority'"
            ).fetchone():
                # Anything here means a writer left the derivation payload delete
                # gate open. A grant cannot commit with foreign keys on, so the
                # list is empty unless something wrote one with them off.
                result["derivation_payload_removal_grants"] = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT reason,granted_at FROM derivation_payload_removal_authority"
                    )
                ]
            if not getattr(args, "retention", False):
                from swingset.state.retention import usage

                # The plan walk holds the whole graph: 13 GB and 13 minutes on
                # the 35 GB copy, past the 8 GB worker. Doctor and the daily
                # summary report the file's bytes; `--retention` walks.
                result["retention"] = {
                    "usage": usage(conn, args.state),
                    "over_size_cap": (args.state / "state.sqlite").stat().st_size
                    > config.retention.max_database_bytes,
                    "walk": "skipped; pass --retention (gc --plan's walk)",
                }
            elif conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='derivation_generations'"
            ).fetchone():
                from swingset.state.retention import report as retention_report

                # The planner in report mode: the same walk, nothing written.
                # The walk needs derivation history and nothing newer: it runs
                # before interning too, and says so with `payloads_interned`.
                result["retention"] = retention_report(
                    conn,
                    args.state,
                    max_database_bytes=config.retention.max_database_bytes,
                    recent_window=config.retention.recent_window,
                    collect_older_than=config.retention.collect_older_than,
                    detail_limit=200 if getattr(args, "json", False) else 20,
                )
            from swingset.state import guards

            # Why fetches are held, by reason, and the rows still seen after
            # their expiry (D-0219).
            result["guards"] = guards.report(conn, now=now)
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
        checkpoints=result.get("checkpoints", []),
        pending_work=result.get("pending_work", []),
        pending_work_basis=result.get("pending_work_basis"),
        retention=result.get("retention"),
        restore_pending=result["restore_pending"],
        requirements=result.get("requirements"),
        registry_verification=result.get("registry_verification"),
        registry_cursors=result.get("registry_cursors"),
        derivation_payload_removal_grants=result.get("derivation_payload_removal_grants", []),
        guards=result.get("guards"),
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
    _write_failed_run(database, str(row[0]), command, f"{type(error).__name__}: {error}")


def _write_failed_run(database: Database, run_id: str, command: str, error: str) -> None:
    """Record a run as failed with `error`, keeping any summary it already has."""
    row = database.connection.execute(
        "SELECT summary_json FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    completed: dict[str, Any] = (
        json.loads(row[0])
        if row is not None and row[0]
        else {"run_id": run_id, "command": command, "stopped": False}
    )
    finished_at = SystemClock().now().isoformat()
    completed.update(finished_at=finished_at, failed=True, error=error)
    from swingset.fetch.archive import canonical, durable_write

    durable_write(database.state_dir / "runs" / f"{run_id}.json", canonical(completed))
    with database.transaction() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
            (finished_at, json.dumps(completed, sort_keys=True), run_id),
        )


def _loop(args: argparse.Namespace, stopped: list[bool]) -> int:
    """One loop tick; exit 1 when it failed. A busy lock escapes as a skip."""
    from swingset.loops import acquire, derive, maintain, plan, release
    from swingset.loops.runtime import Tick, tick

    body = {
        "acquire": acquire.run,
        "plan": plan.run,
        "derive": derive.run,
        "release": release.run,
        "maintain": maintain.run,
    }[args.command]
    # Only release publishes, and only with --publish.
    dry_run = getattr(args, "dry_run", True)
    current: Tick
    with tick(
        args.command,
        state=args.state,
        config_dir=args.config,
        overrides_dir=args.overrides,
        clock=SystemClock(),
        budget=args.budget,
        should_stop=lambda: stopped[0],
        timer=args.timer,
        hub=None if dry_run else hub(),
        dry_run=dry_run,
    ) as current:
        from swingset.state.queue import stop_marker

        marker = stop_marker(args.state, current.run_id)

        def requested(_signum: int, _frame: object) -> None:
            # A stop request is noted on disk, so a kill after the stop
            # timeout is not counted as the running unit's death.
            stopped[0] = True
            with contextlib.suppress(OSError):
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_bytes(b"")

        previous = signal.signal(signal.SIGTERM, requested)
        try:
            if stopped[0]:
                requested(signal.SIGTERM, None)
            body(current)
        finally:
            signal.signal(signal.SIGTERM, previous)
    return int(bool(current.record and current.record["failed"]))


def _mutate(
    args: argparse.Namespace,
    database: Database,
    stopped: list[bool],
    read_back: list[ReadBack],
) -> int:
    """Run one locked command; a local backup appends its deferred read-back."""
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
    from swingset.state import queue
    from swingset.state.attempts import recover_interrupted
    from swingset.state.controls import recover_admissions

    with database.transaction() as conn:
        recover_admissions(conn, now=clock.now())
        recover_interrupted(database, now=clock.now())
        # This process holds the exclusive lock: every lease is dead (D-0216).
        queue.release_all(conn, clock.now())
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
                    scope=for_unit(database.connection, WorkUnit("project", "inventory", "all")),
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

        with queue.owning("cli", run_id, exclusive=True):
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
                if derived.reason in {"control_timeout", "lease_busy"}:
                    log("unit-skipped", unit=asdict(unit), reason=derived.reason)
                    continue
                if derived.source is not None and not derived.waiting_repeat:
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
                        scope=for_unit(
                            database.connection, WorkUnit("project", "inventory", "all")
                        ),
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
            # A copy that will be read back is printed once it passes
            # (_verify_then_prune); --no-prune skips that read-back.
            if args.no_prune:
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
        # Only a checkpoint that was written, and uploaded when it had to be,
        # lets older ones go. A failure above raises before this line.
        if not args.no_prune:
            if args.local:
                # A local copy is read back before older ones may go, so a bad
                # copy never replaces the last good one. That runs after the
                # writer lock is released; see _verify_then_prune. An upload is
                # its own check.
                read_back.append(ReadBack(checkpoint.path, bundle.config.retention, run_id))
            else:
                _prune_checkpoints(args.state, checkpoint.path, bundle.config.retention)
    elif command == "gc":
        from swingset.state.retention import enforce_size_cap, plan, write_plan
        from swingset.state.retention_apply import apply_plan, reclaim

        policy = bundle.config.retention
        if args.candidates_only and not (args.plan or args.apply):
            raise ValueError("--candidates-only goes with --plan or --apply")
        if args.candidates_only and args.plan:
            from swingset.state.retention import candidate_plan, equivalence_totals
            from swingset.state.retention_apply import eligibility

            pruning = candidate_plan(
                database.connection, args.state, collect_older_than=policy.collect_older_than
            )
            print(
                json.dumps(
                    {
                        "digest": pruning.digest,
                        "totals": pruning.content["totals"],
                        # What an apply now would remove; the rest wait out
                        # their age floor.
                        "eligibility": eligibility(pruning, now=clock.now()),
                        # Counted, not walked, so the worker can report
                        # D-0208's trigger; not part of the plan's digest.
                        "equivalences": equivalence_totals(database.connection),
                        "plan": str(write_plan(args.state, pruning)),
                        "next": [
                            f"gc --apply {pruning.digest} --candidates-only removes the "
                            "removable candidates that plan names, under both locks"
                        ],
                    },
                    indent=2,
                )
            )
        elif args.closures or args.collect_closures:
            from swingset.state import closure_collection

            closures: dict[str, Any]
            if args.collect_closures:
                closures = closure_collection.collect(
                    database,
                    plan_digest=args.collect_closures,
                    now=clock.now(),
                    timeout=args.lock_timeout,
                )
            else:
                proof_plan = closure_collection.plan(database.connection, args.state)
                closures = {
                    key: proof_plan[key]
                    for key in ("digest", "proofs", "proof_bytes", "kept_bytes")
                } | {
                    "collectable": len(proof_plan["collectable"]),
                    "collectable_bytes": proof_plan["collectable_bytes"],
                    "kept": proof_plan["kept"],
                    "next": f"gc --collect-closures {proof_plan['digest']}",
                }
            print(json.dumps(closures, indent=2))
        elif args.reclaim:
            print(
                json.dumps(reclaim(database, now=clock.now(), timeout=args.lock_timeout), indent=2)
            )
        elif args.apply:
            applied = apply_plan(
                database,
                plan_digest=args.apply,
                max_database_bytes=policy.max_database_bytes,
                recent_window=policy.recent_window,
                collect_older_than=policy.collect_older_than,
                now=clock.now(),
                timeout=args.lock_timeout,
                candidates_only=args.candidates_only,
            )
            print(json.dumps(applied, indent=2))
            waiting = applied.get("eligibility", {})
            if waiting.get("not_yet_eligible") and not waiting.get("eligible_now"):
                # Exit 0 with empty lists reads as success; say why nothing went.
                print(
                    f"gc --apply: the plan names {waiting['not_yet_eligible']} removable files"
                    " but none has passed its age floor yet; the earliest can go after"
                    f" {waiting['earliest_eligible_after']}. A copied or restored state"
                    " directory resets the floor.",
                    file=sys.stderr,
                )
        else:
            # Planning is also when the cap is noticed: an operator asking what
            # could go should be told the pipeline is already over its limit.
            cap = enforce_size_cap(
                args.state,
                max_database_bytes=policy.max_database_bytes,
                now=clock.now(),
                timeout=args.lock_timeout,
            )
            retention_plan = plan(
                database.connection,
                args.state,
                max_database_bytes=policy.max_database_bytes,
                recent_window=policy.recent_window,
                collect_older_than=policy.collect_older_than,
            )
            summary: dict[str, Any] = {
                "digest": retention_plan.digest,
                "totals": retention_plan.content["totals"],
                "size_cap": cap,
            }
            if args.plan:
                # Only a written plan names an apply. The operator has a file to
                # read before anything goes, and the digest to apply is printed
                # next to where that file landed.
                summary["plan"] = str(write_plan(args.state, retention_plan))
                summary["next"] = [
                    f"gc --apply {retention_plan.digest} removes what that plan names, "
                    "under both locks",
                    "gc --reclaim rewrites the file so freed pages leave it",
                ]
            else:
                # The bare command removes nothing and hands out no digest to
                # apply. It says what a plan would say and points at gc --plan.
                summary["next"] = [
                    "gc --plan writes this plan under state/gc/plans and says how to apply it",
                    "gc --reclaim rewrites the file so freed pages leave it",
                ]
            print(json.dumps(summary, indent=2))
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


def _accept_year(args: argparse.Namespace) -> int:
    """Record a reviewed year's event list as accepted, then store its year in chunks.

    The projector refuses while the year has open history findings or the
    inventory projection is pending, so acceptance cannot outrun the evidence
    (D-0180). An inventory the configured pacing gate holds does not count as
    pending: acceptance then records the committed event list, as the
    phase-two gate reads it (D-0210). The cutoff switch applies (D-0205).

    The acceptance and the year's live rows commit in one bounded write (step
    A); the generation of `project/year/<year>` (that year's association,
    tiers and coverage, plan F17) then stores in chunks and its pointer flips
    in one short write, as `derive_one` stores it (WP4, D-0217). If that
    stops, the acceptance stands and the derive loop resumes the building
    generation. No other year is touched.
    """
    from swingset import cutoff
    from swingset.project.history import accept_year
    from swingset.schedule import pacing
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    if not (args.by or "").strip():
        raise ValueError("accept-year requires --by")
    config = load_config(args.config)
    clock = SystemClock()
    now = clock.now()
    unit = WorkUnit("project", "year", str(args.year))
    with open_database(args.state, lock_timeout=args.lock_timeout) as database:
        with (
            pacing.configured(config.scheduler, clock.now),
            cutoff.configured(config.derivation.cutoff),
            derivations.chunking(database.connection, unit) as chunk,
        ):
            with database.transaction() as conn:
                accept_year(
                    conn,
                    args.year,
                    accepted_by=args.by,
                    accepted_at=now.isoformat(),
                    history_start=config.history_start,
                )
                row = conn.execute(
                    "SELECT year,accepted_at,accepted_by,inventory_digest FROM history_acceptance WHERE year=?",
                    (args.year,),
                ).fetchone()
            if chunk.deferred is not None:

                def finished(write: sqlite3.Connection) -> None:
                    write.execute(
                        "DELETE FROM pending_work WHERE stage=? AND unit_kind=? AND unit_id=?",
                        (unit.stage, unit.unit_kind, unit.unit_id),
                    )

                derivations.finish_building(database, chunk.deferred, commit=finished)
                derivations.collect_orphans(database, unit, keep=None)
    print(
        json.dumps(
            dict(zip(("year", "accepted_at", "accepted_by", "inventory_digest"), row, strict=True))
        )
    )
    return 0


def _migrate_only(args: argparse.Namespace) -> int:
    """Open the database once, as a switch's hold does, and time the migration.

    Run as the service user, so the WAL and shared-memory files it creates
    belong to that user. Prints the schema before and after and the seconds
    the open took (the preflight receipt's migration time).
    """
    path = args.state / "state.sqlite" if args.state.is_dir() else args.state
    before = None
    if path.exists():
        with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            before = int(conn.execute("PRAGMA user_version").fetchone()[0])
    started = time.monotonic()
    with open_database(args.state, lock_timeout=args.lock_timeout) as database:
        after = database.schema_version
    seconds = round(time.monotonic() - started, 3)
    print(json.dumps({"schema_before": before, "schema_after": after, "seconds": seconds}))
    return 0


def _partitions(args: argparse.Namespace) -> int:
    """Build stage 1 of a partitioned release, or read its partition rows (phase 3, WP6).

    `build` needs the writer lock and prints counts, bytes, seconds and peak
    memory; each release build also runs it first (D-0224). `list` and
    `show` read only.
    """
    from swingset.build import partitions

    if args.withheld or args.action is None:
        if args.action is not None or not args.withheld:
            raise ValueError("partitions takes an action (build, list, show) or --withheld")
        with open_database(args.state, lock=False, read_only=True) as database:
            report = withheld_report(database.connection)
        print(json.dumps(report, indent=2, default=str))
        return 0
    if args.action in {"list", "show"}:
        with open_database(args.state, lock=False, read_only=True) as database:
            if args.action == "list":
                result: object = partitions.listing(database, state=args.partition_state)
            else:
                if not args.partition:
                    raise ValueError("partitions show needs a partition name")
                result = partitions.show(database, args.partition)
        print(json.dumps(result, indent=2, default=str))
        return 0
    config = load_config(args.config)
    clock = SystemClock()
    with open_database(args.state, lock_timeout=args.lock_timeout) as database:
        link = database.state_dir / "baseline"
        report = partitions.build(
            database,
            history_start=config.history_start,
            baseline=link.resolve() if link.is_symlink() else None,
            run_id=f"partitions_{uuid.uuid4().hex[:16]}",
            now=clock.now(),
            only=args.only,
        )
    print(json.dumps(report, indent=2, default=str))
    return 0


def withheld_report(conn: sqlite3.Connection) -> dict[str, object]:
    """`partitions --withheld`: the count, the count by reason, and each partition.

    The watchdog's daily `[partitions-withheld]` line counts the same rows.
    """
    from swingset.state import release_store

    rows = release_store.withheld(conn)
    reasons: dict[str, int] = {}
    for row in rows:
        reasons[str(row.reason)] = reasons.get(str(row.reason), 0) + 1
    return {
        "withheld": len(rows),
        "by_reason": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        "partitions": [
            {
                "partition": row.partition,
                "reason": row.reason,
                "detail": json.loads(row.detail_json) if row.detail_json else None,
                "built_at": row.built_at,
            }
            for row in rows
        ],
    }


def _verify_derivations(args: argparse.Namespace) -> int:
    """Verify this week's sample of current link and year units (plan section 10).

    Each unit's write runs and rolls back under the writer lock; nothing is
    committed. Prints the summary the maintain loop records under `verify`,
    and appends a `[read-set-miss]` line to ALERTS.log for each mismatch.
    Exits 1 when a unit mismatched.
    """
    from swingset.state import verify
    from swingset.state.inputs import capture
    from swingset.versions import versions

    kinds = verify.parse_kinds(args.kinds)
    if not 0 < args.sample <= 1:
        raise ValueError("--sample must be greater than 0 and at most 1")
    clock = SystemClock()
    bundle = capture(args.config, args.overrides, args.state, versions())
    with open_database(args.state, lock_timeout=args.lock_timeout) as database:
        summary = verify.run(
            database,
            bundle,
            clock,
            f"verify_{uuid.uuid4().hex[:16]}",
            fraction=args.sample,
            kinds=kinds,
        )
    print(json.dumps(summary, indent=2, default=str))
    return 1 if summary["mismatches"] else 0


def _hold(args: argparse.Namespace) -> int:
    """Read and write retention holds under the control lock, not the writer lock.

    A hold is a promise that everything it needs can be restored with no
    network, so adding one walks its closure first and refuses if any of that
    output is not in the live database.
    """
    from swingset.state.retention import add_hold, holds, remove_hold

    clock = SystemClock()
    if args.action == "list":
        print(json.dumps(holds(args.state), indent=2))
        return 0
    if args.action == "remove":
        if not args.hold_id:
            raise ValueError("hold remove requires --hold-id")
        removed = remove_hold(args.state, args.hold_id, timeout=args.lock_timeout)
        print(json.dumps({"hold_id": args.hold_id, "removed": removed}))
        return 0 if removed else 1
    if not (args.who or "").strip() or not (args.why or "").strip():
        raise ValueError("hold add requires --who and --why")
    with open_database(args.state, lock=False, read_only=True) as database:
        record = add_hold(
            args.state,
            database.connection,
            who=args.who,
            why=args.why,
            now=clock.now(),
            generations=tuple(args.generation),
            artifacts=tuple(args.artifact),
            timeout=args.lock_timeout,
        )
    print(json.dumps(record, indent=2))
    return 0


def _queue(args: argparse.Namespace) -> int:
    """Report the work queue, or requeue quarantined and waiting units (D-0216).

    The report opens read-only. A requeue is one short write that takes no
    process lock, like a pause: it sets the rows ready with no failures
    counted and asks for a retry, so the next derive picks them up.
    """
    from swingset.state import queue
    from swingset.state.work import WorkUnit

    clock = SystemClock()
    if args.action != "requeue":
        if args.error_class or args.unit or args.by:
            raise ValueError("--error-class, --unit and --by belong to `queue requeue`")
        with open_database(args.state, lock=False, read_only=True) as database:
            result = queue.report(database.connection, clock.now(), state=args.queue_state)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0
        states = result["states"]
        assert isinstance(states, dict)
        print(" ".join(f"{state}={count}" for state, count in states.items()))
        oldest = result["oldest_ready_seconds"]
        if oldest is not None:
            print(f"oldest due ready row: {int(float(str(oldest)) // 60)} min")
        classes = result["classes"]
        assert isinstance(classes, dict)
        for name, count in classes.items():
            print(f"class {name or '-'}: {count}")
        units = result["units"]
        assert isinstance(units, list)
        for item in units:
            print(
                f"{item['unit']} {item['state']} after {item['attempts']} x {item['error_class']}"
                f" until {item['next_attempt_at']}: {(item['last_error'] or '')[:120]}"
            )
        return 0
    if not (args.by or "").strip():
        raise ValueError("queue requeue requires --by")
    unit = None
    if args.unit:
        parts = args.unit.split("/", 2)
        if len(parts) != 3 or not all(parts):
            raise ValueError("--unit takes STAGE/KIND/ID")
        unit = WorkUnit(*parts)
    elif not args.error_class:
        raise ValueError("queue requeue requires --error-class or --unit")
    with open_database(args.state, lock=False, lock_timeout=args.lock_timeout) as database:
        with database.transaction() as conn:
            units = queue.requeue(
                conn,
                now=clock.now(),
                by=args.by,
                error_class=args.error_class,
                unit=unit,
                stage=args.stage,
                kind=args.kind,
            )
    print(
        json.dumps(
            {
                "requeued": [f"{item.stage}/{item.unit_kind}/{item.unit_id}" for item in units],
                "by": args.by.strip(),
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    stopped = [False]
    if args.command == "queue":
        try:
            return _queue(args)
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=str(exc))
            return 1
    if args.command in {"pause", "resume"}:
        try:
            return _control(args)
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=str(exc))
            return 1
    if args.command == "hold":
        try:
            return _hold(args)
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=str(exc))
            return 1
    if args.command == "partitions":
        try:
            return _partitions(args)
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=str(exc))
            return 1
    if args.command == "migrate":
        try:
            return _migrate_only(args)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, DatabaseLockedError) as exc:
            log("error", message=f"{type(exc).__name__}: {exc}")
            return 1
    if args.command == "verify-derivations":
        try:
            return _verify_derivations(args)
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=f"{type(exc).__name__}: {exc}")
            return 1
    if args.command == "accept-year":
        try:
            return _accept_year(args)
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=str(exc))
            return 1
    if args.command == "backup" and args.remove_checkpoint is not None:
        # Removing one named checkpoint makes no backup, so it takes no writer
        # lock, captures no input bundle and starts no run. An empty name is a
        # caller whose variable was unset, and is an error, not a backup.
        from swingset.backup.pruning import remove_named

        try:
            if args.local or args.destination or args.no_prune:
                raise ValueError(
                    "--remove-checkpoint removes one named checkpoint and makes no backup; "
                    "do not combine it with --local, --destination or --no-prune"
                )
            if (args.state / "RESTORE_PENDING").exists():
                # A restore into this state directory is under way, and it
                # rewrites everything here. A restore into a *different* state
                # directory that reads a checkpoint from this one leaves no
                # mark here; rename that checkpoint before starting it.
                raise ValueError(
                    "a restore into this state directory is pending. "
                    "Remove this checkpoint once the restore has finished."
                )
            removed = remove_named(args.state / "checkpoints", args.remove_checkpoint)
        except (OSError, ValueError, RuntimeError) as exc:
            log("error", message=f"{type(exc).__name__}: {exc}")
            return 1
        log("checkpoint-removed", checkpoint=str(removed))
        print(removed)
        return 0
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
        if args.command in LOOP_BUDGETS:
            return _loop(args, stopped)
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
            # A local backup's read-back runs below, once the writer lock
            # is released (_verify_then_prune).
            read_back: list[ReadBack] = []
            try:
                code = _mutate(args, database, stopped, read_back)
            except Exception as error:
                _finish_failed_manual_run(database, args.command, error, previous_unfinished)
                raise
        if code == 0 and read_back:
            _verify_then_prune(args.state, read_back[0])
        return code
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
