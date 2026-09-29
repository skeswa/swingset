"""One budgeted cycle with independent collection and offline service shares."""

import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from swingset.build.builder import BuildError, BuildResult
from swingset.build.closure_manifest import ClosureError
from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.recovery import LocalCheckpointRecovery
from swingset.history.acquisition import phase_two_refusals
from swingset.log import log
from swingset.publish.service import Hub, publish, reconcile
from swingset.schedule import pacing
from swingset.schedule.acquisition import Acquisition, Bookkeeping, contained, request_failures
from swingset.schedule.discover import discover
from swingset.schedule.fair_policy import allocation
from swingset.schedule.fairness import (
    backpressure,
    next_delay,
    next_watch,
    prepare_event_turns,
)
from swingset.schedule.offline import Offline, registry_crosscheck
from swingset.schedule.registry import (
    discover_registry,
    run_saved_crosscheck_if_due,
)
from swingset.sources.base import WatchSpec
from swingset.state import derivations, guards, queue, write_deadline
from swingset.state.attempts import recover_interrupted
from swingset.state.control_scopes import for_publication, for_unit, unit_allowed
from swingset.state.controls import (
    ControlPaused,
    matching_pauses,
    operation,
    recover_admissions,
    status,
)
from swingset.state.db import SCHEMA_VERSION, Database, reset_write_counters
from swingset.state.inputs import InputBundle, accept, capture
from swingset.state.work import WorkUnit, unfinished_units
from swingset.versions import repository_identity as repository_identity
from swingset.versions import versions as versions


def baseline_commit(state_dir: Path) -> str | None:
    baseline = state_dir / "baseline"
    if not baseline.is_symlink():
        return None
    return str(json.loads((baseline / "PUBLISHED").read_bytes())["commit"])


def build(
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    remote: Hub | None = None,
    *,
    correction_only: bool = False,
    report: dict[str, Any] | None = None,
) -> BuildResult:
    from swingset.build.service import build_release

    with operation(
        database,
        action_id="build_" + uuid4().hex,
        action_kind="build",
        scope=for_publication(database.connection),
        clock=clock,
        run_id=run_id,
    ):
        return build_release(
            database,
            bundle,
            clock,
            run_id,
            remote,
            correction_only=correction_only,
            report=report,
        )


def correct(
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    summary: dict[str, Any],
    *,
    hub: Hub | None,
    dry_run: bool,
) -> BuildResult:
    """Build a correction-only candidate and, unless a dry run, publish it.

    The caller has checked `build.service.correction_needed`. The cycle and
    the release loop share this (D-0215).
    """
    correction = build(
        database,
        bundle,
        clock,
        run_id,
        remote=hub if not dry_run else None,
        correction_only=True,
    )
    summary["correction_candidate_id"] = correction.candidate_id
    summary["correction_verdict"] = record_verdict(database, correction)
    if not dry_run:
        assert hub is not None
        corrected = publish(database.state_dir, correction.path, hub)
        summary["correction_publication"] = asdict(corrected)
        if corrected.commit:
            summary["correction_commit"] = corrected.commit
    return correction


def record_verdict(database: Database, candidate: BuildResult) -> dict[str, Any]:
    """The candidate's gate verdict, stored on its release manifest row; the summary form.

    An unreadable candidate is reported, not raised: `publish` reads the
    verdict again and fails closed.
    """
    from swingset.publish import gate

    try:
        verdict = gate.read(database.state_dir, candidate.path)
    except (OSError, ValueError, KeyError) as exc:
        return {"state": "unreadable", "error": f"{type(exc).__name__}: {exc}"[:200]}
    with database.transaction() as conn:
        gate.record(conn, candidate.path, verdict)
    if verdict.get("state") != "pass":
        log("release_refused", candidate_id=candidate.candidate_id, reason=gate.refusal(verdict))
    return gate.summary(verdict)


def build_and_publish(
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    summary: dict[str, Any],
    *,
    hub: Hub | None,
    dry_run: bool,
) -> BuildResult:
    """Build a candidate and, unless a dry run, publish it; noted in `summary`.

    A new candidate appends `build` to `summary["stages"]`; a reused one does
    not. A partitioned build notes its release manifest and stage 1's counts
    under `summary["partitions"]` (D-0224), and every build its gate verdict
    under `summary["verdict"]`; a refused verdict publishes nothing (D-0225).
    The cycle and the release loop share this (D-0215).
    """
    report: dict[str, Any] = {}
    try:
        candidate = build(
            database, bundle, clock, run_id, remote=hub if not dry_run else None, report=report
        )
    finally:
        if report:
            summary["partitions"] = report
    if not candidate.reused:
        summary["stages"].append("build")
    summary["candidate_id"] = candidate.candidate_id
    # A dry run evaluates the gate too, so the summary shows what publication
    # would do (D-0225).
    summary["verdict"] = record_verdict(database, candidate)
    if not dry_run:
        if hub is None:
            raise ValueError("publication enabled but no Hub adapter configured")
        published = publish(database.state_dir, candidate.path, hub, dry_run=False)
        summary["publication"] = asdict(published)
        if published.commit:
            summary["publish_commit"] = published.commit
    return candidate


def build_deferred_for_backlog(
    state_dir: Path, config: Config, *, pressure: Mapping[str, Any], now: datetime
) -> bool:
    """While backpressure is active, rebuild at most once per configured interval.

    A build and publication take several minutes of a twelve-minute cycle.
    While a backlog keeps backpressure active, publishing every cycle starves
    the offline phase that drains it, so a fresh enough baseline defers the
    build (D-0182). A cycle with no backlog still builds whenever rows change.
    """
    if not pressure.get("active"):
        return False
    published = state_dir / "baseline" / "PUBLISHED"
    if not published.is_file():
        return False
    verified = json.loads(published.read_bytes()).get("verified_at")
    if not verified:
        return False
    age = (now - datetime.fromisoformat(str(verified))).total_seconds()
    return age < config.scheduler.backlog_publish_interval_seconds


def build_deferred_for_interval(conn: sqlite3.Connection, config: Config, *, now: datetime) -> bool:
    """Whether a build was admitted within the configured build interval.

    Every admitted attempt counts, failed or reused: each one selects and
    validates the whole release closure, which takes minutes (D-0199).
    """
    interval = config.scheduler.build_interval_seconds
    if interval is None:
        return False
    row = conn.execute(
        "SELECT MAX(admitted_at) FROM execution_admissions WHERE action_kind='build'"
    ).fetchone()
    if row is None or row[0] is None:
        return False
    return (now - datetime.fromisoformat(str(row[0]))).total_seconds() < interval


def deferred_for_interval(
    conn: sqlite3.Connection, config: Config, unit: WorkUnit, *, now: datetime
) -> bool:
    """Whether the paced inventory waits its interval (schedule/pacing.py).

    `project/inventory` waits `inventory_interval_seconds` (D-0210), unless a
    calendar rewrote its rows. Years are not paced.
    """
    return pacing.deferred(conn, config.scheduler, unit, now=now)


def _report(summary: dict[str, Any], key: str, read: Callable[[], Any]) -> None:
    """Store one report under `key`, or its error under `<key>_error`."""
    try:
        summary[key] = read()
    except Exception as exc:
        summary[f"{key}_error"] = f"{type(exc).__name__}: {exc}"[:200]
        log("report_failed", report=key, error=str(exc)[:300])


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
    # The run record carries this run's write timings and deadline firings
    # (D-0217): start the process counters afresh.
    reset_write_counters()
    fired_before = write_deadline.fired()
    with database.transaction() as conn:
        recovered_admissions = recover_admissions(conn, now=started)
        interrupted_attempts = recover_interrupted(database, now=started)
        # This process holds the exclusive lock: no lease is alive (D-0216).
        released_leases = queue.release_all(conn, started)
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
        "recovered_admissions": recovered_admissions,
        "interrupted_attempts": interrupted_attempts,
        "held_operations": [],
        # Work-queue activity this cycle (D-0216). A waiting unit whose retry
        # ended the way it waited is a repeat, not a failure.
        "waiting_repeats": 0,
        "control_timeouts": 0,
        "queue": {"released": released_leases, "woken": 0, "lease_busy": 0, "quarantined": []},
        # Guard rows written and cleared by the acquisition passes (D-0219).
        "guards": {"passes": 0, "recorded": 0, "cleared": 0},
    }
    recovery = LocalCheckpointRecovery(
        database.state_dir / "checkpoints", maximum_schema_version=SCHEMA_VERSION
    )
    archive = Archive(database.state_dir, recovery=recovery)
    acquiring: Acquisition | None = None

    def stop() -> bool:
        return should_stop() or clock.now() >= deadline

    def allowed(unit: WorkUnit) -> bool:
        return unit_allowed(database.connection, unit, now=clock.now())

    def held(action: str, exc: ControlPaused) -> None:
        summary["held_operations"].append(
            {"action": action, "reason": "operator_pause", "pauses": exc.pauses}
        )

    summary["stage_errors"] = []
    # Derivation kinds offline selection skipped because their recipe could
    # not be formed, by `stage/kind` (schedule/fairness.next_offline_batch).
    summary["readiness_errors"] = {}

    # Read before the work so the report can name the epochs it moved. A
    # failed read must not cost the run record either: it is noted, and the
    # report then leaves replay bumps out.
    replayed_before: dict[str, int] | None = None
    _report(summary, "replay_epochs", lambda: derivations.replay_epochs(database.connection))
    if "replay_epochs_error" in summary:
        summary["derivation_error"] = summary.pop("replay_epochs_error")
    else:
        replayed_before = summary.pop("replay_epochs")
    # Held for the run: the pacing gate that readers outside scheduling ask
    # (schedule/pacing.holds), and the phase-two gate's refusals by reason.
    scoped = ExitStack()
    refused = scoped.enter_context(phase_two_refusals())
    # The cycle is the exclusive process: every lease it meets is dead.
    scoped.enter_context(queue.owning("cycle", run_id, exclusive=True))
    try:
        bundle = capture(config_dir, overrides_dir, database.state_dir, versions())
        scoped.enter_context(pacing.configured(bundle.config.scheduler, clock.now))
        summary["accepted_inputs"] = sorted(accept(database, bundle, clock))
        from swingset.state.retention import enforce_size_cap

        # A database over its size cap pauses the pipeline before any work is
        # admitted. Reading, controls and recovery do not go through admission,
        # so they keep working, and `gc --plan` still explains what could go.
        summary["retention_cap"] = enforce_size_cap(
            database.state_dir,
            max_database_bytes=bundle.config.retention.max_database_bytes,
            now=clock.now(),
        )
        # A stage switched off in `[stages]` is skipped whole: no operation, no
        # query, no summary key. Turning off `event_pressure` also opens its
        # acquisition gate (event_pressure._enabled).
        stages = bundle.config.stages
        summary["stages_disabled"] = stages.disabled()
        bookkeeping = Bookkeeping(
            database, bundle, clock, run_id, summary, deadline=deadline, held=held
        )

        if not stop():
            bookkeeping.enumerations()
            # Units whose waiting or quarantine clock came due are ready again.
            with contained(summary, "queue_wake"):
                summary["queue"]["woken"] = queue.wake(database, clock.now())
            from swingset.state.work import recover_parse_hints

            # Reconstruct disposable queue hints from retained metadata. Normal
            # derivation controls still govern execution of recovered work.
            recovery_now = clock.now()
            recovery_seconds = min(2.0, (deadline - recovery_now).total_seconds())
            if recovery_seconds > 0 and not should_stop():
                with contained(summary, "parse_hint_recovery"):
                    summary["parse_hint_recovery"] = recover_parse_hints(
                        database,
                        now=recovery_now,
                        wall_seconds=recovery_seconds,
                    )
            bookkeeping.pressure_bootstrap()
            bookkeeping.blocker_observations()
        from swingset.state.verification import verification_summary

        if not stop():
            bookkeeping.requirements_scan()
        if not stop():
            bookkeeping.site_review()
        summary["registry_verification"] = verification_summary(database.connection, clock.now())
        if hub is not None:
            reconciled = reconcile(database.state_dir, hub, dry_run=dry_run)
            summary["reconciliation"] = asdict(reconciled)
            if reconciled.commit:
                summary["publish_commit"] = reconciled.commit
        queued = next(iter(unfinished_units(database.connection)), None) is not None
        if queued and hub is not None and not stop():
            from swingset.build.service import correction_needed

            if correction_needed(database, bundle):
                try:
                    correct(database, bundle, clock, run_id, summary, hub=hub, dry_run=dry_run)
                except ControlPaused as exc:
                    held("correction_build", exc)
        split = allocation(bundle.config, budget)
        summary["scheduler"] = {
            "allocation": asdict(split),
            "phase_seconds": {
                "reconciliation": (clock.now() - started).total_seconds(),
                "acquisition": 0.0,
                "offline": 0.0,
            },
            "backpressure_before": backpressure(
                database.connection, bundle.config, now=clock.now()
            ),
        }
        if not stop():
            discover(database, bundle, clock.now())
            if bundle.config.enabled("wsdc_registry"):
                discover_registry(database, clock.now())
        if not stop():
            bookkeeping.pressure_refresh()
        if not stop():
            bookkeeping.progress_refresh()
        summary["unit_failures"] = []
        offline = Offline(database, archive, bundle, clock, run_id, summary, allowed=allowed)
        # The pass is shared with the acquire loop (schedule/acquisition.py);
        # the cycle's offers come from the plan pass itself.
        passes = acquiring = Acquisition(
            database,
            bundle.config,
            clock,
            run_id,
            summary,
            archive=archive,
            stop=stop,
            should_stop=should_stop,
            transport=transport,
            prepare=prepare_event_turns,
            select=next_watch,
            delay=next_delay,
        )

        def offered(until: datetime) -> tuple[WatchSpec, ...]:
            from swingset.history import backfill

            from .event_timing_observer import archive_offers

            return backfill.offers(
                database,
                bundle.config,
                clock,
                run_id=run_id,
                timing=archive_offers(),
                deadline=until,
            )

        def acquisition(until: datetime) -> bool:
            issued, seconds = passes.run(until, offered)
            summary["scheduler"]["phase_seconds"]["acquisition"] += seconds
            return issued

        def settle_offline() -> None:
            if not stop():
                registry_crosscheck(
                    database, archive, clock, run_id, held=held, run=run_saved_crosscheck_if_due
                )
            if not stop() and build_deferred_for_interval(
                database.connection, bundle.config, now=clock.now()
            ):
                summary["build_deferred"] = "interval"
            elif not stop() and build_deferred_for_backlog(
                database.state_dir,
                bundle.config,
                pressure=backpressure(database.connection, bundle.config, now=clock.now()),
                now=clock.now(),
            ):
                summary["build_deferred"] = "backlog"
            elif not stop():
                try:
                    build_and_publish(
                        database, bundle, clock, run_id, summary, hub=hub, dry_run=dry_run
                    )
                except ControlPaused as exc:
                    held("build", exc)
                except (BuildError, ClosureError) as exc:
                    # A build that cannot assemble a consistent candidate is
                    # reported, not fatal: the offline phase that follows is
                    # what brings the stale generation it tripped over up to
                    # date (D-0187). A closure that fails validation is the
                    # same kind of failure; letting it escape aborted the whole
                    # cycle, fetching included (D-0199).
                    summary["build_error"] = f"{type(exc).__name__}: {exc}"
                    log("build_failed", error=str(exc)[:300])

        # A retained compatible release gets a publication opportunity before
        # continuously arriving work can consume this cycle's entire budget.
        if baseline_commit(database.state_dir) is not None and not stop():
            settle_offline()
        if not stop():
            acquisition(min(deadline, clock.now() + timedelta(seconds=split.acquisition_seconds)))

        while not stop():
            began = clock.now()
            offline.run(stop)
            # Builds and saved interpretation consume offline time before any
            # unused share is lent back to collection.
            settle_offline()
            summary["scheduler"]["phase_seconds"]["offline"] += (
                clock.now() - began
            ).total_seconds()
            # Empty/blocked offline demand lends its unused allocation. Visits
            # remain unique per cycle and each actual HTTP debit keeps host caps.
            if stop() or not acquisition(deadline):
                break
        summary["scheduler"]["backpressure_after"] = backpressure(
            database.connection, bundle.config, now=clock.now()
        )
        summary["held_units"] = [
            {
                **asdict(unit),
                "pauses": matching_pauses(
                    database.connection, for_unit(database.connection, unit), now=clock.now()
                ),
            }
            for unit in unfinished_units(database.connection)
            if not allowed(unit)
        ]
        summary["parse_errors"] = dict(offline.failures)
        _report(summary, "queue_states", lambda: queue.counts_by_state(database.connection))
        _report(summary, "guard_reasons", lambda: guards.counts_by_reason(database.connection))
        summary["restored_artifacts"] = recovery.restored
        # A build error stays non-fatal so offline work continues (D-0187), but
        # it is a failure: 96 of 100 cycles reported failed=false on one (D-0199).
        summary["failed"] = (
            offline.failed()
            or "build_error" in summary
            or bool(summary["stage_errors"])
            or request_failures(summary)
            or bool(summary["readiness_errors"])
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
        scoped.close()
        # A gate that stays closed for hours shows as a count here (I4).
        summary["phase_two_refusals"] = dict(sorted(refused.items()))
        if acquiring is not None:
            acquiring.close()
        # The run record below is what the watchdog reads (I4), so a report
        # query that fails (a locked or full database, or the error that
        # ended the cycle) is noted in it instead of losing it.
        _report(summary, "controls", lambda: status(database.connection, now=clock.now()))
        # What derivation did: generations and cutoffs by kind, and the
        # replay epochs a cutoff moved, which the watchdog watches for a loop.
        _report(
            summary,
            "derivation",
            lambda: derivations.cycle_progress(database.connection, run_id, replayed_before),
        )
        summary.update(write_deadline.run_counters(fired_before))
        # Resident peak, not systemd's MemoryPeak, which counts page cache of
        # the database file: the phase 2 memory gates read this (step 5).
        from swingset.loops.runtime import peak_rss_bytes

        summary["peak_rss_bytes"] = peak_rss_bytes()
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
