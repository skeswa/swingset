"""One budgeted cycle with independent collection and offline service shares."""

import json
import os
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from swingset import __version__
from swingset.build.builder import BuildError, BuildResult
from swingset.build.closure_manifest import ClosureError
from swingset.clock import Clock
from swingset.config import Config
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
    next_offline_batch,
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
    ActionScope,
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
from swingset.state.write_deadline import WriteDeadlineExceeded


def repository_identity(package_root: Path | None = None) -> str:
    """Return the deployed revision, or a deterministic digest for local source.

    A path-flake deployment exports an ``uncommitted:<store name>`` marker that
    changes on every switch, including config-only ones, and every parse and
    projection unit is fingerprinted with this identity. Such a marker is
    ignored in favour of the digest of the Python sources, so only a change
    to the code itself re-queues work (D-0188).
    """
    revision = os.environ.get("SWINGSET_REVISION", "").strip()
    if revision and not revision.startswith("uncommitted:"):
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


# Units selected per read snapshot in the offline phase (D-0182).
OFFLINE_BATCH = 64


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


def history_deferred_for_interval(
    conn: sqlite3.Connection, config: Config, unit: WorkUnit, *, now: datetime
) -> bool:
    """Whether the global history projection materialized within its interval."""
    interval = config.scheduler.history_interval_seconds
    if interval is None or (unit.stage, unit.unit_kind) != ("project", "history"):
        return False
    row = conn.execute(
        "SELECT g.created_at FROM derivation_scopes s JOIN derivation_generations g "
        "ON g.generation_id=s.materialized_generation_id "
        "WHERE s.stage='project' AND s.unit_kind='history' AND s.unit_id=?",
        (unit.unit_id,),
    ).fetchone()
    if row is None:
        return False
    return (now - datetime.fromisoformat(str(row[0]))).total_seconds() < interval


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

    summary["stage_errors"] = []

    @contextmanager
    def contained(stage: str) -> Iterator[None]:
        """A bookkeeping stage that overruns its write is skipped, not fatal.

        Its write rolled back whole. Letting it escape aborted every cycle
        before acquisition for eleven hours on 2026-09-25 (D-0201); the cycle
        still reports failed=true so the watchdog sees it.
        """
        try:
            yield
        except WriteDeadlineExceeded as exc:
            summary["stage_errors"].append({"stage": stage, "error": str(exc)})
            log("stage_failed", stage=stage, error=str(exc)[:300])

    try:
        bundle = capture(config_dir, overrides_dir, database.state_dir, versions())
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
        from swingset.schedule.event_enumerations import bootstrap as bootstrap_events

        if not stop():
            with contained("event_enumerations"):
                summary["event_enumerations"] = bootstrap_events(database, now=clock.now())
            from swingset.state.work import recover_parse_hints

            # Reconstruct disposable queue hints from retained metadata. Normal
            # derivation controls still govern execution of recovered work.
            recovery_now = clock.now()
            recovery_seconds = min(2.0, (deadline - recovery_now).total_seconds())
            if recovery_seconds > 0 and not should_stop():
                with contained("parse_hint_recovery"):
                    summary["parse_hint_recovery"] = recover_parse_hints(
                        database,
                        now=recovery_now,
                        wall_seconds=recovery_seconds,
                    )
            if stages.event_pressure:
                from swingset.schedule.event_pressure import bootstrap as bootstrap_pressure

                with contained("event_pressure_bootstrap"):
                    summary["event_pressure_bootstrap"] = bootstrap_pressure(
                        database, bundle.config, now=clock.now()
                    )
            if stages.event_blocker_observations:
                from swingset.schedule.event_blocker_history import (
                    refresh as observe_event_blockers,
                )

                # Diagnostic bookkeeping may observe a pause without starting
                # paused acquisition, artifact verification, or derivation work.
                with contained("event_blocker_observations"):
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
            with contained("requirements_scan"):
                summary["requirements_scanned"] = scan(
                    database, clock.now(), run_id, history_start=bundle.config.history_start
                )
        if (
            stages.event_site_review
            and not stop()
            and database.connection.execute("SELECT 1 FROM history_acceptance LIMIT 1").fetchone()
        ):
            from swingset.history.event_sites import reconcile as review_event_sites

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
            except WriteDeadlineExceeded as exc:
                summary["stage_errors"].append({"stage": "event_site_review", "error": str(exc)})
                log("stage_failed", stage="event_site_review", error=str(exc)[:300])
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
        if stages.event_pressure and not stop():
            from swingset.schedule.event_pressure import refresh as refresh_pressure

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
            except WriteDeadlineExceeded as exc:
                summary["stage_errors"].append(
                    {"stage": "event_pressure_refresh", "error": str(exc)}
                )
                log("stage_failed", stage="event_pressure_refresh", error=str(exc)[:300])
        if stages.event_progress_refresh and not stop():
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
            except WriteDeadlineExceeded as exc:
                summary["stage_errors"].append(
                    {"stage": "event_progress_refresh", "error": str(exc)}
                )
                log("stage_failed", stage="event_progress_refresh", error=str(exc)[:300])
        visited_watches: set[str] = set()
        attempted_inputs: dict[WorkUnit, set[str]] = {}

        def offline_allowed(unit: WorkUnit) -> bool:
            if not allowed(unit):
                return False
            if history_deferred_for_interval(
                database.connection, bundle.config, unit, now=clock.now()
            ):
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
            # Under backpressure no historical request can be admitted, so the
            # plan pass that finds them is skipped; otherwise it stops at the
            # phase deadline instead of consuming the whole cycle (D-0181).
            pressure = backpressure(database.connection, bundle.config)
            offered_pages = (
                offers(
                    database,
                    bundle.config,
                    clock,
                    run_id=run_id,
                    timing=archive_offers(),
                    deadline=until,
                )
                if not stop() and not pressure["active"]
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
            index_seconds = bundle.config.scheduler.archive_index_seconds
            if (
                index_seconds is not None
                and not stop()
                and not pressure["active"]
                and clock.now() < until
            ):
                from swingset.history.platform import index_platform_archives

                # Step 1 of every platform backfill: index the archive's
                # captures with the acquisition time left after the pages
                # already offered had their turn, so indexing never takes a
                # dispatch's host slot (D-0184). Newly indexed captures are
                # offered from the next pass on.
                summary["archive_index"] = index_platform_archives(
                    client(),
                    bundle.config,
                    clock,
                    history_start=bundle.config.history_start,
                    deadline=min(until, clock.now() + timedelta(seconds=index_seconds)),
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
            if not stop() and build_deferred_for_interval(
                database.connection, bundle.config, now=clock.now()
            ):
                summary["build_deferred"] = "interval"
            elif not stop() and build_deferred_for_backlog(
                database.state_dir,
                bundle.config,
                pressure=backpressure(database.connection, bundle.config),
                now=clock.now(),
            ):
                summary["build_deferred"] = "backlog"
            elif not stop():
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
            while not stop() and (
                batch := next_offline_batch(
                    database.connection,
                    now=clock.now(),
                    allowed=offline_allowed,
                    limit=OFFLINE_BATCH,
                )
            ):
                for unit in batch:
                    if stop():
                        break
                    attempt = derive_one(database, archive, unit, bundle, clock, run_id)
                    if attempt.reason == "operator_pause":
                        continue
                    completed = latest_attempt(database.connection, unit)
                    assert completed is not None
                    attempted_inputs.setdefault(unit, set()).add(
                        str(completed["input_fingerprint"])
                    )
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
        # A build error stays non-fatal so offline work continues (D-0187), but
        # it is a failure: 96 of 100 cycles reported failed=false on one (D-0199).
        summary["failed"] = (
            any(failures[source] / count > 0.1 for source, count in attempts.items())
            or any(item["stage"] != "parse" for item in summary["unit_failures"])
            or "build_error" in summary
            or bool(summary["stage_errors"])
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
