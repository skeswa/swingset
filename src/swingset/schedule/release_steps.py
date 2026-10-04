"""Build and publication steps the release loop and the CLI run.

They were the cycle's until it went (D-0239): `build` admits one release
build (a correction as `correction`, which the build interval ignores), `correct` and `build_and_publish` build a candidate and publish it
unless a dry run, `record_verdict` stores its gate verdict, and the two
`build_deferred_for_*` checks say when a build waits.
"""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from swingset.build.builder import BuildResult
from swingset.clock import Clock
from swingset.config import Config
from swingset.log import log
from swingset.publish.service import Hub, publish
from swingset.state.control_scopes import for_publication
from swingset.state.controls import operation
from swingset.state.db import Database
from swingset.state.inputs import InputBundle


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
    source: Path | None = None,
) -> BuildResult:
    """Admit and run one build. A correction (or a revert, ``source``) is
    admitted as `correction`, so it does not reset the build interval (D-0250)."""
    from swingset.build.service import build_release

    kind = "correction" if correction_only else "build"
    with operation(
        database,
        action_id=f"{kind}_" + uuid4().hex,
        action_kind=kind,
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
            source=source,
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

    The caller has checked `build.service.correction_needed` (D-0215).
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

    While a backlog keeps backpressure active, a fresh enough baseline defers
    the build, so derivation gets the time a build would take (D-0182). With
    no backlog the build interval alone decides.
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
    validates the whole release closure, which takes minutes (D-0199). A
    correction is admitted as `correction` and does not count (D-0250).
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
