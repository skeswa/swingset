"""One budgeted cycle with independent collection and offline service shares."""

import json
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from swingset import __version__
from swingset.build.builder import BuildResult
from swingset.clock import Clock
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.client import FetchClient
from swingset.fetch.recovery import LocalCheckpointRecovery
from swingset.log import log
from swingset.publish.service import Hub, publish, reconcile
from swingset.schedule.derive import derive_one
from swingset.schedule.discover import discover
from swingset.schedule.fair_policy import allocation
from swingset.schedule.fairness import (
    WatchChoice,
    backpressure,
    next_delay,
    next_offline,
    next_watch,
    prepare_event_turns,
    record_offline_service,
    servicing,
)
from swingset.schedule.registry import (
    advance_sweep,
    discover_registry,
    run_saved_crosscheck_if_due,
)
from swingset.schedule.watches import refresh_policy
from swingset.sources import get_page_kind, sources
from swingset.state.attempts import latest_attempt, recover_interrupted
from swingset.state.control_scopes import for_publication, for_unit, for_watch, unit_allowed
from swingset.state.controls import (
    ControlPaused,
    matching_pauses,
    operation,
    recover_admissions,
    status,
)
from swingset.state.db import SCHEMA_VERSION, Database
from swingset.state.inputs import InputBundle, accept, capture
from swingset.state.work import WorkUnit, unfinished_units
from swingset.state.work_fingerprints import input_fingerprint


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
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    remote: Hub | None = None,
    *,
    correction_only: bool = False,
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
            database, bundle, clock, run_id, remote, correction_only=correction_only
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
    with database.transaction() as conn:
        recovered_admissions = recover_admissions(conn, now=started)
        interrupted_attempts = recover_interrupted(database, now=started)
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
    }
    attempts: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    recovery = LocalCheckpointRecovery(
        database.state_dir / "checkpoints", maximum_schema_version=SCHEMA_VERSION
    )
    archive = Archive(database.state_dir, recovery=recovery)
    fetcher: FetchClient | None = None

    def stop() -> bool:
        return should_stop() or clock.now() >= deadline

    def allowed(unit: WorkUnit) -> bool:
        return unit_allowed(database.connection, unit, now=clock.now())

    def held(action: str, exc: ControlPaused) -> None:
        summary["held_operations"].append(
            {"action": action, "reason": "operator_pause", "pauses": exc.pauses}
        )

    try:
        bundle = capture(config_dir, overrides_dir, database.state_dir, versions())
        summary["accepted_inputs"] = sorted(accept(database, bundle, clock))
        from swingset.schedule.event_enumerations import bootstrap as bootstrap_events

        if not stop():
            summary["event_enumerations"] = bootstrap_events(database, now=clock.now())
            from swingset.state.work import recover_parse_hints

            # Reconstruct disposable queue hints from retained metadata. Normal
            # derivation controls still govern execution of recovered work.
            recovery_now = clock.now()
            recovery_seconds = min(2.0, (deadline - recovery_now).total_seconds())
            if recovery_seconds > 0 and not should_stop():
                summary["parse_hint_recovery"] = recover_parse_hints(
                    database,
                    now=recovery_now,
                    wall_seconds=recovery_seconds,
                )
            from swingset.schedule.event_pressure import bootstrap as bootstrap_pressure

            summary["event_pressure_bootstrap"] = bootstrap_pressure(
                database, bundle.config, now=clock.now()
            )
            from swingset.schedule.event_blocker_history import refresh as observe_event_blockers

            # Diagnostic bookkeeping may observe a pause without starting
            # paused acquisition, artifact verification, or derivation work.
            summary["event_blocker_observations"] = observe_event_blockers(
                database,
                bundle.config,
                now=clock.now(),
                run_id=run_id,
                operator_hold=(database.state_dir / "operator-hold").is_file(),
            )
        from swingset.state.requirements import scan
        from swingset.state.verification import verification_summary

        if not stop():
            summary["requirements_scanned"] = scan(
                database, clock.now(), run_id, history_start=bundle.config.history_start
            )
        if (
            not stop()
            and database.connection.execute("SELECT 1 FROM history_acceptance LIMIT 1").fetchone()
        ):
            from swingset.history.event_sites import reconcile as review_event_sites
            from swingset.state.controls import ActionScope

            try:
                with operation(
                    database,
                    action_id="event_sites_" + uuid4().hex,
                    action_kind="project",
                    scope=ActionScope(all_sources=True, kinds=frozenset({"source_event_mapping"})),
                    clock=clock,
                    run_id=run_id,
                ):
                    summary["event_sites_reviewed"] = review_event_sites(
                        database,
                        now=clock.now(),
                        run_id=run_id,
                        history_start=bundle.config.history_start,
                        overrides=tuple(bundle.csv("source_urls.csv")),
                        wall_seconds=min(10, max(0, (deadline - clock.now()).total_seconds())),
                    )
            except ControlPaused as exc:
                held("event_site_review", exc)
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
                    correction = build(
                        database,
                        bundle,
                        clock,
                        run_id,
                        remote=hub if not dry_run else None,
                        correction_only=True,
                    )
                    summary["correction_candidate_id"] = correction.candidate_id
                    if not dry_run:
                        corrected = publish(database.state_dir, correction.path, hub)
                        summary["correction_publication"] = asdict(corrected)
                        if corrected.commit:
                            summary["correction_commit"] = corrected.commit
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
            "backpressure_before": backpressure(database.connection, bundle.config),
        }
        if not stop():
            discover(database, bundle, clock.now())
            if bundle.config.enabled("wsdc_registry"):
                discover_registry(database, clock.now())
        if not stop():
            from swingset.schedule.event_pressure import refresh as refresh_pressure
            from swingset.state.controls import ActionScope

            try:
                with operation(
                    database,
                    action_id="event_pressure_" + uuid4().hex,
                    action_kind="project",
                    scope=ActionScope(
                        all_sources=True,
                        kinds=frozenset(
                            {
                                "work_attempt",
                                "archive_artifact",
                                "admission_blocked",
                                "parse_failure",
                            }
                        ),
                    ),
                    clock=clock,
                    run_id=run_id,
                ):
                    summary["event_pressure_refresh"] = refresh_pressure(
                        database,
                        Archive(database.state_dir),
                        bundle.config,
                        now=clock.now(),
                        wall_seconds=min(5.0, max(0.0, (deadline - clock.now()).total_seconds())),
                    )
            except ControlPaused as exc:
                held("event_pressure_refresh", exc)
        if not stop():
            from swingset.schedule.event_progress import refresh as refresh_progress

            try:
                with operation(
                    database,
                    action_id="event_progress_" + uuid4().hex,
                    action_kind="project",
                    scope=ActionScope(
                        all_sources=True,
                        kinds=frozenset(
                            {
                                "work_attempt",
                                "archive_artifact",
                                "admission_blocked",
                                "parse_failure",
                            }
                        ),
                    ),
                    clock=clock,
                    run_id=run_id,
                ):
                    summary["event_progress_refresh"] = refresh_progress(
                        database,
                        Archive(database.state_dir),
                        bundle.config,
                        now=clock.now(),
                        run_id=run_id,
                        wall_seconds=min(5.0, max(0.0, (deadline - clock.now()).total_seconds())),
                    )
            except ControlPaused as exc:
                held("event_progress_refresh", exc)
        visited_watches: set[str] = set()
        attempted_inputs: dict[WorkUnit, set[str]] = {}

        def offline_allowed(unit: WorkUnit) -> bool:
            if not allowed(unit):
                return False
            previous = attempted_inputs.get(unit)
            return previous is None or input_fingerprint(database.connection, unit) not in previous

        summary["unit_failures"] = []

        def client() -> FetchClient:
            nonlocal fetcher
            if fetcher is None:
                fetcher = FetchClient(
                    database.connection,
                    bundle.config,
                    clock,
                    archive,
                    transport=transport,
                    should_stop=should_stop,
                )
            return fetcher

        def _acquisition(until: datetime) -> bool:
            from swingset.history.backfill import dispatch_one, offers

            from .event_timing_observer import archive_offers

            began = clock.now()
            issued = False
            offered_pages = (
                offers(database, bundle.config, clock, run_id=run_id, timing=archive_offers())
                if not stop()
                else ()
            )
            extra: list[WatchChoice] = []
            for offered in offered_pages:
                if offered.watch_id in visited_watches:
                    continue
                from urllib.parse import urlsplit

                offered_host = urlsplit(offered.archive_url or offered.url).hostname or ""
                extra.append(
                    WatchChoice(
                        key=offered.watch_id,
                        watch_id=None,
                        host=offered_host,
                        category="old",
                        scope=for_watch(
                            database.connection,
                            source=offered.source,
                            watch_id=offered.watch_id,
                            page_kind=offered.parser,
                            watch_kind=offered.kind,
                            host=offered_host,
                        ),
                        due_at=clock.now(),
                        archive=bool(offered.archive_url),
                        history=True,
                    )
                )
            while not stop() and clock.now() < until:
                from .event_timing_observer import checkpoint, resume

                checkpoint()
                with database.transaction() as conn:
                    prepare_event_turns(
                        conn,
                        bundle.config,
                        now=clock.now(),
                        exclude=visited_watches,
                        extra_choices=extra,
                        run_id=run_id,
                    )
                resume()
                choice = next_watch(
                    database.connection,
                    bundle.config,
                    now=clock.now(),
                    exclude=visited_watches,
                    extra_choices=extra,
                    run_id=run_id,
                )
                if choice is None:
                    delay = next_delay(
                        database.connection,
                        bundle.config,
                        now=clock.now(),
                        exclude=visited_watches,
                        extra_choices=extra,
                        run_id=run_id,
                    )
                    if delay is None or delay <= 0:
                        break
                    clock.sleep(min(delay, (until - clock.now()).total_seconds(), 5))
                    continue
                visited_watches.add(choice.key)
                with servicing(choice, run_id=run_id):
                    if choice.archive or choice.history:
                        historical = dispatch_one(
                            database,
                            bundle.config,
                            clock,
                            run_id,
                            deadline=until,
                            fetcher=client(),
                            allocated=True,
                            target_watch_id=choice.key,
                        )
                        summary["history_dispatch"] = {
                            "reason": historical.reason,
                            "watch_id": historical.watch_id,
                        }
                        result = historical.result
                        if result is None:
                            continue
                    else:
                        assert choice.watch_id is not None
                        row = database.connection.execute(
                            "SELECT parser,notes,url,archive_url FROM watches WHERE watch_id=?",
                            (choice.watch_id,),
                        ).fetchone()
                        result = client().fetch(
                            choice.watch_id,
                            get_page_kind(row["parser"]),
                            run_id,
                            deadline=until,
                            sweep=row["notes"] == "sweep",
                        )
                        if not result.skipped:
                            with database.transaction() as conn:
                                refresh_policy(
                                    conn,
                                    bundle.config,
                                    choice.watch_id,
                                    clock.now(),
                                    outcome=result.classification.outcome,
                                )
                                if row["parser"] == "wsdc_registry.dancer":
                                    advance_sweep(database, now=clock.now())
                    if result.skipped:
                        continue
                    issued = True
                    summary["checked"] += 1
                    summary["changed"] += int(result.changed)
                    summary["not_modified"] += int(result.classification.outcome == "NotModified")
                    summary["nonce_unchanged"] += int(result.nonce_unchanged)
                    host = choice.host
                    summary["bytes_by_host"][host] = (
                        summary["bytes_by_host"].get(host, 0) + result.body_bytes
                    )
            summary["scheduler"]["phase_seconds"]["acquisition"] += (
                clock.now() - began
            ).total_seconds()
            return issued

        def acquisition(until: datetime) -> bool:
            from .event_timing_observer import Observer, observing

            with observing(Observer(database, client(), clock, run_id=run_id, deadline=until)):
                return _acquisition(until)

        def settle_offline() -> None:
            if (
                not stop()
                and next(iter(unfinished_units(database.connection, ("parse", "project"))), None)
                is None
            ):
                if database.connection.execute(
                    "SELECT 1 FROM meta WHERE key='registry_crosscheck_due'"
                ).fetchone():
                    try:
                        with operation(
                            database,
                            action_id="crosscheck_" + uuid4().hex,
                            action_kind="registry_crosscheck",
                            scope=for_unit(
                                database.connection, WorkUnit("project", "history", "all")
                            ),
                            clock=clock,
                            run_id=run_id,
                        ):
                            run_saved_crosscheck_if_due(database, archive, clock.now(), run_id)
                    except ControlPaused as exc:
                        held("registry_crosscheck", exc)
            if not stop():
                try:
                    candidate = build(
                        database, bundle, clock, run_id, remote=hub if not dry_run else None
                    )
                    if not candidate.reused:
                        summary["stages"].append("build")
                    summary["candidate_id"] = candidate.candidate_id
                    if not dry_run:
                        if hub is None:
                            raise ValueError("publication enabled but no Hub adapter configured")
                        published = publish(database.state_dir, candidate.path, hub, dry_run=False)
                        summary["publication"] = asdict(published)
                        if published.commit:
                            summary["publish_commit"] = published.commit
                except ControlPaused as exc:
                    held("build", exc)

        # A retained compatible release gets a publication opportunity before
        # continuously arriving work can consume this cycle's entire budget.
        if baseline_commit(database.state_dir) is not None and not stop():
            settle_offline()
        if not stop():
            acquisition(min(deadline, clock.now() + timedelta(seconds=split.acquisition_seconds)))
        while not stop():
            began = clock.now()
            while (
                not stop()
                and (
                    unit := next_offline(
                        database.connection, now=clock.now(), allowed=offline_allowed
                    )
                )
                is not None
            ):
                attempt = derive_one(database, archive, unit, bundle, clock, run_id)
                if attempt.reason == "operator_pause":
                    continue
                completed = latest_attempt(database.connection, unit)
                assert completed is not None
                attempted_inputs.setdefault(unit, set()).add(str(completed["input_fingerprint"]))
                with database.transaction() as conn:
                    record_offline_service(conn, unit, now=clock.now())
                if unit.stage not in summary["stages"]:
                    summary["stages"].append(unit.stage)
                if attempt.failed:
                    summary["unit_failures"].append(
                        {
                            "stage": unit.stage,
                            "unit_kind": unit.unit_kind,
                            "unit_id": unit.unit_id,
                            "reason": attempt.reason,
                        }
                    )
                if unit.stage == "parse" and attempt.source is not None:
                    attempts[attempt.source] += 1
                    failures[attempt.source] += int(attempt.failed)
                    if attempt.source == "wsdc_registry":
                        with database.transaction():
                            advance_sweep(database, now=clock.now())
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
            database.connection, bundle.config
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
        summary["parse_errors"] = dict(failures)
        summary["restored_artifacts"] = recovery.restored
        summary["failed"] = any(
            failures[source] / count > 0.1 for source, count in attempts.items()
        ) or any(item["stage"] != "parse" for item in summary["unit_failures"])
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
        summary["controls"] = status(database.connection, now=clock.now())
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
